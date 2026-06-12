"""Tests for the ablation-campaign mode switches (T1 allowed_sides,
T4 trail_1r, T5 displacement_only). One test per mode's defining behavior."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import SweepEvent, Swing

ET = ZoneInfo("America/New_York")


def bar(ts, o, h, l, c, instrument="MNQ"):
    return Bar(instrument=instrument, timeframe="5min", ts=ts,
               open=Decimal(o), high=Decimal(h), low=Decimal(l),
               close=Decimal(c), volume=100)


def ny_am(i):
    return datetime(2026, 3, 4, 9, 30 + i, tzinfo=ET)


def _cfg(**kw):
    return ComposerConfig(instrument="MNQ", trend_ema_period=0, **kw)


def _short_setup(composer, with_fvg=True):
    """High-side sweep then bearish displacement -> SHORT candidate."""
    ts0 = ny_am(0)
    b_sweep = bar(ts0, "21000", "21010", "20995", "21005")
    sweep = SweepEvent(
        side="high",
        swept_swing=Swing(kind="high", price=Decimal("21008"),
                          bar_ts=ts0, confirmed_ts=ts0),
        pattern="B_one_bar",
        sweep_extreme=Decimal("21010"),
        completed_at=ts0,
        sweep_bar=b_sweep,
    )
    composer.on_sweep(b_sweep, sweep)
    composer.on_bar_close(b_sweep)
    ts1 = ny_am(1)
    b_disp = bar(ts1, "21005", "21006", "20980", "20982")
    fvg = FairValueGap(side="bearish", low=Decimal("20985"),
                       high=Decimal("21000"), created_at=ts1) if with_fvg else None
    event = DisplacementEvent(
        side="bearish", displacement_bar=b_disp, body_size=Decimal("23"),
        atr_at_event=Decimal("5"), body_to_atr=Decimal("4.6"), fvg=fvg,
    )
    return composer.on_displacement(b_disp, event), b_disp


class TestAllowedSides:
    def test_short_suppressed_when_long_only(self):
        signal, _ = _short_setup(SweepDisplacementComposer(_cfg(allowed_sides="long")))
        assert signal is None

    def test_short_fires_when_both(self):
        signal, _ = _short_setup(SweepDisplacementComposer(_cfg()))
        assert signal is not None
        assert signal.side == "short"
