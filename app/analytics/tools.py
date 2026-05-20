from __future__ import annotations

from collections import defaultdict

from app.analytics.loader import (
    load_all_trades,
    load_backtest_summaries,
    load_config,
    load_log_events,
)


def get_performance_summary() -> dict:
    trades = load_all_trades()
    exits = [t for t in trades if t["type"] == "EXIT" and t["realized_pnl"] is not None]
    if not exits:
        return {"error": "no closed trades found"}
    winners = [t for t in exits if t["realized_pnl"] > 0]
    losers  = [t for t in exits if t["realized_pnl"] < 0]
    net_pnl      = sum(t["realized_pnl"] for t in exits)
    gross_profit = sum(t["realized_pnl"] for t in winners)
    gross_loss   = sum(t["realized_pnl"] for t in losers)
    win_rate      = len(winners) / len(exits)
    profit_factor = abs(gross_profit / gross_loss) if gross_loss else None
    # Running max drawdown from a $50k starting balance
    equity = 50_000.0
    peak   = equity
    max_dd = 0.0
    for t in sorted(exits, key=lambda x: x["ts"]):
        equity += t["realized_pnl"]
        peak    = max(peak, equity)
        max_dd  = min(max_dd, equity - peak)
    return {
        "total_trades": len(exits),
        "winners":      len(winners),
        "losers":       len(losers),
        "win_rate":     round(win_rate, 4),
        "net_pnl":      round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss":   round(gross_loss, 2),
        "profit_factor": round(profit_factor, 3) if profit_factor else None,
        "expectancy":   round(net_pnl / len(exits), 2),
        "avg_winner":   round(gross_profit / len(winners), 2) if winners else 0.0,
        "avg_loser":    round(gross_loss   / len(losers),  2) if losers  else 0.0,
        "max_drawdown": round(max_dd, 2),
    }


def get_killzone_breakdown() -> dict:
    trades = load_all_trades()
    exits  = [t for t in trades if t["type"] == "EXIT" and t["realized_pnl"] is not None]
    kz_map: dict[str, list[float]] = defaultdict(list)
    for t in exits:
        kz_map[t["killzone"] or "unknown"].append(t["realized_pnl"])
    result = {}
    for kz, pnls in sorted(kz_map.items()):
        winners = [p for p in pnls if p > 0]
        result[kz] = {
            "trades":   len(pnls),
            "win_rate": round(len(winners) / len(pnls), 4),
            "net_pnl":  round(sum(pnls), 2),
            "avg_pnl":  round(sum(pnls) / len(pnls), 2),
        }
    return result


def get_recent_trades(limit: int = 50) -> list[dict]:
    trades = load_all_trades()
    exits  = [t for t in trades if t["type"] == "EXIT"]
    return sorted(exits, key=lambda x: x["ts"], reverse=True)[:limit]


def get_backtest_runs(sort_by: str = "profit_factor", limit: int = 10) -> list[dict]:
    summaries = load_backtest_summaries()
    valid = [s for s in summaries if s.get(sort_by) is not None]
    valid.sort(key=lambda x: x.get(sort_by) or 0, reverse=True)
    return valid[:limit]


def get_current_config() -> dict:
    return load_config()


def get_log_summary(days: int = 2) -> dict:
    events = load_log_events(days=days)
    msg = lambda e: e.get("message", "").lower()
    vp      = [e for e in events if "vp_filter" in msg(e) or "value area" in msg(e)]
    denied  = [e for e in events if "denied" in msg(e) or "max_contracts" in msg(e) or "locked" in msg(e)]
    errors  = [e for e in events if e.get("level") == "ERROR"]
    warnings = [e for e in events if e.get("level") == "WARNING"]
    return {
        "total_log_lines": len(events),
        "vp_rejections":   len(vp),
        "vp_examples":     [e["message"] for e in vp[:3]],
        "signal_denials":  len(denied),
        "denial_examples": [e["message"] for e in denied[:3]],
        "errors":          len(errors),
        "error_examples":  [e["message"] for e in errors[:3]],
        "warnings":        len(warnings),
    }
