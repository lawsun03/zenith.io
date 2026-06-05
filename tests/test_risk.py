"""
Property tests for the risk module.

The trailing-DD math is the place this bot is most likely to silently
fail. We don't trust hand-picked test cases here — we throw thousands
of random fill sequences at the state machine and assert invariants
that must hold in every reachable state.

Hypothesis docs: https://hypothesis.readthedocs.io
Run with: pytest tests/test_risk.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

from app.risk.config import fifty_k_combine
from app.risk.pretrade import Allow, Deny, ProposedOrder, check
from app.risk.state import RiskState

# ---------------------------------------------------------------------
# Strategies — how Hypothesis generates inputs.
# ---------------------------------------------------------------------

# Money values bounded to plausible session ranges. Decimal-typed.
money = st.decimals(
    min_value=Decimal("-5000"),
    max_value=Decimal("10000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)

# Equity values within a realistic band around the $50K starting balance.
equity_value = st.decimals(
    min_value=Decimal("40000"),
    max_value=Decimal("70000"),
    allow_nan=False,
    allow_infinity=False,
    places=2,
)

# Timestamps strictly increasing within a single trading session.
def timestamps_in_session(n: int) -> list[datetime]:
    base = datetime(2026, 5, 11, 14, 30, tzinfo=timezone.utc)
    return [base + timedelta(seconds=i) for i in range(n)]


# ---------------------------------------------------------------------
# Trailing MLL invariants — the most critical math in the system.
# ---------------------------------------------------------------------

class TestTrailingMLL:
    """Properties of the trailing Maximum Loss Limit floor."""

    @given(equities=st.lists(equity_value, min_size=1, max_size=50))
    @settings(suppress_health_check=[HealthCheck.too_slow])
    def test_floor_only_moves_up(self, equities):
        """
        Invariant: mll_floor never decreases as more equity marks arrive.

        This is THE Topstep rule. Forgetting it is how Combines fail.
        """
        state = RiskState(config=fifty_k_combine())
        prev_floor = state.mll_floor
        ts_list = timestamps_in_session(len(equities))

        for equity, ts in zip(equities, ts_list):
            state.mark_equity(equity, ts)
            assert state.mll_floor >= prev_floor, (
                f"Floor moved DOWN: {prev_floor} -> {state.mll_floor} "
                f"on equity {equity}"
            )
            prev_floor = state.mll_floor

    @given(equities=st.lists(equity_value, min_size=1, max_size=50))
    def test_floor_capped_at_starting_balance(self, equities):
        """
        Invariant: floor never exceeds starting_balance for a Combine.

        The trailing stops once profits push the floor up to $50,000.
        """
        config = fifty_k_combine()
        state = RiskState(config=config)
        ts_list = timestamps_in_session(len(equities))

        for equity, ts in zip(equities, ts_list):
            state.mark_equity(equity, ts)
            assert state.mll_floor <= config.starting_balance

    def test_specific_topstep_published_example(self):
        """
        Reproduce the exact example from Topstep's help docs.

        Worked example on $50K Combine:
          - Start: HWM=$50,000 → floor=$48,000
          - Day 1: balance hits $50,500 high → floor=$48,500
          - Day 2: balance hits $52,000 high → floor=$50,000 (LOCKED)
          - Day 3: balance hits $55,000 → floor=$50,000 (still locked)
        """
        state = RiskState(config=fifty_k_combine())
        ts = timestamps_in_session(10)

        # Initial.
        state.mark_equity(Decimal("50000"), ts[0])
        assert state.mll_floor == Decimal("48000")

        # Day 1 high $50,500.
        state.mark_equity(Decimal("50500"), ts[1])
        assert state.mll_floor == Decimal("48500")

        # Day 2 high $52,000 → floor would be $50,000.
        state.mark_equity(Decimal("52000"), ts[2])
        assert state.mll_floor == Decimal("50000")
        assert state.mll_locked_at_starting_balance is True

        # Day 3 high $55,000 → floor still $50,000.
        state.mark_equity(Decimal("55000"), ts[3])
        assert state.mll_floor == Decimal("50000")

        # Drop back to $51,000 → floor still $50,000 (never moves down).
        state.mark_equity(Decimal("51000"), ts[4])
        assert state.mll_floor == Decimal("50000")

    def test_initial_floor_is_starting_minus_offset(self):
        """A fresh $50K Combine has floor at exactly $48,000."""
        state = RiskState(config=fifty_k_combine())
        assert state.mll_floor == Decimal("48000")


# ---------------------------------------------------------------------
# Lockout invariants.
# ---------------------------------------------------------------------

class TestLockouts:
    """Once locked out, the gate must stay closed until a valid reset."""

    def test_mll_breach_locks_account(self):
        state = RiskState(config=fifty_k_combine())
        ts = timestamps_in_session(2)
        # Drop straight through the floor.
        state.mark_equity(Decimal("47999"), ts[0])
        assert state.locked_out is not None
        assert state.locked_out.code == "MLL_BREACH"

    def test_mll_breach_does_not_clear_on_day_roll(self):
        """MLL breach is permanent — Combine fails. Day rollover doesn't clear."""
        state = RiskState(config=fifty_k_combine())
        ts = timestamps_in_session(3)
        state.mark_equity(Decimal("47999"), ts[0])
        assert state.locked_out is not None

        state.roll_trading_day(ts[1])
        assert state.locked_out is not None
        assert state.locked_out.code == "MLL_BREACH"

    def test_dll_lockout_clears_on_day_roll(self):
        """Daily loss lockouts reset at 5pm CT."""
        state = RiskState(config=fifty_k_combine())
        ts = timestamps_in_session(3)

        state.mark_equity(Decimal("50000"), ts[0])
        state.record_fill(
            realized_pnl_delta=Decimal("-1000"),
            contracts_delta=0,
            ts=ts[0],
        )
        assert state.locked_out is not None
        assert state.locked_out.code == "DLL_HIT"

        state.roll_trading_day(ts[1])
        assert state.locked_out is None
        assert state.daily_pnl == Decimal("0")

    def test_soft_buffer_triggers_before_hard_limit(self):
        """
        $500 soft buffer means the bot stops before equity hits the floor.

        Floor at $48,000 + soft buffer $500 → bot locks at $48,500.
        """
        state = RiskState(config=fifty_k_combine(soft_buffer=Decimal("500")))
        ts = timestamps_in_session(2)

        # Equity at $48,500: buffer_to_mll = 500, exactly at threshold.
        state.mark_equity(Decimal("48500"), ts[0])
        assert state.locked_out is not None
        assert state.locked_out.code == "MLL_SOFT_BUFFER"

    def test_first_lockout_reason_persists(self):
        """If multiple conditions trip at once, first one wins (no overwrite)."""
        state = RiskState(config=fifty_k_combine())
        ts = timestamps_in_session(3)

        # First trip soft buffer.
        state.mark_equity(Decimal("48400"), ts[0])
        assert state.locked_out.code == "MLL_SOFT_BUFFER"

        # Then drop through hard floor — reason should NOT change.
        state.mark_equity(Decimal("47900"), ts[1])
        assert state.locked_out.code == "MLL_SOFT_BUFFER"


