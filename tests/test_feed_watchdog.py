"""Feed-dead watchdog: session gate, state machine, clock, config.

See docs/superpowers/specs/2026-06-16-feed-dead-watchdog-design.md.
Deterministic — injected `now`, no real sleeping.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.risk.flatten import bars_expected


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class TestBarsExpected:
    # 2026-01-15 is a Thursday, CST (UTC-6): 10:00 CT == 16:00 UTC (active)
    def test_active_midsession(self):
        assert bars_expected(_utc(2026, 1, 15, 16, 0))      # 10:00 CT Thu

    def test_daily_maintenance_break(self):
        # 16:30 CT == 22:30 UTC — inside 16:00-17:00 CT break
        assert not bars_expected(_utc(2026, 1, 15, 22, 30))

    def test_reopen_after_break(self):
        # 17:00 CT == 23:00 UTC — session reopens
        assert bars_expected(_utc(2026, 1, 15, 23, 0))

    def test_saturday_closed(self):
        # 2026-01-17 is Saturday
        assert not bars_expected(_utc(2026, 1, 17, 18, 0))

    def test_friday_evening_closed(self):
        # Fri 2026-01-16 16:30 CT == 22:30 UTC (after Fri 16:00 close)
        assert not bars_expected(_utc(2026, 1, 16, 22, 30))

    def test_sunday_reopen(self):
        # Sun 2026-01-18 17:30 CT == 23:30 UTC (after Sun 17:00 open)
        assert bars_expected(_utc(2026, 1, 18, 23, 30))

    def test_early_close_day_afternoon_closed(self):
        # 2026-07-03 early close (noon CT). 13:00 CT CDT == 18:00 UTC — closed
        assert not bars_expected(_utc(2026, 7, 3, 18, 0))

    def test_early_close_day_morning_open(self):
        # 2026-07-03 10:00 CT CDT == 15:00 UTC — still open before noon
        assert bars_expected(_utc(2026, 7, 3, 15, 0))
