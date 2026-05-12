"""
Liquidity tracker — swing structure and sweep detection.

Two responsibilities:

  1. Maintain a rolling list of recent swing highs and lows. A swing
     high is a bar whose high is greater than the high of `lookback`
     bars on each side. Same for swing lows mirrored.

  2. Detect when price "sweeps" a swing level. Two patterns supported,
     either of which emits a sweep:

     Pattern A — Tag and reverse over multiple bars:
        Bar N tags the level (high >= swing_high + min_penetration).
        Within the next K bars, price closes back below the swing high.
        Emit on the bar that completes the close-back.

     Pattern B — One-bar reversal:
        A single bar's high pierces the swing high by min_penetration AND
        its close is back below the swing high. Emits on the same bar.

     The mirror cases for swing lows.

A "sweep" is what price does to take liquidity above old highs or
below old lows before reversing — the structural prerequisite for the
displacement leg. The displacement detector consumes our SweepEvents
and decides whether a tradeable signal forms.

Design choices:
  - Pure: feed bars in, get events out. No I/O, no broker dependency.
  - Streaming: each on_bar() call is O(swings_tracked + lookback_window).
  - Memory: bounded — old swings drop off after `max_swings`.
  - Confirmation lag: a swing high at bar N is only confirmed at bar
    N+lookback. We never "see" a swing in real time; this is a real
    constraint on any structural strategy. Tests assert this property.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Deque, Literal

from app.broker.events import Bar

log = logging.getLogger(__name__)

SwingKind = Literal["high", "low"]
SweepSide = Literal["high", "low"]  # which side of the book got swept


@dataclass(frozen=True)
class Swing:
    """A confirmed swing high or low."""

    kind: SwingKind
    price: Decimal
    bar_ts: datetime          # timestamp of the swing bar itself
    confirmed_ts: datetime    # timestamp when we recognized it (later)


@dataclass(frozen=True)
class SweepEvent:
    """
    A sweep just completed.

    `side` indicates which kind of liquidity was taken:
      - "high" = swept buy-stops above an old swing high
      - "low"  = swept sell-stops below an old swing low

    The displacement detector reads `side` to decide which way the
    reversal would go: a high sweep typically precedes a SHORT setup,
    a low sweep precedes a LONG setup.
    """

    side: SweepSide
    swept_swing: Swing
    pattern: Literal["A_multi_bar", "B_one_bar"]
    sweep_extreme: Decimal     # the highest high (or lowest low) that took the level
    completed_at: datetime     # bar ts when the close-back confirmed the sweep


@dataclass
class LiquidityConfig:
    """Detector parameters. All fields configurable per instrument."""

    # Bars on each side that must be lower (for a high) / higher (for a low)
    # to confirm a swing. Higher = stricter, fewer signals.
    swing_lookback: int = 3

    # How far past the swing a bar must trade for it to count as a tag.
    # In price units of the instrument (NOT ticks). For /MGC at 0.10 tick,
    # 0.20 = 2 ticks of penetration.
    min_penetration: Decimal = Decimal("0.20")

    # For Pattern A: how many bars after the tag we still consider for
    # the close-back. 3 = the tagging bar plus 2 more.
    multi_bar_window: int = 3

    # Cap on how many recent swings we track. Old swings fall off.
    max_swings: int = 50


@dataclass
class _PendingSweep:
    """Internal: a tag that hasn't yet been confirmed or expired."""

    swing: Swing
    side: SweepSide
    tagged_at_bar_idx: int       # bar index when tag occurred
    sweep_extreme: Decimal       # extreme reached during the pending window


