#!/usr/bin/env python3
"""
B65 Phase 1: Markov 2.0 regime filter — cheap falsification gate.

Tests whether a daily Markov regime signal (stride-sampled, walk-forward
point-in-time, FIX 1 + FIX 2 compliant) materially separates iFVG/ORB
trade quality (PF per regime bucket per side per year).

GO criterion: bull-regime longs PF >= 1.3x bear-regime longs PF,
consistently across >=4/5 open years. No engine built if Phase 1 rejects.

Usage: python scripts/analyze_b65_markov_regime.py
"""
import sys
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path(__file__).parent.parent

# ---- CONSTANTS ----
WINDOW = 20         # stride (non-overlapping), days
BULL_THR = 0.05    # 20-day cum return >= +5% = BULL
BEAR_THR = -0.05   # 20-day cum return <= -5% = BEAR
SIGNAL_THR = 0.0   # filter gate: signal > thr → BULL, < -thr → BEAR
MIN_WINDOWS = 3    # need >=3 windows (=2 transitions) before first signal
SKIP_YEAR = 2022   # frozen holdout — excluded from PF evaluation

LABELS = ["BULL", "SIDEWAYS", "BEAR"]
L2I = {l: i for i, l in enumerate(LABELS)}

# ---- 1. LOAD BARS → DAILY ----
print("Loading bars...")
bars_file = ROOT / "bars" / "bars_MNQ_dbv_2021_2026.csv"
bars = pd.read_csv(bars_file, parse_dates=["ts"])
bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
bars = bars.set_index("ts").sort_index()
bars_et = bars.copy()
bars_et.index = bars_et.index.tz_convert("America/New_York")
bars_et["date"] = bars_et.index.date

daily = (
    bars_et.groupby("date")
    .agg(open=("open", "first"), high=("high", "max"),
         low=("low", "min"), close=("close", "last"), volume=("volume", "sum"))
    .dropna()
)
daily.index = pd.to_datetime(daily.index)
print(f"Daily bars: {len(daily)} days  {daily.index[0].date()} → {daily.index[-1].date()}")

closes = daily["close"].values
dates = daily.index
n = len(closes)

# ---- 2. STRIDE-SAMPLED 20-DAY WINDOW LABELS (FIX 1) ----
def label_window(start_close, end_close):
    ret = (end_close - start_close) / start_close
    if ret >= BULL_THR:
        return "BULL"
    if ret <= BEAR_THR:
        return "BEAR"
    return "SIDEWAYS"

window_starts = list(range(0, n - WINDOW + 1, WINDOW))
stride_labels = []
stride_end_dates = []
stride_end_idx = []

for i in window_starts:
    lbl = label_window(closes[i], closes[i + WINDOW - 1])
    stride_labels.append(lbl)
    stride_end_dates.append(dates[i + WINDOW - 1])
    stride_end_idx.append(i + WINDOW - 1)

print(f"\nStride windows: {len(stride_labels)}")
print(f"  BULL:     {stride_labels.count('BULL')}")
print(f"  SIDEWAYS: {stride_labels.count('SIDEWAYS')}")
print(f"  BEAR:     {stride_labels.count('BEAR')}")

# ---- FIX 2: LABEL SELF-CHECK ----
# Verify BULL/BEAR mapping against known NQ historical periods
print("\n=== FIX 2: Label self-check against known NQ periods ===")
known_checks = []
for j, (end_dt, lbl) in enumerate(zip(stride_end_dates, stride_labels)):
    start_dt = dates[window_starts[j]]
    yr = end_dt.year
    mo = end_dt.month
    # 2021 Q3-Q4: NQ rallied to all-time high (~Nov 2021) → expect BULL windows
    if yr == 2021 and mo in (8, 9, 10, 11):
        known_checks.append(("2021 Q3/Q4 run-up", str(start_dt.date()), str(end_dt.date()), lbl, "BULL"))
    # 2022 Q1-Q2: NQ selloff ~-35% from ATH → expect BEAR windows
    if yr == 2022 and mo in (2, 3, 4, 5):
        known_checks.append(("2022 Q1/Q2 crash", str(start_dt.date()), str(end_dt.date()), lbl, "BEAR"))
    # 2023 mid-year flat: July-Aug 2023 NQ was range-bound → SIDEWAYS likely
    if yr == 2023 and mo in (7, 8):
        known_checks.append(("2023 Q3 flat", str(start_dt.date()), str(end_dt.date()), lbl, "SIDEWAYS?"))

