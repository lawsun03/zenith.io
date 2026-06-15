"""
wk6-r2 inline Phase 1 probes.

Probe A: iFVG within-day direction-conflict second-signal gate
  - Group iFVG trades by trading day
  - Rank signals within each day by entry_ts
  - Classify rank-2+ signals as SAME or OPPOSITE to rank-1 direction
  - Compare PF for same vs opposite groups

Probe B: ORB pre-market high/low break gate
  - For each ORB trade, compute the pre-market (08:00-09:29 ET) high and low
    from the 5-min bar data for that date
  - Check if entry bar close > PM high (long) or < PM low (short)
  - Compare PF for PM-break vs within-PM groups

Probe C: iFVG pre-RTH zone creation filter
  - Compute gap_bars from mfe_mae data using the B79 equity_export regeneration
  - Since we lack gap_bars in the CSV, use entry_ts hour as a proxy for zone creation:
    entries in 03-07 ET are mostly London zones (post-RTH-open inversion of pre-RTH FVGs)
    vs entries in 10-15 ET which are fully intraday zones
  - Alternative: check entry_ts < 09:30 ET for PRE-RTH entries (already covered by 9ET block in B74)
  - Note: Without fvg_created_at in CSV, we can only proxy via entry time — limited value
"""

import pandas as pd
import numpy as np
from pathlib import Path

BASE = Path(__file__).parent.parent
CSV = BASE / "research" / "mfe_mae_deployed_combined_clean.csv"
BARS = BASE / "bars" / "bars_MNQ_dbv_2021_2026.csv"

df = pd.read_csv(CSV, parse_dates=["entry_ts", "exit_ts"])

# ── Probe A: iFVG direction-conflict within-day ──────────────────────────────
print("\n=== PROBE A: iFVG within-day direction-conflict second signals ===\n")

ifvg = df[df["engine_type"] == "ifvg"].copy()
ifvg["date"] = ifvg["entry_ts"].dt.date

# Sort by date then entry_ts to rank signals within each day
ifvg = ifvg.sort_values(["date", "entry_ts"]).reset_index(drop=True)
ifvg["rank"] = ifvg.groupby("date").cumcount() + 1

# Get rank-1 direction for each day
rank1 = ifvg[ifvg["rank"] == 1][["date", "side"]].rename(columns={"side": "rank1_side"})
ifvg = ifvg.merge(rank1, on="date", how="left")

# Classify rank-2+ signals
rank2plus = ifvg[ifvg["rank"] >= 2].copy()
rank2plus["conflict"] = rank2plus["side"] != rank2plus["rank1_side"]

n_total = len(rank2plus)
n_conflict = rank2plus["conflict"].sum()
n_continuation = n_total - n_conflict

print(f"Total rank-2+ iFVG signals: {n_total}")
print(f"  Continuation (same direction as rank-1): {n_continuation} ({100*n_continuation/n_total:.1f}%)")
print(f"  Conflict (opposite direction):            {n_conflict} ({100*n_conflict/n_total:.1f}%)")

def pf(group):
    wins = group[group["pnl_usd"] > 0]["pnl_usd"].sum()
    losses = abs(group[group["pnl_usd"] < 0]["pnl_usd"].sum())
    return wins / losses if losses > 0 else float("inf")

cont_group = rank2plus[~rank2plus["conflict"]]
conf_group = rank2plus[rank2plus["conflict"]]

pf_cont = pf(cont_group)
pf_conf = pf(conf_group)

print(f"\nContinuation (n={len(cont_group)}): PF={pf_cont:.3f}, net=${cont_group['pnl_usd'].sum():.0f}")
print(f"Conflict     (n={len(conf_group)}): PF={pf_conf:.3f}, net=${conf_group['pnl_usd'].sum():.0f}")
print(f"Ratio continuation/conflict: {pf_cont/pf_conf:.3f}")

# Per-year breakdown
print("\nPer-year breakdown:")
rank2plus["year"] = pd.to_datetime(rank2plus["entry_ts"]).dt.year
for yr in sorted(rank2plus["year"].unique()):
    y = rank2plus[rank2plus["year"] == yr]
    yc = y[~y["conflict"]]
    yf = y[y["conflict"]]
    pf_c = pf(yc) if len(yc) > 0 else float("nan")
    pf_f = pf(yf) if len(yf) > 0 else float("nan")
    ratio = pf_c / pf_f if pf_f > 0 else float("nan")
    print(f"  {yr}: continuation n={len(yc)} PF={pf_c:.3f} | conflict n={len(yf)} PF={pf_f:.3f} | ratio={ratio:.3f}")

# ── Probe B: ORB pre-market high/low break gate ───────────────────────────────
print("\n\n=== PROBE B: ORB pre-market (08:00-09:29 ET) break gate ===\n")

orb = df[df["engine_type"] == "orb"].copy()
print(f"Total ORB trades: {len(orb)}")

