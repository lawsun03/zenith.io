"""Research mining for wk2-r4 session — new hypothesis generation."""
import pandas as pd
import pytz

et = pytz.timezone("America/New_York")

# --- iFVG data ---
df_ifvg = pd.read_csv("research/mfe_mae_ifvg_clean.csv")
df_ifvg["entry_dt"] = pd.to_datetime(df_ifvg["entry_ts"], utc=True)
df_ifvg["entry_et"] = df_ifvg["entry_dt"].dt.tz_convert(et)
df_ifvg["hour"] = df_ifvg["entry_et"].dt.hour
df_ifvg["minute"] = df_ifvg["entry_et"].dt.minute
df_ifvg["dow"] = df_ifvg["entry_et"].dt.day_name()
df_ifvg = df_ifvg[df_ifvg["year"] != 2022]

print("=" * 60)
print("iFVG HOLD TIME (5y excl 2022)")
print("=" * 60)
winners = df_ifvg[df_ifvg.realized_pnl > 0]
losers = df_ifvg[df_ifvg.realized_pnl < 0]
print(f"Winners n={len(winners)}: mean={winners.hold_seconds.mean()/60:.1f}m  "
      f"p25={winners.hold_seconds.quantile(0.25)/60:.1f}m  "
      f"p50={winners.hold_seconds.median()/60:.1f}m  "
      f"p75={winners.hold_seconds.quantile(0.75)/60:.1f}m  "
      f"p90={winners.hold_seconds.quantile(0.90)/60:.1f}m")
print(f"Losers  n={len(losers)}: mean={losers.hold_seconds.mean()/60:.1f}m  "
      f"p25={losers.hold_seconds.quantile(0.25)/60:.1f}m  "
      f"p50={losers.hold_seconds.median()/60:.1f}m  "
      f"p75={losers.hold_seconds.quantile(0.75)/60:.1f}m  "
      f"p90={losers.hold_seconds.quantile(0.90)/60:.1f}m")
print()

print("=" * 60)
print("iFVG PER-HOUR ALL-SIDES (5y excl 2022)")
print("=" * 60)
for hr in sorted(df_ifvg.hour.unique()):
    sub = df_ifvg[df_ifvg.hour == hr]
    if len(sub) < 10:
        continue
    gp = sub[sub.realized_pnl > 0].realized_pnl.sum()
    gn = abs(sub[sub.realized_pnl < 0].realized_pnl.sum())
    pf = gp / gn if gn > 0 else float("inf")
    wr = len(sub[sub.realized_pnl > 0]) / len(sub)
    net = sub.realized_pnl.sum()
    print(f"  {hr:02d}:xx  n={len(sub):4d}  WR={wr:.1%}  PF={pf:.3f}  net=${net:8,.0f}")
print()

print("=" * 60)
print("iFVG LUNCH-DOLDRUMS BLOCK (11:00-13:00 ET) — hypothesis B44")
print("=" * 60)
lunch = df_ifvg[(df_ifvg.hour >= 11) & (df_ifvg.hour < 13)]
non_lunch = df_ifvg[(df_ifvg.hour < 11) | (df_ifvg.hour >= 13)]
gp = lunch[lunch.realized_pnl > 0].realized_pnl.sum()
gn = abs(lunch[lunch.realized_pnl < 0].realized_pnl.sum())
print(f"  Lunch (11-13 ET): n={len(lunch)},  PF={gp/gn:.3f},  net=${lunch.realized_pnl.sum():,.0f}")
gp = non_lunch[non_lunch.realized_pnl > 0].realized_pnl.sum()
gn = abs(non_lunch[non_lunch.realized_pnl < 0].realized_pnl.sum())
print(f"  Non-lunch:        n={len(non_lunch)}, PF={gp/gn:.3f},  net=${non_lunch.realized_pnl.sum():,.0f}")

# Also check LONG-only lunch signal (B19 config)
df_long = df_ifvg[df_ifvg.side == "long"]
lunch_long = df_long[(df_long.hour >= 11) & (df_long.hour < 13)]
non_lunch_long = df_long[(df_long.hour < 11) | (df_long.hour >= 13)]
gp = lunch_long[lunch_long.realized_pnl > 0].realized_pnl.sum()
gn = abs(lunch_long[lunch_long.realized_pnl < 0].realized_pnl.sum())
print(f"  Lunch LONG only:  n={len(lunch_long)},  PF={gp/gn:.3f},  net=${lunch_long.realized_pnl.sum():,.0f}")
print()