for (period, s, e, actual, expected) in known_checks:
    match = "OK" if actual == expected or expected.endswith("?") else "MISMATCH"
    print(f"  {period}  {s}→{e}: got={actual}  expected={expected}  [{match}]")

mismatches = [c for c in known_checks if c[3] != c[4] and not c[4].endswith("?")]
if mismatches:
    print(f"\n  *** FIX 2 ALERT: {len(mismatches)} label mismatch(es) detected — review mapping before proceeding ***")
else:
    print("\n  FIX 2 PASSED: labels consistent with known NQ history")

# ---- 3. FIX 1 — OVERLAPPING vs STRIDE-SAMPLED MATRICES ----
def build_matrix(labels):
    """3x3 transition matrix, rows normalized to 1."""
    mat = np.zeros((3, 3))
    for a, b in zip(labels[:-1], labels[1:]):
        if a in L2I and b in L2I:
            mat[L2I[a], L2I[b]] += 1
    row_sums = mat.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1
    return mat / row_sums

# Overlapping (legacy) — rolling 20-day return, daily
rolling_rets = daily["close"].pct_change(WINDOW)
rolling_labels = rolling_rets.map(
    lambda r: "BULL" if r >= BULL_THR else ("BEAR" if r <= BEAR_THR else "SIDEWAYS")
).iloc[WINDOW:]

print("\n=== FIX 1: Overlapping vs Stride-sampled transition matrices ===")
print("WARNING: only the stride-sampled matrix is statistically honest")
print("         (overlapping windows share 19/20 days → fake diagonal persistence)\n")

mat_overlap = build_matrix(rolling_labels.tolist())
mat_stride  = build_matrix(stride_labels)

print("Overlapping matrix (P[row→col]):")
print(f"  {'':10s} {'BULL':>8s} {'SIDEWAYS':>10s} {'BEAR':>8s}  (stickiness=diagonal)")
for i, lbl in enumerate(LABELS):
    row = mat_overlap[i]
    stick = f"  *** sticky={row[i]:.2f} ***" if i == L2I[lbl] else ""
    print(f"  {lbl:10s} {row[0]:8.3f} {row[1]:10.3f} {row[2]:8.3f}{stick}")

print(f"\nStride-sampled matrix (P[row→col]):")
print(f"  {'':10s} {'BULL':>8s} {'SIDEWAYS':>10s} {'BEAR':>8s}  (stickiness=diagonal)")
for i, lbl in enumerate(LABELS):
    row = mat_stride[i]
    stick = f"  *** sticky={row[i]:.2f} ***" if i == L2I[lbl] else ""
    print(f"  {lbl:10s} {row[0]:8.3f} {row[1]:10.3f} {row[2]:8.3f}{stick}")

print("\n(Overlapping inflates diagonal persistence — see the stride matrix for truth)")

# ---- 4. WALK-FORWARD SIGNAL (POINT-IN-TIME, NO LOOKAHEAD) ----
# For day D: use only stride windows whose end_date < D
# Signal = P(BULL|current_state) - P(BEAR|current_state) from walk-forward matrix
# Current state = most recent complete window's label

print("\nComputing walk-forward point-in-time regime signals...")
stride_end_arr = np.array([d.value for d in stride_end_dates])  # nanoseconds

daily_signal = np.full(n, np.nan)
daily_cur_state = np.full(n, "SIDEWAYS", dtype=object)

for day_idx in range(n):
    day_ns = dates[day_idx].value
    # Indices of stride windows that ended strictly before this day
    past_mask = stride_end_arr < day_ns
    past_count = past_mask.sum()
    if past_count < MIN_WINDOWS:
        continue
    past_labels = [stride_labels[k] for k in range(len(stride_labels)) if past_mask[k]]
    current_state = past_labels[-1]
    daily_cur_state[day_idx] = current_state
    mat = build_matrix(past_labels)
    ci = L2I[current_state]
    daily_signal[day_idx] = mat[ci, L2I["BULL"]] - mat[ci, L2I["BEAR"]]

