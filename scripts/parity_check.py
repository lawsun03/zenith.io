"""
Live-vs-backtest parity check.

Replays the SAME vendor bars the live bot consumed (fetch them first via
scripts/fetch_bars.py — same TopstepX History API) through run_backtest
with the live bot_config, then diffs against what the live bot actually
journaled (/api/signals + /api/fills). Quantifies:

  1. signal parity — did the backtest emit the same signals (time/side)?
  2. entry slippage — live fill price vs the replay's modeled fill
  3. live-only / backtest-only signals (the divergence ledger)

Usage:
    python scripts/fetch_bars.py --symbol MNQ --days 3 --interval 1 \
        --out bars/bars_MNQ_parity.csv
    python scripts/parity_check.py --bars bars/bars_MNQ_parity.csv \
        --date 2026-06-11 [--engine ifvg] [--api http://127.0.0.1:5175]

--engine overrides bot_config's strategy.engine for the replay (use the
engine that was live on the date being checked).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

import httpx

from app.backtest.runner import BacktestConfig, run_backtest
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def fetch_live(api: str, date: str) -> tuple[list[dict], list[dict]]:
    with httpx.Client(timeout=10) as c:
        signals = c.get(f"{api}/api/signals", params={"limit": 10_000}).json()["items"]
        fills = c.get(f"{api}/api/fills", params={"limit": 10_000}).json()["items"]
    sig = [s for s in signals if s["ts"][:10] == date and s.get("placed", True)]
    fil = [f for f in fills if f["ts"][:10] == date]
    return sig, fil


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", required=True, help="1-min CSV from fetch_bars.py (same vendor as live)")
    ap.add_argument("--date", required=True, help="UTC date to diff, YYYY-MM-DD")
    ap.add_argument("--instrument", default="MNQ")
    ap.add_argument("--config", default="bot_config.json")
    ap.add_argument("--engine", default=None, help="override strategy.engine for the replay")
    ap.add_argument("--api", default="http://127.0.0.1:5175")
    ap.add_argument("--tolerance-min", type=int, default=6,
                    help="signal time-match tolerance in minutes (default: one 5m bar + 1)")
    args = ap.parse_args()

    cfg = load_bot_config(Path(args.config))
    strategy = strategy_for(cfg, args.instrument.upper())
    if args.engine:
        strategy = strategy.model_copy(update={"engine": args.engine})
    tf = (cfg.timeframes or ["5min"])[0]

    print(f"replaying {args.bars} ({tf}, engine={strategy.engine}) ...")
    bt_cfg = BacktestConfig(
        instrument=args.instrument.upper(),
        bars=load_bars_csv(args.bars, args.instrument.upper(), tf),
        timeframe=tf,
        contracts=cfg.contracts,
        risk_per_trade_pct=cfg.risk_per_trade_pct,
        partial_profit_r=cfg.partial_profit_r,
        enabled_killzones=cfg.enabled_killzones,
        strategy_params=strategy,
        # Risk limits OFF: the live shadow account's balance state differs
        # from a fresh sim; parity here is about SIGNAL EMISSION, not the
        # governor. Governor divergences show up as live-only suppressions.
        enforce_risk_limits=False,
    )
    result = asyncio.run(run_backtest(bt_cfg))
    bt_signals = [s for s in result.signals if s["ts"][:10] == args.date]

    live_signals, live_fills = fetch_live(args.api, args.date)
    live_entries = [f for f in live_fills if f.get("is_entry")]

    print(f"\n{args.date}: backtest signals={len(bt_signals)}  "
          f"live signals={len(live_signals)}  live entry fills={len(live_entries)}")

    tol = args.tolerance_min * 60
    matched, live_only = [], []
    bt_pool = list(bt_signals)
    for ls in live_signals:
        lt = _ts(ls["ts"])
        best = None
        for bs in bt_pool:
            if bs["side"] != ls.get("side"):
                continue
            dt = abs((_ts(bs["ts"]) - lt).total_seconds())
            if dt <= tol and (best is None or dt < best[0]):
                best = (dt, bs)
        if best:
            matched.append((ls, best[1], best[0]))
            bt_pool.remove(best[1])
        else:
            live_only.append(ls)

    print(f"\nmatched live<->backtest: {len(matched)}")
    for ls, bs, dt in matched:
        print(f"  {ls['ts'][11:19]}Z {ls.get('side','?'):5s} live entry={ls.get('entry','?'):>10} "
              f"| replay entry={bs['entry']:>10} stop={bs['stop']} (dt={dt:.0f}s)")
    print(f"\nlive-only signals (replay missed): {len(live_only)}")
    for ls in live_only:
        print(f"  {ls['ts'][11:19]}Z {ls.get('side','?'):5s} entry={ls.get('entry','?')} "
              f"{(ls.get('rationale') or '')[:70]}")
    print(f"backtest-only signals (live missed): {len(bt_pool)}")
    for bs in bt_pool:
        print(f"  {bs['ts'][11:19]}Z {bs['side']:5s} entry={bs['entry']} {(bs['rationale'] or '')[:70]}")

    # Entry slippage: live entry fills vs matched replay signal entries.
    slips = []
    for ls, bs, _ in matched:
        lt = _ts(ls["ts"])
        for f in live_entries:
            if abs((_ts(f["ts"]) - lt).total_seconds()) <= tol:
                try:
                    slip = abs(Decimal(str(f["fill_price"])) - Decimal(bs["entry"]))
                    slips.append(slip)
                    print(f"\n  slippage {ls['ts'][11:19]}Z: live fill {f['fill_price']} "
                          f"vs replay {bs['entry']} -> {slip} pts")
                except Exception:
                    pass
                break
    if slips:
        print(f"\nmean |live fill - replay entry|: {sum(slips) / len(slips):.2f} pts "
              f"(modeled slippage: 1 tick = 0.25)")
    return 0


if __name__ == "__main__":
    main()
