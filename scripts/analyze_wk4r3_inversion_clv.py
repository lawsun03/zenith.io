"""
wk4-r3 Phase 1: Inversion bar close-strength (CLV) as iFVG quality predictor.

In close-mode iFVG, entry = inversion bar close. The CLV = (close - low) / (high - low)
of the inversion bar measures HOW STRONGLY the bar closed back into the FVG zone.
A high CLV (close near bar high) = strong absorption conviction.
A low CLV (close near bar middle or low) = weaker, may retest.

Distinct from B62 (ORB CLV is collinear with breakout condition). iFVG inversion bars
don't have a fixed boundary that the close must exceed, so CLV genuinely varies.

Also checks: FVG zone implied width (inferred from entry-price + MAE/MFE at specific MAE tiers).
"""

import sys
import pandas as pd
import numpy as np
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IFVG_CSV = ROOT / "research" / "mfe_mae_ifvg_clean.csv"
BARS_CSV = ROOT / "bars" / "bars_MNQ_dbv_2021_2026.csv"

_ET = "America/New_York"


def pf(series: pd.Series) -> float:
    wins = series[series > 0].sum()
    losses = -series[series < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else float("nan")
    return wins / losses


def load_data():
    print("Loading iFVG trades...")
    trades = pd.read_csv(IFVG_CSV)
    trades["entry_ts"] = pd.to_datetime(trades["entry_ts"], utc=True)
    trades = trades[trades["entry_ts"].dt.year != 2022].copy()
    trades["win"] = trades["realized_pnl"] > 0
    print(f"  {len(trades)} iFVG trades (excl 2022)")

    print("Loading 1-min bars...")
    bars = pd.read_csv(BARS_CSV)
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    print(f"  {len(bars)} 1-min bars")

    # Resample 1-min bars to 5-min (right-closed, right-labeled = bar ts is CLOSE time)
    bars = bars.set_index("ts")
    bars5 = bars.resample("5min", closed="right", label="right").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["close"])
    bars5 = bars5.reset_index()
    print(f"  Resampled to {len(bars5)} 5-min bars")

    return trades, bars5


