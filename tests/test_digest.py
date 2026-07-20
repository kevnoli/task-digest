from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from task_digest.digest import classify_tasks, format_digest, group_by_project
from task_digest.models import DigestKind, DueCategory, VikunjaProject, VikunjaTask

ZONE = ZoneInfo("America/Bahia")
NOW = datetime(2026, 7, 19, 10, 0, tzinfo=ZONE)
PROJECTS = [VikunjaProject(id=10, title="Work"), VikunjaProject(id=20, title="Personal")]


def classify(
    tasks: list[VikunjaTask], kind: DigestKind = DigestKind.MORNING, upcoming_days: int = 3
):
    return classify_tasks(
        tasks,
        PROJECTS,
        now=NOW,
        timezone=ZONE,
        upcoming_days=upcoming_days,
        kind=kind,
        web_url="https://tasks.example.test",
    )


def test_timezone_aware_classification(
    make_task: Callable[..., VikunjaTask],
) -> None:
    tasks = [
        make_task(task_id=1, due_date=datetime(2026, 7, 18, 23, 59, tzinfo=ZONE)),
        make_task(task_id=2, due_date=datetime(2026, 7, 19, 23, 59, tzinfo=ZONE)),
        make_task(task_id=3, due_date=datetime(2026, 7, 22, 0, 0, tzinfo=ZONE)),
        make_task(task_id=4, due_date=datetime(2026, 7, 23, 0, 0, tzinfo=ZONE)),
    ]

    result = classify(tasks)

    assert [task.category for task in result] == [
        DueCategory.OVERDUE,
        DueCategory.TODAY,
        DueCategory.UPCOMING,
    ]
    assert result[0].days_overdue == 1


def test_utc_timestamp_is_converted_to_configured_timezone(
    make_task: Callable[..., VikunjaTask],
) -> None:
    task = make_task(due_date=datetime.fromisoformat("2026-07-20T01:00:00+00:00"))
    assert classify([task])[0].category is DueCategory.TODAY


def test_completed_and_no_due_date_tasks_are_excluded(
    make_task: Callable[..., VikunjaTask],
) -> None:
    assert (
        classify(
            [
                make_task(task_id=1, due_date=NOW, done=True),
                make_task(task_id=2, due_date=None),
            ]
        )
        == []
    )


def test_evening_excludes_upcoming_tasks(make_task: Callable[..., VikunjaTask]) -> None:
    tasks = [
        make_task(task_id=1, due_date=datetime(2026, 7, 18, 12, tzinfo=ZONE)),
        make_task(task_id=2, due_date=datetime(2026, 7, 19, 12, tzinfo=ZONE)),
        make_task(task_id=3, due_date=datetime(2026, 7, 20, 12, tzinfo=ZONE)),
    ]
    assert [task.category for task in classify(tasks, DigestKind.EVENING)] == [
        DueCategory.OVERDUE,
        DueCategory.TODAY,
    ]


def test_priority_then_due_date_then_title_ordering(
    make_task: Callable[..., VikunjaTask],
) -> None:
    tasks = [
        make_task(task_id=1, title="Zulu", due_date=NOW, priority=1),
        make_task(task_id=2, title="Beta", due_date=NOW, priority=4),
        make_task(task_id=3, title="Alpha", due_date=NOW, priority=4),
    ]
    assert [task.title for task in classify(tasks)] == ["Alpha", "Beta", "Zulu"]


def test_project_grouping(make_task: Callable[..., VikunjaTask]) -> None:
    tasks = [
        make_task(task_id=1, due_date=NOW, project_id=20),
        make_task(task_id=2, due_date=NOW, project_id=10),
    ]
    assert list(group_by_project(classify(tasks))) == ["Personal", "Work"]


def test_formatting_escapes_telegram_html_and_marks_priority(
    make_task: Callable[..., VikunjaTask],
) -> None:
    task = make_task(
        title="Fix <migration> & review",
        description="Never trust <script>alert(1)</script>",
        due_date=NOW,
        priority=3,
        labels=["needs review"],
    )
    rendered = format_digest(classify([task]), kind=DigestKind.MORNING, now=NOW, timezone=ZONE)
    assert rendered is not None
    assert "Fix &lt;migration&gt; &amp; review" in rendered
    assert "&lt;script&gt;" in rendered
    assert "<b>[HIGH P3]</b>" in rendered
    assert "#needs_review" in rendered
    assert 'href="https://tasks.example.test/tasks/1"' in rendered


def test_overdue_days_and_empty_sections(make_task: Callable[..., VikunjaTask]) -> None:
    task = make_task(due_date=datetime(2026, 7, 17, 10, tzinfo=ZONE))
    rendered = format_digest(classify([task]), kind=DigestKind.MORNING, now=NOW, timezone=ZONE)
    assert rendered is not None
    assert "2 days overdue" in rendered
    assert "Today's tasks" not in rendered
    assert "Upcoming" not in rendered


def test_empty_digest_returns_none() -> None:
    assert format_digest([], kind=DigestKind.MORNING, now=NOW, timezone=ZONE) is None


def test_evening_digest_includes_unfinished_summary(
    make_task: Callable[..., VikunjaTask],
) -> None:
    task = make_task(due_date=NOW)
    rendered = format_digest(
        classify([task], DigestKind.EVENING),
        kind=DigestKind.EVENING,
        now=NOW,
        timezone=ZONE,
    )
    assert rendered is not None
    assert "Unfinished: <b>1</b> due today, <b>0</b> overdue." in rendered


def test_zero_vikunja_due_date_becomes_none() -> None:
    task = VikunjaTask.model_validate(
        {"id": 1, "title": "No date", "project_id": 10, "due_date": "0001-01-01T00:00:00Z"}
    )
    assert task.due_date is None
