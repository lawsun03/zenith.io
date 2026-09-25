"""Pure predicate evaluators for the Strategy IR.

Every op in strategy-ir.schema.json's vocabulary is implemented here as a
small streaming node: `on_bar(bar)` advances state by exactly one bar and
returns whether the predicate fired on that bar. This mirrors the existing
codebase's own definition of "pure" (see app/strategy/liquidity.py,
displacement.py): deterministic, no I/O, no hidden external state — output
is determined solely by the sequence of bars fed via on_bar, in order.
"No lookahead" is structural, not a convention: a node only ever sees the
bar passed to on_bar, never a future one (tests/test_ir_predicates.py
verifies this with a property test, per CLAUDE.md rule 9).

and/or composition (compose_predicate): each node tracks bars_since_fired
and a persistence window (the op's own `within_bars`, default 1 = "must
fire on this exact bar"). An `and` node is satisfied when every operand
fired within its own persistence window, and fires itself (edge-triggered)
only on the bar all operands become simultaneously satisfied — this is
what reproduces the composer.py state machine ("sweep arms, displacement
must confirm within N bars") using nothing but and/or + within_bars,
without a bespoke "sequence" combinator the schema doesn't have.

Two ops the schema lists have no clean resolution and are NOT implemented:
`opening_range_high`/`opening_range_low` as a `level` value (the schema
gives no field for the opening range's length) and `prior_session_high`/
`prior_session_low` (no field says which session). Both raise
LevelNotResolvable rather than guessing a default — a strategy using them
is rejected, loudly, per CLAUDE.md rule 12 ("a gate that cannot be
evaluated is a failure, not a pass").
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, time as dtime, timedelta
from decimal import Decimal
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

from app.sim.events import Bar

ET = ZoneInfo("America/New_York")

_BIG = 10**9  # "never fired" sentinel for bars_since_fired


class LevelNotResolvable(Exception):
    """A `level` the schema names but this interpreter cannot compute."""


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------

class ATRTracker:
    """Wilder's ATR, period bars. Identical method to
    app/strategy/displacement.py's _update_atr (not reused directly —
    that class also carries FVG state we don't want here)."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._tr: deque[Decimal] = deque(maxlen=period)
        self._prev_close: Decimal | None = None
        self.value: Decimal | None = None

    def on_bar(self, bar: Bar) -> Decimal | None:
        if self._prev_close is None:
            tr = bar.high - bar.low
        else:
            tr = max(bar.high - bar.low,
                      abs(bar.high - self._prev_close),
                      abs(bar.low - self._prev_close))
        self._tr.append(tr)
        self._prev_close = bar.close
        if len(self._tr) < self.period:
            self.value = None
            return None
        if self.value is None:
            self.value = sum(self._tr, Decimal("0")) / Decimal(self.period)
        else:
            n = Decimal(self.period)
            self.value = (self.value * (n - 1) + tr) / n
        return self.value


