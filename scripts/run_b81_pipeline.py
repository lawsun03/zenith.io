"""
B81 pipeline driver: iFVG within-day direction-continuation gate.

Phase A: iFVG combined engine with ifvg_suppress_same_direction_repeat=True, r=2.5.
Phase B: B21 ORB-reentry r0.75, unchanged.

Compares gate vs B57 baseline (r=2.5) using the standard two-phase pipeline_economics().

Usage:
    python scripts/run_b81_pipeline.py
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
EQUITY_DIR_B81 = _REPO_ROOT / "research" / "equity_b81"
BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable

YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}
B57_BENCHMARK = {"net_per_month": 566.0, "sustainability": 3.54}


def _generate_phase_a_equity(year: str, out_path: Path) -> None:
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "1.0",
        "--partial-r", "1.5",
        "--set", "r_multiple=2.5",
        "--set", "ifvg_suppress_same_direction_repeat=True",
        "--out", str(out_path),
    ]
    print(f"    Generating {out_path.name}...", end=" ", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if result.returncode != 0:
        print(f"ERROR\n{result.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_phase_a_csvs() -> None:
    EQUITY_DIR_B81.mkdir(parents=True, exist_ok=True)
    print("=== Generating B81 gate Phase A equity CSVs (if missing) ===")
    for year in YEARS:
        out_path = EQUITY_DIR_B81 / f"gate_r2p5_{year}.csv"
        if out_path.exists():
            print(f"  Skipping {out_path.name} (exists)")
        else:
            _generate_phase_a_equity(year, out_path)
    print()


def stitch(equity_dir: Path, prefix: str) -> list[tuple[datetime, Decimal]]:
    curve: list[tuple[datetime, Decimal]] = []
    offset = Decimal("0")
    for year in YEARS:
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


def phase_stats(equity_dir: Path, prefix: str) -> dict:
    curve = stitch(equity_dir, prefix)
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


def _row(label: str, a: dict, eco: dict) -> str:
    return (f"  {label:<24} A {a['c_passes']:>2}/{a['c_attempts']:<3} "
            f"reset=${a['c_reset_fee']:>4.0f} | "
            f"${eco['net_per_month']:>6.0f}/mo sust={eco['sustainability']:>5.2f}x")


def main() -> None:
    _ensure_phase_a_csvs()

    s_b = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75")
    print(f"Phase B (ORB-reentry r0.75): {s_b['x_busts']}/{s_b['x_accounts']} busts, "
          f"${s_b['x_net_per_account']:.0f}/acct, {s_b['x_avg_days']:.1f}d/acct\n")

    configs = [
        ("B57 baseline r2.5", EQUITY_DIR_B57, "r2p5"),
        ("B81 gate r2.5",     EQUITY_DIR_B81, "gate_r2p5"),
    ]

    print("=" * 78)
    print("PIPELINE ECONOMICS (Phase A varies, Phase B = ORB-reentry r0.75, haircut $200)")
    print("=" * 78)
    rows = {}
    for label, d, prefix in configs:
        s_a = phase_stats(d, prefix)
        if not s_a:
            print(f"  {label}: MISSING Phase A CSVs"); continue
        eco = pipeline_economics(s_a, s_b)
        rows[label] = (s_a, eco)
        print(_row(label, s_a, eco))

    print()
    if "B57 baseline r2.5" in rows and "B81 gate r2.5" in rows:
        _, base_eco = rows["B57 baseline r2.5"]
        _, gate_eco = rows["B81 gate r2.5"]
        d_npm = gate_eco["net_per_month"] - base_eco["net_per_month"]
        d_sust = gate_eco["sustainability"] - base_eco["sustainability"]
        beats_b57 = gate_eco["net_per_month"] > B57_BENCHMARK["net_per_month"] and \
                    gate_eco["sustainability"] > B57_BENCHMARK["sustainability"]
        worse_both_b42 = gate_eco["net_per_month"] <= B42_BENCHMARK["net_per_month"] and \
                         gate_eco["sustainability"] <= B42_BENCHMARK["sustainability"]
        print(f"  B81 gate vs B57 baseline: d$/mo={d_npm:+.0f}, dsust={d_sust:+.2f}x")
        print()
        if beats_b57:
            print("VERDICT: CANDIDATE — B81 gate beats B57 on BOTH $/mo AND sust.")
        elif worse_both_b42:
            print("VERDICT: REJECTED — B81 gate worse than B42 on BOTH metrics (stop rule).")
        else:
            print("VERDICT: MIXED/NO-GO — B81 gate does not beat B57 on both metrics.")


if __name__ == "__main__":
    main()