print("=" * 60)
print("iFVG HOLD-TIME BUCKET PERFORMANCE")
print("=" * 60)
df_ifvg["hold_mins"] = df_ifvg.hold_seconds / 60
bins_hold = [0, 30, 60, 120, 240, 999]
labels_hold = ["0-30m", "30-60m", "1-2h", "2-4h", "4h+"]
df_ifvg["hold_bucket"] = pd.cut(df_ifvg.hold_mins, bins=bins_hold, labels=labels_hold)
for lbl in labels_hold:
    sub = df_ifvg[df_ifvg.hold_bucket == lbl]
    if len(sub) == 0:
        continue
    gp = sub[sub.realized_pnl > 0].realized_pnl.sum()
    gn = abs(sub[sub.realized_pnl < 0].realized_pnl.sum())
    pf = gp / gn if gn > 0 else float("inf")
    wr = len(sub[sub.realized_pnl > 0]) / len(sub)
    net = sub.realized_pnl.sum()
    print(f"  {lbl:8s}: n={len(sub):4d}  WR={wr:.1%}  PF={pf:.3f}  net=${net:8,.0f}")
print()

print("=" * 60)
print("iFVG MFE >= 1.5R analysis (trades that went deep before resolving)")
print("=" * 60)
hi_mfe = df_ifvg[df_ifvg.r_mfe >= 1.5]
hi_mfe_wins = hi_mfe[hi_mfe.realized_pnl > 0]
hi_mfe_loss = hi_mfe[hi_mfe.realized_pnl < 0]
print(f"MFE>=1.5R: n={len(hi_mfe)}/{len(df_ifvg)} ({len(hi_mfe)/len(df_ifvg):.1%})")
print(f"  Winners: {len(hi_mfe_wins)} ({len(hi_mfe_wins)/len(hi_mfe):.1%})  avg hold={hi_mfe_wins.hold_seconds.mean()/60:.1f}m")
print(f"  Losers:  {len(hi_mfe_loss)} ({len(hi_mfe_loss)/len(hi_mfe):.1%})  avg hold={hi_mfe_loss.hold_seconds.mean()/60:.1f}m")
print()

print("=" * 60)
print("iFVG MFE PERCENTILE DISTRIBUTION")
print("=" * 60)
for p in [10, 25, 50, 75, 90, 95, 99]:
    v = df_ifvg.r_mfe.quantile(p / 100)
    print(f"  p{p:2d}: {v:.3f}R")
print()

# --- ORB data ---
df_orb = pd.read_csv("research/mfe_mae_orb_clean.csv")
df_orb["entry_dt"] = pd.to_datetime(df_orb["entry_ts"], utc=True)
df_orb["entry_et"] = df_orb["entry_dt"].dt.tz_convert(et)
df_orb["hour"] = df_orb["entry_et"].dt.hour
df_orb["minute"] = df_orb["entry_et"].dt.minute
df_orb["dow"] = df_orb["entry_et"].dt.day_name()
df_orb["mins_after_open"] = (df_orb["hour"] - 9) * 60 + df_orb["minute"] - 30
df_orb = df_orb[df_orb["year"] != 2022]

print("=" * 60)
print("ORB HOLD TIME ANALYSIS")
print("=" * 60)
orb_wins = df_orb[df_orb.realized_pnl > 0]
orb_loss = df_orb[df_orb.realized_pnl < 0]
print(f"Winners n={len(orb_wins)}: mean={orb_wins.hold_seconds.mean()/60:.1f}m  "
      f"p50={orb_wins.hold_seconds.median()/60:.1f}m  "
      f"p75={orb_wins.hold_seconds.quantile(0.75)/60:.1f}m  "
      f"p90={orb_wins.hold_seconds.quantile(0.90)/60:.1f}m")
print(f"Losers  n={len(orb_loss)}: mean={orb_loss.hold_seconds.mean()/60:.1f}m  "
      f"p50={orb_loss.hold_seconds.median()/60:.1f}m  "
      f"p75={orb_loss.hold_seconds.quantile(0.75)/60:.1f}m  "
      f"p90={orb_loss.hold_seconds.quantile(0.90)/60:.1f}m")
print()

