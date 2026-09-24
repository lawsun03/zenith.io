"""Position sizing — the only module that reads docs/research-loop/accounts/*.json.

Strictly separate from rule design (CLAUDE.md rule 4: "Account size never
enters rule design... enter at position sizing and at the combine
simulator — nowhere else"). Nothing above this module (predicates.py,
engine.py) has ever seen an account balance, a daily loss limit, or a
trailing-drawdown figure, and nothing here feeds back into them.

Carver-style volatility targeting: contracts sized so the strategy's
realised annualised volatility matches `sizing.vol_target_annual` from the
IR document, using each trade's own stop distance as the per-trade risk
proxy and the account's point_value/tick_value to convert points to
dollars. Not the same function as app/risk/sizing.py's fixed-fractional
risk_based_size (that one sizes off live equity for a single order; this
one takes a whole trade sequence and a vol target, per the task's
"(trade sequence, vol_target_annual, account config) -> contract counts").
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .engine import Trade

_ACCOUNTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "docs" / "research-loop" / "accounts"
)

# Trading days per year, used to annualise a per-trade points series.
# Matches the corpus's ~252 trading day convention (docs/research-loop/README.md).
_TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class InstrumentSpec:
    point_value: Decimal
    tick_value: Decimal
    tick_size: Decimal


def load_account_config(name: str) -> dict:
    path = _ACCOUNTS_DIR / f"{name}.json"
    return json.loads(path.read_text())


def instrument_spec(account: dict, symbol: str) -> InstrumentSpec:
    entry = account["instruments"][symbol]
    return InstrumentSpec(
        point_value=Decimal(entry["point_value"]),
        tick_value=Decimal(entry["tick_value"]),
        tick_size=Decimal(entry["tick_size"]),
    )


def size_trades(
    trades: list[Trade],
    vol_target_annual: Decimal,
    account: dict,
    symbol: str,
    *,
    max_contracts: int | None = None,
    trades_per_year: int | None = None,
) -> list[int]:
    """Contracts for each trade, sized so the sequence's realised annualised
    dollar-volatility (stdev of per-trade dollar P&L at 1 contract, scaled
    by trade frequency) matches `vol_target_annual` x starting_balance.

    Returns one contract count per trade, aligned by index. A trade with
    zero measurable risk (stop==entry, already rejected upstream) never
    reaches here. Floor of 1 contract, per the account's position limits
    and any `max_contracts` cap from the IR document.
    """
    if not trades:
        return []

    spec = instrument_spec(account, symbol)
    starting_balance = Decimal(account["starting_balance"])
    account_cap = account["position_limits"].get(
        "max_micro_contracts" if symbol.startswith("M") else "max_mini_contracts"
    )
    cap = min(c for c in (max_contracts, account_cap) if c is not None)

    per_contract_pnl = [t.pnl_points * spec.point_value for t in trades]
    n = len(per_contract_pnl)
    mean = sum(per_contract_pnl, Decimal("0")) / n
    variance = sum((p - mean) ** 2 for p in per_contract_pnl) / n if n > 1 else Decimal("0")
    stdev_per_trade = variance.sqrt() if variance > 0 else Decimal("0")

    freq = Decimal(trades_per_year) if trades_per_year else Decimal(n)
    annual_stdev_per_contract = stdev_per_trade * freq.sqrt()

    target_dollar_vol = vol_target_annual * starting_balance
    if annual_stdev_per_contract <= 0:
        contracts = 1
    else:
        contracts = int(target_dollar_vol / annual_stdev_per_contract)
    contracts = max(1, min(contracts, cap))

    return [contracts] * n
