"""
B33 Phase 1 — anticipatory probe-trigger data mining.

Hypothesis: entering a small-risk "probe" at the liquidity-sweep reclaim bar
(closed-bar sweep+reclaim, same tracking as the iFVG composer), then scaling
up when the iFVG signal confirms, improves blended-entry R vs trading the iFVG
alone.

GO/NO-GO formula (Lesson 1 / B33 spec):
    expected_R = conf_rate × blended_gain − (1 − conf_rate) × probe_only_loss
    GO if expected_R > 0 and individually components are reasonable

Probe trigger:
- A killzone-filtered, non-duplicate sweep event emitted by LiquidityTracker
  (both Pattern A and B) — same as iFVG's on_sweep arming logic.
- probe_entry = sweep completion bar.close (the reclaim bar)
- probe_stop = sweep_extreme ± stop_buffer (opposite of trade direction)

Probe outcomes:
- CONFIRMED: same-direction iFVG signal fires within probe_confirm_window_bars=8
  AND probe stop is NOT hit before the signal
- STOP_HIT: probe stop is hit before a confirming signal (loss = 1.0R probe)
- TARGET_HIT: favorable excursion >= probe_stop_dist within window (gain = 1.0R
  probe) — before stop or iFVG
- TIME_STOP: neither in window → exit at BE (0 approx)

Blended-entry gain (confirmed cases):
    blended_entry = probe_risk_frac × probe_entry + (1 − probe_risk_frac) × ifvg_entry
    gain_R = |blended_entry − ifvg_entry| / ifvg_stop_dist × sign
           = probe_risk_frac × |probe_entry − ifvg_entry| / ifvg_stop_dist
    (positive when probe_entry is directionally better than ifvg_entry)

Runs WITHOUT the grader and HTF feeds (simpler; produces more signals than
production; conf_rate may be slightly optimistic vs deployed signal set).
"""
from __future__ import annotations

import csv
import sys
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone
from typing import Iterator

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app.bot_config import load_bot_config, strategy_for
from app.broker.events import Bar
from app.replay import load_bars_csv
from app.strategy.composer import (
    ComposerConfig,
    Signal,
    SweepDisplacementComposer,
)
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.killzone import (
    all_day,
    default_killzones,
    in_killzone,
    killzones_from_names,
)
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker, SweepEvent

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
PROBE_CONFIRM_WINDOW = 8   # bars to wait for iFVG confirmation
PROBE_RISK_FRAC = Decimal("0.33")  # fraction of normal risk for probe leg
YEARS = [2021, 2023, 2024, 2025, 2026]  # 2022 frozen holdout
BARS_DIR = _REPO / "bars" / "yearly"
BOT_CONFIG_PATH = _REPO / "bot_config.json"
INSTRUMENT = "MNQ"
TIMEFRAME = "5min"


# --------------------------------------------------------------------------- #
# Instrumented composer — captures every armed sweep and every signal
# --------------------------------------------------------------------------- #
@dataclass
class ArmedSweep:
    ts: datetime
    side: str            # "high" (→ short probe) or "low" (→ long probe)
    sweep_extreme: Decimal
    probe_entry: Decimal  # bar.close at probe trigger
    probe_stop: Decimal  # swept extreme ± stop_buffer
    probe_stop_dist: Decimal


