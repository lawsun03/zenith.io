"""Pure deterministic price/side/partial math for the TopstepX broker.

Extracted from topstepx.py: no SDK, no I/O, no broker state. These are the
deterministic transforms CLAUDE.md Rule 5 requires stay in plain Python —
side encoding, dollar-per-point, and the partial-profit/BE split. Unit-tested
directly (test_partial_exit.py, test_topstepx_partials.py)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from .events import Side

log = logging.getLogger(__name__)


# project-x-py side encoding. Centralize so the rest of the file stays clean.
SIDE_BUY = 0
SIDE_SELL = 1

# Dollar P&L per 1-point (1 dollar) price move per contract.
# Used to convert (exit_price - entry_price) → realized dollars.
_POINT_VALUE: dict[str, Decimal] = {
    "MGC":  Decimal("10"),    # Micro Gold: 10 oz
    "GC":   Decimal("100"),   # Gold: 100 oz
    "MNQ":  Decimal("2"),     # Micro Nasdaq-100
    "NQ":   Decimal("20"),    # Nasdaq-100
    "MES":  Decimal("5"),     # Micro E-mini S&P 500
    "ES":   Decimal("50"),    # E-mini S&P 500
    "MCL":  Decimal("100"),   # Micro WTI Crude Oil
    "CL":   Decimal("1000"),  # WTI Crude Oil
    "MBT":  Decimal("0.10"),  # Micro Bitcoin: 0.1 BTC, so $1/BTC move = $0.10/contract
    "M2K":  Decimal("5"),     # Micro Russell 2000
    "RTY":  Decimal("50"),    # Russell 2000
}


def _point_value(instrument: str) -> Decimal:
    """Return the dollar value of a 1-point price move for this instrument."""
    # Strip SDK contract suffix: "CON.F.US.MGC.M26" → "MGC"
    sym = instrument.split(".")[-2] if "." in instrument else instrument.upper()
    val = _POINT_VALUE.get(sym)
    if val is None:
        log.warning("Unknown instrument %r — P&L will be in price units, not dollars", sym)
        return Decimal("1")
    return val


@dataclass(frozen=True)
class PartialPlan:
    """How a partial-profit / BE entry is split. Pure data, no SDK."""
    partial_price: Decimal   # the R-multiple level (scale-out price; also the BE trigger for 1-lots)
    partial_size: int        # contracts to scale out (0 when entry size == 1)
    remaining_size: int      # contracts left after the partial
    be_price: Decimal        # break-even = the actual entry fill price


def _partial_plan(
    entry_price: Decimal,
    stop: Decimal,
    size: int,
    partial_r: Decimal,
    tp1_price: "Decimal | None" = None,
    tp1_fraction: Decimal = Decimal("0.5"),
) -> "PartialPlan | None":
    """Compute the partial/BE plan, or None when partials are disabled.

    If tp1_price is provided (structural TP1 from HTF swings), use it directly.
    Otherwise compute from partial_r: entry ± R*partial_r (R = |entry-stop|).
    Returns None when both tp1_price is None AND partial_r <= 0.

    partial_size = size // 2 for the R-based path; max(0, int(size*fraction)) for structural.
    be_price = entry_price in both cases.
    """
    if tp1_price is not None:
        partial_size = max(0, int(size * tp1_fraction))
        return PartialPlan(
            partial_price=tp1_price,
            partial_size=partial_size,
            remaining_size=size - partial_size,
            be_price=entry_price,
        )
    if partial_r <= 0:
        return None
    r = abs(entry_price - stop)
    is_long = stop < entry_price
    partial_price = entry_price + r * partial_r if is_long else entry_price - r * partial_r
    partial_size = size // 2
    return PartialPlan(
        partial_price=partial_price,
        partial_size=partial_size,
        remaining_size=size - partial_size,
        be_price=entry_price,
    )


def _to_internal_side(sdk_side: int) -> Side:
    return "long" if sdk_side == SIDE_BUY else "short"


def _to_sdk_side(side: Side) -> int:
    return SIDE_BUY if side == "long" else SIDE_SELL
