from app.bot_config import StrategyParams

def test_sweep_level_config_defaults_off():
    sp = StrategyParams()
    assert sp.sweep_levels_tag_enabled is False
    assert sp.sweep_levels_tag_tolerance_ticks == 4


from datetime import datetime, timezone
from decimal import Decimal
from app.broker.events import Bar
from app.strategy.sweep_levels import SweepLevelTracker

def _bar(et_hour, et_min, o, h, l, c, day="2026-06-16"):
    from zoneinfo import ZoneInfo
    ts_et = datetime.fromisoformat(f"{day}T{et_hour:02d}:{et_min:02d}:00").replace(tzinfo=ZoneInfo("America/New_York"))
    return Bar(instrument="MNQ", timeframe="5min", ts=ts_et.astimezone(timezone.utc),
               open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=100)

def test_daily_open_set_at_rth_and_no_lookahead():
    t = SweepLevelTracker()
    t.on_bar(_bar(9, 0, 21000, 21010, 20990, 21005))
    assert t.levels().get("daily_open") is None
    t.on_bar(_bar(9, 30, 21020, 21030, 21015, 21025))
    assert t.levels()["daily_open"] == Decimal("21020")

def test_asian_high_low_locked_after_session_close():
    t = SweepLevelTracker()
    t.on_bar(_bar(19, 0, 21000, 21050, 20990, 21010, day="2026-06-15"))
    t.on_bar(_bar(21, 0, 21010, 21080, 21005, 21070, day="2026-06-15"))
    t.on_bar(_bar(22, 30, 21070, 21075, 21060, 21065, day="2026-06-15"))
    assert t.levels()["asian_high"] == Decimal("21080")
    assert t.levels()["asian_low"] == Decimal("20990")
