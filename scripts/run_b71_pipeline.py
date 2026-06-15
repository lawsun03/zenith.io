"""
B71 driver: LO+r=2.5 full two-phase pipeline benchmark.

Tests the combined effect of allowed_sides=long (removes loss-making iFVG shorts,
Lesson 128 / B15) and r_multiple=2.5 (B57 candidate: shorter target improves
combine throughput) on the full funded pipeline.

Phase A: equity_b71/lo_r25_{year}.csv
  Config: engine=combined, ifvg_entry_mode=close, killzones=all,
  swing_stop_lookback=30, allowed_sides=long, r_multiple=2.5,
  stop_buffer=3.0, min_absolute_body=5.0, risk=1.0%, partial_r=1.5
Phase B: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21)

Baselines:
  B42: $549/mo, sust=3.23x (deployed config, both-sides, r=3.5)
  B57: $566/mo, sust=3.54x (both-sides, r=2.5 — current best candidate)

Success criteria:
  - BEATS B57: $/mo AND sust both exceed B57 -> clear win, recommend as Phase A
  - BEATS B42 only: partial candidate, B57 still recommended
  - Below B42 on both: reject

2022 holdout: required ONLY if result beats B57 on both metrics.

Usage:
    python scripts/run_b71_pipeline.py
"""
from __future__ import annotations

import csv
import subprocess
import sys
from decimal import Decimal
from datetime import datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import (
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)

EQUITY_DIR_B71 = _REPO_ROOT / "research" / "equity_b71"
EQUITY_DIR_B42 = _REPO_ROOT / "research" / "equity_b42"
EQUITY_DIR_B21 = _REPO_ROOT / "research" / "equity_b21"
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


def _generate_equity_csv(year: str, out_path: Path) -> None:
    bars = BARS_YEARLY / f"bars_MNQ_dbv_{year}.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "1.0",
        "--partial-r", "1.5",
        "--set", "allowed_sides=long",
        "--set", "r_multiple=2.5",
        "--set", "swing_stop_lookback=30",
        "--out", str(out_path),
    ]
    print(f"  Generating {out_path.name}...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _generate_holdout_2022(out_path: Path) -> None:
    bars = BARS_YEARLY / "bars_MNQ_dbv_2022.csv"
    cmd = [
        PYTHON, str(EQUITY_EXPORT),
        "--bars", str(bars),
        "--instrument", "MNQ",
        "--timeframe", "5min",
        "--risk-pct", "1.0",
        "--partial-r", "1.5",
        "--set", "allowed_sides=long",
        "--set", "r_multiple=2.5",
        "--set", "swing_stop_lookback=30",
        "--out", str(out_path),
    ]
    print(f"  Generating {out_path.name} (2022 holdout)...", end=" ", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_REPO_ROOT))
    if r.returncode != 0:
        print(f"ERROR\n{r.stderr[-2000:]}", file=sys.stderr)
        sys.exit(1)
    print("done")


def _ensure_equity_csvs(run_holdout: bool = False) -> None:
    print("=== Generating Phase A B71 equity CSVs (if missing) ===")
    for year in YEARS:
        out_path = EQUITY_DIR_B71 / f"lo_r25_{year}.csv"
        if out_path.exists():
            print(f"  Skipping {out_path.name} (exists)")
        else:
            _generate_equity_csv(year, out_path)
    if run_holdout:
        holdout = EQUITY_DIR_B71 / "lo_r25_2022.csv"
        if holdout.exists():
            print(f"  Skipping {holdout.name} (exists)")
        else:
            _generate_holdout_2022(holdout)
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


