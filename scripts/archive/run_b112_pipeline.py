"""
B112 driver: Corrected Phase A two-phase pipeline benchmark.

Tests whether fixing two config parameters (target_clarity_mode=reject +
swing_stop_lookback=0) improves the funded pipeline over the deployed B42
baseline, with zero code changes.

Deployed B42 Phase A config (from bot_config.json as of 2026-06-14):
  engine=combined, ifvg_entry_mode=close, partial_r=1.5, swing_stop_lookback=30,
  target_clarity_mode=off, killzones=all, risk=1.0%, r_multiple=3.5 (MNQ iFVG),
  orb_r_multiple=2.5 (MNQ ORB).

Corrected B112 Phase A config (only two params changed):
  target_clarity_mode=reject, swing_stop_lookback=0
  (everything else identical to B42 deployed)

Phase B equity: equity_b21/orb_reentry_r0p75_{year}.csv (same as B42, B21)

Success criteria (vs B42 deployed: $387/mo, 3.50x sust per wk9-holdout-cpi):
  - PRIMARY: corrected Phase A funded_sim passes > 42 (B42 5y baseline)
  - SECONDARY: pipeline $/mo or sust improves vs deployed (B42+B21) pair
  - Informational either way: pass count is key input for Lawrence Monday config decision.

Usage:
    python scripts/run_b112_pipeline.py
"""
from __future__ import annotations

import csv
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)

EQUITY_DIR_B21  = _REPO_ROOT / "research" / "equity_b21"
EQUITY_DIR_B42  = _REPO_ROOT / "research" / "equity_b42"
EQUITY_DIR_B112 = _REPO_ROOT / "research" / "equity_b112"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B42_BENCHMARK = {"net_per_month": 387.0, "sustainability": 3.50, "phase_a_passes": 42}

BARS_YEARLY   = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable


