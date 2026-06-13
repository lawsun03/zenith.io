"""B30 — Day-of-week (DOW) filter: skip_trading_days parameter.

Defining-behavior tests:
1. iFVG skip_trading_days=["Tuesday"]: Tuesday bar suppressed; Monday bar fires.
2. ORB skip_trading_days=["Monday"]: Monday breakout returns None; Tuesday breakout fires.
3. skip_trading_days=[] (default): all days fire normally (unchanged behavior).
4. DOW check uses ET timezone: bar at 02:00 UTC Tuesday (= 21:00 ET Monday) is correctly
   identified as Monday in ET and suppressed when skip_trading_days=["Monday"].
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing, SweepEvent
from app.strategy.orb import ORBConfig, ORBDetector

ET = ZoneInfo("America/New_York")

# 14:00 UTC = 10:00 ET — inside NY AM killzone, suitable for sweep arming.
_MONDAY = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)   # Mon Jan 5 10:00 ET
_TUESDAY = datetime(2026, 1, 6, 14, 0, tzinfo=timezone.utc)  # Tue Jan 6 10:00 ET


# --- iFVG test helpers ---

def _bar(ts: datetime) -> Bar:
    return Bar(
        instrument="MNQ", timeframe="5min", ts=ts,
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("97"), close=Decimal("101"),
        volume=500,
    )


def _disp(ts: datetime) -> DisplacementEvent:
    fvg = FairValueGap(side="bearish", low=Decimal("98.50"), high=Decimal("99.70"), created_at=ts)
    return DisplacementEvent(
        side="bullish",
        displacement_bar=_bar(ts),
        body_size=Decimal("1.0"),
        atr_at_event=Decimal("5.0"),
        body_to_atr=Decimal("0.2"),
        fvg=fvg,
    )


def _low_sweep(ts: datetime) -> SweepEvent:
    return SweepEvent(
        side="low",
        swept_swing=Swing(
            kind="low", price=Decimal("97.5"),
            bar_ts=ts - timedelta(minutes=5),
            confirmed_ts=ts - timedelta(minutes=2),
        ),
        pattern="B_one_bar",
        sweep_extreme=Decimal("97.0"),
        completed_at=ts,
        sweep_bar=_bar(ts),
    )


def _ifvg_composer(skip_days: list[str]) -> SweepDisplacementComposer:
    cfg = ComposerConfig(
        instrument="MNQ",
        trend_ema_period=0,
        stop_buffer=Decimal("0.30"),
        skip_trading_days=skip_days,
    )
    return SweepDisplacementComposer(cfg)


def _emit(composer: SweepDisplacementComposer, ts: datetime):
    """Arm a sweep then fire a displacement at the given timestamp."""
    b = _bar(ts)
    composer.on_sweep(b, _low_sweep(ts))
    return composer.on_displacement(b, _disp(ts))


# --- ORB test helpers ---

def _orb_bar(y, mo, d, h, m, o, hi, lo, c) -> Bar:
    return Bar(
        instrument="MNQ", timeframe="5min",
        ts=datetime(y, mo, d, h, m, tzinfo=ET),
        open=Decimal(o), high=Decimal(hi), low=Decimal(lo),
        close=Decimal(c), volume=100,
    )


def _feed_range(det: ORBDetector, y, mo, d) -> None:
    """Build OR = 21000-21020 on the given calendar date."""
    det.on_bar(_orb_bar(y, mo, d, 9, 30, "21005", "21015", "21000", "21010"))
    det.on_bar(_orb_bar(y, mo, d, 9, 35, "21010", "21020", "21005", "21018"))
    det.on_bar(_orb_bar(y, mo, d, 9, 40, "21018", "21019", "21008", "21012"))


class TestDOWFilter:

    def test_ifvg_tuesday_suppressed_monday_fires(self):
        """iFVG: skip_trading_days=["Tuesday"] suppresses Tuesday, passes Monday."""
        # Monday fires
        c_mon = _ifvg_composer(["Tuesday"])
        assert _emit(c_mon, _MONDAY) is not None, "Monday should fire"

        # Tuesday suppressed
        c_tue = _ifvg_composer(["Tuesday"])
        assert _emit(c_tue, _TUESDAY) is None, "Tuesday should be suppressed"

    def test_orb_monday_suppressed_tuesday_fires(self):
        """ORB: skip_trading_days=["Monday"] suppresses Monday breakout; Tuesday fires."""
        cfg = ORBConfig(instrument="MNQ", skip_trading_days=["Monday"])
        det = ORBDetector(cfg)

        # Monday: range builds, but breakout bar suppressed
        _feed_range(det, 2026, 1, 5)  # 2026-01-05 = Monday
        sig_mon = det.on_bar(_orb_bar(2026, 1, 5, 9, 45, "21015", "21035", "21010", "21030"))
        assert sig_mon is None, "Monday breakout should be suppressed"

        # Tuesday: range builds and breakout fires
        _feed_range(det, 2026, 1, 6)  # 2026-01-06 = Tuesday
        sig_tue = det.on_bar(_orb_bar(2026, 1, 6, 9, 45, "21015", "21035", "21010", "21030"))
        assert sig_tue is not None, "Tuesday breakout should fire"
        assert sig_tue.side == "long"

    def test_default_skip_empty_all_days_fire(self):
        """skip_trading_days=[] (default): Tuesday iFVG and Monday ORB both fire."""
        # iFVG Tuesday fires with default
        c = _ifvg_composer([])
        assert _emit(c, _TUESDAY) is not None, "Tuesday iFVG should fire (no skip)"

        # ORB Monday fires with default
        cfg = ORBConfig(instrument="MNQ", skip_trading_days=[])
        det = ORBDetector(cfg)
        _feed_range(det, 2026, 1, 5)  # Monday
        sig = det.on_bar(_orb_bar(2026, 1, 5, 9, 45, "21015", "21035", "21010", "21030"))
        assert sig is not None, "Monday ORB should fire (no skip)"

    def test_et_timezone_used_not_utc(self):
        """DOW filter uses ET, not UTC: bar at 02:00 UTC Tuesday = 21:00 ET Monday
        is suppressed when skip_trading_days=['Monday']."""
        # 2026-01-06 02:00 UTC = 2026-01-05 21:00 ET (UTC-5, winter)
        cross_day_ts = datetime(2026, 1, 6, 2, 0, tzinfo=timezone.utc)
        assert cross_day_ts.strftime("%A") == "Tuesday", "sanity: UTC weekday is Tuesday"
        assert cross_day_ts.astimezone(ET).strftime("%A") == "Monday", "sanity: ET weekday is Monday"

        # Arm sweep on Monday inside killzone (14:00 UTC = 10:00 ET, stays in _awaiting)
        composer = _ifvg_composer(["Monday"])
        arm_ts = _MONDAY
        arm_bar = _bar(arm_ts)
        composer.on_sweep(arm_bar, _low_sweep(arm_ts))
        assert len(composer.awaiting) == 1, "sweep armed"

        # Fire displacement at 02:00 UTC Tuesday (= 21:00 ET Monday): must be suppressed
        sig = composer.on_displacement(_bar(cross_day_ts), _disp(cross_day_ts))
        assert sig is None, "Bar on Monday ET should be suppressed (DOW filter uses ET)"
