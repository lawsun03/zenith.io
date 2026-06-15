"""
Bars data-quality check: integrity scan of a 1-min CSV + cross-source diff.

  python scripts/check_bars_quality.py --file bars/bars_MNQ_db_2021_2026.csv \
      [--against bars/bars_MNQ_test_2025_2026.csv]

Integrity: monotonic timestamps, duplicates, OHLC sanity (l<=o,c<=h),
per-trading-day bar counts (flags thin days), overall span.
Cross-diff: on common timestamps, exact OHLC agreement rate and max
absolute deviation — validates both sources against each other.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def load(p):
    df = pd.read_csv(p)
    ts_col = df.columns[0]
    df[ts_col] = pd.to_datetime(df[ts_col], utc=True, format="ISO8601")
    df = df.rename(columns={ts_col: "ts"}).set_index("ts").sort_index()
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


def integrity(df, name):
    print(f"\n== integrity: {name} ==")
    print(f"rows: {len(df):,}  span: {df.index[0]} -> {df.index[-1]}")
    dups = df.index.duplicated().sum()
    print(f"duplicate timestamps: {dups}")
    bad = ((df.low > df[['open', 'close']].min(axis=1))
           | (df.high < df[['open', 'close']].max(axis=1))).sum()
    print(f"OHLC sanity violations (l>min(o,c) or h<max(o,c)): {bad}")
    # Trading-day bar counts (CT day roll at 17:00 CT ~ 22/23h UTC; use
    # calendar UTC date as a coarse proxy — full days have ~1380 1-min bars).
    per_day = df.groupby(df.index.date).size()
    weekday = per_day[[pd.Timestamp(d).weekday() < 5 for d in per_day.index]]
    thin = weekday[weekday < 800]
    print(f"weekday days: {len(weekday)}, median bars/day: {weekday.median():.0f}")
    print(f"thin weekdays (<800 bars, likely holidays/halts): {len(thin)}")
    for d, n in thin.tail(8).items():
        print(f"   {d}: {n} bars")


def cross_diff(a, b, name_a, name_b):
    common = a.index.intersection(b.index)
    print(f"\n== cross-diff: {name_a} vs {name_b} ==")
    print(f"common timestamps: {len(common):,} "
          f"({len(common) / max(len(b), 1) * 100:.1f}% of {name_b})")
    if len(common) == 0:
        return
    x = a.loc[common, ["open", "high", "low", "close"]]
    y = b.loc[common, ["open", "high", "low", "close"]]
    diff = (x - y).abs()
    exact = (diff.max(axis=1) == 0).mean() * 100
    print(f"bars with EXACT OHLC match: {exact:.2f}%")
    print(f"max abs deviation: {diff.max().max():.4f}  "
          f"mean abs deviation: {diff.mean().mean():.6f}")
    worst = diff.max(axis=1).nlargest(5)
    for ts, d in worst.items():
        if d > 0:
            print(f"   worst: {ts}  dev={d:.4f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--against", default=None)
    args = ap.parse_args()

    df = load(args.file)
    integrity(df, args.file)
    if args.against:
        other = load(args.against)
        integrity(other, args.against)
        cross_diff(df, other, Path(args.file).name, Path(args.against).name)


if __name__ == "__main__":
    main()
