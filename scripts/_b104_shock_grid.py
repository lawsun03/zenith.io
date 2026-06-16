"""B104 firm-rule shock grid on B21 ORB-reentry r0.75 per-year equity CSVs.

Baseline rules: XfaRules/CombineRules defaults (payout_cap=2000, mll_distance=2000,
trader_profit_share=0.90). Phase A reference: 34 iFVG combine passes over 5y (B21).
Sweeps each parameter axis independently at h=200 (primary) + h0/h400 (sensitivity).
All per-year CSVs used sequentially (2022 excluded = holdout).
"""
from __future__ import annotations

import csv
import sys
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import daily_pnls_from_equity, simulate_combines, simulate_xfa_chain
from app.risk.account_phase import CombineRules, XfaRules

YEARS = [2021, 2023, 2024, 2025, 2026]  # 2022 excluded (holdout)
BASE_DIR = _REPO_ROOT / "research" / "equity_b21"
PHASE_A_PASSES = 34  # B21 iFVG r1.25 combine Phase A reference (fixed)
MONTHS = 60          # 5 years * 12 months


def load_year(year: int, r: str = "r0p75") -> list[tuple[datetime, Decimal]]:
    path = BASE_DIR / f"orb_reentry_{r}_{year}.csv"
    with open(path) as f:
        return [
            (datetime.fromisoformat(row["ts"]), Decimal(row["equity"]))
            for row in csv.DictReader(f)
        ]


def run_grid(
    haircut: Decimal = Decimal("200"),
    xfa_rules: XfaRules | None = None,
    combine_rules: CombineRules | None = None,
) -> dict:
    xfa_rules = xfa_rules or XfaRules()
    combine_rules = combine_rules or CombineRules()
    total = {"c_passes": 0, "c_busts": 0, "x_busts": 0, "x_net": Decimal("0")}
    for y in YEARS:
        curve = load_year(y)
        daily = daily_pnls_from_equity(curve)
        c = simulate_combines(daily, rules=combine_rules, haircut=haircut)
        x = simulate_xfa_chain(daily, rules=xfa_rules, haircut=haircut)
        total["c_passes"] += c["passes"]
        total["c_busts"] += c["busts"]
        total["x_busts"] += x["busts"]
        total["x_net"] += x["net_payouts"]
    sust = PHASE_A_PASSES / max(total["x_busts"], 1)
    per_mo = float(total["x_net"]) / MONTHS
    return {**total, "sust": round(sust, 2), "per_mo": round(per_mo, 0)}


def row(label: str, r: dict) -> None:
    print(
        f"| {label:<35} | {r['c_passes']:>8} | {r['x_busts']:>7} "
        f"| ${r['x_net']:>10.0f} | ${r['per_mo']:>7.0f} | {r['sust']:>5.2f}x |"
    )


def main() -> None:
    H = Decimal("200")
    BASE = run_grid(haircut=H)

    print("=" * 80)
    print("B104 FIRM-RULE SHOCK GRID  (ORB-reentry r0.75, h200, 2021/23-26 excl holdout)")
    print(f"Phase A reference: {PHASE_A_PASSES} iFVG combine passes (B21, fixed)")
    print("=" * 80)
    hdr = f"| {'Config':<35} | {'C pass':>8} | {'XB busts':>7} | {'XFA net':>11} | {'$/mo':>8} | {'sust':>6} |"
    sep = "-" * len(hdr)
    print(sep)
    print(hdr)
    print(sep)

    # --- BASELINE ---
    row("BASELINE (current rules)", BASE)
    print(sep)

    # --- PAYOUT CAP sweep ---
    print("| PAYOUT CAP SWEEP (all else default)                                        |")
    for cap in [1000, 1500, 2000, 3000, 5000]:
        label = f"  payout_cap=${cap} {'(current)' if cap==2000 else '(old rule)' if cap==5000 else ''}"
        r = run_grid(H, xfa_rules=replace(XfaRules(), payout_cap=Decimal(str(cap))))
        row(label, r)
    print(sep)

    # --- XFA MLL DISTANCE sweep ---
    print("| XFA MLL DISTANCE SWEEP (all else default)                                  |")
    for d in [1500, 2000, 2500, 3000]:
        label = f"  xfa_mll_distance=${d} {'(current)' if d==2000 else ''}"
        r = run_grid(H, xfa_rules=replace(XfaRules(), mll_distance=Decimal(str(d))))
        row(label, r)
    print(sep)

    # --- PROFIT SHARE sweep ---
    print("| PROFIT SHARE SWEEP (all else default)                                      |")
    for ps in ["0.80", "0.85", "0.90", "0.95"]:
        label = f"  profit_share={ps} {'(current)' if ps=='0.90' else ''}"
        r = run_grid(H, xfa_rules=replace(XfaRules(), trader_profit_share=Decimal(ps)))
        row(label, r)
    print(sep)

    # --- COMBINE MLL DISTANCE sweep ---
    print("| COMBINE MLL DISTANCE SWEEP (all else default)                              |")
    for d in [1500, 2000, 2500]:
        label = f"  combine_mll=${d} {'(current)' if d==2000 else ''}"
        r = run_grid(H, combine_rules=replace(CombineRules(), mll_distance=Decimal(str(d))))
        row(label, r)
    print(sep)

    # --- COMPOUND SHOCK: pre-2026-04-28 payout cap ($5k) + realized numbers ---
    print("| COMPOUND / HISTORICAL                                                       |")
    r_old_cap = run_grid(H, xfa_rules=replace(XfaRules(), payout_cap=Decimal("5000")))
    row("  Pre-cap-cut (payout_cap=5000)", r_old_cap)
    cap_cost_mo = r_old_cap["per_mo"] - BASE["per_mo"]
    print(f"|   -> Cap cut cost: ${cap_cost_mo:+.0f}/mo vs current rules{' ' * 44}|")
    print(sep)

    # --- SENSITIVITY: haircut h0/h400 at baseline ---
    print("| HAIRCUT SENSITIVITY (current rules, varying haircut)                        |")
    for h in [0, 200, 400]:
        label = f"  h{h} {'(primary)' if h==200 else ''}"
        r = run_grid(Decimal(str(h)))
        row(label, r)
    print(sep)

    print()
    print("Notes:")
    print("  XFA busts: simulation busts on B21 ORB-reentry r0.75 funded equity.")
    print("  Sust = Phase A passes (34, fixed) / XFA busts.")
    print("  $/mo = total 5y XFA net payouts / 60 months.")
    print("  C passes: combine passes from the SAME ORB-reentry equity (standalone,")
    print("            NOT the two-phase iFVG Phase A pipeline; reported for reference.")


if __name__ == "__main__":
    main()
