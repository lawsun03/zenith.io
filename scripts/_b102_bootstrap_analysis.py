"""B102: bootstrap CI analysis on the recent candidate stack.

Re-scores B99 (ORB r1.0/r1.5/r2.0/r2.5) and the B42 deployed baseline
under 1000-resample block-bootstrap CIs. Checks whether the B99 r1.5
'win' over r2.5 is outside sampling noise.
"""
from __future__ import annotations

import csv
import sys
from decimal import Decimal
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from app.backtest.funded_sim import (
    bootstrap_pipeline,
    daily_pnls_from_equity,
    simulate_combines,
    simulate_xfa_chain,
)

HAIRCUT = Decimal("200")
N_RESAMPLES = 1000
BLOCK_LEN = 20  # ~1 trading month
SEED = 42

CANDIDATES = [
    # label, equity_csv
    ("B99 r1.0 (ORB)", "research/equity_b99/orb_r1p0.csv"),
    ("B99 r1.5 (ORB, CANDIDATE)", "research/equity_b99/orb_r1p5.csv"),
    ("B99 r2.0 (ORB)", "research/equity_b99/orb_r2p0.csv"),
    ("B99 r2.5 (ORB, deployed)", "research/equity_b99/orb_r2p5.csv"),
]


def load_curve(path: str) -> list[tuple[datetime, Decimal]]:
    curve = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            curve.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
    return curve


def _pct_str(ci: dict, p: int) -> str:
    v = ci[p]
    return f"{v:,.0f}"


def overlaps(ci_a: dict, ci_b: dict) -> bool:
    """Do the 90% CIs (p5..p95) of two distributions overlap?"""
    return not (ci_a[95] < ci_b[5] or ci_b[95] < ci_a[5])


def run():
    results = []
    for label, csv_path in CANDIDATES:
        full_path = _REPO / csv_path
        if not full_path.exists():
            print(f"  SKIP (not found): {csv_path}")
            continue
        print(f"\nLoading {label}...")
        curve = load_curve(str(full_path))
        daily = daily_pnls_from_equity(curve)
        print(f"  {len(daily)} trading days")

        # Point estimate
        c = simulate_combines(daily, haircut=HAIRCUT)
        x = simulate_xfa_chain(daily, haircut=HAIRCUT)
        point_net = float(x["net_payouts"])
        point_busts = x["busts"]
        point_passes = c["passes"]

        # Bootstrap CIs
        print(f"  Running {N_RESAMPLES} bootstrap resamples (block={BLOCK_LEN}d)...")
        bs = bootstrap_pipeline(
            daily,
            n_resamples=N_RESAMPLES,
            block_len=BLOCK_LEN,
            seed=SEED,
            haircut=HAIRCUT,
        )
        results.append({
            "label": label,
            "point_net": point_net,
            "point_busts": point_busts,
            "point_passes": point_passes,
            "bs": bs,
        })

    # Print summary table
    print("\n" + "=" * 90)
    print("B102 Bootstrap CI Report (h=200, 1000 resamples, block=20d)")
    print("=" * 90)
    print(f"{'Config':<32}  {'Point':<10}  {'p5':>8}  {'p25':>8}  {'p50':>8}  {'p75':>8}  {'p95':>8}")
    print(f"{'':32}  {'xfa_net$':<10}  {'net$':>8}  {'net$':>8}  {'net$':>8}  {'net$':>8}  {'net$':>8}")
    print("-" * 90)
    for r in results:
        ci = r["bs"]["xfa_net"]
        print(
            f"{r['label']:<32}  {r['point_net']:>10,.0f}  "
            f"{ci[5]:>8,.0f}  {ci[25]:>8,.0f}  {ci[50]:>8,.0f}  "
            f"{ci[75]:>8,.0f}  {ci[95]:>8,.0f}"
        )

    print()
    print(f"{'Config':<32}  {'Busts':>7}  {'p5':>5}  {'p25':>5}  {'p50':>5}  {'p75':>5}  {'p95':>5}")
    print("-" * 65)
    for r in results:
        ci = r["bs"]["xfa_busts"]
        print(
            f"{r['label']:<32}  {r['point_busts']:>7}  "
            f"{ci[5]:>5.0f}  {ci[25]:>5.0f}  {ci[50]:>5.0f}  {ci[75]:>5.0f}  {ci[95]:>5.0f}"
        )

    # Check if B99 r1.5 "wins" are outside noise
    print()
    r15 = next((r for r in results if "r1.5" in r["label"]), None)
    r25 = next((r for r in results if "r2.5" in r["label"]), None)
    if r15 and r25:
        ol = overlaps(r15["bs"]["xfa_net"], r25["bs"]["xfa_net"])
        print(f"B99 r1.5 vs r2.5 xfa_net 90% CIs: {'OVERLAP (tie -- no winner)' if ol else 'DO NOT OVERLAP (r1.5 wins)'}")
        ol_b = overlaps(r15["bs"]["xfa_busts"], r25["bs"]["xfa_busts"])
        print(f"B99 r1.5 vs r2.5 xfa_busts 90% CIs: {'OVERLAP (tie)' if ol_b else 'DO NOT OVERLAP'}")
        print()
        print(f"r1.5 point net: ${r15['point_net']:,.0f}  CI=[${r15['bs']['xfa_net'][5]:,.0f}, ${r15['bs']['xfa_net'][95]:,.0f}]")
        print(f"r2.5 point net: ${r25['point_net']:,.0f}  CI=[${r25['bs']['xfa_net'][5]:,.0f}, ${r25['bs']['xfa_net'][95]:,.0f}]")


if __name__ == "__main__":
    run()