# Load bars - need ET timestamp
try:
    bars = pd.read_csv(BARS, parse_dates=["ts"])
    print(f"Bars loaded: {len(bars)} rows, columns: {list(bars.columns)}")
    # Localize bars to ET if not already
    # bars ts likely is in UTC or local; need to confirm
    # Check first few timestamps
    print("Sample bar timestamps:", bars["ts"].head(3).tolist())
except Exception as e:
    print(f"Error loading bars: {e}")
    bars = None

if bars is not None:
    # Convert bars ts to ET
    # If bars are in UTC, convert. If already ET, check.
    sample_ts = bars["ts"].iloc[0]
    print(f"Sample ts: {sample_ts}, dtype: {bars['ts'].dtype}")

    # Try to work with UTC bars (Databento is UTC)
    # ET = UTC - 5h (EST) or UTC - 4h (EDT)
    # For simplicity, use UTC and adjust: 08:00 ET = 13:00 UTC (EDT) or 13:00 UTC (EST)
    # Better to just check if ts is timezone-aware

    bars_ts = bars["ts"]
    if hasattr(bars_ts.iloc[0], 'tzinfo') and bars_ts.iloc[0].tzinfo is not None:
        # Already tz-aware - convert to ET
        bars["ts_et"] = bars_ts.dt.tz_convert("America/New_York")
    else:
        # Assume UTC
        bars["ts_et"] = pd.to_datetime(bars["ts"], utc=True).dt.tz_convert("America/New_York")

    bars["date"] = bars["ts_et"].dt.date
    bars["hour_et"] = bars["ts_et"].dt.hour
    bars["minute_et"] = bars["ts_et"].dt.minute
    bars["time_et"] = bars["ts_et"].dt.time

    # Pre-market: bars with 08:00 <= time < 09:30 ET
    # In terms of hour: hour 8 (08:00-08:55) AND hour 9 (09:00-09:25, i.e., minute < 30)
    from datetime import time as dtime
    pm_mask = (bars["ts_et"].dt.time >= dtime(8, 0)) & (bars["ts_et"].dt.time < dtime(9, 30))
    pm_bars = bars[pm_mask].copy()

    pm_summary = pm_bars.groupby("date").agg(pm_high=("high", "max"), pm_low=("low", "min")).reset_index()
    print(f"Pre-market bar dates computed: {len(pm_summary)}")

    # ORB signal bar: 09:30-09:44 ET (first 15-min close after RTH open)
    # entry_ts in the CSV should be the close of the signal bar
    # For ORB in close-mode, entry_ts is when the bar closes = 09:35 ET (first 5-min bar)
    # or 09:45 ET if OR uses 2-bar (second 5-min bar)

    orb["date"] = orb["entry_ts"].dt.date
    orb["entry_hour"] = orb["entry_ts"].dt.hour
    orb["entry_minute"] = orb["entry_ts"].dt.minute

    print("\nORB entry time distribution:")
    print(orb.groupby(["entry_hour", "entry_minute"]).size().to_string())

    # Match ORB trades with PM high/low
    # Need to handle timezone: entry_ts may be UTC or ET
    # Check by looking at the hour distribution
    print(f"\nORB entry hour distribution: {orb['entry_hour'].value_counts().to_dict()}")

    # If most entries are at hour 14 (UTC 14:xx = ET 09:xx-10:xx), it's UTC
    # If most entries are at hour 9-10, it's ET

    entry_hours = orb["entry_hour"].value_counts()
    if entry_hours.index[0] in [14, 15]:  # UTC
        print("ORB timestamps appear to be UTC - converting to ET")
        orb["entry_ts_et"] = pd.to_datetime(orb["entry_ts"], utc=True).dt.tz_convert("America/New_York")
        orb["date"] = orb["entry_ts_et"].dt.date
    else:
        print("ORB timestamps appear to be ET")
        orb["entry_ts_et"] = orb["entry_ts"]

    # Get entry bar close price from bars
    # The entry_ts should match a bar close timestamp in bars
    # entry_ts = bar open + 5min (close time of the bar)

    # Create a lookup of bar close prices by ts_et
    bars_lookup = bars.set_index("ts_et")["close"]

    # Match ORB entry_ts to bar close
    # Need to handle timezone matching
    if hasattr(orb["entry_ts_et"].iloc[0], 'tzinfo') and orb["entry_ts_et"].iloc[0].tzinfo is not None:
        # Already tz-aware
        orb_prices = []
        matched = 0
        for _, row in orb.iterrows():
            try:
                price = bars_lookup.loc[row["entry_ts_et"]]
                orb_prices.append(price)
                matched += 1
            except:
                orb_prices.append(None)
        orb["entry_price"] = orb_prices
        print(f"Matched {matched}/{len(orb)} ORB entries to bar close prices")

    # If too few matches, try without tz
    if orb["entry_price"].isna().sum() > len(orb) * 0.5:
        print("Trying without timezone...")
        bars_no_tz = bars.copy()
        bars_no_tz["ts_naive"] = bars_no_tz["ts_et"].dt.tz_localize(None)
        bars_lookup2 = bars_no_tz.set_index("ts_naive")["close"]
        orb["entry_ts_naive"] = pd.to_datetime(orb["entry_ts_et"]).dt.tz_localize(None)
        orb["entry_price"] = orb["entry_ts_naive"].map(bars_lookup2)
        print(f"Matched {orb['entry_price'].notna().sum()}/{len(orb)} ORB entries to bar close prices")

    # Merge with PM high/low
    orb = orb.merge(pm_summary, on="date", how="left")
    n_pm_match = orb[["pm_high", "pm_low"]].notna().all(axis=1).sum()
    print(f"ORB trades with PM data: {n_pm_match}/{len(orb)}")

    # Classify PM break
    has_data = orb["entry_price"].notna() & orb["pm_high"].notna() & orb["pm_low"].notna()
    orb_valid = orb[has_data].copy()
    print(f"ORB trades with full data (entry_price + PM): {len(orb_valid)}")

    if len(orb_valid) > 0:
        # Long: PM break = entry_price > pm_high
        # Short: PM break = entry_price < pm_low
        orb_valid["pm_break"] = np.where(
            orb_valid["side"] == "long",
            orb_valid["entry_price"] > orb_valid["pm_high"],
            orb_valid["entry_price"] < orb_valid["pm_low"]
        )

        n_break = orb_valid["pm_break"].sum()
        n_within = (~orb_valid["pm_break"]).sum()
        print(f"\nPM break signals: {n_break} ({100*n_break/len(orb_valid):.1f}%)")
        print(f"Within PM signals: {n_within} ({100*n_within/len(orb_valid):.1f}%)")

        break_group = orb_valid[orb_valid["pm_break"]]
        within_group = orb_valid[~orb_valid["pm_break"]]

        pf_break = pf(break_group)
        pf_within = pf(within_group)

        print(f"\nPM break  (n={len(break_group)}): PF={pf_break:.3f}, net=${break_group['pnl_usd'].sum():.0f}")
        print(f"Within PM (n={len(within_group)}): PF={pf_within:.3f}, net=${within_group['pnl_usd'].sum():.0f}")
        print(f"Ratio PM-break/within-PM: {pf_break/pf_within:.3f}")

        # Per-year
        print("\nPer-year breakdown:")
        orb_valid["year"] = pd.to_datetime(orb_valid["entry_ts"]).dt.year
        for yr in sorted(orb_valid["year"].unique()):
            y = orb_valid[orb_valid["year"] == yr]
            yb = y[y["pm_break"]]
            yw = y[~y["pm_break"]]
            pf_b = pf(yb) if len(yb) > 0 else float("nan")
            pf_w = pf(yw) if len(yw) > 0 else float("nan")
            ratio = pf_b / pf_w if pf_w > 0 else float("nan")
            print(f"  {yr}: PM-break n={len(yb)} PF={pf_b:.3f} | within-PM n={len(yw)} PF={pf_w:.3f} | ratio={ratio:.3f}")
    else:
        print("No valid data for PM break analysis")

