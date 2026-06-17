"""
Topstep flatten-window math. Pure functions, DST-correct via America/Chicago.

The trading day runs 5:00 PM CT -> 3:10 PM CT next day. The flatten window
is [flatten_time_ct, 17:00 CT): inside it all positions must be closed and
no entries may open. The entry cutoff starts earlier so trades whose
expected hold spans the close are never opened.

CME early-close days (~5–7 per year): the exchange closes at 12:00 PM CT
(noon). The configured flatten (default 15:05) is after that close, so a
naive window would let positions ride through the noon close uncontrolled.
These days are handled automatically: on a known early-close trading day
in_flatten_window opens at 11:50 CT and past_entry_cutoff blocks entries
from 11:30 CT, overriding the (later) configured times. No manual config
change is needed. The early-close date list lives in _EARLY_CLOSE_DATES
below — update it each year from the CME calendar.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.risk.state import CT

_SESSION_OPEN = time(17, 0)  # 5:00 PM CT — new trading day

# CME early-close days: the exchange closes at 12:00 PM CT (noon). The
# configured flatten (15:05) fires after CME has already auto-closed, so on
# these days the window and entry cutoff are auto-shifted earlier. Dates are
# the *trading day* (see trading_day_ct). Update yearly from the CME calendar.
_EARLY_CLOSE_DATES: frozenset[date] = frozenset({
    date(2025, 11, 27),  # day before Thanksgiving
    date(2025, 12, 24),  # Christmas Eve
    date(2025, 12, 31),  # New Year's Eve
    date(2026, 4, 3),    # Good Friday
    date(2026, 7, 3),    # day before Independence Day
    date(2026, 11, 26),  # day before Thanksgiving
    date(2026, 12, 24),  # Christmas Eve
    date(2026, 12, 31),  # New Year's Eve
})
_EARLY_CLOSE_FLATTEN = time(11, 50)       # 10 min before the noon CME close
_EARLY_CLOSE_ENTRY_CUTOFF = time(11, 30)  # stop new entries 30 min before close


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


def is_early_close_day(ts: datetime) -> bool:
    """True when ts falls on a known CME early-close (12:00 PM CT) trading day."""
    return trading_day_ct(ts) in _EARLY_CLOSE_DATES


_DAILY_BREAK_START = time(16, 0)   # 16:00 CT — daily maintenance break begins
_EARLY_CLOSE_TIME = time(12, 0)    # noon CT — close on early-close days


def bars_expected(ts: datetime) -> bool:
    """True when live bars should be arriving for CME equity/metal futures.

    False during: the 16:00-17:00 CT daily maintenance break; the weekend gap
    (Fri 16:00 CT -> Sun 17:00 CT); and after the noon close on early-close days.
    DST-correct via CT. Used by the feed-dead watchdog to suppress false alarms.
    """
    ct = ts.astimezone(CT)
    t = ct.time()
    wd = ct.weekday()  # Mon=0 .. Sun=6

    # Daily maintenance break 16:00-17:00 CT (every trading day)
    if _DAILY_BREAK_START <= t < _SESSION_OPEN:
        return False

    # Weekend gap: Fri 16:00 CT -> Sun 17:00 CT
    if wd == 5:                                  # Saturday: closed all day
        return False
    if wd == 4 and t >= _DAILY_BREAK_START:      # Friday after 16:00 CT
        return False
    if wd == 6 and t < _SESSION_OPEN:            # Sunday before 17:00 CT
        return False

    # Early-close days: no bars from noon CT close to the 17:00 reopen
    if is_early_close_day(ts) and _EARLY_CLOSE_TIME <= t < _SESSION_OPEN:
        return False

    return True


def in_flatten_window(ts: datetime, flatten_time_ct: str) -> bool:
    """True when positions must be flat: [flatten_time, 5:00 PM CT).

    On early-close days the window opens at the earlier of the configured time
    and 11:50 CT, so positions are flattened before the noon CME close.
    """
    start = _parse_hhmm(flatten_time_ct)
    if is_early_close_day(ts):
        start = min(start, _EARLY_CLOSE_FLATTEN)
    t = ts.astimezone(CT).time()
    return start <= t < _SESSION_OPEN


def past_entry_cutoff(ts: datetime, entry_cutoff_time_ct: str) -> bool:
    """True when new entries are blocked: [entry_cutoff, 5:00 PM CT).

    On early-close days the cutoff is the earlier of the configured time and
    11:30 CT, so no entry is opened near the noon close.
    """
    start = _parse_hhmm(entry_cutoff_time_ct)
    if is_early_close_day(ts):
        start = min(start, _EARLY_CLOSE_ENTRY_CUTOFF)
    t = ts.astimezone(CT).time()
    return start <= t < _SESSION_OPEN
