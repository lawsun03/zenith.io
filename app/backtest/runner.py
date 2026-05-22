"""
Backtest runner — reusable core used by scripts/backtest.py and the walk-forward optimizer.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Iterator

log = logging.getLogger(__name__)

from app.broker.events import Bar, Fill
from app.broker.paper import PaperBroker
from app.execution.engine import ExecutionEngine, OrderOutcome, StrategyRunner
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.killzone import default_killzones, killzones_from_names
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker


@dataclass
class BacktestStats:
    trades: int
    wins: int
    losses: int
    win_rate: float
    net_pnl: Decimal
    gross_win: Decimal
    gross_loss: Decimal
    avg_win: Decimal
    avg_loss: Decimal
    profit_factor: float | None
    max_drawdown: Decimal
    expectancy: Decimal
    is_profitable: bool
    passed_combine: bool
    mll_breached: bool
    equity_curve: list[tuple[datetime, Decimal]]
    by_killzone: dict[str, dict]


@dataclass
class BacktestConfig:
    instrument: str
    bars: Iterator[Bar]
    composer_config: ComposerConfig
    starting_balance: Decimal = field(default_factory=lambda: Decimal("50000"))
    soft_buffer: Decimal = field(default_factory=lambda: Decimal("500"))
    liquidity_config: LiquidityConfig = field(default_factory=LiquidityConfig)
    displacement_config: DisplacementConfig = field(default_factory=DisplacementConfig)
    enabled_killzones: list[str] | None = None
    timeframe: str = "1min"
    contracts: int = 1
    slippage_ticks_market: int = 1
    commission_per_side: Decimal = field(default_factory=lambda: Decimal("0.74"))
    partial_profit_r: Decimal = field(default_factory=lambda: Decimal("0"))  # 0 = disabled
    label: str = ""


@dataclass
class BacktestResult:
    config: BacktestConfig
    stats: BacktestStats
    trades: list[dict]
    bars_processed: int
    rejected_signals: int
    label: str


@dataclass
class SweepDimension:
    target: str       # parameter name
    values: list[Any]
    container: str    # "composer" | "liquidity" | "displacement"


def _build_runner(cfg: BacktestConfig) -> StrategyRunner:
    zones = (
        killzones_from_names(cfg.enabled_killzones)
        if cfg.enabled_killzones
        else default_killzones()
    )
    composer_cfg = dataclasses.replace(cfg.composer_config, killzones=zones)
    return StrategyRunner(
        instrument=cfg.instrument,
        timeframe=cfg.timeframe,
        liquidity=LiquidityTracker(cfg.liquidity_config),
        displacement=DisplacementDetector(cfg.displacement_config),
        composer=SweepDisplacementComposer(composer_cfg),
    )


def _compute_stats(
    fills: list[dict],
    risk_state: RiskState,
    starting_balance: Decimal,
) -> BacktestStats:
    exits = [f for f in fills if not f["is_entry"]]
    pnls = [Decimal(f["realized_pnl_delta"]) for f in exits]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net = sum(pnls, Decimal("0"))
    gross_win = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))

    # equity curve: accumulate ALL fills (entry commission + exit P&L)
    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    eq_curve: list[tuple[datetime, Decimal]] = []
    for f in fills:  # ALL fills, not just exits
        pnl_delta = Decimal(f["realized_pnl_delta"])
        if pnl_delta == Decimal("0"):
            continue  # skip zero-delta fills (no change to equity or curve)
        equity += pnl_delta
        ts = datetime.fromisoformat(f["ts"])
        eq_curve.append((ts, starting_balance + equity))
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    n = len(exits)
    profit_target = risk_state.config.profit_target

    return BacktestStats(
        trades=len(exits),
        wins=len(wins),
        losses=len(losses),
        win_rate=round(len(wins) / n * 100, 1) if n > 0 else 0.0,
        net_pnl=net,
        gross_win=gross_win,
        gross_loss=gross_loss,
        avg_win=gross_win / len(wins) if wins else Decimal("0"),
        avg_loss=gross_loss / len(losses) if losses else Decimal("0"),
        profit_factor=float(gross_win / gross_loss) if gross_loss > 0 else None,
        max_drawdown=max_dd,
        expectancy=net / n if n > 0 else Decimal("0"),
        is_profitable=net > 0,
        passed_combine=(
            net >= profit_target
            and risk_state.locked_out is None
        ),
        mll_breached=risk_state.locked_out is not None,
        equity_curve=eq_curve,
        by_killzone={},
    )


def _reconstruct_trades(fills: list[dict]) -> list[dict]:
    """Pair entry fills with exit fills. hold_seconds uses fill timestamps (bar time)."""
    # Assumes strict alternation: entry fill followed by exit fill.
    # A trade still open at backtest end produces a warning and is not counted.
    trades: list[dict] = []
    open_entry: dict | None = None
    for f in fills:
        if f["is_entry"]:
            open_entry = f
        elif open_entry is not None:
            entry_ts = datetime.fromisoformat(open_entry["ts"])
            exit_ts = datetime.fromisoformat(f["ts"])
            hold = int((exit_ts - entry_ts).total_seconds())
            trades.append({
                "instrument": f.get("instrument", ""),
                "side": open_entry["side"],
                "size": open_entry["size"],
                "entry_ts": open_entry["ts"],
                "entry_price": open_entry["fill_price"],
                "exit_ts": f["ts"],
                "exit_price": f["fill_price"],
                "realized_pnl": f["realized_pnl_delta"],
                "hold_seconds": hold,
            })
            open_entry = None
    if open_entry is not None:
        log.warning(
            "_reconstruct_trades: unclosed entry at bar end (entry_ts=%s)",
            open_entry["ts"],
        )
    return trades


async def run_backtest(cfg: BacktestConfig) -> BacktestResult:
    """Run one backtest. Returns a BacktestResult."""
    broker = PaperBroker(
        starting_balance=cfg.starting_balance,
        slippage_ticks_market=cfg.slippage_ticks_market,
        commission_per_side=cfg.commission_per_side,
        partial_profit_r=cfg.partial_profit_r,
    )
    risk_state = RiskState(config=fifty_k_combine(soft_buffer=cfg.soft_buffer))
    runner = _build_runner(cfg)

    fills_captured: list[dict] = []
    rejected_signals = 0

    async def on_signal(signal: Signal, outcome: OrderOutcome) -> None:
        nonlocal rejected_signals
        if not outcome.placed:
            rejected_signals += 1

    async def on_fill(fill: Fill) -> None:
        fills_captured.append({
            "ts": fill.ts.isoformat(),
            "instrument": fill.instrument,
            "side": fill.side,
            "fill_price": str(fill.fill_price),
            "size": fill.size,
            "is_entry": fill.is_entry,
            "realized_pnl_delta": str(fill.realized_pnl_delta),
        })

    engine = ExecutionEngine(
        broker=broker,
        risk_state=risk_state,
        runners=[runner],
        on_signal=on_signal,
        replay_mode=True,
        contracts=cfg.contracts,
    )
    broker.on_fill(on_fill)
    await broker.connect()
    await engine.start()

    bar_count = 0
    for bar in cfg.bars:
        await broker.inject_bar(bar)
        bar_count += 1

    await engine.stop()
    await broker.disconnect()

    stats = _compute_stats(fills_captured, risk_state, cfg.starting_balance)
    trades = _reconstruct_trades(fills_captured)

    return BacktestResult(
        config=cfg,
        stats=stats,
        trades=trades,
        bars_processed=bar_count,
        rejected_signals=rejected_signals,
        label=cfg.label,
    )


def _apply_sweep_dim(cfg: BacktestConfig, dim: SweepDimension, value: Any) -> BacktestConfig:
    """Return a new BacktestConfig with one param overridden."""
    if dim.container == "composer":
        new_sub = dataclasses.replace(cfg.composer_config, **{dim.target: value})
        return dataclasses.replace(cfg, composer_config=new_sub)
    if dim.container == "liquidity":
        new_sub = dataclasses.replace(cfg.liquidity_config, **{dim.target: value})
        return dataclasses.replace(cfg, liquidity_config=new_sub)
    if dim.container == "displacement":
        new_sub = dataclasses.replace(cfg.displacement_config, **{dim.target: value})
        return dataclasses.replace(cfg, displacement_config=new_sub)
    raise ValueError(f"Unknown container: {dim.container!r}")


async def run_sweep(
    base: BacktestConfig,
    dims: list[SweepDimension],
    bars_factory: Callable[[], Iterator[Bar]],
) -> list[BacktestResult]:
    """Run the Cartesian product of sweep dimensions."""
    import itertools
    combos = list(itertools.product(*[d.values for d in dims]))
    results: list[BacktestResult] = []
    for combo in combos:
        cfg = base
        label_parts = []
        for dim, val in zip(dims, combo):
            cfg = _apply_sweep_dim(cfg, dim, val)
            label_parts.append(f"{dim.target}={val}")
        cfg = dataclasses.replace(cfg, bars=bars_factory(), label="  ".join(label_parts))
        results.append(await run_backtest(cfg))
    return results
