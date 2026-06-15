"""
B57 Phase 1 -- Composite quality grader analysis.

Loads per-trade iFVG + ORB MFE/MAE data (5y excl 2022), computes a feature
matrix of validated predictors, fits a weight-of-evidence additive score (no
sklearn: pure pandas/numpy), evaluates per-decile PF on OOS 2025-26.

Features (all PRE-entry, derived from existing MFE/MAE CSVs):
  - is_long: side == long
  - is_rank1: first signal of the day (iFVG); ORB is always rank-1
  - et_hour_bucket: london (2-5 ET), ny_am (8-10 ET), noon (11-12 ET),
                    ny_pm (13-15 ET), other
  - is_orb: engine == orb (vs ifvg)
  - price_regime: NQ entry_price bucket as ATR proxy

Scoring: weight-of-evidence (WoE) per feature from training set.
  WoE(feature=v) = log(P(win|feature=v) / P(win|feature!=v)) * presence
  Combined score = sum of WoE contributions. Fully deterministic.

GO/NO-GO (both required):
  1. Top-decile OOS PF > bottom-decile OOS PF by >= 30% (ratio >= 1.3)
  2. Top-half OOS PF >= best single existing filter (long-only / rank1 / london+ny_am)
     at comparable volume (>= 40% of OOS trades)
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    import pandas as pd
    import numpy as np
    from zoneinfo import ZoneInfo
    ET_TZ = ZoneInfo("America/New_York")
except ImportError as e:
    print(f"Missing dependency: {e}")
    sys.exit(1)

IFVG_CSV = ROOT / "research" / "mfe_mae_ifvg_clean.csv"
ORB_CSV  = ROOT / "research" / "mfe_mae_orb_clean.csv"

TRAIN_YEARS = [2021, 2023, 2024]
OOS_YEARS   = [2025, 2026]
EXCLUDE_YEAR = 2022

# Best single-filter PF benchmarks from prior research (reference; verified OOS):
# B15 long-only sust 1.13x at r1.25 (PF in funded context)
# B19 long-only + london+ny_am: sust 1.60x, PF 1.173 at r1.25
# B23 rank-1 cap: PF 1.137 at r1.25 (worse than B19)
# OOS PF from the trade CSVs will be computed directly below.


def load_csv(path: Path, engine: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["engine"] = engine
    df["entry_ts"] = pd.to_datetime(df["entry_ts"], utc=True)
    df["year"] = df["year"].astype(int)
    return df


def et_hour(ts_utc: "pd.Series") -> "pd.Series":
    """Convert UTC timestamp series to US/Eastern hour-of-day."""
    return ts_utc.dt.tz_convert(ET_TZ).dt.hour


def et_date(ts_utc: "pd.Series") -> "pd.Series":
    return ts_utc.dt.tz_convert(ET_TZ).dt.date


def compute_signal_rank(df: pd.DataFrame) -> pd.Series:
    """Assign signal rank within each ET date, sorted by entry_ts."""
    df = df.copy().sort_values(["et_date", "entry_ts"])
    df["signal_rank"] = df.groupby("et_date").cumcount() + 1
    return df["signal_rank"]


def pf(pnls: pd.Series) -> float:
    wins = pnls[pnls > 0].sum()
    loss = abs(pnls[pnls < 0].sum())
    return float(wins / loss) if loss > 0 else float("inf")


def woe(series: pd.Series, win: pd.Series) -> float:
    """Weight-of-evidence for a binary feature relative to base rate."""
    p_win_true  = win[series == 1].mean() if (series == 1).any() else 0.0
    p_win_false = win[series == 0].mean() if (series == 0).any() else 0.0
    # Avoid log(0)
    eps = 1e-6
    return float(np.log((p_win_true + eps) / (p_win_false + eps)))


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Add feature columns to the dataframe."""
    df = df.copy()
    df["et_hour"] = et_hour(df["entry_ts"])
    df["et_date"] = et_date(df["entry_ts"])

    # Signal rank -- per engine (ORB is always 1; iFVG counted within each day)
    ifvg_mask = df["engine"] == "ifvg"
    df.loc[ifvg_mask, "signal_rank"] = compute_signal_rank(df[ifvg_mask]).values
    df.loc[~ifvg_mask, "signal_rank"] = 1

    # Binary features
    df["is_long"]   = (df["side"] == "long").astype(int)
    df["is_rank1"]  = (df["signal_rank"] == 1).astype(int)
    df["is_orb"]    = (df["engine"] == "orb").astype(int)

    # ET-hour buckets (half-open, in local ET)
    df["hour_london"] = df["et_hour"].between(2, 5, inclusive="left").astype(int)   # 2-4 ET
    df["hour_ny_am"]  = df["et_hour"].between(8, 11, inclusive="left").astype(int)  # 8-10 ET
    df["hour_noon"]   = df["et_hour"].isin([11, 12]).astype(int)
    df["hour_ny_pm"]  = df["et_hour"].isin([13, 14, 15]).astype(int)

    # NQ price-regime (ATR proxy; NQ ranges roughly 12k-22k over study period)
    df["price_regime"] = pd.cut(
        df["entry_price"],
        bins=[0, 15000, 18500, 21000, 9_999_999],
        labels=["low", "mid", "high", "vhigh"],
    ).astype(str)
    price_order = {"low": 0, "mid": 1, "high": 2, "vhigh": 3}
    df["price_regime_num"] = df["price_regime"].map(price_order)

    # Outcome
    df["win"]    = (df["realized_pnl"] > 0).astype(int)
    df["pnl"]    = df["realized_pnl"].astype(float)

    return df


