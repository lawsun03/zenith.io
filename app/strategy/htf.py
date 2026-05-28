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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _Gap:
    side: Literal["bullish", "bearish"]
    low: Decimal      # gap bottom
    high: Decimal     # gap top


class HTFLevelFinder:
    """4h FVGs (primary) + 30min swings (fallback) → a take-profit target.

    Precedence: nearest qualifying 4h FVG near-edge, else nearest qualifying
    30min swing, else None (caller falls back to VP / fixed R).
    """

    def __init__(self, swing_lookback: int = 3) -> None:
        self._swing_lookback = swing_lookback
        self._gaps: list[_Gap] = []           # unmitigated 4h gaps
        self._swing_highs: list[Decimal] = []
        self._swing_lows: list[Decimal] = []

    def rebuild(self, fvg_bars: list[Bar], swing_bars: list[Bar]) -> None:
        self._gaps = self._compute_unmitigated_gaps(fvg_bars)

        tracker = LiquidityTracker(LiquidityConfig(
            swing_lookback=self._swing_lookback,
            max_swings=50,
        ))
        for bar in swing_bars:
            tracker.on_bar(bar)
        self._swing_highs = [s.price for s in tracker.recent_high_swings]
        self._swing_lows = [s.price for s in tracker.recent_low_swings]

    @staticmethod
    def _compute_unmitigated_gaps(bars: list[Bar]) -> list[_Gap]:
        """Find 3-bar FVGs, then drop any a later bar has traded back through."""
        gaps: list[tuple[int, _Gap]] = []
        for i in range(2, len(bars)):
            b1, b3 = bars[i - 2], bars[i]
            if b3.low > b1.high:        # bullish gap
                gaps.append((i, _Gap("bullish", b1.high, b3.low)))
            elif b3.high < b1.low:      # bearish gap
                gaps.append((i, _Gap("bearish", b3.high, b1.low)))

        out: list[_Gap] = []
        for formed_idx, gap in gaps:
            mitigated = False
            for later in bars[formed_idx + 1:]:
                if gap.side == "bullish" and later.low <= gap.low:
                    mitigated = True
                    break
                if gap.side == "bearish" and later.high >= gap.high:
                    mitigated = True
                    break
            if not mitigated:
                out.append(gap)
        return out

    def find_target(
        self,
        side: Literal["long", "short"],
        entry: Decimal,
        stop: Decimal,
        min_r: Decimal,
    ) -> tuple[Decimal, str] | None:
        r = abs(entry - stop)
        if r == 0:
            return None
        min_dist = r * min_r

        if side == "long":
            fvg_levels = [
                g.low for g in self._gaps
                if g.side == "bullish" and g.low > entry and (g.low - entry) >= min_dist
            ]
            if fvg_levels:
                price = min(fvg_levels)      # nearest above
                return price, f"HTF: 4h FVG @ {price} ({(price - entry) / r:.1f}R)"
            swing_levels = [
                p for p in self._swing_highs
                if p > entry and (p - entry) >= min_dist
            ]
            if swing_levels:
                price = min(swing_levels)
                return price, f"HTF: 30min swing @ {price} ({(price - entry) / r:.1f}R)"
            return None

        elif side == "short":
            fvg_levels = [
                g.high for g in self._gaps
                if g.side == "bearish" and g.high < entry and (entry - g.high) >= min_dist
            ]
            if fvg_levels:
                price = max(fvg_levels)      # nearest below
                return price, f"HTF: 4h FVG @ {price} ({(entry - price) / r:.1f}R)"
            swing_levels = [
                p for p in self._swing_lows
                if p < entry and (entry - p) >= min_dist
            ]
            if swing_levels:
                price = max(swing_levels)
                return price, f"HTF: 30min swing @ {price} ({(entry - price) / r:.1f}R)"
            return None

        else:
            raise ValueError(f"find_target: unknown side {side!r}")
