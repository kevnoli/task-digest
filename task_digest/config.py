from __future__ import annotations

from datetime import time
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BeforeValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    raise ValueError("must be one of: true, false, 1, 0, yes, no, on, off")


EnvBool = Annotated[bool, BeforeValidator(_parse_bool)]


class Settings(BaseSettings):
    """Application configuration loaded from environment variables or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    anchor_base_url: str
    anchor_api_token: SecretStr
    anchor_web_url: str
    anchor_timeout_seconds: float = Field(default=15.0, gt=0, le=120)

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None
    telegram_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    telegram_max_retries: int = Field(default=3, ge=0, le=10)

    timezone: str = "America/Bahia"
    morning_digest_enabled: EnvBool = True
    morning_digest_time: time = time(8, 0)
    evening_digest_enabled: EnvBool = True
    evening_digest_time: time = time(17, 0)
    upcoming_days: int = Field(default=3, ge=0, le=30)

    llm_enabled: EnvBool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=180)
    llm_include_descriptions: EnvBool = False

    log_level: str = "INFO"
    dry_run: EnvBool = False
    heartbeat_path: Path = Path("/tmp/task-digest/heartbeat")
    heartbeat_interval_seconds: int = Field(default=20, ge=5, le=300)
    heartbeat_max_age_seconds: int = Field(default=90, ge=15, le=900)
    settings_database_path: Path = Path("./data/task-digest.sqlite3")

    @field_validator("anchor_base_url", "anchor_web_url", "llm_base_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("must be an absolute http:// or https:// URL")
        return normalized

    @field_validator("anchor_api_token")
    @classmethod
    def validate_anchor_token(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("ANCHOR_API_TOKEN must not be empty")
        return value

    @field_validator("telegram_bot_token", "llm_api_key", mode="before")
    @classmethod
    def empty_secret_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("telegram_chat_id", "llm_model", mode="before")
    @classmethod
    def empty_string_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("morning_digest_time", "evening_digest_time", mode="before")
    @classmethod
    def parse_digest_time(cls, value: object) -> object:
        if isinstance(value, time):
            return value
        if isinstance(value, str):
            try:
                parsed = time.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("must use 24-hour HH:MM format, for example 08:00") from exc
            if parsed.second or parsed.microsecond or parsed.tzinfo is not None:
                raise ValueError("must use 24-hour HH:MM format, for example 08:00")
            return parsed
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return normalized

    @model_validator(mode="after")
    def validate_integrations(self) -> Settings:
        if not self.dry_run:
            if self.telegram_bot_token is None:
                raise ValueError("TELEGRAM_BOT_TOKEN is required unless DRY_RUN=true")
            if not self.telegram_chat_id:
                raise ValueError("TELEGRAM_CHAT_ID is required unless DRY_RUN=true")
        if self.llm_enabled:
            if self.llm_api_key is None:
                raise ValueError("LLM_API_KEY is required when LLM_ENABLED=true")
            if not self.llm_model:
                raise ValueError("LLM_MODEL is required when LLM_ENABLED=true")
        return self

    @property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def secret_values(self) -> tuple[str, ...]:
        values = [self.anchor_api_token.get_secret_value()]
        if self.telegram_bot_token is not None:
            values.append(self.telegram_bot_token.get_secret_value())
        if self.llm_api_key is not None:
            values.append(self.llm_api_key.get_secret_value())
        return tuple(value for value in values if value)

    def safe_summary(self) -> dict[str, object]:
        return {
            "anchor_base_url": self.anchor_base_url,
            "anchor_web_url": self.anchor_web_url,
            "timezone": self.timezone,
            "morning_enabled": self.morning_digest_enabled,
            "morning_time": self.morning_digest_time.strftime("%H:%M"),
            "evening_enabled": self.evening_digest_enabled,
            "evening_time": self.evening_digest_time.strftime("%H:%M"),
            "upcoming_days": self.upcoming_days,
            "llm_enabled": self.llm_enabled,
            "llm_model": self.llm_model if self.llm_enabled else None,
            "dry_run": self.dry_run,
            "heartbeat_path": str(self.heartbeat_path),
            "settings_database_path": str(self.settings_database_path),
        }