class LevelTracker:
    """Resolves the IR `level` vocabulary to a current Decimal price.

    Shared across all leaves in a predicate tree (constructed once per
    compiled strategy) since several levels (prior_day_high, session_open)
    are meaningful independent of any single predicate.
    """

    def __init__(self, session_start: dtime) -> None:
        self._session_start = session_start
        self._et_day: date | None = None
        self._week: tuple[int, int] | None = None

        self.session_open: Decimal | None = None
        self.prior_day_high: Decimal | None = None
        self.prior_day_low: Decimal | None = None
        self.midnight_open: Decimal | None = None
        self.London_open: Decimal | None = None
        self.NY_open: Decimal | None = None
        self.prior_week_high: Decimal | None = None
        self.prior_week_low: Decimal | None = None

        # Indicator levels. Fixed names (sma_7/sma_21/vwap), not a parameterised
        # `level`, because the IR's level field is a closed enum — a small,
        # deliberate vocabulary keeps the search surface narrow (CLAUDE.md
        # rule 2). SMAs are of the 1-minute close over the last N bars fed,
        # across session boundaries; VWAP is anchored at session_start each
        # ET day (typical price (H+L+C)/3 x volume). Every value at bar t
        # uses only bars <= t (same contract as session_open etc.).
        self.sma_7: Decimal | None = None
        self.sma_21: Decimal | None = None
        self.vwap: Decimal | None = None
        self._closes_7: deque[Decimal] = deque(maxlen=7)
        self._closes_21: deque[Decimal] = deque(maxlen=21)
        self._sum_7 = Decimal("0")
        self._sum_21 = Decimal("0")
        self._vwap_pv = Decimal("0")
        self._vwap_v = 0

        self._day_high: Decimal | None = None
        self._day_low: Decimal | None = None
        self._week_high: Decimal | None = None
        self._week_low: Decimal | None = None
        self._in_session_today = False

    def on_bar(self, bar: Bar) -> None:
        et = bar.ts.astimezone(ET)
        et_day = et.date()
        if et_day != self._et_day:
            if self._et_day is not None:
                self.prior_day_high = self._day_high
                self.prior_day_low = self._day_low
            self._et_day = et_day
            self._day_high = bar.high
            self._day_low = bar.low
            self._in_session_today = False
            self._vwap_pv = Decimal("0")
            self._vwap_v = 0
            self.vwap = None
        else:
            self._day_high = max(self._day_high, bar.high)
            self._day_low = min(self._day_low, bar.low)

        iso = et.isocalendar()
        week_key = (iso[0], iso[1])
        if week_key != self._week:
            if self._week is not None:
                self.prior_week_high = self._week_high
                self.prior_week_low = self._week_low
            self._week = week_key
            self._week_high = bar.high
            self._week_low = bar.low
        else:
            self._week_high = max(self._week_high, bar.high)
            self._week_low = min(self._week_low, bar.low)

        if et.time() == dtime(0, 0):
            self.midnight_open = bar.open
        if et.time() == dtime(2, 0):
            self.London_open = bar.open
        if et.time() == dtime(9, 30):
            self.NY_open = bar.open
        if not self._in_session_today and et.time() >= self._session_start:
            self.session_open = bar.open
            self._in_session_today = True

        if et.time() >= self._session_start and bar.volume > 0:
            typical = (bar.high + bar.low + bar.close) / Decimal(3)
            self._vwap_pv += typical * bar.volume
            self._vwap_v += bar.volume
            self.vwap = self._vwap_pv / Decimal(self._vwap_v)

        self.sma_7 = self._roll("_closes_7", "_sum_7", 7, bar.close)
        self.sma_21 = self._roll("_closes_21", "_sum_21", 21, bar.close)

    def _roll(self, window_attr: str, sum_attr: str, n: int, close: Decimal) -> Decimal | None:
        window: deque[Decimal] = getattr(self, window_attr)
        total: Decimal = getattr(self, sum_attr)
        if len(window) == n:
            total -= window[0]
        window.append(close)
        total += close
        setattr(self, sum_attr, total)
        return total / Decimal(n) if len(window) == n else None

    def get(self, name: str) -> Decimal | None:
        if name in ("opening_range_high", "opening_range_low",
                    "prior_session_high", "prior_session_low",
                    "swing_high", "swing_low"):
            raise LevelNotResolvable(
                f"level {name!r} needs a parameter (range length / session "
                "name / swing lookback) the IR schema has no field for"
            )
        try:
            return getattr(self, name)
        except AttributeError:
            raise LevelNotResolvable(f"unknown level {name!r}") from None


@dataclass
class EvalCtx:
    """Threaded through on_bar so leaves can share the level tracker and
    read/write per-instrument config without global state."""

    levels: LevelTracker


# ----------------------------------------------------------------------
# Node base + combinators
# ----------------------------------------------------------------------

