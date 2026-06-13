"""One-off research mining script for wk2-r3 session."""
import pandas as pd
import pytz

et = pytz.timezone("America/New_York")

# --- ORB timing analysis ---
df_orb = pd.read_csv("research/mfe_mae_orb_clean.csv")
df_orb["entry_dt"] = pd.to_datetime(df_orb["entry_ts"], utc=True)
df_orb["entry_et"] = df_orb["entry_dt"].dt.tz_convert(et)
df_orb["hour"] = df_orb["entry_et"].dt.hour
df_orb["minute"] = df_orb["entry_et"].dt.minute
df_orb["dow"] = df_orb["entry_et"].dt.day_name()
df_orb["mins_after_open"] = (df_orb["hour"] - 9)*60 + df_orb["minute"] - 30
df_orb = df_orb[df_orb["year"] != 2022]

total = len(df_orb)
wins = (df_orb["realized_pnl"] > 0).sum()
gp = df_orb[df_orb.realized_pnl > 0].realized_pnl.sum()
gn = abs(df_orb[df_orb.realized_pnl < 0].realized_pnl.sum())
print(f"ORB 5y (excl 2022): n={total}, WR={wins/total:.1%}, PF={gp/gn:.3f}")
print()

bins = [15, 25, 35, 45, 60, 90, 120, 450]
labels = ["9:45-55", "9:55-10:05", "10:05-15", "10:15-30", "10:30-11", "11-11:30", "11:30+"]
df_orb["mins_bucket"] = pd.cut(df_orb["mins_after_open"], bins=bins, labels=labels)

print("ORB: Minutes after 9:30 ET (range locks at +15min)")
for lbl in labels:
    sub = df_orb[df_orb.mins_bucket == lbl]
    if len(sub) == 0:
        continue
    gp2 = sub[sub.realized_pnl > 0].realized_pnl.sum()
    gn2 = abs(sub[sub.realized_pnl < 0].realized_pnl.sum())
    pf = gp2 / gn2 if gn2 > 0 else float("inf")
    net = sub.realized_pnl.sum()
    print(f"  {lbl:15s}: n={len(sub):4d}, PF={pf:.3f}, net=${net:8,.0f}")
print()

print("ORB: Side breakdown by timing bucket")
for side in ["long", "short"]:
    sub_s = df_orb[df_orb.side == side]
    gp2 = sub_s[sub_s.realized_pnl > 0].realized_pnl.sum()
    gn2 = abs(sub_s[sub_s.realized_pnl < 0].realized_pnl.sum())
    print(f"  {side}: n={len(sub_s)}, PF={gp2/gn2:.3f}")
    for lbl in labels:
        sub = sub_s[sub_s.mins_bucket == lbl]
        if len(sub) < 5:
            continue
        gp2 = sub[sub.realized_pnl > 0].realized_pnl.sum()
        gn2 = abs(sub[sub.realized_pnl < 0].realized_pnl.sum())
        pf = gp2 / gn2 if gn2 > 0 else float("inf")
        print(f"    {lbl:15s}: n={len(sub):3d}, PF={pf:.3f}")
print()

# --- iFVG MAE vs outcome ---
df_ifvg = pd.read_csv("research/mfe_mae_ifvg_clean.csv")
df_ifvg["entry_dt"] = pd.to_datetime(df_ifvg["entry_ts"], utc=True)
df_ifvg["entry_et"] = df_ifvg["entry_dt"].dt.tz_convert(et)
df_ifvg["hour"] = df_ifvg["entry_et"].dt.hour
df_ifvg["dow"] = df_ifvg["entry_et"].dt.day_name()
df_ifvg = df_ifvg[df_ifvg["year"] != 2022]

gp = df_ifvg[df_ifvg.realized_pnl > 0].realized_pnl.sum()
gn = abs(df_ifvg[df_ifvg.realized_pnl < 0].realized_pnl.sum())
print(f"iFVG 5y (excl 2022): n={len(df_ifvg)}, WR={len(df_ifvg[df_ifvg.realized_pnl>0])/len(df_ifvg):.1%}, PF={gp/gn:.3f}")
print()

