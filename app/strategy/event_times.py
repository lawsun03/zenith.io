"""Economic event times loaded from a CSV.

Generic macro-release infrastructure: reads data/news_events.csv and returns
event timestamps by type (CPI, PPI, FOMC...). Preserved from the removed
news_straddle module because the research loop classifies macro-release
sessions with it.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo
from app.bot_config import StrategyParams
from app.sim.events import Bar
from app.strategy.composer import Signal
from app.strategy.grader import SetupGrader

log = logging.getLogger(__name__)


def load_event_times(path: str, event_type: str) -> list[datetime]:
    """Read release timestamps (UTC-aware) of one event_type from news_events.csv.

    CSV columns: event_type, ts_utc (ISO-8601 with offset). Returns [] if the
    file is missing so a misconfigured path degrades to "no events" rather than
    crashing the live runner build.
    """
    import csv
    import os

    if not os.path.exists(path):
        log.warning("events: file %s not found; no events loaded", path)
        return []
    out: list[datetime] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            if row.get("event_type") == event_type:
                out.append(datetime.fromisoformat(row["ts_utc"]))
    return sorted(out)