class Node:
    persistence_window: int = 1

    def __init__(self) -> None:
        self.bars_since_fired = _BIG
        # Set every on_bar call, independent of consume() — consume() resets
        # bars_since_fired (for the *next* AND evaluation) but callers above
        # the tree (the engine, extracting entry/stop prices) still need to
        # know which leaves fired on THIS bar after the AND has already
        # consumed them.
        self.fired_last = False

    def on_bar(self, bar: Bar, ctx: EvalCtx) -> bool:
        fired = self._evaluate(bar, ctx)
        self.fired_last = fired
        self.bars_since_fired = 0 if fired else self.bars_since_fired + 1
        return fired

    def satisfied(self) -> bool:
        return self.bars_since_fired < self.persistence_window

    def consume(self) -> None:
        self.bars_since_fired = _BIG

    def reset(self) -> None:
        """Clear any per-trade reference state (entry-relative trackers
        like trailing_stop/atr_multiple_move/bars_elapsed). Called by the
        engine whenever a new position opens — an exit tree is reused
        across trades, so 'since entry' must mean *this* entry. No-op for
        leaves with no such state (the default)."""
        self.bars_since_fired = _BIG
        self.fired_last = False

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        raise NotImplementedError


class AndNode(Node):
    def __init__(self, children: list[Node]) -> None:
        super().__init__()
        self.children = children

    def reset(self) -> None:
        super().reset()
        for c in self.children:
            c.reset()

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        for c in self.children:
            c.on_bar(bar, ctx)
        all_sat = all(c.satisfied() for c in self.children)
        if all_sat:
            for c in self.children:
                c.consume()
        return all_sat


class OrNode(Node):
    def __init__(self, children: list[Node]) -> None:
        super().__init__()
        self.children = children
        # Which child last fired True — the engine needs this to know which
        # branch's leaves (e.g. the long side's SweepOfLeaf) to read entry/
        # stop side-channel data from; a leaf that armed several bars ago
        # (still within its persistence window) won't have fired_last=True
        # on the AND's actual trigger bar, so "the branch that fired" beats
        # "leaves with fired_last=True this exact bar".
        self.last_fired_child: Node | None = None

    def reset(self) -> None:
        super().reset()
        self.last_fired_child = None
        for c in self.children:
            c.reset()

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        self.last_fired_child = None
        any_fired = False
        for c in self.children:
            if c.on_bar(bar, ctx):
                any_fired = True
                self.last_fired_child = c
        return any_fired


# ----------------------------------------------------------------------
# Recognizable leaves
# ----------------------------------------------------------------------

@dataclass
class _PendingTag:
    swing_id: int
    swing_price: Decimal
    bars_waited: int
    extreme: Decimal


