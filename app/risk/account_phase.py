"""
Account-phase state machine: Topstep Combine / XFA rule math.

Single source of truth — the live governor (pretrade) and the backtest
funded simulator both import THIS module. Rule numbers come from config
(bot_config.json "phase_rules"), never hardcoded in logic: Topstep changed
rules 8 times between Nov 2025 and Apr 2026.

Granularity caveat: callers feed realized P&L deltas (fills). Open-trade
unrealized excursion is invisible, so MLL touches are UNDERSTATED — pair
sizing decisions with the pretrade cushion governor, which bounds the
worst-case open-trade loss.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

log = logging.getLogger(__name__)

PhaseName = Literal["practice", "combine", "xfa", "live"]


@dataclass(frozen=True)
class CombineRules:
    starting_balance: Decimal = Decimal("50000")
    profit_target: Decimal = Decimal("3000")
    mll_distance: Decimal = Decimal("2000")
    mll_trailing: str = "intraday"          # "eod" | "intraday" (intraday = conservative)
    best_day_cap_frac: Decimal = Decimal("0.45")
    stop_at_target: bool = True


@dataclass(frozen=True)
class XfaRules:
    starting_balance: Decimal = Decimal("0")
    mll_distance: Decimal = Decimal("2000")
    mll_lock_at: Decimal = Decimal("2000")  # balance level where MLL locks at $0
    winning_day_threshold: Decimal = Decimal("150")
    payout_path: str = "standard"           # "standard" | "consistency"
    payout_winning_days: int = 5
    payout_request_floor: Decimal = Decimal("3000")
    payout_cap: Decimal = Decimal("5000")
    payout_fraction: Decimal = Decimal("0.5")
    trader_profit_share: Decimal = Decimal("0.90")  # 90/10 split (accounts after 2026-01-12)


@dataclass
class PhaseTracker:
    phase: PhaseName
    combine: CombineRules = field(default_factory=CombineRules)
    xfa: XfaRules = field(default_factory=XfaRules)

    balance: Decimal = field(init=False)
    high_water: Decimal = field(init=False)       # intraday equity high
    eod_high_water: Decimal = field(init=False)   # ratchets only at roll_day
    today_pnl: Decimal = Decimal("0")
    best_day: Decimal = Decimal("0")              # best COMPLETED day; today checked live
    winning_days: int = 0                         # current payout cycle (xfa)
    mll_locked_at_zero: bool = False              # xfa permanent lock
    post_payout_half_risk: bool = False

    def __post_init__(self) -> None:
        start = (self.combine.starting_balance if self.phase == "combine"
                 else self.xfa.starting_balance)
        self.balance = start
        self.high_water = start
        self.eod_high_water = start

    # -- properties -----------------------------------------------------
    @property
    def total_profit(self) -> Decimal:
        start = (self.combine.starting_balance if self.phase == "combine"
                 else self.xfa.starting_balance)
        return self.balance - start

    @property
    def best_day_live(self) -> Decimal:
        """Best day including today's running P&L (conservative)."""
        return max(self.best_day, self.today_pnl)

    @property
    def mll(self) -> Decimal | None:
        if self.phase == "combine":
            anchor = (self.high_water if self.combine.mll_trailing == "intraday"
                      else self.eod_high_water)
            return min(anchor - self.combine.mll_distance,
                       self.combine.starting_balance)
        if self.phase == "xfa":
            if self.mll_locked_at_zero:
                return Decimal("0")
            return self.eod_high_water - self.xfa.mll_distance  # XFA trails EOD
        return None  # practice / live: no Topstep MLL

    @property
    def cushion(self) -> Decimal | None:
        m = self.mll
        return None if m is None else self.balance - m

    # -- transitions -----------------------------------------------------
    def on_pnl(self, delta: Decimal, ts: datetime) -> None:
        """Realized P&L delta (a fill, commission-inclusive)."""
        self.balance += delta
        self.today_pnl += delta
        if self.balance > self.high_water:
            self.high_water = self.balance

    def roll_day(self, ts: datetime) -> None:
        """5:00 PM CT day roll: finalize today, ratchet EOD anchors."""
        if self.today_pnl > self.best_day:
            self.best_day = self.today_pnl
        if (self.phase == "xfa"
                and self.today_pnl >= self.xfa.winning_day_threshold):
            self.winning_days += 1
        if self.balance > self.eod_high_water:
            self.eod_high_water = self.balance
        if (self.phase == "xfa" and not self.mll_locked_at_zero
                and self.eod_high_water >= self.xfa.mll_lock_at):
            self.mll_locked_at_zero = True
            log.info("XFA MLL locked at $0 (balance reached %s)", self.eod_high_water)
        if (self.post_payout_half_risk
                and self.balance >= self.xfa.payout_request_floor):
            self.post_payout_half_risk = False
        self.today_pnl = Decimal("0")

    def is_dead(self) -> bool:
        m = self.mll
        return m is not None and self.balance <= m

    def target_reached(self) -> bool:
        """Combine pass: target hit AND best day < 50% of total profit."""
        if self.phase != "combine":
            return False
        profit = self.total_profit
        # The /2 here is TOPSTEP'S consistency rule (fixed 50%). It is
        # deliberately separate from best_day_cap_frac (0.45), which is the
        # bot's own tighter intraday throttle, read only by the governor.
        return (profit >= self.combine.profit_target
                and self.best_day_live < profit / 2)

    def payout_eligible(self) -> bool:
        return (self.phase == "xfa"
                and self.winning_days >= self.xfa.payout_winning_days
                and self.balance >= self.xfa.payout_request_floor)

    def request_payout(self) -> Decimal:
        """Withdraw payout_fraction of balance (capped); resets the cycle."""
        amount = min(self.balance * self.xfa.payout_fraction, self.xfa.payout_cap)
        self.balance -= amount
        self.winning_days = 0
        self.post_payout_half_risk = True
        log.info("XFA payout %s, balance now %s (half-risk until %s)",
                 amount, self.balance, self.xfa.payout_request_floor)
        return amount