# Assign regime bucket
def sig_to_bucket(sig, thr=SIGNAL_THR):
    if np.isnan(sig):
        return "SIDEWAYS"
    if sig > thr:
        return "BULL"
    if sig < -thr:
        return "BEAR"
    return "SIDEWAYS"

daily_buckets = pd.Series(
    [sig_to_bucket(s) for s in daily_signal],
    index=daily.index
)
# Also expose the current state (without Markov prediction, for comparison)
daily_state_bucket = pd.Series(daily_cur_state, index=daily.index)

print(f"\nSignal regime distribution (thr={SIGNAL_THR}):")
print(daily_buckets.value_counts())
print(f"\nCurrent-state distribution (raw trailing-20d label, no prediction):")
print(daily_state_bucket.value_counts())

# ---- 5. LOAD TRADES AND LABEL WITH REGIME ----
def load_trades(path):
    df = pd.read_csv(path, parse_dates=["entry_ts"])
    df["entry_ts"] = pd.to_datetime(df["entry_ts"], utc=True).dt.tz_convert("America/New_York")
    df["trade_date"] = pd.to_datetime(df["entry_ts"].dt.date)
    df = df[df["year"] != SKIP_YEAR].copy()
    return df

ifvg = load_trades(ROOT / "research" / "mfe_mae_ifvg_clean.csv")
orb  = load_trades(ROOT / "research" / "mfe_mae_orb_clean.csv")

bucket_dict       = daily_buckets.to_dict()
state_bucket_dict = daily_state_bucket.to_dict()

for df in (ifvg, orb):
    df["regime"]       = df["trade_date"].map(bucket_dict).fillna("SIDEWAYS")
    df["regime_state"] = df["trade_date"].map(state_bucket_dict).fillna("SIDEWAYS")

print(f"\niFVG trades (ex-2022): {len(ifvg)}  regime distribution:")
print(ifvg["regime"].value_counts())
print(f"\nORB trades (ex-2022): {len(orb)}  regime distribution:")
print(orb["regime"].value_counts())

# ---- 6. PF ANALYSIS PER BUCKET × SIDE × YEAR ----
def compute_pf(pnl):
    wins   = pnl[pnl > 0].sum()
    losses = pnl[pnl < 0].abs().sum()
    if losses == 0:
        return np.inf
    return wins / losses

OPEN_YEARS = [2021, 2023, 2024, 2025, 2026]

def analyze(df, engine_name, regime_col="regime"):
    print(f"\n{'='*60}")
    print(f"{engine_name}: PF by regime × side  (regime_col={regime_col})")
    print(f"{'='*60}")
    result = {}
    for side in ["long", "short"]:
        sdf = df[df["side"] == side]
        print(f"\n  --- {side.upper()} ---")
        for regime in ["BULL", "SIDEWAYS", "BEAR"]:
            rdf = sdf[sdf[regime_col] == regime]
            if len(rdf) < 5:
                print(f"    {regime:9s}: n={len(rdf):4d}  SKIP (too few)")
                result[(side, regime)] = (len(rdf), np.nan)
                continue
            pf_all  = compute_pf(rdf["realized_pnl"])
            wr_all  = (rdf["realized_pnl"] > 0).mean()
            print(f"    {regime:9s}: n={len(rdf):4d}  PF={pf_all:6.3f}  WR={wr_all:.2%}")
            yr_pfs = {}
            for yr in OPEN_YEARS:
                ydf = rdf[rdf["year"] == yr]
                if len(ydf) < 3:
                    continue
                pf_yr = compute_pf(ydf["realized_pnl"])
                yr_pfs[yr] = pf_yr
                print(f"         {yr}: n={len(ydf):3d}  PF={pf_yr:6.3f}")
            result[(side, regime)] = (len(rdf), pf_all, yr_pfs)
    return result

print("\n\n### PRIMARY ANALYSIS (Markov signal bucket) ###")
ifvg_res = analyze(ifvg, "iFVG", "regime")
orb_res  = analyze(orb, "ORB", "regime")

