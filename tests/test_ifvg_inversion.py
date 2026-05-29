"""Tests for iFVG inversion detection in DisplacementDetector.

Convention: bars at 1-minute intervals from BASE_TS.
Prices in /MGC range (~$2400) for realism.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.broker.events import Bar
from app.strategy.displacement import DisplacementConfig, DisplacementDetector, FairValueGap

BASE_TS = datetime(2026, 5, 28, 9, 30, tzinfo=timezone.utc)


def bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        instrument="MGC", timeframe="1min",
        ts=BASE_TS + timedelta(minutes=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=100,
    )


def make_detector() -> DisplacementDetector:
    return DisplacementDetector(DisplacementConfig(
        atr_period=3,
        body_atr_multiple=Decimal("1.5"),
        min_body_to_range_ratio=Decimal("0.6"),
        min_absolute_body=Decimal("0.5"),
    ))


def warm_atr(d: DisplacementDetector, n: int = 5, offset: int = 0) -> None:
    """Feed neutral bars to warm ATR."""
    for i in range(n):
        d.on_bar(bar(offset + i, "2400", "2401", "2399", "2400"))


class TestFVGFormation:
    def test_bullish_fvg_added_to_active(self):
        """b3.low > b1.high creates a bullish FVG in _active_fvgs."""
        d = make_detector()
        d.on_bar(bar(0, "2400", "2401", "2399", "2400"))  # b1: high=2401
        d.on_bar(bar(1, "2400", "2402", "2400", "2401"))  # b2: any
        assert len(d.active_fvgs) == 0  # need 3 bars
        d.on_bar(bar(2, "2403", "2404", "2403", "2404"))  # b3: low=2403 > b1.high=2401 → FVG [2401,2403]
        fvgs = d.active_fvgs
        assert len(fvgs) == 1
        assert fvgs[0].side == "bullish"
        assert fvgs[0].low == Decimal("2401")
        assert fvgs[0].high == Decimal("2403")

    def test_bearish_fvg_added_to_active(self):
        """b3.high < b1.low creates a bearish FVG in _active_fvgs."""
        d = make_detector()
        d.on_bar(bar(0, "2405", "2406", "2404", "2405"))  # b1: low=2404
        d.on_bar(bar(1, "2404", "2405", "2403", "2404"))  # b2
        d.on_bar(bar(2, "2400", "2402", "2400", "2401"))  # b3: high=2402 < b1.low=2404 → FVG [2402,2404]
        fvgs = d.active_fvgs
        assert len(fvgs) == 1
        assert fvgs[0].side == "bearish"
        assert fvgs[0].low == Decimal("2402")
        assert fvgs[0].high == Decimal("2404")

    def test_no_gap_no_fvg(self):
        """Overlapping bars produce no FVG."""
        d = make_detector()
        d.on_bar(bar(0, "2400", "2402", "2399", "2401"))
        d.on_bar(bar(1, "2401", "2403", "2400", "2402"))
        d.on_bar(bar(2, "2402", "2404", "2401", "2403"))  # b3.low=2401 < b1.high=2402 → no gap
        assert len(d.active_fvgs) == 0


class TestFVGMitigation:
    def test_bullish_fvg_mitigated_by_wick(self):
        """Bullish FVG [2401,2403] removed when bar.low <= fvg.low (2401)."""
        d = make_detector()
        d.on_bar(bar(0, "2400", "2401", "2399", "2400"))
        d.on_bar(bar(1, "2400", "2402", "2400", "2401"))
        d.on_bar(bar(2, "2403", "2404", "2403", "2404"))
        assert len(d.active_fvgs) == 1

        d.on_bar(bar(3, "2403", "2403", "2401", "2402"))  # bar.low=2401 == fvg.low → mitigated
        assert len(d.active_fvgs) == 0

    def test_bearish_fvg_mitigated_by_wick(self):
        """Bearish FVG [2402,2404] removed when bar.high >= fvg.high (2404)."""
        d = make_detector()
        d.on_bar(bar(0, "2405", "2406", "2404", "2405"))
        d.on_bar(bar(1, "2404", "2405", "2403", "2404"))
        d.on_bar(bar(2, "2400", "2402", "2400", "2401"))
        assert len(d.active_fvgs) == 1

        d.on_bar(bar(3, "2401", "2404", "2401", "2402"))  # bar.high=2404 == fvg.high → mitigated
        assert len(d.active_fvgs) == 0

    def test_fvg_not_mitigated_before_far_edge(self):
        """FVG stays active when bar doesn't reach the far edge."""
        d = make_detector()
        d.on_bar(bar(0, "2400", "2401", "2399", "2400"))
        d.on_bar(bar(1, "2400", "2402", "2400", "2401"))
        d.on_bar(bar(2, "2403", "2404", "2403", "2404"))
        d.on_bar(bar(3, "2404", "2405", "2402", "2403"))  # bar.low=2402 > fvg.low=2401 → stays
        assert len(d.active_fvgs) == 1


