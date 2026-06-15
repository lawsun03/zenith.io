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
