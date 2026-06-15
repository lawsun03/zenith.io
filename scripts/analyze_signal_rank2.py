"""Analyze rank-1 only vs rank-2+ longs only hybrid approach."""
import pandas as pd
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

df = pd.read_csv("research/mfe_mae_ifvg_clean.csv", parse_dates=["entry_ts"])
df["entry_et"] = df["entry_ts"].dt.tz_convert(ET)
df["date"] = df["entry_et"].dt.date

df = df.sort_values(["date", "entry_et"])
df["signal_rank"] = df.groupby("date").cumcount() + 1
df["win"] = df["realized_pnl"] > 0


def pf_summary(sub, label):
    gw = sub.loc[sub["realized_pnl"] > 0, "realized_pnl"].sum()
    gl = sub.loc[sub["realized_pnl"] < 0, "realized_pnl"].abs().sum()
    pf = gw / gl if gl > 0 else float("inf")
    return f"{label}: n={len(sub):>5}  PF={pf:.3f}  (GW={gw:.0f}  GL={gl:.0f})"


# Strategy A: all rank-1 + rank-2+ longs only
hybrid = df[(df["signal_rank"] == 1) | ((df["signal_rank"] > 1) & (df["side"] == "long"))]
print(pf_summary(hybrid, "Hybrid (rank1-all + rank2+-long)"))

# Breakdown by component
print(pf_summary(df[df["signal_rank"] == 1], "  Rank-1 all"))
print(pf_summary(df[(df["signal_rank"] > 1) & (df["side"] == "long")], "  Rank-2+ long"))
print(pf_summary(df[(df["signal_rank"] > 1) & (df["side"] == "short")], "  Rank-2+ short (removed)"))
print()

# Strategy B: rank-1 all + rank-2 long only (drop rank 3+)
b = df[(df["signal_rank"] == 1) | ((df["signal_rank"] == 2) & (df["side"] == "long"))]
print(pf_summary(b, "Rank-1-all + Rank-2-long only"))
print()

# Full baseline
print(pf_summary(df, "Full baseline (all)"))
print()

# B15 approximation: long-only all ranks
print(pf_summary(df[df["side"] == "long"], "Long-only all ranks (B15 proxy)"))
print()

# Monthly trade count estimates
n_years = 5
trading_months = 5 * 12  # 60 months (excl 2022 = 5*12=60)
print(f"Monthly trade count estimates (over {n_years} year excl. 2022):")
print(f"  Full:                {len(df)/trading_months:.1f}/month")
print(f"  Hybrid:              {len(hybrid)/trading_months:.1f}/month")
print(f"  Rank-1 only:         {len(df[df['signal_rank']==1])/trading_months:.1f}/month")
print(f"  Long-only (B15):     {len(df[df['side']=='long'])/trading_months:.1f}/month")

# Also compute for per-year grouped (60 months)
print()
print("=== Year-by-year hybrid PF ===")
df["year"] = df["entry_et"].dt.year
hybrid["year"] = df.loc[hybrid.index, "year"] if "year" in df.columns else hybrid["entry_et"].dt.year
for yr in sorted(df["year"].unique()):
    sub = hybrid[hybrid["year"] == yr]
    gw = sub.loc[sub["realized_pnl"] > 0, "realized_pnl"].sum()
    gl = sub.loc[sub["realized_pnl"] < 0, "realized_pnl"].abs().sum()
    pf = gw / gl if gl > 0 else float("inf")
    print(f"  {yr}: n={len(sub):>4}  PF={pf:.3f}")
