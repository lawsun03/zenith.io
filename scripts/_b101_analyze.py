"""B101 Fib-extension TARGET analyzer.

Reads research/equity_b101/trades_<tag>.csv (one row per closed trade) and emits
a per-variant table: win%, PF, net$, expectancy$/trade, trades/mo, exit class
(target%/stop%/EOD%), mean-R (normalized by avg stop $), effective-R, plus a
per-year overfit guard. Funded XFA parsed from the _funded_*.log files.
Mirrors scripts/_b99_analyze.py.
"""
import csv
import re
import sys
from collections import defaultdict
from datetime import datetime

BASE = "research/equity_b101"
EOD_MINUTES = {(20, 9), (21, 9)}  # 3:10pm CT flatten in CDT/CST

IFVG = [("base", "ifvg_base"), ("1.272", "ifvg_1p272"), ("1.414", "ifvg_1p414"),
        ("1.618", "ifvg_1p618"), ("2.0", "ifvg_2p0"), ("2.618", "ifvg_2p618")]
ORB = [("1.272", "orb_1p272"), ("1.414", "orb_1p414"), ("1.618", "orb_1p618"),
       ("2.0", "orb_2p0"), ("2.618", "orb_2p618")]


def load(tag):
    rows = []
    try:
        with open(f"{BASE}/trades_{tag}.csv", newline="") as f:
            for t in csv.DictReader(f):
                rows.append({
                    "entry": datetime.fromisoformat(t["entry_ts"]),
                    "exit": datetime.fromisoformat(t["exit_ts"]),
                    "side": t["side"], "pnl": float(t["pnl_usd"]),
                })
    except FileNotFoundError:
        return None
    return rows


def classify(t):
    if (t["exit"].hour, t["exit"].minute) in EOD_MINUTES:
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
    return {"n": n, "win%": 100 * wins / n if n else 0, "pf": pf(pnls),
            "net": sum(pnls), "exp": sum(pnls) / n if n else 0,
            "tpm": n / months if months else 0, "mean_r": mean_r,
            "tgt%": 100 * cls["target"] / n if n else 0,
            "stop%": 100 * cls["stop"] / n if n else 0,
            "eod%": 100 * cls["eod"] / n if n else 0}


def per_year_pf(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["entry"].year].append(r["pnl"])
    return {y: pf(p) for y, p in sorted(by.items())}


def funded(tag, h=200):
    """Return (xfa_net, 'busts/accts') from a funded_sim log block."""
    try:
        txt = open(f"{BASE}/_funded_{tag}_h{h}.log").read()
    except FileNotFoundError:
        return None, None
    net = re.search(r"\$[\-0-9,]+ gross / \$([\-0-9,]+) net", txt)
    xfa = re.search(r"XFA:\s*accounts (\d+) \| busts (\d+)", txt)
    net_v = net.group(1).replace(",", "") if net else "?"
    ba = f"{xfa.group(2)}/{xfa.group(1)}" if xfa else "?"
    return net_v, ba


def table(title, variants):
    print("=" * 104)
    print(title)
    print(f"{'ext':>6} {'n':>5} {'win%':>5} {'PF':>5} {'meanR':>6} {'exp$':>7} "
          f"{'tr/mo':>5} {'tgt%':>5} {'stop%':>6} {'eod%':>5} {'net$':>9} "
          f"{'XFAh200':>9} {'b/a':>7}")
    print("-" * 110)
    store = {}
    for label, tag in variants:
        rows = load(tag)
        if rows is None:
            print(f"{label:>6}  (missing {tag})")
            continue
        store[label] = rows
        s = stats(rows)
        net, ba = funded(tag)
        print(f"{label:>6} {s['n']:>5} {s['win%']:>5.1f} {s['pf']:>5.2f} "
              f"{s['mean_r']:>6.3f} {s['exp']:>7.1f} {s['tpm']:>5.1f} {s['tgt%']:>5.1f} "
              f"{s['stop%']:>6.1f} {s['eod%']:>5.1f} {s['net']:>9.0f} "
              f"{str(net):>9} {str(ba):>7}")
    print("\nPer-year PF (overfit guard):")
    for label, rows in store.items():
        yr = per_year_pf(rows)
        print(f"  {label:>6}: " + "  ".join(f"{y}:{v:.2f}" for y, v in yr.items()))
    print()


if __name__ == "__main__":
    table("iFVG Fib-ext target (NOVEL) — baseline = fixed r2.5 deployed", IFVG)
    table("ORB Fib-ext target (CROSS-CHECK vs B99 r1.5/r2.5)", ORB)
