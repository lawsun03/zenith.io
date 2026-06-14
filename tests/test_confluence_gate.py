"""
Tests for B47: iFVG×ORB directional confluence gate.

DailySessionContext shared across iFVG and ORB runners in CombinedRunner.

Gate 1 — ORB suppressed when all prior same-day iFVG signals are OPPOSITE direction.
Gate 2 — post-ORB iFVG signals in the OPPOSITE direction of ORB are suppressed.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.strategy.combined import DailySessionContext


@pytest.fixture
def ctx():
    return DailySessionContext()


TODAY = date(2024, 6, 3)
TOMORROW = date(2024, 6, 4)


# ── DailySessionContext unit tests ─────────────────────────────────────────


def test_gate1_no_prior_ifvg_allows_orb(ctx):
    """Gate 1: no prior iFVG signals today → ORB fires regardless of direction."""
    assert not ctx.gate1_orb_suppressed(TODAY, "long")
    assert not ctx.gate1_orb_suppressed(TODAY, "short")


def test_gate1_same_direction_ifvg_allows_orb(ctx):
    """Gate 1: prior iFVG in the SAME direction → ORB fires (same_only / both cases)."""
    ctx.record_ifvg_signal(TODAY, "long")
    assert not ctx.gate1_orb_suppressed(TODAY, "long")


def test_gate1_opposite_ifvg_suppresses_orb(ctx):
    """Gate 1: ALL prior iFVG signals oppose ORB direction → ORB is suppressed."""
    ctx.record_ifvg_signal(TODAY, "short")
    assert ctx.gate1_orb_suppressed(TODAY, "long")  # short iFVG → long ORB suppressed


def test_gate1_mixed_prior_ifvg_allows_orb(ctx):
    """Gate 1: mixed iFVG signals (both directions) → ORB fires (both case, PF=1.485)."""
    ctx.record_ifvg_signal(TODAY, "long")
    ctx.record_ifvg_signal(TODAY, "short")
    assert not ctx.gate1_orb_suppressed(TODAY, "long")


def test_gate2_no_orb_allows_ifvg(ctx):
    """Gate 2: no ORB signal today → iFVG signals fire regardless of side."""
    assert not ctx.gate2_ifvg_suppressed(TODAY, "long")
    assert not ctx.gate2_ifvg_suppressed(TODAY, "short")


def test_gate2_same_direction_allows_ifvg(ctx):
    """Gate 2: iFVG in SAME direction as ORB → allowed (orb_same case, PF=1.375)."""
    ctx.record_orb_direction(TODAY, "long")
    assert not ctx.gate2_ifvg_suppressed(TODAY, "long")


def test_gate2_opposite_direction_suppresses_ifvg(ctx):
    """Gate 2: iFVG OPPOSES ORB direction → suppressed (orb_opp case, PF=0.757)."""
    ctx.record_orb_direction(TODAY, "long")
    assert ctx.gate2_ifvg_suppressed(TODAY, "short")


def test_context_resets_at_et_midnight(ctx):
    """Day-N iFVG/ORB signals do NOT gate Day-N+1 signals."""
    ctx.record_ifvg_signal(TODAY, "short")
    ctx.record_orb_direction(TODAY, "long")

    # On the next ET day, the context resets: Gate 1 should allow ORB (no prior iFVG)
    assert not ctx.gate1_orb_suppressed(TOMORROW, "long")
    # Gate 2 should also clear (no ORB fired tomorrow yet)
    assert not ctx.gate2_ifvg_suppressed(TOMORROW, "short")


def test_record_ifvg_after_orb_still_tracked(ctx):
    """iFVG signals recorded after ORB fires are tracked correctly."""
    ctx.record_orb_direction(TODAY, "long")
    # Gate 2 suppresses short iFVG
    assert ctx.gate2_ifvg_suppressed(TODAY, "short")
    # But long iFVG is allowed and gets recorded
    assert not ctx.gate2_ifvg_suppressed(TODAY, "long")
    ctx.record_ifvg_signal(TODAY, "long")
    # Gate 1 is irrelevant after ORB fires, but ifvg_sides should now have "long"
    # Verify the side list contains the newly-added long signal
    assert ctx._ifvg_sides == ["long"]
