"""
Integration tests: paper broker + risk module end-to-end.

These tests exercise the full data path that will run live:

  Bar arrives → strategy decides → pretrade.check() → broker.place_bracket
  → fill arrives → risk_state.record_fill → equity tick → risk_state.mark_equity

The key thing under test isn't any single component — it's that they
stay in sync. If a fill flows through the broker but the risk state
doesn't update, every subsequent gate decision is based on stale state.
That's the failure mode that loses Combines.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.broker.events import Bar, Fill, MarkToMarket
from app.broker.paper import PaperBroker
from app.risk.config import fifty_k_combine
from app.risk.pretrade import Allow, Deny, ProposedOrder, check
from app.risk.state import RiskState


def make_bar(
    ts: datetime,
    o: str, h: str, l: str, c: str,
    instrument: str = "MGC",
) -> Bar:
    return Bar(
        instrument=instrument,
        timeframe="1min",
        ts=ts,
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=100,
    )


def base_ts() -> datetime:
    return datetime(2026, 5, 11, 14, 30, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_winning_trade_flows_through_full_loop():
    """
    Place a bracket, hit the target, verify:
      - Realized P&L lands in risk state
      - Equity high-water moves up
      - MLL floor trails accordingly
      - Account is NOT locked
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())

    # Wire broker events to risk state.
    async def on_fill(f: Fill) -> None:
        state.record_fill(
            realized_pnl_delta=f.realized_pnl_delta,
            contracts_delta=f.contracts_delta,
            ts=f.ts,
        )

    async def on_equity(m: MarkToMarket) -> None:
        state.mark_equity(m.equity, m.ts)

    broker.on_fill(on_fill)
    broker.on_equity(on_equity)
    await broker.connect()

    # Risk gate sees a clean account, allows the order.
    # On /MGC: 1 point = 10 ticks × $1/tick = $10. So a 2-point
    # stop-distance = $20 risk per contract. Realistic /MGC math.
    order = ProposedOrder(
        instrument="MGC",
        side="long",
        size=1,
        entry=Decimal("2400"),
        stop=Decimal("2398"),    # -$20 risk
        target=Decimal("2402"),  # +$20 target
    )
    decision = check(order, state)
    assert isinstance(decision, Allow)

    result = await broker.place_bracket(
        instrument=order.instrument,
        side=order.side,
        size=decision.allowed_size,
        entry=order.entry,
        stop=order.stop,
        target=order.target,
    )
    assert result.success
    # Allow event handlers to run.
    await asyncio.sleep(0)

    # After entry fill, risk should know we're +1 long.
    assert state.open_contracts == 1

    # Feed bars; eventually price tags the target.
    ts = base_ts()
    bars = [
        make_bar(ts + timedelta(minutes=1), "2400", "2400.5", "2399.5", "2400.2"),
        make_bar(ts + timedelta(minutes=2), "2400.2", "2401", "2400", "2400.8"),
        make_bar(ts + timedelta(minutes=3), "2400.8", "2402", "2400.5", "2401.5"),  # target hit
    ]
    for b in bars:
        await broker.inject_bar(b)
        await asyncio.sleep(0)

    # Position closed, P&L realized, account up $20.
    assert state.open_contracts == 0
    assert state.realized_balance == Decimal("50020")
    assert state.daily_pnl == Decimal("20")
    # Equity high water moved up at least to $50,020; floor trailed.
    assert state.equity_high_water >= Decimal("50020")
    assert state.mll_floor >= Decimal("48000")  # never below initial
    assert state.locked_out is None