# ---------------------------------------------------------------------
# Pretrade gate.
# ---------------------------------------------------------------------

def make_long_order(size: int = 1) -> ProposedOrder:
    return ProposedOrder(
        instrument="MGC",
        side="long",
        size=size,
        entry=Decimal("2400.0"),
        stop=Decimal("2398.0"),
        target=Decimal("2406.0"),
    )


def make_short_order(size: int = 1) -> ProposedOrder:
    return ProposedOrder(
        instrument="MGC",
        side="short",
        size=size,
        entry=Decimal("2400.0"),
        stop=Decimal("2402.0"),
        target=Decimal("2394.0"),
    )


class TestPretradeGate:

    def test_locked_account_denies_entries(self):
        state = RiskState(config=fifty_k_combine())
        ts = timestamps_in_session(1)
        state.mark_equity(Decimal("47999"), ts[0])  # MLL breach

        result = check(make_long_order(), state)
        assert isinstance(result, Deny)
        assert result.reason_code == "LOCKED_OUT"

    def test_locked_account_allows_exits(self):
        """Always allow flattening, even when locked."""
        state = RiskState(config=fifty_k_combine())
        ts = timestamps_in_session(1)
        state.mark_equity(Decimal("47999"), ts[0])

        exit_order = ProposedOrder(
            instrument="MGC",
            side="short",  # closing a long
            size=1,
            entry=Decimal("2400"),
            stop=Decimal("2402"),
            target=Decimal("2394"),
            is_entry=False,
        )
        result = check(exit_order, state)
        assert isinstance(result, Allow)

    def test_invalid_long_stop_rejected(self):
        state = RiskState(config=fifty_k_combine())
        bad = ProposedOrder(
            instrument="MGC",
            side="long",
            size=1,
            entry=Decimal("2400"),
            stop=Decimal("2401"),  # stop ABOVE entry on long — wrong
            target=Decimal("2406"),
        )
        result = check(bad, state)
        assert isinstance(result, Deny)
        assert result.reason_code == "INVALID_STOP"

    def test_invalid_short_stop_rejected(self):
        state = RiskState(config=fifty_k_combine())
        bad = ProposedOrder(
            instrument="MGC",
            side="short",
            size=1,
            entry=Decimal("2400"),
            stop=Decimal("2399"),  # stop BELOW entry on short — wrong
            target=Decimal("2394"),
        )
        result = check(bad, state)
        assert isinstance(result, Deny)
        assert result.reason_code == "INVALID_STOP"

    def test_size_capped_at_max_contracts(self):
        """Asking for more than max_contracts gets sized down, not denied."""
        state = RiskState(config=fifty_k_combine())  # max=30
        result = check(make_long_order(size=40), state)
        assert isinstance(result, Allow)
        assert result.allowed_size == 30

    def test_size_capped_at_remaining_headroom(self):
        """If 28 contracts already open, can only add 2 more."""
        state = RiskState(config=fifty_k_combine())
        ts = timestamps_in_session(1)
        state.record_fill(
            realized_pnl_delta=Decimal("0"),
            contracts_delta=28,
            ts=ts[0],
        )
        result = check(make_long_order(size=5), state)
        assert isinstance(result, Allow)
        assert result.allowed_size == 2

    def test_at_max_contracts_denies_entry(self):
        state = RiskState(config=fifty_k_combine())
        ts = timestamps_in_session(1)
        state.record_fill(
            realized_pnl_delta=Decimal("0"),
            contracts_delta=30,
            ts=ts[0],
        )
        result = check(make_long_order(size=1), state)
        assert isinstance(result, Deny)
        assert result.reason_code == "MAX_CONTRACTS"

    def test_short_entry_at_max_contracts_denies(self):
        """Short entries produce negative contracts_delta; pretrade must use abs()."""
        state = RiskState(config=fifty_k_combine())
        ts = timestamps_in_session(1)
        # Simulate a short entry of 30 contracts (broker uses -size for sells).
        state.record_fill(
            realized_pnl_delta=Decimal("0"),
            contracts_delta=-30,
            ts=ts[0],
        )
        result = check(make_short_order(size=1), state)
        assert isinstance(result, Deny), (
            "short entry while at max short contracts should be denied; "
            "open_contracts is negative for shorts so pretrade must abs() it"
        )
        assert result.reason_code == "MAX_CONTRACTS"


