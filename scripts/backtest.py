"""
Backtest CLI.

Single run:
    python scripts/backtest.py \\
        --bars data/gc_2024.csv \\
        --instrument GC \\
        --output-dir backtest_results

Parameter sweep:
    python scripts/backtest.py \\
        --bars data/gc_2024.csv \\
        --instrument GC \\
        --sweep r_multiple=1.5,2.0,2.5,3.0 \\
        --sweep body_atr_multiple=1.2,1.5,2.0 \\
        --output-dir backtest_results

The sweep flag is repeatable; each one adds a dimension to the
cartesian product. Recognized parameter names:

  composer:
    r_multiple, displacement_window_bars, stop_buffer
  liquidity:
    swing_lookback, min_penetration, multi_bar_window
  displacement:
    atr_period, body_atr_multiple, min_body_to_range_ratio,
    min_absolute_body
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from decimal import Decimal
from pathlib import Path

# Allow running this script directly: ensure repo root is on sys.path.
# (When run as `python -m app.scripts.backtest`, this is unnecessary;
# when run as `python scripts/backtest.py`, it's required.)
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.report import (
    format_summary,
    format_sweep_table,
    write_equity_csv,
    write_trades_csv,
)
from app.backtest.runner import (
    BacktestConfig,
    BacktestResult,
    SweepDimension,
    run_backtest,
    run_sweep,
)
from app.bot_config import load_bot_config
from app.replay import load_bars_csv
from app.strategy.composer import ComposerConfig
from app.strategy.displacement import DisplacementConfig
from app.strategy.liquidity import LiquidityConfig

# Faithful mode (default): the backtest is seeded from bot_config.json and runs
# with VP enabled, exactly like live. Every sweepable parameter is a field on
# StrategyParams, so all sweeps target the "strategy" container (the only one
# _build_runner honors when strategy_params is set). name → value type.
STRATEGY_PARAM_TYPES: dict[str, type] = {
    "swing_lookback":             int,
    "min_penetration":            Decimal,
    "multi_bar_window":           int,
    "atr_period":                 int,
    "body_atr_multiple":          Decimal,
    "min_body_to_range_ratio":    Decimal,
    "min_absolute_body":          Decimal,
    "displacement_window_bars":   int,
    "stop_buffer":                Decimal,
    "r_multiple":                 Decimal,
    "trend_ema_period":           int,
    "min_atr_filter":             Decimal,
    "max_atr_filter":             Decimal,
    "cooldown_bars_after_stop":   int,
    "min_penetration_atr_factor": Decimal,
    "vp_min_target_r":            Decimal,
    "vp_filter_tolerance":        Decimal,
}

# Legacy mode (--legacy): no VP, bare sub-configs. name → (container, type).
PARAM_REGISTRY = {
    # composer
    "r_multiple":              ("composer",     Decimal),
    "displacement_window_bars":("composer",     int),
    "stop_buffer":             ("composer",     Decimal),
    # liquidity
    "swing_lookback":          ("liquidity",    int),
    "min_penetration":         ("liquidity",    Decimal),
    "multi_bar_window":        ("liquidity",    int),
    # displacement
    "atr_period":              ("displacement", int),
    "body_atr_multiple":       ("displacement", Decimal),
    "min_body_to_range_ratio": ("displacement", Decimal),
    "min_absolute_body":       ("displacement", Decimal),
}


def parse_sweep_arg(arg: str, faithful: bool = True) -> SweepDimension:
    """Parse 'r_multiple=1.5,2.0,2.5'."""
    if "=" not in arg:
        raise SystemExit(f"--sweep needs key=v1,v2,...  got {arg!r}")
    name, vals = arg.split("=", 1)
    name = name.strip()
    if faithful:
        if name not in STRATEGY_PARAM_TYPES:
            raise SystemExit(
                f"Unknown sweep parameter {name!r}. "
                f"Available: {', '.join(sorted(STRATEGY_PARAM_TYPES))}"
            )
        container, kind = "strategy", STRATEGY_PARAM_TYPES[name]
    else:
        if name not in PARAM_REGISTRY:
            raise SystemExit(
                f"Unknown sweep parameter {name!r}. "
                f"Available: {', '.join(sorted(PARAM_REGISTRY))}"
            )
        container, kind = PARAM_REGISTRY[name]
    values = [kind(v.strip()) for v in vals.split(",") if v.strip()]
    if not values:
        raise SystemExit(f"--sweep {name} has no values")
    return SweepDimension(target=name, values=values, container=container)


def build_base_config(args: argparse.Namespace) -> BacktestConfig:
    """
    Build the BacktestConfig that all runs (single or sweep) start from.

    Faithful mode (default): seed StrategyParams, killzones, contracts, and
    risk_per_trade_pct from bot_config.json so the backtest matches live —
    including the VP gate. Legacy mode (--legacy): bare sub-configs, no VP.
    """
    instrument = args.instrument.upper()
    if not getattr(args, "legacy", False):
        bot_cfg = load_bot_config(Path(args.config))
        return BacktestConfig(
            instrument=instrument,
            bars=iter([]),  # filled in per-run
            starting_balance=Decimal(args.starting_balance),
            soft_buffer=Decimal(args.soft_buffer),
            strategy_params=bot_cfg.strategy,
            enabled_killzones=bot_cfg.enabled_killzones,
            contracts=bot_cfg.contracts,
            risk_per_trade_pct=bot_cfg.risk_per_trade_pct,
        )
    # Legacy bare-config path (no VP).
    return BacktestConfig(
        instrument=instrument,
        bars=iter([]),  # filled in per-run
        starting_balance=Decimal(args.starting_balance),
        soft_buffer=Decimal(args.soft_buffer),
        liquidity_config=LiquidityConfig(
            swing_lookback=3,
            min_penetration=Decimal("0.20"),
            multi_bar_window=3,
        ),
        displacement_config=DisplacementConfig(
            atr_period=14,
            body_atr_multiple=Decimal("1.5"),
            min_body_to_range_ratio=Decimal("0.6"),
            min_absolute_body=Decimal("1.0"),
        ),
        composer_config=ComposerConfig(
            instrument=instrument,
            displacement_window_bars=5,
            stop_buffer=Decimal("0.30"),
            r_multiple=Decimal("2.0"),
        ),
    )


def write_outputs(
    result: BacktestResult,
    output_dir: Path,
    suffix: str = "",
) -> None:
    """Write trades.csv, equity.csv, summary.txt for one run."""
    output_dir.mkdir(parents=True, exist_ok=True)
    sfx = f"_{suffix}" if suffix else ""

    write_trades_csv(result.trades, output_dir / f"trades{sfx}.csv")
    write_equity_csv(result.stats.equity_curve, output_dir / f"equity{sfx}.csv")
    summary = format_summary(result.stats, label=result.label)
    (output_dir / f"summary{sfx}.txt").write_text(summary)


async def run_single(args: argparse.Namespace) -> int:
    """One backtest, one summary."""
    base = build_base_config(args)
    base.bars = load_bars_csv(args.bars, base.instrument)
    base.label = f"single ({args.instrument})"

    result = await run_backtest(base)

    print(format_summary(result.stats, label=result.label))
    print()
    print(f"Bars processed: {result.bars_processed}")
    print(f"Signals rejected by gate: {result.rejected_signals}")

    if args.output_dir:
        write_outputs(result, Path(args.output_dir))
        print(f"\nWrote outputs to {args.output_dir}/")

    return 0 if result.stats.is_profitable else 1


async def run_sweep_cmd(
    args: argparse.Namespace,
    dims: list[SweepDimension],
) -> int:
    """Sweep over the cartesian product of the given dimensions."""
    base = build_base_config(args)
    instrument = base.instrument

    def bars_factory():
        return load_bars_csv(args.bars, instrument)

    results = await run_sweep(base, dims, bars_factory)

    print(format_sweep_table(results))
    print()
    print(f"Total runs: {len(results)}")
    print(f"Profitable: {sum(1 for r in results if r.stats.is_profitable)}")
    print(f"Passed Combine target: {sum(1 for r in results if r.stats.passed_combine)}")

    if args.output_dir:
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        # One file per run, plus the comparison table.
        for i, r in enumerate(results):
            write_outputs(r, out, suffix=f"run{i:02d}")
        (out / "sweep_table.txt").write_text(format_sweep_table(results))
        print(f"\nWrote outputs to {out}/")

    return 0


async def run_multi_symbol(args: argparse.Namespace, symbols: list[str]) -> int:
    """Run a single backtest (or sweep) for each symbol and print a combined table."""
    all_results: list[BacktestResult] = []

    for symbol in symbols:
        bars_path = args.bars or f"bars/bars_{symbol}.csv"
        if not Path(bars_path).exists():
            print(f"SKIP {symbol}: {bars_path} not found")
            continue

        args_copy = argparse.Namespace(**vars(args))
        args_copy.instrument = symbol
        args_copy.bars = bars_path

        if args.sweep:
            dims = [parse_sweep_arg(s, faithful=not args.legacy) for s in args.sweep]
            base = build_base_config(args_copy)
            def bars_factory(p=bars_path, sym=symbol):
                return load_bars_csv(p, sym)
            results = await run_sweep(base, dims, bars_factory)
        else:
            base = build_base_config(args_copy)
            base.bars = load_bars_csv(bars_path, symbol)
            base.label = symbol
            results = [await run_backtest(base)]

        all_results.extend(results)

        if args.output_dir:
            out = Path(args.output_dir) / symbol
            out.mkdir(parents=True, exist_ok=True)
            for i, r in enumerate(results):
                write_outputs(r, out, suffix=f"run{i:02d}" if len(results) > 1 else "")

    if all_results:
        print(format_sweep_table(all_results))
        print(f"\nTotal runs: {len(all_results)}")
        print(f"Profitable: {sum(1 for r in all_results if r.stats.is_profitable)}")
        print(f"Passed Combine target: {sum(1 for r in all_results if r.stats.passed_combine)}")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="topstep-bot backtest runner")
    parser.add_argument(
        "--bars", default=None,
        help="Path to OHLCV CSV. Defaults to bars/bars_{SYMBOL}.csv when --symbol is used.",
    )
    parser.add_argument(
        "--symbol", default=None,
        help="Symbol or comma-separated list for multi-symbol runs (e.g. MGC,MNQ,ES). "
             "Auto-loads bars/bars_{SYMBOL}.csv for each.",
    )
    parser.add_argument(
        "--instrument", default="MGC",
        help="Instrument symbol for single-run mode (default: MGC)",
    )
    parser.add_argument(
        "--starting-balance", default="50000",
        help="Starting account balance (default: 50000)",
    )
    parser.add_argument(
        "--soft-buffer", default="500",
        help="Soft buffer above MLL/DLL (default: 500)",
    )
    parser.add_argument(
        "--config", default="bot_config.json",
        help="Live config to seed faithful runs from (default: bot_config.json)",
    )
    parser.add_argument(
        "--legacy", action="store_true",
        help="Use bare sub-configs with NO VP filter (old behavior). "
             "Default is faithful: seed from --config and run VP like live.",
    )
    parser.add_argument(
        "--sweep", action="append", default=[],
        help="Sweep dimension: key=v1,v2,v3 (repeatable)",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Optional directory to write trades/equity/summary CSVs",
    )
    parser.add_argument(
        "--log-level", default="WARNING",
        help="Python log level (default: WARNING)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # Multi-symbol mode: --symbol MGC,MNQ,ES
    if args.symbol:
        symbols = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]
        if len(symbols) > 1 or (len(symbols) == 1 and not args.bars):
            return asyncio.run(run_multi_symbol(args, symbols))
        # Single symbol via --symbol, treat as --instrument
        args.instrument = symbols[0]
        if not args.bars:
            args.bars = f"bars/bars_{symbols[0]}.csv"

    if not args.bars:
        print("ERROR: provide --bars <path> or --symbol <SYMBOL>", file=sys.stderr)
        return 1

    if args.sweep:
        dims = [parse_sweep_arg(s, faithful=not args.legacy) for s in args.sweep]
        return asyncio.run(run_sweep_cmd(args, dims))
    return asyncio.run(run_single(args))


if __name__ == "__main__":
    sys.exit(main())
