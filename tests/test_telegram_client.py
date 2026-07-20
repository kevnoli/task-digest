from __future__ import annotations

import httpx
import pytest

from task_digest.telegram import TelegramClient, TelegramError, split_telegram_html


def test_message_splitting_respects_limit_and_content() -> None:
    message = "\n".join(f"line {index}: " + "x" * 40 for index in range(25))
    chunks = split_telegram_html(message, limit=120)

    assert len(chunks) > 1
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert "\n".join(chunks) == message


def test_long_html_line_is_split_as_valid_escaped_plain_text() -> None:
    chunks = split_telegram_html(f"<b>{'x' * 200}</b>", limit=64)
    assert all(len(chunk) <= 64 for chunk in chunks)
    assert all("<b>" not in chunk for chunk in chunks)
    assert "".join(chunks) == "x" * 200


def test_empty_message_produces_no_chunks() -> None:
    assert split_telegram_html(" \n ") == []


@pytest.mark.asyncio
async def test_telegram_sends_html_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.telegram.org/"
    ) as http_client:
        client = TelegramClient("bot-secret", "-100123", client=http_client)
        count = await client.send_digest("<b>Hello</b>")

    assert count == 1
    assert requests[0].url.path == "/botbot-secret/sendMessage"
    payload = requests[0].read().decode()
    assert '"parse_mode":"HTML"' in payload
    assert '"chat_id":"-100123"' in payload


@pytest.mark.asyncio
async def test_telegram_api_failure_raises_and_does_not_leak_token() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "bad chat"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.telegram.org/"
    ) as http_client:
        client = TelegramClient("bot-secret", "123", client=http_client, max_retries=0)
        with pytest.raises(TelegramError, match="HTTP 400") as error:
            await client.send_digest("hello")
    assert "bot-secret" not in str(error.value)


@pytest.mark.asyncio
async def test_telegram_retries_temporary_failure() -> None:
    attempts = 0
    delays: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"ok": False}, headers={"retry-after": "0"})
        return httpx.Response(200, json={"ok": True})

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.telegram.org/"
    ) as http_client:
        client = TelegramClient("token", "123", client=http_client, max_retries=1, sleep=fake_sleep)
        await client.send_digest("hello")

    assert attempts == 2
    assert delays == [0.0]
