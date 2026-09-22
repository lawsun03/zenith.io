"""B35 — Daily-bias directional gate (ICT "Power of Three").

Defining-behavior tests:
1. Gate off (default): signals unchanged — a signal fires with no prior-day data.
2. Long bias (prior close > open): short signal suppressed; long signal fires.
3. Short bias (prior close < open): long signal suppressed; short signal fires.
4. Long bias, price >= prior-day high: long suppressed (target already consumed).
5. ET-day rollover: bias recomputes from the newly completed daily bar on each
   day change; day-2 bias governs day-3 signals.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.sim.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing, SweepEvent

ET = ZoneInfo("America/New_York")

# Timestamps — all 10:00 ET (inside NY AM killzone 08:30–11:00 ET).
_DAY1 = datetime(2026, 1, 5, 10, 0, tzinfo=ET)   # Monday
_DAY2 = datetime(2026, 1, 6, 10, 0, tzinfo=ET)   # Tuesday
_DAY3 = datetime(2026, 1, 7, 10, 0, tzinfo=ET)   # Wednesday


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bar(ts: datetime, open_: str = "21000", high: str = "21050",
         low: str = "20950", close: str = "21020") -> Bar:
    return Bar(
        instrument="MNQ", timeframe="5min", ts=ts,
        open=Decimal(open_), high=Decimal(high),
        low=Decimal(low), close=Decimal(close),
        volume=500,
    )


def _low_sweep(ts: datetime) -> SweepEvent:
    """Low-side sweep: sell-stops taken → expects bullish (long) reversal."""
    return SweepEvent(
        side="low",
        swept_swing=Swing(
            kind="low", price=Decimal("20960"),
            bar_ts=ts - timedelta(minutes=5),
            confirmed_ts=ts - timedelta(minutes=2),
        ),
        pattern="B_one_bar",
        sweep_extreme=Decimal("20950"),
        completed_at=ts,
        sweep_bar=_bar(ts),
    )


def _high_sweep(ts: datetime) -> SweepEvent:
    """High-side sweep: buy-stops taken → expects bearish (short) reversal."""
    return SweepEvent(
        side="high",
        swept_swing=Swing(
            kind="high", price=Decimal("21040"),
            bar_ts=ts - timedelta(minutes=5),
            confirmed_ts=ts - timedelta(minutes=2),
        ),
        pattern="B_one_bar",
        sweep_extreme=Decimal("21055"),
        completed_at=ts,
        sweep_bar=_bar(ts),
    )


def _bullish_disp(ts: datetime) -> DisplacementEvent:
    """Bullish displacement event → produces a LONG signal."""
    fvg = FairValueGap(
        side="bearish", low=Decimal("20970"), high=Decimal("20985"), created_at=ts,
    )
    return DisplacementEvent(
        side="bullish",
        displacement_bar=_bar(ts),
        body_size=Decimal("1.5"),
        atr_at_event=Decimal("5.0"),
        body_to_atr=Decimal("0.3"),
        fvg=fvg,
    )


def _bearish_disp(ts: datetime) -> DisplacementEvent:
    """Bearish displacement event → produces a SHORT signal."""
    fvg = FairValueGap(
        side="bullish", low=Decimal("21015"), high=Decimal("21030"), created_at=ts,
    )
    return DisplacementEvent(
        side="bearish",
        displacement_bar=_bar(ts),
        body_size=Decimal("1.5"),
        atr_at_event=Decimal("5.0"),
        body_to_atr=Decimal("0.3"),
        fvg=fvg,
    )


def _bias_composer() -> SweepDisplacementComposer:
    cfg = ComposerConfig(
        instrument="MNQ",
        trend_ema_period=0,
        stop_buffer=Decimal("0.30"),
        daily_bias_gate_enabled=True,
    )
    return SweepDisplacementComposer(cfg)


def _no_bias_composer() -> SweepDisplacementComposer:
    cfg = ComposerConfig(
        instrument="MNQ",
        trend_ema_period=0,
        stop_buffer=Decimal("0.30"),
        daily_bias_gate_enabled=False,
    )
    return SweepDisplacementComposer(cfg)


def _feed_day(composer: SweepDisplacementComposer, ts: datetime,
              open_: str, high: str, low: str, close: str) -> None:
    """Feed one bar via on_bar_close to build daily OHLC tracking."""
    b = _bar(ts, open_=open_, high=high, low=low, close=close)
    composer.on_bar_close(b)


def _emit_long(composer: SweepDisplacementComposer, ts: datetime):
    """Arm a low sweep then emit a bullish displacement at ts."""
    b = _bar(ts)
    composer.on_sweep(b, _low_sweep(ts))
    return composer.on_displacement(b, _bullish_disp(ts))


def _emit_short(composer: SweepDisplacementComposer, ts: datetime):
    """Arm a high sweep then emit a bearish displacement at ts."""
    b = _bar(ts)
    composer.on_sweep(b, _high_sweep(ts))
    return composer.on_displacement(b, _bearish_disp(ts))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDailyBiasGate:

    def test_gate_off_signal_fires_without_prior_day(self):
        """Gate disabled (default=False): signal fires even without prior-day data."""
        composer = _no_bias_composer()
        # No prior-day bars fed — gate is off so this shouldn't matter
        sig = _emit_long(composer, _DAY1)
        assert sig is not None, "Gate off: signal must fire"
        assert sig.side == "long"

    def test_long_bias_short_suppressed_long_fires(self):
        """Prior close > open → long bias: short suppressed, long fires."""
        # Prior day: close (21030) > open (21000) → long bias
        composer_short = _bias_composer()
        _feed_day(composer_short, _DAY1, open_="21000", high="21060",
                  low="20940", close="21030")

        # Short signal on day 2 must be suppressed
        sig_short = _emit_short(composer_short, _DAY2)
        assert sig_short is None, "Short must be suppressed on long-bias day"

        # Long signal on day 2 must fire
        composer_long = _bias_composer()
        _feed_day(composer_long, _DAY1, open_="21000", high="21060",
                  low="20940", close="21030")
        sig_long = _emit_long(composer_long, _DAY2)
        assert sig_long is not None, "Long must fire on long-bias day"
        assert sig_long.side == "long"

    def test_short_bias_long_suppressed_short_fires(self):
        """Prior close < open → short bias: long suppressed, short fires."""
        # Prior day: close (20970) < open (21000) → short bias
        composer_long = _bias_composer()
        _feed_day(composer_long, _DAY1, open_="21000", high="21050",
                  low="20940", close="20970")

        # Long signal on day 2 must be suppressed
        sig_long = _emit_long(composer_long, _DAY2)
        assert sig_long is None, "Long must be suppressed on short-bias day"

        # Short signal on day 2 must fire
        composer_short = _bias_composer()
        _feed_day(composer_short, _DAY1, open_="21000", high="21050",
                  low="20940", close="20970")
        sig_short = _emit_short(composer_short, _DAY2)
        assert sig_short is not None, "Short must fire on short-bias day"
        assert sig_short.side == "short"

    def test_long_bias_price_at_prior_high_long_suppressed(self):
        """Long bias, bar.close >= prior-day high: long suppressed (target consumed)."""
        # Prior day: high = 21060, close > open → long bias
        composer = _bias_composer()
        _feed_day(composer, _DAY1, open_="21000", high="21060",
                  low="20940", close="21030")

        # Day 2 bar: close = 21060 (= prior high → target consumed)
        day2_bar = _bar(_DAY2, open_="21040", high="21065", low="21030", close="21060")
        composer.on_sweep(day2_bar, _low_sweep(_DAY2))
        sig = composer.on_displacement(day2_bar, _bullish_disp(_DAY2))
        assert sig is None, "Long must be suppressed when price reaches prior-day high"

    def test_et_day_rollover_bias_recomputes(self):
        """Bias recomputes from the newly completed day at each ET-day rollover."""
        composer = _bias_composer()

        # Day 1: close (21030) > open (21000) → day 2 bias = long
        _feed_day(composer, _DAY1, open_="21000", high="21060",
                  low="20940", close="21030")

        # Day 2: close (20970) < open (21000) → day 3 bias = short
        _feed_day(composer, _DAY2, open_="21000", high="21050",
                  low="20940", close="20970")

        # On day 3: bias from day 2 governs → long should be suppressed
        sig_long = _emit_long(composer, _DAY3)
        assert sig_long is None, "Long must be suppressed: day-3 bias is short (day-2 close < open)"

        # Short should fire on day 3
        composer2 = _bias_composer()
        _feed_day(composer2, _DAY1, open_="21000", high="21060",
                  low="20940", close="21030")
        _feed_day(composer2, _DAY2, open_="21000", high="21050",
                  low="20940", close="20970")
        sig_short = _emit_short(composer2, _DAY3)
        assert sig_short is not None, "Short must fire: day-3 bias is short"
        assert sig_short.side == "short"
