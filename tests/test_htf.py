from datetime import datetime, timezone, timedelta
from decimal import Decimal

from app.broker.events import Bar
from app.strategy.htf import HTFBiasTracker, HTFLevelFinder


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
