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


def _long_setup(composer, with_fvg=True):
    """Low-side sweep then bullish displacement -> LONG candidate."""
    ts0 = ny_am(0)
    b_sweep = bar(ts0, "21000", "21005", "20985", "21002")
    sweep = SweepEvent(
        side="low",
        swept_swing=Swing(kind="low", price=Decimal("20992"),
                          bar_ts=ts0, confirmed_ts=ts0),
        pattern="B_one_bar",
        sweep_extreme=Decimal("20985"),
        completed_at=ts0,
        sweep_bar=b_sweep,
    )
    composer.on_sweep(b_sweep, sweep)
    composer.on_bar_close(b_sweep)
    ts1 = ny_am(1)
    b_disp = bar(ts1, "21002", "21030", "21001", "21028")
    fvg = FairValueGap(side="bullish", low=Decimal("21005"),
                       high=Decimal("21020"), created_at=ts1) if with_fvg else None
    event = DisplacementEvent(
        side="bullish", displacement_bar=b_disp, body_size=Decimal("26"),
        atr_at_event=Decimal("5"), body_to_atr=Decimal("5.2"), fvg=fvg,
    )
    return composer.on_displacement(b_disp, event), b_disp


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

    def test_long_passes_when_long_only(self):
        """B15: long signal must not be blocked when allowed_sides='long'."""
        signal, _ = _long_setup(SweepDisplacementComposer(_cfg(allowed_sides="long")))
        assert signal is not None
        assert signal.side == "long"


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


class TestAtrRefLag:
    def _feed(self, det, n, width, i0=0):
        from datetime import timedelta
        for i in range(n):
            ts = ny_am(0) + timedelta(minutes=5 * (i0 + i))
            base = Decimal("21000")
            det.on_bar(Bar(instrument="MNQ", timeframe="5min", ts=ts,
                           open=base, high=base + width, low=base - width,
                           close=base, volume=100))
        return i0 + n

    def test_lagged_atr_admits_post_flush_displacement(self):
        from datetime import timedelta
        from app.strategy.displacement import DisplacementConfig, DisplacementDetector

        def mk(lag):
            return DisplacementDetector(DisplacementConfig(
                atr_period=5, body_atr_multiple=Decimal("1.0"),
                min_absolute_body=Decimal("1.0"), atr_ref_lag_bars=lag))

        for lag, expect_event in ((0, False), (9, True)):
            det = mk(lag)
            i = self._feed(det, 8, Decimal("4"))          # calm: ATR ~ 8
            i = self._feed(det, 5, Decimal("30"), i)      # flush: ATR balloons
            # displacement candidate: body 12 (> calm ATR 8, < flushed ATR)
            ts = ny_am(0) + timedelta(minutes=5 * i)
            b2 = Bar(instrument="MNQ", timeframe="5min", ts=ts,
                     open=Decimal("20990"), high=Decimal("21003"),
                     low=Decimal("20989"), close=Decimal("21002"), volume=100)
            det.on_bar(b2)
            ts3 = ny_am(0) + timedelta(minutes=5 * (i + 1))
            b3 = Bar(instrument="MNQ", timeframe="5min", ts=ts3,
                     open=Decimal("21002"), high=Decimal("21006"),
                     low=Decimal("21000"), close=Decimal("21005"), volume=100)
            ev = det.on_bar(b3)
            assert (ev is not None) == expect_event, f"lag={lag}"


class TestMaxStopAtr:
    def _wide_swing_setup(self, **cfg_kw):
        """Short setup where swing_stop_lookback anchors the stop far away
        (bar highs at 21100 vs sweep extreme 21010)."""
        composer = SweepDisplacementComposer(
            _cfg(swing_stop_lookback=5, **cfg_kw))
        wide = bar(ny_am(0), "21090", "21100", "21085", "21095")
        composer.on_bar_close(wide)  # seeds _bar_highs with 21100
        return _short_setup(composer)

    def test_cap_falls_back_to_sweep_extreme(self):
        # swing stop = 21100.30 -> r ≈ 118 > 6×ATR(5)=30 -> fallback anchor:
        # sweep extreme 21010 + 0.30 -> r ≈ 28.3 <= 30 -> trades
        signal, _ = self._wide_swing_setup(max_stop_atr=Decimal("6"))
        assert signal is not None
        assert signal.stop == Decimal("21010.30")

    def test_cap_blocks_when_even_sweep_anchor_too_wide(self):
        # 4×ATR(5)=20 < 28.3 -> no trade
        signal, _ = self._wide_swing_setup(max_stop_atr=Decimal("4"))
        assert signal is None

    def test_cap_off_keeps_swing_anchor(self):
        signal, _ = self._wide_swing_setup()
        assert signal is not None
        assert signal.stop == Decimal("21100.30")


class TestOBFallback:
    def _setup(self, composer, prev_bar, with_fvg=False):
        """Sweep high, then bearish displacement with explicit prev_bar (b1)."""
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
            prev_bar=prev_bar,
        )
        return composer.on_displacement(b_disp, event)

    def test_no_fvg_uses_opposite_candle_as_ob_zone(self):
        composer = SweepDisplacementComposer(_cfg(confirmation="ob_fallback"))
        # b1 is BULLISH (opposite of bearish displacement) -> valid OB
        ob = bar(ny_am(0), "21002", "21009", "21000", "21007")  # o=21002 c=21007
        signal = self._setup(composer, prev_bar=ob)
        assert signal is not None
        assert signal.side == "short"
        assert signal.entry == ob.low                # near edge of the OB zone
        assert (signal.fvg_low, signal.fvg_high) == (ob.low, ob.high)

    def test_no_fvg_and_same_color_prev_bar_no_signal(self):
        composer = SweepDisplacementComposer(_cfg(confirmation="ob_fallback"))
        # b1 bearish (same direction as displacement) -> not an OB -> no signal
        same = bar(ny_am(0), "21007", "21009", "21000", "21002")  # o>c bearish
        assert self._setup(composer, prev_bar=same) is None

    def test_with_fvg_behaves_exactly_like_ifvg_mode(self):
        composer = SweepDisplacementComposer(_cfg(confirmation="ob_fallback"))
        ob = bar(ny_am(0), "21002", "21009", "21000", "21007")
        signal = self._setup(composer, prev_bar=ob, with_fvg=True)
        assert signal is not None
        assert signal.entry == Decimal("20985")      # fvg.low, unchanged iFVG path
        assert (signal.fvg_low, signal.fvg_high) == (Decimal("20985"), Decimal("21000"))