def compute_clv(trades: pd.DataFrame, bars5: pd.DataFrame):
    """
    Match each iFVG trade's entry_ts to the 5-min bar and compute CLV.
    entry_ts in close-mode = close of the inversion bar.
    The 5-min bar labeled at the close minute is the inversion bar.
    """
    print("\n=== INVERSION BAR CLV ANALYSIS ===")

    # Merge on exact timestamp
    trades_with_ts = trades[["entry_ts", "realized_pnl", "win", "side"]].copy()
    trades_with_ts["entry_ts_5m"] = trades_with_ts["entry_ts"].dt.floor("5min") + pd.Timedelta("5min")

    bars5_indexed = bars5.set_index("ts")[["open", "high", "low", "close"]]

    matched_rows = []
    unmatched = 0
    for _, row in trades_with_ts.iterrows():
        ts = row["entry_ts_5m"]
        if ts in bars5_indexed.index:
            bar = bars5_indexed.loc[ts]
            h = bar["high"]
            l = bar["low"]
            c = bar["close"]
            rng = h - l
            if rng > 0:
                clv = (c - l) / rng
            else:
                clv = 0.5  # zero-range bar
            matched_rows.append({
                "realized_pnl": row["realized_pnl"],
                "win": row["win"],
                "side": row["side"],
                "clv": clv,
                "entry_ts": row["entry_ts"],
            })
        else:
            unmatched += 1

    print(f"Matched: {len(matched_rows)} / {len(trades_with_ts)} trades (unmatched: {unmatched})")
    if not matched_rows:
        print("No matches — check timestamp alignment")
        return None

    df = pd.DataFrame(matched_rows)

    print(f"\nCLV distribution (inversion bar close strength):")
    print(f"  Mean CLV: {df['clv'].mean():.3f}  Median: {df['clv'].median():.3f}")
    print(f"  Std:  {df['clv'].std():.3f}")
    print(f"  Min:  {df['clv'].min():.3f}  Max:  {df['clv'].max():.3f}")
    print(f"  % CLV >= 0.5 (close in upper half): {(df['clv'] >= 0.5).mean():.1%}")
    print(f"  % CLV >= 0.7 (strong close): {(df['clv'] >= 0.7).mean():.1%}")

    # Quintile analysis
    df["clv_q"] = pd.qcut(df["clv"], 5, labels=["Q1_weak", "Q2", "Q3", "Q4", "Q5_strong"])
    print(f"\nCLV quintile PF:")
    for q in ["Q1_weak", "Q2", "Q3", "Q4", "Q5_strong"]:
        sub = df[df["clv_q"] == q]["realized_pnl"]
        print(f"  {q}: PF={pf(sub):.3f}  n={len(sub)}  WR={sub.gt(0).mean():.1%}")

    # Top-40% vs Bottom-40%
    q40_lo = df["clv"].quantile(0.40)
    q60_hi = df["clv"].quantile(0.60)
    bot40 = df[df["clv"] <= q40_lo]["realized_pnl"]
    top40 = df[df["clv"] >= q60_hi]["realized_pnl"]
    ratio = pf(top40) / pf(bot40) if pf(bot40) > 0 else float("inf")
    print(f"\nGO/NO-GO:")
    print(f"  Bottom-40% (CLV <= {q40_lo:.3f}): PF={pf(bot40):.3f}  n={len(bot40)}")
    print(f"  Top-40%   (CLV >= {q60_hi:.3f}): PF={pf(top40):.3f}  n={len(top40)}")
    print(f"  High/Low PF ratio: {ratio:.3f}  (threshold: >= 1.40)")
    print(f"  Verdict: {'GO' if ratio >= 1.40 else 'NO-GO'}")

    # Per-year
    df["year"] = df["entry_ts"].dt.year
    print(f"\nPer-year (top-40% PF / bottom-40% PF):")
    for yr in sorted(df["year"].unique()):
        sub = df[df["year"] == yr]
        lo_cut = sub["clv"].quantile(0.40)
        hi_cut = sub["clv"].quantile(0.60)
        b = sub[sub["clv"] <= lo_cut]["realized_pnl"]
        t = sub[sub["clv"] >= hi_cut]["realized_pnl"]
        r = pf(t) / pf(b) if pf(b) > 0 else float("inf")
        print(f"  {yr}: bot PF={pf(b):.3f}(n={len(b)}) top PF={pf(t):.3f}(n={len(t)}) ratio={r:.3f}")

    # Long-only analysis (deployed config)
    df_long = df[df["side"] == "long"]
    print(f"\nLong-only subset (deployed config, n={len(df_long)}):")
    bot40_l = df_long[df_long["clv"] <= df_long["clv"].quantile(0.40)]["realized_pnl"]
    top40_l = df_long[df_long["clv"] >= df_long["clv"].quantile(0.60)]["realized_pnl"]
    ratio_l = pf(top40_l) / pf(bot40_l) if pf(bot40_l) > 0 else float("inf")
    print(f"  Bottom-40%: PF={pf(bot40_l):.3f}  n={len(bot40_l)}")
    print(f"  Top-40%:    PF={pf(top40_l):.3f}  n={len(top40_l)}")
    print(f"  Ratio: {ratio_l:.3f}")

    return df


def analyze_mae_tiers(trades: pd.DataFrame):
    """
    Use MAE distribution to characterize iFVG stop quality.
    Lesson 82: MAE < 0.25R WR=93.3%, MAE < 0.5R WR=83.1%.
    New angle: does initial MAE tier predict eventual outcome better than CLV?
    """
    print("\n=== MAE TIER ANALYSIS (Lesson 82 cross-check) ===")
    for tier_lo, tier_hi, label in [
        (0.0, 0.25, "MAE<0.25R (clean)"),
        (0.25, 0.5, "MAE 0.25-0.5R"),
        (0.5, 0.75, "MAE 0.5-0.75R"),
        (0.75, 1.0, "MAE 0.75-1R"),
        (1.0, 99.0, "MAE>1R (full stop)"),
    ]:
        sub = trades[(trades["r_mae"] >= tier_lo) & (trades["r_mae"] < tier_hi)]
        print(f"  {label:<22} n={len(sub):>5}  WR={sub['win'].mean():>6.1%}  PF={pf(sub['realized_pnl']):>6.3f}  Avg_pnl={sub['realized_pnl'].mean():>8.1f}")


def main():
    trades, bars5 = load_data()
    clv_df = compute_clv(trades, bars5)
    analyze_mae_tiers(trades)
    print("\nDone.")


if __name__ == "__main__":
    main()
