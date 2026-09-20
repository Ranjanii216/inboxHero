"""Append-only JSONL event log used as evidence for every capability."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config


def reset(path: Path | None = None) -> None:
    p = path or config.TRACE_PATH
    if p.exists():
        p.unlink()


def emit(event: str, cap: str | None = None, **fields: Any) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "cap": cap,
        **fields,
    }
    config.TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.TRACE_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def read(cap: str | None = None, path: Path | None = None) -> list[dict]:
    """Read all events, optionally filtered by capability."""
    p = path or config.TRACE_PATH
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8") as fh:
        events = [json.loads(line) for line in fh if line.strip()]
    return [e for e in events if cap is None or e.get("cap") == cap]


# Backward compatibility aliases
trace = emit
read_trace = read
reset_trace = reset
