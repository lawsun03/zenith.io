from datetime import datetime, timezone, timedelta
from decimal import Decimal

from app.sim.events import Bar
from app.strategy.htf import HTFBiasTracker, HTFLevelFinder
import pytest


def _bar(i: int, o, h, l, c, tf="4h") -> Bar:
    return Bar(
        instrument="MGC",
        timeframe=tf,
        ts=datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(hours=4 * i),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=100,
    )


def _swing_sequence(highs_lows: list[tuple[float, float]]) -> list[Bar]:
    """Build bars that confirm a swing high and a swing low per pair.

    Each (h, l) pair produces 8 bars: 2 neighbor bars before the swing-high
    pivot, the pivot itself, 2 bars that serve as both post-high and pre-low
    neighbors, the swing-low pivot, and 2 trailing bars to confirm the low.

    High and low pivots are SEPARATE bars (not the same bar) because
    LiquidityTracker._maybe_confirm_swing returns after the first match — a bar
    that is simultaneously the period high AND low would only register as a
    swing high. Using separate pivot bars ensures both swing kinds confirm.

    All neighbor bars have strictly lower highs and strictly higher lows than
    their respective pivot, satisfying the lookback=2 strict-inequality check.
    """
    bars_raw: list[tuple[float, float]] = []
    for h, l in highs_lows:
        mid = (h + l) / 2
        bars_raw += [
            (h - 0.5, mid - 0.6),   # neighbor before swing-high pivot
            (h - 0.5, mid - 0.6),   # neighbor before swing-high pivot
            (h,       mid - 0.5),   # SWING HIGH pivot
            (h - 0.5, mid - 0.6),   # post-high / pre-low neighbor
            (h - 0.5, mid - 0.6),   # pre-low neighbor
            (mid + 0.2, l),         # SWING LOW pivot
            (mid + 0.1, l + 0.3),   # neighbor after swing-low pivot
            (mid + 0.1, l + 0.3),   # neighbor after swing-low pivot
        ]
    bars: list[Bar] = []
    for i, (hh, ll) in enumerate(bars_raw):
        mid = (hh + ll) / 2
        bars.append(_bar(i, mid - 0.2, hh, ll, mid + 0.1))
    return bars


def test_ifvg_bias_neutral_with_fewer_than_3_bars():
    """Fewer than 3 bars → cannot form a FVG → neutral."""
    t = HTFBiasTracker()
    t.rebuild([_bar(0, 10, 11, 9, 10), _bar(1, 10, 11, 9, 10)])
    assert t.bias() == "neutral"


def test_ifvg_bias_neutral_before_any_inversion():
    """FVG exists but has not been inversed yet → neutral."""
    t = HTFBiasTracker()
    # Bearish FVG: b1.low=10 > b3.high=9 → gap [9, 10]
    # b4 close=9 stays at gap edge, does NOT cross high=10
    t.rebuild([
        _bar(0, 11, 12, 10, 11),   # b1: low=10
        _bar(1, 10, 10,  8,  9),   # b2
        _bar(2,  9,  9,  7,  8),   # b3: high=9 → bearish FVG [9, 10]
        _bar(3,  8,  9,  7,  9),   # b4: close=9 — does NOT cross high=10
    ])
    assert t.bias() == "neutral"


def test_ifvg_bias_bullish_after_bearish_fvg_inversion():
    """Bearish FVG inversed (close > fvg.high) → bias = bullish."""
    t = HTFBiasTracker()
    # Bearish FVG: b1.low=10 > b3.high=9 → gap [9, 10]
    # b4 closes at 10.5 > fvg.high=10 → IFVG → bullish
    t.rebuild([
        _bar(0, 11, 12, 10, 11),   # b1: low=10
        _bar(1, 10, 10,  8,  9),   # b2
        _bar(2,  9,  9,  7,  8),   # b3: high=9 → bearish FVG [9, 10]
        _bar(3,  8, 11,  8, 10.5), # b4: close=10.5 > 10 → inversion → bullish
    ])
    assert t.bias() == "bullish"


