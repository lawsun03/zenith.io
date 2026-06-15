"""
Rules-accurate funded-pipeline simulator.

Input: a daily P&L series derived from a backtest equity curve. Replays it
through PhaseTracker (the SAME module the live governor uses) to produce
pass/bust/payout numbers instead of period-total P&L.

Caveats (document in every report):
- Daily granularity: intraday MLL touches between fills are invisible;
  busts are UNDERSTATED. Configure an adverse-excursion haircut once the
  intrabar recorder exists.
- Replaying the same P&L sequence across attempts assumes sizing doesn't
  change with balance — true for fixed-contract runs, approximate otherwise.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from statistics import median

from app.backtest.risk_policy import combine_ramp_multiplier, funded_survival_multiplier
from app.risk.account_phase import CombineRules, PhaseTracker, XfaRules
from app.risk.flatten import trading_day_ct


def daily_pnls_from_equity(
    equity_curve: list[tuple[datetime, Decimal]],
) -> list[tuple[datetime, Decimal]]:
    """Collapse a per-fill equity curve into per-trading-day P&L deltas.

    Returns [(first_ts_of_day, day_pnl)] in chronological order.
    The first equity point is treated as baseline (level, not delta).
    """
    days: dict = {}
    order: list = []
    prev: Decimal | None = None
    # Day attribution silently corrupts on unsorted input — enforce the
    # chronological-order precondition rather than trust callers.
    equity_curve = sorted(equity_curve, key=lambda p: p[0])
    for ts, eq in equity_curve:
        d = trading_day_ct(ts)
        if d not in days:
            days[d] = Decimal("0")
            order.append((d, ts))
        if prev is not None:
            days[d] += eq - prev
        prev = eq
    return [(ts, days[d]) for d, ts in order]


def _dead_with_haircut(tracker: PhaseTracker, haircut: Decimal) -> bool:
    """is_dead() with an assumed intraday adverse excursion of `haircut`
    below the day's closing balance — a conservative proxy until the
    intrabar recorder can measure real MLL touches."""
    m = tracker.mll
    return m is not None and tracker.balance - haircut <= m


def simulate_combines(
    daily_pnl: list[tuple[datetime, Decimal]],
    rules: CombineRules | None = None,
    haircut: Decimal = Decimal("0"),
    risk_policy: str = "constant",
    base_risk_pct: Decimal = Decimal("1.0"),
) -> dict:
    """Sequential Combine attempts over the series. Bust -> new attempt next day.

    Modeling choices:
    - A PASS also restarts a fresh attempt the next day. This measures
      pass-rate DENSITY across the whole series (how often the strategy can
      clear a Combine), not a single account lifecycle — Combine->XFA
      progression is modeled separately by simulate_xfa_chain.
    - Bust wins over pass when one daily delta implies both.
    """
    rules = rules or CombineRules()
    attempts = passes = busts = 0
    days_to_pass: list[int] = []
    tracker: PhaseTracker | None = None
    days_in_attempt = 0
    for ts, pnl in daily_pnl:
        if tracker is None:
            tracker = PhaseTracker(phase="combine", combine=rules, xfa=XfaRules())
            attempts += 1
            days_in_attempt = 0
        if risk_policy == "combine_ramp":
            mult = combine_ramp_multiplier(tracker.balance, tracker.mll, base_risk_pct)
        else:
            mult = Decimal("1")
        tracker.on_pnl(pnl * mult, ts)
        days_in_attempt += 1
        # Daily granularity: this only sees the day's CLOSING balance — an
        # intraday MLL touch that recovered by close is invisible (busts
        # understated, per module caveat; `haircut` partially compensates).
        if _dead_with_haircut(tracker, haircut * mult):
            busts += 1
            tracker = None
            continue
        tracker.roll_day(ts)
        if tracker.target_reached():
            passes += 1
            days_to_pass.append(days_in_attempt)
            tracker = None
    return {
        "attempts": attempts, "passes": passes, "busts": busts,
        "median_days_to_pass": (median(days_to_pass) if days_to_pass else None),
    }


def simulate_xfa_chain(
    daily_pnl: list[tuple[datetime, Decimal]],
    rules: XfaRules | None = None,
    haircut: Decimal = Decimal("0"),
    risk_policy: str = "constant",
    base_risk_pct: Decimal = Decimal("0.75"),
    combine_gap_days: int = 0,
) -> dict:
    """Sequential XFA accounts: bust -> next account starts the following day.

    combine_gap_days: trading days to skip after each bust before starting the
    next account. Models the real combine-gap (re-running Phase A before a new
    funded account can start). Default 0 preserves the existing behavior.
    """
    rules = rules or XfaRules()
    accounts = busts = 0
    gross_payouts = Decimal("0")
    first_payout_days: list[int] = []
    tracker: PhaseTracker | None = None
    days_in_account = 0
    had_payout = False
    gap_remaining = 0
    for ts, pnl in daily_pnl:
        if gap_remaining > 0:
            gap_remaining -= 1
            continue
        if tracker is None:
            tracker = PhaseTracker(phase="xfa", combine=CombineRules(), xfa=rules)
            accounts += 1
            days_in_account = 0
            had_payout = False
        if risk_policy == "funded_survival":
            mult = funded_survival_multiplier(tracker.balance, tracker.mll, base_risk_pct)
        else:
            mult = Decimal("1")
        tracker.on_pnl(pnl * mult, ts)
        days_in_account += 1
        if _dead_with_haircut(tracker, haircut * mult):
            busts += 1
            tracker = None
            gap_remaining = combine_gap_days
            continue
        tracker.roll_day(ts)
        if tracker.payout_eligible():
            gross_payouts += tracker.request_payout()
            if not had_payout:
                first_payout_days.append(days_in_account)
                had_payout = True
    return {
        "accounts": accounts, "busts": busts,
        "gross_payouts": gross_payouts,
        "net_payouts": gross_payouts * rules.trader_profit_share,
        "median_days_to_first_payout": (median(first_payout_days) if first_payout_days else None),
    }


def format_pipeline_summary(
    equity_curve: list[tuple[datetime, Decimal]],
    haircut: Decimal = Decimal("0"),
) -> str:
    daily = daily_pnls_from_equity(equity_curve)
    c = simulate_combines(daily, haircut=haircut)
    x = simulate_xfa_chain(daily, haircut=haircut)
    haircut_note = f", haircut ${haircut:.0f}" if haircut else ""
    return (
        f"COMBINE: attempts {c['attempts']} | passes {c['passes']} | "
        f"busts {c['busts']} | median days-to-pass {c['median_days_to_pass']}\n"
        f"XFA:     accounts {x['accounts']} | busts {x['busts']} | "
        f"payouts ${x['gross_payouts']:.0f} gross / ${x['net_payouts']:.0f} net (90%)\n"
        f"(daily granularity — intraday MLL touches understated{haircut_note})"
    )
