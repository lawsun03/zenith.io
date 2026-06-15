"""B103 benchmark: DLL effect across {h0,h200,h400}x{gap0,12,24} grid.

Runs funded_sim on ORB-reentry r0.75 per-year equity CSVs (excl 2022),
comparing WITH vs WITHOUT DLL=$500 intraday cap.

Replicates the per-year stitching approach from B21: each year runs
independently, busts/passes aggregated across years.
"""
from __future__ import annotations

import csv
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from app.backtest.funded_sim import (
    cap_daily_pnls_at_dll,
    daily_pnls_from_equity,
    daily_pnls_with_low,
    simulate_combines,
    simulate_xfa_chain,
)

YEARS = [2021, 2023, 2024, 2025, 2026]  # 2022 holdout
EQUITY_DIR = _REPO / "research" / "equity_b21"
DLL_AMOUNT = Decimal("500")  # 1% of $50K funded account

HAIRCUTS = [0, 200, 400]
GAPS = [0, 12, 24]


def load_curve(path: Path) -> list[tuple[datetime, Decimal]]:
    curve = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            curve.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
    return curve


def run_year(year: int, haircut: int, gap: int, use_dll: bool) -> dict:
    path = EQUITY_DIR / f"orb_reentry_r0p75_{year}.csv"
    curve = load_curve(path)
    h = Decimal(str(haircut))

    if use_dll:
        daily_ext = daily_pnls_with_low(curve)
        daily = cap_daily_pnls_at_dll(daily_ext, DLL_AMOUNT)
    else:
        daily = daily_pnls_from_equity(curve)

    c = simulate_combines(daily, haircut=h)
    x = simulate_xfa_chain(daily, haircut=h, combine_gap_days=gap)
    return {
        "combine_passes": c["passes"],
        "combine_busts": c["busts"],
        "xfa_busts": x["busts"],
        "xfa_net": float(x["net_payouts"]),
        "xfa_accounts": x["accounts"],
    }


def aggregate(yearly: list[dict]) -> dict:
    return {
        "combine_passes": sum(y["combine_passes"] for y in yearly),
        "combine_busts": sum(y["combine_busts"] for y in yearly),
        "xfa_busts": sum(y["xfa_busts"] for y in yearly),
        "xfa_net": sum(y["xfa_net"] for y in yearly),
        "xfa_accounts": sum(y["xfa_accounts"] for y in yearly),
    }


def sust(agg: dict) -> float:
    busts = agg["xfa_busts"]
    passes = agg["combine_passes"]
    return passes / busts if busts > 0 else float("inf")


print("B103 DLL Benchmark — ORB-reentry r0.75, per-year (2021/23/24/25/26 excl 2022)")
print(f"DLL=${DLL_AMOUNT} vs no DLL baseline\n")

print(f"{'h':>5} {'gap':>4} | {'no-DLL busts':>13} {'DLL busts':>10} {'delta':>7} "
      f"| {'no-DLL sust':>12} {'DLL sust':>9}")
print("-" * 75)

for h in HAIRCUTS:
    for g in GAPS:
        baseline = aggregate([run_year(yr, h, g, use_dll=False) for yr in YEARS])
        dll_r = aggregate([run_year(yr, h, g, use_dll=True) for yr in YEARS])

        b_busts = baseline["xfa_busts"]
        d_busts = dll_r["xfa_busts"]
        delta = d_busts - b_busts
        b_sust = sust(baseline)
        d_sust = sust(dll_r)

        print(f"{h:>5} {g:>4} | {b_busts:>13d} {d_busts:>10d} {delta:>+7d} "
              f"| {b_sust:>12.2f}x {d_sust:>9.2f}x")

print()
print("Passes are invariant (DLL only affects XFA bust phase, not combine P&L).")
print(f"Baseline combine_passes at h=0,g=0: ", end="")
baseline_00 = aggregate([run_year(yr, 0, 0, use_dll=False) for yr in YEARS])
print(f"{baseline_00['combine_passes']}")

print(f"\nDLL days triggered: ", end="")
# Count DLL-triggered days for reference
total_days = 0
dll_days = 0
for yr in YEARS:
    curve = load_curve(EQUITY_DIR / f"orb_reentry_r0p75_{yr}.csv")
    daily_ext = daily_pnls_with_low(curve)
    for _, _, low in daily_ext:
        total_days += 1
        if low <= -DLL_AMOUNT:
            dll_days += 1
print(f"{dll_days}/{total_days} ({100*dll_days/total_days:.1f}%)")
