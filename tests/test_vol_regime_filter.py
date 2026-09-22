"""Tests for volatility regime filter in SweepDisplacementComposer."""
from datetime import datetime, timezone
from decimal import Decimal

from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.sim.events import Bar
from app.strategy.liquidity import Swing, SweepEvent


def _bar(minute: int = 0) -> Bar:
    ts = datetime(2026, 1, 2, 14, minute, tzinfo=timezone.utc)  # 09:00 ET (EST=UTC-5) — in NY AM (08:30–11:00 ET)
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts,
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("98"), close=Decimal("101"),
        volume=200,
    )


def _disp(atr: str, side: str = "bullish") -> DisplacementEvent:
    """Construct a minimal DisplacementEvent with a valid FVG."""
    bar = _bar(1)
    fvg = FairValueGap(
        side=side,
        low=Decimal("99.0"),
        high=Decimal("100.5"),
        created_at=bar.ts,
    )
    return DisplacementEvent(
        side=side,
        displacement_bar=bar,
        body_size=Decimal("1.5"),
        atr_at_event=Decimal(atr),
        body_to_atr=Decimal("1.5") / Decimal(atr),
        fvg=fvg,
    )


def _composer_with_sweep(cfg: ComposerConfig) -> SweepDisplacementComposer:
    """Return a composer that already has a pending sweep registered."""
    composer = SweepDisplacementComposer(cfg)
    sweep = SweepEvent(
        side="low",
        swept_swing=Swing(
            kind="low",
            price=Decimal("99.0"),
            bar_ts=datetime(2026, 1, 2, 9, 55, tzinfo=timezone.utc),
            confirmed_ts=datetime(2026, 1, 2, 9, 58, tzinfo=timezone.utc),
        ),
        pattern="B_one_bar",
        sweep_extreme=Decimal("98.5"),
        completed_at=datetime(2026, 1, 2, 9, 59, tzinfo=timezone.utc),
        sweep_bar=_bar(0),
    )
    # Use a bar in NY AM killzone (13:00 UTC = 08:00 ET)
    composer.on_sweep(_bar(0), sweep)
    return composer


def test_no_filter_emits_signal():
    """With no filter (0 = disabled), any ATR passes."""
    cfg = ComposerConfig(instrument="MGC", min_atr_filter=Decimal("0"), max_atr_filter=Decimal("0"))
    composer = _composer_with_sweep(cfg)
    signal = composer.on_displacement(_bar(1), _disp("1.5"))
    assert signal is not None, "Expected signal with no filter"


def test_min_atr_blocks_low_vol():
    """min_atr_filter > 0 blocks signals when ATR is below the floor."""
    cfg = ComposerConfig(instrument="MGC", min_atr_filter=Decimal("2.0"), max_atr_filter=Decimal("0"))
    composer = _composer_with_sweep(cfg)
    signal = composer.on_displacement(_bar(1), _disp("1.5"))  # ATR 1.5 < floor 2.0
    assert signal is None, "Expected no signal: ATR below min_atr_filter"


def test_min_atr_allows_at_or_above_floor():
    """min_atr_filter passes when ATR equals the floor."""
    cfg = ComposerConfig(instrument="MGC", min_atr_filter=Decimal("1.5"), max_atr_filter=Decimal("0"))
    composer = _composer_with_sweep(cfg)
    signal = composer.on_displacement(_bar(1), _disp("1.5"))  # ATR 1.5 == floor 1.5
    assert signal is not None, "Expected signal: ATR at exactly min_atr_filter"


def test_max_atr_blocks_high_vol():
    """max_atr_filter > 0 blocks signals when ATR exceeds the ceiling."""
    cfg = ComposerConfig(instrument="MGC", min_atr_filter=Decimal("0"), max_atr_filter=Decimal("2.0"))
    composer = _composer_with_sweep(cfg)
    signal = composer.on_displacement(_bar(1), _disp("3.0"))  # ATR 3.0 > ceiling 2.0
    assert signal is None, "Expected no signal: ATR above max_atr_filter"


def test_max_atr_allows_at_or_below_ceiling():
    """max_atr_filter passes when ATR equals the ceiling."""
    cfg = ComposerConfig(instrument="MGC", min_atr_filter=Decimal("0"), max_atr_filter=Decimal("3.0"))
    composer = _composer_with_sweep(cfg)
    signal = composer.on_displacement(_bar(1), _disp("3.0"))  # ATR 3.0 == ceiling 3.0
    assert signal is not None, "Expected signal: ATR at exactly max_atr_filter"
