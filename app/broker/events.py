"""
Broker events — the internal vocabulary the rest of the bot speaks.

Why a translation layer instead of using project-x-py types directly:

  1. The SDK is third-party and may break. If the bot's internal types
     are ours, an SDK upgrade only touches `client.py`, not strategy
     or risk code.
  2. The SDK uses ints for side (0=Buy, 1=Sell). The risk module uses
     "long"/"short". One translation point is better than littering
     conversions everywhere.
  3. Tests for the strategy and risk modules can construct these events
     directly with no SDK dependency.

Convention: every event carries a UTC timestamp. The broker is the
single source of truth for "now" — never use datetime.now() elsewhere
in the live path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

Side = Literal["long", "short"]


@dataclass(frozen=True)
class Bar:
    """OHLCV bar from the data manager."""

    instrument: str
    timeframe: str           # e.g. "1min", "5min"
    ts: datetime             # bar close time, UTC
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True)
class MarkToMarket:
    """
    Live equity update.

    Account equity = realized balance + unrealized P&L on open positions.
    The broker computes this from positions + last trade price and emits
    it on every position update OR on a periodic timer (whichever comes
    first). Risk state needs this to track the trailing-DD high water mark.
    """

    ts: datetime
    equity: Decimal


@dataclass(frozen=True)
class Fill:
    """
    A fill confirmation from the broker.

    realized_pnl_delta is signed:
      - positive when closing a profitable trade
      - negative when closing a loser
      - zero when opening (no realized P&L until the position closes)

    contracts_delta is signed in mini-equivalents:
      - +N for opening N contracts long
      - -N for opening N contracts short OR closing N longs
    """

    ts: datetime
    instrument: str
    side: Side
    fill_price: Decimal
    size: int
    is_entry: bool
    realized_pnl_delta: Decimal
    contracts_delta: int
    broker_order_id: str
    is_stop: bool = False


@dataclass(frozen=True)
class BracketResult:
    """Result of place_bracket_order — three linked orders."""

    success: bool
    entry_order_id: str | None
    stop_order_id: str | None
    target_order_id: str | None
    error: str | None = None


@dataclass(frozen=True)
class BrokerPosition:
    """Snapshot of a single open position from the broker."""

    instrument: str
    side: Side
    size: int                # mini-equivalent
    average_price: Decimal
    unrealized_pnl: Decimal