def test_ifvg_bias_bearish_after_bullish_fvg_inversion():
    """Bullish FVG inversed (close < fvg.low) → bias = bearish."""
    t = HTFBiasTracker()
    # Bullish FVG: b3.low=11 > b1.high=10 → gap [10, 11]
    # b4 closes at 9.5 < fvg.low=10 → IFVG → bearish
    t.rebuild([
        _bar(0,  9, 10,  8,  9),   # b1: high=10
        _bar(1, 10, 12, 10, 11),   # b2
        _bar(2, 11, 12, 11, 11.5), # b3: low=11 → bullish FVG [10, 11]
        _bar(3, 11, 11,  9,  9.5), # b4: close=9.5 < 10 → inversion → bearish
    ])
    assert t.bias() == "bearish"


def test_ifvg_most_recent_fvg_replaces_old_unmitigated_fvg():
    """When a new FVG forms before the tracked one is inversed, the new one is tracked."""
    t = HTFBiasTracker()
    # Bearish FVG [9, 10] forms. Before it's inverted, bullish FVG [9, 12] forms.
    # Bullish FVG [9, 12] is then inverted (close < 9) → bearish.
    t.rebuild([
        _bar(0, 11, 12, 10, 11),   # b1 of FVG1
        _bar(1, 10, 10,  8,  9),   # b2 of FVG1
        _bar(2,  9,  9,  7,  8),   # b3 of FVG1: bearish FVG [9, 10]
        _bar(3,  8,  9,  8,  9),   # b4: close=9, NOT > 10 (no inversion); b1 of FVG2
        _bar(4,  9, 11,  9, 10),   # b5: b2 of FVG2
        _bar(5, 12, 13, 12, 12.5), # b6: b3 of FVG2: low=12 > b4.high=9 → bullish FVG [9, 12]
        _bar(6, 12, 12,  8,  8.5), # b7: close=8.5 < fvg.low=9 → inversion → bearish
    ])
    assert t.bias() == "bearish"


def test_ifvg_bias_persists_after_inversion():
    """Bias stays at the last inversion value; does not revert to neutral."""
    t = HTFBiasTracker()
    # Inversion at bar 3, then 3 more bars with no new FVG → bias stays bullish.
    t.rebuild([
        _bar(0, 11, 12, 10, 11),
        _bar(1, 10, 10,  8,  9),
        _bar(2,  9,  9,  7,  8),   # bearish FVG [9, 10]
        _bar(3,  8, 11,  8, 10.5), # inversion → bullish
        _bar(4, 10, 11,  9, 10),   # no new FVG
        _bar(5, 10, 11,  9, 10),
        _bar(6, 10, 11,  9, 10),
    ])
    assert t.bias() == "bullish"


def _tf_bar(i, o, h, l, c, tf):
    return Bar(
        instrument="MGC", timeframe=tf,
        ts=datetime(2026, 5, 1, tzinfo=timezone.utc) + timedelta(hours=i),
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=100,
    )


def test_level_finder_returns_4h_fvg_above_entry_for_long():
    # Bullish FVG: bar1.high=20, bar3.low=24 → gap [20, 24], near edge 20.
    # No later bar trades back below 20 → unmitigated.
    bars = [
        _tf_bar(0, 18, 20, 17, 19, "4h"),   # b1 (high=20)
        _tf_bar(1, 19, 26, 19, 25, "4h"),   # b2 displacement up
        _tf_bar(2, 25, 27, 24, 26, "4h"),   # b3 (low=24) → gap 20..24
        _tf_bar(3, 26, 28, 25, 27, "4h"),   # stays above gap
    ]
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=bars, swing_bars=[])
    # entry=15, stop=13 → R=2, min_r=2 → need target >= 19. Near edge 20 qualifies.
    found = f.find_target("long", Decimal("15"), Decimal("13"), Decimal("2.0"))
    assert found is not None
    price, label = found
    assert price == Decimal("20")
    assert "4h FVG" in label