class SweepOfLeaf(Node):
    """Sweep of a swing high/low. `n` = bars each side to confirm the swing
    (default 2, matching StrategyParams.swing_lookback). Penetration
    threshold is either ATR-relative (`min_atr`, pools cleanly across
    NQ/ES/GC's very different price scales — CLAUDE.md rule 2) or a fixed
    price-unit constant (`min_offset`, for reproducing a hand-coded
    strategy's own fixed threshold bit-for-bit). Exactly one must be given.
    `within_bars` doubles as the Pattern-A close-back window and this
    leaf's AND-persistence window."""

    def __init__(self, level: str, n: int = 2, min_atr: Decimal | None = None,
                 min_offset: Decimal | None = None,
                 within_bars: int = 5, atr_period: int = 14) -> None:
        super().__init__()
        if level not in ("swing_high", "swing_low"):
            raise ValueError(f"sweep_of.level must be swing_high/swing_low, got {level!r}")
        if (min_atr is None) == (min_offset is None):
            raise ValueError("sweep_of requires exactly one of min_atr / min_offset")
        self.side = "high" if level == "swing_high" else "low"
        self.n = n
        self.min_atr_factor = min_atr
        self.min_offset = min_offset
        self.persistence_window = within_bars
        self._atr = ATRTracker(atr_period)
        self._bars: deque[Bar] = deque(maxlen=n * 4 + 1)
        self._swings: deque[tuple[Decimal, int]] = deque(maxlen=50)
        self._swing_counter = 0
        self._used: set[int] = set()
        self._pending: list[_PendingTag] = []
        self.last_extreme: Decimal | None = None

    def _maybe_confirm_swing(self) -> None:
        need = 2 * self.n + 1
        if len(self._bars) < need:
            return
        w = list(self._bars)[-need:]
        mid = w[self.n]
        left, right = w[:self.n], w[self.n + 1:]
        if self.side == "high":
            if all(mid.high > b.high for b in left) and all(mid.high > b.high for b in right):
                self._swings.append((mid.high, self._swing_counter))
                self._swing_counter += 1
        else:
            if all(mid.low < b.low for b in left) and all(mid.low < b.low for b in right):
                self._swings.append((mid.low, self._swing_counter))
                self._swing_counter += 1

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        atr = self._atr.on_bar(bar)
        self._bars.append(bar)
        self._maybe_confirm_swing()
        if self.min_offset is not None:
            pen = self.min_offset
        elif atr is not None:
            pen = self.min_atr_factor * atr
        else:
            return False

        fired = False
        extreme = None

        for price, sw_id in list(self._swings):
            if sw_id in self._used:
                continue
            if self.side == "high" and bar.high >= price + pen and bar.close < price:
                fired, extreme = True, bar.high
                self._used.add(sw_id)
                self._pending = [p for p in self._pending if p.swing_id != sw_id]
                break
            if self.side == "low" and bar.low <= price - pen and bar.close > price:
                fired, extreme = True, bar.low
                self._used.add(sw_id)
                self._pending = [p for p in self._pending if p.swing_id != sw_id]
                break

        if not fired:
            still: list[_PendingTag] = []
            for p in self._pending:
                p.bars_waited += 1
                closed_back = (bar.close < p.swing_price if self.side == "high"
                               else bar.close > p.swing_price)
                if closed_back:
                    fired, extreme = True, p.extreme
                    self._used.add(p.swing_id)
                elif p.bars_waited >= self.persistence_window:
                    self._used.add(p.swing_id)
                else:
                    still.append(p)
            self._pending = still

        if not fired:
            for price, sw_id in list(self._swings):
                if sw_id in self._used or any(p.swing_id == sw_id for p in self._pending):
                    continue
                if self.side == "high" and bar.high >= price + pen and bar.close >= price:
                    self._pending.append(_PendingTag(sw_id, price, 0, bar.high))
                elif self.side == "low" and bar.low <= price - pen and bar.close <= price:
                    self._pending.append(_PendingTag(sw_id, price, 0, bar.low))

        if fired:
            self.last_extreme = extreme
        return fired


