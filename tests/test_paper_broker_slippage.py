"""Tests for PaperBroker slippage and commission modeling."""
import asyncio
from decimal import Decimal
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


def test_balance_reflects_entry_commission():
    """account_balance() must reflect entry commission deducted."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("1.00"),
    )

    async def go():
        await broker.connect()
        await broker.place_bracket("MGC", "long", 1,
                                   Decimal("100.0"), Decimal("95.0"), Decimal("110.0"))
        return await broker.account_balance()

    balance = asyncio.run(go())
    # Entry commission of $1 should be reflected
    assert balance == Decimal("49999.00"), f"Got {balance}"


def test_market_entry_fills_at_market_not_signal_price():
    """A market entry must fill at the current market (last bar close ± slip),
    NOT at the signal's entry price. Stop/target re-anchor as offsets from the
    fill, matching the live broker's _place_bracket_after_fill.

    Why: stale iFVG signals carry entry prices far off-market (2026-06-10
    parity check: MGC short 'filled' at 4361.20 while the bar was 4197-4202,
    then instantly 'won' because target was above market — fantasy P&L)."""
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
        # Market trading ~4200; signal carries stale prices 160pts above.
        await broker.inject_bar(_bar(_now(), 4199.0, 4202.6, 4197.8, 4200.0))
        await broker.place_bracket(
            "MGC", "short", 1,
            Decimal("4361.2"), Decimal("4364.5"), Decimal("4352.9"),
        )
        brackets = broker.open_brackets()
        # Next bar still ~4200 — must NOT instantly hit the re-anchored target.
        await broker.inject_bar(_bar(_now(), 4200.0, 4201.0, 4199.5, 4200.5))
        return brackets

    brackets = asyncio.run(go())
    entry_fill = next(f for f in fills if f.is_entry)
    # Short market entry fills at last close minus 1 tick (against the trader).
    assert entry_fill.fill_price == Decimal("4199.9"), f"Got {entry_fill.fill_price}"
    # Stop/target keep their signal-relative offsets, anchored at the fill.
    b = brackets[0]
    assert Decimal(str(b["stop"])) == Decimal("4203.2"), f"Got {b['stop']}"     # fill + 3.3
    assert Decimal(str(b["target"])) == Decimal("4191.6"), f"Got {b['target']}" # fill - 8.3
    # No instant exit: market never touched the re-anchored stop or target.
    assert all(f.is_entry for f in fills), f"Unexpected exit fill: {fills}"


def test_cancel_all_does_not_vaporize_positions():
    """cancel_all must NOT delete filled positions — in live it cancels resting
    protective orders only; the position survives until flatten() closes it
    with a real exit fill.

    Why: the engine's reversal path is cancel_all() then flatten(). The old
    paper semantics dropped the bracket (position and all) on cancel_all, so
    flatten found nothing, no exit fill ever fired, and RiskState contracts
    leaked — a 2.5y MNQ backtest deadlocked at MAX_CONTRACTS (-30/30) on
    2024-01-25 and traded nothing for the remaining 2.4 years."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
    )
    fills = []

    async def collect(f: Fill):
        fills.append(f)

    async def go():
        broker.on_fill(collect)
        await broker.connect()
        await broker.inject_bar(_bar(_now(), 100, 101, 99, 100))
        await broker.place_bracket("MGC", "long", 2,
                                   Decimal("100"), Decimal("95"), Decimal("110"))
        await broker.cancel_all("MGC")
        assert len(broker.open_brackets()) == 1, "cancel_all must keep the position"
        await broker.flatten("MGC")

    asyncio.run(go())
    exit_fills = [f for f in fills if not f.is_entry]
    assert len(exit_fills) == 1, "flatten after cancel_all must emit an exit fill"
    # Net contracts return to zero: +2 on entry, -2 on the flatten exit.
    assert sum(f.contracts_delta for f in fills) == 0
    assert broker.open_brackets() == []


def test_stop_slippage_worsens_exit():
    """Stop-loss fills should slip adversely (long stop slips down)."""
    broker = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=1,
        commission_per_side=Decimal("0"),
    )
    ts = _now()
    # Entry slips to 100.1; the stop re-anchors to the fill (constant risk,
    # like the live broker's fill-relative bracket): 100.1 - 5 = 95.1.
    # Bar: low=93 hits stop. With 1-tick exit slippage, fills at 95.1 - 0.10.
    bars = [_bar(ts, 100, 101, 93, 94)]  # low hits stop
    fills = asyncio.run(_run(broker, bars, entry=100, stop=95, target=110,
                              side="long"))
    exit_fill = next(f for f in fills if not f.is_entry)
    assert exit_fill.fill_price == Decimal("95.00"), f"Got {exit_fill.fill_price}"
