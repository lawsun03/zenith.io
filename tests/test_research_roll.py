"""Roll logic tests against a hand-verifiable synthetic GC fixture.

Real Databento data isn't available in this environment (no funded API key,
and pulling it isn't this test's job) — so the roll-date and back-adjustment
tests use a small synthetic two-contract fixture whose expected continuous
series is computed independently, by hand, right here in the test. The
acceptance bar ("within one tick at every roll date") is checked against
that independent calculation, not against the implementation's own output.

Bars are daily here purely so the fixture is easy to verify by eye — build_continuous
is granularity-agnostic and runs unchanged over 1-minute bars in production.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import polars as pl
import pytest

from research.data.roll import RollConfig, build_continuous, compute_roll_schedule

TICK = Decimal("0.10")


def _bars(prices: list[float], volumes: list[int], start: date) -> pl.DataFrame:
    ts = [datetime.combine(start + timedelta(days=i), datetime.min.time(), tzinfo=timezone.utc).replace(hour=16)
          for i in range(len(prices))]
    return pl.DataFrame({
        "ts": ts,
        "open": prices,
        "high": [p + 0.10 for p in prices],
        "low": [p - 0.10 for p in prices],
        "close": prices,
        "volume": volumes,
    })


# Front contract (A): 10 sessions, uptrend +0.50/day, volume rolling off.
A_CLOSE = [2000.00, 2000.50, 2001.00, 2001.50, 2002.00, 2002.50, 2003.00, 2003.50, 2004.00, 2004.50]
A_VOL   = [5000,    4800,    4600,    4200,     3800,     3200,     2600,     2000,     1400,     800]

# Back contract (B): same 10 sessions, +4.20 contango premium, volume ramping up.
B_PREMIUM = 4.20
B_CLOSE = [round(p + B_PREMIUM, 2) for p in A_CLOSE]
B_VOL   = [200, 400, 700, 1100, 1600, 2200, 2900, 3700, 4600, 5600]

START = date(2024, 11, 1)
# B_VOL first exceeds A_VOL on day index 6 (0-based) -> day7 (1-based session count).
EXPECTED_CROSSOVER = START + timedelta(days=6)


@pytest.fixture
def two_contracts() -> dict[str, pl.DataFrame]:
    return {
        "GCZ24": _bars(A_CLOSE, A_VOL, START),
        "GCG25": _bars(B_CLOSE, B_VOL, START),
    }


def test_crossover_date_matches_hand_computed_volume_crossing(two_contracts):
    events = compute_roll_schedule(two_contracts, ["GCZ24", "GCG25"], RollConfig(volume_offset_days=0))
    assert len(events) == 1
    assert events[0].crossover_date == EXPECTED_CROSSOVER
    assert events[0].roll_date == EXPECTED_CROSSOVER


def test_roll_offset_shifts_the_roll_date_not_the_crossover(two_contracts):
    events = compute_roll_schedule(two_contracts, ["GCZ24", "GCG25"], RollConfig(volume_offset_days=2))
    assert events[0].crossover_date == EXPECTED_CROSSOVER  # crossover itself is unchanged
    assert events[0].roll_date == EXPECTED_CROSSOVER + timedelta(days=2)


def test_unadjusted_series_is_a_raw_splice_with_a_real_gap(two_contracts):
    result = build_continuous(two_contracts, ["GCZ24", "GCG25"], RollConfig(volume_offset_days=0), TICK)
    unadj = result.unadjusted.sort("ts")

    roll_date = result.roll_events[0].roll_date
    day_before = unadj.filter(pl.col("ts").dt.date() < roll_date)[-1, "close"]
    day_of = unadj.filter(pl.col("ts").dt.date() == roll_date)[0, "close"]

    # day_before is A's raw close, day_of is B's raw close: the contract-switch
    # premium (4.20) really is visible in the unadjusted series.
    assert day_before == pytest.approx(A_CLOSE[5])
    assert day_of == pytest.approx(B_CLOSE[6])
    assert (day_of - day_before) == pytest.approx(B_PREMIUM + 0.50, abs=0.01)


def test_back_adjusted_series_reproduces_expected_continuous_prices_within_one_tick(two_contracts):
    """Hand-computed expectation: shift every pre-roll bar by the premium
    measured AT the roll date (B_close[roll] - A_close[roll]), leaving the
    back contract's own prices untouched."""
    result = build_continuous(two_contracts, ["GCZ24", "GCG25"], RollConfig(volume_offset_days=0), TICK)
    adj = result.back_adjusted.sort("ts")

    roll_idx = 6  # index into A_CLOSE/B_CLOSE for the roll date
    delta_at_roll = B_CLOSE[roll_idx] - A_CLOSE[roll_idx]

    expected = [round(p + delta_at_roll, 2) for p in A_CLOSE[:roll_idx]] + B_CLOSE[roll_idx:]

    got = adj["close"].to_list()
    assert len(got) == len(expected)
    for i, (g, e) in enumerate(zip(got, expected)):
        assert g == pytest.approx(e, abs=float(TICK)), f"bar {i}: {g} vs expected {e}"


