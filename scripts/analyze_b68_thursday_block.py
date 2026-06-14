"""
B68 Phase 1: Thursday iFVG block — validate Thursday PF in deployed config.

Per Lesson 89: research-baseline DOW stats (ifvg_edge, all-sides) do NOT transfer
to the deployed config (close-mode, long-only). This script runs the deployed config
and extracts per-trade DOW breakdown.

GO criterion: Thursday PF < 1.0 in 3+/5 non-holdout years AND overall Thursday PF < 0.90
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.runner import BacktestConfig, run_backtest
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv

ET = ZoneInfo("America/New_York")
DOW_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def _pf(wins: list, losses: list) -> float:
    gw = sum(wins)
    gl = abs(sum(losses))
    if gl == 0:
        return float("inf") if gw > 0 else 1.0
    return float(gw / gl)


def run_year(year: int, bot_cfg, strategy, risk_pct: Decimal, partial_r: Decimal) -> list[dict]:
    bars_path = _REPO_ROOT / "bars" / "yearly" / f"bars_MNQ_dbv_{year}.csv"
    if not bars_path.exists():
        print(f"  [skip] {bars_path} not found")
        return []
    bars = load_bars_csv(str(bars_path), "MNQ", "5min")
    cfg = BacktestConfig(
        bars=bars,
        strategy_params=strategy,
        instrument="MNQ",
        timeframe="5min",
        risk_per_trade_pct=risk_pct,
        partial_profit_r=partial_r,
        contracts=bot_cfg.contracts,
        enabled_killzones=["all"],
        enforce_risk_limits=False,
    )
    result = asyncio.run(run_backtest(cfg))
    return result.trades


def analyze_trades_dow(trades: list[dict], year: int) -> dict:
    """Return per-DOW stats for given trades."""
    buckets: dict[int, list[Decimal]] = {i: [] for i in range(5)}
    for t in trades:
        ts = datetime.fromisoformat(t["entry_ts"]).astimezone(ET)
        dow = ts.weekday()  # 0=Mon, 3=Thu, 4=Fri
        if dow >= 5:
            continue
        pnl = Decimal(t["realized_pnl"])
        buckets[dow].append(pnl)
    stats = {}
    for dow, pnls in buckets.items():
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        net = sum(pnls, Decimal("0"))
        pf = _pf(wins, losses)
        stats[dow] = {"n": len(pnls), "pf": round(pf, 3), "net": float(net)}
    return stats


def main():
    bot_cfg = load_bot_config(_REPO_ROOT / "bot_config.json")
    strategy = strategy_for(bot_cfg, "MNQ")

    # Use the exact deployed config from bot_config.json — no manual overrides.
    # strategy_for already applies MNQ-specific overrides from strategy_overrides.
    risk_pct = Decimal(str(bot_cfg.risk_per_trade_pct))
    partial_r = Decimal(str(bot_cfg.partial_profit_r))

    print("B68 Phase 1 — Thursday iFVG DOW analysis (DEPLOYED config)")
    print(f"  engine={strategy.engine}  ifvg_entry_mode={strategy.ifvg_entry_mode}")
    print(f"  allowed_sides={strategy.allowed_sides}  r_multiple={strategy.r_multiple}")
    print(f"  swing_stop_lookback={strategy.swing_stop_lookback}  stop_buffer={strategy.stop_buffer}")
    print(f"  min_absolute_body={strategy.min_absolute_body}  risk={risk_pct}%  partial_r={partial_r}")
    print()

    years_to_test = [2021, 2023, 2024, 2025, 2026]  # excl 2022 holdout
    all_trades = []
    per_year_stats: dict[int, dict] = {}

    for year in years_to_test:
        print(f"  Running {year}...", end="", flush=True)
        trades = run_year(year, bot_cfg, strategy, risk_pct, partial_r)
        print(f" {len(trades)} trades")
        if trades:
            all_trades.extend(trades)
            per_year_stats[year] = analyze_trades_dow(trades, year)

    # Print per-year Thursday stats
    print()
    print("=== Per-year Thursday (DOW=3) PF in deployed config ===")
    thu_pf_years = []
    for year in years_to_test:
        if year not in per_year_stats:
            print(f"  {year}: no data")
            continue
        s = per_year_stats[year].get(3, {"n": 0, "pf": 0.0, "net": 0.0})
        thu_pf_years.append(s["pf"])
        tag = "LOSS-MAKING" if s["pf"] < 1.0 else ""
        print(f"  {year}: n={s['n']:3d}  PF={s['pf']:.3f}  net=${s['net']:,.0f}  {tag}")

    # Compute overall Thursday stats
    all_stats = analyze_trades_dow(all_trades, 0)
    thu_overall = all_stats.get(3, {"n": 0, "pf": 0.0, "net": 0.0})
    n_loss_years = sum(1 for pf in thu_pf_years if pf < 1.0)

    print()
    print(f"=== Overall Thursday (5y excl 2022) ===")
    print(f"  n={thu_overall['n']}  PF={thu_overall['pf']:.3f}  net=${thu_overall['net']:,.0f}")
    print(f"  Loss-making years: {n_loss_years}/5")
    print()

    # Full DOW breakdown
    print("=== Full DOW breakdown (5y excl 2022, deployed config) ===")
    total_net = 0.0
    for dow in range(5):
        s = all_stats.get(dow, {"n": 0, "pf": 0.0, "net": 0.0})
        total_net += s["net"]
        tag = "<-- THURSDAY" if dow == 3 else ""
        print(f"  {DOW_NAMES[dow]:12s}: n={s['n']:4d}  PF={s['pf']:.3f}  net=${s['net']:,.0f}  {tag}")
    print(f"  {'TOTAL':12s}: n={sum(all_stats[d]['n'] for d in range(5)):4d}  net=${total_net:,.0f}")

    print()
    # GO/NO-GO decision
    go_years = n_loss_years >= 3
    go_pf = thu_overall["pf"] < 0.90
    print("=== GO/NO-GO VERDICT ===")
    print(f"  Thursday PF < 1.0 in {n_loss_years}/5 years (need 3+): {'YES' if go_years else 'NO'}")
    print(f"  Overall Thursday PF < 0.90 (= {thu_overall['pf']:.3f}): {'YES' if go_pf else 'NO'}")
    if go_years and go_pf:
        print("  => PHASE 1 GO — proceed to Phase 2 (add ifvg_block_days, build, benchmark)")
    else:
        print("  => PHASE 1 NO-GO — Thursday block does not improve deployed config; REJECT")

    # Per-year full DOW (for the journal)
    print()
    print("=== Per-year full DOW breakdown ===")
    print(f"{'Year':6} | {'Mon':>12} | {'Tue':>12} | {'Wed':>12} | {'Thu':>12} | {'Fri':>12}")
    for year in years_to_test:
        if year not in per_year_stats:
            continue
        row = f"{year:6}"
        for dow in range(5):
            s = per_year_stats[year].get(dow, {"n": 0, "pf": 0.0})
            row += f" | n={s['n']:3d} PF={s['pf']:.2f}"
        print(row)


if __name__ == "__main__":
    main()
