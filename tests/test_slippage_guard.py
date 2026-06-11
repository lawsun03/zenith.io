"""Tests for the entry slippage guard (abort mode).

WHY this matters: brackets offset the stop from the actual fill to keep
constant $ risk. When a market entry chases (2026-06-10 MNQ: 13pt adverse on
a 9.8pt planned stop), the fill-relative stop lands inside the retrace zone
— below the planned entry itself — and dies on normal post-spike noise.
The guard must flatten those fills instead of bracketing them, and must
NEVER leave the position naked if the flatten order fails.

These tests mock the SDK suite; they cannot catch real order rejections or
fill-event races — only the guard's decision logic and its fail-safe.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.broker.topstepx import TopstepXBroker, SIDE_BUY, SIDE_SELL


def _broker(frac: str) -> TopstepXBroker:
    return TopstepXBroker(max_entry_slippage_frac=Decimal(frac))


def _short_bracket(signal_entry: str, stop_offset: str, size: int = 1) -> dict:
    # short: stop above entry → positive stop_offset; close side = BUY
    return {
        "signal_entry": Decimal(signal_entry),
        "stop_offset": Decimal(stop_offset),
        "close_sdk_side": SIDE_BUY,
        "size": size,
        "account_id": 123,
        "instrument": "MNQ",
    }


def _mock_suite(flatten_success: bool = True) -> MagicMock:
    suite = MagicMock()
    suite.instrument_id = "CON.F.US.MNQ.M26"
    resp = MagicMock()
    resp.success = flatten_success
    suite.orders.place_market_order = AsyncMock(return_value=resp)
    return suite


def test_disabled_guard_never_aborts():
    """frac=0 must be a true no-op even on a catastrophic chase."""
    b = _broker("0")
    b._get_suite_for = MagicMock()
    # 2026-06-10 numbers: planned 28978.75 short, filled 28965.75 (13pt adverse)
    bracket = _short_bracket("28978.75", "9.80")
    aborted = asyncio.run(b._abort_if_slipped(bracket, Decimal("28965.75")))
    assert aborted is False
    b._get_suite_for.assert_not_called()


def test_slip_within_threshold_proceeds_to_bracket():
    """Normal ~1.6pt chase on a 9.8pt stop (16%) must trade through a 50% guard."""
    b = _broker("0.5")
    b._get_suite_for = MagicMock()
    bracket = _short_bracket("28978.75", "9.80")
    aborted = asyncio.run(b._abort_if_slipped(bracket, Decimal("28977.15")))
    assert aborted is False
    b._get_suite_for.assert_not_called()


def test_excessive_adverse_slip_flattens():
    """The 2026-06-10 fill (133% of stop distance) must be aborted, not bracketed."""
    b = _broker("0.5")
    suite = _mock_suite(flatten_success=True)
    b._get_suite_for = MagicMock(return_value=suite)
    bracket = _short_bracket("28978.75", "9.80", size=2)
    aborted = asyncio.run(b._abort_if_slipped(bracket, Decimal("28965.75")))
    assert aborted is True
    # Flatten must be a market order on the close side for the full size.
    suite.orders.place_market_order.assert_awaited_once_with(
        "CON.F.US.MNQ.M26", SIDE_BUY, 2, 123,
    )


def test_favorable_slippage_never_aborts():
    """A better-than-planned fill (short filled higher) widens edge — keep it."""
    b = _broker("0.5")
    b._get_suite_for = MagicMock()
    bracket = _short_bracket("28978.75", "9.80")
    aborted = asyncio.run(b._abort_if_slipped(bracket, Decimal("28992.00")))
    assert aborted is False


def test_failed_flatten_falls_back_to_brackets():
    """If the flatten order is rejected, the guard must return False so the
    caller places brackets — a naked position is worse than a bad stop."""
    b = _broker("0.5")
    suite = _mock_suite(flatten_success=False)
    b._get_suite_for = MagicMock(return_value=suite)
    bracket = _short_bracket("28978.75", "9.80")
    aborted = asyncio.run(b._abort_if_slipped(bracket, Decimal("28965.75")))
    assert aborted is False  # caller must proceed to protect the position


def test_long_side_adverse_direction():
    """For longs, adverse = filled ABOVE planned entry; guard must use the
    correct sign or it would abort good fills and keep broken ones."""
    b = _broker("0.5")
    suite = _mock_suite(flatten_success=True)
    b._get_suite_for = MagicMock(return_value=suite)
    bracket = {
        "signal_entry": Decimal("4240.0"),
        "stop_offset": Decimal("-3.0"),  # long: stop below entry
        "close_sdk_side": SIDE_SELL,
        "size": 1,
        "account_id": 123,
        "instrument": "MGC",
    }
    # filled 2pt above planned on a 3pt stop = 67% adverse → abort
    assert asyncio.run(b._abort_if_slipped(bracket, Decimal("4242.0"))) is True
    # filled 2pt BELOW planned (favorable) → keep
    assert asyncio.run(b._abort_if_slipped(bracket, Decimal("4238.0"))) is False
