"""Logging configuration with secret redaction."""

from __future__ import annotations

import logging
import sys

from thesis2lib.constants import SENSITIVE_LOG_PATTERN


class RedactionFilter(logging.Filter):
    """Redact log records that may contain secrets or personal data."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if SENSITIVE_LOG_PATTERN.search(message):
            record.msg = "[REDACTED]"
            record.args = ()
        return True


def configure_logging(level: str) -> None:
    numeric = getattr(logging, level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactionFilter())
    logging.basicConfig(
        level=numeric,
        format="%(levelname)s: %(message)s",
        handlers=[handler],
        force=True,
    )
