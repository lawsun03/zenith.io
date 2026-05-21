"""Tests for Phase 2 smaller cleanups: cooldown and ATR-scaled penetration."""
from datetime import datetime, timezone
from decimal import Decimal

from app.broker.events import Bar, Fill
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer


def _bar(i: int = 0) -> Bar:
    ts = datetime(2026, 1, 2, 10, i, tzinfo=timezone.utc)
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts,
        open=Decimal("100"), high=Decimal("101"),
        low=Decimal("99"), close=Decimal("100"),
        volume=100,
    )


# ── Cooldown tests ──────────────────────────────────────────────────────────

def test_cooldown_off_by_default():
    """Default cooldown is 0 (disabled); _cooldown_remaining stays 0 after stop."""
    cfg = ComposerConfig(instrument="MGC")
    composer = SweepDisplacementComposer(cfg)
    composer.on_stop_loss()
    assert composer._cooldown_remaining == 0


def test_cooldown_set_on_stop_loss():
    """on_stop_loss() sets _cooldown_remaining to cooldown_bars_after_stop."""
    cfg = ComposerConfig(instrument="MGC", cooldown_bars_after_stop=3)
    composer = SweepDisplacementComposer(cfg)
    composer.on_stop_loss()
    assert composer._cooldown_remaining == 3


def test_cooldown_decrements_on_bar_close():
    """on_bar_close() decrements _cooldown_remaining toward 0."""
    cfg = ComposerConfig(instrument="MGC", cooldown_bars_after_stop=3)
    composer = SweepDisplacementComposer(cfg)
    composer.on_stop_loss()
    composer.on_bar_close(_bar(0))
    assert composer._cooldown_remaining == 2
    composer.on_bar_close(_bar(1))
    assert composer._cooldown_remaining == 1
    composer.on_bar_close(_bar(2))
    assert composer._cooldown_remaining == 0


def test_cooldown_does_not_go_below_zero():
    """Calling on_bar_close after cooldown expires doesn't go negative."""
    cfg = ComposerConfig(instrument="MGC", cooldown_bars_after_stop=1)
    composer = SweepDisplacementComposer(cfg)
    composer.on_stop_loss()
    composer.on_bar_close(_bar(0))
    composer.on_bar_close(_bar(1))
    assert composer._cooldown_remaining == 0


def test_fill_has_is_stop_field():
    """Fill dataclass has is_stop field defaulting to False."""
    f = Fill(
        ts=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        instrument="MGC",
        side="long",
        fill_price=Decimal("100"),
        size=1,
        is_entry=True,
        realized_pnl_delta=Decimal("0"),
        contracts_delta=1,
        broker_order_id="test-1",
    )
    assert f.is_stop is False


def test_fill_is_stop_can_be_set():
    """Fill.is_stop can be True for stop exits."""
    f = Fill(
        ts=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        instrument="MGC",
        side="short",
        fill_price=Decimal("98"),
        size=1,
        is_entry=False,
        realized_pnl_delta=Decimal("-20"),
        contracts_delta=-1,
        broker_order_id="test-2",
        is_stop=True,
    )
    assert f.is_stop is True


# ── ATR penetration scaling tests ──────────────────────────────────────────

from app.strategy.liquidity import LiquidityConfig, LiquidityTracker


def test_effective_pen_fixed_when_no_factor():
    """Without min_penetration_atr_factor, fixed min_penetration is used."""
    cfg = LiquidityConfig(min_penetration=Decimal("0.20"))
    tracker = LiquidityTracker(cfg)
    assert tracker._effective_pen(atr=Decimal("2.0")) == Decimal("0.20")
    assert tracker._effective_pen(atr=None) == Decimal("0.20")


def test_effective_pen_scales_with_atr():
    """With factor=0.25, effective pen = 0.25 × ATR."""
    cfg = LiquidityConfig(min_penetration=Decimal("0.20"), min_penetration_atr_factor=Decimal("0.25"))
    tracker = LiquidityTracker(cfg)
    assert tracker._effective_pen(atr=Decimal("2.0")) == Decimal("0.50")
    assert tracker._effective_pen(atr=Decimal("0.4")) == Decimal("0.10")


def test_effective_pen_falls_back_to_fixed_when_atr_none():
    """If factor is set but ATR is None (not yet warmed up), use fixed min_penetration."""
    cfg = LiquidityConfig(min_penetration=Decimal("0.20"), min_penetration_atr_factor=Decimal("0.25"))
    tracker = LiquidityTracker(cfg)
    assert tracker._effective_pen(atr=None) == Decimal("0.20")
