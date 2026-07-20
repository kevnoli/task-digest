from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, time
from enum import StrEnum
from pathlib import Path

from pydantic import ValidationError

from task_digest.config import Settings


class SettingsStoreError(RuntimeError):
    """Raised when persisted runtime settings cannot be read or validated."""


class DigestSettingKey(StrEnum):
    TIMEZONE = "timezone"
    MORNING_DIGEST_ENABLED = "morning_digest_enabled"
    MORNING_DIGEST_TIME = "morning_digest_time"
    EVENING_DIGEST_ENABLED = "evening_digest_enabled"
    EVENING_DIGEST_TIME = "evening_digest_time"
    UPCOMING_DAYS = "upcoming_days"


class SettingsStore:
    """Small SQLite repository for non-secret, runtime-adjustable preferences."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def initialize(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_settings (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
        except (OSError, sqlite3.Error) as exc:
            raise SettingsStoreError(
                f"could not initialize settings database at {self.path}"
            ) from exc

    def get_all(self) -> dict[str, str]:
        self.initialize()
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    "SELECT key, value FROM runtime_settings ORDER BY key"
                ).fetchall()
        except sqlite3.Error as exc:
            raise SettingsStoreError("could not read runtime settings") from exc
        allowed = {key.value for key in DigestSettingKey}
        return {str(row["key"]): str(row["value"]) for row in rows if row["key"] in allowed}

    def set(
        self, key: DigestSettingKey, value: str, base_settings: Settings
    ) -> tuple[str, Settings]:
        current = apply_runtime_settings(base_settings, self)
        candidate_data = current.model_dump()
        candidate_data[key.value] = value
        try:
            candidate = Settings.model_validate(candidate_data)
        except ValidationError as exc:
            raise SettingsStoreError(_validation_message(key, exc)) from exc
        normalized = _serialize_value(getattr(candidate, key.value))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO runtime_settings (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key.value, normalized, datetime.now(UTC).isoformat()),
                )
        except sqlite3.Error as exc:
            raise SettingsStoreError(f"could not save {key.value}") from exc
        return normalized, candidate

    def reset(self, key: DigestSettingKey) -> bool:
        self.initialize()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "DELETE FROM runtime_settings WHERE key = ?", (key.value,)
                )
        except sqlite3.Error as exc:
            raise SettingsStoreError(f"could not reset {key.value}") from exc
        return cursor.rowcount > 0

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection


def apply_runtime_settings(settings: Settings, store: SettingsStore) -> Settings:
    persisted = store.get_all()
    if not persisted:
        return settings
    values = settings.model_dump()
    values.update(persisted)
    try:
        return Settings.model_validate(values)
    except ValidationError as exc:
        raise SettingsStoreError("persisted runtime settings are invalid") from exc


def effective_digest_settings(settings: Settings) -> dict[DigestSettingKey, str]:
    return {key: _serialize_value(getattr(settings, key.value)) for key in DigestSettingKey}


def _serialize_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, time):
        return value.strftime("%H:%M")
    return str(value)


def _validation_message(key: DigestSettingKey, error: ValidationError) -> str:
    first = error.errors(include_url=False)[0]
    message = str(first.get("msg", "invalid value"))
    return f"invalid value for {key.value}: {message}"
