"""Gate 1 — frequency floor. Acceptance test: "A strategy trading twice a
week is rejected at gate 1, not later" (docs/research-loop/PHASE-PROMPTS.md
phase 4)."""
from __future__ import annotations

from research.gates.frequency import MAX_GAP_DAYS_MAX, WEEKS_MEETING_FLOOR_MIN, evaluate
from research.gates.metrics import FrequencyStats


def test_meets_floor_and_gap_passes():
    stats = FrequencyStats(weekly_histogram={"2024-W01": 3}, weeks_meeting_floor=1.0,
                            max_gap_days=2, trades_total=3)
    result = evaluate(stats)
    assert result.passed
    assert result.gate == 1
    assert result.measured == 1.0
    assert result.threshold == WEEKS_MEETING_FLOOR_MIN


def test_twice_a_week_fails():
    """3-trades/week floor never met by a strategy averaging 2/week."""
    stats = FrequencyStats(weekly_histogram={f"2024-W{w:02d}": 2 for w in range(1, 53)},
                            weeks_meeting_floor=0.0, max_gap_days=4, trades_total=104)
    result = evaluate(stats)
    assert not result.passed
    assert result.measured == 0.0


def test_large_gap_fails_even_with_good_weekly_average():
    stats = FrequencyStats(weekly_histogram={"2024-W01": 5}, weeks_meeting_floor=1.0,
                            max_gap_days=MAX_GAP_DAYS_MAX + 1, trades_total=5)
    assert not evaluate(stats).passed
