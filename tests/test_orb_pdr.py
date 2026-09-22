"""ORB prior-day-range qualifier (B5) — defining-behavior tests.

The qualifier gates ORB trades on the prior ET-day's range (as % of its close)
being >= the trailing 60-day median.  It is default-off and has no effect until
the lookback window is full.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.sim.events import Bar
from app.strategy.orb import ORBConfig, ORBDetector

ET = ZoneInfo("America/New_York")


def _bar(day: int, h: int, m: int, *, hi: str, lo: str, c: str,
         month: int = 1, year: int = 2025) -> Bar:
    return Bar(
        instrument="MNQ", timeframe="5min",
        ts=datetime(year, month, day, h, m, tzinfo=ET),
        open=Decimal(c), high=Decimal(hi), low=Decimal(lo),
        close=Decimal(c), volume=100,
    )


def _det(pdr_enabled: bool = True, lookback: int = 3) -> ORBDetector:
    return ORBDetector(ORBConfig(
        instrument="MNQ",
        range_minutes=15,
        r_multiple=Decimal("2.0"),
        pdr_enabled=pdr_enabled,
        pdr_lookback=lookback,
    ))


def _feed_day_range(det: ORBDetector, day: int, hi: str, lo: str,
                    month: int = 1, year: int = 2025) -> None:
    """Feed three 5min range-building bars for a day (no signal possible)."""
    det.on_bar(_bar(day, 9, 30, hi=hi, lo=lo, c=hi, month=month, year=year))
    det.on_bar(_bar(day, 9, 35, hi=hi, lo=lo, c=hi, month=month, year=year))
    det.on_bar(_bar(day, 9, 40, hi=hi, lo=lo, c=hi, month=month, year=year))


def _breakout_bar(det: ORBDetector, day: int, close: str,
                  month: int = 1, year: int = 2025):
    """Feed one post-range bar; return the result of on_bar."""
    return det.on_bar(_bar(day, 9, 50, hi=close, lo=str(Decimal(close) - 5), c=close,
                           month=month, year=year))


class TestPDRDisabledNoEffect:
    """With pdr_enabled=False the qualifier never fires."""

    def test_pdr_off_always_allows_signal(self):
        det = _det(pdr_enabled=False, lookback=3)
        # Feed 4 tiny-range days (would normally be below-median)
        for day in range(2, 6):
            _feed_day_range(det, day, hi="20000", lo="19980")   # 20pt range / 20k ≈ 0.10%
        # Day 6: wide breakout — must fire regardless of prior-day ranges
        _feed_day_range(det, 6, hi="20010", lo="19990")
        sig = _breakout_bar(det, 6, close="20030")
        assert sig is not None, "pdr_enabled=False must not filter signals"


class TestPDRWarmup:
    """Trades are allowed before the lookback window is full."""

    def test_below_lookback_allows_signal(self):
        # lookback=3; feed only 1 complete prior day → window has 1 < 3 entries
        det = _det(pdr_enabled=True, lookback=3)
        # Day 2: a small-range day that will sit in the deque
        _feed_day_range(det, 2, hi="20000", lo="19980")  # 20 pt
        # Day 3: breakout — window not yet full (only 1 day recorded), so allow
        _feed_day_range(det, 3, hi="20010", lo="19990")
        sig = _breakout_bar(det, 3, close="20030")
        assert sig is not None, "insufficient warmup should not filter"


class TestPDRQualifier:
    """Core filtering logic once the window is full (lookback=3)."""

    def _setup_three_prior_days(self, det: ORBDetector) -> None:
        """
        Feed 3 complete days with ranges:
          day 2: 100pt / 20000 = 0.50%
          day 3:  50pt / 20000 = 0.25%
          day 4: 200pt / 20000 = 1.00%
        Median of [0.50, 0.25, 1.00] = 0.50%.
        """
        # day 2: wide
        det.on_bar(_bar(2, 9, 0, hi="20100", lo="20000", c="20050"))  # pre-open
        det.on_bar(_bar(2, 16, 0, hi="20100", lo="20000", c="20000"))  # day close
        # day 3: narrow
        det.on_bar(_bar(3, 9, 0, hi="20050", lo="20000", c="20025"))
        det.on_bar(_bar(3, 16, 0, hi="20050", lo="20000", c="20000"))
        # day 4: very wide
        det.on_bar(_bar(4, 9, 0, hi="20200", lo="20000", c="20100"))
        det.on_bar(_bar(4, 16, 0, hi="20200", lo="20000", c="20000"))

    def test_below_median_prior_day_blocks_signal(self):
        """Prior day (day 4 → 1.00%) is wide — but day 5's prior IS day 4 (1.00%)."""
        # Actually let me use a concrete scenario where prior day IS below median.
        det = _det(pdr_enabled=True, lookback=3)
        # Feed 3 complete days:
        #  day 2: range=200pt, close=20000 → 1.00%
        #  day 3: range=200pt, close=20000 → 1.00%
        #  day 4: range=20pt,  close=20000 → 0.10%  ← below median(1.00, 1.00, 0.10)=1.00
        for day, hi, lo in [(2, "20200", "20000"), (3, "20200", "20000")]:
            det.on_bar(_bar(day, 8, 0, hi=hi, lo=lo, c="20000"))
            det.on_bar(_bar(day, 16, 0, hi=hi, lo=lo, c="20000"))
        # day 4: narrow
        det.on_bar(_bar(4, 8, 0, hi="20020", lo="20000", c="20010"))
        det.on_bar(_bar(4, 16, 0, hi="20020", lo="20000", c="20000"))
        # day 5: prior day = day4 (0.10%), median of [1.0, 1.0, 0.10] = 1.0 → BLOCKED
        _feed_day_range(det, 5, hi="20010", lo="19990")
        sig = _breakout_bar(det, 5, close="20030")
        assert sig is None, "below-median prior day must block the ORB signal"

    def test_above_median_prior_day_allows_signal(self):
        """Prior day is above median → signal allowed."""
        det = _det(pdr_enabled=True, lookback=3)
        # Feed 3 complete days:
        #  day 2: range=20pt  → 0.10%
        #  day 3: range=20pt  → 0.10%
        #  day 4: range=200pt → 1.00%  ← above median(0.10, 0.10, 1.00)=0.10
        for day, hi, lo in [(2, "20020", "20000"), (3, "20020", "20000")]:
            det.on_bar(_bar(day, 8, 0, hi=hi, lo=lo, c="20000"))
            det.on_bar(_bar(day, 16, 0, hi=hi, lo=lo, c="20000"))
        # day 4: wide
        det.on_bar(_bar(4, 8, 0, hi="20200", lo="20000", c="20100"))
        det.on_bar(_bar(4, 16, 0, hi="20200", lo="20000", c="20000"))
        # day 5: prior day = day4 (1.00%), median=[0.10, 0.10, 1.00]=0.10 → ALLOWED
        _feed_day_range(det, 5, hi="20010", lo="19990")
        sig = _breakout_bar(det, 5, close="20030")
        assert sig is not None, "above-median prior day must allow the ORB signal"

    def test_exactly_at_median_is_allowed(self):
        """Prior day range == median is not below median → allowed."""
        det = _det(pdr_enabled=True, lookback=3)
        # Three equal-range days → median == any of them
        for day in [2, 3, 4]:
            det.on_bar(_bar(day, 8, 0, hi="20100", lo="20000", c="20050"))
            det.on_bar(_bar(day, 16, 0, hi="20100", lo="20000", c="20000"))
        # Prior day == median → should not be blocked (strictly below is the gate)
        _feed_day_range(det, 5, hi="20010", lo="19990")
        sig = _breakout_bar(det, 5, close="20030")
        assert sig is not None, "range == median must not be blocked"


