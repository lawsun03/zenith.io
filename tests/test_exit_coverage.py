"""Tests for the exit-coverage monitor: ExitCoverage coverage math, the broker's
exchange-order query, protective-order primitives, and config fields."""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.broker.events import BrokerPosition, ExitCoverage


def test_fully_covered_when_stop_and_target_meet_size():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=2, covered_target=2,
    )
    assert cov.fully_covered is True


def test_naked_when_stop_missing():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=0, covered_target=2,
    )
    assert cov.fully_covered is False


def test_naked_when_target_missing():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=2, covered_target=0,
    )
    assert cov.fully_covered is False


def test_naked_when_partially_covered():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=1, covered_target=2,
    )
    assert cov.fully_covered is False


def test_flat_position_is_always_covered():
    cov = ExitCoverage(
        instrument="MGC", position_size=0, side="",
        avg_price=Decimal("0"), covered_stop=0, covered_target=0,
    )
    assert cov.fully_covered is True


# ---------------------------------------------------------------------------
# Task 3: TopstepXBroker.exit_coverage
# ---------------------------------------------------------------------------

def _order(order_type, side, size, status=1):
    o = MagicMock()
    o.type = order_type      # 1=Limit, 4=Stop
    o.side = side            # 0=Bid/Buy, 1=Ask/Sell
    o.size = size
    o.status = status        # 1=Open
    return o


def _coverage_broker(position, open_orders):
    """Stub TopstepXBroker for exit_coverage. position is a BrokerPosition
    or None; open_orders is the list returned by search_open_orders."""
    from app.broker.topstepx import TopstepXBroker

    broker = object.__new__(TopstepXBroker)
    broker._connected = True
    broker._extra_suites = {}
    broker._instruments = [position.instrument] if position else ["MGC"]
    broker.get_positions = AsyncMock(return_value=[position] if position else [])
    suite = MagicMock()
    suite.instrument_id = "CON.F.US.MGC.Q25"
    suite.orders.search_open_orders = AsyncMock(return_value=open_orders)
    broker._suite = suite
    return broker


@pytest.mark.asyncio
async def test_exit_coverage_long_fully_covered():
    pos = BrokerPosition(
        instrument="MGC", side="long", size=2,
        average_price=Decimal("2400.0"), unrealized_pnl=Decimal("0"),
    )
    # Long closes with SELL (side=1): one stop(4) size 2 + one limit(1) size 2.
    orders = [_order(4, 1, 2), _order(1, 1, 2)]
    broker = _coverage_broker(pos, orders)
    cov = await broker.exit_coverage("MGC")
    assert cov.position_size == 2
    assert cov.side == "long"
    assert cov.covered_stop == 2
    assert cov.covered_target == 2
    assert cov.fully_covered is True


@pytest.mark.asyncio
async def test_exit_coverage_ignores_wrong_side_and_closed_orders():
    pos = BrokerPosition(
        instrument="MGC", side="long", size=2,
        average_price=Decimal("2400.0"), unrealized_pnl=Decimal("0"),
    )
    orders = [
        _order(4, 0, 2),            # stop on BUY side — wrong side, ignore
        _order(4, 1, 2, status=3),  # cancelled — ignore
        _order(1, 1, 2),            # valid target
    ]
    broker = _coverage_broker(pos, orders)
    cov = await broker.exit_coverage("MGC")
    assert cov.covered_stop == 0     # the only stop was wrong-side/closed
    assert cov.covered_target == 2
    assert cov.fully_covered is False


@pytest.mark.asyncio
async def test_exit_coverage_flat_when_no_position():
    broker = _coverage_broker(None, [])
    cov = await broker.exit_coverage("MGC")
    assert cov.position_size == 0
    assert cov.fully_covered is True


# ---------------------------------------------------------------------------
# Task 4: Protective-order primitives
# ---------------------------------------------------------------------------

def _placer_broker(position):
    from app.broker.topstepx import TopstepXBroker

    broker = object.__new__(TopstepXBroker)
    broker._connected = True
    broker._extra_suites = {}
    broker._instruments = ["MGC"]
    broker.get_positions = AsyncMock(return_value=[position] if position else [])
    resp = MagicMock(success=True, orderId="emrg1")
    suite = MagicMock()
    suite.instrument_id = "CON.F.US.MGC.Q25"
    suite.orders.place_stop_order = AsyncMock(return_value=resp)
    suite.orders.place_limit_order = AsyncMock(return_value=resp)
    suite.client.account_info = MagicMock(id=42)
    broker._suite = suite
    return broker


@pytest.mark.asyncio
async def test_place_protective_stop_uses_close_side_for_long():
    pos = BrokerPosition(
        instrument="MGC", side="long", size=2,
        average_price=Decimal("2400.0"), unrealized_pnl=Decimal("0"),
    )
    broker = _placer_broker(pos)
    ok = await broker.place_protective_stop("MGC", 2, Decimal("2397.0"))
    assert ok is True
    # Long closes with SELL (1). Args: (instrument_id, side, size, price, account)
    args = broker._suite.orders.place_stop_order.call_args.args
    assert args[1] == 1            # SIDE_SELL
    assert args[2] == 2            # size
    assert args[3] == 2397.0       # price as float


@pytest.mark.asyncio
async def test_place_protective_stop_noops_when_flat():
    broker = _placer_broker(None)
    ok = await broker.place_protective_stop("MGC", 2, Decimal("2397.0"))
    assert ok is True
    broker._suite.orders.place_stop_order.assert_not_called()


@pytest.mark.asyncio
async def test_place_protective_target_uses_close_side_for_short():
    pos = BrokerPosition(
        instrument="MNQ", side="short", size=1,
        average_price=Decimal("20000.0"), unrealized_pnl=Decimal("0"),
    )
    broker = _placer_broker(pos)
    broker._instruments = ["MNQ"]
    ok = await broker.place_protective_target("MNQ", 1, Decimal("19920.0"))
    assert ok is True
    args = broker._suite.orders.place_limit_order.call_args.args
    assert args[1] == 0            # SIDE_BUY closes a short
    assert args[2] == 1
