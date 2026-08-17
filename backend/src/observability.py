import json
import logging
import os
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


TRACE_HEADER = "x-trace-id"
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")

_SECRET_PATTERNS = (
    re.compile(r"(?i)(bearer\s+|token\s+|api[-_ ]?key\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"(?i)([?&](?:api[-_]?key|access[-_]?token|token|key)=)[^&\s]+"),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
)


def redact_secrets(value: str) -> str:
    """Remove credential-shaped data and configured secret values from log text."""
    redacted = value
    for secret_name, secret_value in os.environ.items():
        if secret_value and any(marker in secret_name.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            redacted = redacted.replace(secret_value, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else ''}[REDACTED]", redacted)
    return redacted


def get_trace_id() -> str:
    return _trace_id_ctx.get()


def set_trace_id(trace_id: str) -> None:
    _trace_id_ctx.set(trace_id)


def clear_trace_id() -> None:
    _trace_id_ctx.set("-")


def generate_trace_id() -> str:
    return uuid4().hex


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_secrets(record.getMessage()),
            "trace_id": getattr(record, "trace_id", "-"),
        }

        if record.exc_info:
            payload["exception"] = redact_secrets(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=True)


def configure_logging() -> None:
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(log_level)

    formatter = JsonLogFormatter()
    trace_filter = TraceIdFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(trace_filter)

    app_file_handler = logging.FileHandler(logs_dir / "backend.log")
    app_file_handler.setLevel(log_level)
    app_file_handler.setFormatter(formatter)
    app_file_handler.addFilter(trace_filter)

    error_file_handler = logging.FileHandler(logs_dir / "backend-error.log")
    error_file_handler.setLevel(logging.ERROR)
    error_file_handler.setFormatter(formatter)
    error_file_handler.addFilter(trace_filter)

    root_logger.addHandler(stream_handler)
    root_logger.addHandler(app_file_handler)
    root_logger.addHandler(error_file_handler)

    for logger_name in ("httpx", "httpcore"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