print("\n\n=== PROBE C: iFVG entry-time proxy for zone creation ===\n")
# Without fvg_created_at, we can't directly test pre-RTH zone creation.
# But we know from B74/B79/Lesson 142 that:
# - 9ET entries are structural failures (RTH open dynamics)
# - Freshness (gap_bars) is directionally predictive but below 1.30 threshold
#
# We CAN check: entries 9:30 ET onward (first RTH bars) vs earlier entries
# London session entries (03-07 ET) are pre-RTH zone creations AND intra-London inversions
# This confounds zone creation time with entry time.
#
# Without gap_bars column, a meaningful pre-RTH zone creation filter requires re-running equity_export
# with the new displacement_ts/fvg_created_at column from B79.
#
# Report: flag this as requiring a new equity_export run for a dedicated probe.

ifvg["entry_hour"] = pd.to_datetime(ifvg["entry_ts"]).dt.hour
print("iFVG entry hour distribution:")
hr_stats = []
for hr in sorted(ifvg["entry_hour"].unique()):
    g = ifvg[ifvg["entry_hour"] == hr]
    p = pf(g)
    hr_stats.append((hr, len(g), p, g['pnl_usd'].sum()))

for hr, n, p, net in hr_stats:
    print(f"  Hour {hr:02d}ET: n={n:4d}, PF={p:.3f}, net=${net:+,.0f}")

# Check: is there a clean pre-RTH vs post-RTH split for iFVG entries?
pre_rth = ifvg[ifvg["entry_hour"] < 9]  # before RTH (hour 0-8)
rth_open = ifvg[(ifvg["entry_hour"] == 9)]  # 9ET (pre-open to open)
post_rth = ifvg[ifvg["entry_hour"] >= 10]  # post-open (10ET+)

print(f"\nPre-RTH entries (00-08 ET): n={len(pre_rth)}, PF={pf(pre_rth):.3f}")
print(f"RTH-open entries (09 ET):   n={len(rth_open)}, PF={pf(rth_open):.3f}")
print(f"Post-RTH entries (10+ ET):  n={len(post_rth)}, PF={pf(post_rth):.3f}")
print(f"\nNote: fvg_created_at column not in CSV - pre-RTH ZONE creation filter requires equity_export re-run")
