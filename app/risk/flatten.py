"""
Topstep flatten-window math. Pure functions, DST-correct via America/Chicago.

The trading day runs 5:00 PM CT -> 3:10 PM CT next day. The flatten window
is [flatten_time_ct, 17:00 CT): inside it all positions must be closed and
no entries may open. The entry cutoff starts earlier so trades whose
expected hold spans the close are never opened.

CME early-close days (~5–7 per year): the exchange closes at 12:00 PM CT
(noon). On these days, the last bar arrives at noon and bar-driven flatten
never fires (flatten_time_ct default 15:05 is after the close). The 30s
wall-clock backup task handles this by firing at 15:05 CT regardless of
bar arrival, but by then the CME has already auto-closed all positions.
Recommendation: on early-close days, set entry_cutoff_time_ct="11:30" to
prevent new entries near the early close.

Known early-close dates (adjust flatten_time_ct/entry_cutoff_time_ct manually):
  2025-11-27 — day before Thanksgiving (12:00 PM CT close)
  2025-12-24 — Christmas Eve (12:00 PM CT close)
  2025-12-31 — New Year's Eve (12:00 PM CT close)
  2026-04-03 — Good Friday (12:00 PM CT close)
  2026-07-03 — day before Independence Day (12:00 PM CT close)
  2026-11-26 — day before Thanksgiving (12:00 PM CT close)
  2026-12-24 — Christmas Eve (12:00 PM CT close)
  2026-12-31 — New Year's Eve (12:00 PM CT close)
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
