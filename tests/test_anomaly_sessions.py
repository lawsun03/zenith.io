"""Session-boundary conventions: Globex rollover assigns a bar to the
correct trade date, and RTH/opening-range masks are half-open at the
boundary."""
from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl

from research.anomaly.sessions import (
    OPENING_RANGE_END,
    RTH_END,
    RTH_START,
    opening_range_mask,
    rth_mask,
    with_session_date,
)


def _bars(utc_times: list[datetime]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ts": utc_times,
            "open": [1.0] * len(utc_times),
            "high": [1.0] * len(utc_times),
            "low": [1.0] * len(utc_times),
            "close": [1.0] * len(utc_times),
            "volume": [1] * len(utc_times),
            "instrument": ["NQ"] * len(utc_times),
        }
    ).with_columns(pl.col("ts").dt.replace_time_zone("UTC"))


def test_bar_before_globex_rollover_keeps_the_calendar_date():
    # 2024-01-10 is EST (UTC-5): 17:59 ET == 22:59 UTC.
    df = with_session_date(_bars([datetime(2024, 1, 10, 22, 59, tzinfo=timezone.utc)]))
    assert df["session_date"][0] == date(2024, 1, 10)


def test_bar_at_globex_rollover_belongs_to_the_next_calendar_date():
    # 18:00 ET == 23:00 UTC on 2024-01-10 -> session_date 2024-01-11.
    df = with_session_date(_bars([datetime(2024, 1, 10, 23, 0, tzinfo=timezone.utc)]))
    assert df["session_date"][0] == date(2024, 1, 11)


def test_rth_mask_is_left_closed_right_open():
    # 09:30 ET == 14:30 UTC (in), 16:00 ET == 21:00 UTC (out) on 2024-01-10.
    df = with_session_date(
        _bars(
            [
                datetime(2024, 1, 10, 14, 30, tzinfo=timezone.utc),  # 09:30 ET, in RTH
                datetime(2024, 1, 10, 20, 59, tzinfo=timezone.utc),  # 15:59 ET, in RTH
                datetime(2024, 1, 10, 21, 0, tzinfo=timezone.utc),   # 16:00 ET, NOT in RTH
                datetime(2024, 1, 10, 14, 29, tzinfo=timezone.utc),  # 09:29 ET, NOT in RTH
            ]
        )
    )
    mask = df.select(rth_mask().alias("m"))["m"].to_list()
    assert mask == [True, True, False, False]


def test_opening_range_mask_is_left_closed_right_open():
    assert RTH_START.hour == 9 and RTH_START.minute == 30
    assert OPENING_RANGE_END.hour == 10 and OPENING_RANGE_END.minute == 0
    df = with_session_date(
        _bars(
            [
                datetime(2024, 1, 10, 14, 30, tzinfo=timezone.utc),  # 09:30 ET, in OR
                datetime(2024, 1, 10, 14, 59, tzinfo=timezone.utc),  # 09:59 ET, in OR
                datetime(2024, 1, 10, 15, 0, tzinfo=timezone.utc),   # 10:00 ET, NOT in OR
            ]
        )
    )
    mask = df.select(opening_range_mask().alias("m"))["m"].to_list()
    assert mask == [True, True, False]
    assert RTH_END.hour == 16
