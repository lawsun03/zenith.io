"""
B45 Phase 1 — ORB opening-range width quality filter falsification.

Hypothesis: a narrow opening range (first 15 min of regular session) indicates low
pre-market conviction → false breakouts → early stop-outs (0-2h loser cohort).
A wide OR indicates decisive overnight move being digested → sustained directional
breakout → EOD-flatten winner (4h+ cohort, PF=4.129 per wk2-r4 data).

Method:
1. Load 1-min bars from yearly CSVs (2021/2023/2024/2025/2026, excl 2022 holdout).
2. Aggregate to 5-min bars per day.
3. Per day: OR high/low = max/min of 9:30-9:44 ET window (3 × 5-min bars).
4. ATR(14) = mean of 14 true-range values from 5-min bars ending just before 9:30 ET.
5. OR/ATR ratio = OR_width / ATR14.
6. Load mfe_mae_orb_clean.csv; match each trade to its day's OR/ATR ratio.
7. Bucket by OR/ATR quintile; compute WR, PF, net-PnL per bucket.
8. GO/NO-GO: wide (top 40%) PF >= 1.4x narrow (bottom 40%) AND n>=40 each bucket.
"""
from __future__ import annotations

import csv
import math
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ET = ZoneInfo("America/New_York")
YEARS = [2021, 2023, 2024, 2025, 2026]  # 2022 = frozen holdout
BARS_DIR = REPO / "bars" / "yearly"
MFE_MAE_PATH = REPO / "research" / "mfe_mae_orb_clean.csv"

OR_START_H, OR_START_M = 9, 30
OR_MINS = 15          # 15-min OR = 3 × 5-min bars (9:30, 9:35, 9:40 bars)
BAR_MINS = 5          # 5-min aggregation
ATR_PERIOD = 14       # 14 5-min bars before the OR window

MNQ_TICK = Decimal("0.25")   # not used but for reference
CONTRACT_MULT = Decimal("2")  # $2 per point per MNQ contract


# ---------------------------------------------------------------------------
# Step 1: Load 1-min bars and aggregate to 5-min bars
# ---------------------------------------------------------------------------

def _agg_key(ts_et: datetime) -> datetime:
    """Floor to 5-min ET boundary."""
    m = ts_et.minute - (ts_et.minute % BAR_MINS)
    return ts_et.replace(minute=m, second=0, microsecond=0)


def load_and_aggregate(year: int) -> list[dict]:
    """Return list of 5-min bar dicts for the given year.
    Keys: ts (datetime ET, bar-open time), open, high, low, close, date (ET date).
    """
    path = BARS_DIR / f"bars_MNQ_dbv_{year}.csv"
    buckets: dict[datetime, dict] = {}

    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_utc = datetime.fromisoformat(row["ts"])
            if ts_utc.tzinfo is None:
                ts_utc = ts_utc.replace(tzinfo=timezone.utc)
            ts_et = ts_utc.astimezone(ET)

            key = _agg_key(ts_et)
            h = Decimal(row["high"])
            l = Decimal(row["low"])
            c = Decimal(row["close"])
            o = Decimal(row["open"])

            if key not in buckets:
                buckets[key] = {
                    "ts": key,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "date": key.date(),
                }
            else:
                b = buckets[key]
                b["high"] = max(b["high"], h)
                b["low"] = min(b["low"], l)
                b["close"] = c  # last 1-min bar close

    return sorted(buckets.values(), key=lambda x: x["ts"])


# ---------------------------------------------------------------------------
# Step 2: Compute daily OR/ATR ratio
# ---------------------------------------------------------------------------

