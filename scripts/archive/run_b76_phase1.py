"""B76 Phase 1: Skip-second-iFVG-after-loss day filter — data mining.

Protocol:
- Load mfe_mae_ifvg_clean.csv (research baseline, filter to LO longs, excl 2022)
- For each ET date, determine rank-1 iFVG signal (first long by entry_ts)
- Skip rank-2+ iFVG signals if rank-1 iFVG lost (pnl <= 0)
- Compute PF filtered vs unfiltered, volume cut, daily P&L distribution
- Generate synthetic equity curve at r=1.0% scale and run funded_sim
- Compare to B57 ($566/mo, sust=3.54x) and B42 ($549/mo, sust=3.23x)

Stop rule: if modified combine passes produce sust < 3.23x (B42) → reject.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.backtest.funded_sim import daily_pnls_from_equity, simulate_combines

MFE_MAE_CSV = REPO / "research" / "mfe_mae_ifvg_clean.csv"

# B42/B57 two-phase baselines (per-year pipeline, Phase B = ORB-reentry r=0.75)
B42_PASSES = 42   # Phase A passes over 5y (excl 2022)
B57_PASSES = 46   # Phase A passes with r=2.5 over 5y (excl 2022)
PHASE_B_BUSTS = 13  # Phase B XFA busts (fixed, ORB-reentry r=0.75)
B42_SUST = B42_PASSES / PHASE_B_BUSTS  # 3.23x
B57_SUST = B57_PASSES / PHASE_B_BUSTS  # 3.54x
B42_MONTHLY = 549.0
B57_MONTHLY = 566.0


def et_date(ts_str: str):
    """Convert UTC ISO timestamp to US/Eastern date (approximation)."""
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    # EDT (Mar-Nov) = UTC-4; EST (Dec-Feb) = UTC-5
    offset = -4 if 3 <= dt.month <= 11 else -5
    et = dt + timedelta(hours=offset)
    return et.date()


def load_trades() -> list[dict]:
    """Load LO longs from mfe_mae_ifvg_clean.csv, exclude 2022."""
    trades = []
    with open(MFE_MAE_CSV, newline="") as f:
        for row in csv.DictReader(f):
            year = int(row["year"])
            if year == 2022:
                continue
            if row["side"] != "long":
                continue
            trades.append({
                "year": year,
                "entry_ts": row["entry_ts"],
                "exit_ts": row["exit_ts"],
                "pnl": float(row["realized_pnl"]),
                "r_mfe": float(row["r_mfe"]),
                "r_mae": float(row["r_mae"]),
                "et_date": et_date(row["entry_ts"]),
            })
    return trades


def apply_b76_filter(trades: list[dict]) -> tuple[list[dict], list[dict]]:
    """Apply B76 filter: skip rank-2+ iFVG if rank-1 lost.

    Returns (kept, removed) lists.
    """
    by_date: dict[object, list[dict]] = defaultdict(list)
    for t in trades:
        by_date[t["et_date"]].append(t)

    kept, removed = [], []
    for d in sorted(by_date):
        day = sorted(by_date[d], key=lambda t: t["entry_ts"])
        rank1 = day[0]
        kept.append(rank1)
        rank1_won = rank1["pnl"] > 0  # strict positive = win
        for t in day[1:]:
            if rank1_won:
                kept.append(t)
            else:
                removed.append(t)

    return kept, removed


def compute_pf(trades: list[dict]) -> tuple[float, float, float]:
    """Return (gross_wins, gross_losses, PF)."""
    wins = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    losses = sum(-t["pnl"] for t in trades if t["pnl"] < 0)
    pf = wins / losses if losses > 0 else float("inf")
    return wins, losses, pf


def build_equity_curve(trades: list[dict], scale: float = 1.0,
                        start: float = 50_000.0) -> list[tuple[datetime, Decimal]]:
    """Build a synthetic equity curve (ts, equity) from per-trade data.

    Groups trades by day, applies daily net P&L sequentially.
    The curve has one data point per trading day.
    """
    by_date: dict[object, float] = defaultdict(float)
    for t in trades:
        by_date[t["et_date"]] += t["pnl"] * scale

    curve: list[tuple[datetime, Decimal]] = []
    equity = Decimal(str(start))
    # Use date as ts (midnight UTC); funded_sim only needs ordering
    for d in sorted(by_date):
        ts = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
        equity += Decimal(str(round(by_date[d], 2)))
        curve.append((ts, equity))

    return curve


def main() -> None:
    print("=== B76 Phase 1: Skip-second-iFVG-after-loss filter ===\n")

    trades = load_trades()
    print(f"Loaded {len(trades)} LO long trades (excl 2022) from {MFE_MAE_CSV.name}")
    years = sorted(set(t["year"] for t in trades))
    print(f"Years: {years}")

    kept, removed = apply_b76_filter(trades)
    n_all = len(trades)
    n_kept = len(kept)
    n_removed = len(removed)
    pct_removed = 100.0 * n_removed / n_all

    print(f"\n--- Volume impact ---")
    print(f"All trades:  {n_all}")
    print(f"Kept:        {n_kept}  ({100*n_kept/n_all:.1f}%)")
    print(f"Removed:     {n_removed}  ({pct_removed:.1f}%)")

    # Per-year breakdown of removed trades
    by_year_removed = defaultdict(int)
    by_year_all = defaultdict(int)
    for t in trades:
        by_year_all[t["year"]] += 1
    for t in removed:
        by_year_removed[t["year"]] += 1
    print("\nRemoved per year:")
    for y in sorted(years):
        print(f"  {y}: {by_year_removed[y]} / {by_year_all[y]} ({100*by_year_removed[y]/by_year_all[y]:.1f}%)")

    print("\n--- PF analysis ---")
    all_wins, all_losses, all_pf = compute_pf(trades)
    kept_wins, kept_losses, kept_pf = compute_pf(kept)
    rem_wins, rem_losses, rem_pf = compute_pf(removed)

    print(f"All trades:   PF={all_pf:.4f}  wins=${all_wins:.0f}  losses=${all_losses:.0f}  n={n_all}")
    print(f"Kept (B76):   PF={kept_pf:.4f}  wins=${kept_wins:.0f}  losses=${kept_losses:.0f}  n={n_kept}")
    print(f"Removed:      PF={rem_pf:.4f}  wins=${rem_wins:.0f}  losses=${rem_losses:.0f}  n={n_removed}")
    pf_improvement = (kept_pf / all_pf - 1) * 100
    print(f"PF improvement: {pf_improvement:+.1f}%")

    print("\n--- Removed trades breakdown ---")
    rem_wrs = sum(1 for t in removed if t["pnl"] > 0)
    print(f"Removed WR: {100*rem_wrs/n_removed:.1f}%  ({rem_wrs}/{n_removed})")
    rem_days_with_removal = len(set(t["et_date"] for t in removed))
    print(f"Days with removals: {rem_days_with_removal}")

    # Per-year PF of removed
    for y in sorted(years):
        yr_rem = [t for t in removed if t["year"] == y]
        if yr_rem:
            rw, rl, rpf = compute_pf(yr_rem)
            print(f"  {y} removed: n={len(yr_rem)}, PF={rpf:.3f}")

    print("\n--- Funded pipeline estimate (Phase A combine simulation) ---")
    # Scale factor: mfe_mae generated at r=1.25% research baseline;
    # B42/B57 use r=1.0% deployed. Scale to r=1.0% for comparison.
    scale = 1.0 / 1.25  # 0.80

    for label, t_set in [("Unfiltered (baseline)", trades), ("B76 filtered", kept)]:
        curve = build_equity_curve(t_set, scale=scale, start=50_000.0)
        daily = daily_pnls_from_equity(curve)
        c = simulate_combines(daily, haircut=Decimal("200"))
        passes = c["passes"]
        busts = c["busts"]
        sust_estimate = passes / PHASE_B_BUSTS
        # $/mo estimate: scale from B42 by pass ratio
        base_monthly = B42_MONTHLY  # $549/mo at B42 (42 passes)
        monthly_est = base_monthly * (passes / B42_PASSES) if B42_PASSES > 0 else 0
        print(f"\n{label}:")
        print(f"  Combine: attempts={c['attempts']}, passes={passes}, busts={busts}")
        print(f"  Estimated two-phase sust (passes / {PHASE_B_BUSTS} Phase-B busts) = {sust_estimate:.2f}x")
        print(f"  Estimated $/mo (scaled from B42): ${monthly_est:.0f}")

    print(f"\n--- Baselines for comparison ---")
    print(f"B42 deployed (r=1.0, no filter): sust={B42_SUST:.2f}x, $/mo=${B42_MONTHLY:.0f}")
    print(f"B57 (r=2.5, no filter):          sust={B57_SUST:.2f}x, $/mo=${B57_MONTHLY:.0f}")
    print(f"\nStop rule: sust < {B42_SUST:.2f}x (B42) on BOTH unfiltered proxy AND filtered → REJECT")

    print("\n=== Done ===")


if __name__ == "__main__":
    main()
