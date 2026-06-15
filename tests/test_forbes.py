from datetime import datetime, timezone
from decimal import Decimal
from app.broker.events import Bar
from app.strategy.forbes import _FifteenMinAggregator


def _b(minute, o, h, l, c):
    return Bar(instrument="MNQ", timeframe="1min",
               ts=datetime(2026, 5, 11, 13, minute, tzinfo=timezone.utc),
               open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=100)


def test_aggregator_emits_one_15m_bar_per_15_one_min_bars():
    agg = _FifteenMinAggregator(minutes=15)
    out = []
    for m in range(15):  # 13:00..13:14 -> the 13:00-13:15 bucket
        r = agg.on_bar(_b(m, "100", str(100 + m), str(100 - 1), str(100)))
        if r is not None:
            out.append(r)
    # The 15m bar finalizes when a bar in the NEXT bucket arrives:
    r = agg.on_bar(_b(15, "101", "101", "101", "101"))
    assert r is not None
    assert r.high == Decimal("114")   # max of highs 100..114
    assert r.low == Decimal("99")
    assert r.timeframe == "15min"


from app.strategy.forbes import ForbesPOIMap, _Level


def test_poi_map_nearest_unswept_opposing():
    m = ForbesPOIMap()
    m.add(_Level(price=Decimal("110"), kind="session_high", swept=False))
    m.add(_Level(price=Decimal("120"), kind="swing_high", swept=False))
    m.add(_Level(price=Decimal("105"), kind="session_high", swept=True))  # already swept
    # Long from 100: nearest UNSWEPT level ABOVE = 110 (105 swept, 120 farther).
    lvl = m.nearest_unswept_opposing(side="long", price=Decimal("100"))
    assert lvl is not None and lvl.price == Decimal("110")
    # Short from 100: no unswept level below -> None.
    assert m.nearest_unswept_opposing(side="short", price=Decimal("100")) is None


def test_poi_map_mark_swept_when_price_trades_through():
    m = ForbesPOIMap()
    m.add(_Level(price=Decimal("110"), kind="session_high", swept=False))
    m.update_swept(bar_high=Decimal("111"), bar_low=Decimal("108"))
    assert m.nearest_unswept_opposing(side="long", price=Decimal("100")) is None  # 110 now swept


from app.strategy.kz_levels import KillzoneLevelTracker


def test_kz_tracker_exposes_locked_session_ranges():
    t = KillzoneLevelTracker()
    assert hasattr(t, "locked_ranges")
    assert t.locked_ranges() == {}   # empty before any session locks
