"""B99 ORB target-R sweep analyzer.

Reads research/equity_b99/trades_r{TAG}.csv (one row per closed trade) and emits
a per-R table: win%, PF, net$, expectancy$/trade, trades/mo, exit classification
(target-hit% / stop% / EOD-flat%), mean-R (normalized by avg stop-loss $), plus a
per-year overfit-guard breakdown. Funded/combine numbers are parsed separately
from the run logs. Not authoritative for sizing -- expectancy is the metric.
"""
import csv
from collections import defaultdict
from datetime import datetime

RS = [("1.0", "r1p0"), ("1.5", "r1p5"), ("2.0", "r2p0"), ("2.5", "r2p5")]
BASE = "research/equity_b99"

# EOD flatten fires at 3:10pm CT -> 20:09 (CDT) or 21:09 (CST) UTC.
EOD_MINUTES = {(20, 9), (21, 9)}


def load(tag):
    rows = []
    with open(f"{BASE}/trades_{tag}.csv", newline="") as f:
        for t in csv.DictReader(f):
            rows.append({
                "entry": datetime.fromisoformat(t["entry_ts"]),
                "exit": datetime.fromisoformat(t["exit_ts"]),
                "side": t["side"],
                "pnl": float(t["pnl_usd"]),
            })
    return rows


def classify(t):
    h, m = t["exit"].hour, t["exit"].minute
    if (h, m) in EOD_MINUTES:
        return "eod"
    return "target" if t["pnl"] > 0 else "stop"


def pf(pnls):
    g = sum(p for p in pnls if p > 0)
    l = -sum(p for p in pnls if p < 0)
    return g / l if l else float("inf")


def stats(rows):
    n = len(rows)
    pnls = [r["pnl"] for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    cls = defaultdict(int)
    for r in rows:
        cls[classify(r)] += 1
    stops = [r["pnl"] for r in rows if classify(r) == "stop"]
    avg_stop = abs(sum(stops) / len(stops)) if stops else 1.0
    mean_r = (sum(pnls) / n) / avg_stop if n and avg_stop else 0.0
    months = len({(r["entry"].year, r["entry"].month) for r in rows})
    return {
        "n": n, "win%": 100 * wins / n if n else 0, "pf": pf(pnls),
        "net": sum(pnls), "exp": sum(pnls) / n if n else 0,
        "tpm": n / months if months else 0, "mean_r": mean_r,
        "tgt%": 100 * cls["target"] / n if n else 0,
        "stop%": 100 * cls["stop"] / n if n else 0,
        "eod%": 100 * cls["eod"] / n if n else 0,
    }


print("=" * 96)
print(f"{'R':>4} {'n':>5} {'win%':>5} {'PF':>5} {'meanR':>6} {'exp$':>7} "
      f"{'tr/mo':>5} {'tgt%':>5} {'stop%':>6} {'eod%':>5} {'net$':>9}")
print("-" * 96)
allrows = {}
for r, tag in RS:
    rows = load(tag)
    allrows[r] = rows
    s = stats(rows)
    print(f"{r:>4} {s['n']:>5} {s['win%']:>5.1f} {s['pf']:>5.2f} {s['mean_r']:>6.3f} "
          f"{s['exp']:>7.1f} {s['tpm']:>5.1f} {s['tgt%']:>5.1f} {s['stop%']:>6.1f} "
          f"{s['eod%']:>5.1f} {s['net']:>9.0f}")

print("\n" + "=" * 70)
print("PER-YEAR OVERFIT GUARD (PF | win% | net$)")
print(f"{'year':>5}", end="")
for r, _ in RS:
    print(f" {'r'+r:>18}", end="")
print()
years = sorted({r["entry"].year for rows in allrows.values() for r in rows})
for y in years:
    print(f"{y:>5}", end="")
    for r, _ in RS:
        yr = [t for t in allrows[r] if t["entry"].year == y]
        s = stats(yr)
        print(f" {s['pf']:>5.2f}/{s['win%']:>4.0f}/{s['net']:>7.0f}", end="")
    print()
