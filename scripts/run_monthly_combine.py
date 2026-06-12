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

import app.backtest.runner as _runner_mod
from app.backtest.runner import BacktestConfig, _trading_day_ct, run_backtest
from app.bot_config import load_bot_config, strategy_for
from app.replay import load_bars_csv
from app.risk.config import fifty_k_combine

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


def window_months(
    monthly: list[tuple[str, Path]], window: int, out_dir: Path,
) -> list[tuple[str, Path]]:
    """Concatenate consecutive per-month CSVs into non-overlapping N-month
    windows (a real Combine has no time limit — a longer window models
    keeping one attempt alive across subscription cycles)."""
    if window <= 1:
        return monthly
    out: list[tuple[str, Path]] = []
    for i in range(0, len(monthly) - window + 1, window):
        chunk = monthly[i:i + window]
        label = f"{chunk[0][0]}..{chunk[-1][0]}"
        p = out_dir / f"win{window}_{chunk[0][0]}_{chunk[-1][0]}.csv"
        if not p.exists():
            header = chunk[0][1].read_text().splitlines()[0]
            body = []
            for _, mp in chunk:
                body.extend(mp.read_text().splitlines()[1:])
            p.write_text(header + "\n" + "\n".join(body) + "\n")
        out.append((label, p))
    return out


def eval_month(equity_curve: list[tuple[datetime, Decimal]]) -> dict:
    """Walk the equity curve with the trailing-MLL and consistency rules.

    PASS requires BOTH at the same moment:
      - total profit >= $3,000 (equity >= $53k), and
      - best single trading day < 50% of total profit (Topstep consistency
        rule — a $1.5k+ day means trading on until the total dilutes it).
    Floor = min(hwm - 2000, 50000-locked); breach when equity <= floor.
    """
    hwm = STARTING
    passed_at: datetime | None = None
    breached_at: datetime | None = None
    min_eq = STARTING
    best_day = Decimal("0")
    day_pnl = Decimal("0")
    cur_day = None
    prev_eq = STARTING
    for ts, eq in equity_curve:
        td = _trading_day_ct(ts)
        if cur_day is None:
            cur_day = td
        elif td != cur_day:
            cur_day = td
            day_pnl = Decimal("0")
        day_pnl += eq - prev_eq
        prev_eq = eq
        if day_pnl > best_day:
            best_day = day_pnl

        if eq > hwm:
            hwm = eq
        floor = hwm - MLL_OFFSET
        if floor > STARTING:
            floor = STARTING
        if eq < min_eq:
            min_eq = eq
        if breached_at is None and eq <= floor:
            breached_at = ts
        profit = eq - STARTING
        if (passed_at is None and eq >= TARGET_EQ
                and best_day < profit / 2):
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
        "best_day": best_day,
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
    ap.add_argument("--dpl", default=None,
                    help="Override daily profit limit: dollar amount or 'none' "
                         "(default: fifty_k_combine's $1500)")
    ap.add_argument("--save-id", default=None,
                    help="Write a UI-visible result JSON to backtests/<id>.json")
    ap.add_argument("--save-label", default=None,
                    help="Display label for the saved result (default: param summary)")
    ap.add_argument("--window", type=int, default=1,
                    help="Months per Combine attempt window (default 1)")
    args = ap.parse_args()

    if args.dpl is not None:
        # Research-only override of the self-imposed daily profit cap; the
        # runner builds its risk config via this module-level reference.
        dpl = None if args.dpl.lower() == "none" else Decimal(args.dpl)

        def _patched(soft_buffer=Decimal("500"), _dpl=dpl):
            cfg = fifty_k_combine(soft_buffer)
            return dc_replace(cfg, daily_profit_limit=_dpl)

        _runner_mod.fifty_k_combine = _patched

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
    months = window_months(months, args.window, Path("bars") / "monthly")
    print(f"params: contracts={args.contracts} risk_pct={args.risk_pct} "
          f"partial_r={args.partial_r} killzones={args.killzones} "
          f"overrides={args.set or 'none'} dpl={args.dpl or '1500 (default)'}")
    print(f"{'month':8s} {'trades':>6s} {'win%':>5s} {'net':>10s} {'min_eq':>9s} "
          f"{'best_day':>9s} {'result':18s}")

    passed = failed = neither = 0
    rows: list[dict] = []
    days_to_pass: list[int] = []
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
              f"{r['net']:10.2f} {r['min_eq']:9.2f} {r['best_day']:9.2f} "
              f"{outcome:18s}")
        rows.append({
            "month": r["label"], "trades": r["trades"],
            "win_rate": r["win_rate"], "net": str(r["net"]),
            "min_eq": str(r["min_eq"]), "best_day": str(r["best_day"]),
            "result": outcome,
        })

    total = passed + failed + neither
    print(f"\n{total} months: {passed} passed ({100*passed/total:.0f}%), "
          f"{failed} MLL-failed, {neither} survived without passing")

    if args.save_id:
        _save_ui_result(args, rows, passed, failed, neither)
    return 0


