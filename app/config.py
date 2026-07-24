"""Application paths and configuration locations."""

import json
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIRECTORY = Path(__file__).resolve().parent.parent
CONFIG_DIRECTORY = PROJECT_DIRECTORY / "config"
ENV_PATH = PROJECT_DIRECTORY / ".env"
USER_CONFIG_PATH = CONFIG_DIRECTORY / "user_config.json"
NORMALIZED_EVENT_SCHEMA_PATH = CONFIG_DIRECTORY / "normalized_event_schema.json"
MAPPINGS_DIRECTORY = CONFIG_DIRECTORY / "mappings"
PREDEFINED_MAPPINGS_DIRECTORY = MAPPINGS_DIRECTORY / "predefined"
GENERATED_MAPPINGS_DIRECTORY = MAPPINGS_DIRECTORY / "generated"

USER_CONFIG = json.loads(USER_CONFIG_PATH.read_text(encoding="utf-8"))
AI_CONFIG = USER_CONFIG["ai"]
DATABASE_CONFIG = USER_CONFIG["database"]
SOURCE_CONFIG = USER_CONFIG["sources"]
AI_STATIC_FIELDS = {
    source: settings["ai_static_fields"]
    for source, settings in SOURCE_CONFIG.items()
}
SUPPORTED_SOURCES = frozenset(AI_STATIC_FIELDS)
KAFKA_BOOTSTRAP_SERVERS = USER_CONFIG["kafka"]["bootstrap_servers"]
KAFKA_DELIVERY_TIMEOUT_MS = USER_CONFIG["kafka"]["delivery_timeout_ms"]
KAFKA_HEALTH_TIMEOUT_SECONDS = USER_CONFIG["kafka"][
    "health_timeout_seconds"
]


class AISecrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    openrouter_api_key: SecretStr


class DatabaseSecrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    postgres_password: SecretStr
