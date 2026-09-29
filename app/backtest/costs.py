"""Per-instrument research cost model (docs/research-protocol.md §6).

Tick size, tick value and commission come from the PaperBroker tables so the
simulator and the metrics layer can never disagree on contract math; this
module only adds slippage per order type. The protocol YAML (step 3) will
override these defaults per protocol version.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.sim.paper import DEFAULT_COMMISSION, TICK_SIZE, TICK_VALUE


@dataclass(frozen=True)
class CostSpec:
    tick: Decimal
    tick_value: Decimal
    commission_rt: Decimal            # per contract, round trip
    slip_ticks_market: int = 1        # per side, adverse
    slip_ticks_stop: int = 1
    slip_ticks_limit: int = 0

    @property
    def point_value(self) -> Decimal:
        return self.tick_value / self.tick


def cost_spec(instrument: str, commission_rt: Decimal | None = None) -> CostSpec:
    """Default cost spec. Raises on an instrument without contract specs —
    silently defaulting a tick is how the 50x silver error would have shipped."""
    if instrument not in TICK_SIZE or instrument not in TICK_VALUE:
        raise KeyError(f"no tick size/value for {instrument!r}; add it to app/sim/paper.py")
    if commission_rt is None:
        commission_rt = DEFAULT_COMMISSION.get(instrument, Decimal("0.74")) * 2
    return CostSpec(tick=TICK_SIZE[instrument], tick_value=TICK_VALUE[instrument],
                    commission_rt=commission_rt)
