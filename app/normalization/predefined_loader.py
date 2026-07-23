"""Load predefined mappings and convert them into runtime mappings."""

from pathlib import Path

import yaml
from pydantic import ValidationError
from yaml import YAMLError

from app.normalization.fingerprint import (
    calculate_case_id,
    calculate_schema_fingerprint_from_signature,
)
from app.schemas.mappings import (
    PredefinedMapping,
    PredefinedMappingFile,
    RuntimeMapping,
)


class PredefinedMappingError(ValueError):
    """Raised when a predefined mapping file cannot be converted."""


def convert_predefined_mapping(
    source: str,
    predefined: PredefinedMapping,
) -> RuntimeMapping:
    """Add identifiers to a validated predefined mapping."""

    schema_fingerprint = calculate_schema_fingerprint_from_signature(
        predefined.schema_signature
    )
    case_id = calculate_case_id(source, schema_fingerprint)

    return RuntimeMapping(
        case_id=case_id,
        source=source,
        schema_fingerprint=schema_fingerprint,
        version=predefined.version,
        created_by="user",
        static_fields=predefined.static_fields,
        fields=predefined.fields,
    )


def convert_predefined_file(path: str | Path) -> tuple[RuntimeMapping, ...]:
    """Load and convert every mapping in a predefined YAML file."""

    predefined_path = Path(path)
    try:
        with predefined_path.open(encoding="utf-8") as mapping_file:
            document = yaml.safe_load(mapping_file)
        predefined_file = PredefinedMappingFile.model_validate(document)
    except (OSError, YAMLError, ValidationError) as error:
        raise PredefinedMappingError(
            f"could not load predefined mapping '{predefined_path.name}': {error}"
        ) from error

    return tuple(
        convert_predefined_mapping(predefined_file.source, mapping)
        for mapping in predefined_file.mappings
    )


def load_predefined_mappings(
    directory: str | Path,
) -> tuple[RuntimeMapping, ...]:
    """Load all predefined YAML mappings from a directory."""

    predefined_directory = Path(directory)
    paths = sorted(
        [
            *predefined_directory.glob("*.yaml"),
            *predefined_directory.glob("*.yml"),
        ]
    )

    mappings: dict[str, RuntimeMapping] = {}
    for path in paths:
        for mapping in convert_predefined_file(path):
            if mapping.case_id in mappings:
                raise PredefinedMappingError(
                    "predefined mappings contain duplicate case ID "
                    f"'{mapping.case_id}'"
                )
            mappings[mapping.case_id] = mapping

    return tuple(mappings.values())
