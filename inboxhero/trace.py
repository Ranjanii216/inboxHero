from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import config


def reset() -> None:
    """Start a fresh trace, e.g. at the beginning of a full run."""
    if config.TRACE_PATH.exists():
        config.TRACE_PATH.unlink()


def emit(event: str, cap: str | None = None, **fields: Any) -> None:
    """Append one event. Log ids and short reasons, not full message bodies."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "cap": cap,
        **fields,
    }
    config.TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.TRACE_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read(cap: str | None = None) -> list[dict]:
    """Read events back, optionally only those tagged with `cap`."""
    if not config.TRACE_PATH.exists():
        return []
    with config.TRACE_PATH.open(encoding="utf-8") as fh:
        events = [json.loads(line) for line in fh if line.strip()]
    return [e for e in events if cap is None or e.get("cap") == cap]


def count() -> int:
    """Number of events currently on disk. Use to diff before/after one run's own writes,
    since trace.jsonl accumulates across every invocation and is never cleared automatically."""
    if not config.TRACE_PATH.exists():
        return 0
    with config.TRACE_PATH.open(encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def tail(n: int) -> list[dict]:
    """The last n events on disk. Combine with count() to inspect only what THIS run just wrote:
    before = trace.count(); ...do work...; new_events = trace.tail(trace.count() - before)."""
    if n <= 0 or not config.TRACE_PATH.exists():
        return []
    with config.TRACE_PATH.open(encoding="utf-8") as fh:
        lines = [line for line in fh if line.strip()]
    return [json.loads(line) for line in lines[-n:]]