def _save_ui_result(args, rows: list[dict], passed: int, failed: int, neither: int) -> None:
    """Write a backtests/<id>.json the BacktestsPage can list and open.

    Mirrors the field shape of app.backtest.__main__'s result dict so the
    list/detail UI renders without special-casing; the monthly table rides
    in `monthly_combine` and the pass summary reuses the funded_pipeline
    one-liner (combine block only).
    """
    import json
    import re
    from datetime import datetime, timezone

    run_id = re.sub(r"[^A-Za-z0-9_\-]", "_", args.save_id)[:64]
    total = max(passed + failed + neither, 1)
    net_total = sum(Decimal(r["net"]) for r in rows)
    trades_total = sum(r["trades"] for r in rows)
    wins_total = sum(round(r["trades"] * r["win_rate"] / 100) for r in rows)
    worst_eq = min((Decimal(r["min_eq"]) for r in rows), default=STARTING)
    # Month-end cumulative equity so the detail page draws a curve.
    eq, curve = STARTING, []
    for r in rows:
        eq += Decimal(r["net"])
        curve.append([f"{r['month']}-28T00:00:00+00:00", str(eq)])
    label = args.save_label or (
        f"Monthly Combine: risk {args.risk_pct}% "
        f"{'+ ' + ' '.join(args.set) if args.set else ''}".strip()
    )
    now = datetime.now(timezone.utc).isoformat()
    data = {
        "id": run_id,
        "label": label,
        "instrument": args.instrument.upper(),
        "timeframe": args.timeframe,
        "start_date": rows[0]["month"] if rows else None,
        "end_date": rows[-1]["month"] if rows else None,
        "bars_path": args.bars,
        "bars_processed": 0,
        "starting_balance": str(STARTING),
        "ending_balance": str(STARTING + net_total),
        "duration_seconds": 0,
        "started_at": now,
        "completed_at": now,
        "stats": {
            "trades": trades_total,
            "wins": wins_total,
            "losses": trades_total - wins_total,
            "win_rate": round(100 * wins_total / trades_total, 1) if trades_total else 0.0,
            "net_pnl": str(net_total),
            "gross_win": "0", "gross_loss": "0", "avg_win": "0", "avg_loss": "0",
            "profit_factor": None,
            "max_drawdown": str(STARTING - worst_eq),
            "expectancy": str(net_total / trades_total) if trades_total else "0",
            "is_profitable": net_total > 0,
            "passed_combine": passed > 0,
            "mll_breached": failed > 0,
            "by_killzone": {},
            "equity_curve": curve,
        },
        "funded_pipeline": {
            "combine": {
                "attempts": total, "passes": passed, "busts": failed,
                "median_days_to_pass": None,
            },
            "caveat": (
                f"monthly increments: each month is a fresh $50k Combine; "
                f"{neither} months survived without passing"
            ),
        },
        "monthly_combine": {
            "params": {
                "risk_pct": args.risk_pct, "partial_r": args.partial_r,
                "killzones": args.killzones, "overrides": args.set,
                "dpl": args.dpl or "1500",
            },
            "months": rows,
        },
        "trades": [],
        "signals": [],
        "fills": [],
    }
    out = Path("backtests") / f"{run_id}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, indent=2))
    print(f"saved UI result -> {out}")


if __name__ == "__main__":
    main()
