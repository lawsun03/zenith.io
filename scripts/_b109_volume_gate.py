"""B109: ORB breakout-bar volume Phase-1 data mining.

Hypothesis: ORB breakout-bar volume predicts trade quality.
Method:
  1. Load ORB trades from mfe_mae_deployed_combined_clean.csv (n=862, excl 2022).
  2. Aggregate 1-min bars to 5-min bars (sum volume); join on entry_ts.
  3. Compute vol_ratio = bar_volume / 20-trade rolling median of ORB signal-bar
     volume (rolling over ORB signal bars only -- controls for time-of-day effects).
  4. Quartile-bin by vol_ratio; run edge_diagnostics.localize.
  5. Cross-cut: side x vol_quartile.
  6. GO/NO-GO: PF(top-40%) / PF(bottom-40%) >= 1.40, both n >= 30, 3+/5 years.

If GO  -> recommend Phase-2 ORB engine gate as B111.
If NO-GO -> REJECT (extends the ORB quality-predictor rejection series).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.edge_diagnostics import add_time_dims, localize

MFE_CSV = ROOT / "research" / "mfe_mae_deployed_combined_clean.csv"
BARS_CSV = ROOT / "bars" / "bars_MNQ_dbv_2021_2026.csv"
YEARS = [2021, 2023, 2024, 2025, 2026]  # 2022 = frozen holdout

ROLLING_WINDOW = 20  # trades (ORB signal bars only)
TOP_PCT = 0.60       # top 40% = above p60
BOT_PCT = 0.40       # bottom 40% = below p40
MIN_PF_RATIO = 1.40
MIN_N = 30
MIN_YEARS = 3


def main() -> None:
    # --- 1. Load ORB trades --------------------------------------------------
    mfe = pd.read_csv(MFE_CSV, parse_dates=["entry_ts", "exit_ts"])
    orb = mfe[mfe["engine_type"] == "orb"].copy()
    # Exclude 2022 holdout
    orb["year"] = pd.to_datetime(orb["entry_ts"], utc=True).dt.year
    orb = orb[orb["year"].isin(YEARS)].reset_index(drop=True)
    print(f"ORB trades (excl 2022): {len(orb)}")

    # Normalize entry_ts to UTC
    orb["entry_ts_utc"] = pd.to_datetime(orb["entry_ts"], utc=True)

    # --- 2. Aggregate 1-min bars to 5-min ------------------------------------
    bars = pd.read_csv(BARS_CSV, parse_dates=["ts"])
    bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
    bars = bars.set_index("ts").sort_index()

    # ORB strategy uses 5-min bars where close time has minute % 5 == 4
    # e.g. 13:54 = close of the bar covering 13:50-13:54.
    # pandas resample with offset='4min' and label/closed='right' achieves this:
    #   bin (13:49, 13:54] -> label 13:54
    bars_5m = bars["volume"].resample("5min", offset="4min", closed="right", label="right").sum()
    bars_5m = bars_5m[bars_5m > 0]  # drop empty bins (non-trading hours)
    bars_5m = bars_5m.reset_index()
    bars_5m.columns = ["ts_5m", "vol_5m"]

    print(f"5-min bars with volume: {len(bars_5m)}")

    # --- 3. Join ORB trades to 5-min bar volume ------------------------------
    orb = orb.merge(
        bars_5m.rename(columns={"ts_5m": "entry_ts_utc"}),
        on="entry_ts_utc",
        how="left",
    )
    n_missing = orb["vol_5m"].isna().sum()
    if n_missing > 0:
        print(f"WARNING: {n_missing} ORB trades had no matching 5-min bar volume. Dropping.")
        orb = orb.dropna(subset=["vol_5m"]).reset_index(drop=True)

    print(f"ORB trades with volume data: {len(orb)}")

    # --- 4. Rolling median vol (ORB signal bars only, window=20 trades) ------
    # Sort chronologically
    orb = orb.sort_values("entry_ts_utc").reset_index(drop=True)
    orb["vol_rolling_med"] = orb["vol_5m"].rolling(window=ROLLING_WINDOW, min_periods=5).median()

    # Fallback: for first <5 trades where rolling is NaN, use global median
    global_med = float(orb["vol_5m"].median())
    orb["vol_rolling_med"] = orb["vol_rolling_med"].fillna(global_med)

    orb["vol_ratio"] = orb["vol_5m"] / orb["vol_rolling_med"]

    # --- 5. Quartile bins ----------------------------------------------------
    q25, q50, q75 = orb["vol_ratio"].quantile([0.25, 0.50, 0.75])
    print(f"\nVol ratio quartiles: Q25={q25:.3f} Q50={q50:.3f} Q75={q75:.3f}")
    print(f"Vol ratio range: {orb['vol_ratio'].min():.3f} - {orb['vol_ratio'].max():.3f}")

    def vol_bucket(v: float) -> str:
        if v <= q25:
            return "Q1_low"
        if v <= q50:
            return "Q2"
        if v <= q75:
            return "Q3"
        return "Q4_high"

    orb["vol_quartile"] = orb["vol_ratio"].apply(vol_bucket)

    # Add R column: compute from pnl_usd, treat as R-multiple proxy via win/loss
    # B109 spec uses per-trade CSV for edge_diagnostics which expects value_col='r'
    # Use pnl_usd sign to compute r: winner = +1 notionally, loser = -1 * |loss/win| approx
    # Better: derive R from actual trade outcome using stop_dist implied by r_mae if loss
    # Simplest and most robust: use sign(pnl_usd) * |r_mfe| for wins, -1 for losses at stop
    # Even simpler: the localize() function just needs sign + magnitude to compute PF.
    # Use pnl_usd directly as the value column (localize uses it to compute PF).
    orb["r"] = orb["pnl_usd"]  # PF computed from gross USD P&L

    # Add year and time dims
    orb = add_time_dims(orb, "entry_ts_utc")

    # --- 6. edge_diagnostics on vol_quartile ---------------------------------
    print("\n" + "="*60)
    print("DIMENSION: vol_quartile")
    print("="*60)
    res = localize(orb, dims=["vol_quartile"], value_col="r", year_col="year")

    # --- 7. Cross-cut: side x vol_quartile -----------------------------------
    orb["side_vol"] = orb["side"] + "_" + orb["vol_quartile"]
    print("\n" + "="*60)
    print("DIMENSION: side x vol_quartile")
    print("="*60)
    localize(orb, dims=["side_vol"], value_col="r", year_col="year")

    # --- 8. Year-by-year breakdown -------------------------------------------
    print("\n" + "="*60)
    print("DIMENSION: year")
    print("="*60)
    localize(orb, dims=["year"], value_col="r", year_col="year")

    # --- 9. GO/NO-GO gate ----------------------------------------------------
    print("\n" + "="*60)
    print("GO/NO-GO GATE")
    print("="*60)

    # Compute PF for top-40% (vol_ratio > p60) and bottom-40% (vol_ratio <= p40)
    p40 = orb["vol_ratio"].quantile(0.40)
    p60 = orb["vol_ratio"].quantile(0.60)
    top40 = orb[orb["vol_ratio"] > p60]
    bot40 = orb[orb["vol_ratio"] <= p40]

    def pf(df: pd.DataFrame) -> float:
        g = float(df.loc[df["r"] > 0, "r"].sum())
        l = float(-df.loc[df["r"] < 0, "r"].sum())
        return g / l if l > 0 else float("inf")

    def yr_robustness(df: pd.DataFrame, min_yrs: int) -> tuple[int, int]:
        yr = df.groupby("year")["r"].sum()
        return int((yr > 0).sum()), len(yr)

    pf_top = pf(top40)
    pf_bot = pf(bot40)
    n_top = len(top40)
    n_bot = len(bot40)
    yrs_top, total_yrs_top = yr_robustness(top40, MIN_YEARS)
    yrs_bot, total_yrs_bot = yr_robustness(bot40, MIN_YEARS)
    pf_ratio = pf_top / pf_bot if pf_bot > 0 else float("inf")

    print(f"Top-40% (vol_ratio > p60={p60:.3f}): n={n_top} PF={pf_top:.3f} yrs+={yrs_top}/{total_yrs_top}")
    print(f"Bot-40% (vol_ratio <= p40={p40:.3f}): n={n_bot} PF={pf_bot:.3f} yrs+={yrs_bot}/{total_yrs_bot}")
    print(f"PF ratio (top/bot): {pf_ratio:.3f}  (gate >= {MIN_PF_RATIO})")

    gate_pf = pf_ratio >= MIN_PF_RATIO
    gate_n = n_top >= MIN_N and n_bot >= MIN_N
    gate_yrs = yrs_top >= MIN_YEARS

    print(f"\nGate checks:")
    print(f"  PF ratio >= {MIN_PF_RATIO}: {'PASS' if gate_pf else 'FAIL'} ({pf_ratio:.3f})")
    print(f"  Both groups n >= {MIN_N}: {'PASS' if gate_n else 'FAIL'} ({n_top} / {n_bot})")
    print(f"  Top-40% positive in >= {MIN_YEARS}/5 years: {'PASS' if gate_yrs else 'FAIL'} ({yrs_top}/{total_yrs_top})")

    verdict = "GO" if (gate_pf and gate_n and gate_yrs) else "NO-GO"
    print(f"\nFINAL VERDICT: {verdict}")

    if verdict == "GO":
        print("Action: Queue Phase-2 ORB engine gate as B111.")
    else:
        print("Action: REJECT -- extends ORB quality-predictor rejection series.")

    # --- 10. Volume stats summary -------------------------------------------
    print("\n" + "="*60)
    print("VOLUME DISTRIBUTION SUMMARY")
    print("="*60)
    print(f"Median 5-min signal-bar volume: {orb['vol_5m'].median():.0f}")
    print(f"Mean vol_ratio: {orb['vol_ratio'].mean():.3f}")
    print(f"Vol ratio distribution:")
    print(orb["vol_ratio"].describe().to_string())

    print("\n" + "="*60)
    print("QUARTILE SUMMARY")
    print("="*60)
    for q in ["Q1_low", "Q2", "Q3", "Q4_high"]:
        grp = orb[orb["vol_quartile"] == q]
        print(f"  {q}: n={len(grp)} PF={pf(grp):.3f} WR={float((grp['r']>0).mean()*100):.1f}%")


if __name__ == "__main__":
    main()
