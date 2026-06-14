"""
B54 Phase 1 data mining: ORB alignment with pre-RTH session direction.

For each ORB trade, compute the pre-RTH direction (08:30-09:30 ET window direction
on the same day) and check if the ORB signal aligns with it. The hypothesis:
08:30 economic data releases set the intraday direction; ORB signals that align
with this pre-RTH sentiment have better continuation.

Pre-RTH direction: close of the 09:30 ET bar vs open of the 08:30 ET bar.
Aligned: ORB long when pre-RTH is up; ORB short when pre-RTH is down.
Opposing: ORB long when pre-RTH is down; ORB short when pre-RTH is up.

GO/NO-GO: aligned PF >= 1.25x opposing PF AND each bucket n >= 40.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import csv
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

ORB_CSV = ROOT / "research" / "mfe_mae_orb_clean.csv"
BARS_CSV = ROOT / "bars" / "bars_MNQ_dbv_2021_2026.csv"
EXCLUDE_YEAR = 2022
ET = ZoneInfo("America/New_York")


def load_bars_indexed_by_et(path: Path) -> dict[tuple, dict]:
    """Load bars indexed by (et_date, et_hour, et_minute) → row dict."""
    idx = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
            et = ts.astimezone(ET)
            key = (et.date(), et.hour, et.minute)
            idx[key] = row
    return idx


def get_prertth_direction(bars_et: dict, trade_date) -> str | None:
    """
    Return 'up' or 'down' based on the 08:30-09:30 ET window.
    We use the open of the first 5-min bar at 08:30 ET (labeled at 08:35 close)
    and the close of the bar at 09:30 ET (labeled at 09:35 close, or 09:30 itself).

    Bars are labeled at their CLOSE time. So the bar labeled 08:35 covers 08:30-08:35.
    Pre-RTH direction = close at 09:25 ET bar vs open at 08:35 ET bar.
    (Using 09:25 because 09:30 is the first RTH bar, which is the ORB formation bar.)
    """
    # First pre-RTH bar: labeled at 08:35 ET (covers 08:30-08:35)
    first_bar = bars_et.get((trade_date, 8, 35))
    if first_bar is None:
        # Try 08:30 (might be labeled at close = 08:30 exactly for some data conventions)
        first_bar = bars_et.get((trade_date, 8, 30))
    if first_bar is None:
        return None

    # Last pre-RTH bar: labeled at 09:25 ET (covers 09:20-09:25; just before RTH open)
    last_bar = bars_et.get((trade_date, 9, 25))
    if last_bar is None:
        # Try 09:30 (covers 09:25-09:30)
        last_bar = bars_et.get((trade_date, 9, 30))
    if last_bar is None:
        return None

    open_price = float(first_bar["open"])
    close_price = float(last_bar["close"])

    if close_price > open_price:
        return "up"
    elif close_price < open_price:
        return "down"
    else:
        return "flat"


def main():
    print("Loading bars by ET time...")
    bars_et = load_bars_indexed_by_et(BARS_CSV)
    print(f"Loaded {len(bars_et):,} bar-time entries")

    trades = []
    with open(ORB_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if int(row["year"]) == EXCLUDE_YEAR:
                continue
            trades.append(row)

    print(f"ORB trades (excl {EXCLUDE_YEAR}): {len(trades)}")

    aligned = []
    opposing = []
    flat_dir = []
    no_prertth = []

    for t in trades:
        entry_ts = datetime.fromisoformat(t["entry_ts"].replace("Z", "+00:00"))
        et_date = entry_ts.astimezone(ET).date()
        side = t["side"]
        pnl = float(t["realized_pnl"])
        rec = {"pnl": pnl, "is_win": pnl > 0}

        direction = get_prertth_direction(bars_et, et_date)
        if direction is None:
            no_prertth.append(t)
            continue
        if direction == "flat":
            flat_dir.append(rec)
            continue

        orb_agrees = (
            (side == "long" and direction == "up") or
            (side == "short" and direction == "down")
        )
        if orb_agrees:
            aligned.append(rec)
        else:
            opposing.append(rec)

    print(f"\nAligned: {len(aligned)}, Opposing: {len(opposing)}, Flat: {len(flat_dir)}, No pre-RTH data: {len(no_prertth)}")

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

    all_stats = stats(aligned + opposing + flat_dir)
    aligned_stats = stats(aligned)
    opposing_stats = stats(opposing)

    print("\n=== Pre-RTH Direction Alignment Results ===")
    print(f"{'Subset':<22} {'n':>6} {'WR%':>7} {'PF':>6} {'Net$':>12}")
    print("-" * 57)
    for name, s in [("All (excl 2022)", all_stats), ("Aligned", aligned_stats), ("Opposing", opposing_stats)]:
        print(f"{name:<22} {s['n']:>6} {s['wr']:>7.1f} {s['pf']:>6.3f} {s['net']:>12,.0f}")

    if opposing_stats["pf"] > 0 and aligned_stats["n"] >= 40 and opposing_stats["n"] >= 40:
        ratio = aligned_stats["pf"] / opposing_stats["pf"]
        print(f"\nAligned PF / Opposing PF = {ratio:.3f} (GO threshold: >= 1.25)")
        go = ratio >= 1.25
        print(f"\nPhase 1 verdict: {'GO' if go else 'NO-GO'}")
    else:
        print("\nInsufficient data for GO/NO-GO assessment")

    # Per-year breakdown
    print("\n=== Per-year aligned vs opposing PF ===")
    for year in [2021, 2023, 2024, 2025, 2026]:
        year_trades = [t for t in trades if int(t["year"]) == year]
        year_aligned = []
        year_opposing = []
        for t in year_trades:
            entry_ts = datetime.fromisoformat(t["entry_ts"].replace("Z", "+00:00"))
            et_date = entry_ts.astimezone(ET).date()
            direction = get_prertth_direction(bars_et, et_date)
            if direction is None or direction == "flat":
                continue
            side = t["side"]
            pnl = float(t["realized_pnl"])
            rec = {"pnl": pnl, "is_win": pnl > 0}
            agrees = (side == "long" and direction == "up") or (side == "short" and direction == "down")
            (year_aligned if agrees else year_opposing).append(rec)
        a = stats(year_aligned)
        o = stats(year_opposing)
        print(f"  {year}: aligned PF={a['pf']:.3f} (n={a['n']}), opposing PF={o['pf']:.3f} (n={o['n']})")


if __name__ == "__main__":
    main()
