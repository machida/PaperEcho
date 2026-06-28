"""Structured JSON progress emitted on stdout, consumed by the Rust backend.

One JSON object per line. Anything the pipeline needs to print for humans must
go to stderr so stdout stays a clean machine-readable stream.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def emit(event: str, **fields: Any) -> None:
    rec = {"event": event, **fields}
    sys.stdout.write(json.dumps(rec, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def progress(stage: str, pct: float, msg: str = "") -> None:
    emit("progress", stage=stage, pct=round(float(pct), 1), msg=msg)


def done(**artifacts: Any) -> None:
    emit("done", **artifacts)


def error(message: str) -> None:
    emit("error", message=message)


def log(*parts: Any) -> None:
    """Human-facing log line -> stderr (never pollutes the JSON stdout stream)."""
    print(*parts, file=sys.stderr, flush=True)
