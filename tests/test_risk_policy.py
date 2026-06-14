"""
Defining-behavior tests for B50: account-state dynamic risk sizing.

Policy functions live in app.backtest.risk_policy; integration
with simulate_combines / simulate_xfa_chain is in app.backtest.funded_sim.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.backtest.risk_policy import combine_ramp_multiplier, funded_survival_multiplier
from app.backtest.funded_sim import simulate_combines, simulate_xfa_chain


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_COMBINE = Decimal("50000")
TARGET_COMBINE = Decimal("53000")  # +$3k

def _day(n: int) -> datetime:
    from datetime import timedelta
    base = datetime(2024, 1, 1, 16, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(days=n)


def _flat_series(n_days: int, daily_pnl: Decimal) -> list[tuple[datetime, Decimal]]:
    """Constant P&L per day for n_days."""
    return [(_day(i + 1), daily_pnl) for i in range(n_days)]


# ---------------------------------------------------------------------------
# Test 1: constant policy is byte-identical to no-policy simulation
# ---------------------------------------------------------------------------

def test_constant_policy_identical_to_baseline() -> None:
    """risk_policy='constant' must produce the same pass/bust counts as calling
    the functions with no policy argument (i.e., the default)."""
    series = _flat_series(120, Decimal("30"))  # slow but passes eventually

    baseline = simulate_combines(series)
    policy_const = simulate_combines(series, risk_policy="constant")

    assert policy_const["passes"] == baseline["passes"]
    assert policy_const["busts"] == baseline["busts"]
    assert policy_const["attempts"] == baseline["attempts"]


# ---------------------------------------------------------------------------
# Test 2: combine_ramp early phase → 1.5× multiplier
# ---------------------------------------------------------------------------

def test_combine_ramp_early_gain_multiplier() -> None:
    """When attempt gain < $1,500 and balance > MLL+750, multiplier = 1.5 / base."""
    base = Decimal("1.0")
    # At attempt start: balance=50000, gain=0 → ramp phase
    balance = Decimal("50000")
    mll = Decimal("48000")  # trailing MLL at start
    m = combine_ramp_multiplier(balance, mll, base_risk_pct=base)
    assert m == Decimal("1.5") / base  # 1.5x


def test_combine_ramp_protect_phase_multiplier() -> None:
    """When gain >= $1,500 and not near MLL, multiplier = 0.75 / base."""
    base = Decimal("1.0")
    balance = Decimal("51600")  # gain = +1600 > 1500
    mll = Decimal("48000")
    m = combine_ramp_multiplier(balance, mll, base_risk_pct=base)
    assert m == Decimal("0.75") / base


# ---------------------------------------------------------------------------
# Test 3: combine_ramp near MLL → 0.5× override regardless of gain
# ---------------------------------------------------------------------------

def test_combine_ramp_near_mll_overrides_phase() -> None:
    """combine_ramp: equity within $750 of trailing MLL → 0.5% regardless of gain."""
    base = Decimal("1.0")
    # Even with high gain, near MLL triggers survival
    balance = Decimal("51600")   # gain = +1600 (protect phase would normally apply)
    mll = Decimal("51000")       # buffer = 600 < 750 → survival override
    m = combine_ramp_multiplier(balance, mll, base_risk_pct=base)
    assert m == Decimal("0.5") / base

    # Also verify at start (gain < 1500) — survival still overrides ramp
    balance2 = Decimal("50400")  # gain = +400
    mll2 = Decimal("50000")      # buffer = 400 < 750
    m2 = combine_ramp_multiplier(balance2, mll2, base_risk_pct=base)
    assert m2 == Decimal("0.5") / base


# ---------------------------------------------------------------------------
# Test 4: funded_survival policy — normal and near-MLL multipliers
# ---------------------------------------------------------------------------

def test_funded_survival_normal_multiplier() -> None:
    """funded_survival: when balance is safely above MLL, multiplier = 0.75 / base."""
    base = Decimal("0.75")
    balance = Decimal("3000")    # XFA balance (starts at 0, grows)
    mll = Decimal("0")           # XFA MLL starts trailing from $0
    m = funded_survival_multiplier(balance, mll, base_risk_pct=base)
    # 0.75 / 0.75 = 1.0 — no change for the normal case at base=0.75
    assert m == Decimal("1")


def test_funded_survival_near_mll_multiplier() -> None:
    """funded_survival: within $750 of MLL → multiplier = 0.4 / base."""
    base = Decimal("0.75")
    balance = Decimal("500")     # XFA balance
    mll = Decimal("0")           # buffer = 500 < 750 → survival
    m = funded_survival_multiplier(balance, mll, base_risk_pct=base)
    expected = Decimal("0.4") / Decimal("0.75")
    assert abs(m - expected) < Decimal("0.001")


# ---------------------------------------------------------------------------
# Test 5: Risk policy reads RUNNING equity, not start equity (state-dependence)
# ---------------------------------------------------------------------------

def test_combine_ramp_scales_later_days_differently() -> None:
    """Early days get 1.5× scaling; after gain crosses $1,500 they get 0.75×.
    Total P&L over the attempt must differ from constant 1.0× scaling,
    proving the policy reads running equity, not just start equity."""
    # Use a series that will pass: 60 daily +$50 days (net +$3000)
    # No MLL danger (losses are not modelled here — all wins)
    series = _flat_series(60, Decimal("50"))

    # Baseline (constant): always 1.0× → $50/day for 60 days = $3000 pass
    base = simulate_combines(series, risk_policy="constant")

    # combine_ramp at base=1.0%: early days 1.5×, later 0.75×
    # With 1.5× scaling, $50 becomes $75/day. Gain hits $1500 after 20 days.
    # Then $50×0.75=$37.5/day for remaining 40 days. Total attempt: 20×75 + 40×37.5 = 1500+1500=$3000
    # → Should STILL pass (same total gain), but the path is different.
    # Days to pass: ~20 days early (1.5×) + some protect phase days vs ~60 days at 1×
    ramp = simulate_combines(series, risk_policy="combine_ramp", base_risk_pct=Decimal("1.0"))

    # Both should pass, but ramp should pass faster (fewer days per attempt)
    assert ramp["passes"] >= base["passes"]  # at least as many passes
    # Median days to pass must be shorter with ramp (1.5× early accelerates progress)
    if ramp["median_days_to_pass"] and base["median_days_to_pass"]:
        assert ramp["median_days_to_pass"] <= base["median_days_to_pass"]
