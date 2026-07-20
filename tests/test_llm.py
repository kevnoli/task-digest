from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from task_digest.llm import OpenAICompatibleSummaryProvider, SummaryError
from task_digest.models import DigestKind, DigestTask, DueCategory


def digest_task(description: str = "Sensitive details") -> DigestTask:
    return DigestTask(
        id="note-1:0",
        title="Fix database migration",
        description=description,
        due_at=datetime(2026, 7, 18, 10, tzinfo=ZoneInfo("America/Bahia")),
        priority=4,
        project_id="note-1",
        project_name="Work notes",
        identifier="",
        labels=("backend",),
        url="https://anchor.test/notes/note-1",
        category=DueCategory.OVERDUE,
        days_overdue=1,
    )


@pytest.mark.asyncio
async def test_llm_uses_structured_tasks_without_descriptions_by_default() -> None:
    request_body: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        request_body.update(json.loads(request.content))
        content = json.dumps(
            {
                "introduction": 'Suggested focus: "Fix database migration" is overdue.',
                "referenced_task_ids": ["note-1:0"],
            }
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://llm.test/v1/"
    ) as client:
        provider = OpenAICompatibleSummaryProvider(
            "https://llm.test/v1", "secret", "model", client=client
        )
        summary = await provider.summarize([digest_task()], DigestKind.MORNING)

    assert summary.startswith("Suggested focus")
    assert "Sensitive details" not in json.dumps(request_body)


@pytest.mark.asyncio
async def test_llm_rejects_unknown_task_reference() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        content = json.dumps(
            {"introduction": 'Do "Invented work" first.', "referenced_task_ids": ["unknown"]}
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://llm.test/v1/"
    ) as client:
        provider = OpenAICompatibleSummaryProvider(
            "https://llm.test/v1", "secret", "model", client=client
        )
        with pytest.raises(SummaryError, match="unknown task"):
            await provider.summarize([digest_task()], DigestKind.MORNING)
