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

from app.bot_config import StrategyParams
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
from app.strategy.grader import SetupGrader
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
    """Default-config runner matching the strategy test settings.

    Session filter is disabled (empty list) so the hand-crafted bar timestamps
    always pass. Grader is seeded with HTF swings that bracket the test price
    range so target clarity passes — the engine tests verify plumbing, not
    grader quality criteria.
    """
    grader = SetupGrader()
    # Seed HTF swings around the test price range (~2392–2404) so the grader's
    # target-clarity and premium/discount checks can resolve. Without these the
    # grader grades every test signal "B" (no target) and blocks it, which would
    # break all engine tests that rely on signals reaching the broker.
    grader.update_htf_swings(
        highs=[Decimal("2410"), Decimal("2405")],
        lows=[Decimal("2385"), Decimal("2390")],  # max(lows_below ~2399.5) = 2390 → htf_mid=(2405+2390)/2=2397.5
    )
    # Seed a session range for "NY AM" so P/D check has a midpoint.
    # Short signal entry is ~2399.5. Session low set to 2385 → sess_mid=(2410+2385)/2=2397.5.
    # Entry 2399.5 > 2397.5 (sess_mid) AND > 2397.5 (htf_mid) → short is in premium. Passes P/D.
    from app.broker.events import Bar as _Bar
    _seed_bar = _Bar(
        instrument=instrument, timeframe="1min", ts=in_ny_am(0),
        open=Decimal("2400"), high=Decimal("2410"), low=Decimal("2385"), close=Decimal("2400"),
        volume=100,
    )
    grader.update_session_range(_seed_bar, "NY AM")
    # Seed a synthetic 30min bearish FVG covering [2398.0, 2401.5] so the grader's
    # _htf_singularity_rescue can contain the gapping-sack cluster.
    # SHORT_SIGNAL_BARS at bar 15 produces: signal iFVG [2400.2, 2400.5] and a
    # same-side bearish overlap [2398.3, 2400.8] → cluster [2398.3, 2400.8].
    # The rescue FVG must satisfy: low <= 2398.3 and high >= 2400.8.
    # Bearish 3-bar FVG: b1.low > 2400.8, b3.high < 2398.3.
    _b1 = _Bar(instrument=instrument, timeframe="30min", ts=in_ny_am(0),
               open=Decimal("2403"), high=Decimal("2405"), low=Decimal("2402"), close=Decimal("2402"),
               volume=100)
    _b2 = _Bar(instrument=instrument, timeframe="30min", ts=in_ny_am(30),
               open=Decimal("2401"), high=Decimal("2401"), low=Decimal("2399"), close=Decimal("2400"),
               volume=100)
    _b3 = _Bar(instrument=instrument, timeframe="30min", ts=in_ny_am(60),
               open=Decimal("2398"), high=Decimal("2398.0"), low=Decimal("2396"), close=Decimal("2397"),
               volume=100)
    grader.update_delivery_fvgs([_b1, _b2, _b3])
    return StrategyRunner(
        instrument=instrument,
        timeframe="1min",
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
        grader=grader,
        strategy_cfg=StrategyParams(ifvg_session_windows=[], ifvg_entry_mode="close"),
    )


