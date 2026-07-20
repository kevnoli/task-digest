from __future__ import annotations

import json
import re

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from task_digest.llm.base import SummaryError
from task_digest.models import DigestKind, DigestTask

_QUOTED_TEXT = re.compile(r'[“"]([^”"]+)[”"]')


class _FocusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    introduction: str
    referenced_task_ids: list[int]


class OpenAICompatibleSummaryProvider:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        include_descriptions: bool = False,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._model = model
        self._include_descriptions = include_descriptions
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=httpx.Timeout(timeout),
        )

    async def __aenter__(self) -> OpenAICompatibleSummaryProvider:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def summarize(self, tasks: list[DigestTask], kind: DigestKind) -> str:
        if not tasks:
            raise SummaryError("Cannot summarize an empty task list")
        supplied_tasks = [self._serialize_task(task) for task in tasks]
        prompt = (
            "Create a focus introduction of at most two short sentences for this "
            f"{kind.value} digest. Use only the supplied tasks and facts. If naming a task, "
            "copy its title exactly and put it in double quotes. Do not invent work, dates, "
            "priorities, labels, or project names. Return only a JSON object with keys "
            '"introduction" and "referenced_task_ids". The latter must contain only IDs from '
            "the supplied list.\n\nTasks:\n" + json.dumps(supplied_tasks, ensure_ascii=False)
        )
        try:
            response = await self._client.post(
                "chat/completions",
                json={
                    "model": self._model,
                    "temperature": 0.1,
                    "max_tokens": 140,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You write concise, strictly grounded task summaries.",
                        },
                        {"role": "user", "content": prompt},
                    ],
                },
            )
        except httpx.HTTPError:
            raise SummaryError("Could not connect to the LLM API") from None
        if response.is_error:
            raise SummaryError(f"LLM API request failed with HTTP {response.status_code}")

        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError
            parsed = _FocusResponse.model_validate_json(_strip_code_fence(content))
        except (KeyError, IndexError, TypeError, ValueError, ValidationError) as exc:
            raise SummaryError("LLM API returned an invalid summary response") from exc
        self._validate_grounding(parsed, tasks)
        return parsed.introduction.strip()

    def _serialize_task(self, task: DigestTask) -> dict[str, object]:
        result: dict[str, object] = {
            "id": task.id,
            "title": task.title,
            "project": task.project_name,
            "due_date": task.due_at.isoformat() if task.due_at is not None else None,
            "due_status": task.category.value,
            "days_overdue": task.days_overdue,
            "priority": task.priority,
            "labels": list(task.labels),
        }
        if self._include_descriptions:
            result["description"] = task.description
        return result

    @staticmethod
    def _validate_grounding(summary: _FocusResponse, tasks: list[DigestTask]) -> None:
        introduction = summary.introduction.strip()
        if not introduction or len(introduction) > 400:
            raise SummaryError("LLM summary was empty or too long")
        sentence_count = len([part for part in re.split(r"[.!?]+", introduction) if part.strip()])
        if sentence_count > 2:
            raise SummaryError("LLM summary exceeded two sentences")

        task_by_id = {task.id: task for task in tasks}
        if not summary.referenced_task_ids:
            raise SummaryError("LLM summary did not identify a supplied task")
        if any(task_id not in task_by_id for task_id in summary.referenced_task_ids):
            raise SummaryError("LLM summary referenced an unknown task")
        lowered = introduction.casefold()
        for task_id in summary.referenced_task_ids:
            if task_by_id[task_id].title.casefold() not in lowered:
                raise SummaryError("LLM summary did not use the supplied task title exactly")
        known_titles = {task.title.casefold() for task in tasks}
        if any(
            quoted.casefold() not in known_titles for quoted in _QUOTED_TEXT.findall(introduction)
        ):
            raise SummaryError("LLM summary contained an unknown quoted task")


def _strip_code_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1])
    return stripped
