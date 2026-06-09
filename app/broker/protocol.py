"""
Broker protocol — what the rest of the bot expects from "the broker".

Two implementations:
  - TopstepXBroker: wraps project_x_py.TradingSuite (live and demo)
  - PaperBroker: in-memory simulation for backtests and unit tests

The execution engine talks to this Protocol and never imports the SDK.
That's the point: you can swap the implementation without touching
strategy or risk code.

Async by default — every broker call is awaited. The SDK is async,
the WebSocket feed is async, FastAPI is async, so there's no point
mixing sync code in.
"""

from __future__ import annotations

from decimal import Decimal
from typing import (
    Awaitable,
    Callable,
    Iterable,
    Protocol,
    runtime_checkable,
)

from .events import Bar, BracketResult, BrokerPosition, ExitCoverage, Fill, MarkToMarket, Side


# Callback type aliases. Keep these explicit — the strategy and engine
# wire themselves up by registering these handlers on the broker.
BarHandler = Callable[[Bar], Awaitable[None]]
FillHandler = Callable[[Fill], Awaitable[None]]
EquityHandler = Callable[[MarkToMarket], Awaitable[None]]


@runtime_checkable
class Broker(Protocol):
    """Anything the bot calls on the broker. Nothing else."""

    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    async def account_balance(self) -> Decimal:
        """Realized account balance — what the broker says we have."""
        ...

    async def get_positions(self) -> list[BrokerPosition]:
        """Snapshot of all open positions. Used by the reconciler."""
        ...

    async def exit_coverage(self, instrument: str) -> ExitCoverage:
        """Working-order coverage for the open position in `instrument`.

        Queries the exchange for live stop/target orders on the closing side.
        Used by the reconciler to detect positions with no protective exit.
        """
        ...

    async def place_protective_stop(
        self, instrument: str, size: int, price: Decimal
    ) -> bool:
        """Place a plain protective stop on the closing side of the current
        position at `price`, sized `size`. Returns True on success.

        Emergency use only — NOT registered in the bracket/partial state
        machine. If the position is already flat, no-ops and returns True.
        """
        ...

    async def place_protective_target(
        self, instrument: str, size: int, price: Decimal
    ) -> bool:
        """Place a plain protective limit (take-profit) on the closing side
        of the current position at `price`, sized `size`. Returns True on
        success. Emergency use only — NOT registered in the state machine."""
        ...

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
        """
        Atomic entry + stop-loss + take-profit.

        Optional structural TP1 params: when tp1_price is provided the broker
        places a partial exit at that price and (when be_after_tp1 is True)
        moves the stop to break-even after that leg fills. tp1_fraction controls
        how much of the position to close at TP1 (0.5 = half).

        On success returns the three order IDs. On failure returns an
        error string and no IDs. The caller MUST check `success`.
        """
        ...

    async def flatten(self, instrument: str) -> bool:
        """
        Close any open position in `instrument` at market.

        Used by the engine when a lockout is triggered mid-trade and
        by the kill-switch endpoint. Always allowed by the risk gate.
        """
        ...

    async def cancel_all(self, instrument: str | None = None) -> int:
        """Cancel all working orders. Returns count cancelled."""
        ...

    # ------------------------------------------------------------------
    # Subscriptions — register handlers, broker invokes them on events.
    # ------------------------------------------------------------------

    def on_bar(self, handler: BarHandler) -> None:
        """Register a handler for new closed bars."""
        ...

    def on_fill(self, handler: FillHandler) -> None:
        """Register a handler for fills."""
        ...

    def on_equity(self, handler: EquityHandler) -> None:
        """Register a handler for mark-to-market updates."""
        ...

    async def subscribe(self, instruments: Iterable[str], timeframes: Iterable[str]) -> None:
        """Start the data feed for these instruments and timeframes."""
        ...

    def feed_is_healthy(self) -> bool:
        """True if the real-time event feed is connected and delivering events."""
        ...

    async def get_forming_bar(self, timeframe: str = "1min", instrument: str = "") -> "Bar | None":
        """
        Return the currently-forming bar (partially closed), or None if
        not supported by this broker implementation.
        """
        ...
