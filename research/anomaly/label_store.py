"""Durable, append-only record of regime labels as they come back.

A Grok label costs real money and takes a minute; the parquet table is only
written when a run finishes, so before this existed an interrupted run threw
away every label it had already paid for. Each label is appended here the
moment it is charged and flushed (fsync'd) before the next call, and the
table is rebuilt from this file at the start and end of every run.

One JSON object per line. A crash mid-write can leave a truncated final
line; `load_stored_labels` skips (and logs) any line it can't parse rather
than failing the whole load. If a session appears more than once the last
line wins.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

_FIELDS = (
    "instrument", "session_date", "regime_label", "regime_label_confidence",
    "regime_description", "regime_sources", "labelled_at",
)


def append_label(path: Path, update: dict) -> None:
    """Append one label (the same dict label_top_decile builds) and flush it
    to disk before returning."""
    record = {k: update[k] for k in _FIELDS}
    record["session_date"] = update["session_date"].isoformat()
    record["regime_sources"] = list(update["regime_sources"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
        f.flush()
        os.fsync(f.fileno())


def load_stored_labels(path: Path) -> list[dict]:
    """Every stored label, deduplicated by (instrument, session_date), last
    write wins, with session_date parsed back to a date."""
    if not path.exists():
        return []
    by_key: dict[tuple[str, date], dict] = {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            record["session_date"] = date.fromisoformat(record["session_date"])
            missing = [k for k in _FIELDS if k not in record]
            if missing:
                raise KeyError(missing)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.warning("skipping unreadable line %d of %s (%s)", lineno, path, exc)
            continue
        by_key[(record["instrument"], record["session_date"])] = {k: record[k] for k in _FIELDS}
    return list(by_key.values())
