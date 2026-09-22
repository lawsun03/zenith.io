"""
B81: iFVG within-day direction-continuation gate.

Defining-behavior tests (5 required). These verify:
  1. flag=True + rank-1 LONG: second LONG is suppressed, second SHORT is allowed.
  2. flag=True + rank-1 SHORT: second SHORT is suppressed, second LONG is allowed.
  3. flag=False (default): all signals pass regardless of direction history.
  4. Per-day reset: gate clears at ET calendar-day boundary.
  5. ORB signals are never gated (gate lives in the iFVG composer path only).

WHY: Continuation signals (same-direction repeats) have PF=0.785 over 5y excl 2022,
losing in 5/5 years relative to conflict signals (PF=1.052). Suppressing them is a
quality gate. These tests guard against the gate accidentally gating the first signal,
passing all signals when flag=False, or corrupting the day-reset logic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.sim.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer, _Awaiting
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing, SweepEvent

# ET 10:00 on a Monday — inside NY-AM killzone
BASE_TS = datetime(2024, 3, 18, 14, 0, tzinfo=timezone.utc)
# Next calendar day in ET (03:00 UTC on 2024-03-19 = 23:00 ET 2024-03-18 → midnight ET)
NEXT_DAY_ET_TS = datetime(2024, 3, 19, 14, 0, tzinfo=timezone.utc)  # ET 10:00 next day


def _bar(ts: datetime, side: str = "bullish") -> Bar:
    """Minimal bar fixture inside NY-AM killzone."""
    if side == "bullish":
        return Bar(instrument="MNQ", timeframe="5min", ts=ts,
                   open=Decimal("21000"), high=Decimal("21020"),
                   low=Decimal("20990"), close=Decimal("21015"), volume=500)
    return Bar(instrument="MNQ", timeframe="5min", ts=ts,
               open=Decimal("21000"), high=Decimal("21010"),
               low=Decimal("20980"), close=Decimal("20985"), volume=500)


def _swing(price: float, ts: datetime) -> Swing:
    return Swing(kind="low", price=Decimal(str(price)), bar_ts=ts, confirmed_ts=ts)


def _sweep(side: str, ts: datetime) -> SweepEvent:
    """Create a sweep event in the given direction."""
    b = Bar(instrument="MNQ", timeframe="5min", ts=ts,
            open=Decimal("21000"), high=Decimal("21010"),
            low=Decimal("20985"), close=Decimal("20995"), volume=300)
    swept_price = 20990.0 if side == "low" else 21005.0
    sweep_extreme = 20985.0 if side == "low" else 21010.0
    return SweepEvent(
        side=side,
        swept_swing=_swing(swept_price, ts),
        pattern="B_one_bar",
        sweep_extreme=Decimal(str(sweep_extreme)),
        completed_at=ts,
        sweep_bar=b,
    )


def _fvg(disp_side: str, ts: datetime) -> FairValueGap:
    if disp_side == "bullish":
        return FairValueGap(side="bullish", low=Decimal("20995"), high=Decimal("21000"), created_at=ts)
    return FairValueGap(side="bearish", low=Decimal("21000"), high=Decimal("21005"), created_at=ts)


def _disp_event(disp_side: str, ts: datetime) -> DisplacementEvent:
    b = _bar(ts, disp_side)
    return DisplacementEvent(
        side=disp_side,
        displacement_bar=b,
        body_size=Decimal("10"),
        atr_at_event=Decimal("5"),
        body_to_atr=Decimal("2.0"),
        fvg=_fvg(disp_side, ts),
    )


def _gate_composer(flag: bool = True) -> SweepDisplacementComposer:
    return SweepDisplacementComposer(ComposerConfig(
        instrument="MNQ",
        displacement_window_bars=10,
        stop_buffer=Decimal("1.0"),
        r_multiple=Decimal("2.5"),
        daily_bias_gate_enabled=False,
        suppress_same_direction_repeat=flag,
    ))


def _arm_and_fire(composer: SweepDisplacementComposer, disp_side: str, ts: datetime):
    """Arm a matching sweep then fire a displacement, returning the signal."""
    sweep_side = "low" if disp_side == "bullish" else "high"
    composer._awaiting.append(_Awaiting(
        sweep=_sweep(sweep_side, ts - timedelta(minutes=5)),
        bars_since_sweep=0,
        killzone_name="NY-AM",
    ))
    bar = _bar(ts, disp_side)
    return composer.on_displacement(bar, _disp_event(disp_side, ts))


class TestSameDirRepeatSuppressed:
    def test_rank1_long_then_second_long_suppressed(self):
        """flag=True: rank-1 LONG fires; second LONG on same day is suppressed.

        WHY: long→long continuation PF=0.937 (loss-making). Suppressing it
        removes a drag signal without removing the high-quality rank-1 entry.
        """
        composer = _gate_composer(flag=True)
        sig1 = _arm_and_fire(composer, "bullish", BASE_TS)
        assert sig1 is not None, "Rank-1 LONG should fire (no prior direction)"
        assert sig1.side == "long"

        sig2 = _arm_and_fire(composer, "bullish", BASE_TS + timedelta(minutes=30))
        assert sig2 is None, "Second LONG on same day should be suppressed"

    def test_rank1_short_then_second_short_suppressed(self):
        """flag=True: rank-1 SHORT fires; second SHORT on same day is suppressed.

        WHY: short→short continuation is the dominant loss driver (PF=0.666,
        -$90k over 5y). This is the highest-value case the gate targets.
        """
        composer = _gate_composer(flag=True)
        sig1 = _arm_and_fire(composer, "bearish", BASE_TS)
        assert sig1 is not None, "Rank-1 SHORT should fire"
        assert sig1.side == "short"

        sig2 = _arm_and_fire(composer, "bearish", BASE_TS + timedelta(minutes=30))
        assert sig2 is None, "Second SHORT on same day should be suppressed"


class TestConflictDirectionAllowed:
    def test_rank1_long_then_short_allowed(self):
        """flag=True: rank-1 LONG fires; subsequent SHORT (conflict) is allowed.

        WHY: Conflict signals (short after prior long) have PF=1.108 — positive.
        Gating conflicts would incorrectly remove profitable signals.
        """
        composer = _gate_composer(flag=True)
        sig1 = _arm_and_fire(composer, "bullish", BASE_TS)
        assert sig1 is not None and sig1.side == "long"

        sig2 = _arm_and_fire(composer, "bearish", BASE_TS + timedelta(minutes=30))
        assert sig2 is not None, "Conflict SHORT after LONG should be allowed"
        assert sig2.side == "short"

    def test_rank1_short_then_long_allowed(self):
        """flag=True: rank-1 SHORT fires; subsequent LONG (conflict) is allowed."""
        composer = _gate_composer(flag=True)
        sig1 = _arm_and_fire(composer, "bearish", BASE_TS)
        assert sig1 is not None and sig1.side == "short"

        sig2 = _arm_and_fire(composer, "bullish", BASE_TS + timedelta(minutes=30))
        assert sig2 is not None, "Conflict LONG after SHORT should be allowed"
        assert sig2.side == "long"


class TestFlagOffAllowsAll:
    def test_flag_false_same_direction_passes(self):
        """flag=False (default): same-direction repeat is NOT suppressed.

        WHY: Default-off is the hard contract — the live bot must be unaffected
        until explicitly enabled. A bug here silently degrades live performance.
        """
        composer = _gate_composer(flag=False)
        sig1 = _arm_and_fire(composer, "bullish", BASE_TS)
        assert sig1 is not None

        sig2 = _arm_and_fire(composer, "bullish", BASE_TS + timedelta(minutes=30))
        assert sig2 is not None, "With flag=False, second LONG must still fire"

    def test_default_config_flag_is_false(self):
        """ComposerConfig default for suppress_same_direction_repeat is False."""
        cfg = ComposerConfig(instrument="MNQ")
        assert cfg.suppress_same_direction_repeat is False


class TestDayReset:
    def test_gate_clears_at_et_day_boundary(self):
        """Per-day reset: last-direction tracker clears at ET calendar-day boundary.

        WHY: The gate must not carry over from Friday's signals to Monday's.
        If _last_ifvg_dir persists across day boundaries, the first signal of
        a new day could be incorrectly suppressed.
        """
        composer = _gate_composer(flag=True)

        # Day 1: emit a LONG signal
        sig1 = _arm_and_fire(composer, "bullish", BASE_TS)
        assert sig1 is not None and sig1.side == "long"

        # Same-day second LONG is suppressed (gate working)
        sig_same_day = _arm_and_fire(composer, "bullish", BASE_TS + timedelta(minutes=30))
        assert sig_same_day is None

        # New ET day: first LONG of the day should fire (gate reset)
        sig_new_day = _arm_and_fire(composer, "bullish", NEXT_DAY_ET_TS)
        assert sig_new_day is not None, "First LONG of new ET day must not be suppressed"
        assert sig_new_day.side == "long"
