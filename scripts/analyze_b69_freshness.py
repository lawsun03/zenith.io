"""
B69 Phase 1 — iFVG setup freshness (displacement-to-inversion bar gap).

Runs the iFVG engine (deployed config, close mode, all-day KZ, MNQ overrides)
over 2021/2023/2024/2025/2026 (excl 2022 holdout) and computes win-rate and PF
per freshness bucket defined as:
  fresh: gap_bars in [1, 3]
  mid:   gap_bars in [4, 9]
  stale: gap_bars >= 10

gap_bars = (entry_ts - displacement_ts) / 300 seconds (5-min bars).

GO criteria: fresh PF >= 1.3x stale PF, consistent in 3+/5 years.
"""
from __future__ import annotations

import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.runner import BacktestConfig, BacktestResult, run_backtest
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv

YEARS = [2021, 2023, 2024, 2025, 2026]   # exclude 2022 holdout
BARS_DIR = _REPO_ROOT / "bars" / "yearly"
BOT_CFG_PATH = _REPO_ROOT / "bot_config.json"
TF_SECONDS = 300   # 5-min bars


def _gap_bars(trade: dict) -> float | None:
    """Return gap in 5-min bars between displacement and inversion. None if no displacement_ts."""
    d_ts_str = trade.get("displacement_ts")
    if not d_ts_str:
        return None
    entry_ts = datetime.fromisoformat(trade["entry_ts"])
    disp_ts = datetime.fromisoformat(d_ts_str)
    gap_sec = (entry_ts - disp_ts).total_seconds()
    return gap_sec / TF_SECONDS


def _bucket(gap: float | None) -> str:
    if gap is None:
        return "no_ts"
    if gap <= 3:
        return "fresh"
    if gap <= 9:
        return "mid"
    return "stale"


def _run_year(year: int, strategy) -> list[dict]:
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


def _pf(gw: Decimal, gl: Decimal) -> float | None:
    if gl == 0:
        return None
    return float(gw / gl)


def main() -> None:
    bot_cfg = load_bot_config(BOT_CFG_PATH)
    strategy = strategy_for(bot_cfg, "MNQ")

    overrides = {
        "engine": "ifvg",
        "ifvg_entry_mode": "close",
        "min_absolute_body": Decimal("5.0"),
        "stop_buffer": Decimal("3.0"),
        "r_multiple": Decimal("3.5"),
        "allowed_sides": "long",
        "swing_stop_lookback": 0,
        "target_clarity_mode": "off",
        "grader_min_grade": "F",
    }
    for k, v in overrides.items():
        strategy = strategy.model_copy(update={k: v})

    BUCKETS = ["fresh", "mid", "stale", "no_ts"]
    bucket_stats: dict[str, dict] = {
        b: {"trades": 0, "wins": 0, "gw": Decimal(0), "gl": Decimal(0)}
        for b in BUCKETS
    }

    # Per-year tracking for consistency check.
    year_pf: dict[int, dict[str, float | None]] = {}

    print(f"\n{'Year':>6}  {'Total':>7}  "
          f"{'fresh_n':>8} {'fresh_PF':>9}  "
          f"{'mid_n':>6} {'mid_PF':>7}  "
          f"{'stale_n':>7} {'stale_PF':>8}  {'no_ts_n':>8}")
    print("-" * 90)

    for year in YEARS:
        trades = _run_year(year, strategy)
        yr_stats: dict[str, dict] = {
            b: {"trades": 0, "wins": 0, "gw": Decimal(0), "gl": Decimal(0)}
            for b in BUCKETS
        }
        for t in trades:
            pnl = Decimal(str(t["realized_pnl"]))
            b = _bucket(_gap_bars(t))
            s = yr_stats[b]
            gs = bucket_stats[b]
            for stat in (s, gs):
                stat["trades"] += 1
                if pnl > 0:
                    stat["wins"] += 1
                    stat["gw"] += pnl
                else:
                    stat["gl"] += abs(pnl)

        def ys(b: str) -> str:
            s = yr_stats[b]
            n = s["trades"]
            if n == 0:
                return f"{'0':>8} {'—':>9}"
            pf = _pf(s["gw"], s["gl"])
            pf_str = f"{pf:.3f}" if pf else "N/A"
            return f"{n:>8} {pf_str:>9}"

        no_ts_n = yr_stats["no_ts"]["trades"]
        print(f"{year:>6}  {len(trades):>7}  {ys('fresh')}  {ys('mid')}  {ys('stale')}  {no_ts_n:>8}")

        year_pf[year] = {
            b: _pf(yr_stats[b]["gw"], yr_stats[b]["gl"])
            for b in BUCKETS
        }

    print("-" * 90)
    print("\n=== 5y Combined (excl 2022) ===")
    print(f"{'Bucket':>8} {'n':>6} {'WR%':>7} {'Gross Win':>12} {'Gross Loss':>12} {'Net':>12} {'PF':>7}")
    print("-" * 70)

    for b in BUCKETS:
        s = bucket_stats[b]
        n = s["trades"]
        if n == 0:
            print(f"{b:>8} {'0':>6}")
            continue
        wr = 100 * s["wins"] / n
        pf = _pf(s["gw"], s["gl"])
        net = s["gw"] - s["gl"]
        pf_str = f"{pf:.3f}" if pf else "N/A"
        print(f"{b:>8} {n:>6} {wr:>6.1f}% {float(s['gw']):>11,.0f} {float(s['gl']):>11,.0f} "
              f"{float(net):>+11,.0f} {pf_str:>7}")

    fresh_pf = _pf(bucket_stats["fresh"]["gw"], bucket_stats["fresh"]["gl"])
    stale_pf = _pf(bucket_stats["stale"]["gw"], bucket_stats["stale"]["gl"])

    print("\n=== GO/NO-GO Assessment ===")
    if fresh_pf is None or stale_pf is None or stale_pf == 0:
        print(f"fresh PF: {fresh_pf}  stale PF: {stale_pf}")
        print("CANNOT ASSESS — insufficient data in one bucket")
    else:
        ratio = fresh_pf / stale_pf
        print(f"Fresh PF:  {fresh_pf:.3f}")
        print(f"Stale PF:  {stale_pf:.3f}")
        print(f"PF ratio (fresh/stale): {ratio:.3f}  (threshold: >= 1.30)")

        # Count years where fresh_pf > stale_pf
        go_years = sum(
            1 for y in YEARS
            if year_pf[y]["fresh"] is not None
            and year_pf[y]["stale"] is not None
            and year_pf[y]["fresh"] > year_pf[y]["stale"]
        )
        print(f"Years where fresh > stale PF: {go_years}/{len(YEARS)}")
        print(f"  (requirement: 3+ consistent years)")

        if ratio >= 1.30 and go_years >= 3:
            print("\nVERDICT: GO — freshness predicts outcome; proceed to Phase 2 gate")
        else:
            reason = []
            if ratio < 1.30:
                reason.append(f"PF ratio {ratio:.3f} < 1.30")
            if go_years < 3:
                reason.append(f"only {go_years}/5 consistent years")
            print(f"\nVERDICT: NO-GO — {'; '.join(reason)}")


if __name__ == "__main__":
    main()
