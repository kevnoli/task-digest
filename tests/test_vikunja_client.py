from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from task_digest.vikunja import VikunjaClient, VikunjaError

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_vikunja_pagination_and_authentication() -> None:
    seen_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token-value"
        page = request.url.params["page"]
        seen_pages.append(page)
        fixture = "tasks_page_1.json" if page == "1" else "tasks_page_2.json"
        return httpx.Response(
            200,
            json=load_fixture(fixture),
            headers={"x-pagination-total-pages": "2"},
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://vikunja.test/api/v1/"
    ) as http_client:
        client = VikunjaClient(
            "https://vikunja.test", "token-value", client=http_client, per_page=2
        )
        tasks = await client.fetch_tasks()

    assert [task.id for task in tasks] == [41, 42, 43]
    assert seen_pages == ["1", "2"]


@pytest.mark.asyncio
async def test_vikunja_pagination_without_headers_stops_on_short_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "1":
            return httpx.Response(200, json=load_fixture("tasks_page_1.json"))
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://vikunja.test/api/v1/"
    ) as http_client:
        client = VikunjaClient("https://vikunja.test", "token", client=http_client, per_page=2)
        tasks = await client.fetch_tasks()

    assert len(tasks) == 2


@pytest.mark.asyncio
async def test_fetch_projects_uses_typed_models() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=load_fixture("projects.json"))

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://vikunja.test/api/v1/"
    ) as http_client:
        projects = await VikunjaClient(
            "https://vikunja.test", "token", client=http_client
        ).fetch_projects()

    assert [(project.id, project.title) for project in projects] == [(7, "Work"), (8, "Personal")]


@pytest.mark.asyncio
async def test_vikunja_http_failure_is_clear_and_secret_safe() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://vikunja.test/api/v1/"
    ) as http_client:
        client = VikunjaClient("https://vikunja.test", "super-secret", client=http_client)
        with pytest.raises(VikunjaError, match="HTTP 503") as error:
            await client.fetch_tasks()
    assert "super-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_vikunja_invalid_response_is_rejected() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"not": "a list"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://vikunja.test/api/v1/"
    ) as http_client:
        with pytest.raises(VikunjaError, match="invalid tasks response"):
            await VikunjaClient("https://vikunja.test", "token", client=http_client).fetch_tasks()
