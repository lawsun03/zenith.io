"""Funded-pipeline simulator — sequential Combines + XFA chain.

Why: period-total backtest P&L is misleading; what matters is
pass/bust/payout against real account rules. The sim shares PhaseTracker
with the live governor — one source of truth for rule math.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)

D = Decimal
T0 = datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc)  # 12:00 CT


def daily(pnls: list[str]) -> list[tuple[datetime, Decimal]]:
    """One (ts, pnl) per day."""
    out, ts = [], T0
    for p in pnls:
        out.append((ts, D(p)))
        ts += timedelta(days=1)
    return out


def test_combine_pass_counts():
    # +1.4k x2 days then +0.4k -> pass on day 3 (consistency ok), then flat
    days = ["1400", "1400", "400"] + ["0"] * 5
    res = simulate_combines(daily(days))
    assert res["passes"] == 1 and res["busts"] == 0


def test_combine_bust_then_new_attempt():
    # -2k day busts attempt 1 immediately; attempt 2 starts next day and passes
    days = ["-2000", "1400", "1400", "400"]
    res = simulate_combines(daily(days))
    assert res["busts"] == 1 and res["passes"] == 1
    assert res["attempts"] == 2


def test_xfa_payout_and_bust_chain():
    # 5 winning days of +700 -> payout 1750; then -2k+ run busts the account
    days = ["700"] * 5 + ["-900", "-900"]
    res = simulate_xfa_chain(daily(days))
    assert res["gross_payouts"] == D("1750")
    assert res["busts"] == 1


def test_daily_pnls_from_equity_groups_by_trading_day():
    """Fill-granular equity curve collapses to per-5pm-CT-day deltas."""
    curve = [
        # day 1 (Jan 5 CT): two fills
        (datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc), D("50000")),
        (datetime(2026, 1, 5, 20, 0, tzinfo=timezone.utc), D("50300")),
        # 23:30 UTC = 17:30 CT -> trading day Jan 6
        (datetime(2026, 1, 5, 23, 30, tzinfo=timezone.utc), D("50100")),
        # Jan 6 14:00 UTC = 08:00 CT, still trading day Jan 6
        (datetime(2026, 1, 6, 14, 0, tzinfo=timezone.utc), D("50600")),
    ]
    days = daily_pnls_from_equity(curve)
    assert len(days) == 2
    assert days[0][1] == D("300")    # +300 on day 1
    assert days[1][1] == D("300")    # -200 then +500 on day 2


def test_median_days_to_pass_counted_from_attempt_start():
    """days-to-pass is per-ATTEMPT, not per-series - an off-by-one here
    skews the headline metric the go/no-go decision reads."""
    days = ["1400", "1400", "400"] + ["0"] * 5
    res = simulate_combines(daily(days))
    assert res["median_days_to_pass"] == 3
    # bust on day 1, attempt 2 passes on its own days 1-3 (series days 2-4)
    days2 = ["-2000", "1400", "1400", "400"]
    res2 = simulate_combines(daily(days2))
    assert res2["median_days_to_pass"] == 3


def test_unsorted_equity_curve_is_handled():
    """Day attribution must not depend on caller-supplied ordering."""
    a = datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc)
    b = datetime(2026, 1, 5, 20, 0, tzinfo=timezone.utc)
    curve_sorted = [(a, D("50000")), (b, D("50300"))]
    curve_unsorted = [(b, D("50300")), (a, D("50000"))]
    assert daily_pnls_from_equity(curve_sorted) == daily_pnls_from_equity(curve_unsorted)
