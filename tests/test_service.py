from __future__ import annotations

from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from task_digest.config import Settings
from task_digest.llm.base import SummaryProvider
from task_digest.models import DigestKind, VikunjaProject, VikunjaTask
from task_digest.service import DigestRunner

ZONE = ZoneInfo("America/Bahia")
NOW = datetime(2026, 7, 19, 10, tzinfo=ZONE)


class FakeVikunja:
    def __init__(self, tasks: list[VikunjaTask]) -> None:
        self.tasks = tasks

    async def fetch_tasks(self) -> list[VikunjaTask]:
        return self.tasks

    async def fetch_projects(self) -> list[VikunjaProject]:
        return [VikunjaProject(id=10, title="Work")]


class FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_digest(self, message: str) -> int:
        self.messages.append(message)
        return 1


class FailingSummary:
    async def summarize(self, _tasks: object, _kind: object) -> str:
        raise RuntimeError("provider unavailable")


def live_settings() -> Settings:
    return Settings(
        vikunja_base_url="https://tasks.test",
        vikunja_api_token="vk-secret",
        vikunja_web_url="https://tasks.test",
        telegram_bot_token="tg-secret",
        telegram_chat_id="123",
        dry_run=False,
    )


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_deterministic_digest() -> None:
    task = VikunjaTask(id=1, title="Migrate database", project_id=10, due_date=NOW)
    telegram = FakeTelegram()
    runner = DigestRunner(
        live_settings(),
        FakeVikunja([task]),
        telegram,
        cast(SummaryProvider, FailingSummary()),
    )

    outcome = await runner.run(DigestKind.MORNING, now=NOW)

    assert outcome.message_count == 1
    assert len(telegram.messages) == 1
    assert "Migrate database" in telegram.messages[0]


@pytest.mark.asyncio
async def test_empty_digest_does_not_call_telegram() -> None:
    telegram = FakeTelegram()
    runner = DigestRunner(live_settings(), FakeVikunja([]), telegram)

    outcome = await runner.run(DigestKind.MORNING, now=NOW)

    assert outcome.empty is True
    assert telegram.messages == []


@pytest.mark.asyncio
async def test_morning_and_evening_behavior_differs() -> None:
    upcoming = VikunjaTask(
        id=1,
        title="Tomorrow",
        project_id=10,
        due_date=datetime(2026, 7, 20, 10, tzinfo=ZONE),
    )
    morning_telegram = FakeTelegram()
    evening_telegram = FakeTelegram()

    morning = await DigestRunner(live_settings(), FakeVikunja([upcoming]), morning_telegram).run(
        DigestKind.MORNING, now=NOW
    )
    evening = await DigestRunner(live_settings(), FakeVikunja([upcoming]), evening_telegram).run(
        DigestKind.EVENING, now=NOW
    )

    assert morning.task_count == 1
    assert evening.empty is True
    assert evening_telegram.messages == []
