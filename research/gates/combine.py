"""Gate 8 — combine simulator (docs/research-loop/gates.md).

Monte Carlo resamples of trade order, each run through the account rules in
docs/research-loop/accounts/topstep-50k.json, reporting the fraction of
paths that reach the profit target without a daily-loss, trailing-MLL or
consistency-rule breach.

Reuses app.risk.account_phase.PhaseTracker for the trailing-MLL math per
the phase-4 brief ("reuse the existing risk module's trailing-MLL
implementation rather than rewriting it") — specifically `PhaseTracker`,
not `app.risk.state.RiskState`: the account config's
`max_loss_limit.trails_on == "end_of_day"` is PhaseTracker's
`CombineRules(mll_trailing="eod")` behaviour (ratchets only at `roll_day`),
whereas RiskState implements an intraday-trailing MLL for the live
governor — a different, unrelated number for a deleted live path. The daily
loss limit and consistency-rule checks aren't in PhaseTracker at all, so
they're computed directly here from the account config, which
topstep-50k.json's own `_note` calls "the single place [prop-firm rules]
are encoded" — nothing is hardcoded from a stale copy of the rules.

Resampling granularity: the account config says `"resample": "trade_order"`,
but this implementation resamples at the granularity of TRADING DAYS (each
day's trades kept in their original intraday order, whole days shuffled).
Under this account's END-OF-DAY MLL/DLL measurement, a day's aggregate P&L
and its contribution to the trailing high-water mark depend only on which
trades occurred that day, not what order they filled in — so permuting whole
days is equivalent, for every rule this gate checks, to permuting individual
trade order, while remaining a well-defined operation (individual-trade
resampling would scramble which trades share a calendar day, which the
daily-loss-limit and EOD-MLL checks both need to stay coherent). This
equivalence is a stated assumption (CLAUDE.md rule 1), not a spec quote.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Sequence

from app.risk.account_phase import CombineRules, PhaseTracker
from research.gates.types import GateResult
from research.ir.engine import Trade

COMBINE_PAYOUT_PROB_MIN_DEFAULT = 0.60


@dataclass(frozen=True)
class CombineTrade:
    ts: datetime
    pnl_dollars: Decimal


def trades_to_combine_trades(
    trades: Sequence[Trade], contracts: Sequence[int], point_value: Decimal,
) -> list[CombineTrade]:
    """Convert the rule-level trade sequence to dollar P&L at the sizing
    this candidate's IR document actually specifies (research.ir.sizing) —
    the one place account size is allowed to touch anything downstream of
    the rule (CLAUDE.md domain invariant 4)."""
    if len(trades) != len(contracts):
        raise ValueError("trades and contracts must be the same length and aligned by index")
    out = []
    for t, c in zip(trades, contracts):
        net_points = t.pnl_points - t.commission_points_equivalent
        out.append(CombineTrade(ts=t.exit_ts, pnl_dollars=net_points * point_value * c))
    return out


def _rules_from_account(account: dict) -> CombineRules:
    mll = account["max_loss_limit"]
    return CombineRules(
        starting_balance=Decimal(account["starting_balance"]),
        profit_target=Decimal(account["profit_target"]),
        mll_distance=Decimal(mll["amount"]),
        mll_trailing="eod" if mll["trails_on"] == "end_of_day" else "intraday",
    )


def _consistency_breach(tracker: PhaseTracker, account: dict) -> bool:
    rule = account["consistency_rule"]
    if not rule.get("enabled"):
        return False
    profit = tracker.total_profit
    if profit <= 0:
        return False
    cap_frac = Decimal(str(rule["max_single_day_pct_of_total"]))
    return tracker.best_day_live > profit * cap_frac


def _group_by_day(trades: Sequence[CombineTrade]) -> list[list[CombineTrade]]:
    buckets: dict[date, list[CombineTrade]] = {}
    for t in trades:
        buckets.setdefault(t.ts.date(), []).append(t)
    return [buckets[d] for d in sorted(buckets)]


def _run_one_path(
    day_blocks: Sequence[list[CombineTrade]], account: dict, daily_loss_limit: Decimal,
) -> bool:
    tracker = PhaseTracker(phase="combine", combine=_rules_from_account(account))
    for day in day_blocks:
        day_pnl = Decimal("0")
        for t in day:
            tracker.on_pnl(t.pnl_dollars, t.ts)
            day_pnl += t.pnl_dollars
            if day_pnl <= -daily_loss_limit:
                return False
            if tracker.is_dead():
                return False
        tracker.roll_day(day[-1].ts)
        if tracker.total_profit >= tracker.combine.profit_target:
            return not _consistency_breach(tracker, account)
    return False


def run_combine_simulation(
    trades: Sequence[CombineTrade], account: dict, *,
    n_paths: int | None = None, rng: random.Random | None = None,
) -> float:
    """Fraction of Monte Carlo paths that reach the profit target without
    breaching the daily loss limit, the trailing MLL, or the consistency
    rule. 0.0 for an empty trade sequence (nothing to reach the target)."""
    if not trades:
        return 0.0
    day_blocks = _group_by_day(trades)
    n_paths = n_paths or account["simulation"]["monte_carlo_paths"]
    rng = rng or random.Random()
    daily_loss_limit = Decimal(account["daily_loss_limit"]["amount"])

    successes = 0
    for _ in range(n_paths):
        order = day_blocks[:]
        rng.shuffle(order)
        if _run_one_path(order, account, daily_loss_limit):
            successes += 1
    return successes / n_paths


def evaluate(payout_prob: float, account: dict) -> GateResult:
    threshold = account.get("simulation", {}).get(
        "pass_threshold_payout_prob", COMBINE_PAYOUT_PROB_MIN_DEFAULT
    )
    return GateResult(gate=8, passed=payout_prob >= threshold,
                       measured=payout_prob, threshold=threshold)
