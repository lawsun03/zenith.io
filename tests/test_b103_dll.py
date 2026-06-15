"""B103 defining-behavior tests: intraday DLL cap in funded_sim.

Business rule: Topstep XFA accounts have a daily loss limit (DLL) of 1% of
funded account size ($500 for $50K standard). Once the running daily P&L
reaches -DLL, the bot stops trading for the day. funded_sim must model this.

Tests verify:
- daily_pnls_with_low correctly tracks intraday running-minimum P&L
- cap_daily_pnls_at_dll applies the cap when DLL is triggered intraday
- cap_daily_pnls_at_dll leaves days unchanged when DLL is not triggered
- DLL cap is exactly -dll_amount (not max/min variant)
- Days where EOD is worse than DLL are also capped (DLL prevents further trading)
- dll_amount=0 passes data through unchanged
- Multi-day series with mixed DLL/no-DLL days works correctly
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.backtest.funded_sim import cap_daily_pnls_at_dll, daily_pnls_with_low

# Timestamps: use UTC, different days (>17:00 CT rolls to next calendar day,
# so use midday UTC = morning CT to stay within the same calendar day)
_D1A = datetime(2024, 1, 2, 15, 0, tzinfo=timezone.utc)   # day 1, fill 1
_D1B = datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc)   # day 1, fill 2
_D1C = datetime(2024, 1, 2, 17, 0, tzinfo=timezone.utc)   # day 1, fill 3
_D2A = datetime(2024, 1, 3, 15, 0, tzinfo=timezone.utc)   # day 2, fill 1
_D2B = datetime(2024, 1, 3, 16, 0, tzinfo=timezone.utc)   # day 2, fill 2


class TestDailyPnlsWithLow:
    def test_single_day_no_dip(self):
        # Straight up day — low_pnl should be 0 (no intraday cumulative loss)
        curve = [
            (_D1A, Decimal("50000")),
            (_D1B, Decimal("50200")),
            (_D1C, Decimal("50400")),
        ]
        result = daily_pnls_with_low(curve)
        assert len(result) == 1
        ts, pnl, low = result[0]
        assert pnl == Decimal("400")
        assert low == Decimal("0")  # never dipped below start

    def test_single_day_dip_then_recover(self):
        # Dip to -600, then recover to -200 EOD
        curve = [
            (_D1A, Decimal("50000")),
            (_D1B, Decimal("49400")),  # -600 running
            (_D1C, Decimal("49800")),  # -200 running (recovered +400)
        ]
        result = daily_pnls_with_low(curve)
        assert len(result) == 1
        ts, pnl, low = result[0]
        assert pnl == Decimal("-200")
        assert low == Decimal("-600")  # worst point was -600

    def test_single_day_continuous_loss(self):
        # Continuous losses — low = final P&L
        curve = [
            (_D1A, Decimal("50000")),
            (_D1B, Decimal("49600")),  # -400
            (_D1C, Decimal("49300")),  # -700 total
        ]
        result = daily_pnls_with_low(curve)
        ts, pnl, low = result[0]
        assert pnl == Decimal("-700")
        assert low == Decimal("-700")

    def test_two_days_independent_tracking(self):
        # Day 1: dips to -600, recovers to -200; Day 2: gains only
        curve = [
            (_D1A, Decimal("50000")),
            (_D1B, Decimal("49400")),  # day1 -600
            (_D1C, Decimal("49800")),  # day1 -200 (EOD)
            (_D2A, Decimal("50000")),  # day2 +200
            (_D2B, Decimal("50300")),  # day2 +500 total
        ]
        result = daily_pnls_with_low(curve)
        assert len(result) == 2
        _, p1, l1 = result[0]
        _, p2, l2 = result[1]
        assert p1 == Decimal("-200")
        assert l1 == Decimal("-600")
        # Day 2 runs from $49,800 (EOD day1) to $50,300: delta = +$500
        assert p2 == Decimal("500")
        assert l2 == Decimal("0")  # never went below day2 start


class TestCapDailyPnlsAtDll:
    def _make_series(self, pnl: str, low: str, ts=None):
        return [(ts or _D1A, Decimal(pnl), Decimal(low))]

    def test_dll_not_triggered_unchanged(self):
        series = self._make_series(pnl="-200", low="-300")
        result = cap_daily_pnls_at_dll(series, Decimal("500"))
        assert len(result) == 1
        _, pnl = result[0]
        assert pnl == Decimal("-200")  # low=-300 > -500 threshold, unchanged

    def test_dll_triggered_cap_at_threshold(self):
        # Intraday dip to -600, recovers to -200 — DLL threshold $500
        series = self._make_series(pnl="-200", low="-600")
        result = cap_daily_pnls_at_dll(series, Decimal("500"))
        _, pnl = result[0]
        # DLL triggered (low -600 <= -500); effective P&L capped at -500
        assert pnl == Decimal("-500")

    def test_dll_triggered_eod_worse_than_threshold(self):
        # Intraday dip to -800, EOD also -800 — DLL caps at -500
        series = self._make_series(pnl="-800", low="-800")
        result = cap_daily_pnls_at_dll(series, Decimal("500"))
        _, pnl = result[0]
        assert pnl == Decimal("-500")  # capped, not -800

    def test_dll_triggered_exactly_at_threshold(self):
        # Low exactly equals -dll — should trigger the cap
        series = self._make_series(pnl="-500", low="-500")
        result = cap_daily_pnls_at_dll(series, Decimal("500"))
        _, pnl = result[0]
        assert pnl == Decimal("-500")

    def test_dll_zero_passthrough(self):
        # dll_amount=0 means no cap — return (ts, pnl) unchanged
        series = [(_D1A, Decimal("-800"), Decimal("-800")),
                  (_D2A, Decimal("200"), Decimal("-100"))]
        result = cap_daily_pnls_at_dll(series, Decimal("0"))
        assert [(ts, p) for ts, p in result] == [
            (_D1A, Decimal("-800")),
            (_D2A, Decimal("200")),
        ]

    def test_positive_eod_intraday_dip_below_dll(self):
        # Intraday dip to -600, then big recovery: EOD +300
        # DLL triggered → effective = -500 (loses the recovery, cap wins)
        series = self._make_series(pnl="300", low="-600")
        result = cap_daily_pnls_at_dll(series, Decimal("500"))
        _, pnl = result[0]
        assert pnl == Decimal("-500")

    def test_multi_day_mixed(self):
        # Day 1: DLL triggered (low -600, eod -200) → capped to -500
        # Day 2: DLL not triggered (low -300, eod +100) → unchanged
        # Day 3: DLL triggered (low -700, eod -700) → capped to -500
        series = [
            (_D1A, Decimal("-200"), Decimal("-600")),
            (_D2A, Decimal("100"), Decimal("-300")),
            (datetime(2024, 1, 4, 15, 0, tzinfo=timezone.utc), Decimal("-700"), Decimal("-700")),
        ]
        result = cap_daily_pnls_at_dll(series, Decimal("500"))
        pnls = [p for _, p in result]
        assert pnls == [Decimal("-500"), Decimal("100"), Decimal("-500")]
