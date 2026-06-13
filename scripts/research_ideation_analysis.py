"""One-shot analysis script for research/ideation session — not shipped."""
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

# --- iFVG analysis ---
print("=" * 60)
print("iFVG CLEAN (partial_r=0)")
rows = load_csv("research/mfe_mae_ifvg_clean.csv")
print(f"Total: {len(rows)} trades, WR={wr(rows):.1f}%, PF={pf(rows):.3f}")

print("\nBy side:")
for side in ["long", "short"]:
    sr = [r for r in rows if r["side"] == side]
    print(f"  {side}: n={len(sr)}, WR={wr(sr):.1f}%, PF={pf(sr):.3f}")

print("\nBy hour (ET):")
print(f"{'Hour':>6} | {'n':>5} | {'WR%':>6} | {'PF':>6} | {'med_r_mfe':>10} | {'total_pnl':>10}")
by_hour = {}
for r in rows:
    by_hour.setdefault(r["hour_et"], []).append(r)
for h in sorted(by_hour):
    rs = by_hour[h]
    mfes = [r["r_mfe"] for r in rs]
    print(f"  {h:2d}:00 | {len(rs):5d} | {wr(rs):6.1f} | {pf(rs):6.3f} | {statistics.median(mfes):10.3f} | {sum(r['pnl'] for r in rs):10.0f}")

print("\nBy year:")
print(f"{'Year':>6} | {'n':>5} | {'WR%':>6} | {'PF':>6}")
by_year = {}
for r in rows:
    by_year.setdefault(r["year"], []).append(r)
for y in sorted(by_year):
    rs = by_year[y]
    print(f"  {y} | {len(rs):5d} | {wr(rs):6.1f} | {pf(rs):6.3f}")

# --- ORB analysis ---
print("\n" + "=" * 60)
print("ORB r2.5 CLEAN (partial_r=0)")
orb = load_csv("research/mfe_mae_orb_clean.csv")
print(f"Total: {len(orb)} trades, WR={wr(orb):.1f}%, PF={pf(orb):.3f}")

print("\nBy side:")
for side in ["long", "short"]:
    sr = [r for r in orb if r["side"] == side]
    print(f"  {side}: n={len(sr)}, WR={wr(sr):.1f}%, PF={pf(sr):.3f}")

print("\nBy hour (ET):")
print(f"{'Hour':>6} | {'n':>5} | {'WR%':>6} | {'PF':>6} | {'med_r_mfe':>10}")
by_hour_orb = {}
for r in orb:
    by_hour_orb.setdefault(r["hour_et"], []).append(r)
for h in sorted(by_hour_orb):
    rs = by_hour_orb[h]
    mfes = [r["r_mfe"] for r in rs]
    print(f"  {h:2d}:00 | {len(rs):5d} | {wr(rs):6.1f} | {pf(rs):6.3f} | {statistics.median(mfes):10.3f}")

print("\nBy year:")
for y in sorted(by_year):
    rs_orb = [r for r in orb if r["year"] == y]
    if rs_orb:
        print(f"  {y} | {len(rs_orb):5d} | {wr(rs_orb):6.1f} | {pf(rs_orb):6.3f}")

# --- MFE distribution (winners only) ---
print("\n" + "=" * 60)
print("iFVG winner MFE distribution (R units):")
w_ifvg = [r for r in rows if r["winner"]]
if w_ifvg:
    mfes = sorted(r["r_mfe"] for r in w_ifvg)
    n = len(mfes)
    for pct in [10, 25, 50, 75, 90, 95]:
        idx = min(int(n * pct / 100), n - 1)
        print(f"  p{pct}: {mfes[idx]:.3f}R")

print("\niFVG loser MFE distribution (R units, how far winners fail to run):")
l_ifvg = [r for r in rows if not r["winner"]]
if l_ifvg:
    mfes = sorted(r["r_mfe"] for r in l_ifvg)
    n = len(mfes)
    for pct in [25, 50, 75, 90]:
        idx = min(int(n * pct / 100), n - 1)
        print(f"  p{pct}: {mfes[idx]:.3f}R")

# --- Long-only iFVG ---
print("\n" + "=" * 60)
print("iFVG LONG-ONLY analysis:")
longs = [r for r in rows if r["side"] == "long"]
shorts = [r for r in rows if r["side"] == "short"]
print(f"Long: n={len(longs)}, WR={wr(longs):.1f}%, PF={pf(longs):.3f}")
print(f"Short: n={len(shorts)}, WR={wr(shorts):.1f}%, PF={pf(shorts):.3f}")

# Compute what long-only PF would be if shorts were eliminated
long_only_rows = longs
print(f"\nIf short signals BLOCKED (long-only iFVG):")
print(f"  n={len(long_only_rows)}, trades/mo approx={len(long_only_rows)/60:.0f}")
print(f"  PF={pf(long_only_rows):.3f} (vs all trades PF={pf(rows):.3f})")