def _pf_from_csv(path: Path) -> float | None:
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
    EQUITY_DIR_B71.mkdir(parents=True, exist_ok=True)

    # First pass: generate 5y equity CSVs (no 2022 holdout yet)
    _ensure_equity_csvs(run_holdout=False)

    print("=== Phase A: B71 (allowed_sides=long, r_multiple=2.5) ===")
    a_b71 = phase_stats(EQUITY_DIR_B71, "lo_r25", "B71 Phase A (LO, r=2.5)")
    if a_b71:
        print(
            f"  {a_b71['label']}: combine {a_b71['c_passes']}/{a_b71['c_attempts']} "
            f"(avg {a_b71['c_avg_days']:.1f}d/attempt, {a_b71['c_days_per_funded']:.1f}d/funded, "
            f"${a_b71['c_reset_fee']:.0f}/funded)"
        )

    print("\n=== Phase A: B42 deployed baseline (both-sides, r=3.5) ===")
    a_b42 = phase_stats(EQUITY_DIR_B42, "deployed_r1p0", "B42 Phase A (both-sides, r=3.5)")
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
        print("  MISSING Phase B equity — cannot compute pipeline")
        return

    b71_econ = pipeline_economics(a_b71, s_b) if a_b71 else None
    b42_econ = pipeline_economics(a_b42, s_b) if a_b42 else None

    print()
    print("=" * 115)
    print("PIPELINE COMPARISON: B71 LO+r=2.5 vs B57 (both-sides r=2.5, $566/mo, 3.54x) vs B42 ($549/mo, 3.23x)")
    print("Phase B fixed: ORB-reentry r=0.75")
    print("=" * 115)
    hdr = (f"{'Phase A Config':<48} {'Reset$':>7} {'XFA$':>7} "
           f"{'Net/cyc':>9} {'Cycle d':>8} {'Net/mo':>8} {'Sust':>8} {'vs B42'}")
    print(hdr)
    print("-" * 115)

    for a_stats, a_desc, econ in [
        (a_b71, "B71: LO + r=2.5", b71_econ),
        (a_b42, "B42: deployed (both-sides, r=3.5)", b42_econ),
    ]:
        if not a_stats or not econ:
            continue
        beats_b57 = (econ["net_per_month"] >= B57_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B57_BENCHMARK["sustainability"])
        beats_b42 = (econ["net_per_month"] >= B42_BENCHMARK["net_per_month"]
                     and econ["sustainability"] >= B42_BENCHMARK["sustainability"])
        if beats_b57:
            verdict = "BEATS B57 ***"
        elif beats_b42:
            verdict = "BEATS B42 *"
        else:
            verdict = "below B42"
        print(f"{a_desc:<48} ${econ['reset_cost']:>5.0f}  ${econ['xfa_net']:>5.0f}  "
              f"${econ['net_per_cycle']:>7.0f}  {econ['cycle_days']:>6.1f}d  "
              f"${econ['net_per_month']:>6.0f}  {econ['sustainability']:>6.2f}x  {verdict}")

    print()
    print("=" * 115)
    print("VERDICT")
    print("=" * 115)

    if b71_econ and b42_econ:
        delta_mo_vs_b42 = b71_econ["net_per_month"] - b42_econ["net_per_month"]
        delta_sust_vs_b42 = b71_econ["sustainability"] - b42_econ["sustainability"]
        delta_mo_vs_b57 = b71_econ["net_per_month"] - B57_BENCHMARK["net_per_month"]
        delta_sust_vs_b57 = b71_econ["sustainability"] - B57_BENCHMARK["sustainability"]

        print(f"  B71 vs B42: $/mo {b71_econ['net_per_month']:.0f} vs {b42_econ['net_per_month']:.0f} "
              f"({'+' if delta_mo_vs_b42 >= 0 else ''}{delta_mo_vs_b42:.0f}), "
              f"sust {b71_econ['sustainability']:.2f}x vs {b42_econ['sustainability']:.2f}x "
              f"({'+' if delta_sust_vs_b42 >= 0 else ''}{delta_sust_vs_b42:.2f})")
        print(f"  B71 vs B57: $/mo {b71_econ['net_per_month']:.0f} vs {B57_BENCHMARK['net_per_month']:.0f} "
              f"({'+' if delta_mo_vs_b57 >= 0 else ''}{delta_mo_vs_b57:.0f}), "
              f"sust {b71_econ['sustainability']:.2f}x vs {B57_BENCHMARK['sustainability']:.2f}x "
              f"({'+' if delta_sust_vs_b57 >= 0 else ''}{delta_sust_vs_b57:.2f})")

        beats_b57 = (b71_econ["net_per_month"] >= B57_BENCHMARK["net_per_month"]
                     and b71_econ["sustainability"] >= B57_BENCHMARK["sustainability"])
        beats_b42 = (b71_econ["net_per_month"] >= B42_BENCHMARK["net_per_month"]
                     and b71_econ["sustainability"] >= B42_BENCHMARK["sustainability"])
        below_b42 = (b71_econ["net_per_month"] < B42_BENCHMARK["net_per_month"]
                     and b71_econ["sustainability"] < B42_BENCHMARK["sustainability"])

        if beats_b57:
            print()
            print("  => CANDIDATE: B71 BEATS B57 on both metrics.")
            print("     Monday actions: set allowed_sides=long AND remove MNQ r_multiple override (→ base 2.5).")
            print("     2022 holdout required before finalizing — running now...")
            # Run 2022 holdout
            _ensure_equity_csvs(run_holdout=True)
            holdout = EQUITY_DIR_B71 / "lo_r25_2022.csv"
            pf_2022 = _pf_from_csv(holdout)
            if pf_2022 is not None:
                print(f"     2022 holdout: B71 Phase A PF = {pf_2022:.3f} "
                      f"({'POSITIVE' if pf_2022 >= 1.0 else 'LOSS-MAKING'} in frozen holdout year)")
        elif beats_b42:
            print()
            print("  => PARTIAL CANDIDATE: B71 beats B42 but NOT B57.")
            print("     B57 (both-sides r=2.5) remains the better recommendation.")
            print("     Both B71 and B57 recommend removing MNQ r_multiple override (r=2.5).")
            print("     B71 adds: set allowed_sides=long (further improvement).")
        elif below_b42:
            print()
            print("  => REJECTED: B71 worse than B42 on BOTH metrics.")
            print("     The LO+r=2.5 combination does not improve funded pipeline over deployed config.")
        else:
            print()
            print("  => MIXED: B71 improves one metric but not both vs B42. B57 remains preferred.")

    print()
    print(f"B42 baseline: $549/mo, sust=3.23x")
    print(f"B57 baseline: $566/mo, sust=3.54x (both-sides r=2.5 — current best candidate)")


if __name__ == "__main__":
    main()
