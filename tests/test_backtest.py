"""
Backtest tests — pairing logic, stats math, end-to-end runner.

Bigger surface here than usual because backtest output is what you'll
trust to make real money decisions. If win-rate or drawdown is wrong,
you'd happily run a losing strategy live thinking it's profitable.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.backtest.pairing import Trade, TradePairer
from app.backtest.report import (
    format_summary,
    format_sweep_table,
    write_equity_csv,
    write_trades_csv,
)
from app.backtest.runner import (
    BacktestConfig,
    SweepDimension,
    run_backtest,
    run_sweep,
)
from app.backtest.stats import BacktestStats, compute_stats
from app.broker.events import Bar, Fill
from app.replay import load_bars_csv
from app.strategy.composer import ComposerConfig, Signal
from app.strategy.displacement import DisplacementConfig
from app.strategy.liquidity import LiquidityConfig


# =====================================================================
# Helpers
# =====================================================================

def now_at(minute: int) -> datetime:
    return datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)


def make_signal(side: str = "long", entry: str = "2400", stop: str = "2398",
                target: str = "2404") -> Signal:
    return Signal(
        instrument="MGC",
        side=side,
        entry=Decimal(entry),
        stop=Decimal(stop),
        target=Decimal(target),
        created_at=now_at(0),
        killzone="NY AM",
        sweep_pattern="B_one_bar",
        sweep_extreme=Decimal("2401"),
        fvg_low=Decimal("2400"),
        fvg_high=Decimal("2401"),
        rationale="test signal",
    )


def make_fill(
    is_entry: bool, side: str, price: str, pnl: str = "0",
    minute: int = 0, instrument: str = "MGC", size: int = 1,
) -> Fill:
    return Fill(
        ts=now_at(minute),
        instrument=instrument,
        side=side,
        fill_price=Decimal(price),
        size=size,
        is_entry=is_entry,
        realized_pnl_delta=Decimal(pnl),
        contracts_delta=size if side == "long" else -size,
        broker_order_id=f"oid-{minute}",
    )


# =====================================================================
# TradePairer
# =====================================================================

class TestTradePairer:

    def test_basic_pairing(self):
        pairer = TradePairer()
        sig = make_signal(side="long", entry="2400", stop="2398", target="2404")
        pairer.on_signal(sig)

        # Entry — opens, no trade yet.
        result = pairer.on_fill(make_fill(
            is_entry=True, side="long", price="2400", minute=0,
        ))
        assert result is None

        # Exit at target — completes a winning trade.
        trade = pairer.on_fill(make_fill(
            is_entry=False, side="short", price="2404",
            pnl="40", minute=5,
        ))
        assert trade is not None
        assert trade.side == "long"
        assert trade.realized_pnl == Decimal("40")
        assert trade.is_winner
        assert trade.entry_price == Decimal("2400")
        assert trade.exit_price == Decimal("2404")
        assert trade.hold_time == timedelta(minutes=5)

    def test_r_multiple_long_winner(self):
        """2-point risk, 4-point gain → 2R."""
        pairer = TradePairer()
        sig = make_signal(side="long", entry="2400", stop="2398", target="2404")
        pairer.on_signal(sig)
        pairer.on_fill(make_fill(is_entry=True, side="long", price="2400"))
        trade = pairer.on_fill(make_fill(
            is_entry=False, side="short", price="2404",
            pnl="40", minute=5,
        ))
        assert trade.r_multiple == Decimal("2")

    def test_r_multiple_long_loser(self):
        """Stopped out — should be -1R."""
        pairer = TradePairer()
        sig = make_signal(side="long", entry="2400", stop="2398", target="2404")
        pairer.on_signal(sig)
        pairer.on_fill(make_fill(is_entry=True, side="long", price="2400"))
        trade = pairer.on_fill(make_fill(
            is_entry=False, side="short", price="2398",
            pnl="-20", minute=2,
        ))
        assert trade.r_multiple == Decimal("-1")
        assert trade.is_loser

    def test_r_multiple_short_winner(self):
        """Short: entry=2400, stop=2402, target=2396. Exit at 2396 → 2R."""
        pairer = TradePairer()
        sig = make_signal(side="short", entry="2400", stop="2402", target="2396")
        pairer.on_signal(sig)
        pairer.on_fill(make_fill(is_entry=True, side="short", price="2400"))
        trade = pairer.on_fill(make_fill(
            is_entry=False, side="long", price="2396",
            pnl="40", minute=3,
        ))
        assert trade.side == "short"
        assert trade.r_multiple == Decimal("2")
        assert trade.is_winner

    def test_exit_without_entry_warns_and_returns_none(self, caplog):
        pairer = TradePairer()
        result = pairer.on_fill(make_fill(
            is_entry=False, side="short", price="2400",
        ))
        assert result is None
        assert "no open entry" in caplog.text.lower()

    def test_open_count_tracks_unmatched(self):
        pairer = TradePairer()
        sig = make_signal()
        pairer.on_signal(sig)
        pairer.on_fill(make_fill(is_entry=True, side="long", price="2400"))
        assert pairer.open_count == 1

        pairer.on_fill(make_fill(
            is_entry=False, side="short", price="2404",
            pnl="40", minute=2,
        ))
        assert pairer.open_count == 0

    def test_no_signal_means_no_r_multiple(self):
        """Position opened without a signal (manual order) → R is None."""
        pairer = TradePairer()
        # Entry fill but no signal first.
        pairer.on_fill(make_fill(is_entry=True, side="long", price="2400"))
        trade = pairer.on_fill(make_fill(
            is_entry=False, side="short", price="2404",
            pnl="40", minute=2,
        ))
        assert trade.r_multiple is None


# =====================================================================
# Stats math
# =====================================================================

def make_trade(
    pnl: str, r: str | None = None, killzone: str = "NY AM",
    minute_open: int = 0, minute_close: int = 5,
) -> Trade:
    return Trade(
        instrument="MGC",
        side="long",
        size=1,
        entry_ts=now_at(minute_open),
        entry_price=Decimal("2400"),
        exit_ts=now_at(minute_close),
        exit_price=Decimal("2400") + Decimal(pnl) / Decimal("10"),
        realized_pnl=Decimal(pnl),
        hold_time=timedelta(minutes=minute_close - minute_open),
        signal_entry=Decimal("2400"),
        signal_stop=Decimal("2398"),
        signal_target=Decimal("2404"),
        killzone=killzone,
        sweep_pattern="B_one_bar",
        r_multiple=Decimal(r) if r is not None else None,
    )


class TestStats:

    def test_empty_trades_returns_safe_zeros(self):
        stats = compute_stats(trades=[], starting_balance=Decimal("50000"))
        assert stats.total_trades == 0
        assert stats.net_pnl == Decimal("0")
        assert stats.is_profitable is False

    def test_pure_winners(self):
        trades = [make_trade("40", "2", minute_close=5)] * 3
        stats = compute_stats(trades, Decimal("50000"))
        assert stats.total_trades == 3
        assert stats.winners == 3
        assert stats.losers == 0
        assert stats.win_rate == Decimal("1")
        assert stats.net_pnl == Decimal("120")
        assert stats.profit_factor is not None
        assert stats.profit_factor > 1000  # sentinel for "no losses"

    def test_pure_losers(self):
        trades = [make_trade("-20", "-1", minute_close=2)] * 3
        stats = compute_stats(trades, Decimal("50000"))
        assert stats.winners == 0
        assert stats.losers == 3
        assert stats.win_rate == Decimal("0")
        assert stats.net_pnl == Decimal("-60")
        assert stats.profit_factor is None

    def test_mixed_winrate_and_profit_factor(self):
        """3 winners @ +40, 2 losers @ -20 → 60% win rate, PF=3."""
        trades = (
            [make_trade("40", "2", minute_close=5)] * 3 +
            [make_trade("-20", "-1", minute_close=2)] * 2
        )
        stats = compute_stats(trades, Decimal("50000"))
        assert stats.winners == 3
        assert stats.losers == 2
        assert stats.win_rate == Decimal("0.6")
        # gross_profit = 120, gross_loss = -40, PF = 3.
        assert stats.profit_factor == Decimal("3")
        assert stats.expectancy_per_trade == Decimal("80") / Decimal("5")

    def test_drawdown_from_trade_curve(self):
        """
        Sequence: +100, +100, -150, +50.
        Equity:   50000 → 50100 → 50200 → 50050 → 50100
        Peak:     50000   50100   50200   50200   50200
        DD at exit 3: 50200 - 50050 = 150.
        """
        trades = [
            make_trade("100", minute_open=0, minute_close=5),
            make_trade("100", minute_open=5, minute_close=10),
            make_trade("-150", minute_open=10, minute_close=15),
            make_trade("50", minute_open=15, minute_close=20),
        ]
        stats = compute_stats(trades, Decimal("50000"))
        assert stats.max_drawdown == Decimal("150")

    def test_max_consecutive_losses(self):
        trades = [
            make_trade("40"),    # W
            make_trade("-20"),   # L
            make_trade("-20"),   # L
            make_trade("-20"),   # L  ← longest streak ends here
            make_trade("40"),    # W
            make_trade("-20"),   # L
        ]
        stats = compute_stats(trades, Decimal("50000"))
        assert stats.max_consecutive_losses == 3
        assert stats.max_consecutive_winners == 1

    def test_killzone_breakdown(self):
        trades = (
            [make_trade("40", killzone="NY AM")] * 2 +
            [make_trade("-20", killzone="NY AM")] +
            [make_trade("-20", killzone="London")] * 2
        )
        stats = compute_stats(trades, Decimal("50000"))
        assert "NY AM" in stats.by_killzone
        assert "London" in stats.by_killzone
        assert stats.by_killzone["NY AM"].trades == 3
        assert stats.by_killzone["NY AM"].net_pnl == Decimal("60")
        assert stats.by_killzone["London"].net_pnl == Decimal("-40")

    def test_passed_combine_threshold(self):
        """+$3000 with max DD < $2000 = pass."""
        trades = [make_trade("3001")]
        stats = compute_stats(trades, Decimal("50000"))
        assert stats.passed_combine is True

    def test_failed_combine_due_to_drawdown(self):
        """+$3500 net but DD hits $2500 mid-run = fail."""
        trades = [
            make_trade("100", minute_open=0, minute_close=5),
            make_trade("-2500", minute_open=5, minute_close=10),
            make_trade("5900", minute_open=10, minute_close=20),
        ]
        stats = compute_stats(trades, Decimal("50000"))
        # Net is +$3500 but drawdown was $2400 from peak 50100 → 47700.
        assert stats.net_pnl == Decimal("3500")
        assert stats.max_drawdown >= Decimal("2000")
        assert stats.passed_combine is False


# =====================================================================
# End-to-end runner
# =====================================================================

# The same bar sequence used in test_engine — guaranteed to produce a
# SHORT signal in NY AM that hits the target.
SHORT_SIGNAL_BARS = [
    ("2400",   "2400.4", "2399.6", "2400.1"),
    ("2400.1", "2400.5", "2399.8", "2400.2"),
    ("2400.2", "2400.6", "2399.9", "2400.3"),
    ("2400.3", "2400.7", "2400",   "2400.4"),
    ("2400.4", "2400.8", "2400.1", "2400.5"),
    ("2400.5", "2401",   "2400.3", "2400.8"),
    ("2400.8", "2403",   "2400.5", "2402.5"),
    ("2402.5", "2402.8", "2401.5", "2401.8"),
    ("2401.8", "2402.5", "2401",   "2401.5"),
    ("2401.5", "2403.5", "2401",   "2401.5"),  # sweep
    ("2401.5", "2401.7", "2400.8", "2401"),
    ("2401",   "2401.2", "2398.4", "2398.5"),  # displacement
    ("2398.5", "2398.3", "2397",   "2397.5"),  # FVG
]


def make_bars_in_killzone() -> list[Bar]:
    """Produce bars timestamped inside NY AM."""
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    base = datetime(2026, 5, 11, 9, 0, tzinfo=ET).astimezone(timezone.utc)
    bars = []
    for i, (o, h, l, c) in enumerate(SHORT_SIGNAL_BARS):
        bars.append(Bar(
            instrument="MGC",
            timeframe="1min",
            ts=base + timedelta(minutes=i),
            open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
            volume=100,
        ))
    # Add one more bar to hit the target. Signal target ≈ 2387.3
    # for a 2R short from entry 2398.3 with stop 2403.8.
    bars.append(Bar(
        instrument="MGC",
        timeframe="1min",
        ts=base + timedelta(minutes=len(SHORT_SIGNAL_BARS)),
        open=Decimal("2397.5"),
        high=Decimal("2397.5"),
        low=Decimal("2385"),       # below target 2387.3
        close=Decimal("2386"),
        volume=100,
    ))
    return bars


async def test_runner_produces_one_winning_trade():
    """End-to-end: synthetic bars → SHORT signal → target hit → 1 winning Trade."""
    bars = make_bars_in_killzone()
    config = BacktestConfig(
        instrument="MGC",
        bars=bars,
        starting_balance=Decimal("50000"),
        liquidity_config=LiquidityConfig(
            swing_lookback=2, min_penetration=Decimal("0.20"),
        ),
        displacement_config=DisplacementConfig(
            atr_period=5,
            body_atr_multiple=Decimal("1.0"),
            min_body_to_range_ratio=Decimal("0.5"),
            min_absolute_body=Decimal("0.5"),
        ),
        composer_config=ComposerConfig(
            instrument="MGC",
            displacement_window_bars=5,
            stop_buffer=Decimal("0.30"),
            r_multiple=Decimal("2.0"),
        ),
    )
    result = await run_backtest(config)

    assert result.bars_processed == len(bars)
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.side == "short"
    assert trade.is_winner
    assert trade.killzone == "NY AM"
    assert result.stats.win_rate == Decimal("1")
    assert result.stats.net_pnl > 0


async def test_runner_handles_empty_bars():
    """Backtest with no bars → empty stats, zero trades."""
    config = BacktestConfig(
        instrument="MGC",
        bars=[],
        starting_balance=Decimal("50000"),
    )
    result = await run_backtest(config)
    assert result.bars_processed == 0
    assert result.trades == []
    assert result.stats.total_trades == 0


async def test_sweep_runs_each_combination(tmp_path):
    """Sweep with 2 dimensions × 2 values each = 4 runs."""
    bars = make_bars_in_killzone()

    base = BacktestConfig(
        instrument="MGC",
        bars=iter([]),
        starting_balance=Decimal("50000"),
        liquidity_config=LiquidityConfig(
            swing_lookback=2, min_penetration=Decimal("0.20"),
        ),
        displacement_config=DisplacementConfig(
            atr_period=5,
            body_atr_multiple=Decimal("1.0"),
            min_body_to_range_ratio=Decimal("0.5"),
            min_absolute_body=Decimal("0.5"),
        ),
        composer_config=ComposerConfig(
            instrument="MGC",
            displacement_window_bars=5,
            r_multiple=Decimal("2.0"),
        ),
    )

    dims = [
        SweepDimension(
            target="r_multiple",
            values=[Decimal("1.5"), Decimal("3.0")],
            container="composer",
        ),
        SweepDimension(
            target="atr_period",
            values=[5, 10],
            container="displacement",
        ),
    ]

    # bars_factory must return a fresh iterable each call.
    results = await run_sweep(base, dims, lambda: list(bars))

    assert len(results) == 4
    # Each run has its own label.
    labels = [r.label for r in results]
    assert "r_multiple=1.5  atr_period=5" in labels
    assert "r_multiple=3.0  atr_period=10" in labels
    # Each run produced its own stats independently.
    assert all(r.stats is not None for r in results)


# =====================================================================
# Reporting
# =====================================================================

class TestReporting:

    def test_summary_handles_empty(self):
        stats = compute_stats(trades=[], starting_balance=Decimal("50000"))
        out = format_summary(stats)
        assert "No trades" in out

    def test_summary_renders_key_numbers(self):
        trades = [make_trade("100"), make_trade("-50")]
        stats = compute_stats(trades, Decimal("50000"))
        out = format_summary(stats)
        # Just check that core numbers appear in the output.
        assert "+$50.00" in out  # net P&L
        assert "PROFITABLE" in out
        assert "50.0%" in out    # win rate

    def test_summary_renders_loser(self):
        trades = [make_trade("-200")]
        stats = compute_stats(trades, Decimal("50000"))
        out = format_summary(stats)
        assert "LOSING" in out

    def test_sweep_table_sorts_by_net_pnl(self):
        from app.backtest.runner import BacktestResult
        results = [
            BacktestResult(
                label=f"run{i}", config=None, stats=compute_stats(
                    [make_trade(str(pnl))], Decimal("50000"),
                ),
                trades=[make_trade(str(pnl))], rejected_signals=0,
                bars_processed=10,
            )
            for i, pnl in enumerate([100, -50, 200, 0])
        ]
        out = format_sweep_table(results)
        # +$200 should appear before +$100 in the output.
        idx_200 = out.index("+$200.00")
        idx_100 = out.index("+$100.00")
        assert idx_200 < idx_100

    def test_csv_writers_round_trip(self, tmp_path: Path):
        trades = [make_trade("100"), make_trade("-50")]
        stats = compute_stats(trades, Decimal("50000"))

        trades_csv = tmp_path / "trades.csv"
        equity_csv = tmp_path / "equity.csv"
        write_trades_csv(trades, trades_csv)
        write_equity_csv(stats.equity_curve, equity_csv)

        # Check trades round-trip.
        with trades_csv.open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[0]["side"] == "long"
        assert Decimal(rows[0]["realized_pnl"]) == Decimal("100")

        # Equity curve has at least an anchor point + 2 trade exits.
        with equity_csv.open() as f:
            rows = list(csv.DictReader(f))
        assert len(rows) >= 3
