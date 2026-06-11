"""
Topstep flatten-window math. Pure functions, DST-correct via America/Chicago.

The trading day runs 5:00 PM CT -> 3:10 PM CT next day. The flatten window
is [flatten_time_ct, 17:00 CT): inside it all positions must be closed and
no entries may open. The entry cutoff starts earlier so trades whose
expected hold spans the close are never opened.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.risk.state import CT

_SESSION_OPEN = time(17, 0)  # 5:00 PM CT — new trading day


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def trading_day_ct(ts: datetime) -> date:
    """Topstep trading day: rolls at 5:00 PM CT, so a 6 PM CT bar belongs
    to the NEXT calendar day's session."""
    ct = ts.astimezone(CT)
    d = ct.date()
    if ct.hour >= 17:
        d += timedelta(days=1)
    return d


def in_flatten_window(ts: datetime, flatten_time_ct: str) -> bool:
    """True when positions must be flat: [flatten_time, 5:00 PM CT)."""
    t = ts.astimezone(CT).time()
    return _parse_hhmm(flatten_time_ct) <= t < _SESSION_OPEN


def past_entry_cutoff(ts: datetime, entry_cutoff_time_ct: str) -> bool:
    """True when new entries are blocked: [entry_cutoff, 5:00 PM CT)."""
    t = ts.astimezone(CT).time()
    return _parse_hhmm(entry_cutoff_time_ct) <= t < _SESSION_OPEN
