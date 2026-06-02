"""
Killzone gate — is this timestamp inside a session we trade?

Pure function over a timestamp; no state, no market data. Wraps the
sessions Lawrence trades:

  - Asia           19:00–22:00 ET    (Tokyo morning)
  - London open    02:00–05:00 ET
  - NY AM          08:30–11:00 ET
  - NY PM          13:00–16:00 ET  (10:00–13:00 PT)

ET (America/New_York) handles DST automatically — we never want to
chase a session because daylight saving moved. Storing windows in
ET and converting at check-time is the only safe way.

Outside any killzone, no entry signals fire. Exits are unaffected
(exits are governed by the bracket, not the killzone).

To pick a subset at runtime, call killzones_from_names(["london","ny_am"]).
The bot_config's `enabled_killzones` field uses these names.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
_ET = ET  # alias used by session/news helpers below


@dataclass(frozen=True)
class Killzone:
    """A named trading window in Eastern Time."""

    name: str
    start: time      # ET
    end: time        # ET (exclusive — a bar at end is OUT of session)


def asia() -> Killzone:
    return Killzone(name="Asia", start=time(19, 0), end=time(22, 0))


def london_open() -> Killzone:
    return Killzone(name="London", start=time(2, 0), end=time(5, 0))


def ny_am() -> Killzone:
    return Killzone(name="NY AM", start=time(8, 30), end=time(11, 0))


def ny_pm() -> Killzone:
    return Killzone(name="NY PM", start=time(13, 0), end=time(16, 0))


def london_ny() -> Killzone:
    """London/NY overlap — 06:00–08:30 ET. Active gold window between London close push and NY open."""
    return Killzone(name="London/NY", start=time(6, 0), end=time(8, 30))


def all_day() -> Killzone:
    """Whole-day window (00:00–23:59:59 ET). Disables the killzone time gate so
    the composer records sweeps every hour. Used for all-hours verification runs;
    note the grader's per-session range becomes a 24h range under this zone."""
    return Killzone(name="All", start=time(0, 0), end=time(23, 59, 59))


# Name → builder lookup. Used by bot_config so a JSON config can pick
# the subset of killzones without import gymnastics.
KILLZONE_BUILDERS = {
    "asia":      asia,
    "london":    london_open,
    "london_ny": london_ny,
    "ny_am":     ny_am,
    "ny_pm":     ny_pm,
    "all":       all_day,
}


def killzones_from_names(names: Iterable[str]) -> list[Killzone]:
    """Build a list of killzones from config names. Unknown names are skipped."""
    out: list[Killzone] = []
    for n in names:
        builder = KILLZONE_BUILDERS.get(n)
        if builder is not None:
            out.append(builder())
    return out


def default_killzones() -> list[Killzone]:
    """Lawrence's default killzones: London + NY AM + NY PM (no Asia)."""
    return [london_open(), ny_am(), ny_pm()]


def in_killzone(ts: datetime, zones: Iterable[Killzone]) -> Killzone | None:
    """
    Return the active killzone at ts, or None.

    `ts` may be UTC or any timezone — converted to ET internally. A
    naive datetime is rejected (more strict than the SDK because we
    don't want to silently assume UTC and get DST wrong).
    """
    if ts.tzinfo is None:
        raise ValueError(
            "in_killzone() requires a tz-aware datetime. "
            f"Got naive: {ts!r}"
        )
    et = ts.astimezone(ET)
    t = et.time()
    for zone in zones:
        # Windows don't cross midnight (none of ours do), so simple
        # comparison works. If you add a session like 22:00–02:00 ET,
        # this needs a wraparound branch.
        if zone.start <= t < zone.end:
            return zone
    return None


def killzone_for_bar(ts: datetime) -> Killzone | None:
    """Convenience: check against the default killzones."""
    return in_killzone(ts, default_killzones())


def _parse_time_range_et(range_str: str, ref_date) -> tuple[datetime, datetime]:
    """Parse 'HH:MM-HH:MM' (ET) into UTC datetimes for ref_date."""
    start_str, end_str = range_str.strip().split("-")
    sh, sm = int(start_str[:2]), int(start_str[3:])
    eh, em = int(end_str[:2]), int(end_str[3:])
    start_et = datetime(ref_date.year, ref_date.month, ref_date.day, sh, sm,
                        tzinfo=_ET)
    end_et = datetime(ref_date.year, ref_date.month, ref_date.day, eh, em,
                      tzinfo=_ET)
    return start_et.astimezone(timezone.utc), end_et.astimezone(timezone.utc)


def in_session_window(ts: datetime, windows: list[str]) -> bool:
    """True if ts (UTC) falls within any of the ET time-range strings."""
    if not windows:
        return True  # no filter configured = allow all
    ref = ts.astimezone(_ET).date()
    for w in windows:
        start, end = _parse_time_range_et(w, ref)
        if start <= ts < end:
            return True
    return False


def in_macro_window(ts: datetime, windows: list[str]) -> bool:
    """True if ts falls within a macro-window (grade bonus, not a hard block)."""
    return in_session_window(ts, windows)


def in_news_blackout(ts: datetime, blackout_windows: list[str]) -> bool:
    """
    True if ts falls within a UTC ISO-range string (e.g. '2026-06-06T12:30/2026-06-06T13:00').
    Returns False if blackout_windows is empty.
    """
    for w in blackout_windows:
        parts = w.strip().split("/")
        if len(parts) != 2:
            continue
        start = datetime.fromisoformat(parts[0])
        end = datetime.fromisoformat(parts[1])
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start <= ts < end:
            return True
    return False
