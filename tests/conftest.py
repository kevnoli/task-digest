from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest

from task_digest.config import Settings
from task_digest.models import VikunjaLabel, VikunjaTask


@pytest.fixture
def settings() -> Settings:
    return Settings(
        vikunja_base_url="https://tasks.example.test",
        vikunja_api_token="vikunja-secret",
        vikunja_web_url="https://tasks.example.test",
        dry_run=True,
    )


@pytest.fixture
def make_task() -> Callable[..., VikunjaTask]:
    def factory(
        *,
        task_id: int = 1,
        title: str = "A task",
        due_date: datetime | None = None,
        done: bool = False,
        priority: int = 0,
        project_id: int = 10,
        description: str = "",
        labels: list[str] | None = None,
    ) -> VikunjaTask:
        return VikunjaTask(
            id=task_id,
            title=title,
            due_date=due_date,
            done=done,
            priority=priority,
            project_id=project_id,
            description=description,
            identifier=f"T-{task_id}",
            labels=[
                VikunjaLabel(id=index, title=label)
                for index, label in enumerate(labels or [], start=1)
            ],
        )

    return factory
