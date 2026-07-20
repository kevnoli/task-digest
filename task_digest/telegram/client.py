from __future__ import annotations

import asyncio
import html
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_TAG_PATTERN = re.compile(r"<[^>]*>")


class TelegramError(RuntimeError):
    """Raised when Telegram does not accept a message."""


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

    async def _send_message(self, text: str) -> None:
        endpoint = f"bot{self._bot_token}/sendMessage"
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._client.post(
                    endpoint,
                    json={
                        "chat_id": self._chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
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
            return

        raise TelegramError("Telegram retry loop ended unexpectedly")

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
