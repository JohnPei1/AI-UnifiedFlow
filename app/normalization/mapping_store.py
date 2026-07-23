"""Persist, cache, and retrieve runtime mappings."""

import json
import os
import re
import tempfile
from pathlib import Path

from pydantic import ValidationError

from app.config import (
    GENERATED_MAPPINGS_DIRECTORY,
    PREDEFINED_MAPPINGS_DIRECTORY,
)
from app.normalization.predefined_loader import (
    PredefinedMappingError,
    load_predefined_mappings,
)
from app.schemas.mappings import RuntimeMapping

CASE_ID_PATTERN = re.compile(r"^case_[A-Za-z0-9_-]+$")


class MappingStoreError(ValueError):
    """Raised when a runtime mapping cannot be loaded or saved."""


class MappingStore:
    """Store validated runtime mappings on disk and in memory."""

    def __init__(
        self,
        predefined_directory: str | Path = PREDEFINED_MAPPINGS_DIRECTORY,
        generated_directory: str | Path = GENERATED_MAPPINGS_DIRECTORY,
    ) -> None:
        self.predefined_directory = Path(predefined_directory)
        self.generated_directory = Path(generated_directory)
        self._mappings_by_case_id: dict[str, RuntimeMapping] = {}

    def initialize(self) -> None:
        """Convert predefined mappings and populate the memory cache."""

        self.generated_directory.mkdir(parents=True, exist_ok=True)
        self._mappings_by_case_id.clear()

        try:
            predefined_mappings = load_predefined_mappings(
                self.predefined_directory
            )
        except PredefinedMappingError as error:
            raise MappingStoreError(str(error)) from error

        for mapping in predefined_mappings:
            self.save(mapping)

        for path in sorted(self.generated_directory.glob("*.json")):
            mapping = self._load_generated_file(path)
            self._mappings_by_case_id[mapping.case_id] = mapping

    def get(self, case_id: str) -> RuntimeMapping | None:
        """Return a mapping from memory or its exact generated file."""

        self._validate_case_id(case_id)

        cached = self._mappings_by_case_id.get(case_id)
        if cached is not None:
            return cached

        path = self.generated_directory / f"{case_id}.json"
        try:
            mapping = self._load_generated_file(path)
        except FileNotFoundError:
            return None

        self._mappings_by_case_id[case_id] = mapping
        return mapping

    def save(self, mapping: RuntimeMapping) -> RuntimeMapping:
        """Validate and atomically save a runtime mapping."""

        validated_mapping = self._validated_copy(mapping)
        self._validate_case_id(validated_mapping.case_id)
        self.generated_directory.mkdir(parents=True, exist_ok=True)

        destination = (
            self.generated_directory / f"{validated_mapping.case_id}.json"
        )
        temporary_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.generated_directory,
                prefix=f".{validated_mapping.case_id}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                json.dump(
                    validated_mapping.model_dump(mode="json", by_alias=True),
                    temporary_file,
                    ensure_ascii=False,
                    indent=2,
                    allow_nan=False,
                )
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, destination)
        except (OSError, TypeError, ValueError) as error:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise MappingStoreError(
                f"could not save mapping '{validated_mapping.case_id}': {error}"
            ) from error

        self._mappings_by_case_id[validated_mapping.case_id] = validated_mapping
        return validated_mapping

    def _load_generated_file(self, path: Path) -> RuntimeMapping:
        try:
            with path.open(encoding="utf-8") as mapping_file:
                document = json.load(mapping_file)
            mapping = RuntimeMapping.model_validate(document)
        except FileNotFoundError:
            raise
        except (OSError, json.JSONDecodeError, ValidationError) as error:
            raise MappingStoreError(
                f"could not load generated mapping '{path.name}': {error}"
            ) from error

        if mapping.case_id != path.stem:
            raise MappingStoreError(
                f"mapping case ID '{mapping.case_id}' does not match "
                f"filename '{path.name}'"
            )
        self._validate_case_id(mapping.case_id)
        return mapping

    @staticmethod
    def _validated_copy(mapping: RuntimeMapping) -> RuntimeMapping:
        if not isinstance(mapping, RuntimeMapping):
            raise MappingStoreError("mapping must be a RuntimeMapping")

        try:
            return RuntimeMapping.model_validate(
                mapping.model_dump(mode="json", by_alias=True)
            )
        except ValidationError as error:
            raise MappingStoreError(f"invalid runtime mapping: {error}") from error

    @staticmethod
    def _validate_case_id(case_id: str) -> None:
        if not isinstance(case_id, str) or not CASE_ID_PATTERN.fullmatch(case_id):
            raise MappingStoreError("case ID contains invalid characters")
