"""
PaperBroker — in-memory simulation, same Broker protocol.

What it's for:
  - Unit tests (no SDK, no network)
  - Dry-run mode against a recorded bar stream
  - Smoke-testing the engine + risk wiring before live demo

What it models:
  - Market-order slippage on entries (configurable ticks, default 1)
  - Adverse slippage on stop-loss exits
  - Commission per side per contract (configurable, defaults from DEFAULT_COMMISSION table)

What it does NOT model:
  - Partial fills or order book depth
  - Latency → fills are near-instantaneous when triggered
  - Limit order slippage (limit targets fill at exact price)

Behavior:
  - place_bracket() opens a tracked bracket position immediately at
    the entry price. We don't simulate fill-on-touch for the entry —
    we assume it was a marketable order. The strategy can place market
    or limit; either way the paper broker fills at `entry`.
  - On every Bar fed in via inject_bar(), we check open brackets:
    * If high >= target (long) or low <= target (short) → take-profit fill.
    * If low <= stop (long) or high >= stop (short) → stop-loss fill.
    * If both hit on the same bar (whipsaw), we conservatively fill the
      stop first. This is the pessimistic default; configurable via
      `pessimistic_whipsaw=False` if you want optimistic.
  - Equity snapshots emit on every bar feed, mirroring TopstepXBroker.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from .events import Bar, BracketResult, BrokerPosition, ExitCoverage, Fill, MarkToMarket, Side
from .protocol import BarHandler, EquityHandler, FillHandler

log = logging.getLogger(__name__)


@dataclass
class _OpenBracket:
    """Internal record of a live bracket position."""

    order_id: str
    instrument: str
    side: Side
    size: int
    entry: Decimal
    stop: Decimal
    target: Decimal
    # Partial profit state (all 0/None/False = disabled)
    partial_target: Decimal | None = None  # price to take partial profit
    partial_size: int = 0                  # contracts to exit at partial_target
    partial_filled: bool = False           # True once the partial fill has been emitted
    entry_time: float = 0.0               # Unix timestamp of fill, for chart marker


# Per-instrument tick value. Verified against CME contract specs:
#   /MGC (micro gold):     tick = $0.10, tick value = $1   → $10/point
#   /MNQ (micro Nasdaq):   tick = 0.25,  tick value = $0.50 → $2/point
#   /MCL (micro crude):    tick = $0.01, tick value = $1   → $100/point
#   /MBT (micro Bitcoin):  tick = $5,    tick value = $0.10
#   /GC  (full gold):      tick = $0.10, tick value = $10  → $100/point
TICK_VALUE = {
    "MGC": Decimal("1"),     # micro gold
    "MNQ": Decimal("0.5"),   # micro Nasdaq
    "MES": Decimal("1.25"),  # micro S&P 500
    "MCL": Decimal("1"),     # micro crude
    "MBT": Decimal("0.10"),  # micro Bitcoin
    "GC":  Decimal("10"),    # full gold contract
}

# Tick size per instrument (price units). Used for slippage calculation.
TICK_SIZE = {
    "MGC": Decimal("0.10"),
    "MNQ": Decimal("0.25"),
    "MES": Decimal("0.25"),
    "MCL": Decimal("0.01"),
    "GC":  Decimal("0.10"),
    "MBT": Decimal("5"),
}

# Default commission per side per contract (round-trip = 2×).
DEFAULT_COMMISSION = {
    "MGC": Decimal("0.74"),
    "MNQ": Decimal("0.57"),
    "MES": Decimal("0.57"),
}


def _tick_value(instrument: str) -> Decimal:
    """Look up tick value, defaulting to $1 with a warning."""
    if instrument not in TICK_VALUE:
        log.warning(
            "No tick value for %s; defaulting to $1. Add to TICK_VALUE.",
            instrument,
        )
    return TICK_VALUE.get(instrument, Decimal("1"))


class PaperBroker:
    """In-memory broker. Same Protocol as TopstepXBroker."""

    def __init__(
        self,
        starting_balance: Decimal = Decimal("50000"),
        pessimistic_whipsaw: bool = True,
        slippage_ticks_market: int = 1,
        commission_per_side: Decimal | None = None,  # None = use DEFAULT_COMMISSION table
        partial_profit_r: Decimal = Decimal("0"),    # 0 = disabled; 1.0 = take half at 1R
    ) -> None:
        self._starting_balance = starting_balance
        self._balance = starting_balance
        self._pessimistic = pessimistic_whipsaw
        self._slippage_ticks_market = slippage_ticks_market
        self._commission_per_side = commission_per_side
        self._partial_profit_r = partial_profit_r
        self._connected = False

        self._open: dict[str, _OpenBracket] = {}  # order_id → bracket
        self._next_order_id = 1
        self._last_bar_close: dict[str, Decimal] = {}
        self._current_bar_ts: datetime | None = None  # set in inject_bar; used by place_bracket

        self._bar_handlers: list[BarHandler] = []
        self._fill_handlers: list[FillHandler] = []
        self._equity_handlers: list[EquityHandler] = []

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def feed_is_healthy(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Account
    # ------------------------------------------------------------------

    async def account_balance(self) -> Decimal:
        return self._balance

    async def get_positions(self) -> list[BrokerPosition]:
        return [
            BrokerPosition(
                instrument=b.instrument,
                side=b.side,
                size=b.size,
                average_price=b.entry,
                unrealized_pnl=self._unrealized_for(b),
            )
            for b in self._open.values()
        ]

    async def exit_coverage(self, instrument: str) -> ExitCoverage:
        """Paper positions carry simulated brackets — always fully covered."""
        positions = await self.get_positions()
        pos = next((p for p in positions if p.instrument == instrument), None)
        if pos is None or pos.size == 0:
            return ExitCoverage(
                instrument=instrument, position_size=0, side="",
                avg_price=Decimal("0"), covered_stop=0, covered_target=0,
            )
        size = int(pos.size)
        return ExitCoverage(
            instrument=instrument, position_size=size, side=pos.side,
            avg_price=pos.average_price, covered_stop=size, covered_target=size,
        )

    async def place_protective_stop(
        self, instrument: str, size: int, price: Decimal
    ) -> bool:
        return True

    async def place_protective_target(
        self, instrument: str, size: int, price: Decimal
    ) -> bool:
        return True

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    async def place_bracket(
        self,
        instrument: str,
        side: Side,
        size: int,
        entry: Decimal,
        stop: Decimal,
        target: Decimal,
        tp1_price: "Decimal | None" = None,
        tp1_fraction: Decimal = Decimal("0.5"),
        be_after_tp1: bool = True,
    ) -> BracketResult:
        if not self._connected:
            return BracketResult(False, None, None, None, error="not connected")

        order_id = f"PAPER-{self._next_order_id}"
        self._next_order_id += 1

        # Market-order semantics: fill at the current market (last bar close),
        # NOT at the signal's entry price — stale iFVG signals carry entries far
        # off-market and used to "fill" there, then instantly "win" (2026-06-10
        # parity post-mortem). Falls back to `entry` before any bar is seen.
        market = self._last_bar_close.get(instrument, entry)

        # Apply market-order slippage: shift fill price against the trader.
        tick = TICK_SIZE.get(instrument, Decimal("0.10"))
        slip = tick * self._slippage_ticks_market
        slipped_entry = market + slip if side == "long" else market - slip

        # Re-anchor stop/target as signal-relative offsets from the actual fill,
        # matching the live broker's _place_bracket_after_fill (fill + offset).
        stop = slipped_entry + (stop - entry)
        target = slipped_entry + (target - entry)

        commission = self._commission_for(instrument)

        bracket = _OpenBracket(
            order_id=order_id,
            instrument=instrument,
            side=side,
            size=size,
            entry=slipped_entry,  # record slipped price as the true entry for P&L
            stop=stop,
            target=target,
        )

        if self._partial_profit_r > 0 and size >= 2:
            r = abs(slipped_entry - stop)
            if side == "long":
                pt = slipped_entry + r * self._partial_profit_r
            else:
                pt = slipped_entry - r * self._partial_profit_r
            bracket.partial_target = pt
            bracket.partial_size = size // 2

        fill_ts = (self._current_bar_ts or datetime.now(timezone.utc)).replace(microsecond=0)
        bracket.entry_time = fill_ts.timestamp()
        self._open[order_id] = bracket

        await self._fanout(
            self._fill_handlers,
            Fill(
                ts=fill_ts,
                instrument=instrument,
                side=side,
                fill_price=slipped_entry,
                size=size,
                is_entry=True,
                realized_pnl_delta=-commission * size,  # cost of entry fill
                contracts_delta=size if side == "long" else -size,
                broker_order_id=order_id,
            ),
        )
        self._balance -= commission * size  # deduct entry commission from balance

        return BracketResult(
            success=True,
            entry_order_id=order_id,
            stop_order_id=f"{order_id}-S",
            target_order_id=f"{order_id}-T",
        )

    def open_brackets(self) -> list[dict]:
        """Return live open positions with entry/stop/target for the dashboard."""
        result = []
        for b in self._open.values():
            result.append({
                "instrument": b.instrument,
                "side": b.side,
                "size": b.size,
                "entry": str(b.entry),
                "stop": str(b.stop),
                "target": str(b.target),
                "partial": str(b.partial_target) if b.partial_target else None,
                "entry_time": b.entry_time or None,
            })
        return result

    async def flatten(self, instrument: str) -> bool:
        """Close all open brackets in `instrument` at last bar close."""
        if not self._connected:
            return False
        last = self._last_bar_close.get(instrument)
        if last is None:
            log.warning("flatten(%s) before any bars seen; skipped", instrument)
            return False

        # Use the bar timestamp so fills are stamped at bar time, not wall clock.
        # Without this, a backtest flatten fill could land on a different
        # calendar day than the bar that triggered it, corrupting daily P&L.
        ts = (self._current_bar_ts or datetime.now(timezone.utc)).replace(microsecond=0)
        to_close = [b for b in self._open.values() if b.instrument == instrument]
        for b in to_close:
            await self._close_bracket(b, last, reason="flatten", ts=ts, is_stop=True)
        return True

    async def cancel_all(self, instrument: str | None = None) -> int:
        """In paper, cancel = drop the bracket without filling. No fees."""
        ids = list(self._open.keys())
        if instrument:
            ids = [i for i in ids if self._open[i].instrument == instrument]
        for oid in ids:
            del self._open[oid]
        return len(ids)

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def on_bar(self, handler: BarHandler) -> None:
        self._bar_handlers.append(handler)

    def on_fill(self, handler: FillHandler) -> None:
        self._fill_handlers.append(handler)

    def on_equity(self, handler: EquityHandler) -> None:
        self._equity_handlers.append(handler)

    async def subscribe(
        self,
        instruments: Iterable[str],
        timeframes: Iterable[str],
    ) -> None:
        # No-op in paper. Bars come in via inject_bar().
        return

    # ------------------------------------------------------------------
    # Test/simulation API — what makes this a paper broker, not live.
    # ------------------------------------------------------------------

    def reset(self) -> None:
        """Reset broker state for a new backtest run."""
        self._balance = self._starting_balance
        self._open.clear()
        self._next_order_id = 1
        self._last_bar_close.clear()
        self._current_bar_ts = None

    async def inject_bar(self, bar: Bar) -> None:
        """
        Feed a bar into the simulation. Resolves open brackets if their
        stop/target was touched, then fans the bar out to handlers.
        """
        self._current_bar_ts = bar.ts
        self._last_bar_close[bar.instrument] = bar.close

        # Snapshot keys — modifying the dict while iterating would be a bug.
        for oid in list(self._open.keys()):
            bracket = self._open.get(oid)
            if bracket is None or bracket.instrument != bar.instrument:
                continue

            # Partial profit: if partial_target touched and not yet filled,
            # close partial_size contracts and move the stop to break-even.
            if (
                not bracket.partial_filled
                and bracket.partial_target is not None
                and bracket.partial_size > 0
            ):
                partial_hit = (
                    (bracket.side == "long" and bar.high >= bracket.partial_target)
                    or (bracket.side == "short" and bar.low <= bracket.partial_target)
                )
                if partial_hit:
                    await self._close_partial(bracket, bracket.partial_target, bar.ts)
                    bracket.size -= bracket.partial_size
                    bracket.stop = bracket.entry  # move stop to break-even
                    bracket.partial_filled = True

            # Check stop and target. Both could be hit in the same bar
            # (whipsaw); pessimistic default fills the stop.
            stop_hit = (
                (bracket.side == "long" and bar.low <= bracket.stop)
                or (bracket.side == "short" and bar.high >= bracket.stop)
            )
            target_hit = (
                (bracket.side == "long" and bar.high >= bracket.target)
                or (bracket.side == "short" and bar.low <= bracket.target)
            )

            if stop_hit and target_hit:
                exit_price = bracket.stop if self._pessimistic else bracket.target
                reason = "stop (whipsaw)" if self._pessimistic else "target (whipsaw)"
            elif stop_hit:
                exit_price = bracket.stop
                reason = "stop"
            elif target_hit:
                exit_price = bracket.target
                reason = "target"
            else:
                continue

            is_stop_exit = "stop" in reason  # covers "stop" and "stop (whipsaw)"
            await self._close_bracket(bracket, exit_price, reason=reason, ts=bar.ts, is_stop=is_stop_exit)

        # Bar fans out AFTER fills resolve, so strategy sees fresh
        # post-fill state when it gets the bar.
        await self._fanout(self._bar_handlers, bar)

        # Equity tick after each bar, like the live broker.
        await self._emit_equity_snapshot(bar.ts)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _close_partial(self, bracket: _OpenBracket, exit_price: Decimal, ts: datetime) -> None:
        """Emit a partial fill for bracket.partial_size contracts. Does NOT remove bracket."""
        size = bracket.partial_size
        ticks_per_point = self._ticks_per_point(bracket.instrument)
        points = (exit_price - bracket.entry) if bracket.side == "long" else (bracket.entry - exit_price)
        pnl = points * ticks_per_point * _tick_value(bracket.instrument) * size
        commission = self._commission_for(bracket.instrument)
        pnl -= commission * size
        self._balance += pnl

        await self._fanout(
            self._fill_handlers,
            Fill(
                ts=ts,
                instrument=bracket.instrument,
                side="short" if bracket.side == "long" else "long",
                fill_price=exit_price,
                size=size,
                is_entry=False,
                realized_pnl_delta=pnl,
                contracts_delta=-size if bracket.side == "long" else size,
                broker_order_id=f"{bracket.order_id}-P",
                is_stop=False,
            ),
        )
        log.info(
            "Partial fill: %s %d @ %s P&L=%s; stop moved to BE=%s",
            bracket.instrument, size, exit_price, pnl, bracket.entry,
        )

    async def _close_bracket(
        self,
        bracket: _OpenBracket,
        exit_price: Decimal,
        reason: str,
        ts: datetime | None = None,
        is_stop: bool = False,
    ) -> None:
        """Close a bracket, realize P&L, emit a fill, drop from open set."""
        ts = ts or datetime.now(timezone.utc).replace(microsecond=0)
        if is_stop:
            tick = TICK_SIZE.get(bracket.instrument, Decimal("0.10"))
            slip = tick * self._slippage_ticks_market
            # Long stop: price slips DOWN (worse). Short stop: slips UP (worse).
            exit_price = exit_price - slip if bracket.side == "long" else exit_price + slip
        ticks_per_point = self._ticks_per_point(bracket.instrument)
        if bracket.side == "long":
            points = exit_price - bracket.entry
        else:
            points = bracket.entry - exit_price

        # P&L = points × ticks_per_point × tick_value × size
        pnl = points * ticks_per_point * _tick_value(bracket.instrument) * bracket.size
        commission = self._commission_for(bracket.instrument)
        pnl -= commission * bracket.size
        self._balance += pnl

        del self._open[bracket.order_id]

        await self._fanout(
            self._fill_handlers,
            Fill(
                ts=ts,
                instrument=bracket.instrument,
                side="short" if bracket.side == "long" else "long",
                fill_price=exit_price,
                size=bracket.size,
                is_entry=False,
                realized_pnl_delta=pnl,
                contracts_delta=-bracket.size if bracket.side == "long" else bracket.size,
                broker_order_id=f"{bracket.order_id}-X",
                is_stop=is_stop,
            ),
        )
        log.info(
            "Bracket %s closed at %s (%s); P&L=%s",
            bracket.order_id, exit_price, reason, pnl,
        )

    def _commission_for(self, instrument: str) -> Decimal:
        """Look up commission per side per contract."""
        if self._commission_per_side is not None:
            return self._commission_per_side
        return DEFAULT_COMMISSION.get(instrument, Decimal("0.74"))

    @staticmethod
    def _ticks_per_point(instrument: str) -> Decimal:
        """How many ticks make up one full point of price movement."""
        # MGC: tick = 0.10, so 10 ticks/point. MNQ: tick = 0.25, 4 ticks/point.
        # Approximation suitable for paper P&L; refine per instrument as needed.
        return {
            "MGC": Decimal("10"),
            "MNQ": Decimal("4"),
            "MES": Decimal("4"),    # tick = 0.25
            "MCL": Decimal("100"),  # tick = 0.01
            "MBT": Decimal("20"),   # tick = 5 on a $100k+ contract
        }.get(instrument, Decimal("1"))

    def _unrealized_for(self, b: _OpenBracket) -> Decimal:
        last = self._last_bar_close.get(b.instrument)
        if last is None:
            return Decimal("0")
        ticks_per_point = self._ticks_per_point(b.instrument)
        if b.side == "long":
            points = last - b.entry
        else:
            points = b.entry - last
        return points * ticks_per_point * _tick_value(b.instrument) * b.size

    async def _emit_equity_snapshot(self, ts: datetime) -> None:
        unrealized = sum(
            (self._unrealized_for(b) for b in self._open.values()),
            Decimal("0"),
        )
        await self._fanout(
            self._equity_handlers,
            MarkToMarket(ts=ts, equity=self._balance + unrealized),
        )

    @staticmethod
    async def _fanout(handlers, event) -> None:
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in handlers),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                log.exception("Handler raised: %s", r)
