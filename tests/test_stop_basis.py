"""Stop-basis selector for the iFVG stop (A/B sweep support).

WHY: the deployed iFVG stop anchors beyond the sweep extreme, applied live on
n=4 trades without backtest validation. To A/B iFVG-edge vs sweep-extreme vs
ATR, the composer needs a single `stop_basis` selector whose DEFAULT reproduces
today's behavior exactly (no live behavior change until deliberately swept).
"""
from __future__ import annotations

from decimal import Decimal

from app.bot_config import StrategyParams


def test_stop_basis_defaults_preserve_behavior():
    sp = StrategyParams()
    assert sp.stop_basis == "default"          # sentinel = existing logic
    assert sp.atr_stop_mult == Decimal("1.0")  # only used when stop_basis == "atr"


def _anchor(basis, side, *, entry, sweep_extreme, swing_anchor,
            zone_low=Decimal("100.0"), zone_high=Decimal("101.0"),
            atr=Decimal("2.0"), atr_mult=Decimal("1.0")):
    from app.strategy.composer import _stop_anchor_for_basis
    return _stop_anchor_for_basis(
        basis=basis, side=side, zone_low=zone_low, zone_high=zone_high,
        sweep_extreme=sweep_extreme, swing_anchor=swing_anchor,
        entry=entry, atr=atr, atr_mult=atr_mult,
    )


class TestAnchorLong:
    # long: entry above zone; iFVG edge = zone_low, sweep extreme below it
    def test_ifvg_edge(self):
        assert _anchor("ifvg_edge", "long", entry=Decimal("101.0"),
                       sweep_extreme=Decimal("99.0"), swing_anchor=Decimal("98.5")) == Decimal("100.0")

    def test_sweep_extreme(self):
        assert _anchor("sweep_extreme", "long", entry=Decimal("101.0"),
                       sweep_extreme=Decimal("99.0"), swing_anchor=Decimal("98.5")) == Decimal("99.0")

    def test_atr(self):  # entry - atr_mult*atr = 101 - 1*2 = 99
        assert _anchor("atr", "long", entry=Decimal("101.0"),
                       sweep_extreme=Decimal("99.0"), swing_anchor=Decimal("98.5")) == Decimal("99.0")


class TestAnchorShort:
    # short: entry below zone; iFVG edge = zone_high, sweep extreme above it
    def test_ifvg_edge(self):
        assert _anchor("ifvg_edge", "short", entry=Decimal("100.0"),
                       sweep_extreme=Decimal("102.0"), swing_anchor=Decimal("102.5")) == Decimal("101.0")

    def test_sweep_extreme(self):
        assert _anchor("sweep_extreme", "short", entry=Decimal("100.0"),
                       sweep_extreme=Decimal("102.0"), swing_anchor=Decimal("102.5")) == Decimal("102.0")

    def test_atr(self):  # entry + atr_mult*atr = 100 + 1*3 = 103
        assert _anchor("atr", "short", entry=Decimal("100.0"), atr=Decimal("3.0"),
                       sweep_extreme=Decimal("102.0"), swing_anchor=Decimal("102.5")) == Decimal("103.0")
