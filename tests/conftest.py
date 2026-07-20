from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

import pytest

from task_digest.config import Settings
from task_digest.models import SourceTask


@pytest.fixture
def settings() -> Settings:
    return Settings(
        anchor_base_url="https://anchor.example.test",
        anchor_api_token="anchor-secret",
        anchor_web_url="https://anchor.example.test",
        dry_run=True,
    )


@pytest.fixture
def make_task() -> Callable[..., SourceTask]:
    def factory(
        *,
        task_id: str = "note-1:0",
        title: str = "A task",
        due_date: datetime | None = None,
        completed: bool = False,
        priority: int = 0,
        project_id: str = "note-1",
        project_name: str = "Work",
        description: str = "",
        labels: list[str] | None = None,
    ) -> SourceTask:
        return SourceTask(
            id=task_id,
            title=title,
            due_date=due_date,
            completed=completed,
            priority=priority,
            project_id=project_id,
            project_name=project_name,
            description=description,
            labels=labels or [],
            url=f"https://anchor.example.test/notes/{project_id}",
        )

    return factory
