from __future__ import annotations

import json

import httpx
import pytest

from task_digest.anchor.client import (
    AnchorClient,
    AnchorError,
    AnchorNote,
    parse_unchecked_checklist,
)


def note_payload(
    *,
    content: str | None,
    pinned: bool = False,
    archived: bool = False,
) -> dict[str, object]:
    return {
        "id": "note-1",
        "title": "Groceries",
        "content": content,
        "isPinned": pinned,
        "isArchived": archived,
        "state": "active",
        "createdAt": "2026-07-20T12:00:00.000Z",
        "updatedAt": "2026-07-20T12:00:00.000Z",
        "userId": "user-1",
        "tagIds": ["tag-1"],
        "permission": "owner",
    }


def checklist_content() -> str:
    return json.dumps(
        {
            "ops": [
                {"insert": "Buy "},
                {"insert": "milk", "attributes": {"bold": True}},
                {"insert": "\n", "attributes": {"list": "unchecked"}},
                {"insert": "Bread"},
                {"insert": "\n", "attributes": {"list": "checked"}},
                {"insert": "Eggs & cheese\n", "attributes": {"list": "unchecked"}},
                {"insert": "Ordinary note\n"},
            ]
        }
    )


@pytest.mark.asyncio
async def test_anchor_authentication_and_checklist_mapping() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token-value"
        paths.append(request.url.path)
        if request.url.path == "/api/notes":
            return httpx.Response(
                200, json=[note_payload(content=checklist_content(), pinned=True)]
            )
        if request.url.path == "/api/tags":
            return httpx.Response(200, json=[{"id": "tag-1", "name": "Shopping"}])
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://anchor.test/api/"
    ) as http_client:
        tasks = await AnchorClient(
            "https://anchor.test",
            "token-value",
            "https://anchor.test",
            client=http_client,
        ).fetch_tasks()

    assert sorted(paths) == ["/api/notes", "/api/tags"]
    assert [task.title for task in tasks] == ["Buy milk", "Eggs & cheese"]
    assert [task.id for task in tasks] == ["note-1:0", "note-1:2"]
    assert tasks[0].project_name == "Groceries"
    assert tasks[0].priority == 3
    assert tasks[0].labels == ["Shopping"]
    assert tasks[0].url == "https://anchor.test/notes/note-1"


def test_checked_plain_malformed_and_archived_content_are_excluded() -> None:
    malformed = AnchorNote.model_validate(note_payload(content="not-json"))
    archived = AnchorNote.model_validate(note_payload(content=checklist_content(), archived=True))
    checked_only = AnchorNote.model_validate(
        note_payload(
            content=json.dumps(
                {
                    "ops": [
                        {"insert": "Already done"},
                        {"insert": "\n", "attributes": {"list": "checked"}},
                    ]
                }
            )
        )
    )

    assert parse_unchecked_checklist(malformed, {}, "https://anchor.test") == []
    assert parse_unchecked_checklist(archived, {}, "https://anchor.test") == []
    assert parse_unchecked_checklist(checked_only, {}, "https://anchor.test") == []


@pytest.mark.asyncio
async def test_anchor_http_failure_is_clear_and_secret_safe() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://anchor.test/api/"
    ) as http_client:
        client = AnchorClient(
            "https://anchor.test", "super-secret", "https://anchor.test", client=http_client
        )
        with pytest.raises(AnchorError, match="HTTP 503") as error:
            await client.fetch_tasks()
    assert "super-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_anchor_invalid_response_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/notes":
            return httpx.Response(200, json={"not": "a list"})
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://anchor.test/api/"
    ) as http_client:
        with pytest.raises(AnchorError, match="invalid notes response"):
            await AnchorClient(
                "https://anchor.test", "token", "https://anchor.test", client=http_client
            ).fetch_tasks()
