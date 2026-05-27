"""
Walk-forward optimization CLI.

Usage:
    python scripts/walkforward.py \
        --bars bars_MGC.csv \
        --instrument MGC \
        --out-dir walkforward_results/

Edit GRID_DIMS below to change what's swept.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import itertools
import logging
import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.runner import BacktestConfig, SweepDimension
from app.bot_config import load_bot_config
from app.optimizer.walkforward import ConfigScore, run_walk_forward
from app.replay import load_bars_csv

# --- Edit this to change what's swept ---
# Faithful mode: the base config is seeded from bot_config.json (VP enabled,
# live params). All sweep dims must use the "strategy" container — it's the only
# one _build_runner honors when strategy_params is set. Every field below is a
# StrategyParams field.
GRID_DIMS = [
    SweepDimension("trend_ema_period",  [0, 50],                                          "strategy"),
    SweepDimension("body_atr_multiple", [Decimal("0.8"), Decimal("1.0"), Decimal("1.2")], "strategy"),
    SweepDimension("r_multiple",        [Decimal("2.0"), Decimal("2.5"), Decimal("3.0")], "strategy"),
    SweepDimension("stop_buffer",       [Decimal("0.20"), Decimal("0.30"), Decimal("0.40")], "strategy"),
]
# --- End edit ---


def _build_grid(dims: list[SweepDimension]) -> list[list[tuple[SweepDimension, object]]]:
    combos = list(itertools.product(*[[(d, v) for v in d.values] for d in dims]))
    return [list(c) for c in combos]


def _write_results(scores: list[ConfigScore], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = ["=" * 100, "WALK-FORWARD RANKING (sorted by combine-pass score)", "=" * 100]
    lines.append(f"{'label':<60} {'score':>6} {'pass%':>6} {'fail%':>6} {'inc%':>6} {'avg_pnl':>10}")
    lines.append("-" * 100)
    for s in scores:
        lines.append(
            f"{s.label:<60} {s.score:>6.3f} {s.pass_rate*100:>5.1f}%"
            f" {s.fail_rate*100:>5.1f}% {s.incomplete_rate*100:>5.1f}%"
            f" {'+' if s.avg_net_pnl >= 0 else ''}{s.avg_net_pnl:>9.2f}"
        )
    lines.append("=" * 100)
    if scores:
        best = scores[0]
        lines += [
            "",
            f"RECOMMENDED CONFIG: {best.label}",
            f"  Pass rate:  {best.pass_rate*100:.1f}%",
            f"  Fail rate:  {best.fail_rate*100:.1f}%",
            f"  Score:      {best.score:.3f}",
        ]
        if best.by_killzone:
            lines.append("  Per-killzone (aggregated across all test windows):")
            for kz in sorted(best.by_killzone):
                st = best.by_killzone[kz]
                pnl_sign = "+" if st["net_pnl"] >= 0 else ""
                lines.append(
                    f"    {kz:<10}  {st['trades']:>3} trades  "
                    f"{st['win_rate']:>5.1f}% WR  "
                    f"net {pnl_sign}{st['net_pnl']:>8.2f}"
                )
    ranking_path = out_dir / "walkforward_ranking.txt"
    ranking_path.write_text("\n".join(lines))
    print(f"Ranking saved to {ranking_path}")

    rows = []
    for s in scores:
        for i, w in enumerate(s.windows):
            rows.append({
                "config": s.label,
                "window": i,
                "outcome": w.outcome.value,
                "net_pnl": str(w.net_pnl),
                "max_drawdown": str(w.max_drawdown),
                "trades": w.trades,
                "win_rate": w.win_rate,
                "profit_factor": w.profit_factor if w.profit_factor is not None else "",
            })
    if rows:
        csv_path = out_dir / "walkforward_results.csv"
        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"Per-window results saved to {csv_path}")


async def _run(args: argparse.Namespace) -> None:
    instrument = args.instrument.upper()
    print(f"Loading bars from {args.bars}...", flush=True)
    all_bars = list(load_bars_csv(args.bars, instrument))
    print(f"  {len(all_bars)} bars loaded.", flush=True)

    bot_cfg = load_bot_config(Path(args.config))
    base = BacktestConfig(
        instrument=instrument,
        bars=iter([]),
        starting_balance=Decimal("50000"),
        soft_buffer=Decimal("500"),
        strategy_params=bot_cfg.strategy,
        enabled_killzones=bot_cfg.enabled_killzones,
        contracts=bot_cfg.contracts,
        risk_per_trade_pct=bot_cfg.risk_per_trade_pct,
        slippage_ticks_market=1,
        commission_per_side=Decimal("0.74"),
        partial_profit_r=args.partial_profit_r,
    )

    grid = _build_grid(GRID_DIMS)
    print(f"Grid: {len(grid)} configs x ~42 windows = ~{len(grid) * 42} backtests", flush=True)

    scores = await run_walk_forward(
        all_bars=all_bars,
        base_config=base,
        grid=grid,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
    )

    out_dir = Path(args.out_dir)
    _write_results(scores, out_dir)

    print("\nTop 5 configs by combine-pass score:")
    for s in scores[:5]:
        print(f"  {s.label}  score={s.score:.3f}  pass={s.pass_rate*100:.0f}%  fail={s.fail_rate*100:.0f}%")


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward optimizer for combine-pass-rate scoring.")
    parser.add_argument("--bars", default="bars_MGC.csv", help="Path to bars CSV")
    parser.add_argument("--instrument", default="MGC")
    parser.add_argument("--config", default="bot_config.json",
                        help="Live config to seed the faithful base config from")
    parser.add_argument("--out-dir", default="walkforward_results")
    parser.add_argument("--train-days", type=int, default=30)
    parser.add_argument("--test-days", type=int, default=10)
    parser.add_argument("--step-days", type=int, default=5)
    parser.add_argument(
        "--partial-profit-r", type=Decimal, default=Decimal("0"),
        help="Partial-profit R-multiple (0 = disabled, 1.0 = take half at 1R then BE-trail)",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    )
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
