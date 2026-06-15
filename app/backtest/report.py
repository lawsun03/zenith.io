"""Formatting and CSV writing for backtest results."""
from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.backtest.runner import BacktestResult, BacktestStats


def _pipeline_lines(stats) -> list[str]:
    """Pass/bust/payout summary — replaces the misleading period-total
    'Combine target: PASSED' verdict."""
    try:
        from app.backtest.funded_sim import format_pipeline_summary
        return format_pipeline_summary(stats.equity_curve).splitlines()
    except Exception as e:  # report must never crash a backtest
        return [f"(funded-pipeline summary unavailable: {e})"]


def format_summary(stats: BacktestStats, label: str = "") -> str:
    sep = "=" * 60
    title = f"BACKTEST SUMMARY: {label}" if label else "BACKTEST SUMMARY"
    lines = [
        sep, title, sep,
        f"Verdict:           {'PROFITABLE' if stats.is_profitable else 'UNPROFITABLE'}",
        *_pipeline_lines(stats),
        "",
        f"Net P&L:           {'+' if stats.net_pnl >= 0 else ''}{stats.net_pnl:.2f}",
        f"  Gross profit:    +{stats.gross_win:.2f}",
        f"  Gross loss:      -{stats.gross_loss:.2f}",
        "",
        f"Total trades:      {stats.trades}",
        f"  Winners:         {stats.wins}  ({stats.win_rate:.1f}%)",
        f"  Losers:          {stats.losses}",
        "",
        f"  Expectancy:      {'+' if stats.expectancy >= 0 else ''}{stats.expectancy:.2f} per trade",
        (f"  Profit factor:   {stats.profit_factor:.2f}" if stats.profit_factor else "  Profit factor:   N/A"),
        f"  Max drawdown:    -{stats.max_drawdown:.2f}",
        sep,
    ]
    return "\n".join(lines)


def format_sweep_table(results: list[BacktestResult]) -> str:
    sorted_r = sorted(results, key=lambda r: r.stats.net_pnl, reverse=True)
    sep = "=" * 120
    col = f"{'config':<50} {'trades':>7} {'win%':>6} {'net':>12} {'max DD':>10} {'exp':>8} {'PF':>6}"
    rows = [sep, "PARAMETER SWEEP RESULTS  (sorted by net P&L)", sep, col, "-" * 120]
    for r in sorted_r:
        s = r.stats
        pf = f"{s.profit_factor:.2f}" if s.profit_factor else "N/A"
        sign = "+" if s.net_pnl >= 0 else ""
        esign = "+" if s.expectancy >= 0 else ""
        rows.append(
            f"{r.label:<50} {s.trades:>7} {s.win_rate:>5.1f}%"
            f" {sign}{s.net_pnl:>10.2f}"
            f"  {s.max_drawdown:>9.2f}"
            f" {esign}{s.expectancy:>7.2f}"
            f" {pf:>6}"
        )
    rows.append(sep)
    return "\n".join(rows)


def write_trades_csv(trades: list[dict], out: Path) -> None:
    if not trades:
        out.write_text("")
        return
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
        writer.writeheader()
        writer.writerows(trades)


def write_equity_csv(curve: list[tuple[datetime, Decimal]], out: Path) -> None:
    with out.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ts", "equity"])
        for ts, eq in curve:
            writer.writerow([ts.isoformat(), str(eq)])
