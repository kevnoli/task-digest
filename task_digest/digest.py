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
    VikunjaProject,
    VikunjaTask,
)

_SPACE_PATTERN = re.compile(r"\s+")
_CATEGORY_ORDER = {
    DueCategory.OVERDUE: 0,
    DueCategory.TODAY: 1,
    DueCategory.UPCOMING: 2,
    DueCategory.UNSCHEDULED: 3,
}


def classify_tasks(
    tasks: Iterable[VikunjaTask],
    projects: Iterable[VikunjaProject],
    *,
    now: datetime,
    timezone: ZoneInfo,
    upcoming_days: int,
    kind: DigestKind,
    web_url: str,
) -> list[DigestTask]:
    """Normalize and classify incomplete Vikunja tasks for one digest."""

    local_now = now.astimezone(timezone)
    today = local_now.date()
    project_names = {project.id: project.title for project in projects}
    classified: list[DigestTask] = []

    for task in tasks:
        if task.done:
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
                project_name=project_names.get(task.project_id, f"Project {task.project_id}"),
                identifier=task.identifier,
                labels=tuple(label.title for label in task.labels),
                url=f"{web_url.rstrip('/')}/tasks/{task.id}",
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
    title = "Morning task digest" if kind is DigestKind.MORNING else "Evening task digest"
    lines = [f"<b>{title}</b>", html.escape(local_now.strftime("%A, %d %B %Y"))]

    if introduction:
        lines.extend(["", html.escape(_truncate(_clean_text(introduction), 400), quote=True)])

    if kind is DigestKind.EVENING:
        today_count = sum(task.category is DueCategory.TODAY for task in tasks)
        overdue_count = sum(task.category is DueCategory.OVERDUE for task in tasks)
        unscheduled_count = sum(task.category is DueCategory.UNSCHEDULED for task in tasks)
        lines.extend(
            [
                "",
                f"Unfinished: <b>{today_count}</b> due today, "
                f"<b>{overdue_count}</b> overdue, "
                f"<b>{unscheduled_count}</b> without due dates.",
            ]
        )

    sections: tuple[tuple[DueCategory, str], ...] = (
        (DueCategory.OVERDUE, "Overdue"),
        (DueCategory.TODAY, "Today's tasks"),
        (DueCategory.UPCOMING, "Upcoming"),
        (DueCategory.UNSCHEDULED, "No due date"),
    )
    for category, heading in sections:
        section_tasks = [task for task in tasks if task.category is category]
        if not section_tasks:
            continue
        lines.extend(["", f"<b>{heading}</b>"])
        for project, project_tasks in group_by_project(section_tasks).items():
            lines.extend(["", f"<b>{html.escape(_truncate(project, 100), quote=True)}</b>"])
            for task in project_tasks:
                lines.extend(_render_task(task, local_now))

    return "\n".join(lines).strip()


def _render_task(task: DigestTask, local_now: datetime) -> list[str]:
    title = html.escape(_truncate(_clean_text(task.title), 240), quote=True)
    url = html.escape(task.url, quote=True)
    due = _due_text(task, local_now)
    priority = f" <b>[HIGH P{task.priority}]</b>" if task.priority >= 3 else ""
    labels = ""
    if task.labels:
        rendered = [
            f"#{html.escape(_truncate(_clean_text(label), 40), quote=True).replace(' ', '_')}"
            for label in task.labels[:10]
        ]
        labels = " " + " ".join(rendered)
    identifier = f" [{html.escape(task.identifier, quote=True)}]" if task.identifier.strip() else ""
    lines = [f'• <a href="{url}">{title}</a>{identifier} — {html.escape(due)}{priority}{labels}']
    description = _clean_text(task.description)
    if description:
        lines.append(f"  ↳ {html.escape(_truncate(description, 240), quote=True)}")
    return lines


def _due_text(task: DigestTask, local_now: datetime) -> str:
    due = task.due_at
    if due is None:
        return "no due date"
    time_suffix = "" if (due.hour, due.minute) == (0, 0) else f" at {due:%H:%M}"
    if task.category is DueCategory.OVERDUE:
        unit = "day" if task.days_overdue == 1 else "days"
        return f"{task.days_overdue} {unit} overdue{time_suffix}"
    if task.category is DueCategory.TODAY:
        return f"due today{time_suffix}"
    days_until = (due.date() - local_now.date()).days
    if days_until == 1:
        return f"tomorrow{time_suffix}"
    return f"in {days_until} days ({due:%a %d %b}){time_suffix}"


def _clean_text(value: str) -> str:
    return _SPACE_PATTERN.sub(" ", value).strip()


def _truncate(value: str, maximum: int) -> str:
    if len(value) <= maximum:
        return value
    return f"{value[: maximum - 1].rstrip()}…"


def task_counts(tasks: Sequence[DigestTask]) -> Mapping[DueCategory, int]:
    return {category: sum(task.category is category for task in tasks) for category in DueCategory}
