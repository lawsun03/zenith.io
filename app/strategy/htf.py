"""
Higher-timeframe (HTF) confluence trackers.

Pure modules: bars in, state out. No I/O, no broker. Fed by REST-polled
4h / 30min bars (the live 1min pipeline is single-timeframe — see
main.py._htf_refresh_loop). The execution engine reads these at
signal-decision time.

  - HTFBiasTracker:  4h swing structure → bullish / bearish / neutral.
  - HTFLevelFinder:  4h FVGs + 30min swings → a take-profit target.

Both reuse the existing LiquidityTracker for swing detection so the swing
convention matches the rest of the strategy.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Literal

from app.broker.events import Bar
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker

log = logging.getLogger(__name__)

Bias = Literal["bullish", "bearish", "neutral"]


class HTFBiasTracker:
    """4h swing structure → directional bias.

    bullish = most recent confirmed structure is higher-high AND higher-low.
    bearish = lower-high AND lower-low. Anything else (mixed, or fewer than
    two confirmed swings of each kind) is neutral — fail-open so the gate is
    inert until real structure exists.
    """

    def __init__(self, lookback: int = 3) -> None:
        self._lookback = lookback
        self._bias: Bias = "neutral"

    def rebuild(self, bars: list[Bar]) -> None:
        """Recompute bias from the full bar list (called on each refresh)."""
        old = self._bias

        tracker = LiquidityTracker(LiquidityConfig(
            swing_lookback=self._lookback,
            max_swings=50,
        ))
        for bar in bars:
            tracker.on_bar(bar)

        highs = tracker.recent_high_swings
        lows = tracker.recent_low_swings
        if len(highs) < 2 or len(lows) < 2:
            new_bias: Bias = "neutral"
        else:
            hh = highs[-1].price > highs[-2].price
            hl = lows[-1].price > lows[-2].price
            lh = highs[-1].price < highs[-2].price
            ll = lows[-1].price < lows[-2].price

            if hh and hl:
                new_bias = "bullish"
            elif lh and ll:
                new_bias = "bearish"
            else:
                new_bias = "neutral"

        if new_bias != old:
            log.info("HTFBias: %s -> %s", old, new_bias)
        self._bias = new_bias

    def bias(self) -> Bias:
        return self._bias


class HTFLevelFinder:
    """Placeholder — implemented in the next task."""

    def __init__(self, swing_lookback: int = 3) -> None:
        self._swing_lookback = swing_lookback
