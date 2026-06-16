"""B100 — ORB OR-width Phase-1 cut.

Tags each B99 trade with its opening-range width (9:30+15min ET window, same
as the live ORB detector) then runs edge_diagnostics.localize across OR-width
quartile and OR/ATR-ratio buckets.

Usage:
    python scripts/_b100_analyze.py

Outputs: prints bucketed stats; writes research/equity_b99/b100_tagged_r2p5.csv
and research/equity_b99/b100_tagged_r1p5.csv for inspection.
"""
from __future__ import annotations

import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.edge_diagnostics import localize, add_time_dims  # noqa: E402

BARS_CSV = ROOT / "bars" / "bars_MNQ_dbv_2021_2026.csv"
EQUITY_DIR = ROOT / "research" / "equity_b99"

OR_START_ET = "09:30"
OR_MINUTES = 15


def build_or_table(bars_csv: Path) -> pd.DataFrame:
    """Return a DataFrame with columns: date_et, or_high, or_low, or_width, daily_atr."""
    print("Loading bars …", flush=True)
    bars = pd.read_csv(bars_csv, parse_dates=["ts"])
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)

    # Convert to ET for time-of-day filtering
    et = bars["ts"].dt.tz_convert("America/New_York")
    bars["ts_et"] = et
    bars["date_et"] = et.dt.date
    bars["hour_et"] = et.dt.hour
    bars["minute_et"] = et.dt.minute
    bars["time_et"] = et.dt.hour * 60 + et.dt.minute  # minutes since midnight ET

    # OR window: 9:30 <= time < 9:45  (ts labels bar CLOSE, so close in [9:31..9:45] covers open 9:30..9:44)
    # The ORB detector uses bar.ts >= open_et and bar.ts < open_et + range_minutes for 5-min bars.
    # For 1-min bars: bars with ts in (9:30, 9:45] (close-labeled) cover opens in [9:29, 9:44].
    # We want opens at 9:30..9:44 → close-labels 9:31..9:45.
    # Use: 9:30 < ts_et <= 9:45 as close-labeled convention.
    # (The ORB detector opens the window at the first bar whose close >= 9:30, so this is right.)
    open_min = 9 * 60 + 30
    close_min = open_min + OR_MINUTES  # 9:45
    mask_or = (bars["time_et"] > open_min) & (bars["time_et"] <= close_min)
    or_bars = bars[mask_or].copy()

    or_table = (
        or_bars.groupby("date_et")
        .agg(or_high=("high", "max"), or_low=("low", "min"))
        .reset_index()
    )
    or_table["or_width"] = or_table["or_high"] - or_table["or_low"]

    # Daily ATR: use the regular session (9:30-16:00 ET) daily high/low per date,
    # then compute Wilder 14-day ATR from daily ranges as a normalisation denominator.
    mask_session = (bars["time_et"] > open_min) & (bars["time_et"] <= 16 * 60)
    session_bars = bars[mask_session].copy()
    daily = (
        session_bars.groupby("date_et")
        .agg(day_high=("high", "max"), day_low=("low", "min"), day_close=("close", "last"))
        .reset_index()
    )
    daily = daily.sort_values("date_et").reset_index(drop=True)
    # True Range (no overnight gap since we use session-only): day_high - day_low
    daily["tr"] = daily["day_high"] - daily["day_low"]
    # Simple 14-day rolling mean of TR as ATR proxy (Wilder too slow to warm up for 1-2y)
    daily["atr14"] = daily["tr"].rolling(14, min_periods=5).mean()

    or_table = or_table.merge(daily[["date_et", "atr14"]], on="date_et", how="left")
    or_table["or_atr_ratio"] = or_table["or_width"] / or_table["atr14"]

    # Drop rows with no ATR (warmup period)
    or_table = or_table.dropna(subset=["atr14", "or_width"])
    or_table = or_table[or_table["or_width"] > 0]
    print(f"OR table: {len(or_table)} trading days with valid OR and ATR", flush=True)
    return or_table


def tag_trades(trades_csv: Path, or_table: pd.DataFrame, r_label: str) -> pd.DataFrame:
    """Join trade CSV with OR table; add quartile bucket columns."""
    df = pd.read_csv(trades_csv, parse_dates=["entry_ts", "exit_ts"])
    df["entry_ts"] = pd.to_datetime(df["entry_ts"], utc=True)

    et = df["entry_ts"].dt.tz_convert("America/New_York")
    df["date_et"] = et.dt.date
    df["year"] = et.dt.year
    df["r_label"] = r_label

    df = df.merge(or_table[["date_et", "or_width", "or_atr_ratio", "atr14"]], on="date_et", how="left")
    unmatched = df["or_width"].isna().sum()
    if unmatched > 0:
        print(f"  WARNING: {unmatched} trades with no OR data (holiday/warm-up) — dropped")
    df = df.dropna(subset=["or_width"])

    # Absolute width quartile buckets (Q1=narrowest .. Q4=widest)
    df["or_q"] = pd.qcut(df["or_width"], q=4, labels=["Q1_narrow", "Q2", "Q3", "Q4_wide"])

    # OR/ATR ratio quartile buckets
    df["or_atr_q"] = pd.qcut(df["or_atr_ratio"], q=4,
                              labels=["A1_tight", "A2", "A3", "A4_bloated"])

    # Also: raw buckets by absolute threshold (wide-OR definition from Lawrence's example:
    # OR=133.75pt was an outlier; let's define "very wide" as top 10% and "wide" as >p75)
    p75 = df["or_width"].quantile(0.75)
    p90 = df["or_width"].quantile(0.90)
    p50 = df["or_width"].quantile(0.50)
    print(f"  OR width: p25={df['or_width'].quantile(0.25):.1f}  p50={p50:.1f}  "
          f"p75={p75:.1f}  p90={p90:.1f}  max={df['or_width'].max():.1f}")

    return df


