"""
Equity-curve export — the bridge from any backtest config to funded_sim.

Runs run_backtest on the faithful path (same interface family as
run_monthly_combine.py) with risk limits OFF and flatten ON, and writes the
fill-granularity equity curve as `ts,equity` CSV — exactly what
scripts/funded_sim.py consumes. One command pair gives any engine/config the
full funded-pipeline metrics:

    python scripts/equity_export.py --bars bars/bars_MNQ_dbv_2021_2026.csv \
        --set engine=orb --set orb_r_multiple=2.5 \
        --out research/equity_orb25.csv
    python scripts/funded_sim.py research/equity_orb25.csv --haircut 200

Risk limits are OFF here on purpose: funded_sim applies its own Combine/XFA
rules to the daily P&L; pre-filtering through the $50k governor would
double-count rules and understate the strategy's raw daily P&L.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.runner import BacktestConfig, run_backtest
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--instrument", default="MNQ")
    ap.add_argument("--timeframe", default="5min")
    ap.add_argument("--risk-pct", default=None)
    ap.add_argument("--contracts", type=int, default=None)
    ap.add_argument("--partial-r", default=None)
    ap.add_argument("--killzones", default=None)
    ap.add_argument("--config", default="bot_config.json")
    ap.add_argument("--set", action="append", default=[],
                    help="StrategyParams override, e.g. --set engine=orb")
    ap.add_argument("--trail-1r", action="store_true")
    ap.add_argument("--out", required=True, help="output equity CSV path")
    args = ap.parse_args()

    bot_cfg = load_bot_config(Path(args.config))
    strategy = strategy_for(bot_cfg, args.instrument.upper())
    for ov in args.set:
        k, v = ov.split("=", 1)
        cur = getattr(strategy, k)  # raises if unknown — fail loud
        strategy = strategy.model_copy(update={k: type(cur)(v)})

    contracts = args.contracts if args.contracts is not None else bot_cfg.contracts
    risk_pct = Decimal(args.risk_pct) if args.risk_pct is not None else bot_cfg.risk_per_trade_pct
    partial_r = Decimal(args.partial_r) if args.partial_r is not None else bot_cfg.partial_profit_r
    if args.trail_1r:
        partial_r = Decimal("0")
    killzones = (args.killzones.split(",") if args.killzones
                 else bot_cfg.enabled_killzones)

    cfg = BacktestConfig(
        instrument=args.instrument.upper(),
        bars=load_bars_csv(args.bars, args.instrument.upper(), args.timeframe),
        timeframe=args.timeframe,
        contracts=contracts,
        risk_per_trade_pct=risk_pct,
        partial_profit_r=partial_r,
        enabled_killzones=killzones,
        strategy_params=strategy,
        enforce_risk_limits=False,   # funded_sim applies its own rules
        trail_1r=args.trail_1r,
    )
    result = asyncio.run(run_backtest(cfg))
    curve = result.stats.equity_curve

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "equity"])
        for ts, eq in curve:
            w.writerow([ts.isoformat(), str(eq)])
    print(f"wrote {len(curve)} equity points -> {out} "
          f"(trades={result.stats.trades}, net={result.stats.net_pnl}, "
          f"pf={result.stats.profit_factor})")
    return 0


if __name__ == "__main__":
    main()