def test_level_finder_falls_back_to_30min_swing_when_no_qualifying_fvg():
    # 4h FVG near edge is only 1R away (too close); a 30min swing high qualifies.
    fvg_bars = [
        _tf_bar(0, 18, 20, 17, 19, "4h"),
        _tf_bar(1, 19, 23, 19, 22, "4h"),
        _tf_bar(2, 22, 24, 21, 23, "4h"),   # gap 20..21, near edge 20
        _tf_bar(3, 23, 25, 22, 24, "4h"),
    ]
    # entry=18.5, stop=17.5 → R=1, min_r=2 → need target >= 20.5; FVG edge 20 fails.
    # Build a 30min swing high at 22 (clears 20.5).
    swing_bars = _swing_sequence([(22, 19), (22, 19)])
    for b in swing_bars:
        object.__setattr__(b, "timeframe", "30min")
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=fvg_bars, swing_bars=swing_bars)
    found = f.find_target("long", Decimal("18.5"), Decimal("17.5"), Decimal("2.0"))
    assert found is not None
    price, label = found
    assert price >= Decimal("20.5")
    assert "30min swing" in label


def test_level_finder_returns_none_when_nothing_qualifies():
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=[], swing_bars=[])
    assert f.find_target("long", Decimal("15"), Decimal("13"), Decimal("2.0")) is None


def test_level_finder_ignores_mitigated_fvg():
    # Same bullish gap [20,24], but a later bar trades back to 19 (< 20) → mitigated.
    bars = [
        _tf_bar(0, 18, 20, 17, 19, "4h"),
        _tf_bar(1, 19, 26, 19, 25, "4h"),
        _tf_bar(2, 25, 27, 24, 26, "4h"),
        _tf_bar(3, 26, 27, 19, 20, "4h"),   # low=19 trades back into gap → mitigated
    ]
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=bars, swing_bars=[])
    assert f.find_target("long", Decimal("15"), Decimal("13"), Decimal("2.0")) is None


def test_level_finder_returns_4h_fvg_below_entry_for_short():
    # Bearish FVG: b1.low=80, b3.high=76 → gap [76, 80], near edge (gap.high) = 80.
    bars = [
        _tf_bar(0, 82, 83, 80, 81, "4h"),   # b1 (low=80)
        _tf_bar(1, 81, 81, 74, 75, "4h"),   # b2 displacement down
        _tf_bar(2, 75, 76, 73, 74, "4h"),   # b3 (high=76) → gap 76..80
        _tf_bar(3, 74, 75, 72, 73, "4h"),   # stays below gap
    ]
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=bars, swing_bars=[])
    # entry=85, stop=87 → R=2, min_r=2 → min_dist=4. (85-80)=5 >= 4 → 80 qualifies.
    found = f.find_target("short", Decimal("85"), Decimal("87"), Decimal("2.0"))
    assert found is not None
    price, label = found
    assert price == Decimal("80")
    assert "4h FVG" in label


def test_level_finder_short_ignores_mitigated_bearish_fvg():
    # Same bearish gap [76, 80]; a later bar trades back up to high=81 (>=80) → mitigated.
    bars = [
        _tf_bar(0, 82, 83, 80, 81, "4h"),
        _tf_bar(1, 81, 81, 74, 75, "4h"),
        _tf_bar(2, 75, 76, 73, 74, "4h"),
        _tf_bar(3, 74, 81, 73, 80, "4h"),   # high=81 trades back into gap → mitigated
    ]
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=bars, swing_bars=[])
    assert f.find_target("short", Decimal("85"), Decimal("87"), Decimal("2.0")) is None


