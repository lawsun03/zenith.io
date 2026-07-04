"""
Order-flow primitives — trade-level delta / CVD and the ORB confirmation gate.

Why this exists: a breakout strategy (ORB) cannot tell a real expansion from a
stop-run that merely *closes* beyond the range edge and then reverses. Aggressor
delta can: a genuine breakout is driven by aggressive trades in the breakout
direction; a liquidity grab prints price beyond the edge on flat or opposite
delta. This module turns Databento `trades` (each print tagged with the
aggressing side) into per-minute delta/CVD and exposes a deterministic gate the
ORB detector consults.

Deterministic by design (CLAUDE.md Rule 5): all delta / CVD math is plain
arithmetic — never inferred, never delegated to a model.

Databento `trades` schema `side` field (aggressor side):
    'A' — aggressor bought (lifted the ask)  → buy volume
    'B' — aggressor sold   (hit the bid)     → sell volume
    'N' — none/unknown                       → neither (counted in total only)
"""
from __future__ import annotations

import csv
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

BUY_SIDES = {"A", "a"}
SELL_SIDES = {"B", "b"}

# CSV columns for the per-minute order-flow sidecar. Keyed to a bar's minute so
# the backtest runner can align it with the OHLCV bar of the same timestamp.
CSV_HEADER = ["ts", "buy_vol", "sell_vol", "delta", "cvd", "total_vol"]


def minute_key(ts: datetime) -> datetime:
    """Normalize any timestamp to its UTC minute — the join key between an
    OHLCV bar and its order-flow row. Naive timestamps are assumed UTC."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc).replace(second=0, microsecond=0)


@dataclass(frozen=True)
class OrderFlowBar:
    """Per-minute aggressor summary. `delta` = buy_vol - sell_vol; `cvd` is the
    running total of delta from the start of the series through this minute."""

    ts: datetime      # UTC minute bucket
    buy_vol: int
    sell_vol: int
    delta: int
    cvd: int
    total_vol: int


def aggregate_trades(prints: Iterable[tuple[datetime, int, str]]) -> list[OrderFlowBar]:
    """
    Aggregate trade prints into per-minute OrderFlowBars with a running CVD.

    `prints` is an iterable of (timestamp, size, side) where side is a Databento
    aggressor code. Prints may arrive unsorted; buckets are emitted in
    chronological order and CVD accumulates across them in that order.
    """
    buckets: dict[datetime, list[int]] = {}  # minute -> [buy, sell, total]
    for ts, size, side in prints:
        key = minute_key(ts)
        b = buckets.setdefault(key, [0, 0, 0])
        if side in BUY_SIDES:
            b[0] += size
        elif side in SELL_SIDES:
            b[1] += size
        b[2] += size

    out: list[OrderFlowBar] = []
    cvd = 0
    for key in sorted(buckets):
        buy, sell, total = buckets[key]
        delta = buy - sell
        cvd += delta
        out.append(OrderFlowBar(ts=key, buy_vol=buy, sell_vol=sell,
                                delta=delta, cvd=cvd, total_vol=total))
    return out


def write_csv(bars: Iterable[OrderFlowBar], path: str | Path, append: bool = False) -> int:
    """Write OrderFlowBars to the sidecar CSV. Returns rows written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_header = not append or not p.exists() or p.stat().st_size == 0
    n = 0
    with p.open("a" if append else "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if write_header:
            w.writerow(CSV_HEADER)
        for b in bars:
            w.writerow([b.ts.isoformat(), b.buy_vol, b.sell_vol,
                        b.delta, b.cvd, b.total_vol])
            n += 1
    return n


class OrderFlowSeries:
    """Minute-keyed lookup of OrderFlowBars, injected into the ORB detector.

    Mirrors the session_ctx / alignment_ctx injection pattern: the detector
    holds an optional reference and consults it only when the gate is enabled.
    """

    def __init__(self, bars: Iterable[OrderFlowBar]) -> None:
        self._by_minute: "OrderedDict[datetime, OrderFlowBar]" = OrderedDict(
            (b.ts, b) for b in bars
        )

    def for_bar(self, ts: datetime) -> OrderFlowBar | None:
        """Order flow for the bar closing at `ts`, or None if unavailable."""
        return self._by_minute.get(minute_key(ts))

    def __len__(self) -> int:
        return len(self._by_minute)

    @classmethod
    def from_csv(cls, path: str | Path) -> "OrderFlowSeries":
        p = Path(path)
        bars: list[OrderFlowBar] = []
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                bars.append(OrderFlowBar(
                    ts=minute_key(datetime.fromisoformat(row["ts"])),
                    buy_vol=int(row["buy_vol"]),
                    sell_vol=int(row["sell_vol"]),
                    delta=int(row["delta"]),
                    cvd=int(row["cvd"]),
                    total_vol=int(row["total_vol"]),
                ))
        return cls(bars)


def order_flow_gate(
    side: str, of_bar: OrderFlowBar | None, min_delta: int
) -> tuple[bool, str]:
    """
    Should an ORB breakout be allowed given the breakout bar's order flow?

    Returns (allow, reason). A long breakout needs aggressive *buying*
    (delta >= +min_delta); a short needs aggressive *selling* (delta <= -min_delta).
    A breakout that closes beyond the edge on flat/opposite delta is the
    stop-run signature and is blocked.

    Graceful fallback (mirrors ORB's B82 PM-break gate): when no order-flow row
    exists for the bar, allow the signal rather than silently killing the day —
    missing data must not masquerade as a failed confirmation.
    """
    if of_bar is None:
        return True, "no_data"
    if side == "long":
        if of_bar.delta >= min_delta:
            return True, f"delta {of_bar.delta} >= {min_delta}"
        return False, f"delta {of_bar.delta} < {min_delta} (no buy aggression)"
    else:  # short
        if of_bar.delta <= -min_delta:
            return True, f"delta {of_bar.delta} <= -{min_delta}"
        return False, f"delta {of_bar.delta} > -{min_delta} (no sell aggression)"