def run_analysis(df: pd.DataFrame, r_label: str) -> None:
    print(f"\n{'='*70}")
    print(f"B100 — ORB r={r_label}  n={len(df)} trades")
    print(f"{'='*70}")

    # Primary cut: absolute OR-width quartile
    print("\n--- OR-width absolute quartile (Q1=narrow, Q4=wide) ---")
    localize(df, ["or_q"], value_col="pnl_usd", year_col="year",
             pf_min=1.2, min_n=30, control_pf=None, verbose=True)

    # Secondary cut: OR/ATR ratio quartile
    print("\n--- OR/ATR-ratio quartile (A1=tight, A4=bloated) ---")
    localize(df, ["or_atr_q"], value_col="pnl_usd", year_col="year",
             pf_min=1.2, min_n=30, control_pf=None, verbose=True)

    # Two-way: side x OR-width quartile (to check if wide-OR hurts longs vs shorts differently)
    print("\n--- side x OR-width quartile ---")
    localize(df, ["side", "or_q"], value_col="pnl_usd", year_col="year",
             pf_min=1.2, min_n=20, control_pf=None, verbose=True)

    # Narrow-only slice (keep Q1-Q2, drop Q3-Q4)
    narrow = df[df["or_q"].isin(["Q1_narrow", "Q2"])].copy()
    print(f"\n--- NARROW OR (Q1+Q2, n={len(narrow)}) vs WIDE (Q3+Q4, n={len(df)-len(narrow)}) ---")
    for label, sub in [("NARROW Q1+Q2", narrow), ("WIDE Q3+Q4", df[~df["or_q"].isin(["Q1_narrow", "Q2"])])]:
        wins = (sub["pnl_usd"] > 0).sum()
        losses = (sub["pnl_usd"] < 0).sum()
        gw = sub.loc[sub["pnl_usd"] > 0, "pnl_usd"].sum()
        gl = sub.loc[sub["pnl_usd"] < 0, "pnl_usd"].sum()
        pf = gw / (-gl) if gl < 0 else float("inf")
        print(f"  {label}: n={len(sub)} win%={wins/len(sub)*100:.0f}% "
              f"PF={pf:.3f} net=${gw+gl:+,.0f}")


def main():
    or_table = build_or_table(BARS_CSV)

    results = {}
    for r_tag in ["r2p5", "r1p5", "r1p0"]:
        trades_csv = EQUITY_DIR / f"trades_{r_tag}.csv"
        r_label = r_tag.replace("r", "").replace("p", ".")
        if not trades_csv.exists():
            print(f"Skipping {r_tag} — not found")
            continue
        print(f"\nTagging r={r_label} trades …", flush=True)
        df = tag_trades(trades_csv, or_table, r_label)
        out = EQUITY_DIR / f"b100_tagged_{r_tag}.csv"
        df.to_csv(out, index=False)
        print(f"  Saved to {out}")
        results[r_label] = df

    # Primary analysis on r2.5 (deployed config, Lawrence's concern)
    if "2.5" in results:
        run_analysis(results["2.5"], "2.5")

    # Cross-check on r1.5 (B99 candidate)
    if "1.5" in results:
        run_analysis(results["1.5"], "1.5")

    # Year-by-year check for the top Q4_wide bucket
    df25 = results.get("2.5")
    if df25 is not None:
        wide = df25[df25["or_q"] == "Q4_wide"]
        print(f"\n--- Q4_WIDE year breakdown (r2.5) ---")
        for yr, grp in wide.groupby("year"):
            wins = (grp["pnl_usd"] > 0).sum()
            gw = grp.loc[grp["pnl_usd"] > 0, "pnl_usd"].sum()
            gl = grp.loc[grp["pnl_usd"] < 0, "pnl_usd"].sum()
            pf = gw / (-gl) if gl < 0 else float("inf")
            print(f"  {yr}: n={len(grp)} win%={wins/len(grp)*100:.0f}% PF={pf:.2f} net=${gw+gl:+,.0f}")

    print("\nDONE — B100 Phase-1 analysis complete")


if __name__ == "__main__":
    main()
