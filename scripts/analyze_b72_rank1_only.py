"""
B72 Phase 1 — iFVG rank-1-only gate in close-mode long-only deployed config.

In LO config only long signals fire, so daily rank is computed among longs only.
GO criterion: rank-1 PF >= 1.50 (high bar — Lesson 83/105/109: volume starvation
risk when restricting to rank-1 removes ~40-60% of long signals).

Data source: mfe_mae_ifvg_clean.csv (both-sides close-mode iFVG, 5y excl 2022).
Filter to longs-only, re-rank within day, compute PF by rank.
"""
from __future__ import annotations

import pandas as pd
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
GO_THRESHOLD = 1.50

df = pd.read_csv("research/mfe_mae_ifvg_clean.csv", parse_dates=["entry_ts"])
df["entry_ts"] = df["entry_ts"].dt.tz_convert(ET) if df["entry_ts"].dt.tz else df["entry_ts"].dt.tz_localize("UTC").dt.tz_convert(ET)
df["entry_et"] = df["entry_ts"]
df["date"] = df["entry_et"].dt.date
df["year"] = df["entry_et"].dt.year

# --- LO config: filter to longs only, re-rank within day ---
longs = df[df["side"] == "long"].copy()
longs = longs.sort_values(["date", "entry_et"])
longs["signal_rank"] = longs.groupby("date").cumcount() + 1


def pf(sub: pd.DataFrame) -> float:
    gw = sub.loc[sub["realized_pnl"] > 0, "realized_pnl"].sum()
    gl = sub.loc[sub["realized_pnl"] < 0, "realized_pnl"].abs().sum()
    return gw / gl if gl > 0 else float("inf")


rank1 = longs[longs["signal_rank"] == 1]
rank2plus = longs[longs["signal_rank"] >= 2]

pf_r1 = pf(rank1)
pf_r2plus = pf(rank2plus)
pf_all_long = pf(longs)

print("=== B72 iFVG Rank-1-Only — Long-Only Config ===")
print(f"Data: mfe_mae_ifvg_clean.csv | years: {sorted(longs['year'].unique().tolist())}")
print()
print(f"{'Slice':30s} {'n':>6} {'PF':>7}  {'GrossWin':>10}  {'GrossLoss':>10}")
for label, sub in [
    ("All longs (baseline)", longs),
    ("Rank-1 longs only", rank1),
    ("Rank-2+ longs", rank2plus),
]:
    gw = sub.loc[sub["realized_pnl"] > 0, "realized_pnl"].sum()
    gl = sub.loc[sub["realized_pnl"] < 0, "realized_pnl"].abs().sum()
    p = gw / gl if gl > 0 else float("inf")
    print(f"  {label:28s} {len(sub):>6} {p:>7.3f}  {gw:>10.0f}  {gl:>10.0f}")

print()
print(f"GO criterion: rank-1 PF >= {GO_THRESHOLD:.2f}")
verdict = "GO" if pf_r1 >= GO_THRESHOLD else "NO-GO"
print(f"Rank-1 PF = {pf_r1:.3f}  ->  {verdict}")

print()
print("=== Year-by-year rank-1 PF (LO config) ===")
years_go = 0
for yr in sorted(longs["year"].unique()):
    sub = longs[(longs["year"] == yr) & (longs["signal_rank"] == 1)]
    sub2 = longs[(longs["year"] == yr) & (longs["signal_rank"] >= 2)]
    p1 = pf(sub)
    p2 = pf(sub2) if len(sub2) > 0 else float("nan")
    go = "*GO*" if p1 >= GO_THRESHOLD else ""
    if p1 >= GO_THRESHOLD:
        years_go += 1
    print(f"  {yr}: rank-1 n={len(sub):>3}  PF={p1:.3f}  | rank-2+ n={len(sub2):>3}  PF={p2:.3f}  {go}")
print(f"Years where rank-1 PF >= {GO_THRESHOLD:.2f}: {years_go}/{len(longs['year'].unique())}")

print()
print("=== Signal volume impact ===")
total_months = len(longs["year"].unique()) * 12
print(f"Total longs/month:   {len(longs)/total_months:.1f}")
print(f"Rank-1 longs/month:  {len(rank1)/total_months:.1f}")
print(f"Rank-2+ longs/month: {len(rank2plus)/total_months:.1f}")
print(f"Volume reduction:    -{(1-len(rank1)/len(longs))*100:.0f}% signals removed")

print()
print("=== Days with N long signals ===")
day_counts = longs.groupby("date").size()
for n in range(1, 8):
    cnt = (day_counts == n).sum()
    if cnt > 0:
        print(f"  {n} long signals/day: {cnt} days")
print(f"  >7 long signals/day: {(day_counts > 7).sum()} days")
