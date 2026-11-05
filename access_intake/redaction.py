"""Credential redaction for logs, error messages and request evidence.

Redaction happens in the log formatter, so it also covers interpolated
arguments and exception tracebacks.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path

REDACTED = "[REDACTED]"
_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]+")
_DATABRICKS_TOKEN = re.compile(r"dapi[0-9a-fA-F]{16,}(-\d+)?")
_AUTH_HEADER = re.compile(r"(?i)(\"?authorization\"?\s*[:=]\s*\"?)[^\"\s,}]+(\s+[^\"\s,}]+)?")


def redact(text: str) -> str:
    """Remove the configured API key and common token shapes from text."""
    if not text:
        return text
    key = os.environ.get("API_KEY", "").strip()
    if len(key) >= 4:
        text = text.replace(key, REDACTED)
    text = _BEARER.sub(r"\1" + REDACTED, text)
    text = _AUTH_HEADER.sub(r"\1" + REDACTED, text)
    text = _DATABRICKS_TOKEN.sub(REDACTED, text)
    return text


def redact_headers(headers: dict) -> dict:
    """Copy of an HTTP header dict that is safe to persist as evidence."""
    return {k: (REDACTED if k.lower() == "authorization" else redact(str(v))) for k, v in headers.items()}


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str = "INFO", log_file: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("access_intake")
    logger.setLevel(level.upper())
    logger.handlers.clear()
    fmt = RedactingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(fmt)
        logger.addHandler(h)
    logger.propagate = False
    return logger
