"""
B82 pipeline driver: ORB pre-market break gate.

Phase A: iFVG combined engine, deployed config r=2.5 (equity_b57/ — pre-existing).
Phase B: ORB-reentry r=0.75 WITH orb_require_pm_break=True (generated here).

Compares to B42 ($549/mo, sust 3.23x) and B57 ($566/mo, sust 3.54x) using the
standard two-phase pipeline_economics(). Includes sub-period breakdown (2021+2023
vs 2024-2026) to assess regime dependence.

Usage:
    python scripts/run_b82_pipeline.py
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

EQUITY_DIR_B57 = _REPO_ROOT / "research" / "equity_b57"
EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
EQUITY_DIR_B82 = _REPO_ROOT / "research" / "equity_b82"
BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable

YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
EARLY_YEARS = ["2021", "2023"]
LATE_YEARS = ["2024", "2025", "2026"]
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}
B57_BENCHMARK = {"net_per_month": 566.0, "sustainability": 3.54}


def _generate_phase_b_equity(year: str, out_path: Path) -> None:
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "0.75",
        "--partial-r", "0",
        "--set", "engine=orb",
        "--set", "r_multiple=2.5",
        "--set", "orb_reentry_after_stop=True",
        "--set", "orb_require_pm_break=True",
        "--set", "swing_stop_lookback=0",
        "--out", str(out_path),
    ]
    print(f"    Generating {out_path.name}...", end=" ", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if result.returncode != 0:
        print(f"ERROR\n{result.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_phase_b_csvs() -> None:
    EQUITY_DIR_B82.mkdir(parents=True, exist_ok=True)
    print("=== Generating B82 Phase B equity CSVs (ORB-reentry r0.75 + PM break) ===")
    for year in YEARS:
        out_path = EQUITY_DIR_B82 / f"orb_reentry_pm_r0p75_{year}.csv"
        if out_path.exists():
            print(f"  Skipping {out_path.name} (exists)")
        else:
            _generate_phase_b_equity(year, out_path)
    print()


def stitch(equity_dir: Path, prefix: str, years: list[str] = YEARS) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in years:
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


def phase_stats(equity_dir: Path, prefix: str, years: list[str] = YEARS) -> dict:
    curve = stitch(equity_dir, prefix, years)
    if not curve:
        return {}
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT)
    avg_days = (Decimal(str(trading_days)) / Decimal(str(c["attempts"]))
                if c["attempts"] else Decimal("0"))
    attempts_per_funded = (Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
                           if c["passes"] else Decimal("inf"))
    combine_days = attempts_per_funded * avg_days
    reset_fee = attempts_per_funded * COMBINE_RESET_FEE
    avg_funded_days = (Decimal(str(trading_days)) / Decimal(str(x["accounts"]))
                       if x["accounts"] else Decimal("0"))
    net_per_funded = (x["net_payouts"] / Decimal(str(x["accounts"]))
                      if x["accounts"] else Decimal("0"))
    sustainability = (Decimal(str(c["passes"])) / Decimal(str(x["busts"]))
                      if x["busts"] else Decimal("inf"))
    return {
        "trading_days": trading_days,
        "c_attempts": c["attempts"], "c_passes": c["passes"],
        "c_avg_days": float(avg_days),
        "c_days_per_funded": float(combine_days),
        "c_reset_fee": float(reset_fee),
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
    cycle_days = Decimal(str(a["c_days_per_funded"])) + Decimal(str(b["x_avg_days"]))
    net_per_day = net_per_cycle / cycle_days if cycle_days else Decimal("0")
    net_per_month = net_per_day * TRADING_DAYS_PER_MONTH
    sustainability = (Decimal(str(a["c_passes"])) / Decimal(str(b["x_busts"]))
                      if b["x_busts"] else Decimal("inf"))
    return {
        "reset_cost": float(reset_cost),
        "xfa_net": float(xfa_net),
        "cycle_days": float(cycle_days),
        "net_per_month": float(net_per_month),
        "sustainability": float(sustainability),
    }


def pf_from_funded_stats(stats: dict) -> float:
    """Approximate PF from net payouts and bust count (rough quality indicator)."""
    # Use x_net_payouts as a proxy — positive is good
    return stats.get("x_net_payouts", 0.0)


def main() -> None:
    _ensure_phase_b_csvs()

    print("=== PHASE B STATS: ORB-reentry r0.75 (full 5y) ===")
    s_b_baseline = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75")
    s_b_pm = phase_stats(EQUITY_DIR_B82, "orb_reentry_pm_r0p75")

    print(f"  Baseline (no PM gate): {s_b_baseline['x_busts']}/{s_b_baseline['x_accounts']} busts, "
          f"${s_b_baseline['x_net_per_account']:.0f}/acct, {s_b_baseline['x_avg_days']:.1f}d/acct")
    print(f"  PM break gate:         {s_b_pm['x_busts']}/{s_b_pm['x_accounts']} busts, "
          f"${s_b_pm['x_net_per_account']:.0f}/acct, {s_b_pm['x_avg_days']:.1f}d/acct")
    print()

    print("=== SUB-PERIOD BREAKDOWN (regime dependence check) ===")
    for period_label, period_years in [("2021+2023", EARLY_YEARS), ("2024-2026", LATE_YEARS)]:
        sb_base = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75", period_years)
        sb_pm = phase_stats(EQUITY_DIR_B82, "orb_reentry_pm_r0p75", period_years)
        net_base = sb_base.get("x_net_payouts", 0.0)
        net_pm = sb_pm.get("x_net_payouts", 0.0)
        print(f"  {period_label}: "
              f"baseline busts={sb_base.get('x_busts',0)}, net=${net_base:.0f}  |  "
              f"PM gate busts={sb_pm.get('x_busts',0)}, net=${net_pm:.0f}  |  "
              f"delta=${net_pm - net_base:.0f}")
    print()

    print("=== TWO-PHASE PIPELINE (Phase A = B57 deployed r2.5) ===")
    s_a = phase_stats(EQUITY_DIR_B57, "r2p5")
    print(f"Phase A (B57): {s_a['c_passes']}/{s_a['c_attempts']} combine passes, "
          f"${s_a['c_reset_fee']:.0f}/funded, {s_a['c_days_per_funded']:.1f}d/funded\n")

    configs = [
        ("B57 baseline (no PM gate)", s_b_baseline),
        ("B82 PM break gate",         s_b_pm),
    ]

    print("=" * 78)
    print("PIPELINE ECONOMICS (Phase A = B57, Phase B varies, haircut $200)")
    print("=" * 78)
    rows = {}
    for label, s_b in configs:
        eco = pipeline_economics(s_a, s_b)
        rows[label] = eco
        print(f"  {label:<28} | Phase B {s_b['x_busts']:>2} busts "
              f"${s_b['x_net_per_account']:>5.0f}/acct | "
              f"${eco['net_per_month']:>6.0f}/mo sust={eco['sustainability']:>5.2f}x")

    print()
    if "B57 baseline (no PM gate)" in rows and "B82 PM break gate" in rows:
        base_eco = rows["B57 baseline (no PM gate)"]
        pm_eco = rows["B82 PM break gate"]
        d_npm = pm_eco["net_per_month"] - base_eco["net_per_month"]
        d_sust = pm_eco["sustainability"] - base_eco["sustainability"]
        beats_b57 = pm_eco["net_per_month"] > B57_BENCHMARK["net_per_month"] and \
                    pm_eco["sustainability"] > B57_BENCHMARK["sustainability"]
        beats_b42 = pm_eco["net_per_month"] > B42_BENCHMARK["net_per_month"] and \
                    pm_eco["sustainability"] > B42_BENCHMARK["sustainability"]
        worse_than_b42 = pm_eco["net_per_month"] <= B42_BENCHMARK["net_per_month"] and \
                         pm_eco["sustainability"] <= B42_BENCHMARK["sustainability"]

        print(f"  B82 vs baseline: d$/mo={d_npm:+.0f}, dsust={d_sust:+.2f}x")
        print()
        if beats_b57:
            print("VERDICT: CANDIDATE (STRETCH) — beats B57 on BOTH $/mo AND sust.")
        elif beats_b42:
            print("VERDICT: CANDIDATE — beats B42 on BOTH $/mo AND sust (above B42 floor).")
        elif worse_than_b42:
            print("VERDICT: REJECTED — stop rule fires (both $/mo and sust <= B42).")
        else:
            print("VERDICT: MIXED/NO-GO — does not beat B42 on both metrics.")


if __name__ == "__main__":
    main()
