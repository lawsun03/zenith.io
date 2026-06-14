"""B48 defining-behavior tests: iFVG hybrid rank-aware signal filter.

Mechanism: suppress rank-2+ short signals when ifvg_max_short_rank=1.
Rank-1 short = first short signal emitted on the ET calendar day.
Rank-2+ shorts (PF=0.858, loss-making) are suppressed; long signals
and rank-1 shorts are unaffected.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.broker.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing, SweepEvent

# 15:00 UTC = 10:00 ET (NY AM killzone; winter offset UTC-5)
_DAY1_10 = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)   # Monday 10:00 ET
_DAY2_10 = datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc)   # Tuesday 10:00 ET


def _bar(ts: datetime) -> Bar:
    return Bar(
        instrument="MNQ", timeframe="5min", ts=ts,
        open=Decimal("100"), high=Decimal("103.5"),
        low=Decimal("96"), close=Decimal("99"),
        volume=500,
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


def _high_sweep(ts: datetime) -> SweepEvent:
    return SweepEvent(
        side="high",
        swept_swing=Swing(
            kind="high", price=Decimal("103.0"),
            bar_ts=ts - timedelta(minutes=5),
            confirmed_ts=ts - timedelta(minutes=2),
        ),
        pattern="B_one_bar",
        sweep_extreme=Decimal("103.5"),
        completed_at=ts,
        sweep_bar=_bar(ts),
    )


def _long_disp(ts: datetime) -> DisplacementEvent:
    fvg = FairValueGap(side="bearish", low=Decimal("98.50"), high=Decimal("99.70"), created_at=ts)
    return DisplacementEvent(
        side="bullish",
        displacement_bar=_bar(ts),
        body_size=Decimal("1.0"),
        atr_at_event=Decimal("5.0"),
        body_to_atr=Decimal("0.2"),
        fvg=fvg,
    )


def _short_disp(ts: datetime) -> DisplacementEvent:
    # Bearish FVG: price swept high then displaced bearishly, inverting a bearish FVG zone.
    # entry = zone_low = 100.50; stop = sweep_extreme(103.5) + buffer(0.3) = 103.8
    fvg = FairValueGap(side="bearish", low=Decimal("100.50"), high=Decimal("101.50"), created_at=ts)
    return DisplacementEvent(
        side="bearish",
        displacement_bar=_bar(ts),
        body_size=Decimal("1.0"),
        atr_at_event=Decimal("5.0"),
        body_to_atr=Decimal("0.2"),
        fvg=fvg,
    )


def _composer(max_short_rank: int = 0) -> SweepDisplacementComposer:
    cfg = ComposerConfig(
        instrument="MNQ",
        trend_ema_period=0,
        stop_buffer=Decimal("0.30"),
        max_short_rank=max_short_rank,
    )
    return SweepDisplacementComposer(cfg)


def _emit_long(c: SweepDisplacementComposer, ts: datetime):
    c.on_sweep(_bar(ts), _low_sweep(ts))
    return c.on_displacement(_bar(ts), _long_disp(ts))


def _emit_short(c: SweepDisplacementComposer, ts: datetime):
    c.on_sweep(_bar(ts), _high_sweep(ts))
    return c.on_displacement(_bar(ts), _short_disp(ts))


class TestRankFilter:

    def test_default_no_limit_rank3_short_fires(self):
        """max_short_rank=0 (default): rank-3 shorts fire normally (backward compat)."""
        c = _composer(max_short_rank=0)
        s1 = _emit_short(c, _DAY1_10)
        assert s1 is not None, "rank-1 short must fire when max_short_rank=0"
        s2 = _emit_short(c, _DAY1_10 + timedelta(minutes=10))
        assert s2 is not None, "rank-2 short must fire when max_short_rank=0"
        s3 = _emit_short(c, _DAY1_10 + timedelta(minutes=20))
        assert s3 is not None, "rank-3 short must fire when max_short_rank=0 (backward compat)"

    def test_max1_rank1_fires_rank2_suppressed_long_unaffected(self):
        """max_short_rank=1: rank-1 short fires; rank-2 short suppressed; rank-2 long fires."""
        c = _composer(max_short_rank=1)

        s_short1 = _emit_short(c, _DAY1_10)
        assert s_short1 is not None, "rank-1 short must fire"
        assert s_short1.side == "short"

        s_short2 = _emit_short(c, _DAY1_10 + timedelta(minutes=10))
        assert s_short2 is None, "rank-2 short must be suppressed when max_short_rank=1"

        s_long = _emit_long(c, _DAY1_10 + timedelta(minutes=20))
        assert s_long is not None, "long signals must not be suppressed by max_short_rank"
        assert s_long.side == "long"

    def test_day_reset_rank_counter_clears(self):
        """max_short_rank=1: rank counter resets at ET midnight; day-2 rank-1 short fires."""
        c = _composer(max_short_rank=1)

        s1 = _emit_short(c, _DAY1_10)
        assert s1 is not None, "day-1 rank-1 short fires"

        s2 = _emit_short(c, _DAY1_10 + timedelta(minutes=10))
        assert s2 is None, "day-1 rank-2 short suppressed"

        s_day2 = _emit_short(c, _DAY2_10)
        assert s_day2 is not None, "day-2 rank-1 short must fire (counter reset at ET midnight)"
        assert s_day2.side == "short"

    def test_longs_not_affected_at_any_rank(self):
        """Longs are not affected by max_short_rank at any rank level."""
        c = _composer(max_short_rank=1)
        for i in range(4):
            sig = _emit_long(c, _DAY1_10 + timedelta(minutes=10 * i))
            assert sig is not None, f"long signal {i + 1} must fire regardless of max_short_rank"
            assert sig.side == "long"
