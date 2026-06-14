"""
Defining-behavior tests for B56: ORB×iFVG same-day directional alignment gate.

Gate: ORB signal suppressed when no prior same-direction iFVG has fired today.
- Group A (all same-dir iFVG): PF=1.707  → ORB fires
- Group B (no prior iFVG): PF=0.990     → ORB suppressed
- Group C (all opposite iFVG): PF=0.939 → ORB suppressed
- Group D (mixed iFVG): PF=1.224        → ORB fires

Phase 1 GO: A+D/B+C = 1.48x > 1.4x threshold, 5/5 years consistent.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from app.strategy.combined import DailySessionContext


TODAY = date(2024, 6, 3)
TOMORROW = date(2024, 6, 4)


@pytest.fixture
def ctx():
    return DailySessionContext()


# ── DailySessionContext unit tests ─────────────────────────────────────────


def test_b56_gate_no_prior_ifvg_suppresses_orb(ctx):
    """B56 gate: no prior iFVG today (Group B) → ORB suppressed."""
    assert ctx.gate_b56_orb_suppressed(TODAY, "long")
    assert ctx.gate_b56_orb_suppressed(TODAY, "short")


def test_b56_gate_same_direction_ifvg_allows_orb(ctx):
    """B56 gate: prior same-direction iFVG logged (Group A) → ORB fires."""
    ctx.record_ifvg_signal(TODAY, "long")
    assert not ctx.gate_b56_orb_suppressed(TODAY, "long")


def test_b56_gate_opposite_only_suppresses_orb(ctx):
    """B56 gate: only opposite-direction iFVG (Group C) → ORB suppressed."""
    ctx.record_ifvg_signal(TODAY, "short")
    assert ctx.gate_b56_orb_suppressed(TODAY, "long")


def test_b56_gate_mixed_ifvg_allows_orb(ctx):
    """B56 gate: both same and opposite iFVG logged (Group D) → ORB fires."""
    ctx.record_ifvg_signal(TODAY, "short")
    ctx.record_ifvg_signal(TODAY, "long")
    assert not ctx.gate_b56_orb_suppressed(TODAY, "long")


def test_b56_gate_resets_on_new_day(ctx):
    """B56 gate: prior day's iFVG does NOT carry into the next day."""
    ctx.record_ifvg_signal(TODAY, "long")
    assert not ctx.gate_b56_orb_suppressed(TODAY, "long")
    # New day: no signals logged yet → suppressed again
    assert ctx.gate_b56_orb_suppressed(TOMORROW, "long")


def test_b56_gate_symmetric_for_short_orb(ctx):
    """B56 gate: short ORB requires prior short iFVG; long iFVG only → suppressed."""
    ctx.record_ifvg_signal(TODAY, "long")
    assert ctx.gate_b56_orb_suppressed(TODAY, "short")
    ctx.record_ifvg_signal(TODAY, "short")
    assert not ctx.gate_b56_orb_suppressed(TODAY, "short")


def test_b56_gate_independent_of_gate1_and_gate2(ctx):
    """B56 gate does not interfere with B47 Gate 1/Gate 2 logic."""
    # Group B: no prior iFVG → B56 suppresses, Gate 1 allows (no prior = allow in Gate 1)
    assert ctx.gate_b56_orb_suppressed(TODAY, "long")
    assert not ctx.gate1_orb_suppressed(TODAY, "long")

    # Group C: all opposite → B56 suppresses AND Gate 1 suppresses
    ctx.record_ifvg_signal(TODAY, "short")
    assert ctx.gate_b56_orb_suppressed(TODAY, "long")
    assert ctx.gate1_orb_suppressed(TODAY, "long")
