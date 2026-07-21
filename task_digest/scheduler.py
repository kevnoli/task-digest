from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import logging
import os
import signal
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from task_digest.config import Settings
from task_digest.models import DigestKind
from task_digest.service import execute_once
from task_digest.telegram import TelegramClient, TelegramUpdate

logger = logging.getLogger(__name__)


class ServiceAlreadyRunning(RuntimeError):
    """Raised when another scheduler process owns the runtime lock."""


def _write_heartbeat(
    path: Path, *, started_at: datetime, scheduler_running: bool, pid: int
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": pid,
        "started_at": started_at.isoformat(),
        "scheduler_running": scheduler_running,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def _acquire_lock(path: Path) -> IO[str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise ServiceAlreadyRunning("another task-digest service is already running") from exc
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


async def _scheduled_job(
    settings: Settings, kind: DigestKind, digest_lock: asyncio.Lock | None = None
) -> None:
    try:
        if digest_lock is None:
            await execute_once(settings, kind)
        else:
            async with digest_lock:
                await execute_once(settings, kind)
    except Exception as exc:
        logger.error(
            "scheduled_digest_failed kind=%s error_type=%s", kind.value, type(exc).__name__
        )


async def _run_digest_command(
    settings: Settings, telegram: TelegramClient, digest_lock: asyncio.Lock
) -> None:
    try:
        async with digest_lock:
            outcome = await execute_once(settings, DigestKind.MORNING)
        if outcome.empty:
            await telegram.send_digest("<i>No checklist items to include.</i>")
        logger.info("telegram_digest_command_complete task_count=%d", outcome.task_count)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("telegram_digest_command_failed error_type=%s", type(exc).__name__)
        try:
            await telegram.send_digest("<i>Could not generate the digest right now.</i>")
        except Exception as notice_exc:
            logger.error(
                "telegram_command_error_notice_failed error_type=%s",
                type(notice_exc).__name__,
            )


async def _telegram_command_loop(settings: Settings, digest_lock: asyncio.Lock) -> None:
    if settings.telegram_bot_token is None or settings.telegram_chat_id is None:
        logger.warning("telegram_command_listener_disabled reason=missing_credentials")
        return

    async with TelegramClient(
        settings.telegram_bot_token.get_secret_value(),
        settings.telegram_chat_id,
        timeout=settings.telegram_timeout_seconds,
        max_retries=settings.telegram_max_retries,
    ) as telegram:
        command_registered = False
        offset: int | None = None
        while True:
            try:
                if not command_registered:
                    await telegram.register_digest_command()
                    command_registered = True
                    logger.info("telegram_command_listener_started command=/digest")
                updates = await telegram.get_updates(offset=offset)
                if not updates:
                    continue

                offset = max(update.update_id for update in updates) + 1
                # Confirm this batch before acting so a service restart cannot replay
                # an already-sent digest. A newer update returned here remains pending.
                await telegram.get_updates(offset=offset, poll_timeout=0, limit=1)

                for update in updates:
                    if _is_allowed_digest_command(update, settings.telegram_chat_id):
                        await _run_digest_command(settings, telegram, digest_lock)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "telegram_command_poll_failed error_type=%s retry_seconds=5",
                    type(exc).__name__,
                )
                await asyncio.sleep(5)


def _is_allowed_digest_command(update: TelegramUpdate, allowed_chat_id: str) -> bool:
    return update.chat_id == allowed_chat_id and update.is_command("digest")


async def _heartbeat_loop(settings: Settings, started_at: datetime) -> None:
    while True:
        _write_heartbeat(
            settings.heartbeat_path,
            started_at=started_at,
            scheduler_running=True,
            pid=os.getpid(),
        )
        await asyncio.sleep(settings.heartbeat_interval_seconds)


async def serve(settings: Settings) -> None:
    started_at = datetime.now(UTC)
    lock_file = _acquire_lock(settings.heartbeat_path.with_suffix(".lock"))
    scheduler = AsyncIOScheduler(
        timezone=settings.zoneinfo,
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30},
    )
    digest_lock = asyncio.Lock()
    if settings.morning_digest_enabled:
        scheduler.add_job(
            _scheduled_job,
            CronTrigger(
                hour=settings.morning_digest_time.hour,
                minute=settings.morning_digest_time.minute,
                timezone=settings.zoneinfo,
            ),
            args=(settings, DigestKind.MORNING, digest_lock),
            id="morning-digest",
            replace_existing=True,
        )
    if settings.evening_digest_enabled:
        scheduler.add_job(
            _scheduled_job,
            CronTrigger(
                hour=settings.evening_digest_time.hour,
                minute=settings.evening_digest_time.minute,
                timezone=settings.zoneinfo,
            ),
            args=(settings, DigestKind.EVENING, digest_lock),
            id="evening-digest",
            replace_existing=True,
        )
    if not scheduler.get_jobs():
        lock_file.close()
        raise ValueError("service mode requires at least one enabled digest schedule")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(stop_signal, stop_event.set)

    scheduler.start()
    _write_heartbeat(
        settings.heartbeat_path,
        started_at=started_at,
        scheduler_running=True,
        pid=os.getpid(),
    )
    heartbeat_task = asyncio.create_task(_heartbeat_loop(settings, started_at), name="heartbeat")
    command_task = asyncio.create_task(
        _telegram_command_loop(settings, digest_lock), name="telegram-commands"
    )
    logger.info(
        "scheduler_started timezone=%s jobs=%d pid=%d",
        settings.timezone,
        len(scheduler.get_jobs()),
        os.getpid(),
    )
    try:
        await stop_event.wait()
    finally:
        scheduler.shutdown(wait=False)
        heartbeat_task.cancel()
        command_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat_task
        with contextlib.suppress(asyncio.CancelledError):
            await command_task
        _write_heartbeat(
            settings.heartbeat_path,
            started_at=started_at,
            scheduler_running=False,
            pid=os.getpid(),
        )
        lock_file.close()
        logger.info("scheduler_stopped")


def check_health(path: Path, max_age_seconds: int) -> tuple[bool, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
        started_at = datetime.fromisoformat(payload["started_at"])
        updated_at = datetime.fromisoformat(payload["updated_at"])
        scheduler_running = payload["scheduler_running"] is True
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return False, f"invalid or missing heartbeat: {type(exc).__name__}"

    now = datetime.now(UTC)
    if started_at.tzinfo is None or updated_at.tzinfo is None:
        return False, "heartbeat timestamps are not timezone-aware"
    if started_at > now:
        return False, "heartbeat start time is in the future"
    age = (now - updated_at).total_seconds()
    if age < -5 or age > max_age_seconds:
        return False, f"heartbeat is stale ({age:.0f}s old)"
    if not scheduler_running:
        return False, "scheduler is not running"
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False, f"service process {pid} is not running"
    return True, f"healthy: scheduler pid={pid}, heartbeat_age={age:.0f}s"
