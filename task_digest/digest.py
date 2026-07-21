from __future__ import annotations

import html
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from task_digest.models import (
    DigestKind,
    DigestTask,
    DueCategory,
    SourceTask,
)

_SPACE_PATTERN = re.compile(r"\s+")
_CATEGORY_ORDER = {
    DueCategory.OVERDUE: 0,
    DueCategory.TODAY: 1,
    DueCategory.UPCOMING: 2,
    DueCategory.UNSCHEDULED: 3,
}


def classify_tasks(
    tasks: Iterable[SourceTask],
    *,
    now: datetime,
    timezone: ZoneInfo,
    upcoming_days: int,
    kind: DigestKind,
) -> list[DigestTask]:
    """Normalize and classify incomplete source tasks for one digest."""

    local_now = now.astimezone(timezone)
    today = local_now.date()
    classified: list[DigestTask] = []

    for task in tasks:
        if task.completed:
            continue
        days_overdue = 0
        if task.due_date is None:
            due_at = None
            category = DueCategory.UNSCHEDULED
        else:
            due_at = task.due_date.astimezone(timezone)
            due_date = due_at.date()
            if due_date < today:
                category = DueCategory.OVERDUE
                days_overdue = (today - due_date).days
            elif due_date == today:
                category = DueCategory.TODAY
            elif kind is DigestKind.MORNING and due_date <= today + timedelta(days=upcoming_days):
                category = DueCategory.UPCOMING
            else:
                continue

        classified.append(
            DigestTask(
                id=task.id,
                title=task.title,
                description=task.description,
                due_at=due_at,
                priority=task.priority,
                project_id=task.project_id,
                project_name=task.project_name,
                identifier=task.identifier,
                labels=tuple(task.labels),
                url=task.url,
                category=category,
                days_overdue=days_overdue,
            )
        )

    return sorted(classified, key=_task_sort_key)


def _task_sort_key(task: DigestTask) -> tuple[int, int, datetime, str]:
    return (
        _CATEGORY_ORDER[task.category],
        -task.priority,
        task.due_at or datetime.max.replace(tzinfo=UTC),
        task.title.casefold(),
    )


def group_by_project(tasks: Sequence[DigestTask]) -> dict[str, list[DigestTask]]:
    grouped: defaultdict[str, list[DigestTask]] = defaultdict(list)
    for task in tasks:
        grouped[task.project_name].append(task)
    return {
        project: sorted(project_tasks, key=_task_sort_key)
        for project, project_tasks in sorted(grouped.items(), key=lambda item: item[0].casefold())
    }


def format_digest(
    tasks: Sequence[DigestTask],
    *,
    kind: DigestKind,
    now: datetime,
    timezone: ZoneInfo,
    introduction: str | None = None,
) -> str | None:
    if not tasks:
        return None

    local_now = now.astimezone(timezone)
    title = "Morning checklist digest" if kind is DigestKind.MORNING else "Evening checklist digest"
    lines = [f"<b>{title}</b>", html.escape(local_now.strftime("%A, %d %B %Y"))]

    if introduction:
        lines.extend(["", html.escape(_truncate(_clean_text(introduction), 400), quote=True)])

    if kind is DigestKind.EVENING:
        unfinished_count = len(tasks)
        lines.extend(
            [
                "",
                f"Still unfinished: <b>{unfinished_count}</b> checklist "
                f"{'item' if unfinished_count == 1 else 'items'}.",
            ]
        )

    sections: tuple[tuple[DueCategory, str], ...] = (
        (DueCategory.OVERDUE, "Overdue"),
        (DueCategory.TODAY, "Today's tasks"),
        (DueCategory.UPCOMING, "Upcoming"),
        (DueCategory.UNSCHEDULED, "Unfinished checklist items"),
    )
    for category, heading in sections:
        section_tasks = [task for task in tasks if task.category is category]
        if not section_tasks:
            continue
        lines.extend(["", f"<b>{heading}</b>"])
        for project, project_tasks in group_by_project(section_tasks).items():
            lines.extend(["", _render_project_heading(project, project_tasks)])
            for task in project_tasks:
                lines.extend(_render_task(task))

    return "\n".join(lines).strip()


def _render_project_heading(project: str, tasks: Sequence[DigestTask]) -> str:
    title = html.escape(_truncate(_clean_text(project), 100), quote=True)
    url = html.escape(tasks[0].url, quote=True)
    seen: set[str] = set()
    labels: list[str] = []
    for task in tasks:
        for label in task.labels:
            if label not in seen:
                seen.add(label)
                labels.append(label)
    rendered_labels = [
        f"#{html.escape(_truncate(_clean_text(label), 40), quote=True).replace(' ', '_')}"
        for label in labels[:10]
    ]
    suffix = f" {' '.join(rendered_labels)}" if rendered_labels else ""
    return f'<a href="{url}"><b>{title}</b></a>{suffix}'


def _render_task(task: DigestTask) -> list[str]:
    title = html.escape(_truncate(_clean_text(task.title), 240), quote=True)
    return [f"• {title}"]


def _clean_text(value: str) -> str:
    return _SPACE_PATTERN.sub(" ", value).strip()


def _truncate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return f"{value[: maximum - 1].rstrip()}…"


def task_counts(tasks: Sequence[DigestTask]) -> Mapping[DueCategory, int]:
    return {category: sum(task.category is category for task in tasks) for category in DueCategory}
