"""B13 defining-behavior tests: exec_slippage column.

WHY: `slippage` = fill - signal_entry (FVG proximal edge), which reads 100+ pts
on vertical moves while real fill-vs-market slippage is ~2 ticks. `exec_slippage`
= fill - order_bar_close (close of the bar that triggered the signal), which
measures actual execution quality. Both columns are needed; backward-compat
column name `slippage` stays (it IS useful: plan-deviation diagnostic).
"""
from decimal import Decimal
from datetime import datetime, timezone

from app.broker.events import Bar, Fill
from app.journaling import (
    _entry_slippage,
    _exec_slippage,
    _TRADES_HEADERS,
    _make_bar_close_watcher,
    _last_bar_close,
)


def _fill(fill_price: float, is_entry: bool = True, instrument: str = "MNQ") -> Fill:
    return Fill(
        ts=datetime(2026, 6, 13, 14, 0, tzinfo=timezone.utc),
        instrument=instrument,
        side="long",
        fill_price=Decimal(str(fill_price)),
        size=2,
        is_entry=is_entry,
        realized_pnl_delta=Decimal("0"),
        contracts_delta=2 if is_entry else -2,
        broker_order_id="test-oid-001",
    )


def _bar(close: float, instrument: str = "MNQ") -> Bar:
    return Bar(
        instrument=instrument,
        timeframe="5min",
        ts=datetime(2026, 6, 13, 14, 0, tzinfo=timezone.utc),
        open=Decimal(str(close - 2)),
        high=Decimal(str(close + 5)),
        low=Decimal(str(close - 3)),
        close=Decimal(str(close)),
        volume=500,
    )


# --- header presence ---

def test_exec_slippage_header_present():
    """exec_slippage column must exist in _TRADES_HEADERS."""
    assert "exec_slippage" in _TRADES_HEADERS


def test_slippage_header_still_present():
    """Original slippage column must remain (backward compat)."""
    assert "slippage" in _TRADES_HEADERS


def test_exec_slippage_after_slippage_in_headers():
    """exec_slippage should appear after slippage for column ordering."""
    idx_slip = _TRADES_HEADERS.index("slippage")
    idx_exec = _TRADES_HEADERS.index("exec_slippage")
    assert idx_exec > idx_slip


# --- _exec_slippage function behavior ---

def test_exec_slippage_market_fill():
    """bar_close=100.0, fill=100.25 → exec_slippage='0.25' (1 tick on MNQ)."""
    meta = {"order_bar_close": "100.0"}
    result = _exec_slippage(_fill(100.25), meta)
    assert result == "0.25"


def test_exec_slippage_exact_fill_at_close():
    """fill_price == order_bar_close → exec_slippage='0.0' (Decimal zero)."""
    meta = {"order_bar_close": "21500.0"}
    result = _exec_slippage(_fill(21500.0), meta)
    assert Decimal(result) == Decimal("0")


def test_exec_slippage_exit_rows_empty():
    """Exit fills (is_entry=False) must return '' — not meaningful for exits."""
    meta = {"order_bar_close": "100.0"}
    result = _exec_slippage(_fill(98.0, is_entry=False), meta)
    assert result == ""


def test_exec_slippage_no_bar_close_empty():
    """Missing order_bar_close in meta → '' (backfilled rows, old trades)."""
    meta = {}
    result = _exec_slippage(_fill(100.25), meta)
    assert result == ""


def test_exec_slippage_not_plan_deviation():
    """exec_slippage measures fill vs bar_close, NOT fill vs signal_entry.

    Fill=100.25, signal_entry=90.0 (FVG proximal edge far away),
    bar_close=100.0 → exec_slippage='0.25', slippage='10.25'.
    """
    fill = _fill(100.25)
    meta_exec = {"order_bar_close": "100.0"}
    meta_plan = {"signal_entry": "90.0"}
    assert _exec_slippage(fill, meta_exec) == "0.25"
    assert _entry_slippage(fill, meta_plan) == "10.25"


# --- bar-close watcher wiring ---

def test_bar_close_watcher_updates_last_bar_close():
    """_make_bar_close_watcher returns an async handler that tracks last bar close
    by root instrument (normalised from SDK contract strings)."""
    import asyncio

    _last_bar_close.clear()
    handler = _make_bar_close_watcher()
    asyncio.run(handler(_bar(21400.0, instrument="MNQ")))
    assert _last_bar_close.get("MNQ") == Decimal("21400.0")


def test_bar_close_watcher_normalises_sdk_instrument():
    """CON.F.US.MNQ.M26 → keyed as 'MNQ' in _last_bar_close."""
    import asyncio

    _last_bar_close.clear()
    handler = _make_bar_close_watcher()
    asyncio.run(handler(_bar(21400.0, instrument="CON.F.US.MNQ.M26")))
    assert _last_bar_close.get("MNQ") == Decimal("21400.0")
