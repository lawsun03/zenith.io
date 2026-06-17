"""
B55 driver: Phase A config-optimized full funded-pipeline benchmark.

Tests whether fixing Phase A settings (swing_stop_lookback=0, target_clarity_mode=reject)
vs B42 deployed settings (lookback=30, target_clarity=off) materially improves pipeline
economics. B52 showed +50% monthly combine pass rate from these config changes.

Phase A equity: equity_b55/phase_a_opt_{year}.csv
  Config: engine=combined, ifvg_entry_mode=close, killzones=all, risk=1.0%,
  partial_r=1.5, swing_stop_lookback=0, target_clarity_mode=reject,
  min_absolute_body=5.0 (MNQ), stop_buffer=3.0 (MNQ)
Phase B equity: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21)

Success criteria (vs B42: $549/mo, sust=3.23x):
  - PRIMARY: $/mo AND sust both improve vs B42 -> config change directly deployable
  - SECONDARY: sust improves even if $/mo marginally drops -> pipeline health priority
  - If neither improves: B52's combine-harness result does not transfer to pipeline

Usage:
    python scripts/run_b55_pipeline.py
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

EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
EQUITY_DIR_B42 = _REPO_ROOT / "research" / "equity_b42"
EQUITY_DIR_B55 = _REPO_ROOT / "research" / "equity_b55"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}
B21_BENCHMARK = {"net_per_month": 497.0, "sustainability": 2.62}

BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable


def _generate_equity_csv(year: str, out_path: Path) -> None:
    """Generate Phase A equity CSV with optimized config (lookback=0, target_clarity=reject)."""
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "1.0",
        "--partial-r", "1.5",
        "--set", "swing_stop_lookback=0",
        "--set", "target_clarity_mode=reject",
        "--out", str(out_path),
    ]
    print(f"  Generating {out_path.name}...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _generate_holdout_2022(out_path: Path) -> None:
    """Generate 2022 confirmatory holdout equity CSV."""
    bars = BARS_YEARLY / "bars_MNQ_dbv_2022.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "1.0",
        "--partial-r", "1.5",
        "--set", "swing_stop_lookback=0",
        "--set", "target_clarity_mode=reject",
        "--out", str(out_path),
    ]
    print(f"  Generating {out_path.name} (2022 holdout)...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_equity_csvs() -> None:
    print("=== Generating Phase A optimized equity CSVs (if missing) ===")
    for year in YEARS:
        out_path = EQUITY_DIR_B55 / f"phase_a_opt_{year}.csv"
        if out_path.exists():
            print(f"  Skipping {out_path.name} (exists)")
        else:
            _generate_equity_csv(year, out_path)

    # 2022 holdout (generated last, separate from main analysis)
    holdout = EQUITY_DIR_B55 / "phase_a_opt_2022.csv"
    if holdout.exists():
        print(f"  Skipping {holdout.name} (exists)")
    else:
        _generate_holdout_2022(holdout)
    print()


def stitch(equity_dir: Path, prefix: str, year_list: list[str] = YEARS) -> list[tuple[datetime, Decimal]]:
    """Stitch per-year equity CSVs into a single continuous curve."""
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


def _pf_from_csv(path: Path) -> float | None:
    """Compute PF from equity CSV daily P&L."""
    if not path.exists():
        return None
    rows = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            rows.append(Decimal(row["equity"]))
    if len(rows) < 2:
        return None
    wins = Decimal("0")
    losses = Decimal("0")
    for i in range(1, len(rows)):
        pnl = rows[i] - rows[i - 1]
        if pnl > 0:
            wins += pnl
        elif pnl < 0:
            losses += abs(pnl)
    if losses == 0:
        return float("inf")
    return float(wins / losses)


def phase_stats(equity_dir: Path, prefix: str, label: str) -> dict:
    """Return combine and xfa stats for this config at HAIRCUT."""
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
    """Compute per-cycle and monthly economics for A->B coupling."""
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
    EQUITY_DIR_B55.mkdir(parents=True, exist_ok=True)
    _ensure_equity_csvs()

    print("=== Phase A: B55 optimized config ===")
    a_b55 = phase_stats(EQUITY_DIR_B55, "phase_a_opt", "B55 Phase A (lookback=0, clarity=reject)")
    if a_b55:
        print(
            f"  {a_b55['label']}: combine {a_b55['c_passes']}/{a_b55['c_attempts']} "
            f"(avg {a_b55['c_avg_days']:.1f}d/attempt, {a_b55['c_days_per_funded']:.1f}d/funded, "
            f"${a_b55['c_reset_fee']:.0f}/funded)"
        )

    print("\n=== Phase A: B42 deployed baseline (for reference) ===")
    a_b42 = phase_stats(EQUITY_DIR_B42, "deployed_r1p0", "B42 Phase A deployed (lookback=30, clarity=off)")
    if a_b42:
        print(
            f"  {a_b42['label']}: combine {a_b42['c_passes']}/{a_b42['c_attempts']} "
            f"(avg {a_b42['c_avg_days']:.1f}d/attempt, {a_b42['c_days_per_funded']:.1f}d/funded, "
            f"${a_b42['c_reset_fee']:.0f}/funded)"
        )

    print("\n=== Phase B: ORB-reentry r=0.75 (B21, unchanged) ===")
    b_label = "ORB-reentry r0.75 (B21 Phase B)"
    s_b = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75", b_label)
    if s_b:
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
    print("=" * 110)
    print("PIPELINE COMPARISON: B55 optimized vs B42 deployed Phase A")
    print("Phase B fixed: ORB-reentry r=0.75")
    print("=" * 110)
    hdr = (f"{'Phase A Config':<50} {'Reset$':>7} {'XFA$':>7} "
           f"{'Net/cyc':>9} {'Cycle d':>9} {'Net/mo':>8} {'Sust':>8} {'Verdict'}")
    print(hdr)
    print("-" * 110)

    for a_stats, a_desc in [(a_b55, "B55 (optimized)"), (a_b42, "B42 (deployed baseline)")]:
        if not a_stats:
            continue
        econ = pipeline_economics(a_stats, s_b)
        beats_b42 = (econ["net_per_month"] >= B42_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B42_BENCHMARK["sustainability"])
        beats_b21 = (econ["net_per_month"] >= B21_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B21_BENCHMARK["sustainability"])
        if beats_b42:
            verdict = "BEATS B42 ***"
        elif beats_b21:
            verdict = "beats B21"
        else:
            verdict = "below B21"
        print(f"{a_desc:<50} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  "
              f"${econ['net_per_cycle']:>7.0f}  {econ['cycle_days']:>7.1f}d  "
              f"${econ['net_per_month']:>6.0f}  {econ['sustainability']:>6.2f}x  {verdict}")

    print()
    # 2022 holdout
    holdout = EQUITY_DIR_B55 / "phase_a_opt_2022.csv"
    pf_2022 = _pf_from_csv(holdout)
    if pf_2022 is not None:
        print(f"2022 holdout: Phase A PF = {pf_2022:.3f} "
              f"({'POSITIVE' if pf_2022 >= 1.0 else 'NEGATIVE'} in frozen holdout year)")

    print()
    b55_econ = pipeline_economics(a_b55, s_b) if a_b55 else None
    b42_econ = pipeline_economics(a_b42, s_b) if a_b42 else None
    print("=" * 110)
    print("VERDICT")
    print("=" * 110)
    if b55_econ and b42_econ:
        delta_mo = b55_econ["net_per_month"] - b42_econ["net_per_month"]
        delta_sust = b55_econ["sustainability"] - b42_econ["sustainability"]
        print(f"  B55 vs B42: $/mo {b55_econ['net_per_month']:.0f} vs {b42_econ['net_per_month']:.0f} "
              f"({'+' if delta_mo >= 0 else ''}{delta_mo:.0f})")
        print(f"              sust {b55_econ['sustainability']:.2f}x vs {b42_econ['sustainability']:.2f}x "
              f"({'+' if delta_sust >= 0 else ''}{delta_sust:.2f})")
        if b55_econ["net_per_month"] >= b42_econ["net_per_month"] and b55_econ["sustainability"] >= b42_econ["sustainability"]:
            print("  => CANDIDATE: B55 beats B42 on BOTH metrics. Config change deployable.")
            print("     Monday action: set swing_stop_lookback=0 AND target_clarity_mode=reject in bot_config.json")
        elif b55_econ["sustainability"] >= b42_econ["sustainability"]:
            print("  => PARTIAL CANDIDATE: sust improves (pipeline healthier) but $/mo drops.")
            print("     Consider deploying config change for pipeline stability.")
        elif b55_econ["net_per_month"] >= b42_econ["net_per_month"]:
            print("  => MIXED: $/mo improves but sust drops. Config change is a risk tradeoff.")
        else:
            print("  => REJECTED: B55 worse than B42 on BOTH metrics. B52 combine-harness result")
            print("     does not transfer to full pipeline economics.")

    print()
    print("B42 baseline: $549/mo, sust=3.23x (deployed config Phase A)")
    print("B21 baseline: $497/mo, sust=2.62x (research baseline, reference)")
    print("Phase A delta: swing_stop_lookback 30->0, target_clarity_mode off->reject")


if __name__ == "__main__":
    main()
