"""Application paths and configuration locations."""

from pathlib import Path

PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
CONFIG_DIRECTORY = PROJECT_DIRECTORY / "config"
NORMALIZED_EVENT_SCHEMA_PATH = CONFIG_DIRECTORY / "normalized_event_schema.json"
AI_CONFIG_PATH = CONFIG_DIRECTORY / "ai.json"
SOURCES_CONFIG_PATH = CONFIG_DIRECTORY / "sources.json"
MAPPINGS_DIRECTORY = CONFIG_DIRECTORY / "mappings"
PREDEFINED_MAPPINGS_DIRECTORY = MAPPINGS_DIRECTORY / "predefined"
GENERATED_MAPPINGS_DIRECTORY = MAPPINGS_DIRECTORY / "generated"