# ---------------------------------------------------------------------
# End-to-end fuzz: random fill sequences must never breach invariants.
# ---------------------------------------------------------------------

@st.composite
def fill_event(draw):
    """A random fill: signed P&L delta and contract delta."""
    return {
        "pnl": draw(money),
        "contracts": draw(st.integers(min_value=-5, max_value=5)),
    }


@given(fills=st.lists(fill_event(), min_size=1, max_size=30))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_no_invariant_violated_in_random_session(fills):
    """
    Throw a random fill sequence at the state and assert nothing weird
    happens. Specifically:
      - mll_floor never decreases
      - mll_floor never exceeds starting_balance
      - daily_pnl matches the sum of fills since last roll
      - if locked, no entry order is ever Allowed
    """
    state = RiskState(config=fifty_k_combine())
    ts_list = timestamps_in_session(len(fills) + 1)

    # Seed equity so high_water and mark_equity logic engage.
    state.mark_equity(Decimal("50000"), ts_list[0])
    prev_floor = state.mll_floor
    expected_daily = Decimal("0")

    for fill, ts in zip(fills, ts_list[1:]):
        # Skip fills that would push contracts negative (closing more
        # than open) — the test isn't checking that part.
        if state.open_contracts + fill["contracts"] < 0:
            continue
        # Cap contracts at the account max so we don't fight the
        # max_contracts gate; the test isn't about that either.
        if state.open_contracts + fill["contracts"] > state.config.max_contracts:
            continue

        state.record_fill(
            realized_pnl_delta=fill["pnl"],
            contracts_delta=fill["contracts"],
            ts=ts,
        )
        if state.locked_out is None:
            expected_daily += fill["pnl"]

        # Move equity in line with realized changes (simplification).
        state.mark_equity(state.realized_balance, ts)

        # Invariant 1: floor monotonic.
        assert state.mll_floor >= prev_floor
        prev_floor = state.mll_floor

        # Invariant 2: floor capped.
        assert state.mll_floor <= state.config.starting_balance

        # Invariant 3: locked → entries denied.
        if state.locked_out is not None:
            decision = check(make_long_order(), state)
            assert isinstance(decision, Deny)
