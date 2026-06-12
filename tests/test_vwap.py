"""VWAP mean-reversion detector tests — band-fade entries, episodic re-arm,
session reset, σ warmup. Assertions use the detector's exposed vwap/sigma
properties rather than hand-computed decimals."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.events import Bar
from app.strategy.vwap import VWAPConfig, VWAPDetector

ET = ZoneInfo("America/New_York")


def bar(h, m, o, hi, lo, c, day=4, vol=100):
    return Bar(instrument="MNQ", timeframe="5min",
               ts=datetime(2026, 3, day, h, m, tzinfo=ET),
               open=Decimal(o), high=Decimal(hi), low=Decimal(lo),
               close=Decimal(c), volume=vol)


def _cfg(**kw):
    return VWAPConfig(instrument="MNQ", **kw)


def warm(det, day=4, n=8):
    """Feed n oscillating bars from 09:30 so vwap/σ are established.

    Whole bars shift together (closes ≈ ±1σ of typical price) so the
    oscillation stays well inside a 2σ band."""
    prices = [("21000", "21006", "20998", "21002"),   # tp 21002
              ("21002", "21002", "20990", "20996")]   # tp 20996
    for i in range(n):
        o, hi, lo, c = prices[i % 2]
        total = 30 + 5 * i
        sig = det.on_bar(bar(9 + total // 60, total % 60, o, hi, lo, c, day))
        assert sig is None  # oscillation inside bands
    return det


class TestVWAPDetector:
    def test_long_fade_below_lower_band(self):
        det = warm(VWAPDetector(_cfg(band_sigma=Decimal("2.0"),
                                     stop_sigma=Decimal("1.0"))))
        vwap, sigma = det.vwap, det.sigma
        assert sigma is not None and sigma > 0
        # close far below vwap - 2σ
        c = vwap - sigma * 4
        sig = det.on_bar(bar(10, 30, str(c + 2), str(c + 3), str(c - 1), str(c)))
        assert sig is not None
        assert sig.side == "long"
        assert sig.entry == c
        assert sig.target == det.vwap          # fade target = vwap at entry
        assert sig.stop == c - det.sigma       # stop_sigma=1.0
        assert sig.stop < sig.entry < sig.target

    def test_short_fade_above_upper_band(self):
        det = warm(VWAPDetector(_cfg(band_sigma=Decimal("2.0"))))
        c = det.vwap + det.sigma * 4
        sig = det.on_bar(bar(10, 30, str(c - 2), str(c + 1), str(c - 3), str(c)))
        assert sig is not None and sig.side == "short"
        assert sig.target == det.vwap and sig.target < sig.entry

    def test_no_signal_inside_bands(self):
        det = warm(VWAPDetector(_cfg(band_sigma=Decimal("2.0"))))
        c = det.vwap + det.sigma  # only 1σ out
        assert det.on_bar(bar(10, 30, str(c), str(c + 1), str(c - 1), str(c))) is None

    def test_no_signal_before_min_bars(self):
        det = VWAPDetector(_cfg(min_bars=6, band_sigma=Decimal("2.0")))
        det.on_bar(bar(9, 30, "21000", "21010", "20990", "21005"))
        det.on_bar(bar(9, 35, "21005", "21015", "20995", "20998"))
        # huge stretch on bar 3 — still inside warmup
        assert det.on_bar(bar(9, 40, "20900", "20905", "20850", "20860")) is None

    def test_disarm_then_rearm_on_vwap_retouch(self):
        det = warm(VWAPDetector(_cfg(band_sigma=Decimal("2.0"))))
        c = det.vwap - det.sigma * 4
        assert det.on_bar(bar(10, 30, str(c + 2), str(c + 3), str(c - 1), str(c))) is not None
        # still below band next bar -> disarmed, no second signal
        c2 = det.vwap - det.sigma * 4
        assert det.on_bar(bar(10, 35, str(c2), str(c2 + 1), str(c2 - 1), str(c2))) is None
        # close back AT vwap -> re-arms the long side
        v = det.vwap
        det.on_bar(bar(10, 40, str(v), str(v + 1), str(v - 1), str(v)))
        c3 = det.vwap - det.sigma * 4
        assert det.on_bar(bar(10, 45, str(c3 + 1), str(c3 + 2), str(c3 - 1), str(c3))) is not None

    def test_session_resets_at_anchor(self):
        det = warm(VWAPDetector(_cfg()), day=4)
        # next day's first bar: vwap == its typical price
        b = bar(9, 30, "22000", "22010", "21990", "22005", day=5)
        det.on_bar(b)
        tp = (b.high + b.low + b.close) / 3
        assert det.vwap == tp

    def test_zero_sigma_no_signal(self):
        det = VWAPDetector(_cfg(min_bars=2, band_sigma=Decimal("2.0")))
        for i in range(4):  # identical bars -> σ == 0
            det.on_bar(bar(9, 30 + 5 * i, "21000", "21000", "21000", "21000"))
        assert det.on_bar(bar(10, 0, "21000", "21000", "21000", "21000")) is None
