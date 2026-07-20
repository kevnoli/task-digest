from __future__ import annotations

import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError

from task_digest.models import VikunjaProject, VikunjaTask

logger = logging.getLogger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)


class VikunjaError(RuntimeError):
    """Raised when Vikunja cannot return a valid API response."""


class VikunjaClient:
    def __init__(
        self,
        base_url: str,
        api_token: str,
        timeout: float = 15.0,
        *,
        client: httpx.AsyncClient | None = None,
        per_page: int = 50,
    ) -> None:
        root = base_url.rstrip("/")
        api_url = root if root.endswith("/api/v1") else f"{root}/api/v1"
        self._per_page = per_page
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{api_url}/",
            headers={"Authorization": f"Bearer {api_token}", "Accept": "application/json"},
            timeout=httpx.Timeout(timeout),
        )
        self._client.headers.update(
            {"Authorization": f"Bearer {api_token}", "Accept": "application/json"}
        )

    async def __aenter__(self) -> VikunjaClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def fetch_tasks(self) -> list[VikunjaTask]:
        adapter = TypeAdapter(list[VikunjaTask])
        return await self._get_paginated(
            "tasks",
            adapter,
            extra_params={
                "filter": "done = false",
                "sort_by": "id",
                "order_by": "asc",
            },
        )

    async def fetch_projects(self) -> list[VikunjaProject]:
        return await self._get_paginated("projects", TypeAdapter(list[VikunjaProject]))

    async def _get_paginated(
        self,
        path: str,
        adapter: TypeAdapter[list[ModelT]],
        *,
        extra_params: dict[str, str] | None = None,
    ) -> list[ModelT]:
        page = 1
        results: list[ModelT] = []
        while True:
            params: dict[str, str | int] = {
                "page": page,
                "per_page": self._per_page,
                **(extra_params or {}),
            }
            response = await self._get(path, params=params)
            try:
                page_items = adapter.validate_python(response.json())
            except (ValueError, ValidationError) as exc:
                raise VikunjaError(f"Vikunja returned an invalid {path} response") from exc
            results.extend(page_items)

            total_pages_header = response.headers.get("x-pagination-total-pages")
            if total_pages_header is not None:
                try:
                    total_pages = int(total_pages_header)
                except ValueError as exc:
                    raise VikunjaError("Vikunja returned an invalid pagination header") from exc
                if page >= total_pages:
                    break
            elif len(page_items) < self._per_page:
                break

            page += 1
            if page > 10_000:
                raise VikunjaError("Vikunja pagination exceeded the safety limit")

        logger.info("vikunja_fetch_complete resource=%s count=%d", path, len(results))
        return results

    async def _get(self, path: str, *, params: dict[str, str | int]) -> httpx.Response:
        try:
            response = await self._client.get(path, params=params)
        except httpx.HTTPError:
            raise VikunjaError("Could not connect to the Vikunja API") from None
        if response.is_error:
            raise VikunjaError(f"Vikunja API request failed with HTTP {response.status_code}")
        return response
