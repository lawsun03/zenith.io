"""CPI-day router helpers (single source of truth for "is today a CPI day").

A CPI release prints 08:30 ET, so a "CPI day" is the America/New_York calendar
date of the release. These are pure functions of (timestamp, date-set); no I/O
except load_cpi_dates, which reuses the same news_events.csv the live straddle
scheduler reads. Keeping ET-date logic in one place avoids a UTC-vs-ET off-by-one
between the pretrade gate and the dashboard panel (see test_is_cpi_day_et_vs_utc_boundary).
"""
from __future__ import annotations

from datetime import datetime, timezone, date

from app.strategy.killzone import ET
from app.strategy.event_times import load_event_times


def _et_date(ts: datetime) -> date:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ET).date()


def load_cpi_dates(path: str, event_type: str = "CPI") -> frozenset[date]:
    """ET calendar dates of every `event_type` release in news_events.csv.

    Returns an empty set if the file is missing (load_event_times already
    degrades to [] on a missing path), so a misconfigured path = "no CPI days"
    = base engine runs every day, never a crash.
    """
    return frozenset(_et_date(t) for t in load_event_times(path, event_type))


def is_cpi_day(ts: datetime, cpi_dates: frozenset[date]) -> bool:
    if not cpi_dates:
        return False
    return _et_date(ts) in cpi_dates


def next_cpi_date(ts: datetime, cpi_dates: frozenset[date]) -> date | None:
    today = _et_date(ts)
    future = sorted(d for d in cpi_dates if d >= today)
    return future[0] if future else None


def router_state(ts: datetime, cpi_dates: frozenset[date], base_suppress: bool = True) -> dict:
    """Rule-13 dashboard view. Pure read; never mutates anything. base_entries_suppressed
    is True only when the base is actually suppressed (switch mode); in additive mode the
    base keeps trading on CPI days even though today_is_cpi_day is True."""
    active = is_cpi_day(ts, cpi_dates)
    nxt = next_cpi_date(ts, cpi_dates)
    return {
        "today_is_cpi_day": active,
        "next_cpi_date": nxt.isoformat() if nxt is not None else None,
        "base_entries_suppressed": active and base_suppress,
    }


def engine_cpi_dates(base_suppress: bool, cpi_dates: "frozenset[date]") -> "frozenset[date]":
    """Dates the EXECUTION ENGINE uses to block the base on CPI days (-> CPI_DAY_BLOCK).
    Only populated in switch mode (base_suppress=True); in additive mode (default) the base
    keeps trading -> empty set, while the news_straddle scheduler still arms independently.
    The B94/Lesson-163 income lever: additive +$304/mo vs switch +$8/mo (~40x)."""
    return cpi_dates if base_suppress else frozenset()




