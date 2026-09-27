"""Per-trade prop-combine simulation from prop_rules/*.yaml (spec §6 prop_sim).

A new combine starts on the first day of every month in the trade span and
runs until it passes, fails, or the data ends; combines overlap. Each trade
risks `risk_per_trade_usd`, floored to whole contracts at the trade's own
per-contract risk, and pays contracts × net_R × risk_usd.

Rule math (trailing max loss, profit target, consistency) goes through
PhaseTracker — the same module the live governor uses — so the YAML only
supplies numbers. The daily loss limit is handled here because PhaseTracker
has none for the combine.

Granularity: P&L lands at each trade's exit. Open-trade excursion is
invisible, so max-loss and DLL touches are understated. The daily-bar
version is funded_sim.simulate_combines.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from statistics import median

import yaml

from app.risk.account_phase import CombineRules, PhaseTracker
from app.risk.flatten import trading_day_ct

log = logging.getLogger(__name__)

# Checked-in data, so anchored to the repo root rather than the caller's cwd.
PROP_RULES_DIR = Path(__file__).resolve().parents[2] / "prop_rules"
DEFAULT_ACCOUNT = "topstep_50k"
DEFAULT_RISK_PER_TRADE_USD = Decimal("200")
DLL_ACTIONS = ("stop_day", "fail")


@dataclass(frozen=True)
class PropRules:
    account: str
    last_checked: str | None
    combine: CombineRules
    daily_loss_limit: Decimal | None
    daily_loss_limit_action: str


def load_prop_rules(account: str = DEFAULT_ACCOUNT,
                    rules_dir: Path = PROP_RULES_DIR) -> PropRules:
    path = rules_dir / f"{account}.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    # account_phase.target_reached hardcodes Topstep's 50% consistency rule;
    # a different fraction in the YAML would be silently ignored.
    if Decimal(str(raw["consistency_best_day_frac"])) != Decimal("0.5"):
        raise ValueError(f"{path}: consistency_best_day_frac must be 0.5 "
                         "(fixed in app/risk/account_phase.py target_reached)")
    action = raw.get("daily_loss_limit_action", "stop_day")
    if action not in DLL_ACTIONS:
        raise ValueError(f"{path}: daily_loss_limit_action {action!r} not in {DLL_ACTIONS}")
    dll = raw.get("daily_loss_limit")
    checked = raw.get("last_checked")
    return PropRules(
        account=raw["account"],
        last_checked=str(checked) if checked else None,
        combine=CombineRules(
            starting_balance=Decimal(str(raw["starting_balance"])),
            profit_target=Decimal(str(raw["profit_target"])),
            mll_distance=Decimal(str(raw["max_loss"])),
            mll_trailing=str(raw["max_loss_trailing"]),
        ),
        daily_loss_limit=Decimal(str(dll)) if dll is not None else None,
        daily_loss_limit_action=action,
    )


def _run_combine(rows: list[tuple[date, datetime, Decimal]], rules: PropRules) -> tuple[str, int]:
    t = PhaseTracker(phase="combine", combine=rules.combine)
    dll = rules.daily_loss_limit
    day: date | None = None
    trade_days = 0
    halted = False
    for d, ts, pnl in rows:
        if d != day:
            if day is not None:
                t.roll_day(ts)
                if t.target_reached():
                    return "passed", trade_days
            day, halted = d, False
            trade_days += 1
        if halted:
            continue
        t.on_pnl(pnl, ts)
        if t.is_dead():
            return "failed_max_loss", trade_days
        if dll is not None and t.today_pnl <= -dll:
            if rules.daily_loss_limit_action == "fail":
                return "failed_daily_loss", trade_days
            halted = True
    if day is not None:
        t.roll_day(rows[-1][1])
        if t.target_reached():
            return "passed", trade_days
    if t.total_profit >= rules.combine.profit_target:
        return "unresolved_consistency_blocked", trade_days
    return "unresolved", trade_days


def prop_sim(trades: list[dict], rules: PropRules,
             risk_per_trade_usd: Decimal = DEFAULT_RISK_PER_TRADE_USD) -> dict:
    """`trades` carry exit_ts, net_R and risk_usd (per contract), as written
    by metrics.research_metrics."""
    rows: list[tuple[date, datetime, Decimal]] = []
    skipped = 0
    for tr in trades:
        if "net_R" not in tr:  # no stop distance; research_metrics already logged it
            continue
        risk = Decimal(str(tr["risk_usd"]))
        contracts = (risk_per_trade_usd / risk).to_integral_value(ROUND_FLOOR)
        if contracts < 1:
            skipped += 1
            continue
        ts = datetime.fromisoformat(tr["exit_ts"])
        rows.append((trading_day_ct(ts), ts, contracts * Decimal(str(tr["net_R"])) * risk))
    rows.sort(key=lambda r: r[1])

    out: dict = {
        "account": rules.account,
        "rules_last_checked": rules.last_checked,
        "risk_per_trade_usd": float(risk_per_trade_usd),
        "trades_skipped_risk_too_wide": skipped,
        "caveat": "trade-exit granularity: open-trade excursion invisible, "
                  "max-loss and DLL touches understated",
    }
    if rules.last_checked is None:
        out["warning"] = f"prop_rules/{rules.account}.yaml has never been checked against the firm"
    if skipped:
        log.warning("prop_sim: %d trade(s) need more than $%s risk for 1 contract; skipped",
                    skipped, risk_per_trade_usd)

    counts = {k: 0 for k in ("passed", "failed_max_loss", "failed_daily_loss",
                             "unresolved", "unresolved_consistency_blocked")}
    days_to_pass: list[int] = []
    if rows:
        y, m = rows[0][0].year, rows[0][0].month
        last = rows[-1][0]
        while (y, m) <= (last.year, last.month):
            start = date(y, m, 1)
            outcome, days = _run_combine([r for r in rows if r[0] >= start], rules)
            counts[outcome] += 1
            if outcome == "passed":
                days_to_pass.append(days)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    resolved = counts["passed"] + counts["failed_max_loss"] + counts["failed_daily_loss"]
    out.update({
        "n_combines": sum(counts.values()),
        **counts,
        "pass_rate": counts["passed"] / resolved if resolved else None,
        "median_trade_days_to_pass": median(days_to_pass) if days_to_pass else None,
    })
    return out
