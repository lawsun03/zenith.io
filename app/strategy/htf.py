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
    """4h FVG inversion (IFVG) → directional bias.

    Scans bars chronologically for 3-bar Fair Value Gaps. Tracks the most
    recent FVG. When a bar closes through the far edge of that FVG (inversion),
    flips bias:
      - Bearish FVG inversed (close > fvg.high) → bullish
      - Bullish FVG inversed (close < fvg.low)  → bearish

    Bias persists at the last inversion until the next inversion. Starts
    neutral until the first inversion is observed.

    FVG definition (3-bar pattern, bars b1/b2/b3 in order):
      - Bearish: b1.low > b3.high  → gap zone [b3.high, b1.low]
      - Bullish: b1.high < b3.low  → gap zone [b1.high, b3.low]
    """

    def __init__(self, lookback: int = 3) -> None:
        # lookback retained for API compatibility; unused in IFVG logic.
        self._bias: Bias = "neutral"
        self._last_bar_count: int = 0
        self._tracked_fvg: "_Gap | None" = None
        self._last_inversion_ts = None  # datetime | None

    def rebuild(self, bars: list[Bar]) -> None:
        """Scan bars chronologically for FVG inversions; update bias."""
        old = self._bias
        self._last_bar_count = len(bars)

        if len(bars) < 3:
            self._bias = "neutral"
            self._tracked_fvg = None
            self._last_inversion_ts = None
            return

        ordered = sorted(bars, key=lambda b: b.ts)
        bias: Bias = "neutral"
        tracked: "_Gap | None" = None
        last_inv_ts = None

        for i, bar in enumerate(ordered):
            # 1. Check if this bar inverts the tracked FVG (close through far edge).
            if tracked is not None:
                if tracked.side == "bearish" and bar.close > tracked.high:
                    bias = "bullish"
                    last_inv_ts = bar.ts
                    log.debug(
                        "IFVG: bearish [%s-%s] inversed at %s -> bullish",
                        tracked.low, tracked.high, bar.ts,
                    )
                    tracked = None
                elif tracked.side == "bullish" and bar.close < tracked.low:
                    bias = "bearish"
                    last_inv_ts = bar.ts
                    log.debug(
                        "IFVG: bullish [%s-%s] inversed at %s -> bearish",
                        tracked.low, tracked.high, bar.ts,
                    )
                    tracked = None

            # 2. Check if bar i completes a new 3-bar FVG.
            if i >= 2:
                b1, b3 = ordered[i - 2], bar
                if b3.low > b1.high:       # bullish FVG: gap between b1.high and b3.low
                    tracked = _Gap("bullish", b1.high, b3.low)
                elif b3.high < b1.low:     # bearish FVG: gap between b3.high and b1.low
                    tracked = _Gap("bearish", b3.high, b1.low)

        if bias != old:
            log.info("HTFBias (IFVG): %s -> %s", old, bias)
        self._bias = bias
        self._tracked_fvg = tracked
        self._last_inversion_ts = last_inv_ts

    def bias(self) -> Bias:
        return self._bias

    def diagnostics(self) -> dict:
        return {
            "bias": self._bias,
            "bars_fed": self._last_bar_count,
            "tracked_fvg": {
                "side": self._tracked_fvg.side,
                "low": str(self._tracked_fvg.low),
                "high": str(self._tracked_fvg.high),
            } if self._tracked_fvg else None,
            "last_inversion_ts": (
                self._last_inversion_ts.isoformat()
                if self._last_inversion_ts else None
            ),
        }


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

    @property
    def swing_highs(self) -> list[Decimal]:
        return list(self._swing_highs)

    @property
    def swing_lows(self) -> list[Decimal]:
        return list(self._swing_lows)

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
