"""B105 — lived-variance reporting for funded pipeline.

Why: mean $/account hides right-skew; a large outlier account pulls the mean
up while the typical account earns far less. Dry-spell cadence lets the trader
know how long to hold cash reserves before the next payout. Monthly fixed costs
(software, data, etc.) reduce true net — must be subtracted for honest math.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.backtest.funded_sim import (
    _calendar_months,
    _month_sequence,
    pipeline_variance_summary,
    simulate_xfa_chain,
)

D = Decimal
T0 = datetime(2021, 1, 4, 18, 0, tzinfo=timezone.utc)  # 2021-01-04 12:00 CT


def daily(pnls: list[str]) -> list[tuple[datetime, Decimal]]:
    out, ts = [], T0
    for p in pnls:
        out.append((ts, D(p)))
        ts += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_calendar_months_same_month():
    assert _calendar_months("2021-01", "2021-01") == 1


def test_calendar_months_span():
    assert _calendar_months("2021-01", "2021-06") == 6


def test_calendar_months_year_boundary():
    assert _calendar_months("2021-11", "2022-02") == 4


def test_month_sequence_single():
    assert _month_sequence("2021-03", "2021-03") == ["2021-03"]


def test_month_sequence_span():
    seq = _month_sequence("2021-11", "2022-02")
    assert seq == ["2021-11", "2021-12", "2022-01", "2022-02"]


# ---------------------------------------------------------------------------
# per_account_net_payouts
# ---------------------------------------------------------------------------


def test_per_account_payouts_length_equals_accounts():
    # 2 accounts: first busts (no payout), second makes a payout and ends alive
    days = ["-3000"] + ["800"] * 5
    x = simulate_xfa_chain(daily(days))
    assert len(x["per_account_net_payouts"]) == x["accounts"]


def test_bust_with_no_payout_contributes_zero():
    # Immediate bust (day 1 loss > MLL distance): per-account payout = 0
    days = ["-3000"]
    x = simulate_xfa_chain(daily(days))
    assert x["per_account_net_payouts"][0] == 0.0


def test_per_account_sum_matches_net_payouts():
    # Total of per_account_net_payouts should equal net_payouts
    days = ["800"] * 10 + ["-3000"] + ["800"] * 5
    x = simulate_xfa_chain(daily(days))
    total = sum(x["per_account_net_payouts"])
    assert abs(total - float(x["net_payouts"])) < 0.01


def test_account_with_payout_then_bust_nonzero():
    # Account makes a payout, then busts later — should record the payout, not $0
    days = ["800"] * 5 + ["-3000"]
    x = simulate_xfa_chain(daily(days))
    # Account made payouts ($800*5 over payout threshold) then busted
    assert x["per_account_net_payouts"][0] > 0.0


# ---------------------------------------------------------------------------
# monthly_net_payouts
# ---------------------------------------------------------------------------


def test_monthly_net_payouts_maps_payout_months():
    # All 5 days in Jan 2021, triggering payout — should appear in 2021-01
    days = ["800"] * 5
    x = simulate_xfa_chain(daily(days))
    mmap = x["monthly_net_payouts"]
    assert any(v > 0 for v in mmap.values()), "expected at least one month with payouts"


def test_no_payout_months_absent():
    # Immediate bust — no payouts — monthly map should be empty
    days = ["-3000"]
    x = simulate_xfa_chain(daily(days))
    assert x["monthly_net_payouts"] == {}


# ---------------------------------------------------------------------------
# pipeline_variance_summary
# ---------------------------------------------------------------------------


def _run_xfa(pnls: list[str]) -> dict:
    return simulate_xfa_chain(daily(pnls))


def test_variance_summary_returns_expected_keys():
    x = _run_xfa(["800"] * 5)
    s = pipeline_variance_summary(x)
    for key in (
        "median_net_per_account", "p25_net_per_account",
        "total_months", "max_dry_spell_months",
        "reserve_months", "reserve_needed",
        "monthly_fixed_cost", "net_after_monthly_costs",
        "dry_spells",
    ):
        assert key in s, f"missing key: {key}"


def test_median_lt_mean_when_right_skewed():
    # Two busts ($0) + one large payout: mean > median
    big_payout = ["800"] * 20  # many payouts = large per-account total
    busts = ["-3000", "-3000"]
    x = simulate_xfa_chain(daily(busts + big_payout))
    s = pipeline_variance_summary(x)
    # mean = sum / n; median should be 0 or very low (2 busts vs 1 payer with 3 accounts)
    mean = float(x["net_payouts"]) / x["accounts"]
    assert s["median_net_per_account"] <= mean + 0.01


def test_dry_spell_detected():
    # 31 days (all one month), producing a payout, then 31 more days with no payouts (all bust)
    # Arrange: first "month" has payouts, second has all busts
    # T0 = 2021-01-04; 28 days lands us in Feb 2021
    payout_month = ["800"] * 5 + ["0"] * 25  # payouts in Jan 2021, silent rest of month
    bust_month = ["-3000"]  # bust in Feb 2021 (no payouts)
    x = simulate_xfa_chain(daily(payout_month + bust_month))
    s = pipeline_variance_summary(x)
    # There should be some dry spells (months with $0 payouts)
    assert s["max_dry_spell_months"] >= 1


def test_reserve_months_is_max_dry_spell_plus_one():
    x = _run_xfa(["800"] * 5)
    s = pipeline_variance_summary(x)
    assert s["reserve_months"] == s["max_dry_spell_months"] + 1


def test_monthly_cost_subtracted():
    # Monthly cost should reduce net_after_monthly_costs
    x = _run_xfa(["800"] * 5)
    net = float(x["net_payouts"])
    s = pipeline_variance_summary(x, monthly_fixed_cost=100.0)
    assert s["net_after_monthly_costs"] < net


def test_monthly_cost_zero_leaves_net_unchanged():
    x = _run_xfa(["800"] * 5)
    net = float(x["net_payouts"])
    s = pipeline_variance_summary(x, monthly_fixed_cost=0.0)
    assert abs(s["net_after_monthly_costs"] - net) < 0.01


def test_series_month_fields_populated():
    # simulate_xfa_chain returns series_start_month and series_end_month
    days = ["0"] * 5
    x = simulate_xfa_chain(daily(days))
    assert x["series_start_month"] == "2021-01"
    assert x["series_end_month"] == "2021-01"
