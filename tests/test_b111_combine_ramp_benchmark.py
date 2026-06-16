"""
Defining-behavior test for B111: combine_ramp risk policy on Phase-A combine.

Tests that simulate_combines with risk_policy="combine_ramp" produces a
different pass count than risk_policy="constant" on a series where the 1.5x
ramp-phase multiplier materially changes the attempt progression.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.backtest.funded_sim import simulate_combines


def _series(daily_pnls: list[int | float]) -> list[tuple[datetime, Decimal]]:
    base = datetime(2024, 1, 2, 16, 0, 0, tzinfo=timezone.utc)
    return [(base + timedelta(days=i), Decimal(str(v))) for i, v in enumerate(daily_pnls)]


def test_combine_ramp_different_pass_count_from_constant() -> None:
    """combine_ramp and constant policies produce different pass counts on a
    mixed-P&L series, proving the risk_policy argument is wired through.

    Series: [+500, +500, +500, +500, -1600] × 7 (35 days).

    With constant (1×):
      - Passes on day 19 (balance peaks above $53k mid-run).
      - 1 pass total in 35 days.

    With combine_ramp (base_risk_pct=1.0, ramp=1.5×, protect=0.75×):
      - Attempt 1: ramp accelerates progress; passes on day 14.
      - Attempt 2: starts day 15 (a -1600 loss day); ramp 1.5× amplifies to
        -2400 → balance $47600 < MLL $48000 → BUST.
      - Attempt 3: starts day 16; passes on day 29.
      - 2 passes, 1 bust in 35 days.

    The different pass count proves the multiplier is applied and the policy
    code path in simulate_combines() is live.
    """
    pnl_cycle = [500, 500, 500, 500, -1600]
    series = _series(pnl_cycle * 7)

    c_const = simulate_combines(series, risk_policy="constant")
    c_ramp = simulate_combines(series, risk_policy="combine_ramp",
                               base_risk_pct=Decimal("1.0"))

    assert c_const["passes"] == 1
    assert c_ramp["passes"] == 2
    assert c_ramp["busts"] >= 1
    assert c_const["passes"] != c_ramp["passes"], (
        f"combine_ramp should produce a different pass count: "
        f"constant={c_const['passes']} ramp={c_ramp['passes']}"
    )
