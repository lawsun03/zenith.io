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


class TestReviewGaps:
    """Cases the first review found unfalsifiable or unpinned."""

    def test_payout_cap_binds(self):
        """payout_cap must clamp the 50% fraction - a cap regression would
        otherwise pay out unbounded amounts."""
        t = xfa_tracker()
        t.on_pnl(D("12000"), day(0)); t.roll_day(day(1))
        for n in range(1, 5):
            t.on_pnl(D("200"), day(n)); t.roll_day(day(n + 1))
        assert t.payout_eligible()
        amount = t.request_payout()
        assert amount == D("5000")          # min(12800/2=6400, cap 5000)
        assert t.balance == D("7800")

    def test_post_payout_account_survives(self):
        """After a payout halves the balance, the $0-locked MLL must not
        insta-kill the account (floor 3000 > lock_at 2000 guarantees the
        lock fired before any payout)."""
        t = xfa_tracker()
        for n in range(5):
            t.on_pnl(D("700"), day(n)); t.roll_day(day(n + 1))
        t.request_payout()
        assert t.mll == D("0")
        assert not t.is_dead()

    def test_eod_trailing_intraday_dip_survives(self):
        """With mll_trailing=eod, an intraday dip below the would-be
        intraday floor does NOT kill - that is the entire point of the
        EOD/intraday config switch."""
        t = combine_tracker("eod")
        t.on_pnl(D("1500"), day(0)); t.roll_day(day(1))   # EOD anchor 51500 -> MLL 49500
        t.on_pnl(D("-1900"), day(1))                       # balance 49600, above 49500
        assert not t.is_dead()
        t.on_pnl(D("-150"), day(1))                        # balance 49450 <= 49500
        assert t.is_dead()

    def test_practice_phase_has_no_mll(self):
        t = PhaseTracker(phase="practice", combine=CombineRules(), xfa=XfaRules())
        assert t.mll is None and t.cushion is None
        t.on_pnl(D("-10000"), day(0))
        assert not t.is_dead()


class TestTrackerFromConfig:
    def test_phase_rules_from_bot_config(self):
        """phase_rules round-trips through BotConfig with defaults."""
        from app.bot_config import BotConfig
        from app.risk.account_phase import tracker_from_config

        cfg = BotConfig()
        assert cfg.account_phase == "practice"   # zero behavior change by default
        t = tracker_from_config(cfg)
        assert t.phase == "practice"
        cfg2 = BotConfig(account_phase="combine",
                         phase_rules={"combine": {"best_day_cap_frac": "0.40"}})
        t2 = tracker_from_config(cfg2)
        assert t2.combine.best_day_cap_frac == Decimal("0.40")
        assert t2.combine.profit_target == Decimal("3000")  # defaults survive partial dict

    def test_tracker_from_config_rejects_unsafe_payout_floor(self):
        """payout_request_floor must be >= mll_lock_at: a payout that can fire
        before the XFA's $0 lock would let the post-payout trailing MLL sit
        above the halved balance and insta-kill the account."""
        import pytest
        from app.bot_config import BotConfig
        from app.risk.account_phase import tracker_from_config

        cfg = BotConfig(account_phase="xfa",
                        phase_rules={"xfa": {"payout_request_floor": "1500"}})
        with pytest.raises(ValueError, match="payout_request_floor"):
            tracker_from_config(cfg)


def test_tracker_from_config_rejects_unknown_keys():
    """A typo'd rule name must raise, not silently keep the default -
    a misconfigured MLL/payout number is an account violation waiting."""
    import pytest
    from app.bot_config import BotConfig
    from app.risk.account_phase import tracker_from_config

    cfg = BotConfig(account_phase="combine",
                    phase_rules={"combine": {"proft_target": "4000"}})
    with pytest.raises(ValueError, match="proft_target"):
        tracker_from_config(cfg)


def test_engine_feeds_fills_to_phase_tracker():
    """Every realized fill delta must reach the tracker — the governor's
    cushion math is only as good as the balance it sees."""
    import asyncio
    from app.broker.paper import PaperBroker
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState
    from app.broker.events import Bar

    def bar(ts, price):
        return Bar(instrument="MGC", timeframe="1min", ts=ts,
                   open=D(str(price)), high=D(str(price + 1)),
                   low=D(str(price - 1)), close=D(str(price)), volume=10)

    tracker = PhaseTracker(phase="combine", combine=CombineRules(), xfa=XfaRules())
    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=D("0"))
    engine = ExecutionEngine(broker=broker, risk_state=RiskState(config=fifty_k_combine()),
                             runners=[], replay_mode=True, phase=tracker)

    async def go():
        await broker.connect()
        await engine.start()
        await broker.inject_bar(bar(T0, 100))
        await broker.place_bracket("MGC", "long", 1, D("100"), D("95"), D("110"))
        # next bar hits the target -> exit fill with positive realized pnl
        await broker.inject_bar(bar(T0 + timedelta(minutes=1), 111))

    asyncio.run(go())
    assert tracker.balance > D("50000")


def test_engine_rolls_phase_day_at_5pm_ct():
    """today_pnl must reset at the 5pm CT boundary or the best-day cap and
    winning-day counters compound across days."""
    import asyncio
    from app.broker.paper import PaperBroker
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState
    from app.broker.events import Bar

    def bar(ts, price=100):
        return Bar(instrument="MGC", timeframe="1min", ts=ts,
                   open=D(str(price)), high=D(str(price + 1)),
                   low=D(str(price - 1)), close=D(str(price)), volume=10)

    tracker = PhaseTracker(phase="combine", combine=CombineRules(), xfa=XfaRules())
    tracker.on_pnl(D("700"), T0)
    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=D("0"))
    engine = ExecutionEngine(broker=broker, risk_state=RiskState(config=fifty_k_combine()),
                             runners=[], replay_mode=True, phase=tracker)

    async def go():
        await broker.connect()
        await engine.start()
        # 2026-01-05 22:00 UTC = 16:00 CT (same trading day)
        await broker.inject_bar(bar(datetime(2026, 1, 5, 22, 0, tzinfo=timezone.utc)))
        assert tracker.today_pnl == D("700")
        # 2026-01-05 23:30 UTC = 17:30 CT -> NEXT trading day; roll fires
        await broker.inject_bar(bar(datetime(2026, 1, 5, 23, 30, tzinfo=timezone.utc)))

    asyncio.run(go())
    assert tracker.today_pnl == D("0")
    assert tracker.best_day == D("700")