# Bar sequence designed to fire a SHORT signal in NY AM.
# Same scenario as test_strategy.py but reused here for the engine.
# Includes a prior bullish FVG [2399.0, 2399.5] (bars 5-7) so the
# bearish displacement bar (bar 14, close=2398.5 < fvg.low=2400.2) can
# perform an iFVG inversion and emit a signal via the composer.
SHORT_SIGNAL_BARS = [
    # Warmup (5 bars) — quiet, builds ATR.
    ("2400", "2400.4", "2399.6", "2400.1"),    # 0
    ("2400.1", "2400.5", "2399.8", "2400.2"),  # 1
    ("2400.2", "2400.6", "2399.9", "2400.3"),  # 2
    ("2400.3", "2400.7", "2400", "2400.4"),    # 3
    ("2400.4", "2400.8", "2400.1", "2400.5"),  # 4
    # 3 bars forming a prior bullish FVG [2399.0, 2399.5].
    # All subsequent bars have low > 2399.0 until the displacement bar,
    # so this FVG accumulates more bullish FVGs without being mitigated.
    ("2399.5", "2399.0", "2398.5", "2398.8"),  # 5 b1: high=2399.0
    ("2398.8", "2399.2", "2398.7", "2399.0"),  # 6 middle
    ("2399.0", "2400.2", "2399.5", "2400.0"),  # 7 b3: low=2399.5 > 2399.0 → bullish FVG
    # Build swing high.
    ("2400.5", "2401", "2400.3", "2400.8"),    # 8
    ("2400.8", "2403", "2400.5", "2402.5"),    # 9 SWING HIGH candidate
    ("2402.5", "2402.8", "2401.5", "2401.8"),  # 10
    ("2401.8", "2402.5", "2401", "2401.5"),    # 11 confirms swing high
    # Pattern B sweep of 2403.
    ("2401.5", "2403.5", "2401", "2401.5"),    # 12 SWEEP
    # Displacement window: b1, b2 (displacement), b3.
    ("2401.5", "2401.7", "2400.8", "2401"),    # 13 b1
    # Bearish displacement (b2): close=2398.5 < fvg.low of most recent bullish FVG → iFVG.
    ("2401", "2401.2", "2398.4", "2398.5"),    # 14 DISPLACE
    ("2398.5", "2398.3", "2397", "2397.5"),    # 15 b3
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


def test_runner_records_grader_b_rejection():
    """An unseeded grader grades the test short 'B' (no structural target); the
    runner records it as last_reject on the rejecting bar so the engine can surface
    it to the rejection ledger. last_reject is cleared each bar — capture per-bar,
    exactly as ExecutionEngine._handle_bar reads it right after on_bar()."""
    runner = make_runner()
    runner.grader = SetupGrader()  # fresh/unseeded -> grader-B (no target) -> reject
    rejects = []
    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        runner.on_bar(bar(in_ny_am(i), o, h, l, c))
        if runner.last_reject is not None:
            rejects.append(runner.last_reject)
    assert rejects, "expected a grader-B rejection to be recorded"
    assert rejects[-1].reason.startswith("grader_")
    assert rejects[-1].side == "short"


async def test_engine_invokes_on_reject_on_grader_b():
    """A runner-internal rejection (grader-B) with no placed signal is surfaced
    to the engine's on_reject callback (replay_mode keeps is_stale False)."""
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    runner.grader = SetupGrader()  # unseeded -> grader-B
    captured: list[tuple[str, str, str]] = []

    async def on_reject(info, instrument: str) -> None:
        captured.append((instrument, info.reason, info.side))

    engine = ExecutionEngine(broker, state, [runner], on_reject=on_reject, replay_mode=True)
    await broker.connect()
    await engine.start()
    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        await broker.inject_bar(bar(in_ny_am(i), o, h, l, c))
        await asyncio.sleep(0)
    await engine.stop()
    assert any(r.startswith("grader_") for _, r, _ in captured), captured


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
    If we're already at max contracts in the SAME direction as the signal,
    the gate denies with MAX_CONTRACTS and the broker is never asked to place.

    When the new signal is OPPOSITE to the open position (which is what
    SHORT_SIGNAL_BARS produces against a long position), the engine instead
    initiates a reversal flatten — verified in the reversal path test.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())  # max=30
    runner = make_runner()
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True)
    await broker.connect()
    await engine.start()

    # Pre-fill state to 30 short contracts — same direction as the SHORT signal.
    # With 30 shorts open, any additional short is denied by MAX_CONTRACTS.
    state.record_fill(
        realized_pnl_delta=Decimal("0"),
        contracts_delta=-30,
        ts=in_ny_am(0),
    )

    captured: list[OrderOutcome] = []
    async def cap(_, out: OrderOutcome) -> None:
        captured.append(out)
    engine.on_signal = cap

    # Drive bars; signal will fire but gate denies (same-side at max).
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


def _signal(entry: str, stop: str, target: str, side: str = "long") -> Signal:
    return Signal(
        instrument="MGC", side=side,
        entry=Decimal(entry), stop=Decimal(stop), target=Decimal(target),
        created_at=in_ny_am(0), killzone="NY AM", sweep_pattern="B_one_bar",
        sweep_extreme=Decimal(stop), fvg_low=None, fvg_high=None, rationale="test",
    )


def _engine_with_pct(pct: str) -> tuple[ExecutionEngine, RiskState]:
    rs = RiskState(config=fifty_k_combine())  # starting_balance 50000, max_contracts 30
    eng = ExecutionEngine(
        broker=PaperBroker(),
        risk_state=rs,
        runners=[make_runner()],
        contracts=4,
        risk_per_trade_pct=Decimal(pct),
    )
    return eng, rs


def test_entry_size_wide_stop_caps_at_one():
    """10.8pt stop at $50k / 0.25% ($125 budget, $108/contract) -> 1 contract."""
    eng, rs = _engine_with_pct("0.25")
    rs.mark_equity(Decimal("50000"), in_ny_am(0))
    assert eng._entry_size(_signal("4514.8", "4504.0", "4541.8")) == 1


def test_entry_size_normal_stop():
    """3.0pt stop -> $30/contract; floor(125/30)=4 contracts."""
    eng, rs = _engine_with_pct("0.25")
    rs.mark_equity(Decimal("50000"), in_ny_am(0))
    assert eng._entry_size(_signal("4500.0", "4497.0", "4509.0")) == 4


