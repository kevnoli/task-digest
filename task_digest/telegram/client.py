from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_TAG_PATTERN = re.compile(r"<[^>]*>")


class TelegramError(RuntimeError):
    """Raised when Telegram does not accept a message."""


@dataclass(frozen=True)
class TelegramUpdate:
    """Minimal Telegram update data needed by the command listener."""

    update_id: int
    chat_id: str | None
    text: str | None

    def is_command(self, command: str) -> bool:
        if self.text is None:
            return False
        words = self.text.strip().split(maxsplit=1)
        if not words:
            return False
        first_word = words[0].lower()
        expected = f"/{command.lower()}"
        return first_word == expected or first_word.startswith(f"{expected}@")


def _plain_long_line(line: str, limit: int) -> list[str]:
    plain = html.unescape(_TAG_PATTERN.sub("", line))
    chunks: list[str] = []
    while plain:
        low, high = 1, min(len(plain), limit)
        best = 1
        while low <= high:
            middle = (low + high) // 2
            escaped = html.escape(plain[:middle], quote=True)
            if len(escaped) <= limit:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        cut = best
        if cut < len(plain):
            whitespace = plain.rfind(" ", 0, cut)
            if whitespace > cut // 2:
                cut = whitespace + 1
        chunks.append(html.escape(plain[:cut].rstrip(), quote=True))
        plain = plain[cut:].lstrip()
    return chunks or [""]


def split_telegram_html(message: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Split rendered digest HTML at safe line boundaries.

    Digest lines contain only self-contained tags. An abnormally long line is
    converted to escaped plain text before splitting so no malformed tag or
    entity can be sent to Telegram.
    """

    if limit < 32:
        raise ValueError("message limit must be at least 32 characters")
    if not message.strip():
        return []

    lines: list[str] = []
    for line in message.strip().splitlines():
        if len(line) <= limit:
            lines.append(line)
        else:
            lines.extend(_plain_long_line(line, limit))

    messages: list[str] = []
    current = ""
    for line in lines:
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                messages.append(current)
            current = line
    if current:
        messages.append(current)
    return messages


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout: float = 15.0,
        max_retries: int = 3,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._timeout = timeout
        self._max_retries = max_retries
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url="https://api.telegram.org/", timeout=httpx.Timeout(timeout)
        )

    async def __aenter__(self) -> TelegramClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def send_digest(self, message: str) -> int:
        chunks = split_telegram_html(message)
        for index, chunk in enumerate(chunks, start=1):
            await self._send_message(chunk)
            logger.info("telegram_chunk_sent chunk=%d total=%d", index, len(chunks))
        return len(chunks)

    async def register_digest_command(self) -> None:
        """Expose /digest in Telegram's bot command menu."""

        await self._post_api(
            "setMyCommands",
            {
                "commands": [{"command": "digest", "description": "Send the digest now"}],
                "scope": {"type": "chat", "chat_id": self._chat_id},
            },
        )

    async def get_updates(
        self, *, offset: int | None = None, poll_timeout: int = 25, limit: int = 100
    ) -> list[TelegramUpdate]:
        """Long-poll Telegram for new message updates."""

        payload: dict[str, object] = {
            "timeout": poll_timeout,
            "limit": limit,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        response = await self._post_api(
            "getUpdates",
            payload,
            request_timeout=max(self._timeout, poll_timeout + 5.0),
        )
        result = response.get("result")
        if not isinstance(result, list):
            raise TelegramError("Telegram returned an invalid updates response")
        return [update for item in result if (update := self._parse_update(item)) is not None]

    async def _send_message(self, text: str) -> None:
        await self._post_api(
            "sendMessage",
            {
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )

    async def _post_api(
        self,
        method: str,
        payload_data: dict[str, object],
        *,
        request_timeout: float | None = None,
    ) -> dict[str, Any]:
        endpoint = f"/bot{self._bot_token}/{method}"
        for attempt in range(self._max_retries + 1):
            try:
                if request_timeout is None:
                    response = await self._client.post(endpoint, json=payload_data)
                else:
                    response = await self._client.post(
                        endpoint,
                        json=payload_data,
                        timeout=request_timeout,
                    )
            except httpx.HTTPError:
                if attempt >= self._max_retries:
                    raise TelegramError("Could not connect to the Telegram API") from None
                await self._sleep(2**attempt)
                continue

            payload: object
            try:
                payload = response.json()
            except ValueError:
                payload = None

            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < self._max_retries:
                await self._sleep(self._retry_delay(response, payload, attempt))
                continue
            if response.is_error:
                raise TelegramError(f"Telegram API request failed with HTTP {response.status_code}")
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                description = payload.get("description") if isinstance(payload, dict) else None
                suffix = f": {description}" if isinstance(description, str) else ""
                raise TelegramError(f"Telegram rejected the message{suffix}")
            return payload

        raise TelegramError("Telegram retry loop ended unexpectedly")

    @staticmethod
    def _parse_update(payload: object) -> TelegramUpdate | None:
        if not isinstance(payload, dict):
            return None
        update_id = payload.get("update_id")
        if not isinstance(update_id, int):
            return None
        message = payload.get("message")
        if not isinstance(message, dict):
            return TelegramUpdate(update_id=update_id, chat_id=None, text=None)
        chat = message.get("chat")
        raw_chat_id = chat.get("id") if isinstance(chat, dict) else None
        chat_id = str(raw_chat_id) if isinstance(raw_chat_id, (str, int)) else None
        text = message.get("text")
        return TelegramUpdate(
            update_id=update_id,
            chat_id=chat_id,
            text=text if isinstance(text, str) else None,
        )

    @staticmethod
    def _retry_delay(response: httpx.Response, payload: object, attempt: int) -> float:
        header = response.headers.get("retry-after")
        if header is not None:
            try:
                return max(float(header), 0.0)
            except ValueError:
                pass
        if isinstance(payload, dict):
            parameters = payload.get("parameters")
            if isinstance(parameters, dict):
                retry_after = parameters.get("retry_after")
                if isinstance(retry_after, (int, float)):
                    return max(float(retry_after), 0.0)
        return float(2**attempt)