print("=" * 60)
print("ORB HOLD-TIME BUCKET PERFORMANCE")
print("=" * 60)
df_orb["hold_mins"] = df_orb.hold_seconds / 60
bins_hold = [0, 30, 60, 120, 240, 999]
labels_hold = ["0-30m", "30-60m", "1-2h", "2-4h", "4h+"]
df_orb["hold_bucket"] = pd.cut(df_orb.hold_mins, bins=bins_hold, labels=labels_hold)
for lbl in labels_hold:
    sub = df_orb[df_orb.hold_bucket == lbl]
    if len(sub) == 0:
        continue
    gp = sub[sub.realized_pnl > 0].realized_pnl.sum()
    gn = abs(sub[sub.realized_pnl < 0].realized_pnl.sum())
    pf = gp / gn if gn > 0 else float("inf")
    wr = len(sub[sub.realized_pnl > 0]) / len(sub)
    net = sub.realized_pnl.sum()
    print(f"  {lbl:8s}: n={len(sub):4d}  WR={wr:.1%}  PF={pf:.3f}  net=${net:8,.0f}")
print()

print("=" * 60)
print("ORB SIDE × HOLD-TIME (identifying ORB flattens vs targets)")
print("=" * 60)
for side in ["long", "short"]:
    sub_s = df_orb[df_orb.side == side]
    print(f"  {side.upper()}:")
    for lbl in labels_hold:
        sub = sub_s[sub_s.hold_bucket == lbl]
        if len(sub) < 5:
            continue
        gp = sub[sub.realized_pnl > 0].realized_pnl.sum()
        gn = abs(sub[sub.realized_pnl < 0].realized_pnl.sum())
        pf = gp / gn if gn > 0 else float("inf")
        wr = len(sub[sub.realized_pnl > 0]) / len(sub)
        net = sub.realized_pnl.sum()
        print(f"    {lbl:8s}: n={len(sub):3d}  WR={wr:.1%}  PF={pf:.3f}  net=${net:8,.0f}")
print()

print("=" * 60)
print("ORB: per-year signal count (checking 2021 ORB availability)")
print("=" * 60)
for yr in sorted(df_orb.year.unique()):
    sub = df_orb[df_orb.year == yr]
    print(f"  {yr}: n={len(sub)}, WR={len(sub[sub.realized_pnl>0])/len(sub):.1%}")
print()

print("=" * 60)
print("iFVG: per-year, per-hour — checking if noon drag is consistent across years")
print("=" * 60)
for yr in sorted(df_ifvg.year.unique()):
    yr_sub = df_ifvg[df_ifvg.year == yr]
    lunch_sub = yr_sub[(yr_sub.hour >= 11) & (yr_sub.hour < 13)]
    if len(lunch_sub) == 0:
        continue
    gp = lunch_sub[lunch_sub.realized_pnl > 0].realized_pnl.sum()
    gn = abs(lunch_sub[lunch_sub.realized_pnl < 0].realized_pnl.sum())
    pf = gp / gn if gn > 0 else float("inf")
    non_sub = yr_sub[(yr_sub.hour < 11) | (yr_sub.hour >= 13)]
    gp2 = non_sub[non_sub.realized_pnl > 0].realized_pnl.sum()
    gn2 = abs(non_sub[non_sub.realized_pnl < 0].realized_pnl.sum())
    pf2 = gp2 / gn2 if gn2 > 0 else float("inf")
    print(f"  {yr}: lunch n={len(lunch_sub)} PF={pf:.3f}  |  non-lunch n={len(non_sub)} PF={pf2:.3f}")
print()

