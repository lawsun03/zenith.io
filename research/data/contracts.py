"""Pure functions: which contract months exist for a root over a date range,
and how to spell their Databento raw symbols.

No I/O here — everything is a deterministic function of a calendar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from research.data.instruments import MONTH_CODE_TO_NUM, InstrumentSpec


@dataclass(frozen=True, order=True)
class ContractMonth:
    """One listed contract month, e.g. GCZ24 = GC December 2024."""
    root: str
    month_code: str
    year: int  # four-digit

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.year, MONTH_CODE_TO_NUM[self.month_code])

    @property
    def raw_symbol(self) -> str:
        """Databento raw_symbol spelling: ROOT + month code + 2-digit year."""
        return f"{self.root}{self.month_code}{self.year % 100:02d}"

    def __str__(self) -> str:
        return self.raw_symbol


def contract_months_covering(
    spec: InstrumentSpec,
    start: date,
    end: date,
    lead_months: int = 2,
) -> list[ContractMonth]:
    """Every listed contract month whose trading window could overlap [start, end].

    A contract trades for roughly a year before its own delivery month, so we
    look back `lead_months` months before `start`'s month/year cycle position
    to make sure the front contract active at `start` is included, and forward
    through `end`.
    """
    months = []
    y = start.year - 1  # generous lookback; filtered by sort_key below
    while y <= end.year:
        for code in spec.contract_months:
            months.append(ContractMonth(spec.root, code, y))
        y += 1
    months.sort(key=lambda m: m.sort_key)

    start_key = (start.year, start.month)
    end_key = (end.year, end.month)

    def in_window(m: ContractMonth) -> bool:
        # Keep contracts whose delivery month is from `lead_months` months
        # before `start` through `end` (inclusive) — this comfortably spans
        # the period the contract could plausibly be the front or back month.
        lookback_year = start.year
        lookback_month = start.month - lead_months
        while lookback_month <= 0:
            lookback_month += 12
            lookback_year -= 1
        return (lookback_year, lookback_month) <= m.sort_key <= end_key

    return [m for m in months if in_window(m)]