class LiquidityTracker:
    """
    Per-instrument tracker. One instance per (instrument, timeframe).

    Usage:
        tracker = LiquidityTracker(LiquidityConfig())
        for bar in bars:
            sweeps = tracker.on_bar(bar)
            for sweep in sweeps:
                ...
    """

    def __init__(self, config: LiquidityConfig | None = None) -> None:
        self.config = config or LiquidityConfig()

        # Rolling window of recent bars — needs lookback*2 + 1 to confirm
        # the middle bar as a swing. We keep a bit more for safety.
        self._bars: Deque[Bar] = deque(maxlen=self.config.swing_lookback * 4 + 1)

        # Confirmed swings, capped at max_swings.
        self._swings: Deque[Swing] = deque(maxlen=self.config.max_swings)

        # Pending Pattern-A sweeps awaiting close-back.
        self._pending: list[_PendingSweep] = []

        # Swings that have already produced a sweep event OR a pending
        # tag that expired. Either way, don't re-tag — once is enough.
        # Without this, a swing that's been tagged and expired would
        # re-tag on the very next bar that pierces it again, defeating
        # the multi-bar window.
        self._used_swings: set[int] = set()  # set of id(Swing)

        # Monotonic bar counter for windowing.
        self._bar_idx = 0

    # ------------------------------------------------------------------
    # Read-only views — useful for tests, the dashboard, and the composer.
    # ------------------------------------------------------------------

    @property
    def swings(self) -> list[Swing]:
        return list(self._swings)

    @property
    def recent_high_swings(self) -> list[Swing]:
        return [s for s in self._swings if s.kind == "high"]

    @property
    def recent_low_swings(self) -> list[Swing]:
        return [s for s in self._swings if s.kind == "low"]

    # ------------------------------------------------------------------
    # Bar ingestion — the only state-changing entry point.
    # ------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> list[SweepEvent]:
        """
        Process a closed bar. Returns any sweep events that completed
        on this bar.

        Order of operations matters:
          1. Buffer the bar.
          2. Try to confirm a new swing in the middle of the buffer.
          3. Check for Pattern B (one-bar) sweeps against existing swings.
          4. Update Pattern A pending sweeps; emit any that closed back;
             expire any that ran past the window.
          5. Check for new Pattern A tags on existing swings.
        """
        self._bar_idx += 1
        self._bars.append(bar)
        events: list[SweepEvent] = []

        # 1+2. Confirm a swing if buffer is full enough.
        new_swing = self._maybe_confirm_swing()
        if new_swing is not None:
            self._swings.append(new_swing)

        # 3. Pattern B — one-bar sweep + reversal on the current bar.
        events.extend(self._detect_pattern_b(bar))

        # 4. Pattern A — update pending tags.
        events.extend(self._resolve_pending(bar))

        # 5. Pattern A — register new tags from the current bar.
        self._register_new_tags(bar)

        return events

    # ------------------------------------------------------------------
    # Swing confirmation
    # ------------------------------------------------------------------

    def _maybe_confirm_swing(self) -> Swing | None:
        """
        Check if the bar at position -lookback-1 (the middle of a
        symmetric window) is a swing high or low.

        We need lookback bars before AND after the candidate. The buffer
        always has the most recent bar at index -1; the candidate sits
        at index -(lookback+1).
        """
        lb = self.config.swing_lookback
        needed = lb * 2 + 1
        if len(self._bars) < needed:
            return None

        candidate_idx = -lb - 1
        candidate = self._bars[candidate_idx]

        # Take lookback bars on each side of the candidate.
        # In a deque indexed from the right, the candidate is at -(lb+1),
        # the bars after it are -lb..-1, and bars before it are
        # -(lb+2)..-(2*lb+1).
        left = [self._bars[i] for i in range(-(lb * 2 + 1), -(lb + 1))]
        right = [self._bars[i] for i in range(-lb, 0)]

        # Swing high: candidate.high strictly greater than every other.
        is_swing_high = (
            all(candidate.high > b.high for b in left)
            and all(candidate.high > b.high for b in right)
        )
        if is_swing_high:
            return Swing(
                kind="high",
                price=candidate.high,
                bar_ts=candidate.ts,
                confirmed_ts=self._bars[-1].ts,
            )

        is_swing_low = (
            all(candidate.low < b.low for b in left)
            and all(candidate.low < b.low for b in right)
        )
        if is_swing_low:
            return Swing(
                kind="low",
                price=candidate.low,
                bar_ts=candidate.ts,
                confirmed_ts=self._bars[-1].ts,
            )

        return None

    # ------------------------------------------------------------------
    # Sweep detection
    # ------------------------------------------------------------------

    def _detect_pattern_b(self, bar: Bar) -> list[SweepEvent]:
        """
        Pattern B — one-bar sweep + reversal.

        Bar's high pierces swing high by min_penetration AND
        bar closes BELOW the swing high. Mirror for lows.

        We check against the most recent swing of each kind that hasn't
        been swept yet. (A swing that was already swept is still in the
        list — that's fine; sweeping it again is unusual but valid.)
        """
        events: list[SweepEvent] = []
        pen = self.config.min_penetration

        # Check high sweeps — each unswept swing high above bar.close.
        for swing in reversed(list(self._swings)):
            if swing.kind != "high":
                continue
            if id(swing) in self._used_swings:
                continue
            if (
                bar.high >= swing.price + pen
                and bar.close < swing.price
            ):
                events.append(SweepEvent(
                    side="high",
                    swept_swing=swing,
                    pattern="B_one_bar",
                    sweep_extreme=bar.high,
                    completed_at=bar.ts,
                ))
                self._used_swings.add(id(swing))
                break  # one sweep per bar per side; nearest swing wins

        for swing in reversed(list(self._swings)):
            if swing.kind != "low":
                continue
            if id(swing) in self._used_swings:
                continue
            if (
                bar.low <= swing.price - pen
                and bar.close > swing.price
            ):
                events.append(SweepEvent(
                    side="low",
                    swept_swing=swing,
                    pattern="B_one_bar",
                    sweep_extreme=bar.low,
                    completed_at=bar.ts,
                ))
                self._used_swings.add(id(swing))
                break

        return events

    def _register_new_tags(self, bar: Bar) -> None:
        """
        Pattern A step 1: did this bar tag a swing without immediately
        closing back? If so, start a pending sweep.

        We exclude any swing that produced a Pattern B sweep on the
        SAME bar — Pattern B already emitted, no need to double-count.
        We also exclude swings that have already been used (either
        emitted a sweep or had their pending tag expire).
        """
        pen = self.config.min_penetration

        # High tags: bar pierced a swing high but closed at/above it.
        for swing in reversed(list(self._swings)):
            if swing.kind != "high":
                continue
            if id(swing) in self._used_swings:
                continue
            if (
                bar.high >= swing.price + pen
                and bar.close >= swing.price
            ):
                # Skip if we already have a pending tag on this exact swing.
                if any(p.swing is swing for p in self._pending):
                    continue
                self._pending.append(_PendingSweep(
                    swing=swing,
                    side="high",
                    tagged_at_bar_idx=self._bar_idx,
                    sweep_extreme=bar.high,
                ))
                break

        # Low tags: bar pierced a swing low but closed at/below it.
        for swing in reversed(list(self._swings)):
            if swing.kind != "low":
                continue
            if id(swing) in self._used_swings:
                continue
            if (
                bar.low <= swing.price - pen
                and bar.close <= swing.price
            ):
                if any(p.swing is swing for p in self._pending):
                    continue
                self._pending.append(_PendingSweep(
                    swing=swing,
                    side="low",
                    tagged_at_bar_idx=self._bar_idx,
                    sweep_extreme=bar.low,
                ))
                break

    def _resolve_pending(self, bar: Bar) -> list[SweepEvent]:
        """
        Pattern A step 2: for each pending tag, did THIS bar close
        back through the swing? If yes, emit. If the window expired,
        drop without emitting.

        Either outcome marks the swing as used so a later bar piercing
        the same level doesn't re-tag.
        """
        events: list[SweepEvent] = []
        still_pending: list[_PendingSweep] = []
        window = self.config.multi_bar_window

        for p in self._pending:
            elapsed = self._bar_idx - p.tagged_at_bar_idx

            # Update the running extreme even before resolution.
            if p.side == "high" and bar.high > p.sweep_extreme:
                p_extreme = bar.high
            elif p.side == "low" and bar.low < p.sweep_extreme:
                p_extreme = bar.low
            else:
                p_extreme = p.sweep_extreme

            if p.side == "high" and bar.close < p.swing.price:
                events.append(SweepEvent(
                    side="high",
                    swept_swing=p.swing,
                    pattern="A_multi_bar",
                    sweep_extreme=p_extreme,
                    completed_at=bar.ts,
                ))
                self._used_swings.add(id(p.swing))
                continue  # resolved, drop from pending

            if p.side == "low" and bar.close > p.swing.price:
                events.append(SweepEvent(
                    side="low",
                    swept_swing=p.swing,
                    pattern="A_multi_bar",
                    sweep_extreme=p_extreme,
                    completed_at=bar.ts,
                ))
                self._used_swings.add(id(p.swing))
                continue

            if elapsed >= window:
                # Expired — price never closed back. No sweep event.
                # Mark as used so a later bar piercing the same level
                # doesn't immediately re-tag and bypass the window.
                self._used_swings.add(id(p.swing))
                continue

            still_pending.append(_PendingSweep(
                swing=p.swing,
                side=p.side,
                tagged_at_bar_idx=p.tagged_at_bar_idx,
                sweep_extreme=p_extreme,
            ))

        self._pending = still_pending
        return events
