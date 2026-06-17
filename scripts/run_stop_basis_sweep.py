"""Stop-basis A/B sweep (plan 2026-06-16-stop-basis-sweep.md, Task 4).

Isolates the iFVG leg (engine='ifvg', MNQ, 5min) of the deployed config and
sweeps stop_basis ∈ {default, ifvg_edge, sweep_extreme, atr} over 5y Databento
bars, with risk limits OFF (exploration). Prints a comparison table and saves
JSON. stop_basis only affects the iFVG composer stop, so the combined engine's
ORB leg is excluded to keep the signal clean.

Run: .venv/Scripts/python.exe scripts/run_stop_basis_sweep.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root on path

from app.bot_config import load_bot_config
from app.backtest.runner import BacktestConfig, SweepDimension, run_sweep
from app.replay import load_bars_csv

BARS = "bars/bars_MNQ_dbv_2021_2026.csv"
INSTRUMENT = "MNQ"
TIMEFRAME = "5min"
BASES = ["default", "ifvg_edge", "sweep_extreme", "atr"]


def _base_config() -> BacktestConfig:
    bot = load_bot_config(Path("bot_config.json"))
    # Isolate the iFVG leg; keep every other deployed strategy param identical.
    sp = bot.strategy.model_copy(update={"engine": "ifvg"})
    return BacktestConfig(
        instrument=INSTRUMENT,
        bars=iter(()),  # replaced per-combo by run_sweep via bars_factory
        strategy_params=sp,
        timeframe=TIMEFRAME,
        contracts=getattr(bot, "contracts", 2),
        partial_profit_r=Decimal(str(getattr(bot, "partial_profit_r", "1.5"))),
        enabled_killzones=getattr(bot, "enabled_killzones", None),
        enforce_risk_limits=False,
    )


async def main() -> None:
    base = _base_config()
    dims = [SweepDimension(target="stop_basis", values=BASES, container="strategy")]
    bars_factory = lambda: load_bars_csv(BARS, instrument=INSTRUMENT, timeframe=TIMEFRAME)

    print(f"Stop-basis sweep | {INSTRUMENT} {TIMEFRAME} iFVG | {BARS}")
    print(f"bases: {BASES}  (risk limits OFF, 5y)\n")
    results = await run_sweep(base, dims, bars_factory)

    rows = []
    for r, basis in zip(results, BASES):
        s = r.stats
        rows.append({
            "stop_basis": basis,
            "trades": s.trades,
            "win_rate": s.win_rate,
            "profit_factor": s.profit_factor,
            "net_pnl": float(s.net_pnl),
            "max_drawdown": float(s.max_drawdown),
            "expectancy": float(s.expectancy),
        })

    rows.sort(key=lambda x: x["net_pnl"], reverse=True)
    hdr = f"{'stop_basis':<14}{'trades':>8}{'win%':>8}{'PF':>8}{'net_pnl':>12}{'max_dd':>12}{'expR':>8}"
    print(hdr)
    print("-" * len(hdr))
    for x in rows:
        pf = f"{x['profit_factor']:.2f}" if x["profit_factor"] is not None else "n/a"
        print(f"{x['stop_basis']:<14}{x['trades']:>8}{x['win_rate']:>8.1f}"
              f"{pf:>8}{x['net_pnl']:>12,.0f}{x['max_drawdown']:>12,.0f}{x['expectancy']:>8.1f}")

    out = Path("research") / "stop_basis_sweep_2026-06-16.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2))
    print(f"\nsaved: {out}")


if __name__ == "__main__":
    asyncio.run(main())
