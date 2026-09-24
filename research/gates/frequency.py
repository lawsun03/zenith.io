"""Gate 1 — frequency floor (docs/research-loop/gates.md).

PASS if weeks_meeting_floor >= 0.80 AND max_gap_days <= 10. Measured/
threshold recorded to the ledger is weeks_meeting_floor — max_gap_days is a
second, independent rejection condition the single-scalar ledger column
can't carry, so a max-gap failure still surfaces as `passed=False` even on
a candidate whose weekly average looks fine (documented so a reader of the
ledger doesn't mistake `measured >= threshold` for the whole story).
"""
from __future__ import annotations

from research.gates.metrics import FrequencyStats
from research.gates.types import GateResult

WEEKS_MEETING_FLOOR_MIN = 0.80
MAX_GAP_DAYS_MAX = 10


def evaluate(stats: FrequencyStats) -> GateResult:
    passed = (stats.weeks_meeting_floor >= WEEKS_MEETING_FLOOR_MIN
              and stats.max_gap_days <= MAX_GAP_DAYS_MAX)
    return GateResult(
        gate=1, passed=passed,
        measured=stats.weeks_meeting_floor,
        threshold=WEEKS_MEETING_FLOOR_MIN,
    )
