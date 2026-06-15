"""
B56 driver: ORB×iFVG same-day directional alignment gate — pipeline benchmark.

Gate: ORB signal suppressed when no prior same-direction iFVG has fired today.
  Group A (any same-dir iFVG): PF=1.707  → allowed
  Group B (no prior iFVG):      PF=0.990  → suppressed
  Group C (all opposite iFVG):  PF=0.939  → suppressed
  Group D (mixed iFVG):         PF=1.224  → allowed
  Phase-1 GO: A+D/B+C ratio 1.48x > 1.4x, 5/5 years consistent.

Phase A equity: equity_b42/deployed_r1p0_{year}.csv (same as B42 — deployed combine config)
Phase B equity: equity_b56/orb_reentry_aligned_r0p75_{year}.csv (ORB-reentry + B56 gate)

Success criteria (vs B42 winner: $549/mo, sust 3.23x):
  - ACCEPT: net_per_month >= $549 AND sust >= 3.23 (beats B42 on BOTH)
  - SECONDARY: net_per_month >= $508 AND sust >= 2.85 (beats B31)
  - REJECT: if either metric < B42 baseline

Usage:
    python scripts/run_b56_pipeline.py
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

EQUITY_DIR_B42 = _REPO_ROOT / "research" / "equity_b42"
EQUITY_DIR_B56 = _REPO_ROOT / "research" / "equity_b56"
YEARS = ["2021", "2023", "2024", "2025", "2026"]  # 2022 = frozen holdout
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")

B42_BENCHMARK = {"net_per_month": 549.0, "sustainability": 3.23}
B31_BENCHMARK = {"net_per_month": 508.0, "sustainability": 2.85}

BARS_YEARLY = _REPO_ROOT / "bars" / "yearly"
EQUITY_EXPORT = _REPO_ROOT / "scripts" / "equity_export.py"
PYTHON = sys.executable


def _generate_b56_equity_csv(year: str, out_path: Path) -> None:
    """Generate B56 Phase B equity CSV: ORB-reentry with alignment gate."""
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--set", "engine=combined",
        "--set", "orb_reentry_after_stop=True",
        "--set", "orb_r_multiple=2.5",
        "--set", "orb_ifvg_alignment_required=True",
        "--risk-pct", "0.75",
        "--partial-r", "1.5",
        "--out", str(out_path),
    ]
    print(f"  Generating {out_path.name}...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_b56_equity_csvs() -> None:
    """Generate missing B56 Phase B equity CSVs."""
    EQUITY_DIR_B56.mkdir(exist_ok=True)
    print("=== Generating B56 Phase B equity CSVs (if missing) ===")
    for year in YEARS:
        out_path = EQUITY_DIR_B56 / f"orb_reentry_aligned_r0p75_{year}.csv"
        if out_path.exists():
            print(f"  Skipping {out_path.name} (exists)")
        else:
            _generate_b56_equity_csv(year, out_path)
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
    _ensure_b56_equity_csvs()

    print("=== Phase A: Loading B42 deployed config (combine phase) ===")
    a_label = "B42 deployed r=1.0%"
    s_a = phase_stats(EQUITY_DIR_B42, "deployed_r1p0", a_label)
    if not s_a:
        print("ERROR: missing B42 Phase A equity CSVs — run scripts/run_b42_pipeline.py first")
        sys.exit(1)
    print(
        f"  {a_label}: combine {s_a['c_passes']}/{s_a['c_attempts']} "
        f"(avg {s_a['c_avg_days']:.1f}d/attempt, {s_a['c_days_per_funded']:.1f}d/funded, "
        f"${s_a['c_reset_fee']:.0f}/funded)"
    )

    print("\n=== Phase B: Loading B56 aligned ORB-reentry stats ===")
    b_label = "B56 ORB-reentry aligned r=0.75"
    s_b56 = phase_stats(EQUITY_DIR_B56, "orb_reentry_aligned_r0p75", b_label)
    if not s_b56:
        print("ERROR: missing B56 Phase B equity CSVs")
        sys.exit(1)
    print(
        f"  {b_label}: xfa busts={s_b56['x_busts']}/{s_b56['x_accounts']} "
        f"net ${s_b56['x_net_payouts']:.0f} "
        f"(${s_b56['x_net_per_account']:.0f}/acct, {s_b56['x_avg_days']:.1f}d/acct) "
        f"sust={s_b56['sustainability']:.2f}x"
    )

    print("\n=== Reference: B42 Phase B (unfiltered ORB-reentry) ===")
    b21_label = "B21 ORB-reentry unfiltered r=0.75 (ref)"
    EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
    s_b21 = phase_stats(EQUITY_DIR_B21, "orb_reentry_r0p75", b21_label)
    if s_b21:
        print(
            f"  {b21_label}: xfa busts={s_b21['x_busts']}/{s_b21['x_accounts']} "
            f"net ${s_b21['x_net_payouts']:.0f} "
            f"(${s_b21['x_net_per_account']:.0f}/acct, {s_b21['x_avg_days']:.1f}d/acct) "
            f"sust={s_b21['sustainability']:.2f}x"
        )

    econ_b56 = pipeline_economics(s_a, s_b56)
    econ_ref = pipeline_economics(s_a, s_b21) if s_b21 else None

    print()
    print("=" * 110)
    print("B56 PIPELINE RESULTS")
    print("=" * 110)
    hdr = (f"{'Config':<55} {'Reset$':>7} {'XFA$':>7} "
           f"{'Net/cyc':>9} {'Cycle d':>9} {'Net/mo':>8} {'Sust':>8}")
    print(hdr)
    print("-" * 110)
    for label, econ in [(f"B42-A -> {b_label}", econ_b56),
                         (f"B42-A -> {b21_label}", econ_ref)]:
        if econ is None:
            continue
        beats_b42 = (econ["net_per_month"] >= B42_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B42_BENCHMARK["sustainability"])
        beats_b31 = (econ["net_per_month"] >= B31_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B31_BENCHMARK["sustainability"])
        flag = ""
        if beats_b42:
            flag = " *** BEATS B42"
        elif beats_b31:
            flag = " * BEATS B31"
        elif "(ref)" in label:
            flag = " [ref]"
        print(f"{label:<55} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  "
              f"${econ['net_per_cycle']:>7.0f}  {econ['cycle_days']:>7.1f}d  "
              f"${econ['net_per_month']:>6.0f}  {econ['sustainability']:>6.2f}x{flag}")

    print()
    print("=" * 110)
    print("VERDICT")
    print("=" * 110)
    beats_b42 = (econ_b56["net_per_month"] >= B42_BENCHMARK["net_per_month"]
                 and econ_b56["sustainability"] >= B42_BENCHMARK["sustainability"])
    beats_b31 = (econ_b56["net_per_month"] >= B31_BENCHMARK["net_per_month"]
                 and econ_b56["sustainability"] >= B31_BENCHMARK["sustainability"])
    if beats_b42:
        verdict = "ACCEPT — beats B42 ($549/mo, 3.23x) on BOTH metrics"
    elif beats_b31:
        verdict = "CANDIDATE — beats B31 ($508/mo, 2.85x) but not B42"
    else:
        verdict = "REJECT — below B31 threshold ($508/mo, 2.85x)"
    print(f"  B56 two-phase: ${econ_b56['net_per_month']:.0f}/mo, "
          f"sust {econ_b56['sustainability']:.2f}x — {verdict}")
    if econ_ref:
        print(f"  B42 reference: ${econ_ref['net_per_month']:.0f}/mo, "
              f"sust {econ_ref['sustainability']:.2f}x")
        delta_mo = econ_b56["net_per_month"] - econ_ref["net_per_month"]
        delta_sust = econ_b56["sustainability"] - econ_ref["sustainability"]
        print(f"  Delta vs ref:  ${delta_mo:+.0f}/mo, {delta_sust:+.2f}x sust")

    print()
    print("Definitions:")
    print("  Phase A = B42 deployed combine config (iFVG+ORB combined engine, r=1.0%)")
    print("  Phase B = ORB-reentry r=0.75 with/without B56 alignment gate")
    print("  B42 baseline = $549/mo, sust 3.23x (current top benchmark)")
    print("  Sustainability = A.combine_passes / B.xfa_busts over same 5y window")


if __name__ == "__main__":
    main()
