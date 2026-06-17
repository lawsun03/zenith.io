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
