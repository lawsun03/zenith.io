"""
B16 defining-behavior tests: inversion bar quality gate.

Gate: when inversion_min_body_r > 0 and an FVG is present, the displacement
bar body must be >= inversion_min_body_r × planned_stop_distance.
Planned stop_distance = entry_approx - stop_approx (inline computation using
the sweep_extreme and stop_buffer).
"""
from datetime import datetime, timezone
from decimal import Decimal

from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.sim.events import Bar
from app.strategy.liquidity import Swing, SweepEvent


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------

def _bar(minute: int = 0) -> Bar:
    # 14:XX UTC = 09:XX ET (NY AM = 08:30–11:00 ET)
    ts = datetime(2026, 1, 2, 14, minute, tzinfo=timezone.utc)
    return Bar(
        instrument="MNQ", timeframe="5min", ts=ts,
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("97"), close=Decimal("101"),
        volume=500,
    )


def _disp(body: str) -> DisplacementEvent:
    """
    Bullish displacement event inverting a bearish FVG.
    sweep_extreme=97.0, stop_buffer=0.30 (default):
      stop  = 97.0 - 0.30 = 96.70
      entry = fvg.high = 99.70
      stop_dist = 99.70 - 96.70 = 3.00
    min_body_r=0.15 threshold = 0.15 × 3.0 = 0.45 pts.
    """
    bar = _bar(1)
    fvg = FairValueGap(
        side="bearish",   # bearish FVG inverted by a bullish displacement bar
        low=Decimal("98.50"),
        high=Decimal("99.70"),
        created_at=bar.ts,
    )
    body_dec = Decimal(body)
    return DisplacementEvent(
        side="bullish",
        displacement_bar=bar,
        body_size=body_dec,
        atr_at_event=Decimal("5.0"),
        body_to_atr=body_dec / Decimal("5.0"),
        fvg=fvg,
    )


def _composer_with_sweep(min_body_r: str = "0") -> SweepDisplacementComposer:
    cfg = ComposerConfig(
        instrument="MNQ",
        trend_ema_period=0,
        inversion_min_body_r=Decimal(min_body_r),
        stop_buffer=Decimal("0.30"),
    )
    composer = SweepDisplacementComposer(cfg)
    sweep = SweepEvent(
        side="low",   # low sweep → expect bullish displacement
        swept_swing=Swing(
            kind="low",
            price=Decimal("97.5"),
            bar_ts=datetime(2026, 1, 2, 13, 55, tzinfo=timezone.utc),
            confirmed_ts=datetime(2026, 1, 2, 13, 58, tzinfo=timezone.utc),
        ),
        pattern="B_one_bar",
        sweep_extreme=Decimal("97.0"),   # stop = 97.0 - 0.30 = 96.70
        completed_at=datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc),
        sweep_bar=_bar(0),
    )
    composer.on_sweep(_bar(0), sweep)
    return composer


# ----------------------------------------------------------------
# Tests
# ----------------------------------------------------------------

def test_large_body_passes_quality_gate():
    """
    Inversion bar body=1.0 >= 0.15 × 3.0 = 0.45 → signal fires.
    Confirms the gate allows normal-size inversion bars.
    """
    composer = _composer_with_sweep(min_body_r="0.15")
    signal = composer.on_displacement(_bar(1), _disp("1.0"))
    assert signal is not None, "Expected signal: body 1.0 >= threshold 0.45"


def test_small_body_blocked_by_quality_gate():
    """
    Inversion bar body=0.2 < 0.15 × 3.0 = 0.45 → no signal (doji inversion).
    Confirms the gate suppresses low-conviction reversals.
    """
    composer = _composer_with_sweep(min_body_r="0.15")
    signal = composer.on_displacement(_bar(1), _disp("0.2"))
    assert signal is None, "Expected no signal: body 0.2 < threshold 0.45"


def test_gate_off_by_default_fires_on_any_body():
    """
    inversion_min_body_r=0 (default off) → no quality gate applied.
    Even a tiny body=0.1 produces a signal — existing behavior preserved.
    """
    composer = _composer_with_sweep(min_body_r="0")
    signal = composer.on_displacement(_bar(1), _disp("0.1"))
    assert signal is not None, "Expected signal: gate disabled (min_body_r=0)"
