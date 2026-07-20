from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _parse_optional_datetime(value: object) -> object:
    if value in (None, "", "0001-01-01T00:00:00Z", "0001-01-01T00:00:00+00:00"):
        return None
    return value


class SourceTask(BaseModel):
    """Source-neutral task produced by an integration."""

    model_config = ConfigDict(extra="ignore")

    id: str
    title: str
    description: str = ""
    completed: bool = False
    due_date: datetime | None = None
    priority: int = 0
    project_id: str
    project_name: str
    identifier: str = ""
    labels: list[str] = Field(default_factory=list)
    url: str

    _normalize_due_date = field_validator("due_date", mode="before")(_parse_optional_datetime)

    @field_validator("due_date")
    @classmethod
    def ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class DigestKind(StrEnum):
    MORNING = "morning"
    EVENING = "evening"


class DueCategory(StrEnum):
    OVERDUE = "overdue"
    TODAY = "today"
    UPCOMING = "upcoming"
    UNSCHEDULED = "unscheduled"


class DigestTask(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    description: str
    due_at: datetime | None
    priority: int
    project_id: str
    project_name: str
    identifier: str
    labels: tuple[str, ...]
    url: str
    category: DueCategory
    days_overdue: int = 0
