"""
Monthly Combine simulation: can one month of trading pass the 50K Combine?

Splits a 1min bars CSV into calendar months. Each month runs as a fresh
$50K Combine with risk limits ON (trailing MLL $2k -> locks at $50k,
DLL $1k/day — app.risk fifty_k_combine). Strategy params come from
bot_config.json (instrument overrides applied) plus optional CLI tweaks.

PASS for a month = equity touches $53,000 (+$3k target, commission-
inclusive) BEFORE any trailing-MLL breach. The trailing floor is
recomputed from the fill-granularity equity curve, so intratrade
unrealized dips are not visible — results are optimistic by up to one
open trade's adverse excursion.

Usage:
    python scripts/run_monthly_combine.py \
        --bars bars/bars_MNQ_test_2025_2026.csv --instrument MNQ \
        --timeframe 5min [--risk-pct 0.25] [--contracts 2] \
        [--partial-r 1.5] [--set grader_min_grade=C] [--set stop_buffer=3.0]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from dataclasses import replace as dc_replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.runner import BacktestConfig, run_backtest
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv

STARTING = Decimal("50000")
TARGET_EQ = Decimal("53000")   # +$3k profit target
MLL_OFFSET = Decimal("2000")   # trailing offset; floor locks at STARTING


def split_months(bars_csv: Path, out_dir: Path) -> list[tuple[str, Path]]:
    """Split a 1min CSV into per-month CSVs (cached). Returns [(label, path)]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    months: dict[str, list[str]] = {}
    with open(bars_csv, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for row in reader:
            label = row[0][:7]  # YYYY-MM
            months.setdefault(label, []).append(",".join(row))
    out: list[tuple[str, Path]] = []
    for label in sorted(months):
        p = out_dir / f"{bars_csv.stem}_{label}.csv"
        if not p.exists():
            p.write_text(",".join(header) + "\n" + "\n".join(months[label]) + "\n")
        out.append((label, p))
    return out


def eval_month(equity_curve: list[tuple[datetime, Decimal]]) -> dict:
    """Walk the equity curve with the trailing-MLL rule.

    Returns pass/breach status and the timestamps where each occurred.
    Floor = min(hwm - 2000, 50000-locked); breach when equity <= floor.
    """
    hwm = STARTING
    passed_at: datetime | None = None
    breached_at: datetime | None = None
    min_eq = STARTING
    for ts, eq in equity_curve:
        if eq > hwm:
            hwm = eq
        floor = hwm - MLL_OFFSET
        if floor > STARTING:
            floor = STARTING
        if eq < min_eq:
            min_eq = eq
        if breached_at is None and eq <= floor:
            breached_at = ts
        if passed_at is None and eq >= TARGET_EQ:
            passed_at = ts
        # Stop at the first terminal event: a breach after passing is
        # irrelevant (account already passed); a pass after breaching
        # is irrelevant (account already failed).
        if passed_at or breached_at:
            break
    return {
        "passed_at": passed_at,
        "breached_at": breached_at,
        "min_eq": min_eq,
        "final_eq": equity_curve[-1][1] if equity_curve else STARTING,
    }


async def run_month(label: str, bars_path: Path, args, base_strategy) -> dict:
    cfg = BacktestConfig(
        instrument=args.instrument.upper(),
        bars=load_bars_csv(bars_path, args.instrument.upper(), args.timeframe),
        starting_balance=STARTING,
        timeframe=args.timeframe,
        contracts=args.contracts,
        risk_per_trade_pct=Decimal(args.risk_pct),
        partial_profit_r=Decimal(args.partial_r),
        enabled_killzones=args.killzones.split(",") if args.killzones else ["all"],
        strategy_params=base_strategy,
        enforce_risk_limits=True,
    )
    result = await run_backtest(cfg)
    s = result.stats
    ev = eval_month(s.equity_curve)
    return {
        "label": label,
        "trades": s.trades,
        "win_rate": s.win_rate,
        "net": s.net_pnl,
        **ev,
        "mll_breached": s.mll_breached,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True)
    ap.add_argument("--instrument", default="MNQ")
    ap.add_argument("--timeframe", default="5min")
    ap.add_argument("--contracts", type=int, default=None)
    ap.add_argument("--risk-pct", default=None)
    ap.add_argument("--partial-r", default=None)
    ap.add_argument("--killzones", default=None)
    ap.add_argument("--config", default="bot_config.json")
    ap.add_argument("--set", action="append", default=[],
                    help="StrategyParams override, e.g. --set grader_min_grade=C")
    args = ap.parse_args()

    bot_cfg = load_bot_config(Path(args.config))
    strategy = strategy_for(bot_cfg, args.instrument.upper())
    for ov in args.set:
        k, v = ov.split("=", 1)
        cur = getattr(strategy, k)  # raises if unknown — fail loud
        typ = type(cur)
        strategy = strategy.model_copy(update={k: typ(v)})
    if args.contracts is None:
        args.contracts = bot_cfg.contracts
    if args.risk_pct is None:
        args.risk_pct = str(bot_cfg.risk_per_trade_pct)
    if args.partial_r is None:
        args.partial_r = str(bot_cfg.partial_profit_r)
    if args.killzones is None:
        args.killzones = ",".join(bot_cfg.enabled_killzones)

    months = split_months(Path(args.bars), Path("bars") / "monthly")
    print(f"params: contracts={args.contracts} risk_pct={args.risk_pct} "
          f"partial_r={args.partial_r} killzones={args.killzones} "
          f"overrides={args.set or 'none'}")
    print(f"{'month':8s} {'trades':>6s} {'win%':>5s} {'net':>10s} {'min_eq':>9s} "
          f"{'result':18s}")

    passed = failed = neither = 0
    for label, path in months:
        r = asyncio.run(run_month(label, path, args, strategy))
        if r["passed_at"]:
            outcome = f"PASS on {r['passed_at']:%m-%d}"
            passed += 1
        elif r["breached_at"]:
            outcome = f"FAIL (MLL) {r['breached_at']:%m-%d}"
            failed += 1
        else:
            outcome = "no pass, survived"
            neither += 1
        print(f"{r['label']:8s} {r['trades']:6d} {r['win_rate']:5.1f} "
              f"{r['net']:10.2f} {r['min_eq']:9.2f} {outcome:18s}")

    total = passed + failed + neither
    print(f"\n{total} months: {passed} passed ({100*passed/total:.0f}%), "
          f"{failed} MLL-failed, {neither} survived without passing")
    return 0


if __name__ == "__main__":
    main()
