"""Tests for partial-profit + BE-trail in PaperBroker."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from app.broker.paper import PaperBroker
from app.broker.events import Bar, Fill


def _bar(ts, o, h, l, c, instrument="MGC"):
    return Bar(
        instrument=instrument, timeframe="1min", ts=ts,
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=100,
    )


def _now(offset_min: int = 0):
    return datetime(2026, 1, 2, 10, offset_min, tzinfo=timezone.utc)


async def _run(broker, bars, side="long", entry=100, stop=99, target=102, instrument="MGC"):
    fills = []

    async def collect(f: Fill):
        fills.append(f)

    broker.on_fill(collect)
    await broker.connect()
    await broker.place_bracket(instrument, side, 2, Decimal(str(entry)),
                                Decimal(str(stop)), Decimal(str(target)))
    for b in bars:
        await broker.inject_bar(b)
    return fills


def test_no_partial_baseline():
    """With partial_profit_r=0 (disabled), size=2 fills at full target."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal("0"),
    )
    fills = asyncio.run(_run(
        broker,
        [_bar(_now(1), 100, 103, 99.5, 102)],  # high hits target 102
    ))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 1
    assert exits[0].size == 2
    assert exits[0].fill_price == Decimal("102")


def test_partial_at_1r_long():
    """partial_profit_r=1.0: take 1 contract at 1R (101), stop moves to BE (100)."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal("1.0"),
    )
    # Entry=100, stop=99, so 1R=1. Partial target = 101. Final target = 102.
    # Bar hits 101 but not 102. Low=100.2 stays above the new BE stop=100.
    fills = asyncio.run(_run(
        broker,
        [_bar(_now(1), 100, 101, 100.2, 100.5)],
    ))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 1, f"Expected 1 partial exit, got {len(exits)}"
    assert exits[0].size == 1, "Partial fill should be 1 contract"
    assert exits[0].fill_price == Decimal("101")


def test_be_stop_protects_remainder():
    """After partial at 1R, stop moves to BE so remainder exits at entry on drawdown."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal("1.0"),
    )
    # Bar 1: high=101 triggers partial. Low=99.5 — original stop was 99, not hit,
    #   but BE stop is now 100 and 99.5 < 100, so remainder exits at BE=100.
    fills = asyncio.run(_run(
        broker,
        [_bar(_now(1), 100, 101, 99.5, 100)],
    ))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 2, f"Expected partial + BE exit, got {len(exits)}"
    partial_exit = exits[0]
    be_exit = exits[1]
    assert partial_exit.size == 1
    assert partial_exit.fill_price == Decimal("101")
    assert be_exit.size == 1
    assert be_exit.fill_price == Decimal("100")  # exit at BE, not original stop (99)


def test_full_target_after_partial():
    """Bar 1 triggers partial. Bar 2 hits full target; remainder exits there."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal("1.0"),
    )
    fills = asyncio.run(_run(
        broker,
        [
            _bar(_now(1), 100, 101, 100.2, 100.5),   # partial at 101; low=100.2 above BE
            _bar(_now(2), 100.5, 103, 100.2, 102),   # full target at 102; low=100.2 above BE
        ],
    ))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 2
    assert exits[0].fill_price == Decimal("101")  # partial
    assert exits[1].fill_price == Decimal("102")  # full target
    assert exits[0].size == 1
    assert exits[1].size == 1


def test_stop_before_partial():
    """If stop is hit before partial target, full exit at original stop (no partial)."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal("1.0"),
    )
    fills = asyncio.run(_run(
        broker,
        [_bar(_now(1), 100, 100.5, 98.5, 99)],  # low hits stop 99
    ))
    exits = [f for f in fills if not f.is_entry]
    assert len(exits) == 1
    assert exits[0].size == 2  # full exit, no partial taken
    assert exits[0].fill_price == Decimal("99")
