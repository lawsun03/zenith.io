"""iFVG rank-2+ direction pair breakdown"""
import pandas as pd
from pathlib import Path

CSV = Path("research/mfe_mae_deployed_combined_clean.csv")
df = pd.read_csv(CSV, parse_dates=["entry_ts","exit_ts"])
ifvg = df[df["engine_type"]=="ifvg"].copy()
ifvg["date"] = ifvg["entry_ts"].dt.date
ifvg = ifvg.sort_values(["date","entry_ts"]).reset_index(drop=True)
ifvg["rank"] = ifvg.groupby("date").cumcount() + 1
rank1 = ifvg[ifvg["rank"]==1][["date","side"]].rename(columns={"side":"rank1_side"})
ifvg = ifvg.merge(rank1, on="date", how="left")
rank2 = ifvg[ifvg["rank"]>=2].copy()
rank2["pair"] = rank2["rank1_side"] + "->" + rank2["side"]

def pf(g):
    w = g[g["pnl_usd"]>0]["pnl_usd"].sum()
    l = abs(g[g["pnl_usd"]<0]["pnl_usd"].sum())
    return w/l if l>0 else float("inf")

print("Direction pair analysis (rank-2+ iFVG signals):")
for pair in ["long->long","long->short","short->long","short->short"]:
    g = rank2[rank2["pair"]==pair]
    net = g["pnl_usd"].sum()
    print(f"  {pair}: n={len(g)}, PF={pf(g):.3f}, net={net:+.0f}")

print()
r1 = ifvg[ifvg["rank"]==1]
print(f"Rank-1 direction: long={len(r1[r1['side']=='long'])}, short={len(r1[r1['side']=='short'])}")

# Per-year for long->long (pure continuation long)
print("\nlong->long per year:")
rank2["year"] = rank2["entry_ts"].dt.year
ll = rank2[rank2["pair"]=="long->long"]
for yr in sorted(ll["year"].unique()):
    g = ll[ll["year"]==yr]
    print(f"  {yr}: n={len(g)}, PF={pf(g):.3f}")

print("\nshort->long per year:")
sl = rank2[rank2["pair"]=="short->long"]
for yr in sorted(sl["year"].unique()):
    g = sl[sl["year"]==yr]
    print(f"  {yr}: n={len(g)}, PF={pf(g):.3f}")

print("\nlong->short per year:")
ls = rank2[rank2["pair"]=="long->short"]
for yr in sorted(ls["year"].unique()):
    g = ls[ls["year"]==yr]
    print(f"  {yr}: n={len(g)}, PF={pf(g):.3f}")

print("\nshort->short per year:")
ss = rank2[rank2["pair"]=="short->short"]
for yr in sorted(ss["year"].unique()):
    g = ss[ss["year"]==yr]
    print(f"  {yr}: n={len(g)}, PF={pf(g):.3f}")

# Summary: what drives the conflict advantage?
print("\n--- Summary ---")
print(f"Conflict (long->short + short->long): n={len(rank2[rank2['pair'].isin(['long->short','short->long'])])}, PF={pf(rank2[rank2['pair'].isin(['long->short','short->long'])]):.3f}")
print(f"Continuation (long->long + short->short): n={len(rank2[rank2['pair'].isin(['long->long','short->short'])])}, PF={pf(rank2[rank2['pair'].isin(['long->long','short->short'])]):.3f}")
# Specifically: are SHORT->LONG signals (takes a long after a short) driving the conflict advantage?
sl_pf = pf(sl)
ls_pf = pf(ls)
print(f"Within conflict: short->long PF={sl_pf:.3f} vs long->short PF={ls_pf:.3f}")
print(f"Within continuation: long->long PF={pf(ll):.3f} vs short->short PF={pf(ss):.3f}")
