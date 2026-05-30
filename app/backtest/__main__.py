"""
Entry point for `python -m app.backtest`.

The app/backtest/ package directory shadows app/backtest.py for -m execution,
so this __main__.py re-exposes the standalone CLI that the API server subprocess uses.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.bot_config import BotConfig, StrategyParams, load_bot_config
from app.broker.events import Fill
from app.broker.paper import PaperBroker
from app.execution.engine import ExecutionEngine, OrderOutcome, StrategyRunner
from app.strategy.grader import SetupGrader
from app.replay import load_bars_csv
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState
from app.strategy.composer import (
    ComposerConfig,
    Signal,
    SweepDisplacementComposer,
)
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.killzone import default_killzones, killzones_from_names
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker

log = logging.getLogger("topstep_bot.backtest")


def _build_runner(instrument: str, s, enabled_killzones: list[str] | None = None) -> StrategyRunner:
    zones = killzones_from_names(enabled_killzones) if enabled_killzones else default_killzones()
    strategy_params = s if isinstance(s, StrategyParams) else StrategyParams()
    return StrategyRunner(
        instrument=instrument,
        timeframe="1min",
        liquidity=LiquidityTracker(LiquidityConfig(
            swing_lookback=s.swing_lookback,
            min_penetration=s.min_penetration,
            multi_bar_window=s.multi_bar_window,
            max_swings=50,
        )),
        displacement=DisplacementDetector(DisplacementConfig(
            atr_period=s.atr_period,
            body_atr_multiple=s.body_atr_multiple,
            min_body_to_range_ratio=s.min_body_to_range_ratio,
            min_absolute_body=s.min_absolute_body,
        )),
        composer=SweepDisplacementComposer(ComposerConfig(
            instrument=instrument,
            displacement_window_bars=s.displacement_window_bars,
            stop_buffer=s.stop_buffer,
            r_multiple=s.r_multiple,
            killzones=zones,
        )),
        grader=SetupGrader(),
        strategy_cfg=strategy_params,
    )


def _to_jsonable(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _compute_stats(fills: list[dict]) -> dict:
    exits = [f for f in fills if not f["is_entry"]]
    pnls = [Decimal(f["realized_pnl_delta"]) for f in exits]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    net = sum(pnls, Decimal("0"))
    gross_win = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))

    equity = Decimal("0")
    peak = Decimal("0")
    max_dd = Decimal("0")
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    return {
        "trades": len(exits),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(exits) * 100, 1) if exits else 0.0,
        "net_pnl": str(net),
        "gross_win": str(gross_win),
        "gross_loss": str(gross_loss),
        "avg_win": str(gross_win / len(wins)) if wins else "0",
        "avg_loss": str(gross_loss / len(losses)) if losses else "0",
        "profit_factor": (float(gross_win / gross_loss) if gross_loss > 0 else None),
        "max_drawdown": str(max_dd),
    }


def _reconstruct_trades(fills: list[dict]) -> list[dict]:
    trades: list[dict] = []
    open_entry: dict | None = None
    for f in fills:
        if f["is_entry"]:
            open_entry = f
        elif open_entry is not None:
            trade: dict = {
                "entry_ts": open_entry["ts"],
                "exit_ts": f["ts"],
                "side": open_entry["side"],
                "entry_price": open_entry["fill_price"],
                "exit_price": f["fill_price"],
                "size": open_entry["size"],
                "pnl": f["realized_pnl_delta"],
            }
            if open_entry.get("grade") is not None:
                trade["grade"] = open_entry["grade"]
                trade["criteria"] = open_entry["criteria"]
            trades.append(trade)
            open_entry = None
    return trades


async def _run_backtest(
    config: BotConfig,
    bars_path: str | Path,
    instrument: str,
    timeframe: str,
    starting_balance: Decimal = Decimal("50000"),
) -> dict:
    broker = PaperBroker(starting_balance=starting_balance)
    risk_state = RiskState(config=fifty_k_combine(soft_buffer=Decimal("500")))
    runner = _build_runner(instrument, config.strategy, config.enabled_killzones)

    signals_captured: list[dict] = []
    fills_captured: list[dict] = []
    # Maps entry order_id → {grade, criteria} for grade propagation.
    # on_signal fires after place_bracket (entry fill already emitted), so
    # we stamp fills retroactively before _reconstruct_trades.
    _order_grades: dict[str, dict] = {}

    async def on_signal(signal: Signal, outcome: OrderOutcome) -> None:
        g = signal.setup_grade
        signals_captured.append({
            "ts": signal.created_at.isoformat(),
            "side": signal.side,
            "entry": str(signal.entry),
            "stop": str(signal.stop),
            "target": str(signal.target),
            "killzone": signal.killzone,
            "rationale": signal.rationale,
            "grade": g.grade if g is not None else None,
            "criteria": {
                # mom is a bool: "strong"/"decent" pass, "weak" fails
                "mom": g.momentum_quality != "weak",
                "tgt": g.target_clear,
                "fvg": g.fvg_singular,
                "pd": g.premium_discount_ok,
                "del": g.has_delivery_fvg,
            } if g is not None else None,
            "outcome": {
                "placed": outcome.placed,
                "reason": outcome.reason,
                "allowed_size": outcome.allowed_size,
            },
        })
        if outcome.placed and outcome.broker_order_id and g is not None:
            _order_grades[outcome.broker_order_id] = {
                "grade": g.grade,
                "criteria": {
                    "mom": g.momentum_quality != "weak",
                    "tgt": g.target_clear,
                    "fvg": g.fvg_singular,
                    "pd": g.premium_discount_ok,
                    "del": g.has_delivery_fvg,
                },
            }

    async def on_fill(fill: Fill) -> None:
        fills_captured.append({
            "ts": fill.ts.isoformat(),
            "instrument": fill.instrument,
            "side": fill.side,
            "fill_price": str(fill.fill_price),
            "size": fill.size,
            "is_entry": fill.is_entry,
            "realized_pnl_delta": str(fill.realized_pnl_delta),
            "order_id": fill.broker_order_id or "",
        })

    engine = ExecutionEngine(
        broker=broker,
        risk_state=risk_state,
        runners=[runner],
        on_signal=on_signal,
        replay_mode=True,
    )
    broker.on_fill(on_fill)
    await broker.connect()
    await engine.start()

    started_at = time.time()
    bar_count = 0
    for bar in load_bars_csv(bars_path, instrument=instrument, timeframe=timeframe):
        await broker.inject_bar(bar)
        bar_count += 1
    duration = time.time() - started_at

    await engine.stop()
    await broker.disconnect()

    # Retroactively attach grade to entry fill dicts now that on_signal has fired.
    for fill_dict in fills_captured:
        if fill_dict["is_entry"]:
            grade_info = _order_grades.get(fill_dict["order_id"])
            if grade_info:
                fill_dict["grade"] = grade_info["grade"]
                fill_dict["criteria"] = grade_info["criteria"]

    stats = _compute_stats(fills_captured)
    trades = _reconstruct_trades(fills_captured)

    return {
        "config": _to_jsonable(config.model_dump()),
        "instrument": instrument,
        "timeframe": timeframe,
        "bars_path": str(bars_path),
        "bars_processed": bar_count,
        "starting_balance": str(starting_balance),
        "ending_balance": str(risk_state.realized_balance),
        "duration_seconds": round(duration, 2),
        "stats": stats,
        "trades": trades,
        "signals": signals_captured,
        "fills": fills_captured,
    }


def _generate_id() -> str:
    return (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_" + uuid.uuid4().hex[:6]
    )


def _sanitize_id(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]", "", raw)[:64] or _generate_id()


async def _amain(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="bot_config.json")
    parser.add_argument("--bars", required=True)
    parser.add_argument("--instrument", default="MGC")
    parser.add_argument("--timeframe", default=None)
    parser.add_argument("--starting-balance", default="50000")
    parser.add_argument("--out-dir", default="backtests")
    parser.add_argument("--id", default=None)
    parser.add_argument("--label", default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_bot_config(Path(args.config))
    timeframe = args.timeframe or (cfg.timeframes[0] if cfg.timeframes else "1min")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    run_id = _sanitize_id(args.id) if args.id else _generate_id()
    out_path = out_dir / f"{run_id}.json"

    log.info("Backtest %s starting: instrument=%s tf=%s bars=%s",
             run_id, args.instrument, timeframe, args.bars)

    started_at = datetime.now(timezone.utc).isoformat()
    result = await _run_backtest(
        config=cfg,
        bars_path=args.bars,
        instrument=args.instrument,
        timeframe=timeframe,
        starting_balance=Decimal(args.starting_balance),
    )
    completed_at = datetime.now(timezone.utc).isoformat()

    result["id"] = run_id
    result["label"] = args.label or run_id
    result["started_at"] = started_at
    result["completed_at"] = completed_at

    out_path.write_text(json.dumps(result, indent=2))
    log.info("Backtest %s complete: %d trades, win_rate=%s%%, net=$%s → %s",
             run_id, result["stats"]["trades"], result["stats"]["win_rate"],
             result["stats"]["net_pnl"], out_path)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_amain()))
