"""Research-protocol fill model (docs/research-protocol.md §5, strict_fills=True).

WHY: fill optimism is invisible in a backtest — nothing crashes, the equity
curve just looks better. The SI/GC study found a gap-through-target fill
bug worth +0.08R/trade on the SI 8:25 breakout. Each test pins one rule so a
regression moves a number here, not silently in a research result.

These drive PaperBroker directly with hand-built bars. They cannot catch a
strategy that places orders at prices the broker then re-anchors, nor fills on
the live TopstepX path — they pin the simulator only.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.broker.events import Bar, Fill
from app.broker.paper import PaperBroker
from app.replay import load_bars_csv
from app.strategy.orb import ORBConfig, ORBDetector

TICK = Decimal("0.10")  # MGC
T0 = datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc)  # 09:00 CT, mid-session


def _bar(i, o, h, l, c, ts=None):
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts or T0 + timedelta(minutes=i),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=100,
    )


async def _trade(side, stop, target, bars, strict=True, entry_close=100):
    """Enter at market on the close of a seed bar (1 tick slippage), then play
    `bars`. Returns (entry_fill, first exit_fill or None, broker, all fills)."""
    broker = PaperBroker(slippage_ticks_market=1, commission_per_side=Decimal("0"),
                         strict_fills=strict)
    fills: list[Fill] = []

    async def collect(f: Fill):
        fills.append(f)

    broker.on_fill(collect)
    await broker.connect()
    await broker.inject_bar(_bar(0, entry_close, entry_close, entry_close, entry_close))
    # stop/target are given relative to the slipped fill so tests read in
    # absolute prices: place_bracket re-anchors signal offsets onto the fill.
    slip = TICK if side == "long" else -TICK
    fill = Decimal(str(entry_close)) + slip
    await broker.place_bracket("MGC", side, 1, fill, Decimal(str(stop)), Decimal(str(target)))
    for b in bars:
        await broker.inject_bar(b)
    exits = [f for f in fills if not f.is_entry]
    return fills[0], (exits[0] if exits else None), broker, fills


# 1 — regression for the SI-study bug
async def test_long_target_gap_fills_at_target_not_open():
    # Long from 100.1, target 102. Next bar opens at 103 (gapped past target).
    _, exit_fill, _, _ = await _trade("long", 99, 102, [_bar(1, 103, 104, 102.5, 103.5)])
    assert exit_fill.fill_price == Decimal("102"), "resting limit fills at its own price, never the gap open"


# 2
async def test_short_stop_gap_fills_at_open_plus_slippage():
    # Short from 99.9, stop 101. Bar opens at 102 — the stop was never available.
    _, exit_fill, _, _ = await _trade("short", 101, 97, [_bar(1, 102, 102.5, 101.5, 102)], entry_close=100)
    assert exit_fill.fill_price == Decimal("102") + TICK


async def test_long_stop_gap_fills_at_open_minus_slippage():
    _, exit_fill, _, _ = await _trade("long", 99, 102, [_bar(1, 98, 98.5, 97.5, 98)])
    assert exit_fill.fill_price == Decimal("98") - TICK


async def test_legacy_mode_keeps_stop_at_trigger_price():
    # Pins that paper mode is unchanged: same gap bar fills at the stop in legacy.
    _, exit_fill, _, _ = await _trade("long", 99, 102, [_bar(1, 98, 98.5, 97.5, 98)], strict=False)
    assert exit_fill.fill_price == Decimal("99") - TICK


# 3
async def test_stop_and_target_same_bar_takes_stop():
    _, exit_fill, _, _ = await _trade("long", 99, 102, [_bar(1, 100, 103, 98, 101)])
    assert exit_fill.fill_price == Decimal("99") - TICK
    assert exit_fill.is_stop


# 4
async def test_limit_touched_exactly_does_not_fill():
    _, exit_fill, broker, _ = await _trade("long", 99, 102, [_bar(1, 101, 102, 100.5, 101.5)])
    assert exit_fill is None, "touch without trade-through must leave the target resting"
    assert len(broker.open_brackets()) == 1


async def test_limit_traded_through_one_tick_fills_at_limit():
    _, exit_fill, _, _ = await _trade("long", 99, 102, [_bar(1, 101, 102.1, 100.5, 101.5)])
    assert exit_fill.fill_price == Decimal("102")


async def test_short_limit_touched_exactly_does_not_fill():
    _, exit_fill, _, _ = await _trade("short", 101, 98, [_bar(1, 99, 99.5, 98, 98.5)])
    assert exit_fill is None


# 5
async def test_open_trade_at_flat_by_closes_at_bar_close_with_slippage():
    # ExecutionEngine._enforce_flatten calls broker.flatten() on the first bar
    # inside the flatten window; that must be a time exit at close ± slippage.
    flat_bar = _bar(1, 100.5, 100.8, 100.2, 100.6)
    _, exit_fill, broker, fills = await _trade("long", 99, 102, [flat_bar])
    assert exit_fill is None
    await broker.flatten("MGC")
    assert broker.open_brackets() == []
    assert fills[-1].fill_price == Decimal("100.6") - TICK
    # long 1 MGC from 100.1, out at 100.5 → +0.4 pts × $10/pt
    assert fills[-1].realized_pnl_delta == Decimal("4.0")


# 6
async def test_no_carry_across_session_break_or_roll():
    # 5pm CT (23:00 UTC in January) starts a new trading day. On continuous
    # data the roll lands at a session break, so a position must never see a
    # bar from the next session — here the next session opens 5 points higher
    # (a roll gap) and would hit the target if the position carried.
    last_of_session = _bar(0, 100.4, 100.6, 100.3, 100.5,
                           ts=datetime(2026, 1, 6, 22, 59, tzinfo=timezone.utc))
    next_session = _bar(0, 105, 106, 104.9, 105.5,
                        ts=datetime(2026, 1, 7, 0, 0, tzinfo=timezone.utc))
    _, exit_fill, broker, _ = await _trade("long", 99, 102, [last_of_session, next_session])
    assert exit_fill is not None
    assert exit_fill.fill_price == Decimal("100.5") - TICK, "closed at the entry session's last close"
    assert broker.open_brackets() == []


# 7 — look-ahead guard
def _orb_signals(bars):
    det = ORBDetector(ORBConfig(instrument="MGC"))
    out = []
    for i, b in enumerate(bars):
        s = det.on_bar(b)
        if s is not None:
            out.append((i, s.side, s.entry, s.stop, s.target))
    return out


def _leaky_orb_signals(bars):
    # The same detector fed a close shifted one bar earlier: bar t sees
    # close[t+1]. This is the exact bug class the guard must catch.
    shifted = [
        replace(b, close=bars[i + 1].close) if i + 1 < len(bars) else b
        for i, b in enumerate(bars)
    ]
    return _orb_signals(shifted)


def _causal_violations(signal_fn, bars):
    """Every signal at bar t must be reproduced from bars[:t+1] alone."""
    bad = []
    for sig in signal_fn(bars):
        t = sig[0]
        prefix = signal_fn(bars[: t + 1])
        if not prefix or prefix[-1] != sig:
            bad.append(sig)
    return bad


def _sample_bars():
    return list(load_bars_csv("test_bars.csv", instrument="MGC"))


def test_orb_signals_are_causal():
    bars = _sample_bars()
    assert _orb_signals(bars), "fixture must produce signals or the guard is vacuous"
    assert _causal_violations(_orb_signals, bars) == []


def test_guard_detects_one_bar_lookahead():
    bars = _sample_bars()
    assert _causal_violations(_leaky_orb_signals, bars), (
        "guard failed to flag a feature shifted one bar earlier"
    )
