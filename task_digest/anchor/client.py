from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from task_digest.models import SourceTask

logger = logging.getLogger(__name__)


class AnchorError(RuntimeError):
    """Raised when Anchor cannot return a valid API response."""


class AnchorTag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    color: str | None = None


class AnchorNote(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    title: str
    content: str | None = None
    is_pinned: bool = Field(default=False, alias="isPinned")
    is_archived: bool = Field(default=False, alias="isArchived")
    state: str = "active"
    updated_at: datetime = Field(alias="updatedAt")
    user_id: str = Field(alias="userId")
    tag_ids: list[str] = Field(default_factory=list, alias="tagIds")
    permission: str = "owner"


class AnchorClient:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        web_url: str,
        timeout: float = 15.0,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        root = base_url.rstrip("/")
        api_url = root if root.endswith("/api") else f"{root}/api"
        self._web_url = web_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{api_url}/",
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=httpx.Timeout(timeout),
        )
        self._client.headers.update(
            {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}
        )

    async def __aenter__(self) -> AnchorClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_tasks(self) -> list[SourceTask]:
        notes, tags = await asyncio.gather(self._fetch_notes(), self._fetch_tags())
        tag_names = {tag.id: tag.name for tag in tags}
        tasks = [
            task
            for note in notes
            for task in parse_unchecked_checklist(note, tag_names, self._web_url)
        ]
        logger.info(
            "anchor_fetch_complete notes=%d tags=%d unchecked_items=%d",
            len(notes),
            len(tags),
            len(tasks),
        )
        return tasks

    async def _fetch_notes(self) -> list[AnchorNote]:
        response = await self._get("notes")
        try:
            return TypeAdapter(list[AnchorNote]).validate_python(response.json())
        except (ValueError, ValidationError) as exc:
            raise AnchorError("Anchor returned an invalid notes response") from exc

    async def _fetch_tags(self) -> list[AnchorTag]:
        response = await self._get("tags")
        try:
            return TypeAdapter(list[AnchorTag]).validate_python(response.json())
        except (ValueError, ValidationError) as exc:
            raise AnchorError("Anchor returned an invalid tags response") from exc

    async def _get(self, path: str) -> httpx.Response:
        try:
            response = await self._client.get(path)
        except httpx.HTTPError:
            raise AnchorError("Could not connect to the Anchor API") from None
        if response.is_error:
            raise AnchorError(f"Anchor API request failed with HTTP {response.status_code}")
        return response


def parse_unchecked_checklist(
    note: AnchorNote,
    tag_names: Mapping[str, str],
    web_url: str,
) -> list[SourceTask]:
    """Convert unchecked Quill checklist rows in one Anchor note into tasks."""

    if note.state != "active" or note.is_archived or not note.content:
        return []
    try:
        payload = json.loads(note.content)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get("ops"), list):
        return []

    tasks: list[SourceTask] = []
    current_text: list[str] = []
    line_number = 0
    labels = [tag_names[tag_id] for tag_id in note.tag_ids if tag_id in tag_names]
    project_name = note.title.strip() or "Untitled note"

    for raw_operation in payload["ops"]:
        if not isinstance(raw_operation, dict):
            continue
        inserted = raw_operation.get("insert")
        if not isinstance(inserted, str):
            continue
        raw_attributes = raw_operation.get("attributes")
        attributes = raw_attributes if isinstance(raw_attributes, dict) else {}
        parts = inserted.split("\n")
        for index, part in enumerate(parts):
            if part:
                current_text.append(part)
            if index >= len(parts) - 1:
                continue
            text = "".join(current_text).strip()
            if attributes.get("list") == "unchecked" and text:
                tasks.append(
                    SourceTask(
                        id=f"{note.id}:{line_number}",
                        title=text,
                        priority=3 if note.is_pinned else 0,
                        project_id=note.id,
                        project_name=project_name,
                        labels=labels,
                        url=f"{web_url.rstrip('/')}/notes/{note.id}",
                    )
                )
            current_text = []
            line_number += 1

    return tasks