class DisplacementLeaf(Node):
    """Impulsive bar: body >= min_atr x ATR, body/range >= min_body_ratio,
    body >= min_absolute_body. direction: up=bullish (close>open), down,
    either. Mirrors app/strategy/displacement.py's threshold logic, minus
    the FVG-inversion lookup (that's FvgLeaf — composed via `and`).

    One-bar confirmation lag, exactly like the original: "now" (the bar
    just fed) is bar3 of a trailing 3-bar window; the candidate impulsive
    bar is bar2 (one bar behind now) — you cannot act on a displacement
    bar until the bar after it has closed. ATR is updated with bar3
    (now) *before* bar2's body is measured against it, matching
    DisplacementDetector.on_bar's exact ordering (update ATR, then
    evaluate the bar behind)."""

    def __init__(self, direction: str, min_atr: Decimal = Decimal("1.0"),
                 atr_period: int = 14,
                 min_body_to_range_ratio: Decimal = Decimal("0.6"),
                 min_absolute_body: Decimal = Decimal("1.0")) -> None:
        super().__init__()
        self.direction = direction
        self.body_atr_multiple = min_atr
        self.min_body_to_range_ratio = min_body_to_range_ratio
        self.min_absolute_body = min_absolute_body
        self._atr = ATRTracker(atr_period)
        self._window: deque[Bar] = deque(maxlen=3)
        self.last_side: str | None = None
        self.last_displacement_bar: Bar | None = None
        self.last_prev_close: Decimal | None = None

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        atr = self._atr.on_bar(bar)
        self._window.append(bar)
        if len(self._window) < 3 or atr is None:
            return False
        b1, b2, _b3 = self._window
        body = abs(b2.close - b2.open)
        rng = b2.high - b2.low
        if body < self.min_absolute_body or rng == 0:
            return False
        if body / rng < self.min_body_to_range_ratio:
            return False
        if body < self.body_atr_multiple * atr:
            return False
        side = "up" if b2.close > b2.open else ("down" if b2.close < b2.open else None)
        if side is None:
            return False
        self.last_side = side
        self.last_displacement_bar = b2
        self.last_prev_close = b1.close
        return self.direction == "either" or self.direction == side

    @property
    def atr(self) -> Decimal | None:
        return self._atr.value


@dataclass(frozen=True)
class _Fvg:
    side: str  # "bullish" (gap up) or "bearish" (gap down)
    low: Decimal
    high: Decimal


class FvgLeaf(Node):
    """Fires when bar2 of the trailing 3-bar window [b1, b2, b3=now]
    inverts (closes through) a prior active 3-bar FVG of the opposite
    polarity — the same one-bar confirmation lag as DisplacementLeaf,
    and deliberately the same window shape so ANDing the two together on
    the same "now" bar recovers the original fused iFVG event exactly.
    Mitigation (wick-only fills) and new-FVG formation both use b3 (now)
    and b1, matching DisplacementDetector.on_bar's ordering: evaluate
    inversion first, then mitigate, then form the new gap from (b1, b3).
    direction=up means a bullish inversion (b2 closes back above a
    bearish/gap-down zone); down = bearish inversion."""

    def __init__(self, direction: str, max_active: int = 30) -> None:
        super().__init__()
        self.direction = direction
        self._window: deque[Bar] = deque(maxlen=3)
        self._active: deque[_Fvg] = deque(maxlen=max_active)
        self.last_zone: tuple[Decimal, Decimal] | None = None

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        self._window.append(bar)
        if len(self._window) < 3:
            return False
        b1, b2, b3 = self._window  # b3 is "now"; b2 is the displacement candidate

        inverted: _Fvg | None = None
        fired = False
        for fvg in reversed(self._active):
            if fvg.side == "bearish" and b2.close > fvg.high >= b1.close:
                inverted = fvg
                break
            if fvg.side == "bullish" and b2.close < fvg.low <= b1.close:
                inverted = fvg
                break
        if inverted is not None:
            want = "up" if inverted.side == "bearish" else "down"
            if self.direction in ("either", want):
                fired = True
                self.last_zone = (inverted.low, inverted.high)

        # mitigate wick-only touches (using b3/now), except the zone just inverted
        self._active = deque(
            (f for f in self._active
             if f is inverted or not (
                 (f.side == "bullish" and b3.low <= f.low and b3.close > f.low) or
                 (f.side == "bearish" and b3.high >= f.high and b3.close < f.high)
             )),
            maxlen=self._active.maxlen,
        )

        if b3.low > b1.high:
            self._active.append(_Fvg("bullish", b1.high, b3.low))
        elif b3.high < b1.low:
            self._active.append(_Fvg("bearish", b3.high, b1.low))

        return fired


