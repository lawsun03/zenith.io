"""
Execution engine tests.

Two layers:
  1. Unit tests on the engine wiring — does it call the right things in
     the right order, lock properly, react to lockouts, etc.
  2. End-to-end replay — feed a hand-crafted /MGC bar sequence in, watch
     the engine drive a winning trade and a losing trade through paper.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.broker.events import Bar, MarkToMarket
from app.broker.paper import PaperBroker
from app.execution.engine import (
    ExecutionEngine,
    OrderOutcome,
    SignalEmitted,
    StrategyRunner,
)
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker

ET = ZoneInfo("America/New_York")


def bar(
    ts: datetime,
    o: str, h: str, l: str, c: str,
    instrument: str = "MGC",
) -> Bar:
    return Bar(
        instrument=instrument,
        timeframe="1min",
        ts=ts,
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=100,
    )


def in_ny_am(minute_offset: int) -> datetime:
    """Build a UTC ts that falls inside NY AM (8:30–11:00 ET)."""
    base_et = datetime(2026, 5, 11, 9, 0, tzinfo=ET)
    return (base_et + timedelta(minutes=minute_offset)).astimezone(timezone.utc)


def make_runner(instrument: str = "MGC") -> StrategyRunner:
    """Default-config runner matching the strategy test settings."""
    return StrategyRunner(
        instrument=instrument,
        liquidity=LiquidityTracker(LiquidityConfig(
            swing_lookback=2, min_penetration=Decimal("0.20"),
        )),
        displacement=DisplacementDetector(DisplacementConfig(
            atr_period=5,
            body_atr_multiple=Decimal("1.0"),
            min_body_to_range_ratio=Decimal("0.5"),
            min_absolute_body=Decimal("0.5"),
        )),
        composer=SweepDisplacementComposer(ComposerConfig(
            instrument=instrument,
            displacement_window_bars=5,
            stop_buffer=Decimal("0.30"),
            r_multiple=Decimal("2.0"),
        )),
    )


# Bar sequence designed to fire a SHORT signal in NY AM.
# Same scenario as test_strategy.py but reused here for the engine.
SHORT_SIGNAL_BARS = [
    # Warmup (5 bars) — quiet, builds ATR.
    ("2400", "2400.4", "2399.6", "2400.1"),
    ("2400.1", "2400.5", "2399.8", "2400.2"),
    ("2400.2", "2400.6", "2399.9", "2400.3"),
    ("2400.3", "2400.7", "2400", "2400.4"),
    ("2400.4", "2400.8", "2400.1", "2400.5"),
    # Build swing high.
    ("2400.5", "2401", "2400.3", "2400.8"),
    ("2400.8", "2403", "2400.5", "2402.5"),
    ("2402.5", "2402.8", "2401.5", "2401.8"),
    ("2401.8", "2402.5", "2401", "2401.5"),
    # Pattern B sweep of 2403.
    ("2401.5", "2403.5", "2401", "2401.5"),
    # FVG window: b1, b2 (displacement), b3 (gaps down) → bearish FVG.
    ("2401.5", "2401.7", "2400.8", "2401"),
    ("2401", "2401.2", "2398.4", "2398.5"),
    ("2398.5", "2398.3", "2397", "2397.5"),
]


# =====================================================================
# Engine wiring
# =====================================================================

async def test_engine_starts_and_registers_handlers():
    """After start(), the broker must have our three handlers."""
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    engine = ExecutionEngine(broker, state, [make_runner()])

    await broker.connect()
    await engine.start()
    # PaperBroker uses lists for handlers. Inspecting them is fine for tests.
    assert len(broker._bar_handlers) == 1
    assert len(broker._fill_handlers) == 1
    assert len(broker._equity_handlers) == 1
    await engine.stop()


async def test_engine_idempotent_start():
    """Calling start twice does not double-register handlers."""
    broker = PaperBroker()
    state = RiskState(config=fifty_k_combine())
    engine = ExecutionEngine(broker, state, [make_runner()])

    await broker.connect()
    await engine.start()
    await engine.start()
    assert len(broker._bar_handlers) == 1
    await engine.stop()


# =====================================================================
# Full replay — winning trade
# =====================================================================

async def test_full_replay_short_signal_to_target_hit():
    """
    Drive the engine through a real bar sequence:
      - Strategy fires a SHORT signal
      - Risk gate allows it
      - Broker places the bracket
      - Subsequent bars hit the target
      - Account ends up with realized profit
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())

    captured_signals: list[Signal] = []
    captured_outcomes: list[OrderOutcome] = []

    async def journal(sig: Signal, out: OrderOutcome) -> None:
        captured_signals.append(sig)
        captured_outcomes.append(out)

    engine = ExecutionEngine(broker, state, [make_runner()], on_signal=journal, replay_mode=True)
    await broker.connect()
    await engine.start()

    # Replay the signal-generating sequence.
    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        ts = in_ny_am(i)
        await broker.inject_bar(bar(ts, o, h, l, c))
        await asyncio.sleep(0)  # let handlers run

    # We should have exactly one signal, placed.
    assert len(captured_signals) == 1
    assert captured_signals[0].side == "short"
    assert captured_outcomes[0].placed is True
    assert captured_outcomes[0].reason == "allowed"

    # We're SHORT now. Feed a bar that hits the target.
    sig = captured_signals[0]
    target = sig.target
    # Bar that prints below target → take-profit fills.
    target_hit_bar = bar(
        in_ny_am(len(SHORT_SIGNAL_BARS)),
        str(target + Decimal("0.5")),
        str(target + Decimal("0.5")),
        str(target - Decimal("0.5")),
        str(target),
    )
    await broker.inject_bar(target_hit_bar)
    await asyncio.sleep(0)

    # Position closed, profit realized, no lockout.
    assert state.open_contracts == 0
    assert state.daily_pnl > Decimal("0")
    assert state.locked_out is None

    await engine.stop()


