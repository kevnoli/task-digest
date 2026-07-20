from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from task_digest.config import Settings
from task_digest.digest import classify_tasks, format_digest
from task_digest.llm import OpenAICompatibleSummaryProvider, SummaryProvider
from task_digest.models import DigestKind, VikunjaProject, VikunjaTask
from task_digest.telegram import TelegramClient
from task_digest.vikunja import VikunjaClient

logger = logging.getLogger(__name__)


class VikunjaReader(Protocol):
    async def fetch_tasks(self) -> list[VikunjaTask]: ...

    async def fetch_projects(self) -> list[VikunjaProject]: ...


class TelegramSender(Protocol):
    async def send_digest(self, message: str) -> int: ...


@dataclass(frozen=True)
class DigestOutcome:
    task_count: int
    message_count: int
    empty: bool
    dry_run: bool


class DigestRunner:
    def __init__(
        self,
        settings: Settings,
        vikunja: VikunjaReader,
        telegram: TelegramSender | None,
        summary_provider: SummaryProvider | None = None,
    ) -> None:
        self._settings = settings
        self._vikunja = vikunja
        self._telegram = telegram
        self._summary_provider = summary_provider

    async def run(
        self, kind: DigestKind = DigestKind.MORNING, *, now: datetime | None = None
    ) -> DigestOutcome:
        run_at = now or datetime.now(UTC)
        raw_tasks, projects = await asyncio.gather(
            self._vikunja.fetch_tasks(), self._vikunja.fetch_projects()
        )
        tasks = classify_tasks(
            raw_tasks,
            projects,
            now=run_at,
            timezone=self._settings.zoneinfo,
            upcoming_days=self._settings.upcoming_days,
            kind=kind,
            web_url=self._settings.vikunja_web_url,
        )

        introduction: str | None = None
        if self._summary_provider is not None and tasks:
            try:
                introduction = await self._summary_provider.summarize(tasks, kind)
            except Exception as exc:
                logger.warning(
                    "llm_summary_failed fallback=deterministic error_type=%s",
                    type(exc).__name__,
                )

        digest = format_digest(
            tasks,
            kind=kind,
            now=run_at,
            timezone=self._settings.zoneinfo,
            introduction=introduction,
        )
        if digest is None:
            logger.info("digest_empty kind=%s action=skip", kind.value)
            return DigestOutcome(
                task_count=0,
                message_count=0,
                empty=True,
                dry_run=self._settings.dry_run,
            )

        if self._settings.dry_run:
            print(f"=== DRY RUN: {kind.value} digest; Telegram was not called ===")
            print(digest)
            logger.info("digest_dry_run kind=%s task_count=%d", kind.value, len(tasks))
            return DigestOutcome(task_count=len(tasks), message_count=0, empty=False, dry_run=True)

        if self._telegram is None:
            raise RuntimeError("Telegram sender is not configured")
        message_count = await self._telegram.send_digest(digest)
        logger.info(
            "digest_sent kind=%s task_count=%d message_count=%d",
            kind.value,
            len(tasks),
            message_count,
        )
        return DigestOutcome(
            task_count=len(tasks), message_count=message_count, empty=False, dry_run=False
        )


async def execute_once(settings: Settings, kind: DigestKind = DigestKind.MORNING) -> DigestOutcome:
    async with AsyncExitStack() as stack:
        vikunja = await stack.enter_async_context(
            VikunjaClient(
                settings.vikunja_base_url,
                settings.vikunja_api_token.get_secret_value(),
                timeout=settings.vikunja_timeout_seconds,
            )
        )

        telegram: TelegramClient | None = None
        if not settings.dry_run:
            if settings.telegram_bot_token is None or settings.telegram_chat_id is None:
                raise RuntimeError("Telegram credentials were not validated")
            telegram = await stack.enter_async_context(
                TelegramClient(
                    settings.telegram_bot_token.get_secret_value(),
                    settings.telegram_chat_id,
                    timeout=settings.telegram_timeout_seconds,
                    max_retries=settings.telegram_max_retries,
                )
            )

        summary_provider: OpenAICompatibleSummaryProvider | None = None
        if settings.llm_enabled:
            if settings.llm_api_key is None or settings.llm_model is None:
                raise RuntimeError("LLM credentials were not validated")
            summary_provider = await stack.enter_async_context(
                OpenAICompatibleSummaryProvider(
                    settings.llm_base_url,
                    settings.llm_api_key.get_secret_value(),
                    settings.llm_model,
                    include_descriptions=settings.llm_include_descriptions,
                    timeout=settings.llm_timeout_seconds,
                )
            )

        runner = DigestRunner(settings, vikunja, telegram, summary_provider)
        return await runner.run(kind)
