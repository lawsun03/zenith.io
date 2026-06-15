"""
Defining-behavior tests for KZLevelsRunner.

KZLevelsRunner replaces the swing-based LiquidityTracker with session-range
sweep detection (KillzoneLevelTracker). Sweeps of London/NY-AM/NY-PM session
highs or lows, followed by a displacement + FVG in the reversal direction,
produce a Signal via the same SweepDisplacementComposer as iFVG.

These tests verify:
  1. A session-level sweep + displacement combo emits a Signal (core path).
  2. A sweep with no subsequent displacement emits nothing.
  3. A sweep outside any trading killzone is ignored by the composer.
  4. Wrong-direction displacement (same side as sweep) produces no signal.
  5. engine="kz_levels" in StrategyParams wires KZLevelsRunner via _build_runner.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, time as dtime
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

import pytest

from app.bot_config import StrategyParams
from app.broker.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.grader import SetupGrader
from app.strategy.killzone import Killzone, london_open, ny_am
from app.strategy.kz_levels import KillzoneLevelTracker, KZLevelsRunner

ET = ZoneInfo("America/New_York")


def _bar(ts: datetime, h: float, l: float, c: float,
         o: Optional[float] = None, instrument: str = "MNQ") -> Bar:
    o = o if o is not None else c
    return Bar(
        instrument=instrument,
        timeframe="5min",
        ts=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(l)),
        close=Decimal(str(c)),
        volume=100,
    )


def _ts(hour: int, minute: int = 0, day: int = 5) -> datetime:
    """2026-01-<day> at the given hour:minute ET."""
    return datetime(2026, 1, day, hour, minute, tzinfo=ET)


# Fixed killzone list for tests: London (02-05 ET) + NY AM (08:30-11 ET).
ZONES = [london_open(), ny_am()]


def _make_runner(cfg: Optional[StrategyParams] = None) -> KZLevelsRunner:
    """Build a KZLevelsRunner with loose parameters for deterministic tests.

    Uses confirmation="displacement_only" to isolate the KZ sweep mechanism
    from the iFVG inversion requirement (iFVG confirmation is already covered
    by the iFVG engine's own test suite).
    """
    cfg = cfg or StrategyParams()
    return KZLevelsRunner(
        instrument="MNQ",
        timeframe="5min",
        kz_tracker=KillzoneLevelTracker(),
        displacement=DisplacementDetector(DisplacementConfig(
            atr_period=3,
            body_atr_multiple=Decimal("0.5"),
            min_body_to_range_ratio=Decimal("0.0"),
            min_absolute_body=Decimal("1.0"),
        )),
        composer=SweepDisplacementComposer(ComposerConfig(
            instrument="MNQ",
            r_multiple=Decimal("2.0"),
            stop_buffer=Decimal("0.30"),
            trend_ema_period=0,  # disabled — avoid warmup requirement
            killzones=ZONES,
            confirmation="displacement_only",  # no FVG inversion required in unit test
        )),
        grader=SetupGrader(target_clarity_mode="off"),  # no HTF data in unit tests
        strategy_cfg=cfg,
        zones=ZONES,
    )


def _feed_warmup(runner: KZLevelsRunner, n: int = 5) -> None:
    """Feed n bars before London to warm up the ATR (alternating range=10 pts)."""
    base_ts = _ts(1, 0)  # 01:00 ET — before London
    for i in range(n):
        ts = base_ts + timedelta(minutes=5 * i)
        high = 21010 if i % 2 == 0 else 21005
        low = 21000 if i % 2 == 0 else 20995
        runner.on_bar(_bar(ts, h=high, l=low, c=21000))


def _feed_london(runner: KZLevelsRunner,
                 kz_high: float = 21010, kz_low: float = 20990) -> None:
    """Feed London session bars (02:00–04:55 ET) to establish the KZ range.
    The range extreme is set on the first bar; remaining bars stay inside."""
    # First bar sets the extreme
    runner.on_bar(_bar(_ts(2, 0), h=kz_high, l=kz_low, c=21000))
    # A few more bars inside the range (don't extend the extreme)
    for minute in (5, 10, 15):
        runner.on_bar(_bar(_ts(2, minute), h=21005, l=20995, c=21000))


def _lock_london(runner: KZLevelsRunner) -> None:
    """Feed one bar after London closes (05:05 ET) to finalize the KZ level."""
    runner.on_bar(_bar(_ts(5, 5), h=21002, l=20998, c=21000))


def _feed_sweep_and_displacement(
    runner: KZLevelsRunner,
    sweep_high: float = 21011.0,
    sweep_close: float = 21005.0,
    sweep_ts_hour: int = 9,
    sweep_ts_minute: int = 0,
) -> tuple[list, Optional[object]]:
    """
    Feed: sweep bar (sweeps London high) + 3-bar bearish displacement sequence.
    Returns (all_signals_emitted, final_signal).
    """
    signals = []

    # Sweep bar: bar.high >= kz_high + 0.20, bar.close < kz_high
    sweep_ts = _ts(sweep_ts_hour, sweep_ts_minute)
    sig = runner.on_bar(_bar(sweep_ts, h=sweep_high, l=21004.0, c=sweep_close))
    signals.append(sig)

    # b1: before displacement (establishes bar1 for FVG)
    sig = runner.on_bar(_bar(sweep_ts + timedelta(minutes=5),
                             h=21007.0, l=21002.0, c=21003.0, o=21005.0))
    signals.append(sig)

    # b2: large bearish displacement bar (body=21 pts, range=23 pts)
    sig = runner.on_bar(_bar(sweep_ts + timedelta(minutes=10),
                             h=21003.0, l=20980.0, c=20982.0, o=21003.0))
    signals.append(sig)

    # b3: closes below b1.low (21002 > 20986) → confirms bearish FVG
    sig = runner.on_bar(_bar(sweep_ts + timedelta(minutes=15),
                             h=20986.0, l=20975.0, c=20977.0, o=20982.0))
    signals.append(sig)

    return signals, sig


# -----------------------------------------------------------------------
# Test 1: full path — KZ sweep + bearish displacement → SHORT signal
# -----------------------------------------------------------------------

def test_kz_sweep_and_displacement_emits_signal():
    """After London level is locked, a one-bar sweep of its high followed by
    bearish displacement emits a SHORT signal via the standard composer chain."""
    runner = _make_runner()
    _feed_warmup(runner)
    _feed_london(runner, kz_high=21010.0, kz_low=20990.0)
    _lock_london(runner)

    signals, last_sig = _feed_sweep_and_displacement(runner)

    # Exactly one signal emitted (on the b3 displacement bar)
    non_none = [s for s in signals if s is not None]
    assert len(non_none) == 1, f"Expected 1 signal, got {non_none}"
    sig = non_none[0]

    assert sig.side == "short"
    assert sig.killzone == "NY AM"
    assert sig.sweep_pattern in ("B_one_bar", "A_multi_bar")
    # Entry = displacement close (displacement_only mode; no FVG zone)
    assert sig.entry == Decimal("20977")  # b3 close
    # Stop is above sweep extreme (21011 + 0.30 buffer)
    assert sig.stop > Decimal("21010")


# -----------------------------------------------------------------------
# Test 2: no displacement → no signal
# -----------------------------------------------------------------------

def test_no_displacement_no_signal():
    """A KZ sweep without a subsequent displacement event produces no signal."""
    runner = _make_runner()
    _feed_warmup(runner)
    _feed_london(runner)
    _lock_london(runner)

    # Feed sweep bar
    sweep_ts = _ts(9, 0)
    sig = runner.on_bar(_bar(sweep_ts, h=21011.0, l=21004.0, c=21005.0))
    assert sig is None

    # Feed quiet bars — no large body, no displacement
    for i in range(1, 5):
        sig = runner.on_bar(_bar(
            sweep_ts + timedelta(minutes=5 * i),
            h=21006.0, l=21002.0, c=21004.0,
        ))
        assert sig is None, f"Unexpected signal on quiet bar {i}: {sig}"


# -----------------------------------------------------------------------
# Test 3: sweep outside any killzone is ignored by the composer
# -----------------------------------------------------------------------

def test_sweep_outside_killzone_ignored():
    """A sweep at 05:30 ET (dead zone between London close and NY AM open) is
    suppressed by the KZ tracker itself (no event emitted), so the composer
    never arms. No signal even when displacement bars follow in the same window."""
    runner = _make_runner()
    _feed_warmup(runner)
    _feed_london(runner)
    _lock_london(runner)

    # Feed sweep at 05:30 ET — outside both ZONES (London ends 05:00, NY AM starts 08:30)
    sweep_ts = _ts(5, 30)
    runner.on_bar(_bar(sweep_ts, h=21011.0, l=21004.0, c=21005.0))

    # Displacement bars right after (also outside any killzone)
    runner.on_bar(_bar(sweep_ts + timedelta(minutes=5), h=21007, l=21002, c=21003, o=21005))
    runner.on_bar(_bar(sweep_ts + timedelta(minutes=10), h=21003, l=20980, c=20982, o=21003))
    sig = runner.on_bar(_bar(sweep_ts + timedelta(minutes=15), h=20986, l=20975, c=20977, o=20982))

    assert sig is None, f"Expected None (sweep outside KZ), got {sig}"


# -----------------------------------------------------------------------
# Test 4: wrong-direction displacement blocked
# -----------------------------------------------------------------------

def test_wrong_direction_displacement_no_signal():
    """A high-side KZ sweep (expecting bearish displacement) plus a BULLISH
    displacement does not produce a signal."""
    runner = _make_runner()
    _feed_warmup(runner)
    _feed_london(runner)
    _lock_london(runner)

    sweep_ts = _ts(9, 0)
    # Sweep London high (expect bearish confirmation)
    runner.on_bar(_bar(sweep_ts, h=21011.0, l=21004.0, c=21005.0))

    # b1
    runner.on_bar(_bar(sweep_ts + timedelta(minutes=5),
                       h=21005.0, l=21000.0, c=21003.0, o=21003.0))
    # b2: large BULLISH bar (wrong direction)
    runner.on_bar(_bar(sweep_ts + timedelta(minutes=10),
                       h=21030.0, l=21003.0, c=21028.0, o=21003.0))
    # b3: b3.low > b1.high → bullish FVG (but sweep expects bearish)
    sig = runner.on_bar(_bar(sweep_ts + timedelta(minutes=15),
                             h=21032.0, l=21025.0, c=21028.0, o=21028.0))

    assert sig is None, f"Expected None (wrong direction), got {sig}"


# -----------------------------------------------------------------------
# Test 5: _build_runner returns KZLevelsRunner for engine="kz_levels"
# -----------------------------------------------------------------------

def test_build_runner_returns_kz_levels_runner():
    """_build_runner in both backtest runner and main wires KZLevelsRunner
    when strategy_params.engine == 'kz_levels'."""
    from app.backtest.runner import BacktestConfig, _build_runner

    def _noop_bars():
        return iter([])

    cfg = BacktestConfig(
        instrument="MNQ",
        bars=_noop_bars(),
        strategy_params=StrategyParams(engine="kz_levels"),
    )
    runner = _build_runner(cfg)
    assert isinstance(runner, KZLevelsRunner), \
        f"Expected KZLevelsRunner, got {type(runner).__name__}"
    assert runner.instrument == "MNQ"
    assert runner.strategy_cfg.engine == "kz_levels"
