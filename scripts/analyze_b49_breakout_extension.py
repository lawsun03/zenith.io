"""
B49 Phase 1: ORB breakout extension quality filter falsification.

Hypothesis: when the signal bar closes far past the OR boundary (large extension
measured in ATR units), it signals stronger momentum and predicts a better outcome
(fewer false breakouts, more EOD-flatten winners). Shallow closures — just barely
beyond the boundary — produce noise trades → early stop-outs.

Method:
1. Load 1-min bars from yearly CSVs (2021/2023/2024/2025/2026, excl 2022 holdout).
2. Aggregate to 5-min bars per day (open-time labelled; same as B45).
3. Per day: OR high/low from bars in [9:30, 9:45) ET; ATR(14) from 14 pre-OR bars.
4. Per trade: extension = |entry_price - or_boundary| / atr14
   - Long: extension = (entry_price - or_high) / atr14  [or_high is the broken level]
   - Short: extension = (or_low - entry_price) / atr14  [or_low is the broken level]
5. Bucket by extension quintile; compute WR/PF/net-PnL per bucket.
6. GO/NO-GO: top 40% extension PF >= 1.4x bottom 40% extension PF AND n >= 40 each.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
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


# ---------------------------------------------------------------------------
# Step 1: Load 1-min bars and aggregate to 5-min bars (open-time labelled)
# ---------------------------------------------------------------------------

def _agg_key(ts_et: datetime) -> datetime:
    """Floor to 5-min ET boundary (bar-open label)."""
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
                b["close"] = c

    return sorted(buckets.values(), key=lambda x: x["ts"])


# ---------------------------------------------------------------------------
# Step 2: Compute daily OR high/low and ATR(14)
# ---------------------------------------------------------------------------

def compute_daily_or_atr(bars_5min: list[dict]) -> dict[date, dict]:
    """
    Returns {et_date: {or_high, or_low, or_width, atr14}} for all valid trading days.
    """
    result: dict[date, dict] = {}

    by_date: dict[date, list[dict]] = defaultdict(list)
    for bar in bars_5min:
        by_date[bar["date"]].append(bar)

    all_bars_sorted = sorted(bars_5min, key=lambda b: b["ts"])
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

    for d in sorted(by_date.keys()):
        day_bars = by_date[d]

        or_open = datetime(d.year, d.month, d.day, OR_START_H, OR_START_M, tzinfo=ET)
        or_close = or_open + timedelta(minutes=OR_MINS)

        or_bars = [b for b in day_bars if or_open <= b["ts"] < or_close]
        if not or_bars:
            continue

        or_high = max(b["high"] for b in or_bars)
        or_low = min(b["low"] for b in or_bars)
        or_width = float(or_high - or_low)

        first_or_ts = or_bars[0]["ts"]
        if first_or_ts not in ts_to_idx:
            continue
        first_or_idx = ts_to_idx[first_or_ts]

        if first_or_idx < ATR_PERIOD:
            continue

        atr14 = sum(tr_series[i][1] for i in range(first_or_idx - ATR_PERIOD, first_or_idx)) / ATR_PERIOD

        if atr14 == 0:
            continue

        result[d] = {
            "or_high": float(or_high),
            "or_low": float(or_low),
            "or_width": or_width,
            "atr14": atr14,
        }

    return result


# ---------------------------------------------------------------------------
# Step 3: Load ORB trades
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

            if trade_date.year == 2022:
                continue

            pnl = float(row["realized_pnl"])
            trades.append({
                "date": trade_date,
                "side": row["side"],
                "entry_price": float(row["entry_price"]),
                "pnl": pnl,
                "win": pnl > 0,
                "hold_seconds": int(row["hold_seconds"]),
                "r_mfe": float(row["r_mfe"]),
                "r_mae": float(row["r_mae"]),
            })
    return trades


# ---------------------------------------------------------------------------
# Step 4: Analysis helpers
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


def percentile(lst: list, p: float):
    idx = int(len(lst) * p / 100)
    return lst[min(idx, len(lst) - 1)]


# ---------------------------------------------------------------------------
# Step 5: Main analysis
# ---------------------------------------------------------------------------

def run_analysis() -> bool:
    print("=== B49 Phase 1: ORB Breakout Extension Quality Filter ===\n")

    daily_data: dict[date, dict] = {}
    for year in YEARS:
        print(f"  Loading {year} bars and computing daily OR/ATR...")
        bars = load_and_aggregate(year)
        day_data = compute_daily_or_atr(bars)
        daily_data.update(day_data)
        print(f"    {len(day_data)} trading days with valid OR/ATR data")

    print(f"\n  Total days with OR/ATR: {len(daily_data)}")

    trades = load_orb_trades()
    print(f"  ORB trades loaded: {len(trades)}")

    # Compute per-trade extension: how far entry price cleared the OR boundary in ATR units
    matched = []
    no_match = 0
    neg_ext = 0
    for t in trades:
        d = t["date"]
        if d not in daily_data:
            no_match += 1
            continue
        dd = daily_data[d]
        if t["side"] == "long":
            ext = (t["entry_price"] - dd["or_high"]) / dd["atr14"]
        else:
            ext = (dd["or_low"] - t["entry_price"]) / dd["atr14"]
        if ext < 0:
            neg_ext += 1
        t["extension"] = ext
        t["or_high"] = dd["or_high"]
        t["or_low"] = dd["or_low"]
        t["atr14"] = dd["atr14"]
        matched.append(t)

    print(f"  Matched: {len(matched)}, no-match: {no_match}, negative-ext: {neg_ext}")
    if no_match > 20:
        print(f"  WARNING: {no_match} trades could not be matched")
    if neg_ext > 0:
        print(f"  NOTE: {neg_ext} trades with negative extension (entry below OR boundary) — included in analysis")

    if not matched:
        print("ERROR: No trades matched. Check bars data.")
        return False

    # Extension distribution
    exts_all = sorted(t["extension"] for t in matched)
    q20 = percentile(exts_all, 20)
    q40 = percentile(exts_all, 40)
    q60 = percentile(exts_all, 60)
    q80 = percentile(exts_all, 80)

    print(f"\n  Extension distribution (ATR units): "
          f"p20={q20:.3f}, p40={q40:.3f}, p60={q60:.3f}, p80={q80:.3f}")
    print(f"  Range: min={exts_all[0]:.3f}, max={exts_all[-1]:.3f}")

    bucket_defs = [
        (None, q20, f"Q1 shallowest (ext < {q20:.3f})"),
        (q20, q40, f"Q2 (ext {q20:.3f}–{q40:.3f})"),
        (q40, q60, f"Q3 (ext {q40:.3f}–{q60:.3f})"),
        (q60, q80, f"Q4 (ext {q60:.3f}–{q80:.3f})"),
        (q80, None, f"Q5 deepest (ext > {q80:.3f})"),
    ]

    print("\n=== Quintile Bucket Analysis ===")
    print(f"{'Bucket':<48} {'n':>5} {'WR%':>7} {'PF':>7} {'Net$':>9} {'AvgRmfe':>8}")
    print("-" * 88)

    for lo, hi, label in bucket_defs:
        if lo is None:
            bucket = [t for t in matched if t["extension"] < hi]
        elif hi is None:
            bucket = [t for t in matched if t["extension"] >= lo]
        else:
            bucket = [t for t in matched if lo <= t["extension"] < hi]
        if not bucket:
            continue
        pf, n, net = pf_from_trades(bucket)
        wr = wr_from_trades(bucket)
        avg_rmfe = sum(t["r_mfe"] for t in bucket) / n
        print(f"  {label:<46} {n:>5} {wr*100:>6.1f}% {pf:>7.3f} {net:>9,.0f} {avg_rmfe:>8.3f}")

    # GO/NO-GO: bottom 40% (Q1+Q2) vs top 40% (Q4+Q5)
    shallow_trades = [t for t in matched if t["extension"] < q40]
    deep_trades = [t for t in matched if t["extension"] >= q60]

    shallow_pf, shallow_n, shallow_net = pf_from_trades(shallow_trades)
    deep_pf, deep_n, deep_net = pf_from_trades(deep_trades)
    shallow_wr = wr_from_trades(shallow_trades)
    deep_wr = wr_from_trades(deep_trades)

    print("\n=== GO/NO-GO Criterion ===")
    print(f"  Bottom 40% (shallow, ext < {q40:.3f}): n={shallow_n}, WR={shallow_wr*100:.1f}%, PF={shallow_pf:.3f}")
    print(f"  Top 40%    (deep,    ext >= {q60:.3f}): n={deep_n},  WR={deep_wr*100:.1f}%, PF={deep_pf:.3f}")

    pf_ratio = deep_pf / shallow_pf if shallow_pf > 0 else 0.0
    go = pf_ratio >= 1.4 and shallow_n >= 40 and deep_n >= 40

    print(f"\n  Deep/Shallow PF ratio: {pf_ratio:.3f} (need >= 1.4)")
    print(f"  Shallow n={shallow_n} (need >= 40), Deep n={deep_n} (need >= 40)")
    print(f"\n  VERDICT: {'GO — Phase 2 code warranted' if go else 'NO-GO — Phase 2 NOT warranted'}")
    if not go:
        if pf_ratio < 1.4:
            print(f"  Reason: PF ratio {pf_ratio:.3f} < 1.4 threshold")
        if shallow_n < 40 or deep_n < 40:
            print(f"  Reason: insufficient sample size")

    # Hold-time breakdown by extension bucket
    print("\n=== Hold-Time by Extension Bucket ===")
    print(f"  (EOD = hold >= 4h; early = hold < 2h)")
    print(f"{'Bucket':<25} {'n':>5} {'%early(<2h)':>12} {'%EOD(>4h)':>11}")
    print("-" * 58)
    for lo, hi, label in bucket_defs:
        if lo is None:
            bucket = [t for t in matched if t["extension"] < hi]
        elif hi is None:
            bucket = [t for t in matched if t["extension"] >= lo]
        else:
            bucket = [t for t in matched if lo <= t["extension"] < hi]
        if not bucket:
            continue
        n = len(bucket)
        early = sum(1 for t in bucket if t["hold_seconds"] < 7200) / n * 100
        eod = sum(1 for t in bucket if t["hold_seconds"] >= 14400) / n * 100
        print(f"  {label[:23]:<25} {n:>5} {early:>11.1f}% {eod:>10.1f}%")

    # Long/short breakdown
    print("\n=== Long/Short by Extension Bucket ===")
    for subset_label, lo, hi in [
        ("Shallow (Q1+Q2)", None, q40),
        ("Deep (Q4+Q5)", q60, None),
    ]:
        if lo is None:
            longs = [t for t in matched if t["extension"] < hi and t["side"] == "long"]
            shorts = [t for t in matched if t["extension"] < hi and t["side"] == "short"]
        else:
            longs = [t for t in matched if t["extension"] >= lo and t["side"] == "long"]
            shorts = [t for t in matched if t["extension"] >= lo and t["side"] == "short"]
        lpf, ln, _ = pf_from_trades(longs)
        spf, sn, _ = pf_from_trades(shorts)
        print(f"  {subset_label}: long n={ln} PF={lpf:.3f},  short n={sn} PF={spf:.3f}")

    # Per-year breakdown
    print("\n=== Per-Year Summary ===")
    print(f"{'Year':<8} {'n':>5} {'WR%':>7} {'PF':>7} {'Net$':>9} {'avg_ext':>8}")
    print("-" * 50)
    for yr in sorted(set(t["date"].year for t in matched)):
        yt = [t for t in matched if t["date"].year == yr]
        pf, n, net = pf_from_trades(yt)
        wr = wr_from_trades(yt)
        avg_ext = sum(t["extension"] for t in yt) / n
        print(f"  {yr:<6} {n:>5} {wr*100:>6.1f}% {pf:>7.3f} {net:>9,.0f} {avg_ext:>8.3f}")

    print("\n=== Summary Stats ===")
    all_pf, all_n, all_net = pf_from_trades(matched)
    all_wr = wr_from_trades(matched)
    print(f"  All matched: n={all_n}, WR={all_wr*100:.1f}%, PF={all_pf:.3f}, net={all_net:,.0f}")
    print(f"  Extension stats: min={exts_all[0]:.3f}, "
          f"p25={percentile(exts_all, 25):.3f}, "
          f"median={percentile(exts_all, 50):.3f}, "
          f"p75={percentile(exts_all, 75):.3f}, "
          f"max={exts_all[-1]:.3f}")

    return go


if __name__ == "__main__":
    go = run_analysis()
    sys.exit(0 if go else 1)