def compute_woe_table(train: pd.DataFrame, features: list[str]) -> dict[str, float]:
    """Compute WoE for each binary feature from training set."""
    win = train["win"]
    table = {}
    for f in features:
        table[f] = woe(train[f], win)
    return table


def score_trades(df: pd.DataFrame, woe_table: dict[str, float]) -> pd.Series:
    """Compute composite score (sum of WoE contributions) for each trade."""
    scores = pd.Series(0.0, index=df.index)
    for f, w in woe_table.items():
        scores += df[f].astype(float) * w
    return scores


def decile_pf(df: pd.DataFrame, score_col: str = "score") -> list:
    df = df.copy()
    # Use duplicates='drop' to handle ties; get actual bin labels
    df["decile"] = pd.qcut(df[score_col], 10, labels=False, duplicates="drop")
    buckets = sorted(df["decile"].dropna().unique())
    print(f"\n  {'Bucket':>8} {'n':>5} {'WR%':>6} {'PF':>6}")
    pfs = []
    for d in buckets:
        sub = df[df["decile"] == d]
        _pf = pf(sub["pnl"])
        _wr = sub["win"].mean() * 100
        pfs.append(_pf)
        print(f"  D{int(d)+1:<7d} {len(sub):>5} {_wr:>6.1f} {_pf:>6.3f}")
    return pfs


