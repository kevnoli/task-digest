from __future__ import annotations

from typing import Protocol

from task_digest.models import DigestKind, DigestTask


class SummaryError(RuntimeError):
    """Raised when an optional summary provider cannot produce a safe summary."""


class SummaryProvider(Protocol):
    async def summarize(self, tasks: list[DigestTask], kind: DigestKind) -> str:
        """Return a short, task-grounded introduction."""
        ...
