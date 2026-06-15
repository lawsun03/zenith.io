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


from app.bot_config import StrategyParams
from app.strategy.forbes import ForbesConfig, ForbesDetector


def _cfg(**kw):
    s = StrategyParams(engine="forbes", **kw)
    return ForbesConfig.from_params("MNQ", s)


def test_killzone_gate_blocks_outside_window():
    d = ForbesDetector(_cfg())
    # _b(0,...) is 13:00 UTC = 09:00 ET (summer EDT) -> before the 09:30-10:30 window.
    bar = _b(0, "100", "100", "100", "100")
    assert d.in_killzone(bar.ts) is False


def test_or_fvg_gate_stands_aside_when_no_fvg():
    d = ForbesDetector(_cfg())
    d._or_locked = True
    d._or_fvg_count = 0
    assert d.day_eligible() is False
    d._or_fvg_count = 1
    assert d.day_eligible() is True


from app.strategy.composer import Signal


def test_min_rr_gate_skips_low_rr_setup():
    # WHY: a setup whose nearest-liquidity target is too close (RR < min_rr) must NOT trade.
    d = ForbesDetector(_cfg(forbes_min_rr="3.0"))
    sig = d._build_signal(side="long", entry=Decimal("100"), stop=Decimal("90"),
                          target=Decimal("110"), bar=_b(0,"100","100","100","100"),
                          pattern="ifvg", sweep_level=Decimal("90"))
    assert sig is None  # RR = (110-100)/(100-90) = 1.0 < 3.0


def test_build_signal_uses_liquidity_target_and_passes_min_rr():
    # WHY: a valid setup targets the liquidity level with correct R geometry.
    d = ForbesDetector(_cfg(forbes_min_rr="1.4"))
    sig = d._build_signal(side="long", entry=Decimal("100"), stop=Decimal("90"),
                          target=Decimal("125"), bar=_b(0,"100","100","100","100"),
                          pattern="ifvg", sweep_level=Decimal("90"))
    assert sig is not None
    assert sig.side == "long" and sig.target == Decimal("125") and sig.stop == Decimal("90")


def test_max_trades_per_day_enforced():
    # WHY: with max_trades_per_day=1, a second trigger the same day is suppressed.
    d = ForbesDetector(_cfg(forbes_max_trades_per_day=1))
    d._trades_today = 1
    assert d._can_trade() is False


def test_on_bar_outside_killzone_returns_none():
    # WHY: the killzone gate must hard-block on_bar end-to-end, not just in_killzone().
    # _b(0,...) is 13:00 UTC = 09:00 ET (EDT) -> before the 09:30-10:30 window.
    d = ForbesDetector(_cfg())
    for m in range(20):
        sig = d.on_bar(_b(m, "100", str(100 + m % 3), str(100 - m % 2), "100"))
        assert sig is None


def test_on_bar_stands_aside_when_no_or_fvg():
    # WHY: a day whose opening range held no FVG must produce NO signal even inside
    # the killzone with sweeps occurring — the OR-FVG eligibility gate stands the day aside.
    d = ForbesDetector(_cfg())
    # Feed a long flat sequence spanning the OR window + killzone so the OR locks with
    # zero FVGs (flat bars never form a 3-bar gap), then sweeps cannot produce a trade.
    out = []
    # 30 bars from 13:30 UTC (09:30 ET) onward, all flat -> no FVG, no displacement.
    for m in range(30, 90):
        sig = d.on_bar(_b(m % 60, "100", "100", "100", "100"))
        if sig is not None:
            out.append(sig)
    assert d._or_fvg_count == 0
    assert out == []
