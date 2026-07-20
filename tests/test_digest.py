from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from task_digest.digest import classify_tasks, format_digest, group_by_project
from task_digest.models import DigestKind, DueCategory, SourceTask

ZONE = ZoneInfo("America/Bahia")
NOW = datetime(2026, 7, 19, 10, 0, tzinfo=ZONE)


def classify(
    tasks: list[SourceTask], kind: DigestKind = DigestKind.MORNING, upcoming_days: int = 3
):
    return classify_tasks(
        tasks,
        now=NOW,
        timezone=ZONE,
        upcoming_days=upcoming_days,
        kind=kind,
    )


def test_timezone_aware_classification(make_task: Callable[..., SourceTask]) -> None:
    tasks = [
        make_task(task_id="1", due_date=datetime(2026, 7, 18, 23, 59, tzinfo=ZONE)),
        make_task(task_id="2", due_date=datetime(2026, 7, 19, 23, 59, tzinfo=ZONE)),
        make_task(task_id="3", due_date=datetime(2026, 7, 22, 0, 0, tzinfo=ZONE)),
        make_task(task_id="4", due_date=datetime(2026, 7, 23, 0, 0, tzinfo=ZONE)),
    ]

    result = classify(tasks)

    assert [task.category for task in result] == [
        DueCategory.OVERDUE,
        DueCategory.TODAY,
        DueCategory.UPCOMING,
    ]
    assert result[0].days_overdue == 1


def test_utc_timestamp_is_converted_to_configured_timezone(
    make_task: Callable[..., SourceTask],
) -> None:
    task = make_task(due_date=datetime.fromisoformat("2026-07-20T01:00:00+00:00"))
    assert classify([task])[0].category is DueCategory.TODAY


def test_completed_tasks_are_excluded_and_undated_tasks_are_included(
    make_task: Callable[..., SourceTask],
) -> None:
    result = classify(
        [
            make_task(task_id="1", due_date=NOW, completed=True),
            make_task(task_id="2", due_date=None),
        ]
    )

    assert len(result) == 1
    assert result[0].id == "2"
    assert result[0].category is DueCategory.UNSCHEDULED
    assert result[0].due_at is None


def test_evening_excludes_upcoming_tasks(make_task: Callable[..., SourceTask]) -> None:
    tasks = [
        make_task(task_id="1", due_date=datetime(2026, 7, 18, 12, tzinfo=ZONE)),
        make_task(task_id="2", due_date=datetime(2026, 7, 19, 12, tzinfo=ZONE)),
        make_task(task_id="3", due_date=datetime(2026, 7, 20, 12, tzinfo=ZONE)),
    ]
    assert [task.category for task in classify(tasks, DigestKind.EVENING)] == [
        DueCategory.OVERDUE,
        DueCategory.TODAY,
    ]


def test_priority_then_due_date_then_title_ordering(
    make_task: Callable[..., SourceTask],
) -> None:
    tasks = [
        make_task(task_id="1", title="Zulu", due_date=NOW, priority=1),
        make_task(task_id="2", title="Beta", due_date=NOW, priority=3),
        make_task(task_id="3", title="Alpha", due_date=NOW, priority=3),
    ]
    assert [task.title for task in classify(tasks)] == ["Alpha", "Beta", "Zulu"]


def test_note_grouping(make_task: Callable[..., SourceTask]) -> None:
    tasks = [
        make_task(task_id="1", project_id="b", project_name="Personal"),
        make_task(task_id="2", project_id="a", project_name="Work"),
    ]
    assert list(group_by_project(classify(tasks))) == ["Personal", "Work"]


def test_formatting_escapes_html_and_puts_tags_on_note_heading(
    make_task: Callable[..., SourceTask],
) -> None:
    task = make_task(
        title="Fix <migration> & review",
        description="Never trust <script>alert(1)</script>",
        priority=3,
        labels=["needs review"],
    )
    rendered = format_digest(classify([task]), kind=DigestKind.MORNING, now=NOW, timezone=ZONE)
    assert rendered is not None
    assert "Fix &lt;migration&gt; &amp; review" in rendered
    assert "&lt;script&gt;" not in rendered
    assert "<b>Work</b> #needs_review" in rendered
    assert (
        '• <a href="https://anchor.example.test/notes/note-1">'
        "Fix &lt;migration&gt; &amp; review</a>" in rendered
    )
    assert "unchecked" not in rendered


def test_overdue_section_omits_empty_sections(make_task: Callable[..., SourceTask]) -> None:
    task = make_task(due_date=datetime(2026, 7, 17, 10, tzinfo=ZONE))
    rendered = format_digest(classify([task]), kind=DigestKind.MORNING, now=NOW, timezone=ZONE)
    assert rendered is not None
    assert "<b>Overdue</b>" in rendered
    assert "Today's tasks" not in rendered
    assert "Upcoming" not in rendered


def test_empty_digest_returns_none() -> None:
    assert format_digest([], kind=DigestKind.MORNING, now=NOW, timezone=ZONE) is None


def test_unchecked_section_is_grouped_and_priority_sorted(
    make_task: Callable[..., SourceTask],
) -> None:
    tasks = [
        make_task(task_id="1", title="Low", priority=0),
        make_task(task_id="2", title="High", priority=3),
        make_task(task_id="3", title="Personal", project_id="p", project_name="Personal"),
    ]

    classified = classify(tasks)
    rendered = format_digest(classified, kind=DigestKind.MORNING, now=NOW, timezone=ZONE)

    assert [task.title for task in classified] == ["High", "Low", "Personal"]
    assert rendered is not None
    assert "<b>Unfinished checklist items</b>" in rendered
    assert rendered.index("High") < rendered.index("Low")
    assert "unchecked" not in rendered


def test_evening_digest_includes_unfinished_summary(
    make_task: Callable[..., SourceTask],
) -> None:
    rendered = format_digest(
        classify([make_task()], DigestKind.EVENING),
        kind=DigestKind.EVENING,
        now=NOW,
        timezone=ZONE,
    )
    assert rendered is not None
    assert "Still unfinished: <b>1</b> checklist item." in rendered


def test_naive_source_due_date_is_treated_as_utc() -> None:
    task = SourceTask(
        id="1",
        title="Date",
        due_date=datetime(2026, 7, 19, 12),
        project_id="n",
        project_name="Note",
        url="https://anchor.test/notes/n",
    )
    assert task.due_date is not None
    assert task.due_date.tzinfo is not None
