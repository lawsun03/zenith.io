"""
B86: Funded-sim combine-gap correction — quantify idle-time drag.

Runs the B57 two-phase pipeline (Phase A = iFVG deployed r2.5,
Phase B = ORB-reentry r0.75) with combine_gap_days in {0, 8, 12, 24}.
Reports per-gap $/mo, sust, and the breakeven gap where $/mo falls below $400.

Usage:
    python scripts/run_b86_gap.py
"""
from __future__ import annotations

import csv
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

YEARS = ["2021", "2023", "2024", "2025", "2026"]
BASELINE = Decimal("50000")
COMBINE_RESET_FEE = Decimal("150")
TRADING_DAYS_PER_MONTH = Decimal("21")
HAIRCUT = Decimal("200")


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


def phase_a_stats() -> dict:
    """Phase A = B57 deployed iFVG r2.5 (combine harvesting)."""
    curve = stitch(EQUITY_DIR_B57, "r2p5")
    if not curve:
        raise FileNotFoundError("Missing equity_b57/r2p5_*.csv files")
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    c = simulate_combines(daily, haircut=HAIRCUT)
    avg_days = (Decimal(str(trading_days)) / Decimal(str(c["attempts"]))
                if c["attempts"] else Decimal("0"))
    attempts_per_funded = (Decimal(str(c["attempts"])) / Decimal(str(c["passes"]))
                           if c["passes"] else Decimal("inf"))
    combine_days = attempts_per_funded * avg_days
    reset_fee = attempts_per_funded * COMBINE_RESET_FEE
    return {
        "c_passes": c["passes"], "c_attempts": c["attempts"],
        "c_days_per_funded": float(combine_days),
        "c_reset_fee": float(reset_fee),
    }


def phase_b_stats(combine_gap_days: int) -> dict:
    """Phase B = ORB-reentry r0.75 (equity_b21), with gap correction."""
    curve = stitch(EQUITY_DIR_B21, "orb_reentry_r0p75")
    if not curve:
        raise FileNotFoundError("Missing equity_b21/orb_reentry_r0p75_*.csv files")
    daily = daily_pnls_from_equity(curve)
    trading_days = len(daily)
    x = simulate_xfa_chain(daily, haircut=HAIRCUT, combine_gap_days=combine_gap_days)
    avg_funded_days = (Decimal(str(trading_days)) / Decimal(str(x["accounts"]))
                       if x["accounts"] else Decimal("0"))
    net_per_account = (x["net_payouts"] / Decimal(str(x["accounts"]))
                       if x["accounts"] else Decimal("0"))
    return {
        "x_accounts": x["accounts"], "x_busts": x["busts"],
        "x_net_payouts": float(x["net_payouts"]),
        "x_net_per_account": float(net_per_account),
        "x_avg_days": float(avg_funded_days),
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
        "net_per_month": float(net_per_month),
        "sustainability": float(sustainability),
    }


def main() -> None:
    print("=== B86: Combine-gap correction calibration ===\n")

    print("Loading Phase A (B57 r2.5 iFVG deployed)...")
    a = phase_a_stats()
    print(f"  Phase A: {a['c_passes']}/{a['c_attempts']} passes, "
          f"${a['c_reset_fee']:.0f}/funded, {a['c_days_per_funded']:.1f}d/funded\n")

    print("Loading Phase B (B21 ORB-reentry r0.75) at gap=0 to verify baseline...")
    b0 = phase_b_stats(0)
    eco0 = pipeline_economics(a, b0)
    print(f"  gap=0: {b0['x_busts']}/{b0['x_accounts']} busts, "
          f"${b0['x_net_per_account']:.0f}/acct, {b0['x_avg_days']:.1f}d/acct, "
          f"${eco0['net_per_month']:.0f}/mo, sust={eco0['sustainability']:.2f}x")

    # B57 reference for baseline comparison
    B57_NPM = 566.0
    B57_SUST = 3.54
    baseline_npm = eco0["net_per_month"]
    baseline_sust = eco0["sustainability"]
    if abs(baseline_npm - B57_NPM) > 30:
        print(f"  WARNING: gap=0 baseline ${baseline_npm:.0f}/mo differs from "
              f"B57 ${B57_NPM}/mo by >${abs(baseline_npm - B57_NPM):.0f} "
              f"(Phase A/B CSV mismatch?)", file=sys.stderr)

    print("\n=== Gap sensitivity table ===")
    print(f"{'gap':>5} | {'accounts':>8} | {'busts':>5} | {'$/acct':>7} | "
          f"{'avg_days':>8} | {'$/mo':>7} | {'sust':>5} | {'d$/mo':>7}")
    print("-" * 72)

    results = {}
    for gap in [0, 8, 12, 24]:
        b = phase_b_stats(gap)
        eco = pipeline_economics(a, b)
        results[gap] = {"b": b, "eco": eco}
        d_npm = eco["net_per_month"] - eco0["net_per_month"]
        print(f"{gap:>5} | {b['x_accounts']:>8} | {b['x_busts']:>5} | "
              f"${b['x_net_per_account']:>6.0f} | {b['x_avg_days']:>7.1f}d | "
              f"${eco['net_per_month']:>6.0f} | {eco['sustainability']:>4.2f}x | "
              f"{d_npm:>+6.0f}")

    print()
    print("=== Breakeven analysis ===")
    FLOOR = 400.0
    prev_gap = None
    for gap, r in sorted(results.items()):
        npm = r["eco"]["net_per_month"]
        if npm < FLOOR:
            if prev_gap is not None:
                print(f"  Breakeven gap: between {prev_gap}d and {gap}d "
                      f"($/mo crosses ${FLOOR:.0f}/mo floor)")
            else:
                print(f"  Already below ${FLOOR:.0f}/mo floor at gap=0 (unexpected)")
            break
        prev_gap = gap
    else:
        max_gap = max(results.keys())
        print(f"  Still above ${FLOOR:.0f}/mo at gap={max_gap}d — no breakeven in tested range")

    print()
    print("=== Summary ===")
    npm_gap0 = results[0]["eco"]["net_per_month"]
    npm_gap24 = results[24]["eco"]["net_per_month"]
    drag_pct = (npm_gap0 - npm_gap24) / npm_gap0 * 100
    print(f"  gap=0 (optimistic):  ${npm_gap0:.0f}/mo")
    print(f"  gap=24 (full cycle): ${npm_gap24:.0f}/mo")
    print(f"  Drag from combine-gap: {drag_pct:.1f}%  "
          f"({'MATERIAL (>15%)' if drag_pct > 15 else 'below materiality threshold'})")
    print()
    print("  B86 success criterion: gap=24 reduces $/mo by >15% vs gap=0")
    if drag_pct > 15:
        print("  VERDICT: CRITERION MET — combine-gap model inaccuracy is material.")
        print("  Recommended baseline: gap=12 (half-overlap model) for future benchmarks.")
        print("  Action: quote $X/mo range for Lawrence's planning (NOT $566/mo).")
    else:
        print("  VERDICT: CRITERION NOT MET — gap drag is not material at >15%.")


if __name__ == "__main__":
    main()
