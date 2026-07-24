import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from app.normalization.mapping_store import MappingStore, MappingStoreError
from app.normalization.predefined_loader import convert_predefined_file
from app.schemas.mappings import RuntimeMapping


def write_predefined_mapping(
    directory: Path,
    category: str = "cloud",
) -> RuntimeMapping:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "aws.yaml"
    document = {
        "source": "aws",
        "mappings": [
            {
                "name": "compute_v1",
                "version": 1,
                "schema_signature": [
                    "resource_id:string",
                    "usage_amount:string",
                ],
                "static_fields": {
                    "category": category,
                    "usage_type": "compute_time",
                },
                "fields": {
                    "resource": [
                        {
                            "operation": "copy",
                            "from": "payload.resource_id",
                        }
                    ],
                    "quantity": [
                        {
                            "operation": "copy",
                            "from": "payload.usage_amount",
                        },
                        {"operation": "cast", "to": "float"},
                    ],
                },
            }
        ],
    }
    path.write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    return convert_predefined_file(path)[0]


def test_predefined_mappings_are_converted_automatically(
    tmp_path: Path,
) -> None:
    predefined = tmp_path / "predefined"
    generated = tmp_path / "generated"
    expected = write_predefined_mapping(predefined)
    store = MappingStore(predefined, generated)

    store.initialize()

    assert store.get(expected.case_id) == expected


def test_converted_files_use_case_id_name(tmp_path: Path) -> None:
    predefined = tmp_path / "predefined"
    generated = tmp_path / "generated"
    expected = write_predefined_mapping(predefined)

    MappingStore(predefined, generated).initialize()

    assert (generated / f"{expected.case_id}.json").is_file()


def test_startup_loads_generated_mappings(
    tmp_path: Path,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    predefined = tmp_path / "predefined"
    generated = tmp_path / "generated"
    predefined.mkdir()
    mapping = mapping_factory()
    MappingStore(predefined, generated).save(mapping)

    store = MappingStore(predefined, generated)
    store.initialize()

    assert store.get(mapping.case_id) == mapping


def test_memory_hit_does_not_need_the_file(
    tmp_path: Path,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    store = MappingStore(tmp_path / "predefined", tmp_path / "generated")
    mapping = store.save(mapping_factory())
    (store.generated_directory / f"{mapping.case_id}.json").unlink()

    assert store.get(mapping.case_id) == mapping


def test_get_opens_only_the_exact_generated_file(
    tmp_path: Path,
    mapping_factory: Callable[..., RuntimeMapping],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = tmp_path / "generated"
    mapping = MappingStore(
        tmp_path / "predefined",
        generated,
    ).save(mapping_factory())
    store = MappingStore(tmp_path / "predefined", generated)

    def fail_glob(self: Path, pattern: str):
        raise AssertionError(f"unexpected directory scan: {self} {pattern}")

    monkeypatch.setattr(Path, "glob", fail_glob)

    assert store.get(mapping.case_id) == mapping


def test_invalid_generated_mapping_is_rejected(tmp_path: Path) -> None:
    generated = tmp_path / "generated"
    generated.mkdir()
    (generated / "case_invalid.json").write_text(
        '{"case_id": "case_invalid"}',
        encoding="utf-8",
    )
    store = MappingStore(tmp_path / "predefined", generated)

    with pytest.raises(MappingStoreError, match="could not load"):
        store.get("case_invalid")


def test_save_replaces_file_atomically(
    tmp_path: Path,
    mapping_factory: Callable[..., RuntimeMapping],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = tmp_path / "generated"
    store = MappingStore(tmp_path / "predefined", generated)
    mapping = mapping_factory()
    replacements: list[tuple[Path, Path]] = []
    original_replace = os.replace

    def replace(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        assert source_path.parent == generated
        assert source_path.suffix == ".tmp"
        assert source_path.is_file()
        replacements.append((source_path, destination_path))
        original_replace(source_path, destination_path)

    monkeypatch.setattr(
        "app.normalization.mapping_store.os.replace",
        replace,
    )

    store.save(mapping)

    assert len(replacements) == 1
    assert replacements[0][1] == generated / f"{mapping.case_id}.json"


def test_predefined_mapping_has_startup_priority(tmp_path: Path) -> None:
    predefined = tmp_path / "predefined"
    generated = tmp_path / "generated"
    expected = write_predefined_mapping(predefined)
    generated.mkdir()
    stale = expected.model_copy(
        update={
            "created_by": "ai",
            "static_fields": {
                "category": "ai",
                "usage_type": "tokens",
            },
        }
    )
    (generated / f"{expected.case_id}.json").write_text(
        stale.model_dump_json(by_alias=True),
        encoding="utf-8",
    )

    store = MappingStore(predefined, generated)
    store.initialize()

    assert store.get(expected.case_id) == expected


def test_missing_results_are_not_cached(
    tmp_path: Path,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    generated = tmp_path / "generated"
    store = MappingStore(tmp_path / "predefined", generated)
    mapping = mapping_factory()

    assert store.get(mapping.case_id) is None
    MappingStore(tmp_path / "other", generated).save(mapping)
    assert store.get(mapping.case_id) == mapping


def test_invalid_case_id_is_rejected(tmp_path: Path) -> None:
    store = MappingStore(tmp_path / "predefined", tmp_path / "generated")

    with pytest.raises(MappingStoreError, match="invalid characters"):
        store.get("../mapping")


def test_saved_json_uses_aliases(
    tmp_path: Path,
    mapping_factory: Callable[..., RuntimeMapping],
) -> None:
    store = MappingStore(tmp_path / "predefined", tmp_path / "generated")
    mapping = store.save(mapping_factory())
    document = json.loads(
        (store.generated_directory / f"{mapping.case_id}.json").read_text(
            encoding="utf-8"
        )
    )

    assert document["fields"]["resource"][0]["from"] == "payload.resource_id"
    assert "source_path" not in document["fields"]["resource"][0]
