"""Tests for app.backtest.runner."""
import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from app.backtest.runner import (
    BacktestConfig,
    BacktestResult,
    SweepDimension,
    _apply_sweep_dim,
    _build_runner,
    run_backtest,
    _reconstruct_trades,
)
from app.bot_config import StrategyParams
from app.strategy.composer import ComposerConfig
from app.strategy.displacement import DisplacementConfig
from app.strategy.liquidity import LiquidityConfig
from app.broker.events import Bar


def _make_bars(n: int, instrument: str = "MGC") -> list[Bar]:
    """Generate n flat bars (no displacement signal)."""
    base = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    return [
        Bar(
            instrument=instrument, timeframe="1min",
            ts=base + timedelta(minutes=i),
            open=Decimal("100"), high=Decimal("101"),
            low=Decimal("99"), close=Decimal("100"),
            volume=100,
        )
        for i in range(n)
    ]


def _base_config(bars, instrument="MGC") -> BacktestConfig:
    return BacktestConfig(
        instrument=instrument,
        bars=iter(bars),
        starting_balance=Decimal("50000"),
        soft_buffer=Decimal("500"),
        liquidity_config=LiquidityConfig(swing_lookback=2),
        displacement_config=DisplacementConfig(body_atr_multiple=Decimal("1.0")),
        composer_config=ComposerConfig(instrument=instrument, r_multiple=Decimal("2.0")),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
    )


def test_run_backtest_returns_result():
    """run_backtest returns a BacktestResult with correct bar count."""
    bars = _make_bars(50)
    cfg = _base_config(bars)
    result = asyncio.run(run_backtest(cfg))
    assert isinstance(result, BacktestResult)
    assert result.bars_processed == 50


def test_no_trades_on_flat_bars():
    """Flat bars produce no signals and no trades."""
    bars = _make_bars(100)
    cfg = _base_config(bars)
    result = asyncio.run(run_backtest(cfg))
    assert result.stats.trades == 0
    assert result.stats.net_pnl == Decimal("0")


def test_hold_seconds_positive():
    """Trade hold_seconds must be positive (regression for entry_ts bug)."""
    base_ts = datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)
    entry_fill = {
        "ts": base_ts.isoformat(),
        "side": "long",
        "fill_price": "100.0",
        "size": 1,
        "is_entry": True,
        "realized_pnl_delta": "0",
        "instrument": "MGC",
    }
    exit_fill = {
        "ts": (base_ts + timedelta(minutes=30)).isoformat(),
        "side": "short",
        "fill_price": "105.0",
        "size": 1,
        "is_entry": False,
        "realized_pnl_delta": "50.0",
        "instrument": "MGC",
    }
    trades = _reconstruct_trades([entry_fill, exit_fill])
    assert len(trades) == 1
    assert trades[0]["hold_seconds"] >= 0, "hold_seconds is negative — entry_ts bug!"
    assert trades[0]["hold_seconds"] == 30 * 60


def test_stats_passed_combine():
    """passed_combine is True when net_pnl >= profit_target and no MLL breach."""
    bars = _make_bars(10)
    cfg = _base_config(bars)
    result = asyncio.run(run_backtest(cfg))
    # No trades on flat bars → not passed
    assert result.stats.passed_combine is False


def test_faithful_path_attaches_vp_legacy_does_not():
    """strategy_params set → runner has a VP tracker (faithful to live, which
    runs vp_enabled). strategy_params None → legacy path, no VP. Without this,
    the backtest silently skips the VP gate that live applies on every signal."""
    faithful = BacktestConfig(
        instrument="MGC", bars=iter([]),
        strategy_params=StrategyParams(),
    )
    assert _build_runner(faithful).vp is not None

    legacy = BacktestConfig(
        instrument="MGC", bars=iter([]),
        composer_config=ComposerConfig(instrument="MGC"),
    )
    assert _build_runner(legacy).vp is None


def test_strategy_sweep_dim_updates_strategy_params():
    """A 'strategy'-container sweep overrides a StrategyParams field and is the
    only container that takes effect on the faithful path (the runner is rebuilt
    from strategy_params, so composer/liquidity/displacement overrides are inert)."""
    base = BacktestConfig(
        instrument="MGC", bars=iter([]),
        strategy_params=StrategyParams(trend_ema_period=0),
    )
    dim = SweepDimension(target="trend_ema_period", values=[50], container="strategy")
    out = _apply_sweep_dim(base, dim, 50)
    assert out.strategy_params.trend_ema_period == 50
    assert base.strategy_params.trend_ema_period == 0  # original untouched


def test_backtest_config_defaults():
    """BacktestConfig has sensible defaults for optional fields."""
    cfg = BacktestConfig(
        instrument="MGC",
        bars=iter([]),
        liquidity_config=LiquidityConfig(),
        displacement_config=DisplacementConfig(),
        composer_config=ComposerConfig(instrument="MGC"),
    )
    assert cfg.starting_balance == Decimal("50000")
    assert cfg.timeframe == "1min"
    assert cfg.contracts == 1
