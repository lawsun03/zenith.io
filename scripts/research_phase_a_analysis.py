"""Analyze per-year Phase A combine pass rates for equity_b1/control_r1p25 (ifvg_edge baseline)."""
from __future__ import annotations
import csv
from pathlib import Path
from decimal import Decimal
from datetime import datetime
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.backtest.funded_sim import daily_pnls_from_equity, simulate_combines

BASELINE = Decimal("50000")
YEARS = ["2021", "2023", "2024", "2025", "2026"]
EQUITY_DIR = Path(__file__).parent.parent / "research" / "equity_b1"


def load_csv(p: Path) -> list:
    rows = []
    with open(p, newline="") as f:
        for row in csv.DictReader(f):
            rows.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
    return rows


def main() -> None:
    print("Phase A ifvg_edge (control_r1p25) per-year pass rates:")
    print(f"{'Year':<6} {'Passes':<8} {'Attempts':<10} {'Pass%':<8} {'TradDays':<10}")
    print("-" * 45)
    total_passes = total_attempts = 0
    for yr in YEARS:
        p = EQUITY_DIR / f"control_r1p25_{yr}.csv"
        if not p.exists():
            print(f"{yr:<6} MISSING")
            continue
        rows = load_csv(p)
        daily = daily_pnls_from_equity(rows)
        c = simulate_combines(daily, haircut=Decimal("200"))
        pct = 100 * c["passes"] / c["attempts"] if c["attempts"] else 0.0
        total_passes += c["passes"]
        total_attempts += c["attempts"]
        print(f"{yr:<6} {c['passes']:<8} {c['attempts']:<10} {pct:<8.1f} {len(daily):<10}")
    total_pct = 100 * total_passes / total_attempts if total_attempts else 0.0
    print("-" * 45)
    print(f"{'TOTAL':<6} {total_passes:<8} {total_attempts:<10} {total_pct:<8.1f}")
    print()
    # Project close-mode: B24 showed 11/61 mo vs 7/61 mo -> 1.571x scale
    close_scale = 11.0 / 7.0
    proj_passes = int(total_passes * close_scale)
    proj_pct = total_pct * close_scale
    print(f"Projected close-mode Phase A (x{close_scale:.3f} scale from B24):")
    print(f"  ~{proj_passes} passes (vs {total_passes} ifvg_edge) over 5y")
    print(f"  ~{proj_pct:.1f}% pass rate (vs {total_pct:.1f}% ifvg_edge)")
    print()
    # B21 funded Phase B reference numbers
    b21_xfa_net = 3131.0
    b21_avg_funded_days = 73.5
    haircut = 200.0
    b21_busts = 13

    # Compute B27 projected economics
    avg_days_per_attempt = 6.0  # from B21 journal
    proj_attempts_per_funded = total_attempts / proj_passes if proj_passes else float("inf")
    proj_reset_cost = proj_attempts_per_funded * 150.0
    proj_days_funded = proj_attempts_per_funded * avg_days_per_attempt
    cycle_days = proj_days_funded + b21_avg_funded_days
    net_per_cycle = b21_xfa_net - proj_reset_cost
    net_per_month = net_per_cycle * 21.0 / cycle_days if cycle_days else 0.0
    proj_sust = proj_passes / b21_busts if b21_busts else float("inf")

    print("Projected B27 two-phase economics (close Phase A + ORB-reentry r0.75 Phase B):")
    print(f"  Attempts/funded:  {proj_attempts_per_funded:.2f} (vs 4.76 ifvg_edge)")
    print(f"  Reset cost:       ${proj_reset_cost:.0f} (vs $715 ifvg_edge)")
    print(f"  Days/funded:      {proj_days_funded:.1f}d (vs 28.6 ifvg_edge)")
    print(f"  Cycle days:       {cycle_days:.1f}d (vs 102.1 ifvg_edge)")
    print(f"  Net/cycle:        ${net_per_cycle:.0f} (vs $2,416)")
    print(f"  Net/month:        ${net_per_month:.0f} (vs $497)")
    print(f"  Sustainability:   {proj_sust:.2f}x (vs 2.62x)")
    print()
    print("NOTE: projection assumes same avg days/attempt as ifvg_edge. Actual may differ.")
    print("B27 benchmark will give exact numbers.")


if __name__ == "__main__":
    main()