class TestIFVGInversion:
    def test_bearish_displacement_inverts_bullish_fvg(self):
        """Bearish disp bar closing below fvg.low emits iFVG event with prior bullish FVG."""
        d = make_detector()
        warm_atr(d, n=5, offset=0)

        # Form bullish FVG [2401, 2403] at bars 5-7
        d.on_bar(bar(5, "2400", "2401", "2399", "2400"))  # b1: high=2401
        d.on_bar(bar(6, "2401", "2402", "2400", "2402"))  # b2
        d.on_bar(bar(7, "2403", "2405", "2403", "2404"))  # b3: low=2403 → FVG bullish [2401,2403]
        assert any(f.side == "bullish" for f in d.active_fvgs)

        # Bearish displacement: large body, closes BELOW fvg.low=2401
        # b2 of displacement window at bar 8, b3 at bar 9
        d.on_bar(bar(8, "2404", "2405", "2392", "2393"))  # big bearish b2: close=2393 < fvg.low=2401
        result = d.on_bar(bar(9, "2393", "2394", "2391", "2392"))  # b3

        assert result is not None, "Expected a DisplacementEvent"
        assert result.side == "bearish"
        assert result.fvg is not None, "Expected iFVG to be the prior bullish FVG"
        assert result.fvg.side == "bullish"
        assert result.fvg.low == Decimal("2401")
        assert result.fvg.high == Decimal("2403")

    def test_bullish_displacement_inverts_bearish_fvg(self):
        """Bullish disp bar closing above fvg.high emits iFVG event with prior bearish FVG."""
        d = make_detector()
        warm_atr(d, n=5, offset=0)

        # Form bearish FVG [2398, 2400] at bars 5-7
        d.on_bar(bar(5, "2401", "2402", "2400", "2401"))  # b1: low=2400
        d.on_bar(bar(6, "2400", "2401", "2398", "2399"))  # b2
        d.on_bar(bar(7, "2395", "2398", "2394", "2396"))  # b3: high=2398 < b1.low=2400 → FVG bearish [2398,2400]
        assert any(f.side == "bearish" for f in d.active_fvgs)

        # Bullish displacement: closes ABOVE fvg.high=2400
        d.on_bar(bar(8, "2396", "2408", "2395", "2407"))  # big bullish b2: close=2407 > fvg.high=2400
        result = d.on_bar(bar(9, "2407", "2409", "2406", "2408"))

        assert result is not None
        assert result.side == "bullish"
        assert result.fvg is not None
        assert result.fvg.side == "bearish"
        assert result.fvg.low == Decimal("2398")
        assert result.fvg.high == Decimal("2400")

    def test_displacement_no_prior_fvg_returns_fvg_none(self):
        """If no prior FVG exists, displacement fires with fvg=None."""
        d = make_detector()
        warm_atr(d, n=5, offset=0)
        # No FVGs formed — jump straight to displacement
        d.on_bar(bar(5, "2400", "2401", "2399", "2400"))
        d.on_bar(bar(6, "2400", "2401", "2389", "2390"))  # big bearish
        result = d.on_bar(bar(7, "2390", "2391", "2389", "2390"))

        assert result is not None  # displacement detected
        assert result.fvg is None  # but no iFVG to invert

    def test_mitigated_fvg_not_used_for_inversion(self):
        """A FVG removed by wick mitigation cannot be inverted."""
        d = make_detector()
        warm_atr(d, n=5, offset=0)

        # Form bullish FVG [2401, 2403]
        d.on_bar(bar(5, "2400", "2401", "2399", "2400"))
        d.on_bar(bar(6, "2401", "2402", "2400", "2402"))
        d.on_bar(bar(7, "2403", "2405", "2403", "2404"))

        # Mitigate: bar.low touches 2401 (far edge) WITHOUT inverting (close stays above)
        d.on_bar(bar(8, "2404", "2404", "2401", "2403"))  # wick hits far edge, close=2403 > fvg.low
        assert len(d.active_fvgs) == 0, "FVG should be mitigated"

        # Bearish displacement now — no valid FVG to invert
        d.on_bar(bar(9, "2403", "2404", "2392", "2393"))
        result = d.on_bar(bar(10, "2393", "2394", "2392", "2393"))
        assert result is None or result.fvg is None

    def test_inversion_wins_over_mitigation_on_same_bar(self):
        """
        CRITICAL (Correction 1): A bar whose wick pierces the far edge (mitigation trigger)
        AND whose body closes through (inversion trigger) → iFVG signal fires,
        FVG is NOT dropped as mitigated.

        Sequence:
          - Form bullish FVG [2401, 2403]
          - Big bearish bar: wick high=2405, low=2399 (pierces fvg.low=2401),
            close=2393 (closes through fvg.low=2401 → inversion)
          - Expect: DisplacementEvent with fvg = bullish FVG (inversion wins)
        """
        d = make_detector()
        warm_atr(d, n=5, offset=0)

        # Form bullish FVG [2401, 2403]
        d.on_bar(bar(5, "2400", "2401", "2399", "2400"))
        d.on_bar(bar(6, "2401", "2402", "2400", "2402"))
        d.on_bar(bar(7, "2403", "2405", "2403", "2404"))
        assert any(f.side == "bullish" for f in d.active_fvgs)

        # Displacement bar: wick=2393 pierces fvg.low=2401, body close=2393 inverts it
        # This is b2 of the next evaluation window; b3 follows
        d.on_bar(bar(8, "2404", "2405", "2393", "2393"))  # b2: wick low=2393<2401, close=2393<2401
        result = d.on_bar(bar(9, "2393", "2394", "2392", "2393"))  # b3

        assert result is not None, "iFVG signal should fire — inversion wins over mitigation"
        assert result.fvg is not None, "iFVG should be the prior bullish FVG"
        assert result.fvg.side == "bullish"


class TestPeekDisplacementIFVG:
    def test_peek_none_when_no_prior_fvg(self):
        """peek_displacement returns None if displacement bar has no prior FVG to invert."""
        d = make_detector()
        warm_atr(d, n=5, offset=0)
        d.on_bar(bar(5, "2400", "2401", "2399", "2400"))
        d.on_bar(bar(6, "2400", "2401", "2388", "2389"))  # big bearish, no prior FVG
        assert d.peek_displacement() is None

    def test_peek_returns_side_when_prior_fvg_exists(self):
        """peek_displacement returns (side, b1, b2) when prior FVG can be inverted."""
        d = make_detector()
        warm_atr(d, n=3, offset=0)
        # Form bullish FVG [2401, 2403]
        d.on_bar(bar(3, "2400", "2401", "2399", "2400"))
        d.on_bar(bar(4, "2401", "2402", "2400", "2402"))
        d.on_bar(bar(5, "2403", "2405", "2403", "2404"))
        # Feed big bearish bar (b2 of displacement window) that closes below 2401
        d.on_bar(bar(6, "2404", "2405", "2392", "2393"))
        result = d.peek_displacement()
        assert result is not None
        side, b1, b2 = result
        assert side == "bearish"
