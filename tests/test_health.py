from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from task_digest.scheduler import check_health


def write_heartbeat(path: Path, *, updated_at: datetime, running: bool = True) -> None:
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "started_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
                "scheduler_running": running,
                "updated_at": updated_at.isoformat(),
            }
        ),
        encoding="utf-8",
    )


def test_healthcheck_accepts_live_scheduler(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    write_heartbeat(heartbeat, updated_at=datetime.now(UTC))
    healthy, message = check_health(heartbeat, 90)
    assert healthy is True
    assert "healthy" in message


def test_healthcheck_rejects_stale_or_stopped_scheduler(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    write_heartbeat(heartbeat, updated_at=datetime.now(UTC) - timedelta(minutes=10))
    assert check_health(heartbeat, 90)[0] is False

    write_heartbeat(heartbeat, updated_at=datetime.now(UTC), running=False)
    assert check_health(heartbeat, 90)[0] is False