print("=" * 60)
print("iFVG: early-close day check (16:00 ET flatten vs 16:15 ET flatten)")
print("=" * 60)
df_ifvg["exit_dt"] = pd.to_datetime(df_ifvg["exit_ts"], utc=True)
df_ifvg["exit_et"] = df_ifvg["exit_dt"].dt.tz_convert(et)
df_ifvg["exit_hour"] = df_ifvg["exit_et"].dt.hour
df_ifvg["exit_minute"] = df_ifvg["exit_et"].dt.minute
# EOD flattens: exit at 16:00 or 16:01 ET
eod_flat = df_ifvg[(df_ifvg.exit_hour == 16) & (df_ifvg.exit_minute <= 5)]
not_eod = df_ifvg[(df_ifvg.exit_hour != 16) | (df_ifvg.exit_minute > 5)]
gp = eod_flat[eod_flat.realized_pnl > 0].realized_pnl.sum()
gn = abs(eod_flat[eod_flat.realized_pnl < 0].realized_pnl.sum())
pf = gp / gn if gn > 0 else float("inf")
print(f"  iFVG EOD flattens (exit 16:00-16:05 ET): n={len(eod_flat)}, WR={len(eod_flat[eod_flat.realized_pnl>0])/len(eod_flat):.1%}, PF={pf:.3f}")
gp2 = not_eod[not_eod.realized_pnl > 0].realized_pnl.sum()
gn2 = abs(not_eod[not_eod.realized_pnl < 0].realized_pnl.sum())
pf2 = gp2 / gn2 if gn2 > 0 else float("inf")
print(f"  iFVG non-EOD exits:                      n={len(not_eod)}, WR={len(not_eod[not_eod.realized_pnl>0])/len(not_eod):.1%}, PF={pf2:.3f}")
print()

print("=" * 60)
print("ORB: EOD vs non-EOD exits (confirming 33.6% flatten finding)")
print("=" * 60)
df_orb["exit_dt"] = pd.to_datetime(df_orb["exit_ts"], utc=True)
df_orb["exit_et"] = df_orb["exit_dt"].dt.tz_convert(et)
df_orb["exit_hour"] = df_orb["exit_et"].dt.hour
df_orb["exit_minute"] = df_orb["exit_et"].dt.minute
eod_orb = df_orb[(df_orb.exit_hour == 16) & (df_orb.exit_minute <= 5)]
not_eod_orb = df_orb[(df_orb.exit_hour != 16) | (df_orb.exit_minute > 5)]
gp = eod_orb[eod_orb.realized_pnl > 0].realized_pnl.sum()
gn = abs(eod_orb[eod_orb.realized_pnl < 0].realized_pnl.sum())
pf = gp / gn if gn > 0 else float("inf")
wr = len(eod_orb[eod_orb.realized_pnl > 0]) / len(eod_orb)
print(f"  ORB EOD flattens: n={len(eod_orb)} ({len(eod_orb)/len(df_orb):.1%}), WR={wr:.1%}, PF={pf:.3f}")
gp2 = not_eod_orb[not_eod_orb.realized_pnl > 0].realized_pnl.sum()
gn2 = abs(not_eod_orb[not_eod_orb.realized_pnl < 0].realized_pnl.sum())
pf2 = gp2 / gn2 if gn2 > 0 else float("inf")
wr2 = len(not_eod_orb[not_eod_orb.realized_pnl > 0]) / len(not_eod_orb)
print(f"  ORB non-EOD exits: n={len(not_eod_orb)} ({len(not_eod_orb)/len(df_orb):.1%}), WR={wr2:.1%}, PF={pf2:.3f}")
print()

print("=" * 60)
print("iFVG: first vs second half of year performance (H1 vs H2 each year)")
print("=" * 60)
df_ifvg["month"] = df_ifvg["entry_et"].dt.month
df_ifvg["half"] = df_ifvg["month"].apply(lambda m: "H1" if m <= 6 else "H2")
for yr in sorted(df_ifvg.year.unique()):
    for half in ["H1", "H2"]:
        sub = df_ifvg[(df_ifvg.year == yr) & (df_ifvg.half == half)]
        if len(sub) == 0:
            continue
        gp = sub[sub.realized_pnl > 0].realized_pnl.sum()
        gn = abs(sub[sub.realized_pnl < 0].realized_pnl.sum())
        pf = gp / gn if gn > 0 else float("inf")
        print(f"  {yr} {half}: n={len(sub):4d}, PF={pf:.3f}")
print()

print("=" * 60)
print("ORB: per-year, per-DOW PF (confirming Monday/Wednesday/Friday patterns)")
print("=" * 60)
for dow in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
    sub_d = df_orb[df_orb.dow == dow]
    if len(sub_d) == 0:
        continue
    gp = sub_d[sub_d.realized_pnl > 0].realized_pnl.sum()
    gn = abs(sub_d[sub_d.realized_pnl < 0].realized_pnl.sum())
    pf = gp / gn if gn > 0 else float("inf")
    net = sub_d.realized_pnl.sum()
    print(f"  {dow:10s}: n={len(sub_d):3d}, PF={pf:.3f}, net=${net:8,.0f}")
print()
