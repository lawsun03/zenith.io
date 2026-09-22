"""B55 — iFVG Silver Bullet window (10:00-11:00 ET) gate.

Defining-behavior tests:
1. silver_bullet_only=False (default): signals unchanged vs baseline.
2. =True: valid iFVG at 10:30 ET fires; same setup at 09:45 ET and 11:15 ET suppressed.
3. Window boundary: 10:00:00 ET fires; 11:00:00 ET does NOT (half-open interval).
4. ET timezone correct across UTC: a bar at 15:30 UTC maps to 10:30 ET (winter) and fires.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.sim.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing, SweepEvent

ET = ZoneInfo("America/New_York")

# 2026-01-06 (Tuesday), winter time (ET = UTC-5). Build UTC equivalents:
# 09:45 ET = 14:45 UTC
# 10:00 ET = 15:00 UTC
# 10:30 ET = 15:30 UTC
# 11:00 ET = 16:00 UTC
# 11:15 ET = 16:15 UTC
_09_45_ET = datetime(2026, 1, 6, 14, 45, tzinfo=timezone.utc)
_10_00_ET = datetime(2026, 1, 6, 15, 0,  tzinfo=timezone.utc)
_10_30_ET = datetime(2026, 1, 6, 15, 30, tzinfo=timezone.utc)
_11_00_ET = datetime(2026, 1, 6, 16, 0,  tzinfo=timezone.utc)
_11_15_ET = datetime(2026, 1, 6, 16, 15, tzinfo=timezone.utc)


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


def _composer(silver_bullet_only: bool) -> SweepDisplacementComposer:
    cfg = ComposerConfig(
        instrument="MNQ",
        trend_ema_period=0,
        stop_buffer=Decimal("0.30"),
        silver_bullet_only=silver_bullet_only,
    )
    return SweepDisplacementComposer(cfg)


def _emit(composer: SweepDisplacementComposer, sweep_ts: datetime, disp_ts: datetime):
    """Arm sweep at sweep_ts; fire displacement at disp_ts."""
    composer.on_sweep(_bar(sweep_ts), _low_sweep(sweep_ts))
    return composer.on_displacement(_bar(disp_ts), _disp(disp_ts))


class TestSilverBullet:

    def test_default_off_fires_any_hour(self):
        """silver_bullet_only=False (default): signals fire outside 10-11 ET unchanged."""
        c = _composer(False)
        # 09:45 ET — before the silver bullet window
        sig = _emit(c, _09_45_ET, _09_45_ET)
        assert sig is not None, "Default off: 09:45 ET should fire"

    def test_silver_bullet_fires_in_window(self):
        """silver_bullet_only=True: valid iFVG at 10:30 ET fires."""
        assert _10_30_ET.astimezone(ET).hour == 10, "sanity: 15:30 UTC = 10:30 ET"
        c = _composer(True)
        sig = _emit(c, _10_00_ET, _10_30_ET)
        assert sig is not None, "10:30 ET is inside the 10:00-11:00 window and should fire"

    def test_silver_bullet_suppresses_before_window(self):
        """silver_bullet_only=True: same setup at 09:45 ET is suppressed."""
        assert _09_45_ET.astimezone(ET).hour == 9, "sanity: 14:45 UTC = 09:45 ET"
        c = _composer(True)
        # Sweep armed at 09:45 ET (inside NY AM killzone default, but outside SB window)
        sig = _emit(c, _09_45_ET, _09_45_ET)
        assert sig is None, "09:45 ET is before the Silver Bullet window and should be suppressed"

    def test_silver_bullet_suppresses_after_window(self):
        """silver_bullet_only=True: displacement at 11:15 ET is suppressed."""
        assert _11_15_ET.astimezone(ET).hour == 11, "sanity: 16:15 UTC = 11:15 ET"
        c = _composer(True)
        # Arm sweep inside window; fire displacement outside window
        c.on_sweep(_bar(_10_00_ET), _low_sweep(_10_00_ET))
        sig = c.on_displacement(_bar(_11_15_ET), _disp(_11_15_ET))
        assert sig is None, "11:15 ET is after the Silver Bullet window and should be suppressed"

    def test_window_boundary_open_at_10_00(self):
        """10:00:00 ET fires (window is inclusive on the left boundary)."""
        assert _10_00_ET.astimezone(ET).hour == 10, "sanity: 15:00 UTC = 10:00 ET"
        c = _composer(True)
        sig = _emit(c, _10_00_ET, _10_00_ET)
        assert sig is not None, "10:00 ET exactly is inside the window [10:00, 11:00)"

    def test_window_boundary_closed_at_11_00(self):
        """11:00:00 ET does NOT fire (window is exclusive on the right boundary)."""
        assert _11_00_ET.astimezone(ET).hour == 11, "sanity: 16:00 UTC = 11:00 ET"
        c = _composer(True)
        c.on_sweep(_bar(_10_00_ET), _low_sweep(_10_00_ET))
        sig = c.on_displacement(_bar(_11_00_ET), _disp(_11_00_ET))
        assert sig is None, "11:00 ET exactly is outside the window [10:00, 11:00)"

    def test_sweep_state_preserved_outside_window(self):
        """Sweep armed at 09:45 ET; displacement suppressed at 09:45; fires at 10:30 ET."""
        c = _composer(True)
        # Arm sweep before window
        c.on_sweep(_bar(_09_45_ET), _low_sweep(_09_45_ET))
        assert len(c.awaiting) == 1, "Sweep armed before window"

        # Displacement outside window — suppressed but sweep NOT consumed
        result_before = c.on_displacement(_bar(_09_45_ET), _disp(_09_45_ET))
        assert result_before is None, "09:45 ET displacement should be suppressed"
        assert len(c.awaiting) == 1, "Sweep state must be preserved during suppressed period"

        # Displacement inside window — fires
        result_in = c.on_displacement(_bar(_10_30_ET), _disp(_10_30_ET))
        assert result_in is not None, "10:30 ET displacement should fire after suppressed sweep"