def tracker_from_config(cfg) -> "PhaseTracker":
    """Build a PhaseTracker from BotConfig.account_phase + .phase_rules.

    Partial dicts are fine — dataclass defaults fill the gaps. Decimal
    fields accept strings (config JSON stores numbers as strings).
    Validates cross-field safety invariants (fail loud at startup, not
    mid-session).
    """
    def _conv(rules_cls, raw: dict):
        # Typo'd rule names must raise, not silently keep the default —
        # these are real-money account limits (same precedent as
        # strategy_overrides validation in bot_config.strategy_for).
        unknown = set(raw) - set(rules_cls.__dataclass_fields__)
        if unknown:
            raise ValueError(
                f"phase_rules: unknown {rules_cls.__name__} field(s): {sorted(unknown)}"
            )
        kwargs = {}
        for f in rules_cls.__dataclass_fields__.values():
            if f.name not in raw:
                continue
            v = raw[f.name]
            default = f.default
            # bool check MUST precede int — bool is a subclass of int
            if isinstance(default, bool):
                kwargs[f.name] = bool(v)
            elif isinstance(default, Decimal):
                kwargs[f.name] = Decimal(str(v))
            elif isinstance(default, int):
                kwargs[f.name] = int(v)
            else:
                kwargs[f.name] = str(v)
        return rules_cls(**kwargs)

    combine = _conv(CombineRules, dict(cfg.phase_rules.get("combine", {})))
    xfa = _conv(XfaRules, dict(cfg.phase_rules.get("xfa", {})))
    if xfa.payout_request_floor < xfa.mll_lock_at:
        raise ValueError(
            f"phase_rules.xfa.payout_request_floor ({xfa.payout_request_floor}) must be "
            f">= mll_lock_at ({xfa.mll_lock_at}): a payout before the $0 MLL lock can "
            f"leave the trailing MLL above the post-payout balance (instant account death)."
        )
    return PhaseTracker(phase=cfg.account_phase, combine=combine, xfa=xfa)
