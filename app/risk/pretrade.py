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
from datetime import datetime
from decimal import Decimal
from typing import Literal, Union

from app.broker.pricing import _point_value
from app.risk.account_phase import PhaseTracker
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
    setup_grade: str = ""    # "A".."F" or "" when ungraded


@dataclass(frozen=True)
class Allow:
    allowed_size: int        # may be < requested size


@dataclass(frozen=True)
class Deny:
    reason_code: str         # e.g. "LOCKED_OUT", "MAX_CONTRACTS"
    message: str


Decision = Union[Allow, Deny]


def check(
    order: ProposedOrder,
    state: RiskState,
    phase: PhaseTracker | None = None,
    ts: datetime | None = None,
) -> Decision:
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
    # 1b. Phase governor (combine/xfa only; practice/live skip).
    #     Deterministic hard gates — no overrides (Rule 5).
    # ------------------------------------------------------------
    if phase is not None and phase.phase in ("combine", "xfa") and order.is_entry:
        cushion = phase.cushion
        if cushion is not None and cushion < Decimal("500"):
            return Deny(reason_code="MLL_CUSHION",
                        message=f"Cushion {cushion} < $500 — surviving to next ratchet.")
        if phase.phase == "combine":
            if phase.combine.stop_at_target and phase.target_reached():
                return Deny(reason_code="TARGET_REACHED",
                            message="Combine passed — stop trading, do not give it back.")
            denom = phase.total_profit
            if denom > 0 and phase.today_pnl > 0 \
                    and phase.today_pnl >= phase.combine.best_day_cap_frac * denom:
                return Deny(reason_code="BEST_DAY_CAP",
                            message=f"Today {phase.today_pnl} hit the bot's intraday "
                                    f"best-day cap (tighter than Topstep's 50% rule) "
                                    f"— done for the day.")
        if phase.phase == "xfa":
            if (phase.today_pnl >= 2 * phase.xfa.winning_day_threshold
                    and order.setup_grade not in ("A",)):
                return Deny(reason_code="WINNING_DAY_LOCK",
                            message=f"Today {phase.today_pnl} — protecting the "
                                    f"winning day; A-grade setups only.")

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
        if phase is not None and phase.cushion is not None and phase.phase in ("combine", "xfa"):
            cushion = phase.cushion
            stop_dist = abs(order.entry - order.stop)
            pv = _point_value(order.instrument)
            if stop_dist > 0 and pv > 0:
                # Worst-case single-trade loss must be <= 40% of cushion.
                cap = int((Decimal("0.40") * cushion) / (stop_dist * pv))
                if cushion <= Decimal("1000") or phase.post_payout_half_risk:
                    cap = cap // 2
                if cap <= 0:
                    return Deny(reason_code="MLL_CUSHION",
                                message=f"No size fits 40% of cushion {cushion}.")
                headroom = min(headroom, cap)
        allowed = min(order.size, headroom)
        if allowed <= 0:
            return Deny(
                reason_code="ZERO_SIZE",
                message="Sizing reduced to zero.",
            )
        return Allow(allowed_size=allowed)

    # Exit orders pass through with their requested size.
    return Allow(allowed_size=order.size)
