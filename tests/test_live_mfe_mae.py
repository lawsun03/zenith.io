"""
Defining-behavior tests for B80 — live trade MFE/MAE tracking in TopstepXBroker.

WHY this matters: the live broker must track peak favorable/adverse excursion
in R-units so Lawrence can observe trade quality in the dashboard without
opening the exchange platform. These tests fail if the tracker is dropped,
stops updating, leaks across instruments, or is missing from the SSE payload.
"""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.broker.events import Bar
from app.broker.topstepx import TopstepXBroker


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, order_id, success=True):
        self.orderId = order_id
        self.success = success


class _FakeOrders:
    def __init__(self):
        self._stop_ids = iter(["STOP1", "STOP2", "STOP3"])
        self._limit_ids = iter(["TARGET1", "TARGET2", "TARGET3"])

    async def place_stop_order(self, *a, **kw):
        return _FakeResp(next(self._stop_ids))

    async def place_limit_order(self, *a, **kw):
        return _FakeResp(next(self._limit_ids))

    async def cancel_order(self, *a, **kw):
        return _FakeResp("ok")


class _FakeSuite:
    def __init__(self, instrument_id="CON.F.US.MNQ.M26"):
        self.orders = _FakeOrders()
        self.instrument_id = instrument_id


def _live_broker(instrument="MNQ") -> TopstepXBroker:
    b = TopstepXBroker()
    b._suite = _FakeSuite()
    b._instruments = [instrument]
    return b


def _bar(instrument: str, close: float, ts: datetime | None = None) -> Bar:
    ts = ts or datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
    c = Decimal(str(close))
    return Bar(instrument=instrument, timeframe="5min", ts=ts,
               open=c, high=c, low=c, close=c, volume=100)


def _bracket(fill_price: float, stop: float, target: float,
             instrument="MNQ", entry_side="long", size=1) -> dict:
    fp = Decimal(str(fill_price))
    s = Decimal(str(stop))
    t = Decimal(str(target))
    sdk_side = 1 if entry_side == "long" else 2  # SIDE_SELL=1 for long close
    return {
        "fill_price": fp,
        "stop_offset": s - fp,
        "target_offset": t - fp,
        "close_sdk_side": sdk_side,
        "size": size,
        "account_id": 12345,
        "instrument": instrument,
        "entry_side": entry_side,
    }


def _run(coro):
    async def _wrap():
        result = await coro
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return result
    return asyncio.run(_wrap())


# ---------------------------------------------------------------------------
# Test 1: favorable bar → mfe_r = 1.0, mae_r = 0.0
# ---------------------------------------------------------------------------

class TestMfeAfterFavorableBar:
    def test_long_mfe_one_r_after_favorable_close(self):
        """Long entry=10000, stop=9990 (risk=10pts); close=10010 → mfe_r=1.0, mae_r=0.0."""
        b = _live_broker()

        async def run():
            await b._place_bracket_after_fill(
                _bracket(fill_price=10000, stop=9990, target=10030, entry_side="long")
            )
            b._update_mfe_mae(_bar("MNQ", close=10010))
            return b.live_excursion("MNQ")

        exc = _run(run())
        assert exc["mfe_r"] == pytest.approx(1.0), f"expected mfe_r=1.0, got {exc['mfe_r']}"
        assert exc["mae_r"] == pytest.approx(0.0), f"expected mae_r=0.0, got {exc['mae_r']}"
        assert exc["mfe_pts"] == pytest.approx(10.0), f"expected mfe_pts=10.0, got {exc['mfe_pts']}"
        assert exc["mae_pts"] == pytest.approx(0.0), f"expected mae_pts=0.0, got {exc['mae_pts']}"


# ---------------------------------------------------------------------------
# Test 2: peak MFE preserved after adverse bar
# ---------------------------------------------------------------------------

