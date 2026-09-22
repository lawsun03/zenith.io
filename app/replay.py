"""
Bar replay helper for paper mode.

Reads OHLCV CSVs and yields Bar events, suitable for running the full
strategy/risk/execution loop against historical /GC data.

Expected CSV columns (case-insensitive, common variants accepted):
  timestamp/ts/datetime   ISO 8601 string, ideally tz-aware
  open
  high
  low
  close
  volume                  (optional; defaults to 0)

Use this from main.py when TOPSTEP_BOT_PAPER_BARS is set:

    bars = list(load_bars_csv(path, instrument, timeframe))
    for bar in bars:
        await broker.inject_bar(bar)

Memory note: this loads the full file into memory. For multi-day /GC
1-minute data that's fine (~10K bars/day × 5 days = 50K rows). If you
ever need to stream months of tick data, swap to an iterator.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from app.sim.events import Bar


_TS_KEYS = ("timestamp", "ts", "datetime", "date_time", "time")
_OHLC = {
    "open":   ("open", "o"),
    "high":   ("high", "h"),
    "low":    ("low", "l"),
    "close":  ("close", "c"),
    "volume": ("volume", "vol", "v"),
}


def _pick(row: dict[str, str], candidates: tuple[str, ...]) -> str | None:
    """Find the first candidate present in the row, case-insensitive."""
    lowered = {k.lower(): v for k, v in row.items()}
    for c in candidates:
        if c in lowered:
            return lowered[c]
    return None


def _parse_ts(raw: str) -> datetime:
    """
    Accept ISO 8601 in any of the common shapes our backtester emits.
    Naive timestamps are assumed UTC — which is wrong if your CSV is
    in ET, so prefer tz-aware exports.
    """
    raw = raw.strip()
    # datetime.fromisoformat handles "2026-05-11T13:30:00+00:00" and
    # "2026-05-11 13:30:00" but not trailing "Z" before Python 3.11+.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    ts = datetime.fromisoformat(raw)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _timeframe_minutes(tf: str) -> int:
    """'1min' → 1, '5min' → 5, '1h' → 60, etc."""
    tf = tf.strip().lower()
    if tf.endswith("h"):
        return int(tf[:-1]) * 60
    if tf.endswith("min"):
        return int(tf[:-3])
    return 1


def _bucket_floor(ts: datetime, minutes: int) -> datetime:
    """Truncate a timestamp to the nearest N-minute boundary."""
    total_minutes = ts.hour * 60 + ts.minute
    floored = (total_minutes // minutes) * minutes
    return ts.replace(hour=floored // 60, minute=floored % 60, second=0, microsecond=0)


def _merge(bars: list[Bar], timeframe: str) -> Bar:
    return Bar(
        instrument=bars[0].instrument,
        timeframe=timeframe,
        ts=bars[-1].ts,
        open=bars[0].open,
        high=max(b.high for b in bars),
        low=min(b.low for b in bars),
        close=bars[-1].close,
        volume=sum(b.volume for b in bars),
    )


def load_bars_csv(
    path: str | Path,
    instrument: str,
    timeframe: str = "1min",
) -> Iterator[Bar]:
    """
    Stream Bar events from a CSV file, resampling to `timeframe` on the fly.

    The CSV is assumed to contain 1-minute bars (or any base granularity).
    If `timeframe` is larger (e.g. '5min'), consecutive rows are bucketed
    and merged into OHLCV aggregates before being yielded.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Bar CSV not found: {p}")

    minutes = _timeframe_minutes(timeframe)
    bucket: list[Bar] = []
    bucket_floor: datetime | None = None

    with p.open() as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            ts_raw = _pick(row, _TS_KEYS)
            if ts_raw is None:
                raise ValueError(
                    f"Row {i}: no timestamp column "
                    f"(looked for {', '.join(_TS_KEYS)})"
                )
            o = _pick(row, _OHLC["open"])
            h = _pick(row, _OHLC["high"])
            l = _pick(row, _OHLC["low"])
            c = _pick(row, _OHLC["close"])
            v = _pick(row, _OHLC["volume"]) or "0"
            if None in (o, h, l, c):
                raise ValueError(f"Row {i}: missing OHLC ({row!r})")

            bar = Bar(
                instrument=instrument,
                timeframe=timeframe,
                ts=_parse_ts(ts_raw),
                open=Decimal(o),
                high=Decimal(h),
                low=Decimal(l),
                close=Decimal(c),
                volume=int(float(v)),
            )

            if minutes <= 1:
                yield bar
                continue

            floor = _bucket_floor(bar.ts, minutes)
            if bucket_floor is None:
                bucket_floor = floor

            if floor != bucket_floor:
                if bucket:
                    yield _merge(bucket, timeframe)
                bucket = []
                bucket_floor = floor

            bucket.append(bar)

    if bucket and minutes > 1:
        yield _merge(bucket, timeframe)
