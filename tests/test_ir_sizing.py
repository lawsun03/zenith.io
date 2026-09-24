"""Position sizing: separate module, reads only docs/research-loop/accounts,
never sees rule/predicate internals (CLAUDE.md rule 4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from research.ir.engine import Trade
from research.ir.sizing import instrument_spec, load_account_config, size_trades


def _trade(pnl_points: str) -> Trade:
    ts = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)
    return Trade(
        instrument="MGC", side="long", entry_ts=ts, entry_price=Decimal("100"),
        stop_price=Decimal("99"), target_price=Decimal("102"),
        exit_ts=ts + timedelta(hours=1), exit_price=Decimal("100") + Decimal(pnl_points),
        exit_reason="target", pnl_points=Decimal(pnl_points),
    )


def test_load_topstep_50k_account() -> None:
    account = load_account_config("topstep-50k")
    assert account["starting_balance"] == "50000.00"
    spec = instrument_spec(account, "MGC")
    assert spec.point_value == Decimal("10.00")
    assert spec.tick_size == Decimal("0.10")


def test_size_trades_floors_at_one_contract() -> None:
    account = load_account_config("topstep-50k")
    trades = [_trade("1"), _trade("-1"), _trade("2"), _trade("-2")]
    sizes = size_trades(trades, Decimal("0.15"), account, "MGC")
    assert len(sizes) == 4
    assert all(s >= 1 for s in sizes)


def test_size_trades_respects_max_contracts_cap() -> None:
    account = load_account_config("topstep-50k")
    # Near-zero variance trades -> the raw vol-target formula would want a
    # very large contract count; the cap must still bind.
    trades = [_trade("0.01"), _trade("-0.01"), _trade("0.01"), _trade("-0.01")] * 20
    sizes = size_trades(trades, Decimal("0.40"), account, "MGC", max_contracts=3)
    assert all(s <= 3 for s in sizes)


def test_size_trades_empty_sequence() -> None:
    account = load_account_config("topstep-50k")
    assert size_trades([], Decimal("0.15"), account, "MGC") == []


def test_micro_vs_mini_position_limit_selection() -> None:
    account = load_account_config("topstep-50k")
    trades = [_trade("0.01"), _trade("-0.01")] * 10
    micro_sizes = size_trades(trades, Decimal("0.40"), account, "MGC")
    mini_sizes = size_trades(trades, Decimal("0.40"), account, "GC")
    assert max(micro_sizes) <= account["position_limits"]["max_micro_contracts"]
    assert max(mini_sizes) <= account["position_limits"]["max_mini_contracts"]