class CrossOfLeaf(Node):
    """Price crosses a named level. direction=up: close crosses from at/
    below the level to above it; down: mirror; either: either direction.

    With `level_b` set, it is `level` that crosses `level_b` instead (e.g.
    sma_7 crossing sma_21): up = level goes from at/below level_b to above
    it. Neither series' value at bar t depends on any later bar."""

    def __init__(self, level: str, direction: str = "either", level_b: str | None = None) -> None:
        super().__init__()
        self.level = level
        self.level_b = level_b
        self.direction = direction
        self._prev_close: Decimal | None = None
        self._prev_diff: Decimal | None = None

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        if self.level_b is not None:
            a, b = ctx.levels.get(self.level), ctx.levels.get(self.level_b)
            prev_diff = self._prev_diff
            if a is None or b is None:
                self._prev_diff = None
                return False
            diff = a - b
            self._prev_diff = diff
            if prev_diff is None:
                return False
            crossed_up = prev_diff <= 0 < diff
            crossed_down = prev_diff >= 0 > diff
            if self.direction == "up":
                return crossed_up
            if self.direction == "down":
                return crossed_down
            return crossed_up or crossed_down

        lvl = ctx.levels.get(self.level)
        prev = self._prev_close
        self._prev_close = bar.close
        if lvl is None or prev is None:
            return False
        crossed_up = prev <= lvl < bar.close
        crossed_down = prev >= lvl > bar.close
        if self.direction == "up":
            return crossed_up
        if self.direction == "down":
            return crossed_down
        return crossed_up or crossed_down


class CloseBeyondLeaf(Node):
    """Close beyond a level by at least `min_atr` x ATR (0 = any amount)."""

    def __init__(self, level: str, direction: str = "either",
                 min_atr: Decimal = Decimal("0"), atr_period: int = 14) -> None:
        super().__init__()
        self.level = level
        self.direction = direction
        self.min_atr_factor = min_atr
        self._atr = ATRTracker(atr_period)

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        atr = self._atr.on_bar(bar)
        lvl = ctx.levels.get(self.level)
        if lvl is None:
            return False
        buf = self.min_atr_factor * atr if (self.min_atr_factor > 0 and atr is not None) else Decimal("0")
        above = bar.close > lvl + buf
        below = bar.close < lvl - buf
        if self.direction == "up":
            return above
        if self.direction == "down":
            return below
        return above or below


class RetraceToLeaf(Node):
    """Price returns to within `pct` (fraction of the distance travelled
    since the level) of a level, having previously closed beyond it."""

    def __init__(self, level: str, pct: Decimal = Decimal("0")) -> None:
        super().__init__()
        self.level = level
        self.pct = pct
        self._departed_above: Decimal | None = None
        self._departed_below: Decimal | None = None

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        lvl = ctx.levels.get(self.level)
        if lvl is None:
            return False
        fired = False
        tol = self.pct * abs(lvl) if self.pct > 0 else Decimal("0")
        if bar.close > lvl:
            self._departed_above = max(self._departed_above or lvl, bar.close)
        if bar.close < lvl:
            self._departed_below = min(self._departed_below or lvl, bar.close)
        if self._departed_above is not None and abs(bar.close - lvl) <= tol and bar.close <= self._departed_above:
            fired = True
            self._departed_above = None
        if self._departed_below is not None and abs(bar.close - lvl) <= tol and bar.close >= self._departed_below:
            fired = True
            self._departed_below = None
        return fired


class InsideRangeLeaf(Node):
    """Bar's full range [low, high] sits inside [level, level_b]."""

    def __init__(self, level: str, level_b: str) -> None:
        super().__init__()
        self.level = level
        self.level_b = level_b

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        a, b = ctx.levels.get(self.level), ctx.levels.get(self.level_b)
        if a is None or b is None:
            return False
        lo, hi = min(a, b), max(a, b)
        return lo <= bar.low and bar.high <= hi


