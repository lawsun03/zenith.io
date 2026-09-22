"""Continuous-contract construction: volume-based roll, back-adjusted and
unadjusted series.

Databento gives us one raw price series per individual contract month. This
module stitches a chronological sequence of contracts into one continuous
series, using our own roll rule rather than Databento's built-in continuous
symbology — CLAUDE.md phase-0 scope asks for a *configurable* volume-based
roll, which Databento's `.c.0`/`.v.0` symbols don't expose control over.

Roll rule: roll to the back contract on the first date its volume exceeds
the front contract's, shifted by a configurable calendar-day offset.

Back-adjustment: additive ("Panama") — the most recent contract segment is
left untouched; every earlier segment is shifted by the cumulative sum of
the price jumps measured at each roll date, so the series has no artificial
gap at a roll while the current price level matches the live contract.
Additive (not ratio) adjustment is used because downstream stops and ATR
are point-based (CLAUDE.md domain invariant 4), not percentage-based.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import polars as pl


@dataclass(frozen=True)
class RollConfig:
    volume_offset_days: int = 0


@dataclass(frozen=True)
class RollEvent:
    from_contract: str
    to_contract: str
    crossover_date: date  # first date back-contract volume exceeds front's
    roll_date: date       # crossover_date + offset, snapped to a traded date


@dataclass(frozen=True)
class ContinuousResult:
    unadjusted: pl.DataFrame
    back_adjusted: pl.DataFrame
    roll_events: tuple[RollEvent, ...]


_BAR_SCHEMA = ("ts", "open", "high", "low", "close", "volume")


def _daily_volume(df: pl.DataFrame) -> pl.DataFrame:
    return (
        df.with_columns(pl.col("ts").dt.date().alias("date"))
        .group_by("date")
        .agg(pl.col("volume").sum())
        .sort("date")
    )


def _find_crossover(front: pl.DataFrame, back: pl.DataFrame) -> date | None:
    """First date, in the overlap window, that back's daily volume exceeds front's."""
    front_daily = _daily_volume(front)
    back_daily = _daily_volume(back)
    joined = front_daily.join(back_daily, on="date", how="inner", suffix="_back").sort("date")
    if joined.is_empty():
        return None
    crossed = joined.filter(pl.col("volume_back") > pl.col("volume"))
    if crossed.is_empty():
        return None
    return crossed[0, "date"]


def compute_roll_schedule(
    contracts: dict[str, pl.DataFrame],
    order: list[str],
    config: RollConfig,
) -> list[RollEvent]:
    """One RollEvent per adjacent pair in `order` (chronological, front to back)."""
    events: list[RollEvent] = []
    for front_sym, back_sym in zip(order, order[1:]):
        front = contracts[front_sym]
        back = contracts[back_sym]
        crossover = _find_crossover(front, back)
        if crossover is None:
            # No observed crossover (e.g. thin/synthetic data): fall back to
            # rolling at the front contract's last traded date so the series
            # still has full coverage.
            crossover = front["ts"].dt.date().max()

        roll_date = crossover + timedelta(days=config.volume_offset_days)

        back_dates = back["ts"].dt.date().unique().sort()
        on_or_after = back_dates.filter(back_dates >= roll_date)
        roll_date = on_or_after[0] if len(on_or_after) else back_dates[-1]

        events.append(RollEvent(front_sym, back_sym, crossover, roll_date))
    return events


def _segment_bounds(order: list[str], roll_events: list[RollEvent]) -> dict[str, tuple[date | None, date | None]]:
    """[start, end) effective-date window each contract contributes to the continuous series."""
    roll_dates = [e.roll_date for e in roll_events]
    bounds: dict[str, tuple[date | None, date | None]] = {}
    for i, sym in enumerate(order):
        start = roll_dates[i - 1] if i > 0 else None
        end = roll_dates[i] if i < len(roll_dates) else None
        bounds[sym] = (start, end)
    return bounds


def build_continuous(
    contracts: dict[str, pl.DataFrame],
    order: list[str],
    config: RollConfig,
    tick_size: Decimal,
) -> ContinuousResult:
    """Stitch `contracts` (front-to-back in `order`) into one continuous series.

    Each frame in `contracts` must have columns ts/open/high/low/close/volume,
    `ts` sorted ascending. Granularity-agnostic — works identically for
    1-minute or daily bars.
    """
    if len(order) < 1:
        raise ValueError("need at least one contract")

    roll_events = compute_roll_schedule(contracts, order, config)
    bounds = _segment_bounds(order, roll_events)

    unadjusted_parts = []
    for sym in order:
        start, end = bounds[sym]
        df = contracts[sym]
        d = df["ts"].dt.date()
        mask = pl.Series([True] * len(df))
        if start is not None:
            mask = mask & (d >= start)
        if end is not None:
            mask = mask & (d < end)
        seg = df.filter(mask).with_columns(pl.lit(sym).alias("contract"))
        unadjusted_parts.append(seg)
    unadjusted = pl.concat(unadjusted_parts).sort("ts")

    # Cumulative additive adjustment per segment: sum of (back_close - front_close)
    # at every roll date at or after this segment, using the CLOSE on the roll
    # date from each side of that roll.
    deltas = []
    for event in roll_events:
        front_df = contracts[event.from_contract]
        back_df = contracts[event.to_contract]
        front_close = _close_on(front_df, event.roll_date)
        back_close = _close_on(back_df, event.roll_date)
        deltas.append(float(back_close) - float(front_close))

    cumulative = [0.0] * len(order)
    running = 0.0
    for i in range(len(order) - 1, -1, -1):
        cumulative[i] = running
        if i > 0:
            running += deltas[i - 1]

    price_cols = ["open", "high", "low", "close"]
    adjusted_parts = []
    for i, sym in enumerate(order):
        seg = unadjusted_parts[i]
        adj = cumulative[i]
        if adj == 0.0:
            adjusted_parts.append(seg)
        else:
            adjusted_parts.append(seg.with_columns([(pl.col(c) + adj).alias(c) for c in price_cols]))
    back_adjusted = pl.concat(adjusted_parts).sort("ts")

    return ContinuousResult(unadjusted, back_adjusted, tuple(roll_events))


def _close_on(df: pl.DataFrame, d: date) -> float:
    day_rows = df.filter(df["ts"].dt.date() == d)
    if day_rows.is_empty():
        raise ValueError(f"no bars on {d} to anchor the roll adjustment")
    return day_rows[-1, "close"]