# =====================================================================
# Full replay — losing trade and lockout flatten
# =====================================================================

async def test_full_replay_losing_trade_does_not_lock_account():
    """
    Same signal, but bars hit the stop instead of the target.
    Account loses 1R but doesn't breach DLL or MLL on a single trade.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    engine = ExecutionEngine(broker, state, [make_runner()], replay_mode=True)
    await broker.connect()
    await engine.start()

    captured: list[Signal] = []
    async def cap(sig: Signal, _) -> None:
        captured.append(sig)
    engine.on_signal = cap

    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        await broker.inject_bar(bar(in_ny_am(i), o, h, l, c))
        await asyncio.sleep(0)

    assert len(captured) == 1
    sig = captured[0]
    stop = sig.stop
    # Bar that prints above stop → stop fills.
    stop_hit_bar = bar(
        in_ny_am(len(SHORT_SIGNAL_BARS)),
        str(stop - Decimal("0.5")),
        str(stop + Decimal("0.5")),
        str(stop - Decimal("0.5")),
        str(stop),
    )
    await broker.inject_bar(stop_hit_bar)
    await asyncio.sleep(0)

    assert state.open_contracts == 0
    assert state.daily_pnl < Decimal("0")
    # One losing 1R trade should not trigger DLL ($1,000) on a $50K account.
    assert state.locked_out is None

    await engine.stop()


# =====================================================================
# Lockout transition flattens open position
# =====================================================================

async def test_lockout_mid_position_triggers_flatten():
    """
    Set up a position, then push equity below the soft buffer via a
    direct mark. Engine must detect the lockout transition and flatten
    the position via the broker, not wait for the bracket to fill.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine(soft_buffer=Decimal("500")))
    engine = ExecutionEngine(broker, state, [make_runner()], replay_mode=True)
    await broker.connect()
    await engine.start()

    # Seed equity high so the high-water doesn't sit at 50000 forever.
    await broker._fanout(
        broker._equity_handlers,
        MarkToMarket(ts=in_ny_am(0), equity=Decimal("50000")),
    )
    await asyncio.sleep(0)

    # Manually open a position via the broker.
    await broker.place_bracket(
        instrument="MGC",
        side="long",
        size=1,
        entry=Decimal("2400"),
        stop=Decimal("2390"),
        target=Decimal("2410"),
    )
    await asyncio.sleep(0)
    assert state.open_contracts == 1

    # Push equity to $48,400 — below the $48,500 soft-buffer line.
    bad_mtm = MarkToMarket(ts=in_ny_am(1), equity=Decimal("48400"))
    await broker._fanout(broker._equity_handlers, bad_mtm)
    await asyncio.sleep(0)

    # Engine should have flattened.
    assert state.locked_out is not None
    positions = await broker.get_positions()
    assert positions == [], f"Engine did not flatten: {positions!r}"

    await engine.stop()


