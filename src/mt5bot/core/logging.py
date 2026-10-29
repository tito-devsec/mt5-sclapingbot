"""Structured logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, ClassVar

_REDACTED = "***REDACTED***"
_PATTERNS = [
    re.compile(r"(?i)(password|passwd|api[_-]?key|token|secret|login)(\s*[=:]\s*)([^\s,;'\"]+)"),
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
]


class SecretRegistry:
    """Process-wide set of literal secret values to scrub from output."""

    _values: ClassVar[set[str]] = set()

    @classmethod
    def register(cls, value: str | None) -> None:
        if value and len(value) >= 3:
            cls._values.add(value)

    @classmethod
    def scrub(cls, text: str) -> str:
        for v in sorted(cls._values, key=len, reverse=True):
            text = text.replace(v, _REDACTED)
        for pat in _PATTERNS:
            if pat.groups >= 3:
                text = pat.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
            else:
                text = pat.sub(_REDACTED, text)
        return text


def register_settings_secrets(settings: Any) -> None:
    """Register every SecretStr value found in the settings tree."""
    from pydantic import BaseModel, SecretStr

    def walk(obj: Any) -> None:
        if isinstance(obj, SecretStr):
            SecretRegistry.register(obj.get_secret_value())
        elif isinstance(obj, BaseModel):
            for name in type(obj).model_fields:
                walk(getattr(obj, name))
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)
        elif isinstance(obj, dict):
            for item in obj.values():
                walk(item)

    walk(settings)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = SecretRegistry.scrub(record.getMessage())
        record.args = None
        if record.exc_info:
            record.exc_text = SecretRegistry.scrub(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            record.ctx = {k: SecretRegistry.scrub(str(v)) for k, v in extra.items()}
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ctx = getattr(record, "ctx", None)
        if isinstance(ctx, dict):
            payload.update(ctx)
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_format: bool = True) -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter())
    handler.setFormatter(
        JsonFormatter() if json_format else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(logger: logging.Logger, level: int, msg: str, **ctx: Any) -> None:
    logger.log(level, msg, extra={"ctx": ctx})
