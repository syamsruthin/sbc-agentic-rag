"""JSON log lines (one per request, one per turn), with the request ID; never user text."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        out.update({k: v for k, v in record.__dict__.items() if k not in _STANDARD})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


def configure(level: str = "INFO", log_file: str | None = None) -> None:
    """JSON lines on stdout (CloudWatch on AWS) and, when ``log_file`` (default: ``LOG_FILE``) is set, the
    same lines appended to that file (the full local stack's ``.local/logs/api.jsonl``)."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    path = log_file if log_file is not None else os.environ.get("LOG_FILE")
    if path:
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level)
