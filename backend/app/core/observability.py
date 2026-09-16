"""Structured logging with request/tenant context and secret redaction.

Every log record automatically carries the current request context (request_id, trace_id,
tenant_id, hotel_id, conversation_id, channel). Call `log_event(logger, "name", **fields)`
for machine-readable events; `configure_logging` chooses JSON (production) or readable text.
"""

from contextvars import ContextVar
from datetime import UTC, datetime
import json
import logging
import re
from typing import Any

CONTEXT_FIELDS = ("request_id", "trace_id", "tenant_id", "hotel_id", "conversation_id", "channel")
_context: ContextVar[dict[str, str] | None] = ContextVar("request_context", default=None)

REDACTED = "[REDACTED]"
_SECRET_PATTERNS = [
    re.compile(r"sk-(?:ant-)?[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(api[_-]?key|password|secret|authorization|token)(\s*[=:]\s*)([^\s,;\"']{6,})"),
]
_SENSITIVE_KEYS = {"api_key", "anthropic_api_key", "authorization", "password", "secret", "token", "admin_api_tokens"}


def bind_context(**fields: str | None) -> None:
    current = dict(_context.get() or {})
    current.update({k: v for k, v in fields.items() if v is not None})
    _context.set(current)


def reset_context() -> None:
    _context.set({})


def current_context() -> dict[str, str]:
    return dict(_context.get() or {})


class Redactor:
    def __init__(self, secret_values: list[str] | None = None):
        self.secret_values = [v for v in (secret_values or []) if v]

    def redact_text(self, text: str) -> str:
        for value in self.secret_values:
            text = text.replace(value, REDACTED)
        for pattern in _SECRET_PATTERNS:
            if pattern.groups >= 3:
                text = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
            else:
                text = pattern.sub(REDACTED, text)
        return text

    def redact(self, value: Any, key: str | None = None) -> Any:
        if key is not None and key.lower() in _SENSITIVE_KEYS:
            return REDACTED
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {k: self.redact(v, k) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.redact(v) for v in value]
        return value


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        ctx = _context.get() or {}
        for name in CONTEXT_FIELDS:
            if not hasattr(record, name):
                setattr(record, name, ctx.get(name))
        return True


class RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor):
        super().__init__()
        self.redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.redactor.redact_text(record.msg)
        if record.args:
            record.args = tuple(self.redactor.redact(a) for a in record.args) if isinstance(record.args, tuple) else record.args
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            record.fields = self.redactor.redact(fields)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in CONTEXT_FIELDS:
            value = getattr(record, name, None)
            if value:
                payload[name] = value
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record)} {record.levelname} {record.name} {record.getMessage()}"
        ctx = " ".join(f"{n}={getattr(record, n)}" for n in CONTEXT_FIELDS if getattr(record, n, None))
        fields = getattr(record, "fields", None)
        extra = " ".join(f"{k}={v}" for k, v in fields.items()) if isinstance(fields, dict) else ""
        line = " ".join(part for part in (base, extra, ctx) if part)
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(level: str, fmt: str, secret_values: list[str] | None = None) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if fmt == "json" else TextFormatter())
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactingFilter(Redactor(secret_values)))
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_hotel_assistant", False):
            root.removeHandler(existing)
    handler._hotel_assistant = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a structured event. Never pass guest message text or secrets as fields."""
    logger.log(level, event, extra={"fields": fields})
