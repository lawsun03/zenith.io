"""Gate 8 — combine simulator. Reuses app.risk.account_phase.PhaseTracker
for the trailing-MLL math (see research/gates/combine.py docstring for why
that module and not app.risk.state.RiskState)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from research.gates.combine import (
    CombineTrade, evaluate, run_combine_simulation, trades_to_combine_trades,
)
from research.ir.engine import Trade
from research.ir.sizing import load_account_config

ACCOUNT = load_account_config("topstep-50k")
BASE_TS = datetime(2024, 1, 2, 13, 30, tzinfo=timezone.utc)


def _combine_trade(day_offset: int, pnl: str) -> CombineTrade:
    return CombineTrade(ts=BASE_TS + timedelta(days=day_offset), pnl_dollars=Decimal(pnl))


def test_steady_winner_reaches_target_regardless_of_order():
    # $100/day for 40 days: profit target $3000 reached after 30 winning
    # days; single-day P&L never comes close to the $1000 DLL or the
    # $2000 MLL distance, so every permutation of day order succeeds.
    trades = [_combine_trade(i, "100") for i in range(40)]
    prob = run_combine_simulation(trades, ACCOUNT, n_paths=200, rng=random.Random(0))
    assert prob == 1.0
    result = evaluate(prob, ACCOUNT)
    assert result.passed
    assert result.threshold == ACCOUNT["simulation"]["pass_threshold_payout_prob"]


def test_daily_loss_limit_breach_always_fails():
    # Every day loses more than the $1000 DLL alone, and the profit target
    # is never reached — whichever day lands first in a shuffle, that day
    # alone ends the path. (A single early DLL-breaching day mixed among
    # otherwise-winning days is NOT guaranteed to fail: if enough winning
    # days are shuffled ahead of it, the path can hit the profit target and
    # stop — CombineRules.stop_at_target — before that day is ever reached.)
    trades = [_combine_trade(i, "-1200") for i in range(10)]
    prob = run_combine_simulation(trades, ACCOUNT, n_paths=200, rng=random.Random(1))
    assert prob == 0.0


def test_trailing_mll_breach_fails():
    # One very good day, then a large losing day that breaches the $2000
    # trailing MLL distance from the high-water mark (but not the $1000 DLL,
    # since it's spread as two separate days below).
    trades = [
        _combine_trade(0, "3500"),   # profit target already exceeded on day 1
        _combine_trade(1, "-900"),
        _combine_trade(2, "-900"),
        _combine_trade(3, "-900"),
    ]
    prob = run_combine_simulation(trades, ACCOUNT, n_paths=1, rng=random.Random(2))
    assert prob == 0.0


def test_empty_trades_scores_zero():
    assert run_combine_simulation([], ACCOUNT) == 0.0


def test_trades_to_combine_trades_nets_commission_and_sizes():
    t = Trade(
        instrument="NQ", side="long", entry_ts=BASE_TS, entry_price=Decimal("100"),
        stop_price=Decimal("90"), target_price=Decimal("120"), exit_ts=BASE_TS,
        exit_price=Decimal("110"), exit_reason="target",
        pnl_points=Decimal("10"), commission_points_equivalent=Decimal("0.5"),
    )
    out = trades_to_combine_trades([t], [2], point_value=Decimal("20"))
    assert len(out) == 1
    assert out[0].pnl_dollars == (Decimal("10") - Decimal("0.5")) * Decimal("20") * 2


def test_mismatched_lengths_raise():
    import pytest
    with pytest.raises(ValueError):
        trades_to_combine_trades([], [1], point_value=Decimal("20"))


def test_per_trade_point_value_prices_each_trade_at_its_own_ratio():
    # Silver's micro is 1/5 of full, NQ's is 1/10: a scalar would mis-cost one of them.
    def _t(inst):
        return Trade(
            instrument=inst, side="long", entry_ts=BASE_TS, entry_price=Decimal("100"),
            stop_price=Decimal("90"), target_price=Decimal("120"), exit_ts=BASE_TS,
            exit_price=Decimal("110"), exit_reason="target",
            pnl_points=Decimal("1000"), commission_points_equivalent=Decimal("0"),
        )
    out = trades_to_combine_trades([_t("NQ"), _t("SI")], [1, 1], [Decimal("0.1"), Decimal("0.2")])
    assert [o.pnl_dollars for o in out] == [Decimal("100.0"), Decimal("200.0")]


def test_per_trade_point_value_length_mismatch_raises():
    import pytest
    with pytest.raises(ValueError):
        trades_to_combine_trades([], [], [Decimal("1")])
