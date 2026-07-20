from __future__ import annotations

import logging
from collections.abc import Iterable


class SecretRedactingFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, "[REDACTED]")
        record.msg = message
        record.args = ()
        return True


class SecretRedactingFormatter(logging.Formatter):
    def __init__(self, fmt: str, datefmt: str | None, secrets: Iterable[str]) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._secrets = tuple(secret for secret in secrets if secret)

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in self._secrets:
            rendered = rendered.replace(secret, "[REDACTED]")
        return rendered


def configure_logging(level: str, secrets: Iterable[str] = ()) -> None:
    secret_values = tuple(secrets)
    handler = logging.StreamHandler()
    handler.setFormatter(
        SecretRedactingFormatter(
            fmt="%(asctime)s level=%(levelname)s logger=%(name)s message=%(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
            secrets=secret_values,
        )
    )
    handler.addFilter(SecretRedactingFilter(secret_values))
    logging.basicConfig(level=level, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
