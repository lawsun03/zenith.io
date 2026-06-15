"""
MFE/MAE excursion analysis — B2 Step 2.

Runs the backtest across specified years, collects per-trade MFE/MAE data,
and prints distribution statistics to inform exit-ladder design.

Usage:
    python scripts/analyze_mfe_mae.py --years 2021,2023,2024,2025,2026 \
        --set engine=ifvg --out research/mfe_mae_control.csv

    python scripts/analyze_mfe_mae.py --years 2021,2023,2024,2025,2026 \
        --set engine=orb --set orb_r_multiple=2.5 \
        --out research/mfe_mae_orb25.csv

Never include 2022 (frozen holdout) unless explicitly doing confirmatory.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from decimal import Decimal
from pathlib import Path

import statistics

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.runner import BacktestConfig, run_backtest
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv


def _percentiles(vals: list[float], ps=(25, 50, 75, 90)) -> dict[str, float]:
    if not vals:
        return {f"p{p}": 0.0 for p in ps}
    s = sorted(vals)
    n = len(s)
    result = {}
    for p in ps:
        idx = (p / 100) * (n - 1)
        lo, frac = int(idx), idx - int(idx)
        hi = min(lo + 1, n - 1)
        result[f"p{p}"] = round(s[lo] + frac * (s[hi] - s[lo]), 3)
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars-dir", default="bars/yearly")
    ap.add_argument("--instrument", default="MNQ")
    ap.add_argument("--timeframe", default="5min")
    ap.add_argument("--risk-pct", default=None)
    ap.add_argument("--contracts", type=int, default=None)
    ap.add_argument("--partial-r", default=None)
    ap.add_argument("--killzones", default=None)
    ap.add_argument("--config", default="bot_config.json")
    ap.add_argument("--set", action="append", default=[],
                    help="StrategyParams override, e.g. --set engine=orb")
    ap.add_argument("--years", default="2021,2023,2024,2025,2026",
                    help="Comma-separated years to include (exclude 2022)")
    ap.add_argument("--out", required=True, help="output CSV path")
    args = ap.parse_args()

    bot_cfg = load_bot_config(Path(args.config))
    strategy = strategy_for(bot_cfg, args.instrument.upper())
    for ov in args.set:
        k, v = ov.split("=", 1)
        cur = getattr(strategy, k)
        strategy = strategy.model_copy(update={k: type(cur)(v)})

    contracts = args.contracts if args.contracts is not None else bot_cfg.contracts
    risk_pct = Decimal(args.risk_pct) if args.risk_pct is not None else bot_cfg.risk_per_trade_pct
    partial_r = Decimal(args.partial_r) if args.partial_r is not None else bot_cfg.partial_profit_r
    killzones = (args.killzones.split(",") if args.killzones
                 else bot_cfg.enabled_killzones)

    years = [y.strip() for y in args.years.split(",")]
    bars_dir = Path(args.bars_dir)

    all_trades: list[dict] = []
    year_stats: list[str] = []

    for year in years:
        bars_file = bars_dir / f"bars_{args.instrument.upper()}_dbv_{year}.csv"
        if not bars_file.exists():
            print(f"WARNING: {bars_file} not found, skipping {year}")
            continue

        cfg = BacktestConfig(
            instrument=args.instrument.upper(),
            bars=load_bars_csv(str(bars_file), args.instrument.upper(), args.timeframe),
            timeframe=args.timeframe,
            contracts=contracts,
            risk_per_trade_pct=risk_pct,
            partial_profit_r=partial_r,
            enabled_killzones=killzones,
            strategy_params=strategy,
            enforce_risk_limits=False,
        )
        result = asyncio.run(run_backtest(cfg))

        for t in result.trades:
            t["year"] = year
        all_trades.extend(result.trades)
        year_stats.append(
            f"  {year}: {result.stats.trades} trades, "
            f"net={result.stats.net_pnl}, pf={result.stats.profit_factor}"
        )
        print(year_stats[-1])

    if not all_trades:
        print("No trades produced.")
        return 1

    # Write per-trade CSV
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "year", "instrument", "side", "entry_ts", "entry_price", "exit_ts",
        "exit_price", "realized_pnl", "hold_seconds", "mfe_pts", "mae_pts",
        "r_mfe", "r_mae",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_trades)

    # --- Distribution analysis ---
    trades_with_exc = [t for t in all_trades if "r_mfe" in t]
    winners = [t for t in trades_with_exc if Decimal(t["realized_pnl"]) > 0]
    losers  = [t for t in trades_with_exc if Decimal(t["realized_pnl"]) < 0]

    def _r(t): return float(t["r_mfe"])
    def _a(t): return float(t["r_mae"])

    print(f"\n=== MFE/MAE distribution — {len(trades_with_exc)} trades ===")
    print(f"  Winners: {len(winners)}   Losers: {len(losers)}")

    if winners:
        w_mfe = _percentiles([_r(t) for t in winners])
        w_mae = _percentiles([_a(t) for t in winners])
        print(f"\n  Winner MFE (R-multiples): {w_mfe}")
        print(f"  Winner MAE (R-multiples): {w_mae}")

    if losers:
        l_mfe = _percentiles([_r(t) for t in losers])
        l_mae = _percentiles([_a(t) for t in losers])
        print(f"\n  Loser MFE (R-multiples): {l_mfe}")
        print(f"  Loser MAE (R-multiples): {l_mae}")

    if winners and losers:
        # Exit-ladder design hints
        wp50_mfe = _percentiles([_r(t) for t in winners])["p50"]
        wp75_mfe = _percentiles([_r(t) for t in winners])["p75"]
        lp50_mae = _percentiles([_a(t) for t in losers])["p50"]
        print(f"\n  Ladder design hints:")
        print(f"    Partial candidate: take at {wp50_mfe:.2f}R (winner p50 MFE)")
        print(f"    Trail candidate:   trail from {wp75_mfe:.2f}R (winner p75 MFE)")
        print(f"    MAE gate:          losers median adverse {lp50_mae:.2f}R "
              f"(losers that exceeded entry MAE)")

    # Overall by side
    for side in ("long", "short"):
        side_trades = [t for t in trades_with_exc if t["side"] == side]
        if side_trades:
            s_mfe = _percentiles([_r(t) for t in side_trades])
            print(f"\n  {side.upper()} MFE percentiles: {s_mfe}")

    print(f"\n  wrote {len(all_trades)} trades -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
