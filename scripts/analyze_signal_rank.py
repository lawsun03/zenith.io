"""Analyze per-signal-rank PF from iFVG MFE/MAE data."""
import pandas as pd
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

df = pd.read_csv("research/mfe_mae_ifvg_clean.csv", parse_dates=["entry_ts"])
df["entry_et"] = df["entry_ts"].dt.tz_convert(ET)
df["date"] = df["entry_et"].dt.date

# Rank each signal within its day
df = df.sort_values(["date", "entry_et"])
df["signal_rank"] = df.groupby("date").cumcount() + 1  # 1 = first signal of day

# PF by signal rank
df["win"] = df["realized_pnl"] > 0
gross_win = df.groupby("signal_rank")["realized_pnl"].apply(lambda x: x[x > 0].sum())
gross_loss = df.groupby("signal_rank")["realized_pnl"].apply(lambda x: x[x < 0].abs().sum())
count = df.groupby("signal_rank")["realized_pnl"].count()
wr = df.groupby("signal_rank")["win"].mean()

print("=== iFVG Per-Signal-Rank Analysis (5y excl 2022) ===")
print(f"{'Rank':>5} {'Count':>6} {'WR%':>6} {'GrossWin':>10} {'GrossLoss':>10} {'PF':>6}")
for rank in sorted(count.index[:10]):
    gw = gross_win.get(rank, 0)
    gl = gross_loss.get(rank, 0)
    pf = gw / gl if gl > 0 else float("inf")
    print(f"{rank:>5} {count[rank]:>6} {wr[rank]*100:>5.1f}% {gw:>10.0f} {gl:>10.0f} {pf:>6.3f}")

print()
print(f"Total signals with rank >= 4: {len(df[df['signal_rank'] >= 4])}")
print()

# How many days have N signals?
day_counts = df.groupby("date").size()
print("=== Days with N signals ===")
for n in range(1, 11):
    cnt = (day_counts == n).sum()
    if cnt > 0:
        print(f"  {n} signals: {cnt} days")
print(f"  >10 signals: {(day_counts > 10).sum()} days")

# Cumulative PF for signals 1-only, 1-2, 1-3, all
print()
print("=== Cumulative PF if we cap at N signals per day ===")
for cap in [1, 2, 3, 4, 5, "all"]:
    if cap == "all":
        sub = df
    else:
        sub = df[df["signal_rank"] <= cap]
    gw = sub.loc[sub["realized_pnl"] > 0, "realized_pnl"].sum()
    gl = sub.loc[sub["realized_pnl"] < 0, "realized_pnl"].abs().sum()
    pf = gw / gl if gl > 0 else float("inf")
    print(f"  Cap {str(cap):>3}: n={len(sub):>5} PF={pf:.3f}  (GW={gw:.0f}  GL={gl:.0f})")

# Per-rank breakdown by side
print()
print("=== Per-Rank PF by Side (rank 1-5) ===")
for rank in range(1, 6):
    sub = df[df["signal_rank"] == rank]
    for side in ["long", "short"]:
        s = sub[sub["side"] == side]
        if len(s) == 0:
            continue
        gw = s.loc[s["realized_pnl"] > 0, "realized_pnl"].sum()
        gl = s.loc[s["realized_pnl"] < 0, "realized_pnl"].abs().sum()
        pf = gw / gl if gl > 0 else float("inf")
        print(f"  Rank {rank} {side:>5}: n={len(s):>4} PF={pf:.3f}")