@pytest.mark.asyncio
async def test_losing_streak_triggers_dll_lockout():
    """
    On /MGC, 1 contract risking 2 points = $20. To hit the DLL
    ($1,000), we'd need 50 losing trades — too many for a sensible
    test. Instead, take 5 trades with sized stops that lose $200
    each (20-point stops on /MGC).
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine(soft_buffer=Decimal("0")))

    async def on_fill(f: Fill):
        state.record_fill(f.realized_pnl_delta, f.contracts_delta, f.ts)

    async def on_equity(m: MarkToMarket):
        state.mark_equity(m.equity, m.ts)

    broker.on_fill(on_fill)
    broker.on_equity(on_equity)
    await broker.connect()

    ts = base_ts()
    for i in range(5):
        # 20-point stop on /MGC = $200 loss per trade.
        order = ProposedOrder(
            instrument="MGC",
            side="long",
            size=1,
            entry=Decimal("2400"),
            stop=Decimal("2380"),    # -$200
            target=Decimal("2500"),  # not reached
        )
        decision = check(order, state)
        if isinstance(decision, Deny):
            assert i >= 4, f"Locked out unexpectedly early at trade {i}"
            break

        await broker.place_bracket(
            instrument=order.instrument,
            side=order.side,
            size=decision.allowed_size,
            entry=order.entry,
            stop=order.stop,
            target=order.target,
        )
        await asyncio.sleep(0)

        bar = make_bar(
            ts + timedelta(minutes=i),
            "2400", "2400.5", "2378", "2380",
        )
        await broker.inject_bar(bar)
        await asyncio.sleep(0)

    # After 5 losing trades at -$200 each, account is down $1,000 → DLL hit.
    assert state.daily_pnl <= Decimal("-1000")
    assert state.locked_out is not None
    assert state.locked_out.code == "DLL_HIT"

    # Try one more entry — must be denied.
    sixth = ProposedOrder(
        instrument="MGC",
        side="long",
        size=1,
        entry=Decimal("2400"),
        stop=Decimal("2380"),
        target=Decimal("2500"),
    )
    decision = check(sixth, state)
    assert isinstance(decision, Deny)
    assert decision.reason_code == "LOCKED_OUT"


@pytest.mark.asyncio
async def test_soft_buffer_stops_bot_before_official_mll():
    """
    Lawrence's $500 soft buffer.

    Scenario: account has accumulated losses across multiple sessions
    (so DLL doesn't trip), bringing equity down to $48,500 — exactly
    the soft buffer line above the $48,000 MLL floor. The bot must
    self-stop here, before equity touches the official MLL.

    To exercise *only* the MLL soft buffer (not the DLL), we directly
    mark the equity rather than running fills. Using day rollovers
    between fills would also work but is unnecessary noise for this test.
    """
    state = RiskState(config=fifty_k_combine(soft_buffer=Decimal("500")))

    # Simulate equity arriving at $48,500 directly. In production this
    # happens after multiple sessions of small losses; the day rollovers
    # in between cleared each day's DLL state.
    state.mark_equity(Decimal("48500"), base_ts())

    # Buffer to floor = 48,500 - 48,000 = 500. Soft buffer = 500. Tripped.
    assert state.buffer_to_mll == Decimal("500")
    assert state.locked_out is not None
    assert state.locked_out.code == "MLL_SOFT_BUFFER"

    # Critical: equity is $48,500, NOT $48,000. Bot stopped before
    # the official failure line.
    assert state.current_equity > Decimal("48000")


@pytest.mark.asyncio
async def test_dll_trips_before_mll_when_loss_concentrated_in_one_day():
    """
    Companion test to the one above: when the same $1,500 loss lands
    in a single session, the DLL ($1,000) trips first. This is the
    correct ordering — DLL is the more aggressive guardrail.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine(soft_buffer=Decimal("500")))

    async def on_fill(f: Fill):
        state.record_fill(f.realized_pnl_delta, f.contracts_delta, f.ts)

    async def on_equity(m: MarkToMarket):
        state.mark_equity(m.equity, m.ts)

    broker.on_fill(on_fill)
    broker.on_equity(on_equity)
    await broker.connect()

    order = ProposedOrder(
        instrument="MGC",
        side="long",
        size=1,
        entry=Decimal("2400"),
        stop=Decimal("2250"),   # 150 points × $10/point = $1,500 loss
        target=Decimal("2500"),
    )
    decision = check(order, state)
    assert isinstance(decision, Allow)

    await broker.place_bracket(
        instrument="MGC",
        side="long",
        size=1,
        entry=order.entry,
        stop=order.stop,
        target=order.target,
    )
    await asyncio.sleep(0)

    ts = base_ts()
    bar = make_bar(ts + timedelta(minutes=1), "2400", "2400.5", "2240", "2250")
    await broker.inject_bar(bar)
    await asyncio.sleep(0)

    # The DLL is the binding constraint here, not the MLL soft buffer.
    # Either DLL_HIT (full -$1,000+ realized) or DLL_SOFT_BUFFER is correct.
    assert state.locked_out is not None
    assert state.locked_out.code in {"DLL_HIT", "DLL_SOFT_BUFFER"}


@pytest.mark.asyncio
async def test_lockout_still_allows_flatten():
    """
    Even after lockout, an exit order to flatten an open position
    must be allowed. The risk gate never traps us in a position.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine(soft_buffer=Decimal("0")))

    async def on_fill(f: Fill):
        state.record_fill(f.realized_pnl_delta, f.contracts_delta, f.ts)

    async def on_equity(m: MarkToMarket):
        state.mark_equity(m.equity, m.ts)

    broker.on_fill(on_fill)
    broker.on_equity(on_equity)
    await broker.connect()

    # Force the state into a lockout by directly marking a bad equity.
    state.mark_equity(Decimal("47500"), base_ts())
    assert state.locked_out is not None

    # An exit order (is_entry=False) should still be allowed.
    flatten_order = ProposedOrder(
        instrument="MGC",
        side="short",
        size=1,
        entry=Decimal("2390"),
        stop=Decimal("2392"),
        target=Decimal("2384"),
        is_entry=False,
    )
    decision = check(flatten_order, state)
    assert isinstance(decision, Allow)
