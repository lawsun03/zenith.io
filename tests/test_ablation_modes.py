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


class TestTrail1R:
    def test_ratchets_at_exact_1r_and_exits_on_stop_only(self):
        from app.broker.paper import PaperBroker

        async def run():
            br = PaperBroker(starting_balance=Decimal("50000"),
                             slippage_ticks_market=0, trail_1r=True)
            fills = []

            async def collect(f):
                fills.append(f)

            br.on_fill(collect)
            await br.connect()
            ts = datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc)
            await br.inject_bar(bar(ts, "21000", "21000", "21000", "21000"))
            res = await br.place_bracket(
                "MNQ", "long", 2,
                entry=Decimal("21000"), stop=Decimal("20990"),
                target=Decimal("21035"),
            )
            assert res.success
            b = br._open[res.entry_order_id]
            assert b.trail_r == Decimal("10")
            assert b.partial_target is None  # no partials in trail mode

            # +1R high: stop ratchets to BE exactly (effective next bar)
            await br.inject_bar(bar(ts, "21000", "21010", "20995", "21008"))
            assert b.stop == Decimal("21000")

            # high crosses the old TP (21035): must NOT take profit; +3R
            # reached so stop ratchets to +2R
            await br.inject_bar(bar(ts, "21008", "21036", "21005", "21030"))
            assert res.entry_order_id in br._open
            assert b.stop == Decimal("21020")

            # low touches the ratcheted stop -> stop exit at the stop price
            await br.inject_bar(bar(ts, "21030", "21031", "21019", "21022"))
            assert res.entry_order_id not in br._open
            exit_fill = fills[-1]
            assert exit_fill.is_stop
            assert exit_fill.fill_price == Decimal("21020")

        asyncio.run(run())


class TestDisplacementOnly:
    def test_signal_fires_without_ifvg(self):
        composer = SweepDisplacementComposer(_cfg(confirmation="displacement_only"))
        signal, b_disp = _short_setup(composer, with_fvg=False)
        assert signal is not None
        assert signal.side == "short"
        assert signal.entry == b_disp.close          # confirmation-bar close
        assert signal.fvg_low is None and signal.fvg_high is None
        # stop unchanged: past sweep extreme (21010) + stop_buffer (0.30)
        assert signal.stop == Decimal("21010.30")

    def test_default_mode_still_requires_ifvg(self):
        signal, _ = _short_setup(SweepDisplacementComposer(_cfg()), with_fvg=False)
        assert signal is None
