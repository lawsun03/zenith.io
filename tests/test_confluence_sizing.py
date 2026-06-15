"""
B58 — Confluence-weighted sizing defining-behavior tests.

Four required tests per spec:
1. risk_policy != "confluence" (default): sizing unchanged.
2. count>=3 → 1.5x; count==2 → 1.0x; count<=1 → 0.5x ladder.
3. No trade suppressed: all confluence_count values reach place_bracket (additive).
4. 1.5x up-sized trade respects per-trade risk cap (no MLL breach).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.bot_config import StrategyParams
from app.broker.paper import PaperBroker
from app.execution.engine import ExecutionEngine, _confluence_multiplier
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState
from app.strategy.composer import Signal
from tests.test_engine import in_ny_am, make_runner

D = Decimal


def _signal_with_count(count: int, side: str = "long") -> Signal:
    """Signal with a 3pt stop (easy math) and given confluence_count."""
    return Signal(
        instrument="MGC",
        side=side,
        entry=D("4500"),
        stop=D("4497") if side == "long" else D("4503"),
        target=D("4506") if side == "long" else D("4494"),
        created_at=in_ny_am(0),
        killzone="NY AM",
        sweep_pattern="B_one_bar",
        sweep_extreme=D("4497") if side == "long" else D("4503"),
        fvg_low=None,
        fvg_high=None,
        rationale="test",
        confluence_count=count,
    )


def _engine(risk_policy: str, base_contracts: int = 2) -> tuple[ExecutionEngine, RiskState]:
    rs = RiskState(config=fifty_k_combine())
    rs.mark_equity(D("50000"), in_ny_am(0))
    sp = StrategyParams(risk_policy=risk_policy)
    eng = ExecutionEngine(
        broker=PaperBroker(starting_balance=D("50000")),
        risk_state=rs,
        runners=[make_runner()],
        contracts=base_contracts,
        risk_per_trade_pct=D("0"),   # fixed-contracts mode for predictable math
        strategy_cfg=sp,
    )
    return eng, rs


# ── Test 1 ─────────────────────────────────────────────────────────────────
def test_constant_policy_sizing_unchanged():
    """risk_policy='constant' (default): _entry_size returns base contracts
    regardless of confluence_count, so no trade is mis-sized."""
    eng, _ = _engine("constant", base_contracts=2)
    for count in [0, 1, 2, 3, 4]:
        assert eng._entry_size(_signal_with_count(count)) == 2, (
            f"constant policy should always return 2, got != 2 at count={count}"
        )


# ── Test 2 ─────────────────────────────────────────────────────────────────
def test_confluence_multiplier_ladder():
    """B58 fixed ladder: count<=1 → 0.5x, count==2 → 1.0x, count>=3 → 1.5x.
    Also verifies _entry_size applies the ladder correctly in fixed-contracts mode."""
    # Module-level helper
    assert _confluence_multiplier(0) == D("0.5")
    assert _confluence_multiplier(1) == D("0.5")
    assert _confluence_multiplier(2) == D("1.0")
    assert _confluence_multiplier(3) == D("1.5")
    assert _confluence_multiplier(4) == D("1.5")

    eng, _ = _engine("confluence", base_contracts=2)
    # 2 × 0.5 = 1.0 → int 1
    assert eng._entry_size(_signal_with_count(0)) == 1
    assert eng._entry_size(_signal_with_count(1)) == 1
    # 2 × 1.0 = 2
    assert eng._entry_size(_signal_with_count(2)) == 2
    # 2 × 1.5 = 3
    assert eng._entry_size(_signal_with_count(3)) == 3
    assert eng._entry_size(_signal_with_count(4)) == 3


# ── Test 3 ─────────────────────────────────────────────────────────────────
def test_confluence_sizing_additive_no_suppression():
    """All signals — regardless of confluence_count — reach place_bracket.
    confluence_count is informational only; no signal is dropped by it."""
    placed_counts: list[int] = []

    async def _run(count: int) -> None:
        eng, rs = _engine("confluence", base_contracts=2)
        await eng.broker.connect()
        broker: PaperBroker = eng.broker  # type: ignore[assignment]
        signal = _signal_with_count(count)
        outcome = await eng._act_on_signal(signal)
        placed_counts.append(int(outcome.placed))

    for c in [0, 1, 2, 3]:
        asyncio.run(_run(c))

    # All 4 signals should have been placed — confluence only scales size, never gates.
    assert placed_counts == [1, 1, 1, 1], (
        f"All confluence_count values must place orders; got {placed_counts}"
    )


# ── Test 4 ─────────────────────────────────────────────────────────────────
def test_confluence_1p5x_respects_max_contracts_cap():
    """1.5x sizing is capped at effective_max (max_contracts from RiskConfig).
    Ensures no MLL breach from over-sizing beyond account cap."""
    # fifty_k_combine() has max_contracts=30, but we override via max_contracts_override.
    # Use base=4 contracts × 1.5 = 6 → should be capped at override of 4.
    rs = RiskState(config=fifty_k_combine())
    rs.mark_equity(D("50000"), in_ny_am(0))
    sp = StrategyParams(risk_policy="confluence")
    eng = ExecutionEngine(
        broker=PaperBroker(starting_balance=D("50000")),
        risk_state=rs,
        runners=[make_runner()],
        contracts=4,
        risk_per_trade_pct=D("0"),
        strategy_cfg=sp,
        max_contracts_override=4,   # cap at 4 contracts
    )
    size = eng._entry_size(_signal_with_count(3))  # count=3 → 1.5x → 4×1.5=6, capped→4
    assert size == 4, f"1.5x of 4 (=6) should be capped at override=4, got {size}"

    # Also verify a smaller base (2 × 1.5 = 3) still passes through when below cap.
    eng2, _ = _engine("confluence", base_contracts=2)
    size2 = eng2._entry_size(_signal_with_count(3))  # 2 × 1.5 = 3 < max_contracts(30)
    assert size2 == 3, f"2 × 1.5 = 3, no cap needed, got {size2}"
