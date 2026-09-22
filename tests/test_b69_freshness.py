"""
B69: iFVG setup freshness — displacement_ts instrumentation.

Defining-behavior tests. These verify:
  1. Signal.displacement_ts defaults to None (backward compat — non-iFVG paths
     hand-build Signals without this field).
  2. SweepDisplacementComposer.on_displacement populates displacement_ts from
     DisplacementEvent.displacement_bar.ts.
  3. gap_bars = (created_at - displacement_ts) / 5min is computable and correct.
  4. Multiple bars between displacement and inversion produce the expected gap.

WHY: The freshness hypothesis (fresh invertion = stronger conviction) requires
this timestamp to be accurate. If displacement_ts is wrong or missing, the
analysis script produces garbage buckets and the GO/NO-GO decision is invalid.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.sim.events import Bar
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer, _Awaiting
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing, SweepEvent

BASE_TS = datetime(2024, 3, 14, 14, 0, tzinfo=timezone.utc)   # ET 10:00 — inside NY-AM killzone


def _bar(i: int, o: float, h: float, lo: float, c: float, instrument: str = "MNQ") -> Bar:
    return Bar(
        instrument=instrument, timeframe="5min",
        ts=BASE_TS + timedelta(minutes=5 * i),
        open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(lo)), close=Decimal(str(c)),
        volume=500,
    )


def _swing(price: float, bar_i: int) -> Swing:
    ts = BASE_TS + timedelta(minutes=5 * bar_i)
    return Swing(kind="low", price=Decimal(str(price)), bar_ts=ts, confirmed_ts=ts)


def _sweep_event(price: float, bar_i: int) -> SweepEvent:
    b = _bar(bar_i, price - 2, price, price - 5, price - 3)
    return SweepEvent(
        side="low",
        swept_swing=_swing(price - 1, bar_i),
        pattern="B_one_bar",
        sweep_extreme=Decimal(str(price)),
        completed_at=b.ts,
        sweep_bar=b,
    )


def _displacement_event(disp_bar: Bar, fvg_low: float, fvg_high: float) -> DisplacementEvent:
    fvg = FairValueGap(
        side="bullish",
        low=Decimal(str(fvg_low)),
        high=Decimal(str(fvg_high)),
        created_at=disp_bar.ts,
    )
    return DisplacementEvent(
        side="bullish",
        displacement_bar=disp_bar,
        body_size=Decimal("10"),
        atr_at_event=Decimal("5"),
        body_to_atr=Decimal("2.0"),
        fvg=fvg,
    )


def _composer() -> SweepDisplacementComposer:
    return SweepDisplacementComposer(ComposerConfig(
        instrument="MNQ",
        displacement_window_bars=10,
        stop_buffer=Decimal("1.0"),
        r_multiple=Decimal("2.5"),
        # Disable all extra gates for clean testing.
        daily_bias_gate_enabled=False,
    ))


class TestSignalDisplacementTsDefault:
    def test_signal_displacement_ts_defaults_to_none(self):
        """Hand-built Signal (non-iFVG path) has displacement_ts=None by default.

        WHY: Non-iFVG engines (ORB, VWAP) and unit tests build Signals directly.
        Adding a required field here would break every existing call site.
        """
        s = Signal(
            instrument="MNQ", side="long",
            entry=Decimal("21000"), stop=Decimal("20990"), target=Decimal("21025"),
            created_at=BASE_TS,
            killzone="NY-AM", sweep_pattern="B_one_bar",
            sweep_extreme=Decimal("20985"), fvg_low=None, fvg_high=None,
            rationale="test",
        )
        assert s.displacement_ts is None

    def test_signal_accepts_explicit_displacement_ts(self):
        """Signal stores an explicit displacement_ts correctly."""
        disp_ts = BASE_TS - timedelta(minutes=10)
        s = Signal(
            instrument="MNQ", side="long",
            entry=Decimal("21000"), stop=Decimal("20990"), target=Decimal("21025"),
            created_at=BASE_TS,
            killzone="NY-AM", sweep_pattern="B_one_bar",
            sweep_extreme=Decimal("20985"), fvg_low=None, fvg_high=None,
            rationale="test",
            displacement_ts=disp_ts,
        )
        assert s.displacement_ts == disp_ts


class TestComposerPopulatesDisplacementTs:
    def test_displacement_ts_matches_fvg_created_at(self):
        """on_displacement sets signal.displacement_ts = event.fvg.created_at.

        WHY: event.displacement_bar.ts is structurally always 1 bar before
        signal.created_at (the 3-bar detection window enforces this). The meaningful
        freshness metric is the FVG's age: how long ago was the FVG formed before
        it was inverted? That's event.fvg.created_at, not displacement_bar.ts.
        """
        fvg_created_ts = BASE_TS + timedelta(minutes=5)  # FVG formed 1 bar after BASE_TS
        composer = _composer()

        sweep = _sweep_event(price=20990, bar_i=0)
        composer._awaiting.append(_Awaiting(
            sweep=sweep,
            bars_since_sweep=0,
            killzone_name="NY-AM",
        ))

        # Displacement bar fires at bar 7 (inverts an FVG formed at fvg_created_ts = bar 1).
        disp_bar = _bar(7, 20995, 21010, 20993, 21008)
        event = _displacement_event(disp_bar, fvg_low=20995, fvg_high=21000)
        # Override fvg.created_at to simulate an FVG formed 6 bars earlier.
        from app.strategy.displacement import FairValueGap
        old_fvg = FairValueGap(
            side="bullish",
            low=Decimal("20995"),
            high=Decimal("21000"),
            created_at=fvg_created_ts,
        )
        from dataclasses import replace as dc_replace
        event = dc_replace(event, fvg=old_fvg)

        inversion_bar = _bar(8, 20998, 21005, 20996, 21002)
        signal = composer.on_displacement(inversion_bar, event)

        assert signal is not None, "Expected a signal to be emitted"
        assert signal.displacement_ts == fvg_created_ts
        # Sanity: created_at is the inversion-confirmation bar, not fvg_created_ts.
        assert signal.created_at == inversion_bar.ts
        assert signal.created_at != signal.displacement_ts

    def test_gap_bars_computed_correctly(self):
        """gap_bars = (created_at - displacement_ts) / timeframe = FVG age.

        WHY: The analysis script uses this formula. If gap_bars is always 1
        (because displacement_bar.ts was used instead of fvg.created_at), the
        freshness buckets collapse and the GO/NO-GO decision is meaningless.
        """
        composer = _composer()
        sweep = _sweep_event(price=20990, bar_i=0)
        composer._awaiting.append(_Awaiting(
            sweep=sweep, bars_since_sweep=0, killzone_name="NY-AM",
        ))

        fvg_created_ts = BASE_TS + timedelta(minutes=5 * 2)  # FVG formed at bar 2
        disp_bar = _bar(7, 20995, 21010, 20993, 21008)       # inversion at bar 7
        event = _displacement_event(disp_bar, fvg_low=20995, fvg_high=21000)
        from app.strategy.displacement import FairValueGap
        from dataclasses import replace as dc_replace
        old_fvg = FairValueGap(side="bullish", low=Decimal("20995"),
                               high=Decimal("21000"), created_at=fvg_created_ts)
        event = dc_replace(event, fvg=old_fvg)
        inversion_bar = _bar(8, 20998, 21005, 20996, 21002)  # signal bar

        signal = composer.on_displacement(inversion_bar, event)
        assert signal is not None

        timeframe_seconds = 5 * 60
        gap = (signal.created_at - signal.displacement_ts).total_seconds()
        gap_bars = gap / timeframe_seconds
        # bar 8 - bar 2 = 6 bars of FVG age
        assert gap_bars == pytest.approx(6.0)

    def test_no_fvg_gives_none_displacement_ts(self):
        """When event.fvg is None (displacement-only mode), displacement_ts is None.

        WHY: The displacement-only confirmation path (T5 semantics) has no FVG zone.
        Storing displacement_bar.ts there would give a structurally meaningless gap=1.
        Storing None is more honest — the analysis script can skip those trades.
        """
        composer = SweepDisplacementComposer(ComposerConfig(
            instrument="MNQ",
            displacement_window_bars=10,
            stop_buffer=Decimal("1.0"),
            r_multiple=Decimal("2.5"),
            confirmation="displacement_only",
            daily_bias_gate_enabled=False,
        ))
        sweep = _sweep_event(price=20990, bar_i=0)
        composer._awaiting.append(_Awaiting(
            sweep=sweep, bars_since_sweep=0, killzone_name="NY-AM",
        ))

        disp_bar = _bar(1, 20995, 21010, 20993, 21008)
        # fvg=None simulates the displacement-only path
        from app.strategy.displacement import DisplacementEvent
        event = DisplacementEvent(
            side="bullish",
            displacement_bar=disp_bar,
            body_size=Decimal("10"),
            atr_at_event=Decimal("5"),
            body_to_atr=Decimal("2.0"),
            fvg=None,
        )
        inversion_bar = _bar(2, 20998, 21005, 20996, 21002)
        signal = composer.on_displacement(inversion_bar, event)

        # displacement-only still produces a signal (T5 semantics), but no displacement_ts.
        if signal is not None:
            assert signal.displacement_ts is None
