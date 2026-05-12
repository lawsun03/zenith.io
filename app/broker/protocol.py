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

from .events import Bar, BracketResult, BrokerPosition, Fill, MarkToMarket, Side


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
    ) -> BracketResult:
        """
        Atomic entry + stop-loss + take-profit.

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
