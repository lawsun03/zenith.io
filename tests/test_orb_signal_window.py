"""B43 — ORB late-session signal cutoff (orb_signal_window_mins parameter).

Defining-behavior tests:
1. signal_window_mins=0 (default): signals fire at any time after range close (no cutoff).
2. signal_window_mins=60: bar at exactly 60min post-open (10:30 ET) is blocked;
   bar at 59min post-open (10:29 ET) fires normally.
3. signal_window_mins=60: signal fired at 55min; after stop + re-arm, new entry
   at 65min is blocked (cutoff affects NEW signal generation only).
4. Window check uses ET timezone: bars timestamped in UTC are correctly evaluated
   against the ET-based cutoff.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.sim.events import Bar
from app.strategy.orb import ORBComposer, ORBConfig, ORBDetector

ET_ZONE = __import__("zoneinfo").ZoneInfo("America/New_York")
UTC = timezone.utc


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bar(h: int, m: int, o: str, hi: str, lo: str, c: str,
         year: int = 2026, month: int = 3, day: int = 4,
         tz=None) -> Bar:
    if tz is None:
        tz = ET_ZONE
    return Bar(
        instrument="MNQ",
        timeframe="5min",
        ts=datetime(year, month, day, h, m, tzinfo=tz),
        open=Decimal(o),
        high=Decimal(hi),
        low=Decimal(lo),
        close=Decimal(c),
        volume=100,
    )


def _feed_range(det: ORBDetector, year: int = 2026, month: int = 3, day: int = 4,
                tz=None) -> None:
    """Feed 3 bars to build OR = [21000, 21020]."""
    det.on_bar(_bar(9, 30, "21005", "21015", "21000", "21010", year, month, day, tz))
    det.on_bar(_bar(9, 35, "21010", "21020", "21005", "21018", year, month, day, tz))
    det.on_bar(_bar(9, 40, "21018", "21019", "21008", "21012", year, month, day, tz))


def _breakout_bar(h: int, m: int, day: int = 4, tz=None) -> Bar:
    """A bullish bar that closes above OR high (21020) — fires a long signal."""
    return _bar(h, m, "21000", "21050", "20995", "21045", 2026, 3, day, tz)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class TestORBSignalWindow:

    def test_no_cutoff_by_default(self):
        """signal_window_mins=0 (default): signals fire late in the session."""
        cfg = ORBConfig(instrument="MNQ", signal_window_mins=0)
        det = ORBDetector(cfg)
        _feed_range(det)
        # 11:00 ET = 90 min post-open — no cutoff, should fire
        sig = det.on_bar(_breakout_bar(11, 0))
        assert sig is not None

    def test_signal_blocked_at_cutoff(self):
        """signal_window_mins=60: bar at exactly 60min post-open is blocked."""
        cfg = ORBConfig(instrument="MNQ", signal_window_mins=60)
        det = ORBDetector(cfg)
        _feed_range(det)
        # 9:30 + 60min = 10:30 ET → blocked (>= 60)
        sig = det.on_bar(_breakout_bar(10, 30))
        assert sig is None

    def test_signal_allowed_before_cutoff(self):
        """signal_window_mins=60: bar at 59min post-open (10:29 ET) fires normally."""
        cfg = ORBConfig(instrument="MNQ", signal_window_mins=60)
        det = ORBDetector(cfg)
        _feed_range(det)
        # 9:30 + 59min = 10:29 ET → allowed (< 60)
        sig = det.on_bar(_breakout_bar(10, 29))
        assert sig is not None
        assert sig.side == "long"

    def test_window_blocks_new_signals_after_rearm(self):
        """signal_window_mins=60: signal at 55min fires; reentry at 65min is blocked.

        Existing position (from the 55min signal) is unaffected — the cutoff only
        suppresses NEW signal generation from on_bar.
        """
        cfg = ORBConfig(instrument="MNQ", signal_window_mins=60, reentry_after_stop=True)
        det = ORBDetector(cfg)
        composer = ORBComposer(detector=det, reentry_after_stop=True)
        _feed_range(det)

        # Signal fires within window: 9:30 + 55min = 10:25 ET
        sig1 = det.on_bar(_breakout_bar(10, 25))
        assert sig1 is not None, "signal at 55min should fire (within window)"

        # Simulate stop → re-arm the detector for a second signal today
        composer.on_stop_loss()

        # Bar at 65min post-open (10:35 ET) would otherwise fire a second signal,
        # but the window is closed → None
        sig2 = det.on_bar(_breakout_bar(10, 35))
        assert sig2 is None, "reentry at 65min should be blocked (past window)"

    def test_window_uses_et_timezone(self):
        """Window cutoff is computed in ET: UTC-timestamped bars are correctly evaluated.

        March 4, 2026 is EST (UTC-5). Market opens at 09:30 ET = 14:30 UTC.
        signal_window_mins=60 → cutoff at 10:30 ET = 15:30 UTC.
        A breakout bar at 15:29 UTC (= 10:29 ET, 59min) should fire.
        """
        cfg = ORBConfig(instrument="MNQ", signal_window_mins=60)
        det = ORBDetector(cfg)

        # Feed range bars with UTC timestamps: 14:30-14:40 UTC = 9:30-9:40 ET
        det.on_bar(Bar(instrument="MNQ", timeframe="5min",
                       ts=datetime(2026, 3, 4, 14, 30, tzinfo=UTC),
                       open=Decimal("21005"), high=Decimal("21015"),
                       low=Decimal("21000"), close=Decimal("21010"), volume=100))
        det.on_bar(Bar(instrument="MNQ", timeframe="5min",
                       ts=datetime(2026, 3, 4, 14, 35, tzinfo=UTC),
                       open=Decimal("21010"), high=Decimal("21020"),
                       low=Decimal("21005"), close=Decimal("21018"), volume=100))
        det.on_bar(Bar(instrument="MNQ", timeframe="5min",
                       ts=datetime(2026, 3, 4, 14, 40, tzinfo=UTC),
                       open=Decimal("21018"), high=Decimal("21019"),
                       low=Decimal("21008"), close=Decimal("21012"), volume=100))

        # 15:29 UTC = 10:29 ET = 59min post-open → should fire
        sig_ok = det.on_bar(Bar(instrument="MNQ", timeframe="5min",
                                ts=datetime(2026, 3, 4, 15, 29, tzinfo=UTC),
                                open=Decimal("21000"), high=Decimal("21050"),
                                low=Decimal("20995"), close=Decimal("21045"),
                                volume=100))
        assert sig_ok is not None, "15:29 UTC (10:29 ET) should fire within window"

        # 15:30 UTC = 10:30 ET = 60min → blocked (use a fresh detector)
        det2 = ORBDetector(cfg)
        det2.on_bar(Bar(instrument="MNQ", timeframe="5min",
                        ts=datetime(2026, 3, 4, 14, 30, tzinfo=UTC),
                        open=Decimal("21005"), high=Decimal("21015"),
                        low=Decimal("21000"), close=Decimal("21010"), volume=100))
        det2.on_bar(Bar(instrument="MNQ", timeframe="5min",
                        ts=datetime(2026, 3, 4, 14, 35, tzinfo=UTC),
                        open=Decimal("21010"), high=Decimal("21020"),
                        low=Decimal("21005"), close=Decimal("21018"), volume=100))
        det2.on_bar(Bar(instrument="MNQ", timeframe="5min",
                        ts=datetime(2026, 3, 4, 14, 40, tzinfo=UTC),
                        open=Decimal("21018"), high=Decimal("21019"),
                        low=Decimal("21008"), close=Decimal("21012"), volume=100))
        sig_blocked = det2.on_bar(Bar(instrument="MNQ", timeframe="5min",
                                      ts=datetime(2026, 3, 4, 15, 30, tzinfo=UTC),
                                      open=Decimal("21000"), high=Decimal("21050"),
                                      low=Decimal("20995"), close=Decimal("21045"),
                                      volume=100))
        assert sig_blocked is None, "15:30 UTC (10:30 ET) should be blocked (>= 60min)"
