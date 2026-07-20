from __future__ import annotations

from datetime import datetime
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from task_digest.config import Settings
from task_digest.llm.base import SummaryProvider
from task_digest.models import DigestKind, SourceTask
from task_digest.service import DigestRunner

ZONE = ZoneInfo("America/Bahia")
NOW = datetime(2026, 7, 19, 10, tzinfo=ZONE)


class FakeSource:
    def __init__(self, tasks: list[SourceTask]) -> None:
        self.tasks = tasks

    async def fetch_tasks(self) -> list[SourceTask]:
        return self.tasks


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
        anchor_base_url="https://anchor.test",
        anchor_api_token="anchor-secret",
        anchor_web_url="https://anchor.test",
        telegram_bot_token="tg-secret",
        telegram_chat_id="123",
        dry_run=False,
    )


def task(title: str = "Buy milk") -> SourceTask:
    return SourceTask(
        id="note-1:0",
        title=title,
        project_id="note-1",
        project_name="Groceries",
        url="https://anchor.test/notes/note-1",
    )


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_deterministic_digest() -> None:
    telegram = FakeTelegram()
    runner = DigestRunner(
        live_settings(),
        FakeSource([task()]),
        telegram,
        cast(SummaryProvider, FailingSummary()),
    )

    outcome = await runner.run(DigestKind.MORNING, now=NOW)

    assert outcome.message_count == 1
    assert len(telegram.messages) == 1
    assert "Buy milk" in telegram.messages[0]


@pytest.mark.asyncio
async def test_empty_digest_does_not_call_telegram() -> None:
    telegram = FakeTelegram()
    runner = DigestRunner(live_settings(), FakeSource([]), telegram)

    outcome = await runner.run(DigestKind.MORNING, now=NOW)

    assert outcome.empty is True
    assert telegram.messages == []


@pytest.mark.asyncio
async def test_morning_and_evening_both_include_unchecked_items() -> None:
    morning_telegram = FakeTelegram()
    evening_telegram = FakeTelegram()

    morning = await DigestRunner(live_settings(), FakeSource([task()]), morning_telegram).run(
        DigestKind.MORNING, now=NOW
    )
    evening = await DigestRunner(live_settings(), FakeSource([task()]), evening_telegram).run(
        DigestKind.EVENING, now=NOW
    )

    assert morning.task_count == 1
    assert evening.task_count == 1
    assert "Still unfinished" not in morning_telegram.messages[0]
    assert "Still unfinished" in evening_telegram.messages[0]
