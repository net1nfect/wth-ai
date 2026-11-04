"""Structured, rotating, secret-safe logging."""

from __future__ import annotations

import json
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


SECRET_PATTERN = re.compile(
    r"(?i)(authorization|password|secret|token|api[_-]?key)([\"'=:\s]+)([^,\s\"}]+)"
)


def mask_sensitive(value: Any) -> Any:
    """Recursively mask common secret fields."""
    if isinstance(value, dict):
        return {
            key: "***" if any(term in key.lower() for term in ("password", "secret", "token", "api_key", "authorization")) else mask_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_sensitive(item) for item in value]
    if isinstance(value, str):
        return SECRET_PATTERN.sub(r"\1\2***", value)
    return value


class JsonFormatter(logging.Formatter):
    """Format records as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_sensitive(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def get_logger(
    name: str,
    engagement_id: str | None = None,
    log_dir: str | Path | None = None,
) -> logging.Logger:
    """Create or return a configured logger."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)
    directory = Path(log_dir or os.environ.get("WTH_LOG_DIR", "logs/engagement_logs"))
    directory.mkdir(parents=True, exist_ok=True)
    suffix = engagement_id or "system"
    file_handler = RotatingFileHandler(
        directory / f"{suffix}.jsonl", maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.propagate = False
    return logger


def audit(logger: logging.Logger, event: str, details: dict[str, Any]) -> None:
    """Write a structured audit event."""
    logger.info("AUDIT %s", json.dumps(mask_sensitive({"event": event, **details}), sort_keys=True))
