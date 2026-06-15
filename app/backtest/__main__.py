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
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.backtest.runner import BacktestConfig, run_backtest as _runner_backtest
from app.bot_config import BotConfig, load_bot_config, strategy_for
from app.replay import load_bars_csv

log = logging.getLogger("topstep_bot.backtest")


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


async def _run_backtest(
    config: BotConfig,
    bars_path: str | Path,
    instrument: str,
    timeframe: str,
    starting_balance: Decimal = Decimal("50000"),
    enforce_risk_limits: bool = True,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    t0 = time.time()
    bc = BacktestConfig(
        instrument=instrument,
        bars=load_bars_csv(bars_path, instrument=instrument, timeframe=timeframe),
        starting_balance=starting_balance,
        timeframe=timeframe,
        contracts=config.contracts,
        risk_per_trade_pct=config.risk_per_trade_pct,
        partial_profit_r=config.partial_profit_r,
        max_entry_slippage_frac=config.max_entry_slippage_frac,
        enabled_killzones=config.enabled_killzones,
        strategy_params=strategy_for(config, instrument),
        enforce_risk_limits=enforce_risk_limits,
    )
    result = await _runner_backtest(bc)
    duration = time.time() - t0

    s = result.stats
    stats = {
        "trades": s.trades,
        "wins": s.wins,
        "losses": s.losses,
        "win_rate": s.win_rate,
        "net_pnl": str(s.net_pnl),
        "gross_win": str(s.gross_win),
        "gross_loss": str(s.gross_loss),
        "avg_win": str(s.avg_win),
        "avg_loss": str(s.avg_loss),
        "profit_factor": s.profit_factor,
        "max_drawdown": str(s.max_drawdown),
        "expectancy": str(s.expectancy),
        "is_profitable": s.is_profitable,
        "passed_combine": s.passed_combine,
        "mll_breached": s.mll_breached,
        "by_killzone": s.by_killzone,
        "equity_curve": [[ts.isoformat(), str(eq)] for ts, eq in s.equity_curve],
    }

    # Funded-pipeline replay (sequential Combines + XFA chain through the
    # same PhaseTracker the live governor uses). Never let it kill a result.
    try:
        from app.backtest.funded_sim import (
            daily_pnls_from_equity, simulate_combines, simulate_xfa_chain,
        )
        daily = daily_pnls_from_equity(s.equity_curve)
        funded_pipeline = _to_jsonable({
            "combine": simulate_combines(daily),
            "xfa": simulate_xfa_chain(daily),
            "caveat": "daily granularity — intraday MLL touches understated",
        })
    except Exception as e:
        funded_pipeline = {"error": str(e)}

    return {
        "config": _to_jsonable(config.model_dump()),
        "instrument": instrument,
        "timeframe": timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "bars_path": str(bars_path),
        "bars_processed": result.bars_processed,
        "starting_balance": str(starting_balance),
        "ending_balance": str(starting_balance + s.net_pnl),
        "duration_seconds": round(duration, 2),
        "stats": stats,
        "funded_pipeline": funded_pipeline,
        "trades": result.trades,
        "signals": [],
        "fills": [],
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
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--no-risk-limits", action="store_true",
                        help="Disable MLL/DLL/DPL (exploration only — not representative of live conditions)")
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
        enforce_risk_limits=not args.no_risk_limits,
        start_date=args.start_date,
        end_date=args.end_date,
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
    import sys
    sys.exit(asyncio.run(_amain()))
