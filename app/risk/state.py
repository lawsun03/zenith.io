"""
RiskState — the live tracker that every order is checked against.

This module is intentionally pure: no I/O, no broker calls, no time
side-effects. You feed it events (fills, mark-to-market ticks, day
boundaries), and you read its derived values (current MLL floor, buffer
to floor, daily P&L, lockouts).

Pure-function design matters because:

  1. It's the only way to property-test the trailing-DD math reliably.
  2. The reconciler can replay an event log to rebuild state if the
     process crashes mid-session.
  3. The same code runs in backtests, dry-runs, and live with no changes.

Event sources in production:
  - Mark-to-market ticks from the WebSocket feed → mark_equity()
  - Order fills from the broker → record_fill()
  - The wall clock crossing 5:00 PM CT → roll_trading_day()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from .config import Money, TopstepAccountConfig

CT = ZoneInfo("America/Chicago")


@dataclass
class LockoutReason:
    """Why is the account locked out? Carried for audit/dashboard display."""

    code: str           # short identifier for routing/logic
    message: str        # human-readable for the dashboard


@dataclass
class RiskState:
    """
    All mutable risk state for one account.

    Money invariants (asserted in tests):
      - mll_floor only ever moves up, never down (until reset for new combine)
      - mll_floor never exceeds starting_balance (Combine rule)
      - daily_pnl resets to 0 exactly when roll_trading_day() is called
      - if locked_out is set, any check() call returns Deny
    """

    config: TopstepAccountConfig

    # Realized account balance — only changes on fills.
    realized_balance: Money = field(default=Decimal("0"))

    # Highest live equity ever seen this account (realized + unrealized).
    # The trailing MLL is computed from this. Once set, only goes up.
    equity_high_water: Money = field(default=Decimal("0"))

    # Net P&L since the last 5pm CT reset.
    daily_pnl: Money = field(default=Decimal("0"))

    # Open contracts (mini-equivalent). 1 mini = 10 micros.
    open_contracts: int = 0

    # When set, no new entries permitted for this session.
    locked_out: Optional[LockoutReason] = None

    # Tracks whether the trailing MLL has hit its lock point (= starting
    # balance for a Combine). Once locked, it stops trailing.
    mll_locked_at_starting_balance: bool = False

    # Used to detect day rollovers from outside.
    last_event_ts: Optional[datetime] = None

    def __post_init__(self) -> None:
        if self.realized_balance == 0:
            self.realized_balance = self.config.starting_balance
        if self.equity_high_water == 0:
            self.equity_high_water = self.config.starting_balance

    # ------------------------------------------------------------------
    # Derived values — computed, never stored, so they can't go stale.
    # ------------------------------------------------------------------

    @property
    def mll_floor(self) -> Money:
        """
        Trailing Maximum Loss Limit floor.

        Combine rule: floor = max(equity_high_water - mll_initial_offset,
                                  starting_balance - mll_initial_offset)
        but capped at starting_balance once high-water reaches
        starting_balance + mll_initial_offset.

        Worked example on $50K Combine ($2K MLL):
          - Start: HWM=$50,000 → floor=$48,000
          - HWM rises to $51,000 → floor=$49,000
          - HWM rises to $52,000 → floor=$50,000 (LOCKED — won't move further)
          - HWM rises to $55,000 → floor=$50,000 (still locked)
        """
        starting = self.config.starting_balance
        offset = self.config.mll_initial_offset

        trailing_floor = self.equity_high_water - offset

        # Lock at starting balance once trailing would push past it.
        if trailing_floor >= starting:
            return starting
        return trailing_floor

    @property
    def buffer_to_mll(self) -> Money:
        """
        How much equity room until the MLL floor.

        This is THE number to watch in the dashboard. Below soft_buffer
        the bot should refuse new entries even if the official limit
        hasn't been hit.
        """
        return self.current_equity - self.mll_floor

    @property
    def current_equity(self) -> Money:
        """
        Realized balance + unrealized open-position P&L.

        Stored as equity_high_water when this is the new max.
        """
        # In this design, realized_balance is updated on every fill, and
        # mark_equity() pushes the high water up when ticks move favorably.
        # So current_equity at any point is realized_balance + delta-since-last-mark,
        # but we track it implicitly through mark_equity() callers passing
        # the live equity in. See mark_equity() below.
        return self._current_equity

    _current_equity: Money = field(default=Decimal("0"), init=False, repr=False)

    @property
    def buffer_to_dll(self) -> Money:
        """How much loss room until the daily loss limit triggers."""
        # daily_pnl is signed: -800 means down $800 today.
        # DLL trips when daily_pnl <= -daily_loss_limit.
        return self.config.daily_loss_limit + self.daily_pnl

    # ------------------------------------------------------------------
    # State transitions — the only ways state should change.
    # ------------------------------------------------------------------

    def mark_equity(self, equity: Money, ts: datetime) -> None:
        """
        Update live equity from a mark-to-market tick.

        Pushes high-water up if appropriate. Does NOT change realized
        balance — that only changes on fills.
        """
        self._current_equity = equity
        if equity > self.equity_high_water:
            self.equity_high_water = equity
            # Check if we just crossed the lock point.
            if self.equity_high_water >= (
                self.config.starting_balance + self.config.mll_initial_offset
            ):
                self.mll_locked_at_starting_balance = True
        self.last_event_ts = ts
        self._check_auto_lockouts()

    def record_fill(
        self,
        realized_pnl_delta: Money,
        contracts_delta: int,
        ts: datetime,
    ) -> None:
        """
        Apply a fill: update realized balance, daily P&L, position count.

        contracts_delta is signed: +1 opens 1 contract, -1 closes 1.
        For a flatten of 3 longs, pass contracts_delta=-3.
        """
        self.realized_balance += realized_pnl_delta
        self.daily_pnl += realized_pnl_delta
        self.open_contracts += contracts_delta

        # When you close a winning trade, current_equity may drop because
        # the unrealized profit just became realized — but realized_balance
        # absorbs it 1:1, so equity is unchanged. Caller is responsible
        # for marking equity correctly post-fill.
        self.last_event_ts = ts
        self._check_auto_lockouts()

    def reset(self) -> None:
        """Reset all mutable state to starting values (for paper backtest reruns)."""
        self.realized_balance = self.config.starting_balance
        self.equity_high_water = self.config.starting_balance
        self._current_equity = self.config.starting_balance
        self.daily_pnl = Decimal("0")
        self.open_contracts = 0
        self.locked_out = None
        self.mll_locked_at_starting_balance = False
        self.last_event_ts = None

    def roll_trading_day(self, ts: datetime) -> None:
        """
        Reset daily counters at 5:00 PM CT.

        Lockouts that were caused by daily-only conditions (DLL hit) clear.
        Lockouts caused by MLL breach do NOT clear — that's a Combine
        failure and requires a reset/new account.
        """
        self.daily_pnl = Decimal("0")
        if self.locked_out and self.locked_out.code in {
            "DLL_HIT",
            "DLL_SOFT_BUFFER",
        }:
            self.locked_out = None
        self.last_event_ts = ts

    # ------------------------------------------------------------------
    # Internal: auto-lockouts triggered when state crosses a threshold.
    # ------------------------------------------------------------------

    def _check_auto_lockouts(self) -> None:
        """Check all lockout conditions in priority order."""
        if self.locked_out is not None:
            return  # Already locked; don't overwrite the original reason.

        # Hard MLL breach — Combine fails. This is permanent for the account.
        if self._current_equity > 0 and self._current_equity <= self.mll_floor:
            self.locked_out = LockoutReason(
                code="MLL_BREACH",
                message=(
                    f"Maximum Loss Limit hit. Equity {self._current_equity} "
                    f"≤ floor {self.mll_floor}. Combine failed."
                ),
            )
            return

        # Soft MLL buffer — bot self-stops before the official line.
        if (
            self._current_equity > 0
            and self.buffer_to_mll <= self.config.soft_buffer
        ):
            self.locked_out = LockoutReason(
                code="MLL_SOFT_BUFFER",
                message=(
                    f"Soft buffer hit: {self.buffer_to_mll} remaining to MLL. "
                    f"Threshold {self.config.soft_buffer}."
                ),
            )
            return

        # Daily loss limit hit.
        if self.daily_pnl <= -self.config.daily_loss_limit:
            self.locked_out = LockoutReason(
                code="DLL_HIT",
                message=f"Daily loss limit hit: {self.daily_pnl}.",
            )
            return

        # Soft DLL buffer.
        if self.buffer_to_dll <= self.config.soft_buffer:
            self.locked_out = LockoutReason(
                code="DLL_SOFT_BUFFER",
                message=(
                    f"Soft DLL buffer hit: {self.buffer_to_dll} room remaining."
                ),
            )
            return


def is_in_trading_day(ts: datetime, config: TopstepAccountConfig) -> bool:
    """
    Is this timestamp inside the Topstep trading day window?

    Trading day: 5:00 PM CT to 3:10 PM CT the next calendar day.
    Outside this window, the daily counters should not be updated and
    no orders should fire.
    """
    ct = ts.astimezone(CT)
    t = ct.time()
    open_t = time(config.trading_day_reset_hour_ct, 0)
    close_t = time(
        config.trading_day_close_hour_ct,
        config.trading_day_close_minute_ct,
    )
    # The window crosses midnight: open is 17:00, close is 15:10 next day.
    # So we're "in" if it's after 17:00 OR before 15:10.
    return t >= open_t or t < close_t
