"""
B51 Phase 1 — SetupGrader grade-vs-outcome analysis.

Runs the iFVG engine (deployed config, close mode, all-day KZ, MNQ overrides)
over 2021/2023/2024/2025/2026 (excl 2022 holdout) and computes win-rate, PF,
and mean-R per grade bucket (A/B/C/D/F).

GO/NO-GO threshold: top-2 grade bucket PF >= 1.3× bottom-2 grade bucket PF.
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.runner import BacktestConfig, BacktestResult, run_backtest
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv

YEARS = [2021, 2023, 2024, 2025, 2026]  # exclude 2022 holdout
BARS_DIR = _REPO_ROOT / "bars" / "yearly"
BOT_CFG_PATH = _REPO_ROOT / "bot_config.json"


def _run_year(year: int, strategy, bot_cfg) -> list[dict]:
    bars_path = BARS_DIR / f"bars_MNQ_dbv_{year}.csv"
    bars = load_bars_csv(str(bars_path), "MNQ", "5min")
    cfg = BacktestConfig(
        instrument="MNQ",
        bars=bars,
        timeframe="5min",
        contracts=1,
        risk_per_trade_pct=Decimal("1.0"),
        partial_profit_r=Decimal("0"),
        enabled_killzones=["all"],
        strategy_params=strategy,
        enforce_risk_limits=False,
    )
    result: BacktestResult = asyncio.run(run_backtest(cfg))
    return result.trades


def main() -> None:
    bot_cfg = load_bot_config(BOT_CFG_PATH)
    strategy = strategy_for(bot_cfg, "MNQ")

    # Deployed iFVG config: close mode, all-day KZ, MNQ body/stop overrides
    # Use engine=ifvg to isolate only graded iFVG signals (ORB signals are ungraded)
    overrides = {
        "engine": "ifvg",
        "ifvg_entry_mode": "close",
        "min_absolute_body": Decimal("5.0"),
        "stop_buffer": Decimal("3.0"),
        "r_multiple": Decimal("3.5"),
        "allowed_sides": "long",           # deployed: long-only iFVG
        "swing_stop_lookback": 0,          # research parity
        "target_clarity_mode": "off",      # deployed setting
        "grader_min_grade": "F",           # accept all grades (deployed default)
    }
    for k, v in overrides.items():
        strategy = strategy.model_copy(update={k: v})

    # Accumulate per-grade stats
    grade_stats: dict[str, dict] = {
        g: {"trades": 0, "wins": 0, "gross_win": Decimal(0), "gross_loss": Decimal(0),
            "r_sum": Decimal(0)}
        for g in ["A", "B", "C", "D", "F"]
    }
    total_trades = 0
    ungraded = 0

    for year in YEARS:
        trades = _run_year(year, strategy, bot_cfg)
        for t in trades:
            grade = t.get("grade", "")
            if not grade or grade not in grade_stats:
                ungraded += 1
                continue
            pnl = Decimal(str(t["realized_pnl"]))
            s = grade_stats[grade]
            s["trades"] += 1
            total_trades += 1
            if pnl > 0:
                s["wins"] += 1
                s["gross_win"] += pnl
            else:
                s["gross_loss"] += abs(pnl)
        print(f"  Year {year}: {len(trades)} trades")

    print(f"\nTotal graded trades: {total_trades}  (ungraded/ORB: {ungraded})")
    print("\n=== Grade vs Outcome (deployed iFVG config, 5y excl 2022) ===")
    print(f"{'Grade':>6} {'n':>6} {'WR%':>7} {'PF':>8} {'Gross Win':>12} {'Gross Loss':>12} {'Net':>12}")
    print("-" * 70)

    pf_by_grade = {}
    for grade in ["A", "B", "C", "D", "F"]:
        s = grade_stats[grade]
        n = s["trades"]
        if n == 0:
            print(f"{'  ' + grade:>6} {'0':>6} {'—':>7} {'—':>8}")
            pf_by_grade[grade] = None
            continue
        wr = 100 * s["wins"] / n
        pf = (s["gross_win"] / s["gross_loss"]) if s["gross_loss"] > 0 else None
        net = s["gross_win"] - s["gross_loss"]
        pf_str = f"{pf:.3f}" if pf else "N/A"
        print(f"{'  ' + grade:>6} {n:>6} {wr:>6.1f}% {pf_str:>8} "
              f"{float(s['gross_win']):>11,.0f} {float(s['gross_loss']):>11,.0f} "
              f"{float(net):>+11,.0f}")
        pf_by_grade[grade] = pf

    print("\n=== GO/NO-GO Assessment ===")
    # Top-2 grades: A + B; bottom-2: D + F
    top_grades = ["A", "B"]
    bot_grades = ["D", "F"]

    top_gw = sum(grade_stats[g]["gross_win"] for g in top_grades)
    top_gl = sum(grade_stats[g]["gross_loss"] for g in top_grades)
    bot_gw = sum(grade_stats[g]["gross_win"] for g in bot_grades)
    bot_gl = sum(grade_stats[g]["gross_loss"] for g in bot_grades)

    top_pf = (top_gw / top_gl) if top_gl > 0 else None
    bot_pf = (bot_gw / bot_gl) if bot_gl > 0 else None

    if top_pf is None or bot_pf is None or bot_pf == 0:
        print(f"Top-2 (A+B) PF: {top_pf}  Bottom-2 (D+F) PF: {bot_pf}")
        print("CANNOT ASSESS — insufficient data in one bucket")
    else:
        ratio = top_pf / bot_pf
        print(f"Top-2 (A+B) PF:    {float(top_pf):.3f}")
        print(f"Bottom-2 (D+F) PF: {float(bot_pf):.3f}")
        print(f"PF ratio (top/bot): {float(ratio):.3f}  (threshold: >= 1.3)")
        if ratio >= Decimal("1.3"):
            print("VERDICT: GO — grade predicts outcome; proceed to Phase 2")
        else:
            print("VERDICT: NO-GO — grade does not predict outcome; reject B51")


if __name__ == "__main__":
    main()