def test_entry_size_disabled_uses_fixed_contracts():
    """risk_per_trade_pct=0 -> fall back to fixed contracts (4)."""
    eng, rs = _engine_with_pct("0")
    rs.mark_equity(Decimal("50000"), in_ny_am(0))
    assert eng._entry_size(_signal("4514.8", "4504.0", "4541.8")) == 4


def test_entry_size_equity_fallback_before_first_tick():
    """Before any mark_equity tick, current_equity is 0; fall back to realized_balance."""
    eng, rs = _engine_with_pct("0.25")
    # no mark_equity call -> _current_equity == 0, realized_balance == 50000
    assert eng._entry_size(_signal("4500.0", "4497.0", "4509.0")) == 4


# =====================================================================
# HTF confluence — bias gate, VP filter bypass, target precedence
# =====================================================================

class _StubBias:
    def __init__(self, value): self._v = value
    def bias(self): return self._v


class _StubLevels:
    def __init__(self, result): self._r = result
    def find_target(self, side, entry, stop, min_r): return self._r


async def _run_short_signal(engine, broker):
    captured = []
    async def cap(_, out): captured.append(out)
    engine.on_signal = cap
    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        await broker.inject_bar(bar(in_ny_am(i), o, h, l, c))
        await asyncio.sleep(0)
    return captured


@pytest.mark.asyncio
async def test_htf_bias_blocks_counter_trend_short():
    """Bullish 4h bias must block a short signal and emit reason='htf_bias'."""
    from app.bot_config import StrategyParams
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    cfg = StrategyParams(vp_enabled=False, htf_bias_enabled=True)
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    engine.htf_bias = _StubBias("bullish")   # bullish bias -> block shorts
    await broker.connect()
    await engine.start()
    captured = await _run_short_signal(engine, broker)
    assert len(captured) == 1
    assert captured[0].placed is False
    assert captured[0].reason == "htf_bias"


@pytest.mark.asyncio
async def test_htf_bias_neutral_does_not_block():
    """Neutral 4h bias must not block any direction."""
    from app.bot_config import StrategyParams
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    cfg = StrategyParams(vp_enabled=False, htf_bias_enabled=True)
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    engine.htf_bias = _StubBias("neutral")   # neutral -> no block
    await broker.connect()
    await engine.start()
    captured = await _run_short_signal(engine, broker)
    assert len(captured) == 1
    assert captured[0].reason != "htf_bias"


@pytest.mark.asyncio
async def test_htf_target_overrides_when_enabled():
    """When htf_target_enabled, the HTF level price replaces the composer target."""
    from app.bot_config import StrategyParams
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    cfg = StrategyParams(vp_enabled=False, htf_target_enabled=True)
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    # SHORT_SIGNAL_BARS generate entry ~2401, stop ~2403.8 (above entry for short).
    # Target 2395.0 is below entry — plausible short target.
    engine.htf_levels = _StubLevels((Decimal("2395.0"), "HTF: 4h FVG @ 2395.0 (3.0R)"))
    placed = []
    async def cap(sig, out):
        if out.placed: placed.append(sig)
    engine.on_signal = cap
    await broker.connect()
    await engine.start()
    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        await broker.inject_bar(bar(in_ny_am(i), o, h, l, c))
        await asyncio.sleep(0)
    assert placed and placed[0].target == Decimal("2395.0")


@pytest.mark.asyncio
async def test_htf_disabled_is_unchanged():
    """When htf_bias_enabled=False, a present htf_bias stub must be ignored."""
    from app.bot_config import StrategyParams
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    cfg = StrategyParams(vp_enabled=False)   # htf flags default False
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    engine.htf_bias = _StubBias("bullish")   # present but must be IGNORED (flag off)
    await broker.connect()
    await engine.start()
    captured = await _run_short_signal(engine, broker)
    assert len(captured) == 1
    assert captured[0].reason != "htf_bias"   # gate not consulted when disabled


@pytest.mark.asyncio
async def test_vp_filter_denies_out_of_value_area_short_with_neutral_bias():
    """
    VP filter must deny a short whose entry is well below VAL when bias is neutral.

    SHORT_SIGNAL_BARS produce a short entry near 2397–2401. We set
    VAL=2410 so the short entry is ~10+ pts below VAL — clearly outside
    the value area. With neutral bias (no bypass), the VP filter must fire
    and deny with reason='vp_filter'.
    """
    from app.bot_config import StrategyParams
    from app.strategy.volume_profile import VolumeProfileTracker, VolumeProfile
    from datetime import date

    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    runner.vp = VolumeProfileTracker()
    # Short entry ~2397-2401 is below VAL=2410, tolerance=2.0 → entry < VAL - tol → rejected.
    runner.vp._prior = VolumeProfile(
        session_date=date(2026, 5, 26),
        poc=Decimal("2415"), vah=Decimal("2420"), val=Decimal("2410"),
        hvns=[], total_volume=1000,
    )

    cfg = StrategyParams(vp_enabled=True, htf_bias_enabled=True, vp_filter_tolerance=Decimal("2.0"))
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    engine.htf_bias = _StubBias("neutral")   # neutral bias — no bypass
    await broker.connect()
    await engine.start()
    captured = await _run_short_signal(engine, broker)

    assert len(captured) == 1
    assert captured[0].placed is False
    assert captured[0].reason == "vp_filter"


