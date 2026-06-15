"""Risk governor — phase-aware hard gates (no AI overrides, Rule 5).

Encodes the funded-pipeline spec:
a) MLL cushion: <$1k -> half size; <$500 -> block; worst-case single-trade
   loss <= 40% of cushion.
b) Stop-at-target (combine): a pass given back is the most expensive outcome.
c) Best-day cap (combine): one monster day delays the pass via consistency.
d) Winning-day tighten (xfa): >= 2x threshold -> A-grades only.
e) Post-payout half risk (xfa).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.risk.account_phase import CombineRules, PhaseTracker, XfaRules
from app.risk.config import fifty_k_combine
from app.risk.pretrade import Allow, Deny, ProposedOrder, check
from app.risk.state import RiskState

D = Decimal
TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


def order(size=2, entry="2400", stop="2395", grade="B"):
    # MGC: $10/point => stop distance 5 pts = $50/contract
    return ProposedOrder(instrument="MGC", side="long", size=size,
                         entry=D(entry), stop=D(stop), target=D("2410"),
                         setup_grade=grade)


def state():
    return RiskState(config=fifty_k_combine())


def combine(profit="0", best_day="0"):
    t = PhaseTracker(phase="combine", combine=CombineRules(), xfa=XfaRules())
    t.on_pnl(D(profit), TS)
    t.best_day = D(best_day)
    return t


def test_no_phase_means_no_new_gates():
    assert isinstance(check(order(), state()), Allow)


def test_cushion_below_500_blocks_entries():
    t = combine(profit="-1501")            # cushion 499
    d = check(order(), state(), phase=t, ts=TS)
    assert isinstance(d, Deny) and d.reason_code == "MLL_CUSHION"


def test_worst_case_loss_capped_at_40pct_of_cushion():
    t = combine(profit="-1000")            # cushion 1000 -> half-risk zone (<= $1k)
    # 40% of 1000 = $400; $50/contract -> max 8; half-risk halves to 4;
    # requested 2 still fits.
    a = check(order(size=2), state(), phase=t, ts=TS)
    assert isinstance(a, Allow) and a.allowed_size == 2
    # requested 20 gets cut to 4 (8 by 40%-cap, halved by cushion zone)
    a2 = check(order(size=20), state(), phase=t, ts=TS)
    assert isinstance(a2, Allow) and a2.allowed_size == 4


def test_stop_at_target_blocks_entries():
    # Need profit >= 3000 AND best_day_live < profit/2.
    # Three-day setup: two completed days (1000 each) + today (1100).
    # profit=3100, best_day=1000, today=1100, best_day_live=1100 < 1550. ✓
    t = combine()
    t.on_pnl(D("1000"), TS); t.roll_day(TS)
    t.on_pnl(D("1000"), TS); t.roll_day(TS)
    t.on_pnl(D("1100"), TS)
    assert t.target_reached()
    d = check(order(), state(), phase=t, ts=TS)
    assert isinstance(d, Deny) and d.reason_code == "TARGET_REACHED"


def test_best_day_cap_stops_the_day():
    t = combine()
    t.on_pnl(D("1500"), TS)                # today 1500, total 1500 -> ratio 1.0
    d = check(order(), state(), phase=t, ts=TS)
    assert isinstance(d, Deny) and d.reason_code == "BEST_DAY_CAP"


def test_xfa_tighten_after_2x_threshold_blocks_b_grades():
    t = PhaseTracker(phase="xfa", combine=CombineRules(), xfa=XfaRules())
    t.on_pnl(D("2500"), TS); t.roll_day(TS)   # lock MLL at 0, cushion ok
    t.on_pnl(D("300"), TS)                    # today >= 2 x 150
    d = check(order(grade="B"), state(), phase=t, ts=TS)
    assert isinstance(d, Deny) and d.reason_code == "WINNING_DAY_LOCK"
    a = check(order(grade="A"), state(), phase=t, ts=TS)
    assert isinstance(a, Allow)


def test_exits_never_blocked_by_governor():
    """Flatten/exit orders must always pass — you must always be allowed
    to close a position, even at zero cushion."""
    t = combine(profit="-1900")            # cushion 100
    ex = ProposedOrder(instrument="MGC", side="short", size=2,
                       entry=D("2400"), stop=D("2405"), target=D("2390"),
                       is_entry=False)
    assert isinstance(check(ex, state(), phase=t, ts=TS), Allow)


class TestBoundaryPins:
    """Pin the governor constants so silent drift fails a test."""

    def test_cap_constant_is_40pct_not_45(self):
        """At cushion 2000 / $50 per contract: 0.40 -> cap 16, 0.45 -> 18.
        Guards against copying the adjacent best_day_cap_frac (0.45)."""
        t = combine()                       # fresh: cushion 2000, > $1000 zone
        a = check(order(size=20), state(), phase=t, ts=TS)
        assert isinstance(a, Allow) and a.allowed_size == 16

    def test_no_halving_above_1000_cushion(self):
        """Halving applies only at cushion <= $1000 (or post-payout)."""
        t = combine()                       # cushion 2000
        a = check(order(size=20), state(), phase=t, ts=TS)
        assert a.allowed_size == 16         # NOT 8

    def test_post_payout_half_risk_halves_size(self):
        t = PhaseTracker(phase="xfa", combine=CombineRules(), xfa=XfaRules())
        t.on_pnl(D("2500"), TS); t.roll_day(TS)   # locked at 0, cushion 2500
        t.post_payout_half_risk = True
        a = check(order(size=20, grade="A"), state(), phase=t, ts=TS)
        # cap = int(0.40*2500/50) = 20 -> halved 10
        assert isinstance(a, Allow) and a.allowed_size == 10

    def test_winning_day_lock_needs_2x_threshold(self):
        """Between 1x and 2x the winning-day threshold, B-grades still trade."""
        t = PhaseTracker(phase="xfa", combine=CombineRules(), xfa=XfaRules())
        t.on_pnl(D("2500"), TS); t.roll_day(TS)
        t.on_pnl(D("299"), TS)              # 150 <= today < 300
        a = check(order(size=2, grade="B"), state(), phase=t, ts=TS)
        assert isinstance(a, Allow)
