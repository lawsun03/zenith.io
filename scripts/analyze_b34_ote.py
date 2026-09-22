"""
B34 Phase 1 — Breaker-block + OTE retracement falsification.

Hypothesis: after a sweep+BOS impulse, trades that see a retrace to the OTE
zone (0.62-0.79 Fibonacci of the impulse leg) before continuing perform
materially better than trades with no retrace or shallow retrace.

GO/NO-GO criteria:
  - OTE bucket (0.62-0.79) shows materially higher WR/PF than other buckets
  - "Materially" = OTE PF >= 1.20 AND OTE WR >= 40%
  - If all buckets show similar (especially negative) performance → NO-GO

Methodology:
  1. Replay 5y bars (2021/2023/2024/2025/2026) through SweepBOSDetector to
     collect all BOS signals (each defines an impulse leg: sweep_extreme → bos_close).
  2. For each BOS signal, track the subsequent 20 bars to find the deepest
     retrace (expressed as a fib ratio of the impulse leg height).
  3. Then track the next 50 bars from the deepest retrace point to determine
     if price hit the target (2.5R from bos_close) or stop (sweep_extreme ±
     stop_buffer) first.
  4. Bucket by retrace_fib and compute WR/PF/MFE per bucket.

Note: "entry" for performance simulation is always at bos_close (the BOS bar
close, the natural entry for sweep_bos). The retrace bucket just tells us
"did this trade retrace into the OTE zone before working?" — bucketing by
retrace depth AFTER entry is the cleanest per-bucket comparison.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import NamedTuple

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app.bot_config import load_bot_config, strategy_for
from app.sim.events import Bar
from app.replay import load_bars_csv
from app.strategy.killzone import all_day, killzones_from_names
from app.strategy.sweep_bos import SweepBOSConfig, SweepBOSDetector
from app.strategy.composer import Signal

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
YEARS = [2021, 2023, 2024, 2025, 2026]  # 2022 frozen holdout
BARS_DIR = _REPO / "bars" / "yearly"
BOT_CONFIG_PATH = _REPO / "bot_config.json"
INSTRUMENT = "MNQ"
TIMEFRAME = "5min"

RETRACE_WINDOW = 20   # bars to look for deepest retrace after BOS signal
OUTCOME_WINDOW = 60   # bars after BOS to look for target/stop hit

# Fib buckets for OTE analysis
# Bucket index → (low_fib, high_fib, label)
FIB_BUCKETS = [
    (0.00, 0.00, "no_retrace"),       # price never retraced past BOS close
    (0.00, 0.38, "shallow_0.38"),     # very shallow retrace
    (0.38, 0.50, "mid_0.38_0.50"),
    (0.50, 0.62, "golden_0.50_0.62"),
    (0.62, 0.79, "OTE_0.62_0.79"),    # ← hypothesis: best performance here
    (0.79, 1.00, "deep_0.79_1.00"),   # near stop — risky retrace
    (1.00, 9.99, "stopped_out"),      # stop hit during retrace window
]


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #
@dataclass
class BOSEvent:
    signal: Signal
    bos_close: Decimal       # entry price = BOS bar close
    sweep_extreme: Decimal   # from signal.sweep_extreme
    stop: Decimal            # from signal.stop
    stop_dist: Decimal       # abs(bos_close - stop)
    impulse_height: Decimal  # abs(bos_close - sweep_extreme)
    bar_idx: int             # index into all_bars for lookahead
    side: str                # "long" or "short"


class BucketResult(NamedTuple):
    label: str
    n: int
    wins: int
    losses: int
    win_rate: float
    pf: float
    avg_mfe: float   # in R units from bos_close
    avg_mae: float   # in R units from bos_close (adverse = toward stop)
    retrace_fibs: list  # sample of actual retrace fib values in this bucket


# --------------------------------------------------------------------------- #
# Replay one year
# --------------------------------------------------------------------------- #
def _replay_year(year: int, s, killzones) -> tuple[list[Bar], list[Signal]]:
    """Run one year through SweepBOSDetector; return bars + signals."""
    bars_path = BARS_DIR / f"bars_MNQ_dbv_{year}.csv"
    bars: list[Bar] = list(load_bars_csv(str(bars_path), INSTRUMENT, TIMEFRAME))

    cfg = SweepBOSConfig(
        instrument=INSTRUMENT,
        swing_lookback=s.swing_lookback,
        min_penetration=s.min_penetration,
        multi_bar_window=s.multi_bar_window,
        stop_buffer=s.stop_buffer,
        r_multiple=s.r_multiple,
        bos_window_bars=getattr(s, "ifvg_sweep_window_bars", 10),
        killzones=killzones,
    )
    det = SweepBOSDetector(cfg)
    signals: list[Signal] = []
    for bar in bars:
        sig = det.on_bar(bar)
        if sig is not None:
            signals.append(sig)
    return bars, signals


# --------------------------------------------------------------------------- #
# Analyse: measure retrace depth + outcome per BOS event
# --------------------------------------------------------------------------- #
def _build_bos_events(all_bars: list[Bar], all_signals: list[Signal],
                      stop_buffer: Decimal) -> list[BOSEvent]:
    """Create BOSEvent records with bar index for lookahead."""
    ts_to_idx: dict = {b.ts: i for i, b in enumerate(all_bars)}
    events: list[BOSEvent] = []
    for sig in all_signals:
        idx = ts_to_idx.get(sig.created_at)
        if idx is None:
            continue
        bos_close = sig.entry
        sweep_ext = sig.sweep_extreme
        stop = sig.stop
        stop_dist = abs(bos_close - stop)
        impulse_h = abs(bos_close - sweep_ext)
        if stop_dist <= 0 or impulse_h <= 0:
            continue
        events.append(BOSEvent(
            signal=sig,
            bos_close=bos_close,
            sweep_extreme=sweep_ext,
            stop=stop,
            stop_dist=stop_dist,
            impulse_height=impulse_h,
            bar_idx=idx,
            side=sig.side,
        ))
    return events


def _measure_event(ev: BOSEvent, all_bars: list[Bar]) -> tuple[str, float, float, float, float]:
    """
    Returns (bucket_label, retrace_fib, outcome_r, mfe_r, mae_r).
    outcome_r: +2.5 if target hit, -1.0 if stop hit, 0.0 if neither in window.
    mfe_r / mae_r in R units from bos_close.
    """
    bars = all_bars
    n = len(bars)
    start = ev.bar_idx
    sd = float(ev.stop_dist)
    bos = float(ev.bos_close)
    stop_lvl = float(ev.stop)
    impulse_h = float(ev.impulse_height)
    target_lvl = float(ev.signal.target)
    r_mult = float(abs(ev.signal.target - ev.bos_close) / ev.stop_dist) if ev.stop_dist > 0 else 2.5

    # Phase 1: find deepest retrace in RETRACE_WINDOW bars
    retrace_fib = 0.0
    stopped_in_retrace = False

    for offset in range(1, RETRACE_WINDOW + 1):
        idx = start + offset
        if idx >= n:
            break
        bar = bars[idx]
        if ev.side == "long":
            # retrace = price going DOWN below bos_close
            depth = bos - float(bar.low)
            if depth > 0:
                retrace_fib = max(retrace_fib, depth / impulse_h)
            # stop check
            if float(bar.low) <= stop_lvl:
                stopped_in_retrace = True
                break
        else:
            # short: retrace = price going UP above bos_close
            depth = float(bar.high) - bos
            if depth > 0:
                retrace_fib = max(retrace_fib, depth / impulse_h)
            if float(bar.high) >= stop_lvl:
                stopped_in_retrace = True
                break

    if stopped_in_retrace:
        return ("stopped_out", retrace_fib, -1.0, 0.0, -1.0)

    # Determine bucket label
    bucket_label = "no_retrace"
    for lo, hi, label in FIB_BUCKETS:
        if lo == 0.0 and hi == 0.0:
            if retrace_fib < 0.001:
                bucket_label = label
                break
        elif lo == 0.0 and retrace_fib < hi and retrace_fib >= lo:
            if retrace_fib >= 0.001:
                bucket_label = label
                break
        elif retrace_fib >= lo and retrace_fib < hi:
            bucket_label = label
            break

    # Phase 2: track OUTCOME_WINDOW bars for target/stop hit
    # MFE/MAE measured from bos_close (not from retrace entry)
    mfe_r = 0.0
    mae_r = 0.0
    outcome_r = 0.0

    for offset in range(1, OUTCOME_WINDOW + 1):
        idx = start + offset
        if idx >= n:
            break
        bar = bars[idx]
        if ev.side == "long":
            fav = (float(bar.high) - bos) / sd   # positive = favorable
            adv = (bos - float(bar.low)) / sd    # positive = adverse
            mfe_r = max(mfe_r, fav)
            mae_r = max(mae_r, adv)
            if float(bar.high) >= target_lvl:
                outcome_r = abs(r_mult)
                break
            if float(bar.low) <= stop_lvl:
                outcome_r = -1.0
                break
        else:
            fav = (bos - float(bar.low)) / sd
            adv = (float(bar.high) - bos) / sd
            mfe_r = max(mfe_r, fav)
            mae_r = max(mae_r, adv)
            if float(bar.low) <= target_lvl:
                outcome_r = abs(r_mult)
                break
            if float(bar.high) >= stop_lvl:
                outcome_r = -1.0
                break

    return (bucket_label, retrace_fib, outcome_r, mfe_r, mae_r)


# --------------------------------------------------------------------------- #
# Aggregate per-bucket
# --------------------------------------------------------------------------- #
def _aggregate(events: list[BOSEvent], all_bars: list[Bar]) -> list[BucketResult]:
    by_bucket: dict[str, dict] = {}
    for _, _, label in FIB_BUCKETS:
        by_bucket[label] = {
            "wins": [], "losses": [], "mfes": [], "maes": [], "fibs": [],
        }

    for ev in events:
        label, fib, outcome_r, mfe_r, mae_r = _measure_event(ev, all_bars)
        if label not in by_bucket:
            by_bucket[label] = {"wins": [], "losses": [], "mfes": [], "maes": [], "fibs": []}
        by_bucket[label]["fibs"].append(fib)
        by_bucket[label]["mfes"].append(mfe_r)
        by_bucket[label]["maes"].append(mae_r)
        if outcome_r > 0:
            by_bucket[label]["wins"].append(outcome_r)
        elif outcome_r < 0:
            by_bucket[label]["losses"].append(outcome_r)
        # outcome_r == 0: neither hit in window — skip from WR/PF calc

    results = []
    for _, _, label in FIB_BUCKETS:
        d = by_bucket[label]
        wins = d["wins"]
        losses = d["losses"]
        all_trades = wins + losses
        n = len(d["fibs"])
        n_traded = len(all_trades)
        if n_traded == 0:
            wr = 0.0
            pf = 0.0
        else:
            wr = len(wins) / n_traded
            gross_win = sum(abs(r) for r in wins)
            gross_loss = sum(abs(r) for r in losses)
            pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
        avg_mfe = sum(d["mfes"]) / n if n > 0 else 0.0
        avg_mae = sum(d["maes"]) / n if n > 0 else 0.0
        results.append(BucketResult(
            label=label, n=n, wins=len(wins), losses=len(losses),
            win_rate=wr, pf=pf, avg_mfe=avg_mfe, avg_mae=avg_mae,
            retrace_fibs=sorted(d["fibs"])[:5],
        ))
    return results


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    bot_cfg = load_bot_config(BOT_CONFIG_PATH)
    s = strategy_for(bot_cfg, INSTRUMENT)
    # Research baseline overrides (match B33 / deployed config)
    s = s.model_copy(update={"swing_stop_lookback": 0})

    kz_names = bot_cfg.enabled_killzones
    if kz_names == ["all"] or kz_names is None:
        killzones = [all_day()]
    else:
        killzones = killzones_from_names(kz_names)

    print(f"\n=== B34 Phase 1 — Breaker-Block + OTE Retrace Analysis ===")
    print(f"Instrument: {INSTRUMENT}, stop_buffer={s.stop_buffer}, r_mult={s.r_multiple}")
    print(f"Killzones: {'all_day' if kz_names == ['all'] else kz_names}")
    print(f"Retrace window: {RETRACE_WINDOW} bars | Outcome window: {OUTCOME_WINDOW} bars")
    print(f"Years: {YEARS} (2022 frozen holdout excluded)\n")

    all_bars: list[Bar] = []
    all_signals: list[Signal] = []

    for year in YEARS:
        bars, signals = _replay_year(year, s, killzones)
        all_bars.extend(bars)
        all_signals.extend(signals)
        print(f"  {year}: {len(bars):5d} bars  |  {len(signals):4d} BOS signals")

    print(f"\n  TOTAL: {len(all_bars):6d} bars  |  {len(all_signals):5d} BOS signals")

    events = _build_bos_events(all_bars, all_signals, s.stop_buffer)
    print(f"  Events with valid impulse height: {len(events)}\n")

    results = _aggregate(events, all_bars)

    # Summary table
    print("=== Per-Fib-Bucket Performance (entry at BOS close) ===")
    print(f"  {'Bucket':<22} {'N':>5} {'Traded':>7} {'W':>5} {'L':>5} {'WR':>7} "
          f"{'PF':>6} {'MFE':>7} {'MAE':>7}")
    print(f"  {'-'*22} {'-'*5} {'-'*7} {'-'*5} {'-'*5} {'-'*7} {'-'*6} {'-'*7} {'-'*7}")

    for r in results:
        traded = r.wins + r.losses
        if r.n == 0:
            continue
        ote_marker = " <-- HYPOTHESIS" if "OTE" in r.label else ""
        print(f"  {r.label:<22} {r.n:>5} {traded:>7} {r.wins:>5} {r.losses:>5} "
              f"{r.win_rate:>6.1%} {r.pf:>6.2f} {r.avg_mfe:>7.2f}R {r.avg_mae:>7.2f}R"
              f"{ote_marker}")  # noqa: E501

    # Overall (all non-stopped events)
    non_stopped = [r for r in results if r.label != "stopped_out"]
    total_n = sum(r.n for r in non_stopped)
    total_w = sum(r.wins for r in non_stopped)
    total_l = sum(r.losses for r in non_stopped)
    total_traded = total_w + total_l
    overall_wr = total_w / total_traded if total_traded > 0 else 0.0
    # Use weighted avg r_mult across all non-stopped events (approx 3.5)
    overall_pf_gross_win = sum(r.wins * 3.5 for r in non_stopped)
    overall_pf_gross_loss = total_l
    overall_pf = overall_pf_gross_win / overall_pf_gross_loss if overall_pf_gross_loss > 0 else 0.0

    print(f"\n  {'TOTAL (non-stopped)':<22} {total_n:>5} {total_traded:>7} {total_w:>5} "
          f"{total_l:>5} {overall_wr:>6.1%} {overall_pf:>6.2f}")

    # Find OTE result
    ote_result = next((r for r in results if "OTE" in r.label), None)

    print(f"\n=== GO/NO-GO Decision ===")
    if ote_result and ote_result.n > 0:
        print(f"  OTE bucket (0.62-0.79): n={ote_result.n}, WR={ote_result.win_rate:.1%}, "
              f"PF={ote_result.pf:.2f}, MFE={ote_result.avg_mfe:.2f}R")

        # Find best non-OTE bucket for comparison
        non_ote = [r for r in non_stopped if "OTE" not in r.label and r.n >= 30]
        if non_ote:
            best_non_ote = max(non_ote, key=lambda x: x.pf)
            print(f"  Best non-OTE bucket: {best_non_ote.label}: "
                  f"WR={best_non_ote.win_rate:.1%}, PF={best_non_ote.pf:.2f}")

        ote_pf_threshold = 1.20
        ote_wr_threshold = 0.40
        ote_n_threshold = 30  # minimum events for statistical relevance

        if (ote_result.pf >= ote_pf_threshold
                and ote_result.win_rate >= ote_wr_threshold
                and ote_result.n >= ote_n_threshold):
            # Also check: OTE is materially better than non-OTE buckets
            if non_ote:
                avg_non_ote_pf = sum(r.pf * r.n for r in non_ote) / sum(r.n for r in non_ote)
                if ote_result.pf > avg_non_ote_pf * 1.15:  # 15% better than weighted avg
                    verdict = "GO — OTE bucket shows material separation; proceed to Phase 2"
                else:
                    verdict = (f"NO-GO — OTE PF ({ote_result.pf:.2f}) meets threshold but not "
                               f"materially better than other buckets (avg non-OTE PF: "
                               f"{avg_non_ote_pf:.2f}). MGC fib no-edge finding replicates.")
            else:
                verdict = "GO (insufficient non-OTE data for comparison)"
        else:
            fails = []
            if ote_result.n < ote_n_threshold:
                fails.append(f"n={ote_result.n} < {ote_n_threshold} (insufficient data)")
            if ote_result.pf < ote_pf_threshold:
                fails.append(f"PF={ote_result.pf:.2f} < {ote_pf_threshold}")
            if ote_result.win_rate < ote_wr_threshold:
                fails.append(f"WR={ote_result.win_rate:.1%} < {ote_wr_threshold:.0%}")
            verdict = f"NO-GO — {'; '.join(fails)}. Do NOT build Phase 2 engine."
    else:
        verdict = "NO-GO — insufficient OTE bucket data to evaluate."

    print(f"\n  VERDICT: {verdict}\n")

    # Diagnostic: distribution of retrace depths
    all_fibs = []
    for r in non_stopped:
        all_fibs.extend(r.retrace_fibs)
    print(f"=== Diagnostic: BOS signal counts and retrace frequency ===")
    for r in results:
        if r.n == 0:
            continue
        pct = r.n / len(events) * 100 if events else 0
        print(f"  {r.label:<22}: {r.n:>5} ({pct:>4.1f}%)")

    print(f"\n  MGC fib finding (prior): 'every bucket was equally negative'.")
    print(f"  MNQ result: see OTE bucket PF above vs others.")
    print()


if __name__ == "__main__":
    main()
