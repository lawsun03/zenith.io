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
    # Bar immediately before the displacement bar (b1) — the order-block
    # candidate for the composer's ob_fallback mode. None for hand-built events.
    prev_bar: Bar | None = None


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

    # Price-normalized floor (B6): when > 0, threshold = bar_close * pct
    # instead of the fixed min_absolute_body. 0 = disabled (default).
    # Calibration: 5.0pts / 21000 ≈ 0.000238 matches MNQ at 2024+ prices.
    min_absolute_body_pct: Decimal = Decimal("0")

    # Use the ATR from N bars ago as the body threshold reference (0 = off,
    # current behavior). Rationale: a volatility flush inflates ATR exactly
    # when the reversal displacement prints, raising the bar pro-cyclically —
    # V-bottom impulses get filtered by the very move they reverse.
    atr_ref_lag_bars: int = 0


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
        self._active_fvgs: deque[FairValueGap] = deque(maxlen=30)
        # ATR history for the lagged threshold reference (atr_ref_lag_bars).
        self._atr_hist: Deque[Decimal] = deque(
            maxlen=max(1, self.config.atr_ref_lag_bars + 1))

    @property
    def atr(self) -> Decimal | None:
        """Current ATR value, or None if not yet warmed up."""
        return self._atr

    @property
    def active_fvgs(self) -> list[FairValueGap]:
        """Unmitigated 1min FVGs available for iFVG inversion detection."""
        return list(self._active_fvgs)

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

        # Evaluate inversion FIRST (body-close check on b2 vs active FVGs).
        # This must happen before mitigation so the same bar can't both
        # invert and mitigate the same FVG — inversion wins.
        result = self._evaluate(bar1, bar2, bar3)

        # Wick-based mitigation: remove FVGs whose far edge was pierced by
        # bar3's wick, EXCEPT any FVG that was just inverted (already captured
        # in result.fvg). Pass inverted_fvg=None if no inversion fired.
        inverted = result.fvg if result is not None else None
        self._mitigate_fvgs(bar3, inverted)

        # Form any new 3-bar FVG from the current window AFTER inversion check
        # so a newly-formed FVG can't be immediately inverted on the same bar.
        new_fvg = self._form_fvg(bar1, bar3)
        if new_fvg is not None:
            self._active_fvgs.append(new_fvg)

        return result

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
        if self._atr is not None:
            self._atr_hist.append(self._atr)

    def _threshold_atr(self) -> Decimal | None:
        """ATR used for the body threshold: the lagged value when
        atr_ref_lag_bars is set (pre-flush reference), else current."""
        if self.config.atr_ref_lag_bars > 0 and self._atr_hist:
            return self._atr_hist[0]
        return self._atr

    # ------------------------------------------------------------------
    # Displacement evaluation
    # ------------------------------------------------------------------

    def _form_fvg(self, b1: Bar, b3: Bar) -> FairValueGap | None:
        """Check if b1 and b3 bracket a 3-bar gap."""
        if b3.low > b1.high:
            return FairValueGap(side="bullish", low=b1.high, high=b3.low, created_at=b3.ts)
        if b3.high < b1.low:
            return FairValueGap(side="bearish", low=b3.high, high=b1.low, created_at=b3.ts)
        return None

    def _find_inverted_fvg(
        self, displacement_bar: Bar, side: DisplacementSide, prev_close: Decimal
    ) -> FairValueGap | None:
        """
        Search _active_fvgs for one the displacement bar's body closed through.
        Bearish displacement (close < open) inverts bullish FVG when bar.close < fvg.low.
        Bullish displacement (close > open) inverts bearish FVG when bar.close > fvg.high.
        Returns the most-recently-formed matching FVG, or None.

        The inversion must happen ON the displacement bar: prev_close (the bar
        before it) must still be on the near side of the far edge. Without this,
        an FVG that price closed through long ago (with no displacement firing)
        re-matches every later displacement bar — entries land at stale FVG
        edges far from market (2026-06-10 parity post-mortem, up to 70 pts off).
        """
        for fvg in reversed(self._active_fvgs):
            if side == "bearish" and fvg.side == "bullish":
                if displacement_bar.close < fvg.low <= prev_close:
                    return fvg
            elif side == "bullish" and fvg.side == "bearish":
                if displacement_bar.close > fvg.high >= prev_close:
                    return fvg
        return None

    def _mitigate_fvgs(self, bar: Bar, inverted_fvg: FairValueGap | None) -> None:
        """
        Remove FVGs whose far edge was breached by a wick WITHOUT closing through —
        EXCEPT the one that was just inverted (inversion wins over mitigation).

        Bullish FVG far edge = fvg.low:
          - Mitigated when bar.low <= fvg.low AND bar.close > fvg.low (wick only).
          - If bar.close <= fvg.low, the bar closed through → that's inversion, not
            mitigation. Keep the FVG so a subsequent inversion check can claim it.
        Bearish FVG far edge = fvg.high:
          - Mitigated when bar.high >= fvg.high AND bar.close < fvg.high.
          - If bar.close >= fvg.high, the bar closed through → keep for inversion.
        """
        self._active_fvgs = deque(
            (fvg for fvg in self._active_fvgs
             if fvg is inverted_fvg or not (
                 (fvg.side == "bullish" and bar.low <= fvg.low and bar.close > fvg.low) or
                 (fvg.side == "bearish" and bar.high >= fvg.high and bar.close < fvg.high)
             )),
            maxlen=30,
        )

    def _evaluate(self, b1: Bar, b2: Bar, b3: Bar) -> DisplacementEvent | None:
        """
        Did bar2 displace AND invert a prior active FVG (iFVG)?
        Body/ATR/range thresholds unchanged. FVG reported is the prior
        active FVG that b2's body closed through — not b2's own 3-bar gap.
        If no prior FVG was inverted, fvg=None.
        """
        cfg = self.config
        atr = self._threshold_atr()
        assert atr is not None  # guarded by caller

        body = abs(b2.close - b2.open)
        bar_range = b2.high - b2.low

        min_body = (b2.close * cfg.min_absolute_body_pct
                    if cfg.min_absolute_body_pct > 0
                    else cfg.min_absolute_body)
        if body < min_body:
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

        ifvg = self._find_inverted_fvg(b2, side, prev_close=b1.close)

        ratio = body / atr  # safe: atr is non-zero in normal markets;
                            # if ATR is 0 we'd not have warmed up.
        return DisplacementEvent(
            side=side,
            displacement_bar=b2,
            body_size=body,
            atr_at_event=atr,
            body_to_atr=ratio,
            fvg=ifvg,
            prev_bar=b1,
        )

    def peek_displacement(self) -> "tuple[DisplacementSide, Bar, Bar] | None":
        """
        Non-mutating: return (side, b1, b2) if the most recent bar qualifies
        as a displacement bar AND there is a prior active FVG it could invert.
        Returns None if no prior FVG exists to invert (no iFVG entry possible).
        """
        if len(self._window) < 2 or self._atr is None:
            return None
        b1 = self._window[-2]
        b2 = self._window[-1]
        cfg = self.config
        atr = self._atr

        body = abs(b2.close - b2.open)
        bar_range = b2.high - b2.low

        min_body = (b2.close * cfg.min_absolute_body_pct
                    if cfg.min_absolute_body_pct > 0
                    else cfg.min_absolute_body)
        if body < min_body:
            return None
        if bar_range == 0:
            return None
        if body / bar_range < cfg.min_body_to_range_ratio:
            return None
        if body < atr * cfg.body_atr_multiple:
            return None

        if b2.close > b2.open:
            side: DisplacementSide = "bullish"
        elif b2.close < b2.open:
            side = "bearish"
        else:
            return None

        if self._find_inverted_fvg(b2, side, prev_close=b1.close) is None:
            return None

        return side, b1, b2