# =====================================================================
# Bar concurrency — fills landing during gate eval don't corrupt state
# =====================================================================

async def test_signal_denied_when_already_at_max_contracts():
    """
    If we've already opened a position elsewhere (max_contracts reached
    via direct broker calls), a new signal hits the gate at MAX_CONTRACTS
    and is denied — the broker is NEVER asked to place.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())  # max=30
    runner = make_runner()
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True)
    await broker.connect()
    await engine.start()

    # Pre-fill state to 30 open contracts (max for $50K Combine).
    state.record_fill(
        realized_pnl_delta=Decimal("0"),
        contracts_delta=30,
        ts=in_ny_am(0),
    )

    captured: list[OrderOutcome] = []
    async def cap(_, out: OrderOutcome) -> None:
        captured.append(out)
    engine.on_signal = cap

    # Drive bars; signal will fire but gate denies.
    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        await broker.inject_bar(bar(in_ny_am(i), o, h, l, c))
        await asyncio.sleep(0)

    assert len(captured) == 1
    assert captured[0].placed is False
    assert captured[0].reason == "MAX_CONTRACTS"

    await engine.stop()


# =====================================================================
# VP disabled bypasses gate
# =====================================================================

@pytest.mark.asyncio
async def test_vp_disabled_bypasses_gate():
    """
    When vp_enabled=False, signals must reach the broker even if the VP filter
    would reject them (entry outside value area).
    """
    from app.strategy.volume_profile import VolumeProfileTracker, VolumeProfile
    from app.bot_config import StrategyParams
    from decimal import Decimal
    from datetime import date

    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())

    # Build runner with a VP tracker that has a prior profile loaded.
    runner = make_runner()
    runner.vp = VolumeProfileTracker()
    # Inject a profile where long above 1910 would be rejected (VAH=1905, tol=2.0).
    runner.vp._prior = VolumeProfile(
        session_date=date(2026, 5, 18),
        poc=Decimal("1900"), vah=Decimal("1905"), val=Decimal("1895"),
        hvns=[], total_volume=1000,
    )

    # vp_enabled=False — VP filter must be completely bypassed.
    cfg = StrategyParams(vp_enabled=False)
    engine = ExecutionEngine(
        broker, state, [runner],
        strategy_cfg=cfg,
        replay_mode=True,
    )
    await broker.connect()
    await engine.start()

    captured: list[OrderOutcome] = []
    async def cap(sig, out: OrderOutcome) -> None:
        captured.append(out)
    engine.on_signal = cap

    # SHORT_SIGNAL_BARS generate a short signal with entry near 1902 — inside the VA.
    # But we want to confirm ANY signal passes through. Drive the short signal bars.
    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        await broker.inject_bar(bar(in_ny_am(i), o, h, l, c))
        await asyncio.sleep(0)

    # The signal must have reached the broker (placed=True or denied by risk, not VP).
    assert len(captured) >= 1
    # Specifically: the denial reason must NOT be VP-related (VP doesn't log here,
    # it just returns None from apply()). The broker either placed or denied for risk.
    # The key assertion: if VP were active, apply() returns None and on_signal is never called.
    # Since vp_enabled=False, on_signal WAS called, which is what we verify above.
