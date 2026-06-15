"""B86: combine_gap_days parameter in simulate_xfa_chain.

WHY: funded_sim restarts the next XFA account the day after a bust, but in
reality the next funded account can't start until the NEXT combine pass
(24.6 trading days on average). The gap parameter quantifies this idle-time
drag. These tests verify the skip logic is correct before running the
calibration benchmark.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.backtest.funded_sim import simulate_xfa_chain

D = Decimal
T0 = datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc)


def daily(pnls: list[str]) -> list[tuple[datetime, Decimal]]:
    out, ts = [], T0
    for p in pnls:
        out.append((ts, D(p)))
        ts += timedelta(days=1)
    return out


def test_gap0_matches_default_behavior():
    """gap=0 must be identical to the pre-gap baseline (regression guard)."""
    pnls = ["-2000", "700", "700", "700", "700", "700"]
    d = daily(pnls)
    baseline = simulate_xfa_chain(d)
    with_gap0 = simulate_xfa_chain(d, combine_gap_days=0)
    assert baseline == with_gap0


def test_gap5_skips_days_after_bust():
    """After a bust, gap=5 skips 5 days before starting the next account.

    With gap=5: bust day 1 -> days 2-6 are skipped -> account 2 starts day 7.
    If only days 2-6 are in the series, account 2 never starts -> busts=1, accounts=1.
    With gap=0: account 2 starts day 2, runs through days 2-6, accumulates payouts.
    """
    # day 1 bust, days 2-6 would be profitable for account 2 at gap=0
    pnls = ["-2000"] + ["700"] * 5
    d = daily(pnls)

    gap0 = simulate_xfa_chain(d, combine_gap_days=0)
    gap5 = simulate_xfa_chain(d, combine_gap_days=5)

    # gap=0: account 2 starts day 2, earns 5x700=3500 -> payout + surviving
    assert gap0["accounts"] == 2
    assert gap0["gross_payouts"] > D("0")

    # gap=5: bust on day 1, days 2-6 skipped, series ends -> account 2 never starts
    assert gap5["accounts"] == 1
    assert gap5["busts"] == 1
    assert gap5["gross_payouts"] == D("0")


def test_gap5_bust_skips_then_new_account_starts():
    """Account 2 starts after the gap and accrues payouts normally."""
    # day 1 bust, days 2-6 skipped (gap=5), day 7-12 profitable for account 2
    pnls = ["-2000"] + ["0"] * 5 + ["700"] * 5
    d = daily(pnls)

    gap5 = simulate_xfa_chain(d, combine_gap_days=5)

    # Account 1 busts on day 1. Account 2 starts on day 7 (after 5-day gap).
    # Account 2 runs days 7-11 (5 profitable days) -> payout available.
    assert gap5["accounts"] == 2
    assert gap5["busts"] == 1
    assert gap5["gross_payouts"] > D("0")

    # With gap=0, account 2 starts day 2 and runs all days 2-11 (including the 0s)
    gap0 = simulate_xfa_chain(d, combine_gap_days=0)
    # Both should have 2 accounts, but gap=0 has no idle days so same gross_payouts
    assert gap0["accounts"] == 2
    # Key difference: both same days of productive trading, same payouts
    assert gap5["gross_payouts"] == gap0["gross_payouts"]


def test_gap_bust_on_last_day_no_error():
    """A bust on the last day of the series with gap days extending past the
    end must not raise an error. The gap days simply exhaust the series.

    XFA MLL starts at starting_balance(0) - mll_distance(2000) = -2000.
    Flat days keep eod_high_water at 0. A -2100 day falls below MLL=-2000.
    """
    pnls = ["0"] * 3 + ["-2100"]  # bust on day 4 (the last day)
    d = daily(pnls)
    # Should not raise; gap extends past end of series silently
    result = simulate_xfa_chain(d, combine_gap_days=5)
    assert result["busts"] == 1
    assert result["accounts"] == 1  # account 2 never starts (no days remaining after gap)
