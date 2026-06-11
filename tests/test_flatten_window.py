"""Flatten-rule config + window math.

Why: Topstep requires flat by 3:10 PM CT (4:10 PM ET); the bot held 66
positions through that window in the 2025-26 test data. Times are config,
not code (Topstep changed rules 8x in 6 months), and DST-correct via
America/Chicago — never fixed UTC offsets.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.bot_config import BotConfig


def test_flatten_config_defaults():
    cfg = BotConfig()
    assert cfg.flatten_enabled is True
    assert cfg.flatten_time_ct == "15:05"      # 4:05 PM ET, 5 min buffer
    assert cfg.entry_cutoff_time_ct == "14:30" # 3:30 PM ET


from app.risk.flatten import in_flatten_window, past_entry_cutoff


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class TestFlattenWindow:
    # 2026-01-15 is CST (UTC-6): 15:05 CT == 21:05 UTC
    def test_before_flatten_time_not_in_window(self):
        assert not in_flatten_window(_utc(2026, 1, 15, 21, 4), "15:05")

    def test_at_flatten_time_in_window(self):
        assert in_flatten_window(_utc(2026, 1, 15, 21, 5), "15:05")

    def test_after_session_close_not_in_window(self):
        # 17:00 CT = next trading day; flatten window ended
        assert not in_flatten_window(_utc(2026, 1, 15, 23, 0), "15:05")

    def test_dst_boundary_uses_cdt(self):
        # 2026-07-15 is CDT (UTC-5): 15:05 CT == 20:05 UTC
        assert in_flatten_window(_utc(2026, 7, 15, 20, 5), "15:05")
        assert not in_flatten_window(_utc(2026, 7, 15, 20, 4), "15:05")


class TestEntryCutoff:
    def test_before_cutoff_allowed(self):
        assert not past_entry_cutoff(_utc(2026, 1, 15, 20, 29), "14:30")

    def test_after_cutoff_blocked(self):
        assert past_entry_cutoff(_utc(2026, 1, 15, 20, 30), "14:30")

    def test_evening_session_after_5pm_ct_allowed(self):
        # 18:00 CT = new trading day, overnight trading is allowed
        assert not past_entry_cutoff(_utc(2026, 1, 16, 0, 0), "14:30")
