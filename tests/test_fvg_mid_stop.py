"""B36 — FVG-midpoint stop placement.

Defining-behavior tests:
1. stop_mode="swing" (default): stop identical to baseline (sweep extreme – buffer).
2. stop_mode="fvg_mid": stop = (fvg_low + fvg_high) / 2; target scaled by new r.
3. fvg_mid stop is strictly tighter than swing stop (smaller r distance from entry).
4. stop_mode="fvg_mid_abs": same FVG midpoint stop but target unchanged (higher R).
"""
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing, SweepEvent

ET = ZoneInfo("America/New_York")
_TS = datetime(2026, 1, 5, 10, 0, tzinfo=ET)


def _bar(ts: datetime = _TS) -> Bar:
    return Bar(
        instrument="MNQ", timeframe="5min", ts=ts,
        open=Decimal("21000"), high=Decimal("21050"),
        low=Decimal("20960"), close=Decimal("21020"),
        volume=500,
    )


def _low_sweep(ts: datetime = _TS) -> SweepEvent:
    """Low-side sweep → expects bullish (long) displacement."""
    return SweepEvent(
        side="low",
        swept_swing=Swing(
            kind="low", price=Decimal("20960"),
            bar_ts=ts - timedelta(minutes=5),
            confirmed_ts=ts - timedelta(minutes=2),
        ),
        pattern="B_one_bar",
        sweep_extreme=Decimal("20950"),  # wide; swing stop much lower than FVG
        completed_at=ts,
        sweep_bar=_bar(ts),
    )


def _bullish_disp(ts: datetime = _TS) -> DisplacementEvent:
    """Bullish displacement → LONG signal.  FVG zone: low=20970, high=20985."""
    fvg = FairValueGap(
        side="bearish",
        low=Decimal("20970"),   # distal edge for long
        high=Decimal("20985"),  # proximal edge / entry
        created_at=ts,
    )
    return DisplacementEvent(
        side="bullish",
        displacement_bar=_bar(ts),
        body_size=Decimal("2.0"),
        atr_at_event=Decimal("5.0"),
        body_to_atr=Decimal("0.4"),
        fvg=fvg,
    )


def _composer(stop_mode: str = "swing") -> SweepDisplacementComposer:
    cfg = ComposerConfig(
        instrument="MNQ",
        trend_ema_period=0,          # no EMA warmup needed
        stop_buffer=Decimal("0.30"),
        r_multiple=Decimal("2.5"),
        stop_mode=stop_mode,
    )
    return SweepDisplacementComposer(cfg)


def _emit_long(composer: SweepDisplacementComposer):
    b = _bar()
    composer.on_sweep(b, _low_sweep())
    return composer.on_displacement(b, _bullish_disp())


class TestFvgMidStop:

    def test_swing_mode_is_default_identical(self):
        """stop_mode='swing' produces the same signal as the no-arg default."""
        sig_default = _emit_long(_composer())
        sig_swing = _emit_long(_composer(stop_mode="swing"))
        assert sig_default is not None
        assert sig_swing is not None
        # Both must use sweep_extreme - buffer as stop
        expected_stop = Decimal("20950") - Decimal("0.30")  # 20949.70
        assert sig_default.stop == expected_stop
        assert sig_swing.stop == expected_stop
        assert sig_default.stop == sig_swing.stop
        assert sig_default.target == sig_swing.target

    def test_fvg_mid_stop_value(self):
        """stop_mode='fvg_mid': stop = (fvg_low + fvg_high) / 2."""
        sig = _emit_long(_composer(stop_mode="fvg_mid"))
        assert sig is not None
        # FVG zone: low=20970, high=20985 → midpoint = 20977.5
        expected_stop = (Decimal("20970") + Decimal("20985")) / 2  # 20977.5
        assert sig.stop == expected_stop
        # Entry for long in edge mode = zone_high = 20985
        assert sig.entry == Decimal("20985")
        # Target must be entry + r × r_multiple where r = entry - stop
        r = sig.entry - sig.stop
        assert r > 0
        expected_target = sig.entry + r * Decimal("2.5")
        assert sig.target == expected_target

    def test_fvg_mid_stop_is_tighter_than_swing(self):
        """fvg_mid stop distance is strictly smaller than swing stop distance."""
        sig_swing = _emit_long(_composer(stop_mode="swing"))
        sig_fvg = _emit_long(_composer(stop_mode="fvg_mid"))
        assert sig_swing is not None and sig_fvg is not None
        r_swing = sig_swing.entry - sig_swing.stop
        r_fvg = sig_fvg.entry - sig_fvg.stop
        assert r_fvg < r_swing, (
            f"fvg_mid r={r_fvg} must be < swing r={r_swing}"
        )
        # Both must have positive r and correct side
        assert r_fvg > 0
        assert r_swing > 0
        assert sig_swing.side == sig_fvg.side == "long"

    def test_fvg_mid_abs_keeps_original_target(self):
        """stop_mode='fvg_mid_abs': stop at FVG midpoint, target unchanged from swing mode."""
        sig_swing = _emit_long(_composer(stop_mode="swing"))
        sig_abs = _emit_long(_composer(stop_mode="fvg_mid_abs"))
        assert sig_swing is not None and sig_abs is not None
        # Stop must be at FVG midpoint (tighter than swing)
        expected_fvg_mid = (Decimal("20970") + Decimal("20985")) / 2
        assert sig_abs.stop == expected_fvg_mid
        # Target must equal the original swing-mode target (unchanged)
        assert sig_abs.target == sig_swing.target
        # Effective R is higher (smaller stop, same target)
        r_abs = sig_abs.entry - sig_abs.stop
        r_swing = sig_swing.entry - sig_swing.stop
        assert r_abs < r_swing  # smaller stop distance
        assert sig_abs.target > sig_abs.entry + r_abs * Decimal("2.5")  # target exceeds standard r_mult
