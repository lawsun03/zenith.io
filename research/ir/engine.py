"""Strategy IR execution engine.

Walks a single-instrument bar stream, evaluates the IR document's entry/
exit/stop/target, and emits a Trade sequence. Decimal for all money math
(CLAUDE.md rule "Decimal for all money math, never float").

Fill model mirrors app/sim/paper.py's PaperBroker exactly (same bracket
semantics, same pessimistic stop/target tie-break) so a bit-identical
comparison against the hand-coded strategies is apples-to-apples:
  - entry: market fill, slipped `slippage_ticks` against the trader
  - each subsequent bar: stop/target checked via high/low touch; both hit
    the same bar (whipsaw) -> pessimistic default fills the stop
  - exits fill at the exact stop/target price, no additional slippage
  - commission subtracted per side

This module does NOT decide position sizing (see sizing.py) and does NOT
apply any gate — it is exactly the composable "evaluate entry/exit/stop/
target, emit a trade sequence" the task scopes it as.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
from decimal import Decimal
from typing import Callable, Iterable, Iterator

from app.sim.events import Bar

from .predicates import (
    AndNode, ATRTracker, EvalCtx, FvgLeaf, LevelTracker, Node, OrNode,
    SweepOfLeaf, compile_predicate,
)


@dataclass(frozen=True)
class Trade:
    instrument: str
    side: str                 # "long" | "short"
    entry_ts: datetime
    entry_price: Decimal      # post-slippage fill price
    stop_price: Decimal
    target_price: Decimal
    exit_ts: datetime
    exit_price: Decimal
    exit_reason: str          # "stop" | "target" | "stop (whipsaw)" | "session_end" | "exit_rule"
    pnl_points: Decimal       # signed, (exit-entry) for long, (entry-exit) for short
    commission_points_equivalent: Decimal = Decimal("0")


@dataclass
class _OpenPosition:
    side: str
    entry_ts: datetime
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal


@dataclass(frozen=True)
class DecisionPoint:
    """One in-session bar evaluated while flat — the moment a trainee
    (research/trainer/) would face a decision. `fired` means a tradeable
    setup resolved (side and stop both computable, matching exactly what
    _on_bar would open a position on). `near_miss` means an `and` branch
    armed — some but not all operands satisfied — without confirming;
    see _has_near_miss. Bars outside the session, or while a position is
    open, never produce a DecisionPoint: the engine isn't watching for a
    new entry there, so there is nothing to grade a trainee on."""

    ts: datetime
    fired: bool
    near_miss: bool
    side: str | None
    entry_price: Decimal | None
    stop_price: Decimal | None
    target_price: Decimal | None


def _has_near_miss(node: Node) -> bool:
    """True if any `and` branch in the tree has some, but not all, of its
    operands currently satisfied — armed but not (yet) confirmed. Walks
    generically via the public Node.satisfied()/.children rather than any
    per-leaf knowledge, so it stays correct as predicates.py grows ops."""
    children = getattr(node, "children", None)
    if not children:
        return False
    if isinstance(node, AndNode):
        satisfied = sum(1 for c in children if c.satisfied())
        if 0 < satisfied < len(children):
            return True
    return any(_has_near_miss(c) for c in children)


def _tz_time(ts: datetime, tz) -> dtime:
    return ts.astimezone(tz).time()


def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def _flatten_leaves(node: Node) -> list[Node]:
    out: list[Node] = []
    children = getattr(node, "children", None)
    if children:
        for c in children:
            out.extend(_flatten_leaves(c))
    else:
        out.append(node)
    return out


def _infer_side(leaves: list[Node]) -> str | None:
    for leaf in leaves:
        direction = getattr(leaf, "direction", None)
        if direction == "up":
            return "long"
        if direction == "down":
            return "short"
    return None


def _fired_branch_leaves(tree: Node) -> list[Node]:
    """Leaves of the specific branch that fired this bar. For a bare `or`
    of `and`s (our two example strategies), this is the fired child's own
    leaves — including ones satisfied from a few bars ago (e.g. the
    sweep_of that armed the AND), not just leaves with fired_last=True on
    this exact bar. Falls back to every leaf when the tree isn't an
    `or` (a bare `and`, or a single leaf)."""
    if isinstance(tree, OrNode) and tree.last_fired_child is not None:
        return _flatten_leaves(tree.last_fired_child)
    return _flatten_leaves(tree)


class IRBacktestEngine:
    """One instance per (strategy, instrument) backtest run."""

    def __init__(self, ir_doc: dict, instrument: str, *,
                 tick_size: Decimal = Decimal("0.01"),
                 slippage_ticks: int = 0,
                 commission_per_side: Decimal = Decimal("0"),
                 on_decision_point: Callable[[DecisionPoint], None] | None = None) -> None:
        self.doc = ir_doc
        self.instrument = instrument
        self.tick_size = tick_size
        self.slippage_ticks = slippage_ticks
        self.commission_per_side = commission_per_side
        self.on_decision_point = on_decision_point

        from zoneinfo import ZoneInfo
        self.tz = ZoneInfo(ir_doc["session"]["tz"])
        self.session_start = _parse_hhmm(ir_doc["session"]["start"])
        self.session_end = _parse_hhmm(ir_doc["session"]["end"])

        self.entry_tree = compile_predicate(ir_doc["entry"], self.session_end)
        self.exit_tree = compile_predicate(ir_doc["exit"], self.session_end) if ir_doc.get("exit") else None

        self.levels = LevelTracker(self.session_start)
        self.ctx = EvalCtx(levels=self.levels)

        stop_cfg = ir_doc["stop"]
        self.stop_type = stop_cfg["type"]
        self.stop_multiple = Decimal(str(stop_cfg["multiple"]))
        self.stop_lookback = stop_cfg["lookback"]
        self.stop_anchor = stop_cfg.get("anchor", "sweep_extreme")
        self.stop_buffer = Decimal(str(stop_cfg.get("buffer", 0)))

        target_cfg = ir_doc.get("target")
        self.target_type = target_cfg["type"] if target_cfg else "r_multiple"
        self.target_multiple = Decimal(str(target_cfg["multiple"])) if target_cfg else Decimal("2.0")

        self._atr = ATRTracker(self.stop_lookback)
        self._position: _OpenPosition | None = None
        self.trades: list[Trade] = []

    # ------------------------------------------------------------------

    def run(self, bars: Iterable[Bar]) -> list[Trade]:
        for bar in bars:
            self._on_bar(bar)
        return self.trades

    def _on_bar(self, bar: Bar) -> None:
        self.levels.on_bar(bar)
        atr = self._atr.on_bar(bar)

        if self._position is not None:
            self._check_exit(bar)
            return

        entry_fired = self.entry_tree.on_bar(bar, self.ctx)
        et = _tz_time(bar.ts, self.tz)
        in_session = self.session_start <= et < self.session_end

        # Resolve side/entry/stop/target once, whether or not a trade ends
        # up opening — the decision-point hook below and the trade-opening
        # code afterward must see the exact same values, never two
        # independently-derived computations that could drift apart.
        side = entry_price = stop_price = target_price = None
        if entry_fired and in_session:
            branch_leaves = _fired_branch_leaves(self.entry_tree)
            side = _infer_side(branch_leaves)
            if side is not None:
                entry_price = self._entry_price(bar, side, branch_leaves)
                stop_price = self._stop_price(bar, side, entry_price, branch_leaves, atr)
                if stop_price is not None:
                    r = abs(entry_price - stop_price)
                    if r > 0:
                        target_price = self._target_price(entry_price, stop_price, side, r, atr)
                    else:
                        stop_price = None  # degenerate zero-risk stop — not tradeable

        resolved = stop_price is not None and target_price is not None

        if self.on_decision_point is not None and in_session:
            self.on_decision_point(DecisionPoint(
                ts=bar.ts,
                fired=resolved,
                near_miss=(not resolved) and not entry_fired and _has_near_miss(self.entry_tree),
                side=side if resolved else None,
                entry_price=entry_price if resolved else None,
                stop_price=stop_price if resolved else None,
                target_price=target_price if resolved else None,
            ))

        if not (entry_fired and in_session and resolved):
            return

        slip = self.tick_size * self.slippage_ticks
        filled_entry = entry_price + slip if side == "long" else entry_price - slip
        stop_price = stop_price + (filled_entry - entry_price)
        target_price = target_price + (filled_entry - entry_price)

        self._position = _OpenPosition(side, bar.ts, filled_entry, stop_price, target_price)
        if self.exit_tree is not None:
            self.exit_tree.reset()

    def _entry_price(self, bar: Bar, side: str, fired_now: list[Node]) -> Decimal:
        for leaf in fired_now:
            if isinstance(leaf, FvgLeaf) and leaf.last_zone is not None:
                lo, hi = leaf.last_zone
                return hi if side == "long" else lo
        return bar.close

    def _stop_price(self, bar: Bar, side: str, entry: Decimal,
                     fired_now: list[Node], atr: Decimal | None) -> Decimal | None:
        if self.stop_type in ("atr", "realised_sigma"):
            if atr is None:
                return None
            return entry - self.stop_multiple * atr if side == "long" else entry + self.stop_multiple * atr

        # structural
        anchor: Decimal | None = None
        if self.stop_anchor == "entry_bar":
            anchor = bar.low if side == "long" else bar.high
        elif self.stop_anchor == "sweep_extreme":
            for leaf in fired_now:
                if isinstance(leaf, SweepOfLeaf) and leaf.last_extreme is not None:
                    anchor = leaf.last_extreme
                    break
        if anchor is None:
            return None
        # Buffer widens the stop beyond the anchor (matches
        # app/strategy/composer.py: stop = stop_anchor -/+ buf) — a fixed
        # price-unit offset, pooled as the same constant across instruments.
        return anchor - self.stop_buffer if side == "long" else anchor + self.stop_buffer

    def _target_price(self, entry: Decimal, stop: Decimal, side: str,
                       r: Decimal, atr: Decimal | None) -> Decimal:
        if self.target_type == "atr" and atr is not None:
            dist = self.target_multiple * atr
        else:
            dist = self.target_multiple * r
        return entry + dist if side == "long" else entry - dist

    def _check_exit(self, bar: Bar) -> None:
        pos = self._position
        assert pos is not None
        stop_hit = ((pos.side == "long" and bar.low <= pos.stop_price) or
                    (pos.side == "short" and bar.high >= pos.stop_price))
        target_hit = ((pos.side == "long" and bar.high >= pos.target_price) or
                      (pos.side == "short" and bar.low <= pos.target_price))

        exit_price = exit_reason = None
        if stop_hit and target_hit:
            exit_price, exit_reason = pos.stop_price, "stop (whipsaw)"
        elif stop_hit:
            exit_price, exit_reason = pos.stop_price, "stop"
        elif target_hit:
            exit_price, exit_reason = pos.target_price, "target"

        if exit_price is None and self.exit_tree is not None:
            if self.exit_tree.on_bar(bar, self.ctx):
                exit_price, exit_reason = bar.close, "exit_rule"

        if exit_price is None:
            return

        pnl = ((exit_price - pos.entry_price) if pos.side == "long"
               else (pos.entry_price - exit_price))
        self.trades.append(Trade(
            instrument=self.instrument, side=pos.side,
            entry_ts=pos.entry_ts, entry_price=pos.entry_price,
            stop_price=pos.stop_price, target_price=pos.target_price,
            exit_ts=bar.ts, exit_price=exit_price, exit_reason=exit_reason,
            pnl_points=pnl, commission_points_equivalent=self.commission_per_side * 2,
        ))
        self._position = None


def run_backtest(ir_doc: dict, instrument: str, bars: Iterable[Bar], **kwargs) -> list[Trade]:
    return IRBacktestEngine(ir_doc, instrument, **kwargs).run(bars)
