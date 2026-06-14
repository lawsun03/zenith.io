"""
B53 Phase 1 data mining: iFVG next-bar directional confirmation.

For each iFVG trade in mfe_mae_ifvg_clean.csv, check whether the 5-min bar
immediately AFTER entry (entry_ts + 5min) closes in the same direction as the
signal. Trades where N+1 confirms should have higher WR/PF if Lesson 74 holds
("stop failure rate scales inversely with number of confirmation steps").

GO/NO-GO: confirmed subset PF >= 1.15x aggregate PF AND confirmed n >= 60% of total.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import csv
from datetime import datetime, timedelta, timezone
import statistics

IFVG_CSV = ROOT / "research" / "mfe_mae_ifvg_clean.csv"
BARS_CSV = ROOT / "bars" / "bars_MNQ_dbv_2021_2026.csv"
EXCLUDE_YEAR = 2022


def load_bars(path: Path) -> dict[str, dict]:
    """Load bars indexed by timestamp string → row dict."""
    bars = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars[row["ts"]] = row
    return bars


def parse_ts(s: str) -> datetime:
    """Parse ISO timestamp to UTC datetime."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def next_bar_ts(entry_ts: datetime) -> str:
    """Return the 5-min bar timestamp 5 minutes after entry."""
    nb = entry_ts + timedelta(minutes=5)
    # Bars are labeled at their CLOSE time — so the bar open is at nb - 5min = entry_ts
    # The bar whose CLOSE is at entry_ts + 5min is the one we want.
    return nb.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def main():
    print("Loading bars...")
    bars = load_bars(BARS_CSV)
    print(f"Loaded {len(bars):,} bars")

    trades = []
    with open(IFVG_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row["year"]) == EXCLUDE_YEAR:
                continue
            trades.append(row)

    print(f"iFVG trades (excl {EXCLUDE_YEAR}): {len(trades)}")

    confirmed = []   # next bar confirms the direction
    unconfirmed = [] # next bar goes against the direction
    no_next = []     # next bar not found in data

    for t in trades:
        entry_ts = parse_ts(t["entry_ts"])
        entry_price = float(t["entry_price"])
        side = t["side"]
        pnl = float(t["realized_pnl"])
        is_win = pnl > 0

        nb_key = next_bar_ts(entry_ts)
        nb = bars.get(nb_key)
        if nb is None:
            no_next.append(t)
            continue

        nb_close = float(nb["close"])

        if side == "long":
            confirm = nb_close > entry_price
        else:
            confirm = nb_close < entry_price

        record = {"pnl": pnl, "is_win": is_win}
        if confirm:
            confirmed.append(record)
        else:
            unconfirmed.append(record)

    print(f"\nNext bar found: {len(confirmed)+len(unconfirmed)}, missing: {len(no_next)}")

    def stats(group: list[dict]) -> dict:
        if not group:
            return {"n": 0, "wr": 0, "pf": 0, "net": 0}
        n = len(group)
        wins = [r["pnl"] for r in group if r["pnl"] > 0]
        losses = [r["pnl"] for r in group if r["pnl"] <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
        wr = len(wins) / n * 100
        net = gross_win - gross_loss
        return {"n": n, "wr": wr, "pf": pf, "net": net}

    all_stats = stats(confirmed + unconfirmed)
    conf_stats = stats(confirmed)
    unconf_stats = stats(unconfirmed)

    print("\n=== N+1 Bar Directional Confirmation Results ===")
    print(f"{'Subset':<20} {'n':>6} {'WR%':>7} {'PF':>6} {'Net$':>12}")
    print("-" * 55)
    for name, s in [("All (excl 2022)", all_stats), ("Confirmed", conf_stats), ("Not confirmed", unconf_stats)]:
        print(f"{name:<20} {s['n']:>6} {s['wr']:>7.1f} {s['pf']:>6.3f} {s['net']:>12,.0f}")

    if all_stats["pf"] > 0 and conf_stats["n"] > 0:
        ratio = conf_stats["pf"] / all_stats["pf"]
        pct_confirmed = conf_stats["n"] / (conf_stats["n"] + unconf_stats["n"]) * 100
        print(f"\nConfirmed PF / All PF = {ratio:.3f} (GO threshold: >= 1.15)")
        print(f"Confirmed % of total = {pct_confirmed:.1f}% (GO threshold: >= 60%)")
        go = ratio >= 1.15 and pct_confirmed >= 60
        print(f"\nPhase 1 verdict: {'GO' if go else 'NO-GO'}")
    else:
        print("\nInsufficient data for GO/NO-GO assessment")

    # Per-year breakdown
    print("\n=== Per-year (confirmed vs not-confirmed PF) ===")
    for year in [2021, 2023, 2024, 2025, 2026]:
        year_trades = [t for t in trades if int(t["year"]) == year]
        year_conf = []
        year_unconf = []
        for t in year_trades:
            entry_ts = parse_ts(t["entry_ts"])
            entry_price = float(t["entry_price"])
            side = t["side"]
            pnl = float(t["realized_pnl"])
            nb_key = next_bar_ts(entry_ts)
            nb = bars.get(nb_key)
            if nb is None:
                continue
            nb_close = float(nb["close"])
            confirm = nb_close > entry_price if side == "long" else nb_close < entry_price
            rec = {"pnl": pnl, "is_win": pnl > 0}
            (year_conf if confirm else year_unconf).append(rec)
        cs = stats(year_conf)
        us = stats(year_unconf)
        print(f"  {year}: confirmed PF={cs['pf']:.3f} (n={cs['n']}), unconfirmed PF={us['pf']:.3f} (n={us['n']})")


if __name__ == "__main__":
    main()