def main() -> None:
    # Load and combine
    ifvg = load_csv(IFVG_CSV, "ifvg")
    orb  = load_csv(ORB_CSV,  "orb")
    df   = pd.concat([ifvg, orb], ignore_index=True)

    # Exclude holdout year
    df = df[df["year"] != EXCLUDE_YEAR].reset_index(drop=True)
    df = build_features(df)

    # Split
    train = df[df["year"].isin(TRAIN_YEARS)].copy()
    oos   = df[df["year"].isin(OOS_YEARS)].copy()

    print(f"Total: {len(df)} trades  |  Train (2021+23+24): {len(train)}  |  OOS (2025+26): {len(oos)}")
    print(f"Engine split — iFVG: {(df['engine']=='ifvg').sum()}  ORB: {(df['engine']=='orb').sum()}")

    # Add interaction: long side in London (B15+B19 interaction)
    for d in [df, train, oos]:
        d["is_long_london"] = ((d["is_long"] == 1) & (d["hour_london"] == 1)).astype(int)
        d["is_long_ny_am"]  = ((d["is_long"] == 1) & (d["hour_ny_am"] == 1)).astype(int)

    # Feature set (all pre-entry, deterministic)
    features = [
        "is_long", "is_rank1", "is_orb",
        "hour_london", "hour_ny_am", "hour_noon", "hour_ny_pm",
        "is_long_london", "is_long_ny_am",
    ]

    # Training PF breakdown by each feature for reference
    print("\n--- Training-set feature PF breakdown ---")
    print(f"  {'Feature':30s} {'True n':>7} {'True PF':>8} {'False n':>8} {'False PF':>9} {'WoE':>7}")
    woe_table = {}
    for f in features:
        t = train[train[f] == 1]
        ff = train[train[f] == 0]
        t_pf  = pf(t["pnl"])
        ff_pf = pf(ff["pnl"])
        w = woe(train[f], train["win"])
        woe_table[f] = w
        print(f"  {f:30s} {len(t):>7} {t_pf:>8.3f} {len(ff):>8} {ff_pf:>9.3f} {w:>7.3f}")

    print("\n--- WoE table (score contributions) ---")
    for f, w in sorted(woe_table.items(), key=lambda x: -abs(x[1])):
        print(f"  {f:30s}  WoE={w:+.3f}")

    # Score all trades
    df["score"] = score_trades(df, woe_table)
    train["score"] = score_trades(train, woe_table)
    oos["score"]  = score_trades(oos,   woe_table)

    # Baseline PF
    print(f"\n--- Baseline PF ---")
    print(f"  Train PF: {pf(train['pnl']):.3f}  (n={len(train)})")
    print(f"  OOS PF:   {pf(oos['pnl']):.3f}  (n={len(oos)})")

    # OOS per-decile analysis
    print("\n--- OOS per-decile PF (composite score) ---")
    pfs_oos = decile_pf(oos, "score")

    top_d_pf = pfs_oos[-1]  # highest-score decile
    bot_d_pf = pfs_oos[0]   # lowest-score decile
    ratio = top_d_pf / bot_d_pf if bot_d_pf > 0 else float("inf")

    # OOS top-half vs bottom-half
    med_score = oos["score"].median()
    top_half  = oos[oos["score"] >= med_score]
    bot_half  = oos[oos["score"] <  med_score]

    print(f"\n  Top-half  OOS PF: {pf(top_half['pnl']):.3f}  (n={len(top_half)}, {100*len(top_half)/len(oos):.0f}% of OOS)")
    print(f"  Bot-half  OOS PF: {pf(bot_half['pnl']):.3f}  (n={len(bot_half)})")
    print(f"  Full      OOS PF: {pf(oos['pnl']):.3f}  (n={len(oos)})")

    # Best single-filter baselines on OOS data
    print("\n--- OOS single-filter baselines ---")
    long_oos     = oos[oos["is_long"] == 1]
    rank1_oos    = oos[oos["is_rank1"] == 1]
    ldn_nyam_oos = oos[(oos["hour_london"] == 1) | (oos["hour_ny_am"] == 1)]
    orb_oos      = oos[oos["is_orb"] == 1]
    ifvg_oos     = oos[oos["is_orb"] == 0]

    single_filters = {
        "long-only":         long_oos,
        "rank-1-only":       rank1_oos,
        "london+ny_am only": ldn_nyam_oos,
        "orb-only":          orb_oos,
        "ifvg-only":         ifvg_oos,
    }
    for name, sub in single_filters.items():
        print(f"  {name:25s} PF={pf(sub['pnl']):.3f}  (n={len(sub)}, {100*len(sub)/len(oos):.0f}%)")

    best_single_pf = max(pf(s["pnl"]) for s in single_filters.values()
                         if len(s) >= 0.3 * len(oos))  # min 30% volume
    best_single_name = max(
        ((n, pf(s["pnl"])) for n, s in single_filters.items() if len(s) >= 0.3 * len(oos)),
        key=lambda x: x[1]
    )[0]

    # GO/NO-GO
    print("\n--- GO/NO-GO evaluation ---")
    print(f"  Criterion 1: top-decile/bot-decile ratio = {ratio:.3f}  (need >= 1.30)")
    print(f"    Top-decile PF:  {top_d_pf:.3f}")
    print(f"    Bot-decile PF:  {bot_d_pf:.3f}")
    crit1 = ratio >= 1.30

    top_half_pf = pf(top_half["pnl"])
    vol_ok = len(top_half) >= 0.40 * len(oos)
    print(f"  Criterion 2: top-half PF ({top_half_pf:.3f}) >= best single filter PF ({best_single_pf:.3f} = {best_single_name})")
    print(f"    Volume check: {len(top_half)} >= {0.40*len(oos):.0f} (40%): {'PASS' if vol_ok else 'FAIL'}")
    crit2 = top_half_pf >= best_single_pf and vol_ok

    print(f"\n  Criterion 1 (decile ratio): {'PASS' if crit1 else 'FAIL'}")
    print(f"  Criterion 2 (beats best single + volume): {'PASS' if crit2 else 'FAIL'}")
    verdict = "GO -- proceed to Phase 2" if (crit1 and crit2) else "NO-GO -- Phase 1 rejected"
    print(f"\n  VERDICT: {verdict}")

    # Per-year OOS breakdown
    print("\n--- OOS per-year PF (composite top-half vs baseline) ---")
    print(f"  {'Year':>6} {'n_all':>6} {'PF_all':>8} {'n_top':>6} {'PF_top':>8} {'n_single':>9} {'best_single_pf':>14}")
    for yr in OOS_YEARS:
        yr_df   = oos[oos["year"] == yr]
        yr_top  = yr_df[yr_df["score"] >= med_score]
        yr_best = yr_df[yr_df["is_long"] == 1]  # long-only as representative
        print(f"  {yr:>6} {len(yr_df):>6} {pf(yr_df['pnl']):>8.3f} {len(yr_top):>6} {pf(yr_top['pnl']):>8.3f} {len(yr_best):>9} {pf(yr_best['pnl']):>14.3f}")

    return verdict


if __name__ == "__main__":
    main()