class TimeWindowLeaf(Node):
    """bar.ts (ET) falls within [start, end)."""

    def __init__(self, start: str, end: str) -> None:
        super().__init__()
        sh, sm = start.split(":")
        eh, em = end.split(":")
        self._start = dtime(int(sh), int(sm))
        self._end = dtime(int(eh), int(em))

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        t = bar.ts.astimezone(ET).time()
        return self._start <= t < self._end


class ConsecutiveClosesLeaf(Node):
    """Last `n` closes all in the same direction (up or down)."""

    def __init__(self, n: int = 2, direction: str = "up") -> None:
        super().__init__()
        self.n = n
        self.direction = direction
        self._closes: deque[Decimal] = deque(maxlen=n + 1)

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        self._closes.append(bar.close)
        if len(self._closes) < self.n + 1:
            return False
        vals = list(self._closes)
        diffs = [b - a for a, b in zip(vals, vals[1:])]
        if self.direction == "up":
            return all(d > 0 for d in diffs)
        return all(d < 0 for d in diffs)


# ----------------------------------------------------------------------
# Non-recognizable leaves (exit/stop only)
# ----------------------------------------------------------------------

class BarsElapsedLeaf(Node):
    """Fires exactly `n` bars after the tree containing it was reset
    (typically: bars since entry — the engine resets exit trees on entry)."""

    def __init__(self, n: int) -> None:
        super().__init__()
        self.n = n
        self._count = 0

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        self._count += 1
        return self._count >= self.n

    def reset(self) -> None:
        super().reset()
        self._count = 0


class SessionEndLeaf(Node):
    """Fires on the last bar at/after session.end (ET)."""

    def __init__(self, session_end: dtime) -> None:
        super().__init__()
        self._end = session_end

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        return bar.ts.astimezone(ET).time() >= self._end


class AtrMultipleMoveLeaf(Node):
    """Fires when |close - reference| >= multiple x ATR. Reference is the
    first bar's close seen (e.g. entry bar, when used inside an exit tree
    reset on entry)."""

    def __init__(self, multiple: Decimal, lookback: int = 14) -> None:
        super().__init__()
        self.multiple = multiple
        self._atr = ATRTracker(lookback)
        self._ref: Decimal | None = None

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        atr = self._atr.on_bar(bar)
        if self._ref is None:
            self._ref = bar.close
            return False
        if atr is None:
            return False
        return abs(bar.close - self._ref) >= self.multiple * atr

    def reset(self) -> None:
        super().reset()
        self._ref = None


class TrailingStopLeaf(Node):
    """Fires when price retraces `multiple` x ATR from the best close seen
    since this leaf was last reset (e.g. since entry)."""

    def __init__(self, multiple: Decimal, lookback: int = 14) -> None:
        super().__init__()
        self.multiple = multiple
        self._atr = ATRTracker(lookback)
        self._best: Decimal | None = None
        self._first: Decimal | None = None

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        atr = self._atr.on_bar(bar)
        if self._first is None:
            self._first = bar.close
            self._best = bar.close
            return False
        # Direction inferred from drift since the reference bar.
        long_bias = bar.close >= self._first
        if long_bias:
            self._best = max(self._best, bar.close)
            retrace = self._best - bar.close
        else:
            self._best = min(self._best, bar.close)
            retrace = bar.close - self._best
        if atr is None:
            return False
        return retrace >= self.multiple * atr

    def reset(self) -> None:
        super().reset()
        self._best = None
        self._first = None


class RollingQuantileLeaf(Node):
    """Fires when bar.close's rank in the trailing `lookback` closes is at
    or above (quantile) / below (1-quantile handled by direction) the given
    quantile. direction reuses "up" (>= quantile) / "down" (<= 1-quantile)."""

    def __init__(self, quantile: Decimal, lookback: int = 20, direction: str = "up") -> None:
        super().__init__()
        self.quantile = quantile
        self.direction = direction
        self._window: deque[Decimal] = deque(maxlen=lookback)

    def _evaluate(self, bar: Bar, ctx: EvalCtx) -> bool:
        self._window.append(bar.close)
        if len(self._window) < self._window.maxlen:
            return False
        sorted_vals = sorted(self._window)
        rank = sorted_vals.index(bar.close) / (len(sorted_vals) - 1)
        if self.direction == "down":
            return Decimal(str(rank)) <= (Decimal("1") - self.quantile)
        return Decimal(str(rank)) >= self.quantile


