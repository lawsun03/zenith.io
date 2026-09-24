"""Trade-sequence statistics shared by several gates.

Deliberately sizing- and account-ignorant (CLAUDE.md domain invariant 4:
"Account size never enters rule design... enter at position sizing and at
the combine simulator — nowhere else"). Every function here takes a
`list[research.ir.engine.Trade]` plus, at most, an instrument's point value
— never a contract count, never an account config. Gate 8 (combine.py) is
the one place downstream that layers sizing and account rules on top.

Sharpe here is computed on R-multiples (pnl_points / risk at entry), not on
dollar P&L: R-multiples are dimensionless and volatility-normalized by
construction (the stop distance IS the volatility-scaled risk unit — see
research/ir/engine.py's stop pricing), which is what lets a Sharpe figure
pool cleanly across NQ/ES/GC despite their very different tick values,
without smuggling any account-specific sizing into what is supposed to be a
property of the rule alone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from research.ir.engine import Trade


def r_multiples(trades: Sequence[Trade]) -> list[float]:
    out = []
    for t in trades:
        risk = abs(t.entry_price - t.stop_price)
        if risk <= 0:
            raise ValueError(
                f"trade at {t.entry_ts} has non-positive risk distance "
                f"({t.entry_price} vs stop {t.stop_price}) — cannot form an R-multiple"
            )
        out.append(float(t.pnl_points / risk))
    return out


def sharpe_from_trades(trades: Sequence[Trade], trades_per_year: float, *,
                        annualize: bool = True) -> float:
    """Mean/stdev of R-multiples, optionally annualized by sqrt(trades_per_year)
    (matches the annualization convention already used in
    research/ir/sizing.py's volatility targeting).

    Raises ValueError on fewer than 2 trades or zero variance — a Sharpe
    ratio is not a meaningful number in either case, and gate 5/6/7 must not
    silently receive a fabricated one (CLAUDE.md rule 12).
    """
    if len(trades) < 2:
        raise ValueError("need at least 2 trades to compute a Sharpe ratio")
    r = r_multiples(trades)
    n = len(r)
    mean = sum(r) / n
    variance = sum((x - mean) ** 2 for x in r) / (n - 1)
    if variance <= 0:
        raise ValueError("R-multiples have zero variance — cannot compute a Sharpe ratio")
    std = math.sqrt(variance)
    sr = mean / std
    if annualize:
        sr *= math.sqrt(trades_per_year)
    return sr


def skew_and_kurtosis(trades: Sequence[Trade]) -> tuple[float, float]:
    """Sample skew and NON-EXCESS kurtosis (normal = 3.0) of R-multiples —
    the exact convention research.stats.deflated_sharpe.deflated_sharpe_ratio
    documents its `kurtosis` argument as requiring."""
    r = r_multiples(trades)
    n = len(r)
    if n < 3:
        raise ValueError("need at least 3 trades to compute skew/kurtosis")
    mean = sum(r) / n
    m2 = sum((x - mean) ** 2 for x in r) / n
    m3 = sum((x - mean) ** 3 for x in r) / n
    m4 = sum((x - mean) ** 4 for x in r) / n
    if m2 <= 0:
        raise ValueError("R-multiples have zero variance — cannot compute skew/kurtosis")
    skew = m3 / m2 ** 1.5
    kurtosis = m4 / m2 ** 2
    return skew, kurtosis


def mean_edge_dollars_per_trade(trades: Sequence[Trade], point_value: Decimal) -> Decimal:
    """Mean per-contract dollar P&L per trade — gate 2's numerator. At one
    contract deliberately (see module docstring): comparable to a per-
    contract modelled round-turn cost regardless of what any account would
    actually size the trade to."""
    if not trades:
        raise ValueError("no trades to measure edge from")
    total = sum((t.pnl_points * point_value for t in trades), Decimal("0"))
    return total / len(trades)


def _iso_week_key(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _all_week_keys(start: date, end: date) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    d = start
    while d <= end:
        k = _iso_week_key(d)
        if k not in seen:
            seen.add(k)
            keys.append(k)
        d += timedelta(days=1)
    return keys


@dataclass(frozen=True)
class FrequencyStats:
    weekly_histogram: dict[str, int]
    weeks_meeting_floor: float
    max_gap_days: int
    trades_total: int


def frequency_stats(
    trades: Sequence[Trade], sample_start: date, sample_end: date, *,
    min_trades_per_week: int = 3,
) -> FrequencyStats:
    """Gate 1's measurement, on the DISTRIBUTION not the mean (gates.md gate
    1: "A strategy averaging 3/week by clustering 20 trades in volatile
    stretches and nothing for a month fails, correctly."). Every calendar
    week in [sample_start, sample_end] is counted, including weeks with zero
    trades — a quiet strategy must show up as a bad week, not an absent one.
    """
    week_keys = _all_week_keys(sample_start, sample_end)
    histogram = {k: 0 for k in week_keys}
    entry_dates = sorted(t.entry_ts.date() for t in trades)
    for d in entry_dates:
        k = _iso_week_key(d)
        if k in histogram:
            histogram[k] += 1

    weeks_meeting_floor = (
        sum(1 for c in histogram.values() if c >= min_trades_per_week) / len(histogram)
        if histogram else 0.0
    )

    boundary_dates = [sample_start, *entry_dates, sample_end]
    max_gap = max(
        (b - a).days for a, b in zip(boundary_dates, boundary_dates[1:])
    ) if len(boundary_dates) > 1 else 0

    return FrequencyStats(
        weekly_histogram=histogram,
        weeks_meeting_floor=weeks_meeting_floor,
        max_gap_days=max_gap,
        trades_total=len(trades),
    )
