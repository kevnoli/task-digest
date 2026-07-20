from __future__ import annotations

import logging

import pytest

from task_digest.logging import configure_logging


def test_secret_is_redacted_from_message_and_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO", ["top-secret-token"])
    logger = logging.getLogger("redaction-test")
    try:
        raise RuntimeError("failed with top-secret-token")
    except RuntimeError:
        logger.exception("request token=top-secret-token")

    captured = capsys.readouterr()
    assert "top-secret-token" not in captured.err
    assert "[REDACTED]" in captured.err
