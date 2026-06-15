"""
wk5-r3 research: displacement bar body/ATR ratio as iFVG quality predictor.
Uses mfe_mae_ifvg_clean.csv (research-baseline, all-sides) + 1-min bars resampled to 5-min.
Displacement bar approximated as entry_ts - 2 bars (600s) — exact for fresh signals (54.7%),
approximate for mid/stale. Filtered to LO longs, 5y excl 2022.
Phase 1 GO: top-40%/bottom-40% PF ratio >= 1.30, consistent 3+/5 years.
"""
import pandas as pd
import numpy as np
import sys

MFE_CSV = "research/mfe_mae_ifvg_clean.csv"
BARS_CSV = "bars/bars_MNQ_dbv_2021_2026.csv"

# --- Load trades ---
trades = pd.read_csv(MFE_CSV, parse_dates=["entry_ts", "exit_ts"])
# Filter: long only, exclude 2022
trades = trades[(trades["side"] == "long") & (trades["year"] != 2022)].copy()
print(f"Trades after filter: {len(trades)}")

# --- Load and resample 1-min bars to 5-min ---
print("Loading bars...")
bars = pd.read_csv(BARS_CSV, parse_dates=["ts"], index_col="ts")
bars = bars.sort_index()
# Resample to 5-min, right-labeled, closed on right
bars5 = bars.resample("5min").agg({
    "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
}).dropna()
bars5["body"] = (bars5["close"] - bars5["open"]).abs()
# Rolling ATR (14 bars of 5-min = 70 min)
bars5["tr"] = pd.concat([
    bars5["high"] - bars5["low"],
    (bars5["high"] - bars5["close"].shift(1)).abs(),
    (bars5["low"] - bars5["close"].shift(1)).abs()
], axis=1).max(axis=1)
bars5["atr14"] = bars5["tr"].rolling(14).mean()
bars5["body_atr"] = bars5["body"] / bars5["atr14"].replace(0, np.nan)
print(f"5-min bars: {len(bars5)}, body_atr range: {bars5['body_atr'].min():.3f}-{bars5['body_atr'].max():.3f}")

# --- Compute displacement bar timestamp per trade ---
# Displacement bar = 2 bars before entry (entry_ts - 600s for fresh signals)
# entry_ts is the 5-min bar close at which we enter
# We round entry_ts to the nearest 5-min boundary first
def round_to_5min(ts):
    """Round down to nearest 5-min mark."""
    minutes = ts.minute
    rounded_min = (minutes // 5) * 5
    return ts.replace(minute=rounded_min, second=0, microsecond=0)

trades["entry_ts_5m"] = trades["entry_ts"].apply(round_to_5min)
trades["disp_ts"] = trades["entry_ts_5m"] - pd.Timedelta(seconds=600)  # 2 bars before

# --- Join displacement bar body/ATR ---
body_atr_map = bars5["body_atr"]
trades["disp_body_atr"] = trades["disp_ts"].map(body_atr_map)

matched = trades["disp_body_atr"].notna().sum()
print(f"Matched displacement bars: {matched}/{len(trades)} ({100*matched/len(trades):.1f}%)")
trades = trades[trades["disp_body_atr"].notna()].copy()

# --- Compute PF per trade ---
# Use realized_pnl as profit (positive = win)
trades["win"] = trades["realized_pnl"] > 0
trades["pnl_abs"] = trades["realized_pnl"].abs()

def compute_pf(subset):
    wins = subset[subset["win"]]["realized_pnl"].sum()
    losses = subset[~subset["win"]]["realized_pnl"].abs().sum()
    if losses == 0:
        return np.nan
    return wins / losses

# --- Quartile bucketing ---
trades["q"] = pd.qcut(trades["disp_body_atr"], 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"])
print("\n--- Quintile PF (displacement bar body/ATR) ---")
print(f"{'Bucket':<6} {'n':>5} {'WR%':>6} {'PF':>7} {'range':>20}")
q_results = {}
for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
    s = trades[trades["q"] == q]
    pf = compute_pf(s)
    wr = s["win"].mean() * 100
    lo = s["disp_body_atr"].min()
    hi = s["disp_body_atr"].max()
    q_results[q] = pf
    print(f"{q:<6} {len(s):>5} {wr:>6.1f} {pf:>7.3f} [{lo:.3f}-{hi:.3f}]")

# GO criterion: top-40% vs bottom-40%
bottom_40 = trades[trades["q"].isin(["Q1", "Q2"])]
top_40 = trades[trades["q"].isin(["Q4", "Q5"])]
pf_bottom = compute_pf(bottom_40)
pf_top = compute_pf(top_40)
ratio = pf_top / pf_bottom
print(f"\nBottom-40% (Q1+Q2) PF: {pf_bottom:.3f}, n={len(bottom_40)}")
print(f"Top-40%    (Q4+Q5) PF: {pf_top:.3f},  n={len(top_40)}")
print(f"Top/Bottom ratio: {ratio:.3f} (GO threshold: >= 1.30)")

# --- Per-year consistency ---
print("\n--- Per-year ratio (top-40 PF / bottom-40 PF) ---")
year_ratios = []
for yr in sorted(trades["year"].unique()):
    s = trades[trades["year"] == yr]
    pf_b = compute_pf(s[s["q"].isin(["Q1", "Q2"])])
    pf_t = compute_pf(s[s["q"].isin(["Q4", "Q5"])])
    r = pf_t / pf_b if (pf_b and pf_b > 0) else np.nan
    year_ratios.append(r >= 1.30 if not np.isnan(r) else False)
    print(f"  {yr}: bottom={pf_b:.3f}, top={pf_t:.3f}, ratio={r:.3f}, GO={'YES' if (r and r>=1.30) else 'NO'}")

n_consistent = sum(year_ratios)
print(f"\nYears consistent (ratio>=1.30): {n_consistent}/5")
print(f"\nVERDICT: {'GO' if ratio >= 1.30 and n_consistent >= 3 else 'NO-GO'}")