def test_find_target_rejects_unknown_side():
    f = HTFLevelFinder(swing_lookback=2)
    f.rebuild(fvg_bars=[], swing_bars=[])
    with pytest.raises(ValueError):
        f.find_target("buy", Decimal("10"), Decimal("9"), Decimal("2.0"))


def test_strategy_params_htf_defaults_inert():
    from app.bot_config import StrategyParams
    s = StrategyParams()
    assert s.htf_bias_enabled is False
    assert s.htf_bias_timeframe == "4h"
    assert s.htf_bias_lookback == 3
    assert s.htf_target_enabled is False
    assert s.htf_target_min_r == Decimal("2.0")
    assert s.htf_swing_timeframe == "30min"


def test_ifvg_diagnostics_before_rebuild():
    """Before any rebuild, diagnostics is neutral with no tracked FVG."""
    t = HTFBiasTracker()
    d = t.diagnostics()
    assert d["bias"] == "neutral"
    assert d["tracked_fvg"] is None
    assert d["last_inversion_ts"] is None


def test_ifvg_diagnostics_after_inversion():
    """After an inversion, diagnostics shows the new bias and inversion timestamp."""
    t = HTFBiasTracker()
    t.rebuild([
        _bar(0, 11, 12, 10, 11),
        _bar(1, 10, 10,  8,  9),
        _bar(2,  9,  9,  7,  8),   # bearish FVG [9, 10]
        _bar(3,  8, 11,  8, 10.5), # inversion → bullish
    ])
    d = t.diagnostics()
    assert d["bias"] == "bullish"
    assert d["last_inversion_ts"] is not None
    assert "T" in d["last_inversion_ts"]  # ISO format


def test_aggregate_bars_folds_1min_into_4h():
    """Aggregation must fold N consecutive 1-min bars into the correct 4h OHLCV.
    O = first bar's open, H = max high, L = min low, C = last bar's close,
    V = sum volumes. Bucket boundary at 4h-aligned UTC epoch."""
    from app.main import _aggregate_bars, _tf_to_seconds
    from datetime import datetime, timezone

    # Eight 1-min bars all within a single 4h bucket (00:00–04:00 UTC).
    base = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)
    one_min = []
    for i in range(8):
        one_min.append(Bar(
            instrument="MGC", timeframe="1min",
            ts=base + timedelta(minutes=i),
            open=Decimal(str(100 + i)),
            high=Decimal(str(110 + i)),
            low=Decimal(str(90 + i)),
            close=Decimal(str(105 + i)),
            volume=10,
        ))

    four_h = _aggregate_bars(one_min, _tf_to_seconds("4h"), "4h")
    assert len(four_h) == 1
    b = four_h[0]
    assert b.open  == Decimal("100")   # first 1min's open
    assert b.close == Decimal("112")   # last 1min's close (105+7)
    assert b.high  == Decimal("117")   # max high (110+7)
    assert b.low   == Decimal("90")    # min low (90+0)
    assert b.volume == 80              # 8 × 10
    assert b.timeframe == "4h"
    assert b.ts == base                # bucket boundary


def test_aggregate_bars_splits_across_buckets():
    """Bars spanning multiple 4h windows must produce one bar per bucket."""
    from app.main import _aggregate_bars, _tf_to_seconds
    from datetime import datetime, timezone

    base = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)
    bars = []
    # 5h of 1min bars → spans two 4h buckets (00–04 and 04–08).
    for i in range(300):  # 300 minutes = 5h
        bars.append(Bar(
            instrument="MGC", timeframe="1min",
            ts=base + timedelta(minutes=i),
            open=Decimal("100"), high=Decimal("101"),
            low=Decimal("99"), close=Decimal("100"), volume=1,
        ))

    out = _aggregate_bars(bars, _tf_to_seconds("4h"), "4h")
    assert len(out) == 2
    # First bucket has 240 bars (4h × 60min), second has 60 (the remaining hour).
    assert out[0].volume == 240
    assert out[1].volume == 60
    assert out[1].ts == base + timedelta(hours=4)
