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
