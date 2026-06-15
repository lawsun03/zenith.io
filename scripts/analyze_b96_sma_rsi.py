"""B96: 200-day SMA regime + RSI-pullback-in-trend feature analysis.

Phase-1 only: data-mine the B88 deployed trade CSV.
GO/NO-GO per edge_diagnostics robust bar: PF>=1.2, n>=30, positive in >=60% years,
beats aggregate PF (control_pf). A hit is a NEW HYPOTHESIS to confirm OOS, not a deploy.
"""
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from scripts.edge_diagnostics import localize

TRADE_CSV = "research/mfe_mae_deployed_b88.csv"
BARS_CSV = "bars/bars_MNQ_dbv_2021_2026.csv"


def _pf(sub: pd.DataFrame, col: str = "r") -> float:
    gw = sub.loc[sub[col] > 0, col].sum()
    gl = -sub.loc[sub[col] < 0, col].sum()
    return gw / gl if gl > 0 else float("inf")


def _rsi(series: pd.Series, period: int) -> pd.Series:
    """RSI via Wilder EMA (EWM com = period-1)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.inf)
    return 100 - (100 / (1 + rs))


# ---------------------------------------------------------------------------
# 1. Build daily close + indicators from 5-min bars
# ---------------------------------------------------------------------------
print("Loading bars...")
bars = pd.read_csv(BARS_CSV, parse_dates=["ts"])
bars = bars.sort_values("ts")
bars["date"] = bars["ts"].dt.normalize()

# Last bar of each calendar day = daily "close"
daily = bars.groupby("date")["close"].last().to_frame().sort_index()

# 200-day SMA of daily closes
daily["sma200"] = daily["close"].rolling(200, min_periods=200).mean()
daily["rsi14"] = _rsi(daily["close"], 14)
daily["rsi2"] = _rsi(daily["close"], 2)

# Prior-day values: available at market open (no lookahead)
prev = daily.shift(1).rename(columns={c: f"prev_{c}" for c in daily.columns})
daily_ext = pd.concat([daily, prev], axis=1).reset_index()

print(f"Daily rows: {len(daily_ext)}")
first_sma = daily_ext.dropna(subset=["sma200"])["date"].min()
print(f"  SMA200 valid from: {first_sma}")

# ---------------------------------------------------------------------------
# 2. Load trade CSV and merge indicators
# ---------------------------------------------------------------------------
trades = pd.read_csv(TRADE_CSV, parse_dates=["entry_ts"])
trades["date"] = trades["entry_ts"].dt.normalize()
trades["year"] = trades["entry_ts"].dt.year
trades["r"] = trades["pnl_usd"]   # dollar-denominated PF proxy (sign = win/loss)

merged = trades.merge(daily_ext, on="date", how="left")

# ---------------------------------------------------------------------------
# 3. Regime tagging  (use PRIOR DAY close vs PRIOR DAY SMA — no lookahead)
# ---------------------------------------------------------------------------
has_sma = merged[["prev_close", "prev_sma200"]].notna().all(axis=1)
merged["regime_200sma"] = np.where(
    has_sma,
    np.where(merged["prev_close"] > merged["prev_sma200"], "bull", "bear"),
    "no_sma",
)

# RSI(14) bins (standard)
merged["rsi14_state"] = pd.cut(
    merged["prev_rsi14"],
    bins=[0, 30, 50, 70, 100],
    labels=["oversold<30", "neutral_30-50", "neutral_50-70", "overbought>70"],
    include_lowest=True,
)

# RSI(2) bins (Connors: very tight fast oscillator)
merged["rsi2_state"] = pd.cut(
    merged["prev_rsi2"],
    bins=[0, 10, 30, 70, 90, 100],
    labels=["v_oversold<10", "oversold_10-30", "neutral_30-70", "overbought_70-90", "v_overbought>90"],
    include_lowest=True,
)

print(f"\nTotal trades in CSV: {len(merged)}")
print("Regime distribution:")
print(merged["regime_200sma"].value_counts().to_string())

# ---------------------------------------------------------------------------
# 4. Filter to valid-SMA rows; compute aggregate PF as control
# ---------------------------------------------------------------------------
df = merged[merged["regime_200sma"] != "no_sma"].copy()
print(f"\nUsable trades (valid 200-SMA history): {len(df)}")
print(f"  Excluded (no SMA / 2021 warmup): {len(merged) - len(df)}")

agg_pf = _pf(df)
print(f"Aggregate PF of usable set: {agg_pf:.4f}  <- control_pf for all tests")

# ---------------------------------------------------------------------------
# 5.  200-SMA regime × side
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("TEST 1: 200-SMA REGIME × SIDE")
print("=" * 60)
df["regime_side"] = df["regime_200sma"] + "_" + df["side"]
localize(df, ["regime_side"], value_col="r", control_pf=agg_pf, verbose=True)

print("\n" + "=" * 60)
print("TEST 2: 200-SMA REGIME ALONE")
print("=" * 60)
localize(df, ["regime_200sma"], value_col="r", control_pf=agg_pf, verbose=True)

# ---------------------------------------------------------------------------
# 6.  RSI within BULL regime
# ---------------------------------------------------------------------------
bull = df[df["regime_200sma"] == "bull"].copy()
bull_long = bull[bull["side"] == "long"].copy()
print(f"\nBull regime: {len(bull)} trades  |  Bull-long: {len(bull_long)} trades")

if len(bull) > 0:
    bull_pf = _pf(bull)
    print(f"Bull regime aggregate PF: {bull_pf:.4f}")

    print("\n" + "=" * 60)
    print("TEST 3: RSI(14) STATE in BULL REGIME — all sides")
    print("=" * 60)
    b14 = bull[bull["rsi14_state"].notna()].copy()
    localize(b14, ["rsi14_state"], value_col="r", control_pf=bull_pf, verbose=True)

    print("\n" + "=" * 60)
    print("TEST 4: RSI(14) STATE in BULL REGIME — longs only")
    print("=" * 60)
    bl14 = bull_long[bull_long["rsi14_state"].notna()].copy()
    if len(bl14) > 0:
        bl14_pf = _pf(bl14)
        localize(bl14, ["rsi14_state"], value_col="r", control_pf=bl14_pf, verbose=True)

    print("\n" + "=" * 60)
    print("TEST 5: RSI(2) STATE in BULL REGIME — longs only  (Connors style)")
    print("=" * 60)
    bl2 = bull_long[bull_long["rsi2_state"].notna()].copy()
    if len(bl2) > 0:
        bl2_pf = _pf(bl2)
        localize(bl2, ["rsi2_state"], value_col="r", control_pf=bl2_pf, verbose=True)

# ---------------------------------------------------------------------------
# 7.  Engine-type × regime cross-cut
# ---------------------------------------------------------------------------
print("\n" + "=" * 60)
print("TEST 6: ENGINE TYPE × REGIME")
print("=" * 60)
df["eng_regime"] = df["engine_type"] + "_" + df["regime_200sma"]
localize(df, ["eng_regime"], value_col="r", control_pf=agg_pf, verbose=True)

# ---------------------------------------------------------------------------
# 8.  Bear regime breakdown (sanity check)
# ---------------------------------------------------------------------------
bear = df[df["regime_200sma"] == "bear"].copy()
print(f"\nBear regime: {len(bear)} trades   PF={_pf(bear):.4f}")
print(f"Bear-long: {len(bear[bear['side']=='long'])}   PF={_pf(bear[bear['side']=='long']):.4f}")
print(f"Bear-short: {len(bear[bear['side']=='short'])}   PF={_pf(bear[bear['side']=='short']):.4f}")

print("\n=== B96 DONE ===")
