"""Session-boundary conventions for the anomaly pass.

One canonical session definition is used for all three instruments, even
though GC's historical pit hours differ from NQ/ES: a per-instrument
session window would be exactly the per-instrument special-casing
CLAUDE.md rule 2 forbids at the IR layer, just one layer earlier. RTH is
fixed at the standard US index-futures window (09:30-16:00
America/New_York); "overnight" is everything between the prior session's
RTH close and the current session's RTH open — the full Globex session
preceding it, so it needs no separate bar range of its own (see
features.py: overnight move is just this session's RTH open minus the
prior session's RTH close).

A CME Globex trading day's bars span roughly 18:00 ET the previous
calendar day to 17:00 ET the named day. `with_session_date` assigns each
bar to that named day: anything at or after 18:00 ET belongs to the next
calendar day's session.
"""
from __future__ import annotations

from datetime import time

import polars as pl

NY_TZ = "America/New_York"

RTH_START = time(9, 30)
RTH_END = time(16, 0)

# First 30 minutes of RTH — this repo's existing ORB scripts' common
# default window; kept as a fixed pooled constant, not per-instrument.
OPENING_RANGE_END = time(10, 0)

_GLOBEX_ROLLOVER = time(18, 0)


def with_session_date(bars: pl.DataFrame) -> pl.DataFrame:
    """Add `local_ts` (America/New_York) and `session_date` columns to `bars`.

    `bars` must have a `ts` column that is a timezone-aware (UTC) Datetime,
    the shape research.data.loader.load_bars returns.
    """
    return bars.with_columns(
        pl.col("ts").dt.convert_time_zone(NY_TZ).alias("local_ts")
    ).with_columns(
        pl.when(pl.col("local_ts").dt.time() >= _GLOBEX_ROLLOVER)
        .then(pl.col("local_ts").dt.date().dt.offset_by("1d"))
        .otherwise(pl.col("local_ts").dt.date())
        .alias("session_date")
    )


def rth_mask() -> pl.Expr:
    return pl.col("local_ts").dt.time().is_between(RTH_START, RTH_END, closed="left")


def opening_range_mask() -> pl.Expr:
    return pl.col("local_ts").dt.time().is_between(RTH_START, OPENING_RANGE_END, closed="left")
