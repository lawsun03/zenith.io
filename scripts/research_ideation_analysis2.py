"""Deeper analysis: long-only by year, killzone hours, ORB second-entry estimate."""
import csv
import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

def load_csv(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            ts = datetime.fromisoformat(r["entry_ts"]).astimezone(ET)
            r["hour_et"] = ts.hour
            r["month"] = (ts.year, ts.month)
            r["year"] = ts.year
            r["r_mfe"] = float(r["r_mfe"])
            r["r_mae"] = float(r["r_mae"])
            r["pnl"] = float(r["realized_pnl"])
            r["winner"] = r["pnl"] > 0
            rows.append(r)
    return rows

def pf(rows):
    wins = sum(r["pnl"] for r in rows if r["winner"])
    losses = abs(sum(r["pnl"] for r in rows if not r["winner"]))
    return wins / losses if losses > 0 else float("inf")

def wr(rows):
    if not rows:
        return 0.0
    return sum(1 for r in rows if r["winner"]) / len(rows) * 100

rows = load_csv("research/mfe_mae_ifvg_clean.csv")
longs = [r for r in rows if r["side"] == "long"]
shorts = [r for r in rows if r["side"] == "short"]

# Long-only by year
print("Long-only iFVG by year vs ALL trades:")
print(f"{'Year':>6} | {'L-n':>5} | {'L-WR%':>7} | {'L-PF':>6} || {'A-n':>5} | {'A-WR%':>7} | {'A-PF':>6}")
by_year_long = {}
by_year_all = {}
for r in rows:
    by_year_all.setdefault(r["year"], []).append(r)
    if r["side"] == "long":
        by_year_long.setdefault(r["year"], []).append(r)
for y in sorted(by_year_all):
    la = by_year_long.get(y, [])
    al = by_year_all[y]
    print(f"  {y} | {len(la):5d} | {wr(la):7.1f} | {pf(la):6.3f} || {len(al):5d} | {wr(al):7.1f} | {pf(al):6.3f}")

# Check monthly distribution for long-only (Combine needs 60-90/month)
print("\nMonthly trade count distribution (long-only iFVG):")
by_month = {}
for r in longs:
    by_month.setdefault(r["month"], 0)
    by_month[r["month"]] += 1
counts = sorted(by_month.values())
n = len(counts)
for pct in [10, 25, 50, 75, 90]:
    idx = min(int(n * pct / 100), n - 1)
    print(f"  p{pct}: {counts[idx]} trades/month")
print(f"  max: {max(counts)}, min: {min(counts)}, months with >=20: {sum(1 for c in counts if c>=20)}/{n}")
print(f"  months with >=30: {sum(1 for c in counts if c>=30)}/{n}")

# Key: is there a killzone filter that would help? Check which hours contribute most PnL drag
print("\nWorst 5 hours for iFVG PnL (potential killzone targets):")
by_hour = {}
for r in rows:
    by_hour.setdefault(r["hour_et"], []).append(r)
hour_pnls = [(h, sum(r["pnl"] for r in rs), len(rs), pf(rs)) for h, rs in by_hour.items()]
for h, total_pnl, n, p in sorted(hour_pnls, key=lambda x: x[1])[:7]:
    print(f"  {h:2d}:00 ET  n={n:4d}  total_pnl={total_pnl:8.0f}  PF={p:.3f}")

print("\nBest 5 hours for iFVG PnL:")
for h, total_pnl, n, p in sorted(hour_pnls, key=lambda x: -x[1])[:7]:
    print(f"  {h:2d}:00 ET  n={n:4d}  total_pnl={total_pnl:8.0f}  PF={p:.3f}")

# ORB: estimate second-signal contribution
# If max_trades_per_day=2, we get trades in hours 10+ as well
# From ORB data: hour 9 = 632 (first breakouts), hour 10 = 351 (second breakouts after first was stopped or late first)
print("\n--- ORB signal timing analysis ---")
orb = load_csv("research/mfe_mae_orb_clean.csv")
by_hour_orb = {}
for r in orb:
    by_hour_orb.setdefault(r["hour_et"], []).append(r)
print("Hour | n | WR% | PF | note")
for h in sorted(by_hour_orb):
    rs = by_hour_orb[h]
    note = ""
    if h == 9:
        note = "<-- current primary (range closes 9:45)"
    elif h == 10:
        note = "<-- these are LATE first breakouts (OR still intact after 9:45)"
    elif h >= 11:
        note = "<-- very rare, OR already consumed"
    print(f"  {h:2d}:00 | {len(rs):3d} | {wr(rs):5.1f} | {pf(rs):.3f} | {note}")

# Check ORB by year long vs short
print("\nORB long vs short by year:")
for y in sorted(set(r["year"] for r in orb)):
    yl = [r for r in orb if r["year"] == y and r["side"] == "long"]
    ys = [r for r in orb if r["year"] == y and r["side"] == "short"]
    print(f"  {y}: long={len(yl)} WR={wr(yl):.0f}% PF={pf(yl):.3f}  short={len(ys)} WR={wr(ys):.0f}% PF={pf(ys):.3f}")

# ORB monthly count distribution (how many signal months have only 1 trade?)
print("\nORB monthly trade count distribution:")
by_month_orb = {}
for r in orb:
    by_month_orb.setdefault(r["month"], 0)
    by_month_orb[r["month"]] += 1
counts_orb = sorted(by_month_orb.values())
n = len(counts_orb)
for pct in [10, 25, 50, 75, 90]:
    idx = min(int(n * pct / 100), n - 1)
    print(f"  p{pct}: {counts_orb[idx]} trades/month")
print(f"  max: {max(counts_orb)}, min: {min(counts_orb)}")
print(f"  median: {statistics.median(counts_orb):.0f} trades/month")
print(f"  total months: {n}; 5yr avg: {len(orb)/n:.1f} trades/month")
