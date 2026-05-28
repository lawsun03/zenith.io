from datetime import datetime, timezone, timedelta
from decimal import Decimal

from app.broker.events import Bar
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


def test_bias_neutral_when_insufficient_data():
    t = HTFBiasTracker(lookback=2)
    t.rebuild([_bar(0, 10, 11, 9, 10)])
    assert t.bias() == "neutral"


def test_bias_bullish_on_higher_highs_and_higher_lows():
    t = HTFBiasTracker(lookback=2)
    t.rebuild(_swing_sequence([(12, 8), (14, 10)]))
    assert t.bias() == "bullish"


def test_bias_bearish_on_lower_highs_and_lower_lows():
    t = HTFBiasTracker(lookback=2)
    t.rebuild(_swing_sequence([(14, 10), (12, 8)]))
    assert t.bias() == "bearish"


def test_bias_neutral_on_mixed_structure():
    t = HTFBiasTracker(lookback=2)
    t.rebuild(_swing_sequence([(12, 10), (14, 8)]))
    assert t.bias() == "neutral"


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
