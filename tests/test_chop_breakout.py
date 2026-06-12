"""chop_breakout: spec-mandated tests, one per defining behavior."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.broker.events import Bar
from app.strategy.chop_breakout import ChopBreakoutConfig, ChopBreakoutDetector
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing

T0 = datetime(2026, 3, 4, 14, 0, tzinfo=timezone.utc)


def bar(i, lo, hi, c=None, o=None, vol=100):
    lo, hi = Decimal(lo), Decimal(hi)
    c = Decimal(c) if c else (lo + hi) / 2
    o = Decimal(o) if o else c
    return Bar(instrument="MNQ", timeframe="5min", ts=T0 + timedelta(minutes=5 * i),
               open=o, high=hi, low=lo, close=c, volume=vol)


def _cfg(**kw):
    return ChopBreakoutConfig(instrument="MNQ", **kw)


def feed_history(det, n=130, i0=0):
    """Wide oscillating bars: 20-bar range ≈ 70 → the percentile history."""
    for i in range(n):
        base = 21000 + (30 if i % 2 else -30)
        det.on_bar(bar(i0 + i, str(base - 5), str(base + 5)))
    return i0 + n


def tight(i, width=4):
    return bar(i, str(21000 - width // 2), str(21000 + width // 2))


class TestGate:
    def test_chop_at_exactly_min_chop_bars(self):
        det = ChopBreakoutDetector(_cfg())
        i = feed_history(det)
        seen = []
        for k in range(40):
            det.on_bar(tight(i + k))
            seen.append((det._streak, det.state))
        for streak, state in seen:
            if streak <= 11:
                assert state == "idle"
        assert "chop" in [s for _, s in seen]
        first_chop = next(s for s in seen if s[1] == "chop")
        assert first_chop[0] == 12  # CHOP first appears exactly at streak 12

    def test_boundaries_widen_never_shrink(self):
        det = ChopBreakoutDetector(_cfg())
        i = feed_history(det)
        k = 0
        while det.state != "chop":
            det.on_bar(tight(i + k))
            k += 1
            assert k < 60, "never entered chop"
        hi0, lo0 = det.chop_high, det.chop_low
        det.on_bar(bar(i + k, str(lo0), str(hi0 + 2)))      # higher high, compressed
        assert det.chop_high == hi0 + 2 and det.chop_low == lo0
        det.on_bar(bar(i + k + 1, str(lo0 + 1), str(hi0)))  # inside bar
        assert det.chop_high == hi0 + 2 and det.chop_low == lo0  # never shrink
