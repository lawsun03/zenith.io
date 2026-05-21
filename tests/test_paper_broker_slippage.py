"""Tests for PaperBroker slippage and commission modeling."""
import asyncio
from decimal import Decimal
import pytest
from app.broker.paper import PaperBroker
from app.broker.events import Bar, Fill
from datetime import datetime, timezone


def _bar(ts, o, h, l, c, instrument="MGC"):
    return Bar(
        instrument=instrument, timeframe="1min", ts=ts,
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=100,
    )


def _now():
    return datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc)


async def _run(broker, bars, entry, stop, target, side="long"):
    fills = []

    async def collect_fill(f: Fill):
        fills.append(f)

    broker.on_fill(collect_fill)
    await broker.connect()
    await broker.place_bracket("MGC", side, 1, Decimal(str(entry)),
                                Decimal(str(stop)), Decimal(str(target)))
    for b in bars:
        await broker.inject_bar(b)
    return fills


def test_no_slippage_baseline():
    """With 0 slippage and 0 commission, long exit at target returns exact P&L."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
    )
    ts = _now()
    # Entry at 100, target at 110 — should make $100 (10 points × $10/point × 1 contract)
    bars = [_bar(ts, 100, 115, 99, 110)]  # high hits target
    fills = asyncio.run(_run(broker, bars, entry=100, stop=95, target=110))
    exit_fill = next(f for f in fills if not f.is_entry)
    assert exit_fill.fill_price == Decimal("110")
    assert exit_fill.realized_pnl_delta == Decimal("100")  # 10 pts × $10/pt


def test_market_slippage_worsens_entry():
    """1-tick slippage on market entry shifts fill price against the trader."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=1,
        commission_per_side=Decimal("0"),
    )
    fills = []

    async def collect(f: Fill):
        fills.append(f)

    async def go():
        broker.on_fill(collect)
        await broker.connect()
        await broker.place_bracket("MGC", "long", 1,
                                   Decimal("100.0"), Decimal("95.0"), Decimal("110.0"))
    asyncio.run(go())
    entry_fill = next(f for f in fills if f.is_entry)
    # Long entry slips UP by 1 tick (0.10 for MGC)
    assert entry_fill.fill_price == Decimal("100.1")


def test_commission_deducted_from_pnl():
    """Commission is deducted from each fill's realized_pnl_delta."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0.74"),
    )
    ts = _now()
    bars = [_bar(ts, 100, 115, 99, 110)]

    fills = asyncio.run(_run(broker, bars, entry=100, stop=95, target=110))
    entry_fill = next(f for f in fills if f.is_entry)
    exit_fill = next(f for f in fills if not f.is_entry)

    # Entry fill: gross P&L is 0 (entry), minus $0.74 commission
    assert entry_fill.realized_pnl_delta == Decimal("-0.74")
    # Exit fill: gross $100 minus $0.74 commission
    assert exit_fill.realized_pnl_delta == Decimal("99.26")