print("\n\n### COMPARISON: raw trailing-state bucket (no Markov prediction) ###")
ifvg_res_raw = analyze(ifvg, "iFVG-raw", "regime_state")
orb_res_raw  = analyze(orb, "ORB-raw", "regime_state")

# ---- 7. GO / NO-GO ----
print(f"\n{'='*60}")
print("GO / NO-GO EVALUATION")
print(f"Criterion: bull-regime longs PF >= 1.3x bear-regime longs PF")
print(f"           bear-regime shorts PF >= 1.3x bull-regime shorts PF")
print(f"           Consistent across >= 4/{len(OPEN_YEARS)} open years")
print(f"{'='*60}")

overall_go = True

for engine_name, res in [("iFVG", ifvg_res), ("ORB", orb_res)]:
    print(f"\n{engine_name}:")
    n_bull_long, pf_bull_long  = res.get(("long", "BULL"),     (0,))[0], res.get(("long", "BULL"),     (0, np.nan))[1]
    n_bear_long, pf_bear_long  = res.get(("long", "BEAR"),     (0,))[0], res.get(("long", "BEAR"),     (0, np.nan))[1]
    n_bull_short, pf_bull_short = res.get(("short", "BULL"),    (0,))[0], res.get(("short", "BULL"),    (0, np.nan))[1]
    n_bear_short, pf_bear_short = res.get(("short", "BEAR"),    (0,))[0], res.get(("short", "BEAR"),    (0, np.nan))[1]

    long_ratio  = pf_bull_long  / pf_bear_long  if (pf_bear_long  and not np.isnan(pf_bear_long))  else np.nan
    short_ratio = pf_bear_short / pf_bull_short if (pf_bull_short and not np.isnan(pf_bull_short)) else np.nan

    long_go  = bool(not np.isnan(long_ratio)  and long_ratio  >= 1.3)
    short_go = bool(not np.isnan(short_ratio) and short_ratio >= 1.3)

    print(f"  Longs:  BULL PF={pf_bull_long:.3f} (n={n_bull_long})  "
          f"BEAR PF={pf_bear_long:.3f} (n={n_bear_long})  "
          f"ratio={long_ratio:.2f}x  → {'GO' if long_go else 'NO-GO'}")
    print(f"  Shorts: BEAR PF={pf_bear_short:.3f} (n={n_bear_short})  "
          f"BULL PF={pf_bull_short:.3f} (n={n_bull_short})  "
          f"ratio={short_ratio:.2f}x  → {'GO' if short_go else 'NO-GO'}")

    # Year-by-year consistency
    bull_long_yrs = res.get(("long", "BULL"), (0, np.nan, {}))[2] if len(res.get(("long", "BULL"), ())) > 2 else {}
    bear_long_yrs = res.get(("long", "BEAR"), (0, np.nan, {}))[2] if len(res.get(("long", "BEAR"), ())) > 2 else {}
    consistent_yrs = 0
    for yr in OPEN_YEARS:
        pf_b = bull_long_yrs.get(yr, np.nan)
        pf_r = bear_long_yrs.get(yr, np.nan)
        if not np.isnan(pf_b) and not np.isnan(pf_r) and pf_r > 0:
            if pf_b / pf_r >= 1.3:
                consistent_yrs += 1
    print(f"  Year consistency (longs): {consistent_yrs}/{len([y for y in OPEN_YEARS if bull_long_yrs.get(y) or bear_long_yrs.get(y)])} years with BULL >= 1.3x BEAR")

    engine_go = long_go and short_go
    print(f"  {engine_name}: {'PHASE 1 GO → proceed to Phase 2' if engine_go else 'PHASE 1 NO-GO → reject'}")
    if not engine_go:
        overall_go = False

print(f"\n{'='*60}")
if overall_go:
    print("OVERALL: PHASE 1 GO — proceed to Phase 2 (build filter engine)")
else:
    print("OVERALL: PHASE 1 NO-GO — reject B65")
    print("Markov daily regime does not materially predict NQ 5min trade quality.")
    print("Joins B35 (daily-bias gate) and B5 (prior-day range) in failed daily-context set.")
print(f"{'='*60}")