class TestPeakMfePersisted:
    def test_mfe_peak_held_after_adverse_bar(self):
        """After favorable bar (mfe_r=1.0), adverse bar must not reduce MFE peak.
        Second bar close=9995: adverse=5pts → mae_r=0.5; mfe_r still 1.0."""
        b = _live_broker()

        async def run():
            await b._place_bracket_after_fill(
                _bracket(fill_price=10000, stop=9990, target=10030, entry_side="long")
            )
            from datetime import timedelta
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            b._update_mfe_mae(_bar("MNQ", close=10010, ts=base))                       # +10pts favorable
            b._update_mfe_mae(_bar("MNQ", close=9995, ts=base + timedelta(minutes=5))) # -5pts adverse
            return b.live_excursion("MNQ")

        exc = _run(run())
        assert exc["mfe_r"] == pytest.approx(1.0), f"MFE peak should be preserved: {exc['mfe_r']}"
        assert exc["mae_r"] == pytest.approx(0.5), f"Expected mae_r=0.5 (5/10): {exc['mae_r']}"


# ---------------------------------------------------------------------------
# Test 3: flat (no position) → all zeros
# ---------------------------------------------------------------------------

class TestFlatReturnsZeros:
    def test_live_excursion_returns_zeros_when_flat(self):
        """live_excursion() returns zero R-values when no position is tracked."""
        b = _live_broker()
        exc = b.live_excursion("MNQ")
        assert exc["mfe_r"] == 0.0
        assert exc["mae_r"] == 0.0
        assert exc["mfe_pts"] == 0.0
        assert exc["mae_pts"] == 0.0

    def test_live_excursion_clears_after_exit_fill(self):
        """Tracker resets when the OCO exit fires, so flat state is restored."""
        b = _live_broker()

        async def run():
            await b._place_bracket_after_fill(
                _bracket(fill_price=10000, stop=9990, target=10030, entry_side="long")
            )
            b._update_mfe_mae(_bar("MNQ", close=10010))
            assert b.live_excursion("MNQ")["mfe_r"] > 0, "tracker should be active"
            # Simulate stop/target fill: pop the exit pair and clear tracker
            stop_id = "STOP1"
            if stop_id in b._exit_pairs:
                pair_info = b._exit_pairs.pop(stop_id)
                b._exit_pairs.pop(pair_info["paired_id"], None)
            b._mfe_tracker.pop("MNQ", None)   # same as what _on_fill_event does
            return b.live_excursion("MNQ")

        exc = _run(run())
        assert exc["mfe_r"] == 0.0
        assert exc["mae_r"] == 0.0


# ---------------------------------------------------------------------------
# Test 4: open_brackets() includes mfe_r / mae_r / mfe_pts / mae_pts
# ---------------------------------------------------------------------------

class TestOpenBracketsIncludesMfeMae:
    def test_open_brackets_has_mfe_mae_fields(self):
        """open_brackets() must include pos_mfe_r, pos_mae_r (SSE position event).

        WHY: the frontend polls /api/positions and the strategy_state SSE event
        both derive from open_brackets(). Missing fields = blank dashboard.
        """
        b = _live_broker()

        async def run():
            await b._place_bracket_after_fill(
                _bracket(fill_price=10000, stop=9990, target=10030, entry_side="long")
            )
            b._update_mfe_mae(_bar("MNQ", close=10010))
            return b.open_brackets()

        brackets = _run(run())
        assert len(brackets) >= 1, f"expected at least 1 open bracket, got {len(brackets)}"
        pos = brackets[0]
        assert "mfe_r" in pos, f"mfe_r missing from open_brackets(): {list(pos.keys())}"
        assert "mae_r" in pos, f"mae_r missing from open_brackets(): {list(pos.keys())}"
        assert "mfe_pts" in pos, f"mfe_pts missing from open_brackets()"
        assert "mae_pts" in pos, f"mae_pts missing from open_brackets()"
        assert pos["mfe_r"] == pytest.approx(1.0), f"mfe_r should be 1.0, got {pos['mfe_r']}"
        assert pos["mae_r"] == pytest.approx(0.0), f"mae_r should be 0.0, got {pos['mae_r']}"