print("iFVG: outcome by initial MAE bucket (r_mae vs stop)")
bins_mae = [0, 0.25, 0.5, 0.75, 1.0, 5.0]
labels_mae = ["0-0.25R", "0.25-0.5R", "0.5-0.75R", "0.75-1R", "1R+"]
df_ifvg["mae_bucket"] = pd.cut(df_ifvg.r_mae, bins=bins_mae, labels=labels_mae)
for lbl in labels_mae:
    sub = df_ifvg[df_ifvg.mae_bucket == lbl]
    if len(sub) == 0:
        continue
    gp2 = sub[sub.realized_pnl > 0].realized_pnl.sum()
    gn2 = abs(sub[sub.realized_pnl < 0].realized_pnl.sum())
    pf = gp2 / gn2 if gn2 > 0 else float("inf")
    wr = len(sub[sub.realized_pnl > 0]) / len(sub)
    print(f"  {lbl:12s}: n={len(sub):4d}, WR={wr:.1%}, PF={pf:.3f}")
print()

print("iFVG: early MAE bucket (<= 0.3R) -- trades that moved favorably first")
early_fav = df_ifvg[df_ifvg.r_mae <= 0.3]
late_adv = df_ifvg[df_ifvg.r_mae > 0.3]
gp1 = early_fav[early_fav.realized_pnl > 0].realized_pnl.sum()
gn1 = abs(early_fav[early_fav.realized_pnl < 0].realized_pnl.sum())
gp2 = late_adv[late_adv.realized_pnl > 0].realized_pnl.sum()
gn2 = abs(late_adv[late_adv.realized_pnl < 0].realized_pnl.sum())
print(f"  MAE<=0.3R (clean run): n={len(early_fav)}, PF={gp1/gn1:.3f}, WR={len(early_fav[early_fav.realized_pnl>0])/len(early_fav):.1%}")
print(f"  MAE>0.3R (dipped first): n={len(late_adv)}, PF={gp2/gn2:.3f}, WR={len(late_adv[late_adv.realized_pnl>0])/len(late_adv):.1%}")
print()

print("iFVG: hold time (seconds) for winners vs losers")
winners = df_ifvg[df_ifvg.realized_pnl > 0]
losers = df_ifvg[df_ifvg.realized_pnl < 0]
print(f"  Winners: mean={winners.hold_seconds.mean():.0f}s, p50={winners.hold_seconds.median():.0f}s, p75={winners.hold_seconds.quantile(0.75):.0f}s")
print(f"  Losers:  mean={losers.hold_seconds.mean():.0f}s,  p50={losers.hold_seconds.median():.0f}s,  p75={losers.hold_seconds.quantile(0.75):.0f}s")
print()

print("iFVG: MFE >= 1.5R (would survive a 1.5R partial exit) by side")
for side in ["long", "short"]:
    sub = df_ifvg[df_ifvg.side == side]
    hi_mfe = sub[sub.r_mfe >= 1.5]
    gp2 = hi_mfe[hi_mfe.realized_pnl > 0].realized_pnl.sum()
    gn2 = abs(hi_mfe[hi_mfe.realized_pnl < 0].realized_pnl.sum())
    pf = gp2 / gn2 if gn2 > 0 else float("inf")
    print(f"  {side} MFE>=1.5R: n={len(hi_mfe)}/{len(sub)} ({len(hi_mfe)/len(sub):.1%}), PF={pf:.3f}")
    sub_rest = sub[sub.r_mfe < 1.5]
    gp3 = sub_rest[sub_rest.realized_pnl > 0].realized_pnl.sum()
    gn3 = abs(sub_rest[sub_rest.realized_pnl < 0].realized_pnl.sum())
    pf2 = gp3 / gn3 if gn3 > 0 else float("inf")
    print(f"  {side} MFE<1.5R:  n={len(sub_rest)}/{len(sub)} ({len(sub_rest)/len(sub):.1%}), PF={pf2:.3f}")
print()

print("ORB: MFE bucket analysis (how often does each MFE zone occur)")
bins_mfe = [0, 0.5, 1.0, 1.5, 2.0, 2.5, 5.0]
labels_mfe = ["0-0.5R", "0.5-1R", "1-1.5R", "1.5-2R", "2-2.5R", "2.5R+"]
df_orb["mfe_bucket"] = pd.cut(df_orb.r_mfe, bins=bins_mfe, labels=labels_mfe)
for lbl in labels_mfe:
    sub = df_orb[df_orb.mfe_bucket == lbl]
    if len(sub) == 0:
        continue
    gp2 = sub[sub.realized_pnl > 0].realized_pnl.sum()
    gn2 = abs(sub[sub.realized_pnl < 0].realized_pnl.sum())
    pf = gp2 / gn2 if gn2 > 0 else float("inf")
    wr = len(sub[sub.realized_pnl > 0]) / len(sub)
    print(f"  {lbl:12s}: n={len(sub):4d}, WR={wr:.1%}, PF={pf:.3f}, net={sub.realized_pnl.sum():8,.0f}")