class TestPDRDailyReset:
    """The filter re-evaluates independently each day."""

    def test_alternate_blocked_allowed_by_alternating_ranges(self):
        """Alternating wide/narrow prior days produces alternating allow/block."""
        det = _det(pdr_enabled=True, lookback=2)
        # 2-day lookback for tractability
        # day 2: narrow (0.10%)
        det.on_bar(_bar(2, 8, 0, hi="20020", lo="20000", c="20010"))
        det.on_bar(_bar(2, 16, 0, hi="20020", lo="20000", c="20000"))
        # day 3: wide (1.00%)
        det.on_bar(_bar(3, 8, 0, hi="20200", lo="20000", c="20100"))
        det.on_bar(_bar(3, 16, 0, hi="20200", lo="20000", c="20000"))
        # deque now [0.10, 1.00], median=0.55
        # day 4 prior = day 3 = 1.00% >= 0.55 → allowed
        _feed_day_range(det, 4, hi="20010", lo="19990")
        sig4 = _breakout_bar(det, 4, close="20030")
        assert sig4 is not None, "day 4 (prior wide): should be allowed"

        # day 4: narrow (0.10%) — feed one more complete day
        det.on_bar(_bar(4, 16, 0, hi="20030", lo="19990", c="19990"))
        # deque now [1.00, 0.10] (maxlen=2), median=0.55
        # day 5 prior = day 4 = 0.10% < 0.55 → blocked
        _feed_day_range(det, 5, hi="20010", lo="19990")
        sig5 = _breakout_bar(det, 5, close="20030")
        assert sig5 is None, "day 5 (prior narrow): should be blocked"