class _InstrumentedComposer(SweepDisplacementComposer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.armed_sweeps: list[ArmedSweep] = []
        self.signals: list[Signal] = []
        self._stop_buf: Decimal = self.config.stop_buffer

    def on_sweep(self, bar: Bar, sweep: SweepEvent) -> None:
        before = len(self._awaiting)
        super().on_sweep(bar, sweep)
        after = len(self._awaiting)
        if after > before:
            # A new sweep was armed — record as a probe trigger
            if sweep.side == "low":
                # low sweep → long probe; stop below swept extreme
                probe_stop = sweep.sweep_extreme - self._stop_buf
            else:
                # high sweep → short probe; stop above swept extreme
                probe_stop = sweep.sweep_extreme + self._stop_buf
            probe_stop_dist = abs(bar.close - probe_stop)
            self.armed_sweeps.append(ArmedSweep(
                ts=bar.ts,
                side=sweep.side,
                sweep_extreme=sweep.sweep_extreme,
                probe_entry=bar.close,
                probe_stop=probe_stop,
                probe_stop_dist=probe_stop_dist,
            ))

    def on_displacement(self, bar, event) -> Signal | None:
        sig = super().on_displacement(bar, event)
        if sig is not None:
            self.signals.append(sig)
        return sig


# --------------------------------------------------------------------------- #
# Single-year replay
# --------------------------------------------------------------------------- #
def _replay_year(year: int, s, killzones) -> tuple[list[Bar], _InstrumentedComposer]:
    """Run one year of bars through the detector stack; return bars + composer."""
    bars_path = BARS_DIR / f"bars_MNQ_dbv_{year}.csv"
    bars: list[Bar] = list(load_bars_csv(str(bars_path), INSTRUMENT, TIMEFRAME))

    liq = LiquidityTracker(LiquidityConfig(
        swing_lookback=s.swing_lookback,
        min_penetration=s.min_penetration,
        multi_bar_window=s.multi_bar_window,
        max_swings=50,
    ))
    disp = DisplacementDetector(DisplacementConfig(
        atr_period=s.atr_period,
        body_atr_multiple=s.body_atr_multiple,
        min_body_to_range_ratio=s.min_body_to_range_ratio,
        min_absolute_body=s.min_absolute_body,
    ))
    comp = _InstrumentedComposer(ComposerConfig(
        instrument=INSTRUMENT,
        displacement_window_bars=s.displacement_window_bars,
        stop_buffer=s.stop_buffer,
        r_multiple=s.r_multiple,
        killzones=killzones,
        trend_ema_period=0,          # no EMA filter for data mining
        cooldown_bars_after_stop=0,  # no cooldown
        swing_stop_lookback=0,       # research baseline
        allowed_sides="both",        # both directions
        confirmation="ifvg",
    ))

    for bar in bars:
        sweeps = liq.on_bar(bar)
        dv = disp.on_bar(bar)
        for sv in sweeps:
            comp.on_sweep(bar, sv)
        if dv is not None:
            comp.on_displacement(bar, dv)
        comp.on_bar_close(bar)

    return bars, comp


# --------------------------------------------------------------------------- #
# Post-processing: match sweeps → signals, compute probe outcomes
# --------------------------------------------------------------------------- #
def _analyze(all_bars: list[Bar], all_sweeps: list[ArmedSweep],
             all_signals: list[Signal]) -> dict:
    """
    For each armed sweep:
    1. Find the matching signal (same direction, within PROBE_CONFIRM_WINDOW bars,
       probe stop not hit before signal bar).
    2. Track bars between sweep and signal/window-end for stop/target hits.
    3. Compute blended-entry gain for confirmed probes.
    """
    # Index bars by timestamp
    ts_to_idx: dict[datetime, int] = {b.ts: i for i, b in enumerate(all_bars)}

    # Index signals by timestamp + side for quick lookup
    # signal direction: "low" sweep → "long" signal; "high" sweep → "short" signal
    sig_by_ts: dict[tuple[datetime, str], Signal] = {}
    for sig in all_signals:
        sig_by_ts[(sig.created_at, sig.side)] = sig

    # Expected iFVG side from probe side
    def probe_ifvg_side(sweep_side: str) -> str:
        return "long" if sweep_side == "low" else "short"

    confirmed = []
    stop_hit = []
    target_hit = []
    time_stop = []

    for sweep in all_sweeps:
        probe_side = probe_ifvg_side(sweep.side)
        start_idx = ts_to_idx.get(sweep.ts)
        if start_idx is None:
            continue

        # Check bars T+1 ... T+PROBE_CONFIRM_WINDOW
        probe_stopped = False
        probe_targeted = False
        confirmed_signal: Signal | None = None
        stop_dist = sweep.probe_stop_dist
        if stop_dist <= 0:
            continue  # degenerate (entry == stop)

        for offset in range(1, PROBE_CONFIRM_WINDOW + 1):
            idx = start_idx + offset
            if idx >= len(all_bars):
                break
            bar = all_bars[idx]

            # Check probe stop first (adversity)
            if sweep.side == "low":
                # long probe: stop is below entry; adverse = bar.low <= probe_stop
                if bar.low <= sweep.probe_stop:
                    probe_stopped = True
                    stop_hit.append(sweep)
                    break
                # favorable: bar.high >= probe_entry + stop_dist (1R target)
                if not probe_stopped and bar.high >= sweep.probe_entry + stop_dist:
                    probe_targeted = True
            else:
                # short probe: stop is above entry; adverse = bar.high >= probe_stop
                if bar.high >= sweep.probe_stop:
                    probe_stopped = True
                    stop_hit.append(sweep)
                    break
                if not probe_stopped and bar.low <= sweep.probe_entry - stop_dist:
                    probe_targeted = True

            # Check for confirming signal on this bar
            sig = sig_by_ts.get((bar.ts, probe_side))
            if sig is not None:
                confirmed_signal = sig
                # Probe stop not hit before this bar → confirmed
                confirmed.append((sweep, sig))
                break

        if not probe_stopped and confirmed_signal is None:
            # No stop hit, no signal in window
            if probe_targeted:
                target_hit.append(sweep)
            else:
                time_stop.append(sweep)

    # Blended-entry gain for confirmed probes
    gains = []
    for sweep, sig in confirmed:
        ifvg_entry = sig.entry
        ifvg_stop_dist = abs(sig.entry - sig.stop)
        if ifvg_stop_dist <= 0:
            continue
        probe_ent = sweep.probe_entry
        if sweep.side == "low":
            # long: probe should be lower (better)
            raw_diff = ifvg_entry - probe_ent
        else:
            # short: probe should be higher (better)
            raw_diff = probe_ent - ifvg_entry
        # gain = probe_risk_frac × raw_diff / ifvg_stop_dist
        gain_r = float(PROBE_RISK_FRAC * raw_diff / ifvg_stop_dist)
        gains.append(gain_r)

    total = len(all_sweeps)
    n_confirmed = len(confirmed)
    n_stop_hit = len(stop_hit)
    n_target_hit = len(target_hit)
    n_time_stop = len(time_stop)

    conf_rate = n_confirmed / total if total else 0.0
    stop_rate = n_stop_hit / total if total else 0.0
    target_rate = n_target_hit / total if total else 0.0
    time_stop_rate = n_time_stop / total if total else 0.0

    blended_gain_avg = sum(gains) / len(gains) if gains else 0.0
    blended_gain_pos = sum(g for g in gains if g > 0)
    blended_gain_neg = sum(g for g in gains if g < 0)

    # Probe-only loss: stop_hit → lose probe_risk_frac (=0.33R of normal)
    # target_hit → gain probe_risk_frac; time_stop → 0
    # Weight by frequency:
    unconfirmed_total = n_stop_hit + n_target_hit + n_time_stop
    if unconfirmed_total > 0:
        probe_only_outcome = (
            float(PROBE_RISK_FRAC) * (-n_stop_hit + n_target_hit)
        ) / unconfirmed_total
    else:
        probe_only_outcome = 0.0

    # GO/NO-GO formula
    # expected_R = conf_rate × blended_gain_avg − (1 − conf_rate) × probe_only_loss_avg
    # but probe_only includes both gains and losses
    expected_r = conf_rate * blended_gain_avg - (1.0 - conf_rate) * abs(probe_only_outcome)

    return {
        "total_sweeps": total,
        "confirmed": n_confirmed,
        "stop_hit": n_stop_hit,
        "target_hit": n_target_hit,
        "time_stop": n_time_stop,
        "total_signals": len(all_signals),
        "conf_rate": conf_rate,
        "stop_rate": stop_rate,
        "target_rate": target_rate,
        "time_stop_rate": time_stop_rate,
        "blended_gain_avg": blended_gain_avg,
        "blended_gain_pos": blended_gain_pos,
        "blended_gain_neg": blended_gain_neg,
        "blended_gain_pos_pct": len([g for g in gains if g > 0]) / len(gains) if gains else 0.0,
        "probe_only_outcome": probe_only_outcome,
        "expected_r": expected_r,
        "gains_sample": sorted(gains)[:5] + sorted(gains)[-5:] if len(gains) >= 10 else gains,
    }


def main() -> None:
    bot_cfg = load_bot_config(BOT_CONFIG_PATH)
    s = strategy_for(bot_cfg, INSTRUMENT)
    # Research baseline overrides
    s = s.model_copy(update={
        "swing_stop_lookback": 0,
    })

    # Killzones: deployed "all" config
    kz_names = bot_cfg.enabled_killzones
    if kz_names == ["all"] or kz_names is None:
        killzones = [all_day()]
    else:
        killzones = killzones_from_names(kz_names)

    print(f"\n=== B33 Phase 1 — Probe Trigger Data Mining ===")
    print(f"Instrument: {INSTRUMENT}, stop_buffer={s.stop_buffer}, "
          f"min_absolute_body={s.min_absolute_body}")
    print(f"Killzones: {'all_day' if kz_names == ['all'] else kz_names}")
    print(f"Years: {YEARS} (2022 frozen holdout excluded)\n")

    all_bars_combined: list[Bar] = []
    all_sweeps_combined: list[ArmedSweep] = []
    all_signals_combined: list[Signal] = []

    for year in YEARS:
        bars, comp = _replay_year(year, s, killzones)
        all_bars_combined.extend(bars)
        all_sweeps_combined.extend(comp.armed_sweeps)
        all_signals_combined.extend(comp.signals)
        print(f"  {year}: {len(bars):5d} bars  |  {len(comp.armed_sweeps):4d} sweeps  |  "
              f"{len(comp.signals):4d} iFVG signals")

    print(f"\n  TOTAL: {len(all_bars_combined):6d} bars  |  "
          f"{len(all_sweeps_combined):5d} sweeps  |  "
          f"{len(all_signals_combined):5d} iFVG signals\n")

    r = _analyze(all_bars_combined, all_sweeps_combined, all_signals_combined)

    print("=== Probe Outcome Distribution ===")
    print(f"  Total probe triggers (armed sweeps): {r['total_sweeps']}")
    print(f"  iFVG signals in same period:         {r['total_signals']}")
    print(f"")
    print(f"  Confirmed (iFVG within {PROBE_CONFIRM_WINDOW} bars, stop unhit): "
          f"{r['confirmed']:4d}  ({r['conf_rate']:.1%})")
    print(f"  Probe stop hit before iFVG:          "
          f"{r['stop_hit']:4d}  ({r['stop_rate']:.1%})")
    print(f"  Probe target hit (1.0R, unconfirmed):{r['target_hit']:4d}  ({r['target_rate']:.1%})")
    print(f"  Time-stop (8 bars, no signal):       "
          f"{r['time_stop']:4d}  ({r['time_stop_rate']:.1%})")

    print(f"\n=== Blended-Entry Gain (confirmed probes only) ===")
    print(f"  n = {r['confirmed']}")
    print(f"  mean gain_R = {r['blended_gain_avg']:.4f}R  "
          f"(positive = probe improves entry in correct direction)")
    print(f"  % of confirmed probes with positive gain: {r['blended_gain_pos_pct']:.1%}")
    if r.get('gains_sample'):
        print(f"  sample (sorted): {[f'{g:.3f}' for g in r['gains_sample']]}")

    print(f"\n=== Probe-Only Economics (unconfirmed) ===")
    print(f"  Stop rate: {r['stop_rate']:.1%}  (lose ~0.33R normal risk)")
    print(f"  Target rate: {r['target_rate']:.1%}  (gain ~0.33R normal risk)")
    print(f"  Time-stop rate: {r['time_stop_rate']:.1%}  (~0)")
    print(f"  Net probe-only outcome per unconfirmed trigger: {r['probe_only_outcome']:.4f}R")

    print(f"\n=== GO/NO-GO Decision ===")
    print(f"  conf_rate     = {r['conf_rate']:.3f}")
    print(f"  blended_gain  = {r['blended_gain_avg']:.4f}R")
    print(f"  probe_only    = {r['probe_only_outcome']:.4f}R  "
          f"(negative = net loss on unconfirmed)")
    er = r['expected_r']
    print(f"  expected_R    = {er:.4f}R")
    print()

    if er > 0 and r['blended_gain_avg'] > 0 and r['conf_rate'] > 0.05:
        verdict = "GO — proceed to Phase 2 engine implementation"
    elif er > 0:
        verdict = "MARGINAL GO — weak positive expectancy; implement with caution"
    else:
        verdict = "NO-GO — probe mechanism has negative expectancy; reject B33"

    print(f"  VERDICT: {verdict}")
    print()

    # Additional diagnostic: sweep vs signal ratio
    ratio = r['total_sweeps'] / r['total_signals'] if r['total_signals'] > 0 else float('inf')
    print(f"=== Diagnostic ===")
    print(f"  Sweep:Signal ratio = {ratio:.1f}x  "
          f"(1.0 = every sweep leads to a signal; higher = more unconfirmed probes)")
    print(f"  This ratio determines how many 'bleed' trades the probe generates.")
    print()

    if er <= 0:
        print("B33 Phase 1 verdict: NO-GO. Do NOT proceed to Phase 2.")
        print("Document finding in findings.json + JOURNAL.md + LESSONS.md and exit.")
    else:
        print("B33 Phase 1 verdict: GO. Proceed to Phase 2 engine implementation.")


if __name__ == "__main__":
    main()
