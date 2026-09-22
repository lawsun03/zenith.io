"""
B82: ORB pre-market break gate.

Defines 6 required behaviors:
1. Long signal that clears pm_high is ALLOWED (flag=True).
2. Long signal that stays within pm range is SUPPRESSED (flag=True).
3. Short signal that clears pm_low is ALLOWED (flag=True).
4. Short signal that stays within pm range is SUPPRESSED (flag=True).
5. flag=False: all signals pass regardless of PM relationship.
6. No pre-market bars for the day: signal ALLOWED (graceful fallback).

WHY: The PM-break gate removes within-PM ORB signals (PF=0.886, net=-$19,463 over 5y)
while preserving PM-break signals (PF=1.857). Tests guard against: (a) incorrectly
suppressing PM-break signals, (b) failing to suppress within-PM signals, (c) the
fallback when no PM data exists breaking the session, (d) the flag default incorrectly
affecting live behavior.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.sim.events import Bar
from app.strategy.orb import ORBConfig, ORBDetector

# 2024-03-18 (Monday, EDT = UTC-4): chosen to be in DST (safe date)
# UTC offsets: 08:00 ET = 12:00 UTC, 09:30 ET = 13:30 UTC, 09:45 ET = 13:45 UTC
_INST = "MNQ"


def _bar(hour_utc: int, minute_utc: int, close: float, high: float, low: float) -> Bar:
    ts = datetime(2024, 3, 18, hour_utc, minute_utc, tzinfo=timezone.utc)
    return Bar(
        instrument=_INST, timeframe="5min", ts=ts,
        open=Decimal("21000"), high=Decimal(str(high)),
        low=Decimal(str(low)), close=Decimal(str(close)),
        volume=100,
    )


def _make_detector(require_pm_break: bool = True) -> ORBDetector:
    return ORBDetector(ORBConfig(
        instrument=_INST,
        open_et="09:30",
        range_minutes=15,
        r_multiple=Decimal("2.5"),
        require_pm_break=require_pm_break,
    ))


def _feed_pm_bars(det: ORBDetector) -> None:
    """Feed two PM bars establishing pm_high=21050, pm_low=20950."""
    # 08:05 ET = 12:05 UTC
    det.on_bar(_bar(12, 5, close=21000, high=21050, low=20980))
    # 08:15 ET = 12:15 UTC
    det.on_bar(_bar(12, 15, close=20990, high=21020, low=20950))


def _feed_or_bars(det: ORBDetector) -> None:
    """Feed OR-building bars 09:30-09:44 ET establishing OR=[20980, 21020]."""
    # 09:30 ET = 13:30 UTC (first OR bar)
    det.on_bar(_bar(13, 30, close=21010, high=21020, low=20990))
    # 09:35 ET = 13:35 UTC
    det.on_bar(_bar(13, 35, close=20995, high=21010, low=20980))
    # 09:40 ET = 13:40 UTC
    det.on_bar(_bar(13, 40, close=21005, high=21015, low=20985))


# ---------------------------------------------------------------------------
# Test 1: Long signal that clears pm_high is ALLOWED
# ---------------------------------------------------------------------------
def test_long_clears_pm_high_allowed() -> None:
    """close=21055 > OR high (21020) → long breakout; 21055 > pm_high (21050) → ALLOWED."""
    det = _make_detector(require_pm_break=True)
    _feed_pm_bars(det)    # pm_high=21050, pm_low=20950
    _feed_or_bars(det)    # OR=[20980, 21020]
    # 09:45 ET = 13:45 UTC: first post-OR bar, close > or_high AND close > pm_high
    sig = det.on_bar(_bar(13, 45, close=21055, high=21060, low=21022))
    assert sig is not None, "Long breakout clearing pm_high should fire"
    assert sig.side == "long"


# ---------------------------------------------------------------------------
# Test 2: Long signal within PM range is SUPPRESSED
# ---------------------------------------------------------------------------
def test_long_within_pm_range_suppressed() -> None:
    """close=21025 > OR high (21020) → long breakout; 21025 <= pm_high (21050) → SUPPRESSED."""
    det = _make_detector(require_pm_break=True)
    _feed_pm_bars(det)    # pm_high=21050
    _feed_or_bars(det)    # OR high=21020
    # close=21025: clears OR high but stays inside PM range
    sig = det.on_bar(_bar(13, 45, close=21025, high=21030, low=21021))
    assert sig is None, "Long breakout within PM range should be suppressed"


# ---------------------------------------------------------------------------
# Test 3: Short signal that clears pm_low is ALLOWED
# ---------------------------------------------------------------------------
def test_short_clears_pm_low_allowed() -> None:
    """close=20940 < OR low (20980) → short breakout; 20940 < pm_low (20950) → ALLOWED."""
    det = _make_detector(require_pm_break=True)
    _feed_pm_bars(det)    # pm_low=20950
    _feed_or_bars(det)    # OR=[20980, 21020]
    sig = det.on_bar(_bar(13, 45, close=20940, high=20979, low=20935))
    assert sig is not None, "Short breakout clearing pm_low should fire"
    assert sig.side == "short"


# ---------------------------------------------------------------------------
# Test 4: Short signal within PM range is SUPPRESSED
# ---------------------------------------------------------------------------
def test_short_within_pm_range_suppressed() -> None:
    """close=20970 < OR low (20980) → short breakout; 20970 >= pm_low (20950) → SUPPRESSED."""
    det = _make_detector(require_pm_break=True)
    _feed_pm_bars(det)    # pm_low=20950
    _feed_or_bars(det)    # OR low=20980
    sig = det.on_bar(_bar(13, 45, close=20970, high=20979, low=20965))
    assert sig is None, "Short breakout within PM range should be suppressed"


# ---------------------------------------------------------------------------
# Test 5: flag=False passes all signals regardless of PM relationship
# ---------------------------------------------------------------------------
def test_flag_false_passes_all_signals() -> None:
    """Same setup as test 2 (within PM), but flag=False → signal fires normally."""
    det = _make_detector(require_pm_break=False)
    _feed_pm_bars(det)    # PM data exists but flag is off
    _feed_or_bars(det)
    # close=21025: within PM range but flag is off — should still fire
    sig = det.on_bar(_bar(13, 45, close=21025, high=21030, low=21021))
    assert sig is not None, "flag=False should pass all signals regardless of PM range"
    assert sig.side == "long"


# ---------------------------------------------------------------------------
# Test 6: No PM bars — graceful fallback allows signals
# ---------------------------------------------------------------------------
def test_no_pm_bars_allows_signal() -> None:
    """No PM bars fed → _pm_high is None → gate skipped → signal fires normally."""
    det = _make_detector(require_pm_break=True)
    # Skip PM bar feeding; go straight to OR
    _feed_or_bars(det)
    # close=21025: would be suppressed if PM data existed (within hypothetical range),
    # but no PM data → fallback → allowed
    sig = det.on_bar(_bar(13, 45, close=21025, high=21030, low=21021))
    assert sig is not None, "No PM bars should allow all signals (graceful fallback)"
    assert sig.side == "long"
