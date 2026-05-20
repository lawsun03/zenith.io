"""
Displacement detector — large impulsive moves and the FVGs they create.

Concepts:

  Displacement: a single bar (or sequence) that moves price impulsively
  — a body large relative to recent ATR, with little wick. After a
  liquidity sweep, displacement in the opposite direction is the
  classic "smart money reversal" pattern; entering on a retrace into
  the FVG created by the displacement is the trade.

  Fair Value Gap (FVG): a three-bar imbalance.
    - Bullish FVG: bar3.low > bar1.high  → gap from bar1.high to bar3.low
    - Bearish FVG: bar3.high < bar1.low  → gap from bar1.low to bar3.high
  Bar 2 is the displacement bar; bars 1 and 3 are its neighbors. The
  zone between bar1's extreme and bar3's opposite extreme is "unfilled
  fair value" that price often retraces to.

What this module does NOT do:
  - Decide whether to trade. That's the composer's job (composer.py).
  - Read killzones or risk state. Pure: bars in, displacement events out.
  - Track FVG fills over time. The composer handles fill tracking
    against active signals; once an FVG is consumed, it's done.

ATR is computed inline — Wilder's method, period configurable. Could
import from project-x-py's indicators, but keeping this module
SDK-free makes it easier to test and reuse in the backtester.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Deque, Literal

from app.broker.events import Bar

DisplacementSide = Literal["bullish", "bearish"]


@dataclass(frozen=True)
class FairValueGap:
    """
    The gap zone left by a displacement.

    For a bullish FVG: low = bar1.high, high = bar3.low. Price typically
    revisits this zone before continuing; the strategy enters on retrace.

    For a bearish FVG: low = bar3.high, high = bar1.low.
    """

    side: DisplacementSide   # bullish or bearish
    low: Decimal             # zone bottom
    high: Decimal            # zone top
    created_at: datetime     # ts of bar 3 (when the FVG became visible)


@dataclass(frozen=True)
class DisplacementEvent:
    """
    A displacement just completed.

    `displacement_bar` is bar 2 (the impulsive one). The FVG, if any,
    is reported separately — sometimes a displacement bar exists
    without leaving an FVG (when the surrounding bars overlap), and
    that's still useful info even if not directly tradeable.
    """

    side: DisplacementSide
    displacement_bar: Bar
    body_size: Decimal       # absolute |close - open|
    atr_at_event: Decimal    # ATR value used for the threshold check
    body_to_atr: Decimal     # ratio — for logging/dashboarding
    fvg: FairValueGap | None # may be None if no gap formed


@dataclass
class DisplacementConfig:
    """Detector parameters. All configurable per instrument."""

    # ATR lookback. 14 is the convention; tune for your timeframe.
    atr_period: int = 14

    # Body must be at least this multiple of ATR to qualify. 1.5–2.0
    # is a reasonable starting range; tune from backtest.
    body_atr_multiple: Decimal = Decimal("1.5")

    # Body must also be at least this fraction of total bar range.
    # Rejects "spinning top" bars that are big in absolute terms but
    # mostly wick. 0.6 = body must be 60% of the bar.
    min_body_to_range_ratio: Decimal = Decimal("0.6")

    # Floor on the absolute body size, in price units. Prevents tiny
    # bars in low-volatility regimes from counting as displacements
    # just because ATR is also tiny. /MGC: $1.00 = 10 ticks.
    min_absolute_body: Decimal = Decimal("1.0")


class DisplacementDetector:
    """
    Per-instrument displacement detector.

    Streaming: feed bars in via on_bar(), get DisplacementEvents out.
    Maintains a rolling 3-bar window plus ATR state.
    """

    def __init__(self, config: DisplacementConfig | None = None) -> None:
        self.config = config or DisplacementConfig()
        self._window: Deque[Bar] = deque(maxlen=3)
        self._tr_window: Deque[Decimal] = deque(maxlen=self.config.atr_period)
        self._atr: Decimal | None = None
        self._prev_close: Decimal | None = None

    @property
    def atr(self) -> Decimal | None:
        """Current ATR value, or None if not yet warmed up."""
        return self._atr

    def on_bar(self, bar: Bar) -> DisplacementEvent | None:
        """
        Process a closed bar. Returns a DisplacementEvent if the bar
        that just closed (bar 3 of the window) revealed a displacement
        on the bar BEFORE it (bar 2). Returns None otherwise.

        We only fire after bar 3 because the FVG can only be confirmed
        once bar 3 has closed. This is the same one-bar lag that
        anyone using FVGs lives with — you cannot enter on the
        displacement bar itself, only the bar after.
        """
        self._update_atr(bar)
        self._window.append(bar)

        if len(self._window) < 3:
            return None
        if self._atr is None:
            return None

        bar1, bar2, bar3 = self._window[0], self._window[1], self._window[2]
        return self._evaluate(bar1, bar2, bar3)

    # ------------------------------------------------------------------
    # ATR (Wilder's smoothing)
    # ------------------------------------------------------------------

    def _update_atr(self, bar: Bar) -> None:
        """Update the running ATR with the True Range of `bar`."""
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(
                bar.high - bar.low,
                abs(bar.high - self._prev_close),
                abs(bar.low - self._prev_close),
            )
        self._tr_window.append(tr)
        self._prev_close = bar.close

        if len(self._tr_window) < self.config.atr_period:
            self._atr = None
            return

        if self._atr is None:
            # First fully-warm value: simple average of TR window.
            total = sum(self._tr_window, Decimal("0"))
            self._atr = total / Decimal(self.config.atr_period)
        else:
            # Wilder smoothing: ATR = (prev_ATR * (n-1) + TR) / n
            n = Decimal(self.config.atr_period)
            self._atr = (self._atr * (n - 1) + tr) / n

    # ------------------------------------------------------------------
    # Displacement evaluation
    # ------------------------------------------------------------------

    def _evaluate(self, b1: Bar, b2: Bar, b3: Bar) -> DisplacementEvent | None:
        """Did bar 2 displace? If so, did it leave an FVG?"""
        cfg = self.config
        atr = self._atr
        assert atr is not None  # guarded by caller

        body = abs(b2.close - b2.open)
        bar_range = b2.high - b2.low

        if body < cfg.min_absolute_body:
            return None
        if bar_range == 0:
            return None
        if body / bar_range < cfg.min_body_to_range_ratio:
            return None
        if body < atr * cfg.body_atr_multiple:
            return None

        # Direction follows the body's sign.
        if b2.close > b2.open:
            side: DisplacementSide = "bullish"
        elif b2.close < b2.open:
            side = "bearish"
        else:
            return None  # doji body — already filtered by min_absolute_body
                         # in normal cases, kept as a safety net

        fvg = self._compute_fvg(b1, b3, side)

        ratio = body / atr  # safe: atr is non-zero in normal markets;
                            # if ATR is 0 we'd not have warmed up.
        return DisplacementEvent(
            side=side,
            displacement_bar=b2,
            body_size=body,
            atr_at_event=atr,
            body_to_atr=ratio,
            fvg=fvg,
        )

    def peek_displacement(self) -> "tuple[DisplacementSide, Bar, Bar] | None":
        """
        Non-mutating: return (side, b1, b2) if the most recently processed bar
        qualifies as a displacement. b2 = window[-1], b1 = window[-2].
        The caller can then test FVG by passing a forming bar as b3 to
        DisplacementDetector._compute_fvg(b1, forming_bar, side).
        Returns None if ATR not warmed up or window too small.
        """
        if len(self._window) < 2 or self._atr is None:
            return None
        b1 = self._window[-2]
        b2 = self._window[-1]
        cfg = self.config
        atr = self._atr

        body = abs(b2.close - b2.open)
        bar_range = b2.high - b2.low

        if body < cfg.min_absolute_body:
            return None
        if bar_range == 0:
            return None
        if body / bar_range < cfg.min_body_to_range_ratio:
            return None
        if body < atr * cfg.body_atr_multiple:
            return None

        if b2.close > b2.open:
            return "bullish", b1, b2
        if b2.close < b2.open:
            return "bearish", b1, b2
        return None

    @staticmethod
    def _compute_fvg(
        b1: Bar,
        b3: Bar,
        side: DisplacementSide,
    ) -> FairValueGap | None:
        """
        FVG check on the outer two bars of a 3-bar window.

        Bullish FVG exists iff b3.low > b1.high.
        Bearish FVG exists iff b3.high < b1.low.

        If the gap doesn't exist (bars overlap), returns None — the
        displacement still happened, it just isn't a clean entry zone.
        """
        if side == "bullish" and b3.low > b1.high:
            return FairValueGap(
                side="bullish",
                low=b1.high,
                high=b3.low,
                created_at=b3.ts,
            )
        if side == "bearish" and b3.high < b1.low:
            return FairValueGap(
                side="bearish",
                low=b3.high,
                high=b1.low,
                created_at=b3.ts,
            )
        return None
