"""
B75 Phase 1: ORB flatten-time sensitivity (15:30 vs 16:00 ET).

For each ORB trade that was still open at 15:30 ET (exited in [15:30, 16:00] ET,
including the force-flatten at 16:00 ET exactly), compute the hypothetical P&L
if we had exited at the 15:30 ET bar close instead.

Two cohort analyses:
A) "Late-EOD pre-flatten" = exit_ts_ET in [15:30, 16:00) - hit stop/target 15:30-16:00
B) "Force-flattened" = exit_ts_ET exactly 16:00 ET - force-flatten only
C) "Full open-at-1530" = A + B = all trades still open at 15:30 ET

GO criterion: Hypothetical 15:30 flatten improves aggregate net P&L by >= 15%
AND improvement is consistent in 3+/5 open years (for the full cohort C).

Output: per-year table + aggregate comparison + GO/NO-GO verdict.
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import timezone
import pytz

REPO = Path(__file__).resolve().parent.parent
MFE_MAE = REPO / "research" / "mfe_mae_orb_clean.csv"
BARS = REPO / "bars" / "bars_MNQ_dbv_2021_2026.csv"

ET = pytz.timezone("America/New_York")


def load_trades():
    df = pd.read_csv(MFE_MAE)
    df["entry_ts"] = pd.to_datetime(df["entry_ts"], utc=True)
    df["exit_ts"] = pd.to_datetime(df["exit_ts"], utc=True)
    df["exit_ts_et"] = df["exit_ts"].dt.tz_convert(ET)
    df["entry_ts_et"] = df["entry_ts"].dt.tz_convert(ET)
    df["exit_hour_et"] = df["exit_ts_et"].dt.hour
    df["exit_minute_et"] = df["exit_ts_et"].dt.minute
    df["exit_date_et"] = df["exit_ts_et"].dt.date
    df["year"] = df["exit_ts_et"].dt.year
    return df


def load_bars():
    print("Loading bars CSV...")
    bars = pd.read_csv(BARS, usecols=["ts", "close"])
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    bars["ts_et"] = bars["ts"].dt.tz_convert(ET)
    bars["date_et"] = bars["ts_et"].dt.date
    bars["hour_et"] = bars["ts_et"].dt.hour
    bars["minute_et"] = bars["ts_et"].dt.minute
    # Bar at ts_ET=15:29 closes at 15:30 ET (1-min bar close = bar open + 1 min)
    bars_1529 = bars[(bars["hour_et"] == 15) & (bars["minute_et"] == 29)].copy()
    bars_1529 = bars_1529.set_index("date_et")["close"]
    return bars_1529


def compute_hyp_pnl(group_df, bars_1529):
    """Compute hypothetical P&L if exiting at 15:30 ET bar close."""
    group = group_df.copy()
    # Per-trade contract value: pnl / price_move_per_contract
    direction = group["side"].map({"long": 1, "short": -1})
    price_move = (group["exit_price"] - group["entry_price"]) * direction
    # skip trades where price_move ~= 0 (ambiguous direction)
    valid = price_move.abs() > 0.001
    group = group[valid].copy()
    direction = direction[valid]
    price_move_valid = price_move[valid]
    group["contract_val"] = group["realized_pnl"].values / price_move_valid.values

    # look up 15:29 ET bar close for the exit date
    group["hyp_price"] = group["exit_date_et"].map(bars_1529)
    group = group.dropna(subset=["hyp_price"])

    direction2 = group["side"].map({"long": 1, "short": -1})
    group["hyp_pnl"] = group["contract_val"] * (group["hyp_price"] - group["entry_price"]) * direction2
    return group


def analyze_cohort(label, cohort, bars_1529):
    print(f"\n{'='*70}")
    print(f"COHORT: {label}  (n={len(cohort)})")
    print(f"{'='*70}")

    cohort_hyp = compute_hyp_pnl(cohort, bars_1529)
    if len(cohort_hyp) == 0:
        print("  No matched trades — skipping.")
        return None, []

    years = sorted(cohort_hyp["year"].unique())
    print(f"{'Year':<8} {'n':<6} {'Actual $':<14} {'Hyp 15:30 $':<14} {'Delta $':<14} {'Delta %':<10} {'GO?'}")
    print("-" * 75)

    year_gos = []
    for yr in years:
        yr_data = cohort_hyp[cohort_hyp["year"] == yr]
        actual = yr_data["realized_pnl"].sum()
        hyp = yr_data["hyp_pnl"].sum()
        delta = hyp - actual
        pct = delta / abs(actual) * 100 if abs(actual) > 0.01 else 0.0
        go = pct >= 15.0
        year_gos.append(go)
        print(f"{yr:<8} {len(yr_data):<6} ${actual:>11.0f}  ${hyp:>11.0f}  ${delta:>11.0f}  {pct:>8.1f}%  {'GO' if go else 'NO-GO'}")

    print("-" * 75)
    total_actual = cohort_hyp["realized_pnl"].sum()
    total_hyp = cohort_hyp["hyp_pnl"].sum()
    total_delta = total_hyp - total_actual
    total_pct = total_delta / abs(total_actual) * 100 if abs(total_actual) > 0.01 else 0.0
    years_go_count = sum(year_gos)
    print(f"{'ALL':<8} {len(cohort_hyp):<6} ${total_actual:>11.0f}  ${total_hyp:>11.0f}  ${total_delta:>11.0f}  {total_pct:>8.1f}%")
    print()
    print(f"  Aggregate >= 15%: {'MET' if total_pct >= 15.0 else 'NOT MET'} ({total_pct:.1f}%)")
    print(f"  3+/5 years:       {'MET' if years_go_count >= 3 else 'NOT MET'} ({years_go_count}/{len(years)} years)")

    return {"total_actual": total_actual, "total_hyp": total_hyp, "total_pct": total_pct,
            "n": len(cohort_hyp), "years_go": years_go_count, "n_years": len(years)}, year_gos


def run():
    trades = load_trades()
    bars_1529 = load_bars()

    print(f"\nTotal ORB trades: {len(trades)}")

    # Debug: exit hour distribution in ET
    print("\nExit hour distribution (ET):")
    print(trades["exit_hour_et"].value_counts().sort_index().to_string())

    # Cohort A: exits in [15:30, 16:00) ET = hit stop/target in last 30 min (not force-flatten)
    mask_a = (trades["exit_hour_et"] == 15) & (trades["exit_minute_et"] >= 30)
    cohort_a = trades[mask_a].copy()

    # Cohort B: exits exactly at 16:00 ET = force-flatten
    mask_b = (trades["exit_hour_et"] == 16) & (trades["exit_minute_et"] == 0)
    cohort_b = trades[mask_b].copy()

    # Cohort C: all trades still open at 15:30 ET (A + B)
    cohort_c = pd.concat([cohort_a, cohort_b], ignore_index=True)

    print(f"\nCohort A (hit stop/target 15:30-15:59 ET):  n={len(cohort_a)}")
    print(f"Cohort B (force-flattened at 16:00 ET):     n={len(cohort_b)}")
    print(f"Cohort C (all open at 15:30 ET, A+B):       n={len(cohort_c)}")
    print(f"Earlier exits (before 15:30 ET):             n={len(trades) - len(cohort_a) - len(cohort_b)}")

    stats_a, years_a = analyze_cohort("A: Stop/target hits 15:30-15:59 ET", cohort_a, bars_1529)
    stats_b, years_b = analyze_cohort("B: Force-flattened at 16:00 ET (main EOD cohort)", cohort_b, bars_1529)
    stats_c, years_c = analyze_cohort("C: All open at 15:30 ET (A + B)", cohort_c, bars_1529)

    print(f"\n{'='*70}")
    print("PHASE 1 VERDICT")
    print(f"{'='*70}")
    if stats_c is None:
        print("PHASE 1 NO-GO: insufficient data")
        return

    agg_go = stats_c["total_pct"] >= 15.0
    year_go = stats_c["years_go"] >= 3
    overall_go = agg_go and year_go

    print(f"\nFull cohort C (n={stats_c['n']} trades open at 15:30 ET):")
    print(f"  Aggregate improvement: {stats_c['total_pct']:.1f}%  ({'MET' if agg_go else 'NOT MET'} >= 15% criterion)")
    print(f"  Years consistent:      {stats_c['years_go']}/{stats_c['n_years']}  ({'MET' if year_go else 'NOT MET'} >= 3/5 criterion)")
    print()
    if overall_go:
        print("==> PHASE 1 GO -- 15:30 ET flatten materially improves late-EOD P&L")
        print("    Proceed to Phase 2: add orb_flatten_hour parameter to ORBDetector")
    else:
        print("==> PHASE 1 NO-GO -- 15:30 ET flatten does not meet both GO criteria")
        print("    Reject B75; current 16:00 ET flatten is not demonstrably worse")


if __name__ == "__main__":
    run()