def test_back_adjusted_series_has_no_gap_at_the_roll_date(two_contracts):
    """The whole point of back-adjustment: at the roll date, the two
    contracts' prices coincide within one tick — no artificial jump beyond
    ordinary day-to-day drift."""
    result = build_continuous(two_contracts, ["GCZ24", "GCG25"], RollConfig(volume_offset_days=0), TICK)
    adj = result.back_adjusted.sort("ts")
    roll_date = result.roll_events[0].roll_date

    before = adj.filter(pl.col("ts").dt.date() < roll_date)[-1, "close"]
    after = adj.filter(pl.col("ts").dt.date() == roll_date)[0, "close"]

    # Both underlying series trend +0.50/session; that ordinary drift should
    # be all that's left once the contract-switch jump is removed.
    assert (after - before) == pytest.approx(0.50, abs=float(TICK))


def test_back_adjusted_matches_at_every_roll_date_for_a_three_contract_chain():
    """Two rolls in sequence: assert the tick-level match holds at BOTH roll
    dates, not just a single-roll case."""
    c1_close = [100.0 + 0.1 * i for i in range(12)]
    c1_vol = [5000 - 400 * i for i in range(12)]
    c2_close = [102.0 + 0.1 * i for i in range(12)]  # +2.00 premium over c1
    c2_vol = [500 + 500 * i for i in range(12)]
    c3_close = [104.5 + 0.1 * i for i in range(12)]  # +2.50 premium over c2's own start... see below
    c3_vol = [50 + 40 * i for i in range(12)]
    # give c3 a later, sharper volume ramp so its crossover with c2 lands after c2's own roll
    c3_vol = [50] * 8 + [3000, 4000, 5000, 6000]

    start = date(2024, 6, 3)
    contracts = {
        "A": _bars(c1_close, c1_vol, start),
        "B": _bars(c2_close, c2_vol, start),
        "C": _bars(c3_close, c3_vol, start),
    }
    result = build_continuous(contracts, ["A", "B", "C"], RollConfig(volume_offset_days=0), TICK)
    assert len(result.roll_events) == 2

    adj = result.back_adjusted.sort("ts")
    for event in result.roll_events:
        before = adj.filter(pl.col("ts").dt.date() < event.roll_date)
        after = adj.filter(pl.col("ts").dt.date() >= event.roll_date)
        assert not before.is_empty() and not after.is_empty()
        gap = after[0, "close"] - before[-1, "close"]
        # Both chains drift +0.10/session; only that should remain at each roll.
        assert gap == pytest.approx(0.10, abs=float(TICK))


def test_unadjusted_and_back_adjusted_agree_on_the_current_contract():
    """Per CLAUDE.md — back-adjustment must never touch the most recent
    (currently active) contract's own prices."""
    contracts = {
        "GCZ24": _bars(A_CLOSE, A_VOL, START),
        "GCG25": _bars(B_CLOSE, B_VOL, START),
    }
    result = build_continuous(contracts, ["GCZ24", "GCG25"], RollConfig(volume_offset_days=0), TICK)
    roll_date = result.roll_events[0].roll_date

    unadj_tail = result.unadjusted.filter(pl.col("ts").dt.date() >= roll_date).sort("ts")["close"].to_list()
    adj_tail = result.back_adjusted.filter(pl.col("ts").dt.date() >= roll_date).sort("ts")["close"].to_list()
    assert unadj_tail == adj_tail
