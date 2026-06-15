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


def daily_pnls_with_low(
    equity_curve: list[tuple[datetime, Decimal]],
) -> list[tuple[datetime, Decimal, Decimal]]:
    """Like daily_pnls_from_equity, but also tracks intraday running-minimum P&L.

    Returns [(first_ts_of_day, day_pnl, day_low_pnl)] where day_low_pnl is the
    worst cumulative P&L seen during the day (always <= 0 on days with any loss,
    may be negative even if the day closes positive due to intraday dips).

    Used by cap_daily_pnls_at_dll to model the daily loss limit (DLL).
    """
    days_pnl: dict = {}
    days_low: dict = {}
    days_running: dict = {}
    order: list = []
    prev: Decimal | None = None
    equity_curve = sorted(equity_curve, key=lambda p: p[0])
    for ts, eq in equity_curve:
        d = trading_day_ct(ts)
        if d not in days_pnl:
            days_pnl[d] = Decimal("0")
            days_low[d] = Decimal("0")
            days_running[d] = Decimal("0")
            order.append((d, ts))
        if prev is not None:
            delta = eq - prev
            days_pnl[d] += delta
            days_running[d] += delta
            if days_running[d] < days_low[d]:
                days_low[d] = days_running[d]
        prev = eq
    return [(ts, days_pnl[d], days_low[d]) for d, ts in order]


def cap_daily_pnls_at_dll(
    daily_with_low: list[tuple[datetime, Decimal, Decimal]],
    dll_amount: Decimal,
) -> list[tuple[datetime, Decimal]]:
    """Apply daily loss limit cap to a series produced by daily_pnls_with_low.

    When a day's intraday running-low P&L drops below -dll_amount, the bot
    would have stopped trading at the DLL threshold — subsequent fills don't
    occur. Effective day P&L is capped at -dll_amount.

    Days where the EOD P&L is worse than -dll_amount are also capped (DLL
    prevents trading after the threshold, limiting further loss). Days where
    the intraday low never reached -dll_amount are returned unchanged.

    Args:
        daily_with_low: output of daily_pnls_with_low
        dll_amount: daily loss threshold in $, positive (e.g. Decimal("500"))

    Returns [(ts, effective_pnl)] — same format as daily_pnls_from_equity.
    """
    if dll_amount <= 0:
        return [(ts, pnl) for ts, pnl, _ in daily_with_low]
    neg_dll = -dll_amount
    result = []
    for ts, day_pnl, day_low in daily_with_low:
        if day_low <= neg_dll:
            # DLL was breached intraday; cap effective P&L at -dll_amount
            result.append((ts, neg_dll))
        else:
            result.append((ts, day_pnl))
    return result


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


def _pct(samples: list[float], p: int) -> float:
    """Linear-interpolation percentile; p in 0–100."""
    if not samples:
        return 0.0
    s = sorted(samples)
    n = len(s)
    pos = p * (n - 1) / 100.0
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    return s[lo] + (pos - lo) * (s[hi] - s[lo])


def bootstrap_pipeline(
    daily_pnl: list[tuple[datetime, Decimal]],
    n_resamples: int = 1000,
    block_len: int = 20,
    seed: int = 42,
    haircut: Decimal = Decimal("0"),
    combine_gap_days: int = 0,
) -> dict:
    """Block-bootstrap CIs for funded-pipeline headline metrics.

    Resamples the daily P&L sequence in blocks of `block_len` consecutive
    trading days (preserving intramonth clustering) and runs the full
    simulate_combines + simulate_xfa_chain on each resample.

    Returns 5/25/50/75/95 percentile CIs for:
      xfa_net       — total net payouts ($)
      xfa_busts     — total funded-account busts
      combine_passes — total Combine passes

    Interpretation: two configs' 90% CIs (p5–p95) on xfa_net must NOT OVERLAP
    to call one a clear winner over the other. Overlapping CIs indicate the
    observed difference is within sampling noise.
    """
    import random as _rnd

    n = len(daily_pnl)
    if n < block_len:
        raise ValueError(f"Series too short ({n} days) for block_len={block_len}")

    timestamps = [ts for ts, _ in daily_pnl]
    pnl_vals = [p for _, p in daily_pnl]
    n_blocks = n // block_len  # number of complete blocks

    rng = _rnd.Random(seed)

    xfa_net: list[float] = []
    xfa_busts_s: list[float] = []
    combine_passes_s: list[float] = []

    for _ in range(n_resamples):
        # Draw blocks with replacement until we have enough source indices.
        src: list[int] = []
        while len(src) < n:
            b = rng.randrange(n_blocks)
            src.extend(range(b * block_len, b * block_len + block_len))
        src = src[:n]

        # Original timestamps, resampled P&L values — preserves calendar shape.
        resampled = [(timestamps[i], pnl_vals[src[i]]) for i in range(n)]

        c = simulate_combines(resampled, haircut=haircut)
        x = simulate_xfa_chain(
            resampled, haircut=haircut, combine_gap_days=combine_gap_days
        )
        xfa_net.append(float(x["net_payouts"]))
        xfa_busts_s.append(float(x["busts"]))
        combine_passes_s.append(float(c["passes"]))

    pct_keys = [5, 25, 50, 75, 95]

    def ci(samples: list[float]) -> dict:
        return {p: round(_pct(samples, p), 1) for p in pct_keys}

    return {
        "xfa_net": ci(xfa_net),
        "xfa_busts": ci(xfa_busts_s),
        "combine_passes": ci(combine_passes_s),
        "n_resamples": n_resamples,
        "block_len": block_len,
        "series_len": n,
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
