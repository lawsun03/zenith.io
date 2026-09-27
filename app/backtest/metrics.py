"""Research metrics in R, always gross and net side by side (protocol §6).

Trades come out of the backtest with slipped fill prices. Gross R removes the
slippage the broker applied; net R then charges the CostSpec once, and the
stress figure charges slippage × stress_multiplier. Commission is never
stressed — it is known, slippage is not.

Metrics are per contract (size-independent). A trade whose exit fill was a
stop or a forced flatten is treated as a stop/market exit (slipped); any other
exit is a resting limit (unslipped). Partial exits are not paired correctly by
the runner's trade reconstruction and are out of scope here.
"""
from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from app.backtest.costs import CostSpec
from app.risk.flatten import trading_day_ct

log = logging.getLogger(__name__)

COST_R_FLAG = 0.15


def trade_r(trade: dict, spec: CostSpec, applied_slip_ticks: int,
            stress_multiplier: float = 2.0) -> dict | None:
    """Per-trade gross/net/stress R. None when the trade has no stop distance."""
    risk = Decimal(str(trade.get("stop_dist") or "0"))
    if risk <= 0:
        return None
    sign = Decimal(1) if trade["side"] == "long" else Decimal(-1)
    applied = spec.tick * applied_slip_ticks
    exit_is_stop = bool(trade.get("exit_is_stop"))
    # Undo the broker's adverse slippage: entries are always market fills.
    gross_entry = Decimal(str(trade["entry_price"])) - sign * applied
    gross_exit = Decimal(str(trade["exit_price"])) + (sign * applied if exit_is_stop else 0)
    gross_r = sign * (gross_exit - gross_entry) / risk

    exit_slip = spec.slip_ticks_stop if exit_is_stop else spec.slip_ticks_limit
    slip_pts = spec.tick * (spec.slip_ticks_market + exit_slip)
    comm_pts = spec.commission_rt / spec.point_value
    cost_r = (slip_pts + comm_pts) / risk
    stress_r = (slip_pts * Decimal(str(stress_multiplier)) + comm_pts) / risk
    return {
        "gross_R": float(gross_r),
        "net_R": float(gross_r - cost_r),
        "net_R_stress": float(gross_r - stress_r),
        "cost_R": float(cost_r),
        "risk_usd": float(risk * spec.point_value),
    }


def _t(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    return statistics.fmean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else None


def _payoff(xs: list[float]) -> float | None:
    wins = [x for x in xs if x > 0]
    losses = [-x for x in xs if x < 0]
    if not wins or not losses:
        return None
    return statistics.fmean(wins) / statistics.fmean(losses)


def _max_drawdown(xs: list[float]) -> float:
    peak = cum = dd = 0.0
    for x in xs:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def _longest_losing_streak(xs: list[float]) -> int:
    best = run = 0
    for x in xs:
        run = run + 1 if x < 0 else 0
        best = max(best, run)
    return best


def research_metrics(trades: list[dict], spec: CostSpec, applied_slip_ticks: int,
                     stress_multiplier: float = 2.0,
                     mll_usd: Decimal = Decimal("2000")) -> dict:
    """§6 metric set. Also writes gross_R/net_R/net_R_stress/cost_R onto each
    trade dict so trade lists carry the same numbers as the summary."""
    rows: list[tuple[datetime, dict]] = []
    skipped = 0
    for t in trades:
        r = trade_r(t, spec, applied_slip_ticks, stress_multiplier)
        if r is None:
            skipped += 1
            continue
        t.update({k: round(v, 4) for k, v in r.items()})
        rows.append((datetime.fromisoformat(t["entry_ts"]), r))
    if skipped:
        log.warning("research_metrics: %d trade(s) without stop distance excluded", skipped)
    rows.sort(key=lambda x: x[0])

    n = len(rows)
    out: dict = {"n_trades": n, "excluded_no_stop": skipped,
                 "stress_multiplier": stress_multiplier}
    if n == 0:
        return out

    gross = [r["gross_R"] for _, r in rows]
    net = [r["net_R"] for _, r in rows]
    stress = [r["net_R_stress"] for _, r in rows]
    cost = [r["cost_R"] for _, r in rows]
    span_days = (rows[-1][0] - rows[0][0]).total_seconds() / 86400
    weeks = max(span_days / 7, 1.0)

    per_year: dict[int, dict] = defaultdict(lambda: {"n": 0, "gross_R": 0.0, "net_R": 0.0})
    for ts, r in rows:
        y = per_year[trading_day_ct(ts).year]
        y["n"] += 1
        y["gross_R"] += r["gross_R"]
        y["net_R"] += r["net_R"]
    years = {k: {"n": v["n"], "gross_R": round(v["gross_R"], 4), "net_R": round(v["net_R"], 4)}
             for k, v in sorted(per_year.items())}
    year_net = [v["net_R"] for v in years.values()]
    best = max(years, key=lambda k: years[k]["net_R"])
    rest = [r["net_R"] for ts, r in rows if trading_day_ct(ts).year != best]

    loss_usd = [-r["net_R"] * r["risk_usd"] for _, r in rows if r["net_R"] < 0]
    cost_median = statistics.median(cost)
    out.update({
        "trades_per_week": round(n / weeks, 2),
        "gross_R_mean": statistics.fmean(gross),
        "net_R_mean": statistics.fmean(net),
        "net_R_mean_stress": statistics.fmean(stress),
        "cost_R_median": cost_median,
        "cost_R_flag": cost_median > COST_R_FLAG,
        "gross_t": _t(gross),
        "net_t": _t(net),
        "gross_win_rate": sum(x > 0 for x in gross) / n,
        "net_win_rate": sum(x > 0 for x in net) / n,
        "gross_payoff_ratio": _payoff(gross),
        "net_payoff_ratio": _payoff(net),
        "max_drawdown_R": _max_drawdown(net),
        "longest_losing_streak": _longest_losing_streak(net),
        "per_year": years,
        "years_positive_frac": sum(v > 0 for v in year_net) / len(year_net),
        "drop_best_year_net_R": statistics.fmean(rest) if rest else None,
        "median_risk_usd": statistics.median(r["risk_usd"] for _, r in rows),
        "risk_vs_prop_mll": float(mll_usd) / statistics.fmean(loss_usd) if loss_usd else None,
    })
    return out
