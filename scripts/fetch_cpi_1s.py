"""Fetch Databento ohlcv-1s for CPI event windows only (cheap, budget-gated).

For each CPI release in data/news_events.csv, pull NQ.v.0 1-second bars for
[event-30min, event+90min] and write one concatenated CSV. Estimates the TOTAL
cost first and ABORTS if it exceeds --max-cost (protocol: estimate before spend).
Used to resolve the CPI straddle's entry-bar intra-minute path (B85).
"""
from __future__ import annotations
import argparse, csv, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pandas as pd

DATASET = "GLBX.MDP3"; SCALE = 1_000_000_000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NQ.v.0")
    ap.add_argument("--event-type", default="CPI")
    ap.add_argument("--pre-min", type=int, default=30)
    ap.add_argument("--post-min", type=int, default=90)
    ap.add_argument("--out", default="bars/bars_NQ_1s_cpi_windows.csv")
    ap.add_argument("--max-cost", type=float, default=3.0)
    ap.add_argument("--estimate-only", action="store_true")
    a = ap.parse_args()

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        print("DATABENTO_API_KEY not set", file=sys.stderr); sys.exit(1)
    import databento as db
    cli = db.Historical(key)

    ev = pd.read_csv("data/news_events.csv", parse_dates=["ts_utc"])
    ev = ev[(ev.event_type == a.event_type) & (ev.ts_utc.dt.year != 2022)
            & (ev.ts_utc >= "2021-06-13") & (ev.ts_utc <= "2026-06-11")]
    windows = [(e - timedelta(minutes=a.pre_min), e + timedelta(minutes=a.post_min)) for e in ev.ts_utc]
    print(f"{a.event_type} windows: {len(windows)}  schema=ohlcv-1s  symbol={a.symbol}")

    total = 0.0
    for s, en in windows:
        total += cli.metadata.get_cost(dataset=DATASET, symbols=[a.symbol], schema="ohlcv-1s",
                                       stype_in="continuous", start=s.isoformat(), end=en.isoformat())
    print(f"estimated total cost: ${total:.4f}  (cap ${a.max_cost})")
    if a.estimate_only:
        return
    if total > a.max_cost:
        print(f"ABORT: ${total:.4f} exceeds --max-cost ${a.max_cost}", file=sys.stderr); sys.exit(2)

    out = Path(a.out); out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["ts", "open", "high", "low", "close", "volume"])
        for s, en in windows:
            data = cli.timeseries.get_range(dataset=DATASET, symbols=[a.symbol], schema="ohlcv-1s",
                                            stype_in="continuous", start=s.isoformat(), end=en.isoformat())
            for r in data:
                ts = datetime.fromtimestamp(r.ts_event / SCALE, tz=timezone.utc).isoformat()
                w.writerow([ts, f"{r.open/SCALE:.4f}", f"{r.high/SCALE:.4f}",
                            f"{r.low/SCALE:.4f}", f"{r.close/SCALE:.4f}", r.volume]); n += 1
    print(f"wrote {n} 1s bars -> {a.out}  | actual cost ${total:.4f}")


if __name__ == "__main__":
    main()