# ----------------------------------------------------------------------
# Compiler
# ----------------------------------------------------------------------

_DEC_FIELDS = ("min_atr", "pct", "multiple", "quantile")


def _dec(node: dict, key: str, default: Decimal | None = None) -> Decimal | None:
    if key not in node:
        return default
    return Decimal(str(node[key]))


def compile_predicate(node: dict, session_end: dtime = dtime(16, 0)) -> Node:
    """Compile a JSON predicate node (already schema-valid) into a Node
    tree. `session_end` parameterizes the `session_end` op (schema gives it
    no field of its own — it means the strategy's own `session.end`).
    Raises LevelNotResolvable eagerly is NOT possible here (levels are
    only known at bar time) — that error surfaces the first time on_bar
    runs against an unresolvable level."""
    op = node["op"]
    if op in ("and", "or"):
        children = [compile_predicate(o, session_end) for o in node["operands"]]
        return AndNode(children) if op == "and" else OrNode(children)

    direction = node.get("direction", "either")
    within_bars = node.get("within_bars", 1)

    if op == "sweep_of":
        if "min_offset" in node:
            leaf = SweepOfLeaf(
                level=node["level"], n=node.get("n", 2),
                min_offset=_dec(node, "min_offset"),
                within_bars=within_bars,
            )
        else:
            leaf = SweepOfLeaf(
                level=node["level"], n=node.get("n", 2),
                min_atr=_dec(node, "min_atr", Decimal("0.05")),
                within_bars=within_bars,
            )
    elif op == "displacement":
        leaf = DisplacementLeaf(direction=direction, min_atr=_dec(node, "min_atr", Decimal("1.0")))
    elif op == "fvg":
        leaf = FvgLeaf(direction=direction)
    elif op == "cross_of":
        leaf = CrossOfLeaf(level=node["level"], direction=direction, level_b=node.get("level_b"))
    elif op == "close_beyond":
        leaf = CloseBeyondLeaf(level=node["level"], direction=direction,
                                min_atr=_dec(node, "min_atr", Decimal("0")))
    elif op == "retrace_to":
        leaf = RetraceToLeaf(level=node["level"], pct=_dec(node, "pct", Decimal("0")))
    elif op == "inside_range":
        leaf = InsideRangeLeaf(level=node["level"], level_b=node["level_b"])
    elif op == "time_window":
        leaf = TimeWindowLeaf(start=node["start"], end=node["end"])
    elif op == "consecutive_closes":
        leaf = ConsecutiveClosesLeaf(n=node.get("n", 2),
                                      direction="up" if direction != "down" else "down")
    elif op == "bars_elapsed":
        leaf = BarsElapsedLeaf(n=node["n"])
    elif op == "session_end":
        leaf = SessionEndLeaf(session_end=session_end)
    elif op == "atr_multiple_move":
        leaf = AtrMultipleMoveLeaf(multiple=_dec(node, "multiple", Decimal("1.0")),
                                    lookback=node.get("lookback", 14))
    elif op == "trailing_stop":
        leaf = TrailingStopLeaf(multiple=_dec(node, "multiple", Decimal("1.0")),
                                 lookback=node.get("lookback", 14))
    elif op == "rolling_quantile":
        leaf = RollingQuantileLeaf(quantile=_dec(node, "quantile", Decimal("0.9")),
                                    lookback=node.get("lookback", 20), direction=direction)
    else:
        raise ValueError(f"no evaluator for op {op!r}")

    leaf.persistence_window = within_bars
    return leaf
