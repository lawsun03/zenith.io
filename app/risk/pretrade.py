"""
Pretrade gate — the only path from a Signal to a broker order.

The execution engine MUST call check() before placing any order. If
check() returns Deny, the order is dropped and logged. There is no
override; manual buttons in the dashboard route through here too.

Design notes:
  - check() is a pure function of (proposed order, current state). No I/O.
  - Return type is a tagged union (Allow | Deny). No exceptions for
    expected denial; exceptions only for bugs.
  - Allow may carry an `allowed_size` smaller than requested — the gate
    is permitted to size down to fit constraints, but never up.
  - All checks compose; the first failing check determines the denial.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Union

from .config import Money
from .state import RiskState

Side = Literal["long", "short"]


@dataclass(frozen=True)
class ProposedOrder:
    """An order the strategy wants to place. Not yet sent to the broker."""

    instrument: str          # e.g. "MGC" (micro gold)
    side: Side
    size: int                # contracts (mini-equivalent count)
    entry: Money             # limit/stop entry price
    stop: Money              # protective stop price
    target: Money            # take-profit price
    is_entry: bool = True    # False for exit/flatten orders


@dataclass(frozen=True)
class Allow:
    allowed_size: int        # may be < requested size


@dataclass(frozen=True)
class Deny:
    reason_code: str         # e.g. "LOCKED_OUT", "MAX_CONTRACTS"
    message: str


Decision = Union[Allow, Deny]


def check(order: ProposedOrder, state: RiskState) -> Decision:
    """
    Run every gate in priority order. First failing gate decides.

    Order matters here: lockouts are checked before sizing, because a
    locked-out account shouldn't even consider the order. Exit orders
    bypass entry-only checks (you must always be allowed to flatten).
    """

    # ------------------------------------------------------------
    # 1. Lockout: nothing new opens, but exits are always allowed.
    # ------------------------------------------------------------
    if state.locked_out is not None:
        if order.is_entry:
            return Deny(
                reason_code="LOCKED_OUT",
                message=f"Account locked: {state.locked_out.message}",
            )
        # Fall through for exit orders — closing a position is never blocked.

    # ------------------------------------------------------------
    # 2. Sanity: stop must actually be protective relative to entry.
    # ------------------------------------------------------------
    if order.is_entry:
        if order.side == "long" and order.stop >= order.entry:
            return Deny(
                reason_code="INVALID_STOP",
                message=f"Long stop {order.stop} not below entry {order.entry}.",
            )
        if order.side == "short" and order.stop <= order.entry:
            return Deny(
                reason_code="INVALID_STOP",
                message=f"Short stop {order.stop} not above entry {order.entry}.",
            )

    # ------------------------------------------------------------
    # 3. Size: cap at remaining contract headroom.
    # ------------------------------------------------------------
    if order.is_entry:
        max_contracts = state.config.max_contracts
        # open_contracts is signed (negative = short); use abs so short
        # positions consume headroom the same way longs do.
        headroom = max_contracts - abs(state.open_contracts)
        if headroom <= 0:
            return Deny(
                reason_code="MAX_CONTRACTS",
                message=(
                    f"Already at max contracts "
                    f"({state.open_contracts}/{max_contracts})."
                ),
            )
        allowed = min(order.size, headroom)
        if allowed <= 0:
            return Deny(
                reason_code="ZERO_SIZE",
                message="Sizing reduced to zero.",
            )
        return Allow(allowed_size=allowed)

    # Exit orders pass through with their requested size.
    return Allow(allowed_size=order.size)