def _generate_corrected_equity_csv(year: str, out_path: Path) -> None:
    """Generate corrected Phase A equity CSV (target_clarity_mode=reject, swing_stop_lookback=0)."""
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "1.0",
        "--partial-r", "1.5",
        "--set", "target_clarity_mode=reject",
        "--set", "swing_stop_lookback=0",
        "--out", str(out_path),
    ]
    print(f"  Generating {out_path.name}...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_corrected_equity_csvs() -> None:
    EQUITY_DIR_B112.mkdir(parents=True, exist_ok=True)
    print("=== Generating corrected Phase A equity CSVs (if missing) ===")
    for year in YEARS:
        out_path = EQUITY_DIR_B112 / f"corrected_r1p0_{year}.csv"
        if out_path.exists():
            print(f"  Skipping {out_path.name} (exists)")
        else:
            _generate_corrected_equity_csv(year, out_path)
    print()


def stitch(equity_dir: Path, prefix: str, year_list: list[str] = YEARS) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in year_list:
        p = equity_dir / f"{prefix}_{year}.csv"
        if not p.exists():
            print(f"  WARNING: missing {p}", file=sys.stderr)
            continue
        seg: list[tuple[datetime, Decimal]] = []
        with p.open(newline="") as f:
            for row in csv.DictReader(f):
                seg.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
        if not seg:
            continue
        if curve:
            offset = curve[-1][1] - BASELINE
        curve.extend((ts, eq + offset) for ts, eq in seg)
    return curve


def phase_stats(equity_dir: Path, prefix: str, label: str) -> dict:
    curve = stitch(equity_dir, prefix)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    avg_days_per_attempt = (
        Decimal(str(trading_days)) / Decimal(str(c["attempts"]))
        if c["attempts"] else Decimal("0")
    )
    attempts_per_funded = (
        Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
        if c["passes"] else Decimal("inf")
    )
    combine_days_per_funded = attempts_per_funded * avg_days_per_attempt
    reset_fee_per_funded = attempts_per_funded * COMBINE_RESET_FEE
    avg_funded_days = (
        Decimal(str(trading_days)) / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    net_per_funded = (
        x["net_payouts"] / Decimal(str(x["accounts"]))
        if x["accounts"] else Decimal("0")
    )
    sustainability = (
        Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
        if x["busts"] else Decimal("inf")
    )
    return {
        "label": label, "prefix": prefix,
        "trading_days": trading_days,
        "c_attempts": c["attempts"], "c_passes": c["passes"],
        "c_avg_days": float(avg_days_per_attempt),
        "c_attempts_per_funded": float(attempts_per_funded),
        "c_days_per_funded": float(combine_days_per_funded),
        "c_reset_fee": float(reset_fee_per_funded),
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_payouts": float(x["net_payouts"]),
        "x_net_per_account": float(net_per_funded),
        "x_avg_days": float(avg_funded_days),
        "sustainability": float(sustainability),
    }


def pipeline_economics(a: dict, b: dict) -> dict:
    reset_cost = Decimal(str(a["c_reset_fee"]))
    xfa_net = Decimal(str(b["x_net_per_account"]))
    net_per_cycle = xfa_net - reset_cost
    combine_days = Decimal(str(a["c_days_per_funded"]))
    funded_days = Decimal(str(b["x_avg_days"]))
    cycle_days = combine_days + funded_days
    net_per_day = net_per_cycle / cycle_days if cycle_days else Decimal("0")
    net_per_month = net_per_day * TRADING_DAYS_PER_MONTH
    sustainability = (
        Decimal(str(a["c_passes"])) / Decimal(str(b["x_busts"]))
        if b["x_busts"] else Decimal("inf")
    )
    return {
        "reset_cost": float(reset_cost),
        "xfa_net": float(xfa_net),
        "net_per_cycle": float(net_per_cycle),
        "cycle_days": float(cycle_days),
        "net_per_month": float(net_per_month),
        "sustainability": float(sustainability),
    }


def main() -> None:
    _ensure_corrected_equity_csvs()

    print("=== Phase A: Loading equity stats ===")
    phase_a_configs = [
        (EQUITY_DIR_B112, "corrected_r1p0", "B112 corrected (target_clarity=reject + lookback=0)"),
        (EQUITY_DIR_B42,  "deployed_r1p0",  "B42 deployed (target_clarity=off + lookback=30)"),
    ]

    stats: dict[str, dict] = {}
    for equity_dir, prefix, label in phase_a_configs:
        print(f"  Loading {label}...", end=" ", flush=True)
        s = phase_stats(equity_dir, prefix, label)
        if not s:
            print("MISSING — skipped")
            continue
        stats[label] = s
        print(
            f"combine {s['c_passes']}/{s['c_attempts']} "
            f"(avg {s['c_avg_days']:.1f}d/attempt, {s['c_days_per_funded']:.1f}d/funded, "
            f"${s['c_reset_fee']:.0f}/funded)"
        )

    print("\n=== Phase B: Loading ORB-reentry r=0.75 equity stats ===")
    b_label = "ORB-reentry r0.75 (B21 Phase B)"
    s_b = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75", b_label)
    if s_b:
        stats[b_label] = s_b
        print(
            f"  {b_label}: xfa busts={s_b['x_busts']}/{s_b['x_accounts']} "
            f"net ${s_b['x_net_payouts']:.0f} "
            f"(${s_b['x_net_per_account']:.0f}/acct, {s_b['x_avg_days']:.1f}d/acct) "
            f"sust={s_b['sustainability']:.2f}x"
        )
    else:
        print("  MISSING — cannot compute two-phase results")
        return

    print()
    print("=" * 120)
    print("B112 PHASE A COMPARISON (corrected vs deployed)")
    print("=" * 120)
    deployed_label = "B42 deployed (target_clarity=off + lookback=30)"
    corrected_label = "B112 corrected (target_clarity=reject + lookback=0)"
    if deployed_label in stats and corrected_label in stats:
        d = stats[deployed_label]
        c = stats[corrected_label]
        pass_delta = c["c_passes"] - d["c_passes"]
        pass_pct = (pass_delta / d["c_passes"] * 100) if d["c_passes"] else 0.0
        print(f"  Deployed Phase A: {d['c_passes']} passes / {d['c_attempts']} attempts "
              f"({d['c_passes']/d['c_attempts']*100:.1f}%) "
              f"avg {d['c_avg_days']:.1f}d/attempt")
        print(f"  Corrected Phase A: {c['c_passes']} passes / {c['c_attempts']} attempts "
              f"({c['c_passes']/c['c_attempts']*100:.1f}%) "
              f"avg {c['c_avg_days']:.1f}d/attempt")
        print(f"  Delta: {pass_delta:+d} passes ({pass_pct:+.1f}%)")
        if c["c_passes"] > B42_BENCHMARK["phase_a_passes"]:
            print(f"  *** Phase A pass count improvement: {c['c_passes']} > {B42_BENCHMARK['phase_a_passes']} (B42 baseline) ***")
        else:
            print(f"  Phase A pass count: {c['c_passes']} vs {B42_BENCHMARK['phase_a_passes']} (B42 baseline) -- no improvement")

    print()
    print("=" * 120)
    print("B112 TWO-PHASE PIPELINE MATRIX (Phase A -> ORB-reentry r0.75 Phase B)")
    print("Sustainability = Phase_A.combine_passes / Phase_B.xfa_busts")
    print("=" * 120)
    hdr = (f"{'Phase A Config':<55} {'Reset$':>7} {'XFA$':>7} "
           f"{'Net/cyc':>9} {'Cycle d':>9} {'Net/mo':>8} {'Sust':>8}")
    print(hdr)
    print("-" * 120)

    rows = []
    for equity_dir, prefix, a_label in phase_a_configs:
        if a_label not in stats:
            continue
        a_s = stats[a_label]
        econ = pipeline_economics(a_s, s_b)
        rows.append((econ["net_per_month"], a_label, econ))

    rows.sort(key=lambda r: -r[0])
    for _, a_label, econ in rows:
        beats_b42 = (econ["net_per_month"] > B42_BENCHMARK["net_per_month"]
                     or econ["sustainability"] > B42_BENCHMARK["sustainability"])
        flag = " *** BEATS B42" if beats_b42 else ""
        print(f"{a_label:<55} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  "
              f"${econ['net_per_cycle']:>7.0f}  {econ['cycle_days']:>7.1f}d  "
              f"${econ['net_per_month']:>6.0f}  {econ['sustainability']:>6.2f}x{flag}")

    print()
    print("=" * 120)
    print("VERDICT")
    print("=" * 120)
    if corrected_label in stats:
        c_s = stats[corrected_label]
        econ = pipeline_economics(c_s, s_b)
        pass_improvement = c_s["c_passes"] > B42_BENCHMARK["phase_a_passes"]
        pipeline_improvement = (econ["net_per_month"] > B42_BENCHMARK["net_per_month"]
                                or econ["sustainability"] > B42_BENCHMARK["sustainability"])
        print(f"  Corrected Phase A passes: {c_s['c_passes']} (deployed: {B42_BENCHMARK['phase_a_passes']})")
        print(f"  Pipeline $/mo: ${econ['net_per_month']:.0f} (deployed: ${B42_BENCHMARK['net_per_month']:.0f})")
        print(f"  Sustainability: {econ['sustainability']:.2f}x (deployed: {B42_BENCHMARK['sustainability']:.2f}x)")
        if pass_improvement and pipeline_improvement:
            print("  CANDIDATE — both Phase A passes and pipeline metrics improve with corrected config.")
            print("  ACTION: Lawrence can deploy target_clarity_mode=reject + swing_stop_lookback=0 on Monday.")
        elif pass_improvement:
            print("  INFORMATIONAL — Phase A passes improve but pipeline metrics mixed.")
            print("  ACTION: Still worth making the config change; more Phase A passes sustains funded accounts.")
        else:
            print("  NEUTRAL — corrected config does not improve Phase A passes or pipeline metrics.")
            print("  ACTION: No config change recommended based on this benchmark.")

    print()
    print("Config changes tested (zero code changes — config only):")
    print("  target_clarity_mode: 'off' (deployed) -> 'reject' (corrected)")
    print("  swing_stop_lookback: 30 (deployed) -> 0 (corrected)")
    print()
    print("Source: B26 Lessons 58-59 (calendar-month harness: deployed 6/61 10% -> corrected 11/61 18%).")
    print("B112 quantifies this in the funded pipeline (continuous attempts, not monthly snapshots).")


if __name__ == "__main__":
    main()
