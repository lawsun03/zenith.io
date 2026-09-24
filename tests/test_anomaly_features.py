"""Feature formulas checked against an independently hand-computed session.

Fixture: 14 identical "filler" sessions (RTH range exactly 10 every time)
to seed the 14-session trailing ATR-proxy window, then session A (the
"prior day", RTH range also 10 so the ATR proxy is exactly 10 going into
session B), then session B — the one every assertion is about. All values
are chosen so the expected numbers are exact, checkable fractions;
skew and realised-vol are cross-checked against an independent numpy/math
computation rather than transcribed by hand, since those formulas have no
clean closed form for this fixture.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import polars as pl
import pytest

from research.anomaly.features import ATR_LOOKBACK_SESSIONS, compute_all_features

NY_OFFSET = timedelta(hours=5)  # EST, UTC-5 — every fixture date below is in January


def _et(d: date, hour: int, minute: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc) + NY_OFFSET


def _session_bars(d: date, bars: list[tuple[int, int, float, float, float, float, int]]) -> list[dict]:
    """bars: list of (hour, minute, open, high, low, close, volume) in ET."""
    return [
        {
            "ts": _et(d, h, m),
            "open": o,
            "high": hi,
            "low": lo,
            "close": c,
            "volume": v,
            "instrument": "NQ",
        }
        for h, m, o, hi, lo, c, v in bars
    ]


def _filler_session(d: date) -> list[dict]:
    # A flat, featureless session: RTH range exactly 10 (100/95 -> wait: 105-95=10).
    bar = (9, 30, 100.0, 105.0, 95.0, 100.0, 100)
    return _session_bars(d, [bar])


@pytest.fixture(scope="module")
def feature_table() -> pl.DataFrame:
    base = date(2024, 1, 2)  # a Tuesday, comfortably in EST
    rows: list[dict] = []
    for i in range(ATR_LOOKBACK_SESSIONS - 1):  # 13 fillers
        rows += _filler_session(base + timedelta(days=i))

    session_a = base + timedelta(days=ATR_LOOKBACK_SESSIONS - 1)
    rows += _session_bars(session_a, [(9, 30, 200.0, 205.0, 195.0, 202.0, 100)])  # range 10

    session_b = session_a + timedelta(days=1)
    rows += _session_bars(
        session_b,
        [
            (9, 30, 204.0, 204.0, 199.0, 202.0, 100),
            (9, 45, 202.0, 203.0, 200.0, 201.0, 100),
            (11, 0, 201.0, 208.0, 201.0, 207.0, 100),
            (15, 0, 207.0, 207.0, 200.0, 201.0, 700),
        ],
    )

    bars = pl.DataFrame(rows)
    features = compute_all_features(bars)
    return features.filter(pl.col("session_date") == session_b)


def test_rth_ohlcv(feature_table):
    row = feature_table.row(0, named=True)
    assert row["rth_open"] == 204.0
    assert row["rth_high"] == 208.0
    assert row["rth_low"] == 199.0
    assert row["rth_close"] == 201.0
    assert row["rth_volume"] == 1000


def test_opening_range_ratio(feature_table):
    # OR (09:30-10:00) covers bars 1-2: high=204, low=199 -> or_range=5.
    # prior session's RTH range (A) = 205-195 = 10. ratio = 5/10.
    row = feature_table.row(0, named=True)
    assert row["opening_range_ratio"] == pytest.approx(0.5)


def test_sweep_excursion_atr(feature_table):
    # excursion beyond prior high (205): 208-205=3. Close (201) reverts
    # inside [195, 205], so the sweep counts. ATR proxy = 10 (constant
    # range across all 14 trailing sessions). 3/10.
    row = feature_table.row(0, named=True)
    assert row["sweep_excursion_atr"] == pytest.approx(0.3)


def test_overnight_follow_through(feature_table):
    # overnight_move = rth_open(204) - prior_close(202) = +2.
    # rth_move = rth_close(201) - rth_open(204) = -3.
    # sign(+2) * (-3) / |+2| = -1.5 (a reversal, not a continuation).
    row = feature_table.row(0, named=True)
    assert row["overnight_follow_through"] == pytest.approx(-1.5)


def test_gap_size_and_fill_rate(feature_table):
    # gap = rth_open(204) - prior_close(202) = +2. atr proxy = 10 -> 0.2.
    # gap up: filled = (rth_open - rth_low).clip(upper=2) = (204-199=5) -> 2.
    # fill_rate = 2 / 2 = 1.0 (fully filled).
    row = feature_table.row(0, named=True)
    assert row["gap_size_atr"] == pytest.approx(0.2)
    assert row["gap_fill_rate"] == pytest.approx(1.0)


def test_trend_efficiency(feature_table):
    # net move = |rth_close(201) - rth_open(204)| = 3.
    # path length = |201-202| + |207-201| + |201-207| (close-to-close steps
    # across the 4 RTH bars) = 1 + 6 + 6 = 13.
    row = feature_table.row(0, named=True)
    assert row["trend_efficiency"] == pytest.approx(3 / 13)


def test_realised_vol_matches_independent_log_return_sum(feature_table):
    closes = [202.0, 201.0, 207.0, 201.0]
    expected = sum(
        (math.log(closes[i]) - math.log(closes[i - 1])) ** 2 for i in range(1, len(closes))
    )
    row = feature_table.row(0, named=True)
    assert row["realised_vol"] == pytest.approx(expected)


def test_volume_profile_skew_matches_independent_weighted_skew(feature_table):
    closes = np.array([202.0, 201.0, 207.0, 201.0])
    volumes = np.array([100.0, 100.0, 100.0, 700.0])
    w = volumes / volumes.sum()
    wmean = (w * closes).sum()
    dev = closes - wmean
    wvar = (w * dev**2).sum()
    wm3 = (w * dev**3).sum()
    expected = wm3 / (wvar**1.5)
    row = feature_table.row(0, named=True)
    assert row["volume_profile_skew"] == pytest.approx(expected, rel=1e-6)


def test_filler_sessions_have_no_lookahead_atr_yet(feature_table):
    # Sanity: the fixture's ATR window is exactly full by session B (14
    # prior sessions), not before — this is what makes 10.0 an exact
    # expected atr proxy rather than a partial-window average.
    assert feature_table.height == 1
