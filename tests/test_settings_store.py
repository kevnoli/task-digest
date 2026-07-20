from __future__ import annotations

from pathlib import Path

import pytest

from task_digest.config import Settings
from task_digest.settings_store import (
    DigestSettingKey,
    SettingsStore,
    SettingsStoreError,
    apply_runtime_settings,
    effective_digest_settings,
)


def settings_for(path: Path) -> Settings:
    return Settings(
        vikunja_base_url="https://tasks.example.test",
        vikunja_api_token="vikunja-secret",
        vikunja_web_url="https://tasks.example.test",
        dry_run=True,
        settings_database_path=path,
    )


def test_sqlite_setting_persists_and_overrides_environment(tmp_path: Path) -> None:
    path = tmp_path / "settings.sqlite3"
    base = settings_for(path)
    store = SettingsStore(path)

    normalized, candidate = store.set(DigestSettingKey.MORNING_DIGEST_TIME, "06:45", base)
    reloaded = apply_runtime_settings(base, SettingsStore(path))

    assert normalized == "06:45"
    assert candidate.morning_digest_time.strftime("%H:%M") == "06:45"
    assert reloaded.morning_digest_time.strftime("%H:%M") == "06:45"
    assert path.is_file()


def test_boolean_and_integer_values_are_normalized(tmp_path: Path) -> None:
    path = tmp_path / "settings.sqlite3"
    base = settings_for(path)
    store = SettingsStore(path)

    assert store.set(DigestSettingKey.EVENING_DIGEST_ENABLED, "OFF", base)[0] == "false"
    assert store.set(DigestSettingKey.UPCOMING_DAYS, "7", base)[0] == "7"

    effective = apply_runtime_settings(base, store)
    assert effective.evening_digest_enabled is False
    assert effective.upcoming_days == 7


def test_invalid_setting_is_not_saved(tmp_path: Path) -> None:
    path = tmp_path / "settings.sqlite3"
    base = settings_for(path)
    store = SettingsStore(path)

    with pytest.raises(SettingsStoreError, match="morning_digest_time"):
        store.set(DigestSettingKey.MORNING_DIGEST_TIME, "25:99", base)

    assert store.get_all() == {}


def test_reset_returns_to_environment_value(tmp_path: Path) -> None:
    path = tmp_path / "settings.sqlite3"
    base = settings_for(path)
    store = SettingsStore(path)
    store.set(DigestSettingKey.UPCOMING_DAYS, "9", base)

    assert store.reset(DigestSettingKey.UPCOMING_DAYS) is True
    assert store.reset(DigestSettingKey.UPCOMING_DAYS) is False
    assert apply_runtime_settings(base, store).upcoming_days == 3


def test_effective_settings_are_secret_free(tmp_path: Path) -> None:
    values = effective_digest_settings(settings_for(tmp_path / "settings.sqlite3"))

    assert set(values) == set(DigestSettingKey)
    assert "vikunja-secret" not in repr(values)
