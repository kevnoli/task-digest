from __future__ import annotations

import pytest
from pydantic import ValidationError

from task_digest.config import Settings

BASE = {
    "vikunja_base_url": "https://tasks.example.test",
    "vikunja_api_token": "secret",
    "vikunja_web_url": "https://tasks.example.test",
}


def test_defaults_and_safe_summary() -> None:
    settings = Settings(**BASE, dry_run=True)

    assert settings.timezone == "America/Bahia"
    assert settings.upcoming_days == 3
    assert settings.morning_digest_time.strftime("%H:%M") == "08:00"
    assert "secret" not in repr(settings.safe_summary())


@pytest.mark.parametrize(("value", "expected"), [("yes", True), ("0", False), ("OFF", False)])
def test_predictable_boolean_parsing(value: str, expected: bool) -> None:
    settings = Settings(**BASE, dry_run=True, evening_digest_enabled=value)
    assert settings.evening_digest_enabled is expected


def test_invalid_boolean_has_useful_error() -> None:
    with pytest.raises(ValidationError, match="must be one of"):
        Settings(**BASE, dry_run=True, evening_digest_enabled="sometimes")


def test_invalid_timezone_is_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown IANA timezone"):
        Settings(**BASE, dry_run=True, timezone="Mars/Olympus")


def test_telegram_credentials_required_outside_dry_run() -> None:
    with pytest.raises(ValidationError, match="TELEGRAM_BOT_TOKEN"):
        Settings(**BASE, dry_run=False)


def test_llm_configuration_is_validated() -> None:
    with pytest.raises(ValidationError, match="LLM_API_KEY"):
        Settings(**BASE, dry_run=True, llm_enabled=True, llm_model="gpt-test")


def test_schedule_time_requires_hours_and_minutes() -> None:
    with pytest.raises(ValidationError, match="HH:MM"):
        Settings(**BASE, dry_run=True, morning_digest_time="08:00:01")
