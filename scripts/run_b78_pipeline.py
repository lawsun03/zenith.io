"""
B78 driver: Phase A risk=0.75% pipeline sensitivity.

Tests whether reducing combine-phase risk from 1.0% -> 0.75% while holding
r_multiple=2.5 (B57 config) improves funded-pipeline economics.

Phase A equity: equity_b78/deployed_r75pct_{year}.csv (risk=0.75%, r=2.5)
Phase B equity: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21)

Hypothesis: smaller position size -> smaller MLL-risk-per-trade -> higher
combine pass rate + lower XFA bust rate, net improvement in $/mo and sust.

Success criteria (vs B57: $566/mo, sust 3.54x):
  - PRIMARY: $/mo > $566 AND sust > 3.54x (beats B57 on BOTH)
  - SECONDARY: $/mo >= $549 AND sust >= 3.23x (beats B42)
  - Stop rule: BOTH metrics below B42 -> reject

Usage:
    python scripts/run_b78_pipeline.py
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
EQUITY_DIR_B78 = _REPO_ROOT / "research" / "equity_b78"
YEARS = ["2021", "2023", "2024", "2025", "2026"]
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}
B57_BENCHMARK = {"net_per_month": 566.0, "sustainability": 3.54}

BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable


def _generate_equity_csv(year: str, out_path: Path) -> None:
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "0.75",
        "--partial-r", "1.5",
        "--set", "r_multiple=2.5",
        "--out", str(out_path),
    ]
    print(f"  Generating {out_path.name}...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_equity_csvs() -> None:
    print("=== Generating B78 equity CSVs (if missing) ===")
    for year in YEARS:
        out_path = EQUITY_DIR_B78 / f"deployed_r75pct_{year}.csv"
        if out_path.exists():
            print(f"  Skipping {out_path.name} (exists)")
        else:
            _generate_equity_csv(year, out_path)
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
    _ensure_equity_csvs()

    print("=== Phase B: ORB-reentry r=0.75 (B21, fixed) ===")
    b_label = "ORB-reentry r0.75 (B21 Phase B)"
    s_b = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75", b_label)
    if not s_b:
        print("  MISSING Phase B equity — cannot compute two-phase results")
        return
    print(
        f"  {b_label}: xfa busts={s_b['x_busts']}/{s_b['x_accounts']} "
        f"net ${s_b['x_net_payouts']:.0f} "
        f"(${s_b['x_net_per_account']:.0f}/acct, {s_b['x_avg_days']:.1f}d/acct)"
    )

    print()
    print("=== Phase A candidates ===")
    phase_a_configs = [
        (EQUITY_DIR_B78, "deployed_r75pct", "B78 r=0.75% r_mult=2.5"),
        (EQUITY_DIR_B42, "deployed_r1p0",   "B42 r=1.0% r_mult=3.5 (baseline)"),
    ]
    stats: dict[str, dict] = {}
    for equity_dir, prefix, label in phase_a_configs:
        s = phase_stats(equity_dir, prefix, label)
        if not s:
            print(f"  {label}: MISSING — skipped")
            continue
        stats[label] = s
        print(
            f"  {label}: combine {s['c_passes']}/{s['c_attempts']} "
            f"(avg {s['c_avg_days']:.1f}d/attempt, {s['c_days_per_funded']:.1f}d/funded, "
            f"${s['c_reset_fee']:.0f}/funded)"
        )

    print()
    print("=" * 100)
    print("B78 TWO-PHASE PIPELINE (Phase A risk=0.75% r=2.5 -> ORB-reentry r0.75 Phase B)")
    print("Sustainability = Phase_A.combine_passes / Phase_B.xfa_busts")
    print("=" * 100)
    hdr = (f"{'Phase A config':<40} {'Reset$':>7} {'XFA$':>7} "
           f"{'Net/cyc':>9} {'Cycle d':>9} {'Net/mo':>8} {'Sust':>8}")
    print(hdr)
    print("-" * 100)

    rows = []
    for equity_dir, prefix, a_label in phase_a_configs:
        if a_label not in stats:
            continue
        a_s = stats[a_label]
        econ = pipeline_economics(a_s, s_b)
        rows.append((econ["net_per_month"], a_label, econ))

    rows.sort(key=lambda r: -r[0])
    for _, a_label, econ in rows:
        beats_b57 = (econ["net_per_month"] > B57_BENCHMARK["net_per_month"]
                     and econ["sustainability"] > B57_BENCHMARK["sustainability"])
        beats_b42 = (econ["net_per_month"] >= B42_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B42_BENCHMARK["sustainability"])
        flag = ""
        if beats_b57:
            flag = " *** BEATS B57"
        elif beats_b42:
            flag = " * BEATS B42"
        elif "(baseline)" in a_label:
            flag = " [baseline]"
        else:
            flag = " BELOW B42"
        print(f"{a_label:<40} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  "
              f"${econ['net_per_cycle']:>7.0f}  {econ['cycle_days']:>7.1f}d  "
              f"${econ['net_per_month']:>6.0f}  {econ['sustainability']:>6.2f}x{flag}")

    print()
    print("=== VERDICT ===")
    b78_label = "B78 r=0.75% r_mult=2.5"
    if b78_label in stats:
        a_s = stats[b78_label]
        econ = pipeline_economics(a_s, s_b)
        nm = econ["net_per_month"]
        su = econ["sustainability"]
        beats_b57 = nm > B57_BENCHMARK["net_per_month"] and su > B57_BENCHMARK["sustainability"]
        beats_b42 = nm >= B42_BENCHMARK["net_per_month"] and su >= B42_BENCHMARK["sustainability"]
        both_below_b42 = (nm < B42_BENCHMARK["net_per_month"]
                          and su < B42_BENCHMARK["sustainability"])
        if beats_b57:
            v = f"CANDIDATE — beats B57 ($566/mo, 3.54x) on BOTH metrics"
        elif beats_b42:
            v = f"CANDIDATE — beats B42 ($549/mo, 3.23x) but not B57"
        elif both_below_b42:
            v = f"REJECTED — stop rule triggered (BOTH metrics below B42)"
        else:
            v = f"BELOW B57 — mixed result vs B42; does not beat frontier"
        print(f"  B78: ${nm:.0f}/mo, sust {su:.2f}x — {v}")
        print()
        print("Benchmarks:")
        print(f"  B42 deployed r=1.0% r=3.5:  ${B42_BENCHMARK['net_per_month']:.0f}/mo, {B42_BENCHMARK['sustainability']:.2f}x")
        print(f"  B57 both-sides r=2.5 r=1.0%: ${B57_BENCHMARK['net_per_month']:.0f}/mo, {B57_BENCHMARK['sustainability']:.2f}x")


if __name__ == "__main__":
    main()
