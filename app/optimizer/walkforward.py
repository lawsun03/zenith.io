"""
Walk-forward optimizer for combine-pass-rate scoring.

Splits bars into rolling train/test windows, runs each param config
against every test window, scores configs by combine-pass-rate.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Iterator

from app.backtest.runner import (
    BacktestConfig,
    SweepDimension,
    _apply_sweep_dim,
    run_backtest,
)
from app.sim.events import Bar

log = logging.getLogger(__name__)


class CombineOutcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCOMPLETE = "INCOMPLETE"


@dataclass
class WindowResult:
    outcome: CombineOutcome
    net_pnl: Decimal
    max_drawdown: Decimal
    trades: int
    win_rate: float
    profit_factor: float | None = None
    by_killzone: dict[str, dict] = field(default_factory=dict)


@dataclass
class ConfigScore:
    label: str
    score: float          # pass_rate - 2 * fail_rate
    pass_rate: float
    fail_rate: float
    incomplete_rate: float
    avg_net_pnl: Decimal
    windows: list[WindowResult]
    config: BacktestConfig
    by_killzone: dict[str, dict] = field(default_factory=dict)  # aggregated across all windows


def _rolling_windows(
    trading_dates: list[date],
    train_days: int,
    test_days: int,
    step_days: int,
) -> Iterator[tuple[list[date], list[date]]]:
    """Yield (train_dates, test_dates) pairs with a rolling window."""
    total = train_days + test_days
    i = 0
    while i + total <= len(trading_dates):
        train = trading_dates[i : i + train_days]
        test = trading_dates[i + train_days : i + total]
        yield train, test
        i += step_days


def _score_config(windows: list[WindowResult]) -> float:
    """Combine-pass score: pass_rate - 2 * fail_rate."""
    n = len(windows)
    if n == 0:
        return -999.0
    passes = sum(1 for w in windows if w.outcome == CombineOutcome.PASS)
    fails = sum(1 for w in windows if w.outcome == CombineOutcome.FAIL)
    return passes / n - 2 * fails / n


def _score_config_by_expectancy(windows: list[WindowResult]) -> float:
    """Fallback score: avg per-trade net P&L across all windows.

    Used when combine-pass scoring is degenerate (all INCOMPLETE). A 10-day
    test window cannot reach the $3k combine target, so expectancy is the only
    meaningful differentiator.
    """
    total_pnl = sum(float(w.net_pnl) for w in windows if w.trades > 0)
    total_trades = sum(w.trades for w in windows if w.trades > 0)
    if total_trades == 0:
        return float("-inf")
    return total_pnl / total_trades


def scores_are_degenerate(scores: list[ConfigScore]) -> bool:
    """True if all configs have identical combine-pass scores (no differentiation).

    This occurs when test windows are too short for any config to reach the combine
    target — every window is INCOMPLETE and every score is 0.000. In that case the
    RECOMMENDED CONFIG block is meaningless (it's just grid-iteration order).
    """
    if len(scores) < 2:
        return False
    return all(s.score == scores[0].score for s in scores[1:])


def _classify_outcome_from_stats(stats) -> CombineOutcome:
    if stats.passed_combine:
        return CombineOutcome.PASS
    if stats.mll_breached:
        return CombineOutcome.FAIL
    return CombineOutcome.INCOMPLETE


def _bars_for_dates(all_bars: list[Bar], dates: set[date]) -> list[Bar]:
    return [b for b in all_bars if b.ts.date() in dates]


async def run_walk_forward(
    all_bars: list[Bar],
    base_config: BacktestConfig,
    grid: list[list[tuple[SweepDimension, object]]],
    train_days: int = 30,
    test_days: int = 10,
    step_days: int = 5,
) -> list[ConfigScore]:
    """
    Run walk-forward optimization.

    Args:
        all_bars: All bars sorted by timestamp ascending.
        base_config: Template config; bars field is replaced per window.
        grid: List of param combo lists, each item is [(dim, value), ...].
        train_days/test_days/step_days: Window scheme.

    Returns:
        List of ConfigScore, sorted by score descending.
    """
    trading_dates = sorted(set(b.ts.date() for b in all_bars))
    windows = list(_rolling_windows(trading_dates, train_days, test_days, step_days))
    log.info("Walk-forward: %d windows, %d configs", len(windows), len(grid))

    configs: list[tuple[str, BacktestConfig]] = []
    for combo in grid:
        cfg = base_config
        label_parts = []
        for dim, val in combo:
            cfg = _apply_sweep_dim(cfg, dim, val)
            label_parts.append(f"{dim.target}={val}")
        cfg = dataclasses.replace(cfg, label=" | ".join(label_parts))
        configs.append((cfg.label, cfg))

    scores_map: dict[str, list[WindowResult]] = {label: [] for label, _ in configs}

    for wi, (train_dates, test_dates) in enumerate(windows):
        test_date_set = set(test_dates)
        test_bars = _bars_for_dates(all_bars, test_date_set)
        if not test_bars:
            continue
        log.info(
            "Window %d/%d: test=%s->%s bars=%d",
            wi + 1, len(windows),
            min(test_dates), max(test_dates), len(test_bars),
        )

        for label, cfg in configs:
            window_cfg = dataclasses.replace(cfg, bars=iter(test_bars))
            try:
                result = await run_backtest(window_cfg)
            except Exception:
                log.exception("Backtest failed for config %s window %d", label, wi)
                scores_map[label].append(WindowResult(
                    outcome=CombineOutcome.FAIL,
                    net_pnl=Decimal("0"),
                    max_drawdown=Decimal("0"),
                    trades=0,
                    win_rate=0.0,
                    profit_factor=None,
                ))
                continue

            s = result.stats
            outcome = _classify_outcome_from_stats(s)
            scores_map[label].append(WindowResult(
                outcome=outcome,
                net_pnl=s.net_pnl,
                max_drawdown=s.max_drawdown,
                trades=s.trades,
                win_rate=s.win_rate,
                profit_factor=s.profit_factor,
                by_killzone=s.by_killzone,
            ))

    results: list[ConfigScore] = []
    for label, cfg in configs:
        wrs = scores_map[label]
        score = _score_config(wrs)
        n = len(wrs) if wrs else 1
        passes = sum(1 for w in wrs if w.outcome == CombineOutcome.PASS)
        fails = sum(1 for w in wrs if w.outcome == CombineOutcome.FAIL)
        incompletes = sum(1 for w in wrs if w.outcome == CombineOutcome.INCOMPLETE)
        avg_pnl = sum((w.net_pnl for w in wrs), Decimal("0")) / n if wrs else Decimal("0")

        # Aggregate by_killzone across all windows for this config.
        kz_agg: dict[str, dict] = {}
        for wr in wrs:
            for kz, st in wr.by_killzone.items():
                if kz not in kz_agg:
                    kz_agg[kz] = {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
                kz_agg[kz]["trades"] += st["trades"]
                kz_agg[kz]["wins"] += st["wins"]
                kz_agg[kz]["losses"] += st["losses"]
                kz_agg[kz]["net_pnl"] += st["net_pnl"]
        for kz in kz_agg:
            t = kz_agg[kz]["trades"]
            kz_agg[kz]["win_rate"] = round(kz_agg[kz]["wins"] / t * 100, 1) if t > 0 else 0.0

        results.append(ConfigScore(
            label=label,
            score=score,
            pass_rate=passes / n,
            fail_rate=fails / n,
            incomplete_rate=incompletes / n,
            avg_net_pnl=avg_pnl,
            windows=wrs,
            config=cfg,
            by_killzone=kz_agg,
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results
