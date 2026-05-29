"""Tests for session/macro/news window filtering in killzone helpers.

Tests must fail if session window logic silently allows or blocks trades.
DST transition test ensures NY-to-UTC conversion is correct year-round.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.strategy.killzone import in_session_window, in_macro_window, in_news_blackout

ET = ZoneInfo("America/New_York")


def utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestSessionWindow:
    # NY AM session: 09:00-11:00 ET
    # In summer (EDT = UTC-4): 09:00 ET = 13:00 UTC, 11:00 ET = 15:00 UTC
    # In winter (EST = UTC-5): 09:00 ET = 14:00 UTC, 11:00 ET = 16:00 UTC

    def test_inside_session_window_summer(self):
        """09:30 ET in June → 13:30 UTC → inside 09:00-11:00 window."""
        ts = utc(2026, 6, 10, 13, 30)  # 09:30 ET summer
        assert in_session_window(ts, ["09:00-11:00"]) is True

    def test_outside_session_window_summer(self):
        """12:00 ET (lunch) → 16:00 UTC → outside 09:00-11:00 window."""
        ts = utc(2026, 6, 10, 16, 0)  # 12:00 ET summer
        assert in_session_window(ts, ["09:00-11:00"]) is False

    def test_dst_transition_winter_session_window(self):
        """09:30 ET in January → 14:30 UTC → inside 09:00-11:00 window."""
        ts = utc(2026, 1, 15, 14, 30)  # 09:30 ET winter (EST)
        assert in_session_window(ts, ["09:00-11:00"]) is True

    def test_empty_windows_allows_all(self):
        """Empty session_windows list = no filter (allow all times)."""
        ts = utc(2026, 6, 10, 20, 0)  # 16:00 ET — late afternoon
        assert in_session_window(ts, []) is True

    def test_signal_blocked_outside_session_window(self):
        """Signal at 15:30 UTC (11:30 ET) is outside 09:00-11:00 window."""
        ts = utc(2026, 6, 10, 15, 30)
        assert in_session_window(ts, ["09:00-11:00"]) is False

    def test_signal_passes_inside_session_window(self):
        ts = utc(2026, 6, 10, 14, 0)  # 10:00 ET summer
        assert in_session_window(ts, ["09:00-11:00"]) is True


class TestNewsBlackout:
    def test_news_blackout_blocks_signal(self):
        """UTC range 12:30-13:00 blocks signal at 12:45 UTC."""
        ts = utc(2026, 6, 6, 12, 45)
        assert in_news_blackout(ts, ["2026-06-06T12:30/2026-06-06T13:00"]) is True

    def test_news_blackout_not_active_outside_range(self):
        ts = utc(2026, 6, 6, 13, 5)  # after blackout ends
        assert in_news_blackout(ts, ["2026-06-06T12:30/2026-06-06T13:00"]) is False

    def test_empty_blackout_list_blocks_nothing(self):
        ts = utc(2026, 6, 6, 12, 45)
        assert in_news_blackout(ts, []) is False

    def test_malformed_blackout_entry_skipped(self):
        """Malformed entry (no slash) is skipped without crashing."""
        ts = utc(2026, 6, 6, 12, 45)
        assert in_news_blackout(ts, ["malformed-entry"]) is False