@pytest.mark.asyncio
async def test_agreeing_bias_bypasses_vp_filter():
    """
    Agreeing 4h bias must bypass the VP value-area filter.

    Same setup as test_vp_filter_denies_out_of_value_area_short_with_neutral_bias
    but with bearish bias (agrees with the short signal). The short entry is
    still well below VAL — but the bias bypass must suppress the VP rejection.
    This is the 2026-05-27 regression case: real below-value-area shorts that
    were correctly aligned with the 4h trend.
    """
    from app.bot_config import StrategyParams
    from app.strategy.volume_profile import VolumeProfileTracker, VolumeProfile
    from datetime import date

    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    runner.vp = VolumeProfileTracker()
    # Same out-of-value-area profile as the neutral-bias test above.
    runner.vp._prior = VolumeProfile(
        session_date=date(2026, 5, 26),
        poc=Decimal("2415"), vah=Decimal("2420"), val=Decimal("2410"),
        hvns=[], total_volume=1000,
    )

    cfg = StrategyParams(vp_enabled=True, htf_bias_enabled=True, vp_filter_tolerance=Decimal("2.0"))
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    engine.htf_bias = _StubBias("bearish")   # agrees with short → bypass VP filter
    await broker.connect()
    await engine.start()
    captured = await _run_short_signal(engine, broker)

    assert len(captured) == 1
    # The VP filter must NOT have fired — bias bypass must take effect.
    assert captured[0].reason != "vp_filter"


@pytest.mark.asyncio
async def test_htf_warns_once_when_flag_on_but_tracker_none(caplog):
    """Defense-in-depth: flag on but tracker is None must log a WARNING (once),
    not silently no-op. The signal should NOT be denied for htf_bias — the
    gate is correctly inert when the tracker is missing (fail-open)."""
    import logging
    from app.bot_config import StrategyParams
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    cfg = StrategyParams(vp_enabled=False, htf_bias_enabled=True)
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg)
    # htf_bias deliberately left as None — the misconfig case
    assert engine.htf_bias is None
    await broker.connect()
    await engine.start()

    with caplog.at_level(logging.WARNING, logger="app.execution.engine"):
        captured = await _run_short_signal(engine, broker)

    assert len(captured) == 1
    # Gate must NOT have blocked (fail-open when tracker is None).
    assert captured[0].reason != "htf_bias"
    # Warning must have fired.
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "tracker is None" in r.getMessage()]
    assert len(warnings) >= 1, f"expected 'tracker is None' warning, got: {[r.getMessage() for r in caplog.records]}"

    # Second run on the same engine must NOT re-log (one-shot guard).
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.execution.engine"):
        captured2 = await _run_short_signal(engine, broker)
    # Some signals may be captured (or none, depending on bar replay); the
    # contract under test is that the warning is NOT repeated.
    warnings2 = [r for r in caplog.records
                 if "tracker is None" in r.getMessage()]
    assert len(warnings2) == 0


@pytest.mark.asyncio
async def test_htf_live_toggle_takes_effect_without_restart():
    """Simulates PATCH /api/config hot-apply: flipping strategy_cfg flags and
    assigning trackers on a running engine must change the gate's behavior
    immediately on the next bar — the contract PATCH relies on."""
    from app.bot_config import StrategyParams
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    # Start with HTF OFF, no trackers.
    cfg_off = StrategyParams(vp_enabled=False)
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True, strategy_cfg=cfg_off)
    assert engine.htf_bias is None
    await broker.connect()
    await engine.start()

    first = await _run_short_signal(engine, broker)
    # With HTF off, the short signal should not be htf_bias-denied.
    assert len(first) == 1
    assert first[0].reason != "htf_bias"

    # Simulate the PATCH hot-apply: flip the flag and assign a tracker that
    # would block this signal. Reset the warning guard so subsequent missed
    # tracker assignments would still surface.
    engine.strategy_cfg = StrategyParams(vp_enabled=False, htf_bias_enabled=True)
    engine.htf_bias = _StubBias("bullish")
    engine._htf_warned = False

    second = await _run_short_signal(engine, broker)
    assert len(second) == 1
    assert second[0].placed is False
    assert second[0].reason == "htf_bias"
