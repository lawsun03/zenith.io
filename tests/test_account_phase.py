"""PhaseTracker — Topstep Combine/XFA rule state machine.

Why each test exists:
- MLL ratchet semantics decide whether the account lives or dies; EOD vs
  intraday is a config switch because Topstep's docs are ambiguous (2026).
- XFA's $0 lock at +$2k is the most valuable milestone in the pipeline.
- Winning-day accounting gates payouts; off-by-$1 errors cost real money.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.risk.account_phase import CombineRules, PhaseTracker, XfaRules

D = Decimal
T0 = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)  # Monday 09:00 CT


def day(n: int) -> datetime:
    return T0 + timedelta(days=n)


def combine_tracker(trailing: str = "intraday") -> PhaseTracker:
    return PhaseTracker(phase="combine",
                        combine=CombineRules(mll_trailing=trailing),
                        xfa=XfaRules())


def xfa_tracker() -> PhaseTracker:
    return PhaseTracker(phase="xfa", combine=CombineRules(), xfa=XfaRules())


class TestCombineMLL:
    def test_initial_mll(self):
        t = combine_tracker()
        assert t.mll == D("48000")

    def test_intraday_trailing_ratchets_immediately(self):
        t = combine_tracker("intraday")
        t.on_pnl(D("500"), day(0))
        assert t.mll == D("48500")

    def test_eod_trailing_ratchets_only_on_roll(self):
        t = combine_tracker("eod")
        t.on_pnl(D("500"), day(0))
        assert t.mll == D("48000")          # not yet
        t.roll_day(day(1))
        assert t.mll == D("48500")          # ratcheted at EOD

    def test_mll_caps_at_starting_balance(self):
        t = combine_tracker("intraday")
        t.on_pnl(D("2500"), day(0))
        assert t.mll == D("50000")

    def test_dead_when_balance_touches_mll(self):
        t = combine_tracker("intraday")
        t.on_pnl(D("-2000"), day(0))
        assert t.is_dead()


class TestCombinePass:
    def test_target_with_consistency_ok(self):
        t = combine_tracker()
        t.on_pnl(D("1400"), day(0)); t.roll_day(day(1))
        t.on_pnl(D("1400"), day(1)); t.roll_day(day(2))
        t.on_pnl(D("400"), day(2))
        assert t.target_reached()           # 3200 total, best day 1400 < 50%

    def test_big_day_delays_not_fails(self):
        t = combine_tracker()
        t.on_pnl(D("2000"), day(0)); t.roll_day(day(1))
        t.on_pnl(D("1100"), day(1))
        assert not t.target_reached()       # 3100 total, best 2000 >= 50%
        t.roll_day(day(2))
        t.on_pnl(D("1000"), day(2))
        assert t.target_reached()           # 4100 total, best 2000 < 50%


class TestXFA:
    def test_winning_day_threshold_exact(self):
        t = xfa_tracker()
        t.on_pnl(D("149"), day(0)); t.roll_day(day(1))
        assert t.winning_days == 0          # +$149 is NOT a winning day
        t.on_pnl(D("150"), day(1)); t.roll_day(day(2))
        assert t.winning_days == 1          # +$150 IS

    def test_mll_locks_at_zero_once_2k_reached(self):
        t = xfa_tracker()
        assert t.mll == D("-2000")
        t.on_pnl(D("2000"), day(0)); t.roll_day(day(1))
        assert t.mll == D("0")
        t.on_pnl(D("3000"), day(1)); t.roll_day(day(2))
        assert t.mll == D("0")              # locked — never trails above 0

    def test_payout_resets_winning_days_and_halves_balance(self):
        t = xfa_tracker()
        for n in range(5):
            t.on_pnl(D("700"), day(n)); t.roll_day(day(n + 1))
        assert t.winning_days == 5 and t.balance == D("3500")
        amount = t.request_payout()
        assert amount == D("1750")          # 50% of balance, under cap
        assert t.balance == D("1750")
        assert t.winning_days == 0          # counter resets each cycle