def compute_daily_or_atr(bars_5min: list[dict]) -> dict[date, dict]:
    """
    Returns {et_date: {"or_width": Decimal, "atr14": float, "ratio": float}}
    for all trading days that have a full OR window.
    """
    result: dict[date, dict] = {}

    # Pre-compute true range with prev_close
    trs = []  # list of (ts, tr) for ATR sliding window
    prev_close: Decimal | None = None

    # Group bars by date for easier scanning
    by_date: dict[date, list[dict]] = defaultdict(list)
    for bar in bars_5min:
        by_date[bar["date"]].append(bar)

    all_dates = sorted(by_date.keys())

    # Build a flat list of all 5-min bars sorted by ts for ATR computation
    all_bars_sorted = sorted(bars_5min, key=lambda b: b["ts"])

    # Build a ts → index lookup for efficient pre-open window extraction
    ts_to_idx = {b["ts"]: i for i, b in enumerate(all_bars_sorted)}

    # Compute TR for all bars
    tr_series: list[tuple[datetime, float]] = []
    prev_c: Decimal | None = None
    for bar in all_bars_sorted:
        h, l, c = bar["high"], bar["low"], bar["close"]
        if prev_c is None:
            tr = float(h - l)
        else:
            tr = float(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        tr_series.append((bar["ts"], tr))
        prev_c = c

    ts_to_tr_idx = {ts: i for i, (ts, _) in enumerate(tr_series)}

    for d in all_dates:
        day_bars = by_date[d]

        # OR window: bars with ts_et in [9:30, 9:45)
        or_open = datetime(d.year, d.month, d.day, OR_START_H, OR_START_M, tzinfo=ET)
        or_close = or_open + timedelta(minutes=OR_MINS)

        or_bars = [b for b in day_bars if or_open <= b["ts"] < or_close]
        if not or_bars:
            continue  # no OR bars on this day (holiday / no-data day)

        or_high = max(b["high"] for b in or_bars)
        or_low = min(b["low"] for b in or_bars)
        or_width = float(or_high - or_low)

        # ATR(14): take the 14 5-min bars immediately before OR start
        # Find index of first OR bar in global sorted list
        first_or_ts = or_bars[0]["ts"]
        if first_or_ts not in ts_to_idx:
            continue
        first_or_idx = ts_to_idx[first_or_ts]

        # ATR period: last ATR_PERIOD bars before this one
        if first_or_idx < ATR_PERIOD:
            continue  # not enough history

        atr_bars_trs = [tr_series[i][1] for i in range(first_or_idx - ATR_PERIOD, first_or_idx)]
        atr14 = sum(atr_bars_trs) / len(atr_bars_trs)

        if atr14 == 0:
            continue

        ratio = or_width / atr14

        result[d] = {
            "or_width": or_width,
            "atr14": atr14,
            "ratio": ratio,
            "or_high": float(or_high),
            "or_low": float(or_low),
        }

    return result


# ---------------------------------------------------------------------------
# Step 3: Load ORB trades and match to daily OR/ATR
# ---------------------------------------------------------------------------

def load_orb_trades() -> list[dict]:
    trades = []
    with open(MFE_MAE_PATH, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_utc = datetime.fromisoformat(row["entry_ts"])
            if ts_utc.tzinfo is None:
                ts_utc = ts_utc.replace(tzinfo=timezone.utc)
            ts_et = ts_utc.astimezone(ET)
            trade_date = ts_et.date()

            # Skip 2022 (frozen holdout)
            if trade_date.year == 2022:
                continue

            pnl = float(row["realized_pnl"])
            is_win = pnl > 0
            trades.append({
                "date": trade_date,
                "side": row["side"],
                "pnl": pnl,
                "win": is_win,
                "mfe_pts": float(row["mfe_pts"]),
                "mae_pts": float(row["mae_pts"]),
                "r_mfe": float(row["r_mfe"]),
                "r_mae": float(row["r_mae"]),
                "hold_seconds": int(row["hold_seconds"]),
            })
    return trades


# ---------------------------------------------------------------------------
# Step 4: Bucket analysis
# ---------------------------------------------------------------------------

def pf_from_trades(trades: list[dict]) -> tuple[float, int, float]:
    """Returns (PF, n, net_pnl)."""
    gross_win = sum(t["pnl"] for t in trades if t["win"])
    gross_loss = abs(sum(t["pnl"] for t in trades if not t["win"]))
    n = len(trades)
    net = gross_win - gross_loss
    pf = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    return pf, n, net


def wr_from_trades(trades: list[dict]) -> float:
    if not trades:
        return 0.0
    return sum(1 for t in trades if t["win"]) / len(trades)


def run_analysis() -> None:
    print("=== B45 Phase 1: ORB Opening-Range Width Quality Filter ===\n")

    # Load daily OR/ATR ratios across all years
    daily_ratio: dict[date, dict] = {}
    for year in YEARS:
        print(f"  Loading {year} bars and computing daily OR/ATR...")
        bars = load_and_aggregate(year)
        ratios = compute_daily_or_atr(bars)
        daily_ratio.update(ratios)
        print(f"    {len(ratios)} trading days with valid OR/ATR data")

    print(f"\n  Total days with OR/ATR: {len(daily_ratio)}")

    # Load ORB trades
    trades = load_orb_trades()
    print(f"  ORB trades loaded: {len(trades)}")

    # Match trades to daily OR/ATR ratio
    matched = []
    no_match = 0
    for t in trades:
        if t["date"] in daily_ratio:
            t["ratio"] = daily_ratio[t["date"]]["ratio"]
            t["or_width"] = daily_ratio[t["date"]]["or_width"]
            t["atr14"] = daily_ratio[t["date"]]["atr14"]
            matched.append(t)
        else:
            no_match += 1

    print(f"  Matched: {len(matched)}, no-match: {no_match}")
    if no_match > 20:
        print(f"  WARNING: {no_match} trades could not be matched to OR/ATR data")

    if not matched:
        print("ERROR: No trades matched. Check bars data.")
        return

    # Distribution of ratios
    ratios_all = sorted(t["ratio"] for t in matched)
    n_total = len(ratios_all)

    # Quintile bucket boundaries (5 equal-count buckets)
    def percentile(lst, p):
        idx = int(len(lst) * p / 100)
        return lst[min(idx, len(lst) - 1)]

    # Use 5 buckets (quintiles)
    q20 = percentile(ratios_all, 20)
    q40 = percentile(ratios_all, 40)
    q60 = percentile(ratios_all, 60)
    q80 = percentile(ratios_all, 80)

    bucket_defs = [
        (0, q20, f"Q1 narrowest (ratio < {q20:.2f})"),
        (q20, q40, f"Q2 (ratio {q20:.2f}-{q40:.2f})"),
        (q40, q60, f"Q3 (ratio {q40:.2f}-{q60:.2f})"),
        (q60, q80, f"Q4 (ratio {q60:.2f}-{q80:.2f})"),
        (q80, 1e9, f"Q5 widest (ratio > {q80:.2f})"),
    ]

    print(f"\n  Ratio distribution: p20={q20:.2f}, p40={q40:.2f}, p60={q60:.2f}, p80={q80:.2f}")

    print("\n=== Quintile Bucket Analysis ===")
    print(f"{'Bucket':<45} {'n':>5} {'WR%':>7} {'PF':>7} {'Net$':>9} {'AvgR_MFE':>9}")
    print("-" * 85)

    bucket_results = []
    for lo, hi, label in bucket_defs:
        bucket = [t for t in matched if lo <= t["ratio"] < hi]
        if not bucket:
            continue
        pf, n, net = pf_from_trades(bucket)
        wr = wr_from_trades(bucket)
        avg_mfe = sum(t["r_mfe"] for t in bucket) / n
        print(f"  {label:<43} {n:>5} {wr*100:>6.1f}% {pf:>7.3f} {net:>9,.0f} {avg_mfe:>9.3f}")
        bucket_results.append((lo, hi, label, n, wr, pf, net))

    # GO/NO-GO gate: wide (top 40% = Q4+Q5) vs narrow (bottom 40% = Q1+Q2)
    narrow_trades = [t for t in matched if t["ratio"] < q40]
    wide_trades = [t for t in matched if t["ratio"] >= q60]

    narrow_pf, narrow_n, narrow_net = pf_from_trades(narrow_trades)
    wide_pf, wide_n, wide_net = pf_from_trades(wide_trades)
    narrow_wr = wr_from_trades(narrow_trades)
    wide_wr = wr_from_trades(wide_trades)

    print("\n=== GO/NO-GO Criterion ===")
    print(f"  Bottom 40% (narrow OR, ratio < {q40:.2f}): n={narrow_n}, WR={narrow_wr*100:.1f}%, PF={narrow_pf:.3f}")
    print(f"  Top 40%    (wide OR,   ratio >= {q60:.2f}): n={wide_n},  WR={wide_wr*100:.1f}%, PF={wide_pf:.3f}")

    ratio_required = 1.4
    pf_ratio = wide_pf / narrow_pf if narrow_pf > 0 else 0

    print(f"\n  Wide/Narrow PF ratio: {pf_ratio:.3f} (need >= {ratio_required:.1f})")
    print(f"  Narrow n={narrow_n} (need >= 40), Wide n={wide_n} (need >= 40)")

    go = (pf_ratio >= ratio_required and narrow_n >= 40 and wide_n >= 40)
    print(f"\n  VERDICT: {'GO — Phase 2 code warranted' if go else 'NO-GO — Phase 2 NOT warranted'}")

    if not go:
        if pf_ratio < ratio_required:
            print(f"  Reason: PF ratio {pf_ratio:.3f} < {ratio_required:.1f} threshold")
        if narrow_n < 40 or wide_n < 40:
            print(f"  Reason: insufficient sample size in one or both buckets")

    # Additional breakdown: early vs late hold by OR width bucket
    print("\n=== Hold-Time Analysis by OR/ATR Bucket ===")
    print(f"  (Does wide OR -> more 4h+ winners? Early stops = hold_seconds < 7200)")
    print(f"{'Bucket':<25} {'n':>5} {'%early(<2h)':>12} {'%EOD(>4h)':>11}")
    print("-" * 60)
    for lo, hi, label in bucket_defs:
        bucket = [t for t in matched if lo <= t["ratio"] < hi]
        if not bucket:
            continue
        n = len(bucket)
        early = sum(1 for t in bucket if t["hold_seconds"] < 7200) / n * 100
        eod = sum(1 for t in bucket if t["hold_seconds"] >= 14400) / n * 100
        print(f"  {label[:23]:<25} {n:>5} {early:>11.1f}% {eod:>10.1f}%")

    # Simple 3-bucket (narrow / mid / wide) for clarity
    print("\n=== Simplified 3-Bucket Summary ===")
    q33 = percentile(ratios_all, 33)
    q67 = percentile(ratios_all, 67)
    three_buckets = [
        ([t for t in matched if t["ratio"] < q33], f"Narrow (ratio < {q33:.2f})"),
        ([t for t in matched if q33 <= t["ratio"] < q67], f"Mid ({q33:.2f}-{q67:.2f})"),
        ([t for t in matched if t["ratio"] >= q67], f"Wide (ratio >= {q67:.2f})"),
    ]
    for bucket, label in three_buckets:
        pf, n, net = pf_from_trades(bucket)
        wr = wr_from_trades(bucket)
        print(f"  {label:<30} n={n:>3}, WR={wr*100:.1f}%, PF={pf:.3f}, net={net:,.0f}")

    # Long/short breakdown for wide vs narrow
    print("\n=== Long/Short by Bucket ===")
    for subset_label, lo, hi in [("Narrow (Q1+Q2)", 0, q40), ("Wide (Q4+Q5)", q60, 1e9)]:
        longs = [t for t in matched if lo <= t["ratio"] < hi and t["side"] == "long"]
        shorts = [t for t in matched if lo <= t["ratio"] < hi and t["side"] == "short"]
        lpf, ln, lnet = pf_from_trades(longs)
        spf, sn, snet = pf_from_trades(shorts)
        print(f"  {subset_label}: long n={ln} PF={lpf:.3f}, short n={sn} PF={spf:.3f}")

    print("\n=== Summary Stats ===")
    print(f"  All matched trades: n={len(matched)}, overall PF={pf_from_trades(matched)[0]:.3f}")
    ratios_vals = [t["ratio"] for t in matched]
    print(f"  OR/ATR ratio stats: min={min(ratios_vals):.2f}, p25={percentile(sorted(ratios_vals), 25):.2f}, "
          f"median={percentile(sorted(ratios_vals), 50):.2f}, p75={percentile(sorted(ratios_vals), 75):.2f}, "
          f"max={max(ratios_vals):.2f}")

    return go


if __name__ == "__main__":
    go = run_analysis()
    sys.exit(0 if go else 1)
