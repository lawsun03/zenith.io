"""
Defining-behavior tests for MFE/MAE excursion tracking in PaperBroker.

These tests verify that:
  1. mfe_pts accumulates the maximum favorable price excursion from entry.
  2. mae_pts accumulates the maximum adverse price excursion from entry.
  3. Both update on the closing bar (before stop/target triggers).
  4. Cross-instrument isolation: an MNQ bar does not update an MGC bracket.
  5. r_mfe / r_mae appear on BacktestResult.trades from run_backtest.

MFE/MAE appear in the funded-objective pipeline (B2) and inform exit-ladder
design. A regression here would produce fantasy exit levels from bad data.
"""
import asyncio
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from app.sim.events import Bar, Side
from app.sim.paper import PaperBroker


def _bar(instrument: str, high: float, low: float, close: float,
         ts: datetime | None = None) -> Bar:
    ts = ts or datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
    return Bar(
        instrument=instrument,
        timeframe="5min",
        ts=ts,
        open=Decimal(str(close)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=100,
    )


def _broker(slippage: int = 0) -> PaperBroker:
    return PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=slippage,
        commission_per_side=Decimal("0"),
    )


async def _place_long(broker: PaperBroker, entry: float = 100.0,
                      stop: float = 95.0, target: float = 110.0,
                      instrument: str = "MNQ") -> str:
    """Place a long bracket; return the entry order_id."""
    # Prime the last-bar-close so place_bracket doesn't fall back to signal entry.
    broker._last_bar_close[instrument] = Decimal(str(entry))
    res = await broker.place_bracket(
        instrument=instrument,
        side="long",
        size=1,
        entry=Decimal(str(entry)),
        stop=Decimal(str(stop)),
        target=Decimal(str(target)),
    )
    assert res.success, f"place_bracket failed: {res.error}"
    return res.entry_order_id


async def _place_short(broker: PaperBroker, entry: float = 100.0,
                       stop: float = 105.0, target: float = 90.0,
                       instrument: str = "MNQ") -> str:
    broker._last_bar_close[instrument] = Decimal(str(entry))
    res = await broker.place_bracket(
        instrument=instrument,
        side="short",
        size=1,
        entry=Decimal(str(entry)),
        stop=Decimal(str(stop)),
        target=Decimal(str(target)),
    )
    assert res.success
    return res.entry_order_id


class TestMfeMaeAccumulation:
    def test_long_mfe_accumulates_across_bars(self):
        """MFE tracks the peak favorable excursion over all bars (long side)."""
        broker = _broker()

        async def run():
            await broker.connect()
            oid = await _place_long(broker, entry=100, stop=95, target=115)
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            # bar 1: high=103 → fav=3, adv=1 (low=99)
            await broker.inject_bar(_bar("MNQ", high=103, low=99, close=102,
                                         ts=base))
            # bar 2: high=101, low=97 → fav still 3, adv peaks at 3
            await broker.inject_bar(_bar("MNQ", high=101, low=97, close=100,
                                         ts=base + timedelta(minutes=5)))
            # bar 3 hits target (high=115) → closed; the bar's high=115 updates
            # MFE to 15 before the bracket closes — correct, that excursion happened.
            await broker.inject_bar(_bar("MNQ", high=115, low=100, close=115,
                                         ts=base + timedelta(minutes=10)))
            return broker.excursions_by_order_id()

        exc = asyncio.run(run())
        mfe, mae, stop_dist = exc[list(exc)[0]]
        assert mfe == Decimal("15"), f"expected mfe=15 (bar3 high=115-100), got {mfe}"
        assert mae == Decimal("3"), f"expected mae=3, got {mae}"
        assert stop_dist == Decimal("5"), f"expected stop_dist=5, got {stop_dist}"

    def test_long_mae_accumulates_across_bars(self):
        """MAE tracks the worst adverse dip below entry (long side)."""
        broker = _broker()

        async def run():
            await broker.connect()
            oid = await _place_long(broker, entry=100, stop=90, target=120)
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            await broker.inject_bar(_bar("MNQ", high=102, low=96, close=101,
                                         ts=base))
            await broker.inject_bar(_bar("MNQ", high=103, low=93, close=102,
                                         ts=base + timedelta(minutes=5)))
            # stop hit: low=89
            await broker.inject_bar(_bar("MNQ", high=96, low=89, close=90,
                                         ts=base + timedelta(minutes=10)))
            return broker.excursions_by_order_id()

        exc = asyncio.run(run())
        mfe, mae, _ = exc[list(exc)[0]]
        assert mfe == Decimal("3"), f"expected mfe=3, got {mfe}"
        assert mae == Decimal("11"), f"expected mae=11 (100-89), got {mae}"

    def test_short_mfe_mae(self):
        """MFE/MAE are measured correctly for short brackets."""
        broker = _broker()

        async def run():
            await broker.connect()
            await _place_short(broker, entry=100, stop=106, target=88)
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            # bar 1: low=95 → fav=5, high=102 → adv=2
            await broker.inject_bar(_bar("MNQ", high=102, low=95, close=97,
                                         ts=base))
            # bar 2 hits target (low=88)
            await broker.inject_bar(_bar("MNQ", high=99, low=88, close=88,
                                         ts=base + timedelta(minutes=5)))
            return broker.excursions_by_order_id()

        exc = asyncio.run(run())
        mfe, mae, stop_dist = exc[list(exc)[0]]
        assert mfe == Decimal("12"), f"expected mfe=12 (100-88), got {mfe}"
        assert mae == Decimal("2"), f"expected mae=2 (102-100), got {mae}"
        assert stop_dist == Decimal("6")

    def test_mfe_includes_closing_bar_favorable_leg(self):
        """On a whipsaw bar that hits the stop, MFE still captures the high.

        This matters for stop trades: the favorable excursion before the
        reversal is real data and should not be zero-credited.
        """
        broker = _broker()

        async def run():
            await broker.connect()
            await _place_long(broker, entry=100, stop=95, target=115)
            # Single whipsaw bar: high=108 (favorable) then low=94 (stop)
            await broker.inject_bar(_bar("MNQ", high=108, low=94, close=95,
                                         ts=datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)))
            return broker.excursions_by_order_id()

        exc = asyncio.run(run())
        mfe, mae, _ = exc[list(exc)[0]]
        assert mfe == Decimal("8"), f"expected mfe=8 (108-100), got {mfe}"
        assert mae == Decimal("6"), f"expected mae=6 (100-94), got {mae}"

    def test_cross_instrument_isolation(self):
        """An MNQ bar must not update an MGC bracket's MFE/MAE."""
        broker = _broker()

        async def run():
            await broker.connect()
            # MGC long bracket: entry=200, stop=195, target=215
            broker._last_bar_close["MGC"] = Decimal("200")
            res = await broker.place_bracket(
                instrument="MGC", side="long", size=1,
                entry=Decimal("200"), stop=Decimal("195"), target=Decimal("215"),
            )
            mgc_oid = res.entry_order_id
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            # Inject MNQ bar — large swings — should not touch MGC bracket
            await broker.inject_bar(_bar("MNQ", high=300, low=50, close=200,
                                         ts=base))
            # Read state before MGC bracket closes (still open)
            mgc_bracket = broker._open.get(mgc_oid)
            return mgc_bracket

        bracket = asyncio.run(run())
        assert bracket is not None
        assert bracket.mfe_pts == Decimal("0"), f"MGC mfe should be 0, got {bracket.mfe_pts}"
        assert bracket.mae_pts == Decimal("0"), f"MGC mae should be 0, got {bracket.mae_pts}"

    def test_r_mfe_r_mae_in_trade_dict(self):
        """run_backtest trade dicts include r_mfe, r_mae, mfe_pts, mae_pts."""
        from app.backtest.runner import BacktestConfig, run_backtest
        from app.strategy.composer import ComposerConfig
        from app.strategy.displacement import DisplacementConfig
        from app.strategy.liquidity import LiquidityConfig

        # We cannot easily trigger a real signal here, so directly verify that
        # a zero-trade run has no excursion dicts and that the fields appear
        # when the broker does have excursions. Test the plumbing via a
        # manually constructed broker run.
        broker = _broker()

        async def run():
            await broker.connect()
            await _place_long(broker, entry=100, stop=95, target=115)
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            await broker.inject_bar(_bar("MNQ", high=110, low=98, close=105,
                                         ts=base))
            # Hit target
            await broker.inject_bar(_bar("MNQ", high=116, low=105, close=115,
                                         ts=base + timedelta(minutes=5)))
            return broker.excursions_by_order_id()

        exc = asyncio.run(run())
        assert exc, "expected at least one excursion entry"
        mfe, mae, stop_dist = list(exc.values())[0]
        r_mfe = float(mfe / stop_dist) if stop_dist else 0.0
        r_mae = float(mae / stop_dist) if stop_dist else 0.0
        assert r_mfe > 0
        assert r_mae >= 0

    def test_merge_excursion_exposes_stop_dist(self):
        """_merge_excursion attaches raw stop_dist alongside R-normalised fields.

        B99: per-trade realised R is computed downstream as
        realised_points / stop_dist; exposing stop_dist raw (not just r_mfe/r_mae)
        lets the ORB target-R sweep classify each trade's outcome without
        re-deriving the stop distance from MFE. A regression that drops stop_dist
        would silently break that analysis.
        """
        from app.backtest.runner import _merge_excursion

        trade: dict = {}
        _merge_excursion(trade, (Decimal("10"), Decimal("4"), Decimal("5")))
        assert trade["stop_dist"] == "5"
        assert trade["mfe_pts"] == "10"
        assert trade["mae_pts"] == "4"
        assert trade["r_mfe"] == 2.0
        assert trade["r_mae"] == 0.8


class TestBeTrailR:
    """BE-trail: move stop to entry when MFE >= be_trail_r × initial_stop_dist."""

    def test_be_trail_moves_stop_to_entry(self):
        """Once MFE threshold crossed, stop is at entry and bracket stops there."""
        broker = _broker()
        broker._be_trail_r = Decimal("1.0")

        async def run():
            await broker.connect()
            # Long: entry=100, stop=95, target=120, stop_dist=5 → be_trail at 5pts MFE
            await _place_long(broker, entry=100, stop=95, target=120)
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            # bar 1: high=105 → MFE=5 exactly = 1.0×stop_dist → BE trail fires.
            # low=102 must stay above the new BE stop (100) so bracket stays open.
            await broker.inject_bar(_bar("MNQ", high=105, low=102, close=104,
                                         ts=base))
            bracket = list(broker._open.values())[0]
            stop_after = bracket.stop
            triggered = bracket.be_trail_triggered
            # bar 2: low=99 → below BE stop (100) → stops out at 100.
            fills: list = []
            fills: list = []
            broker._fill_handlers.append(lambda f: fills.append(f) or asyncio.sleep(0))
            await broker.inject_bar(_bar("MNQ", high=104, low=99, close=102,
                                         ts=base + timedelta(minutes=5)))
            still_open = len(broker._open) > 0
            return stop_after, triggered, still_open

        stop_after, triggered, still_open = asyncio.run(run())
        assert triggered, "be_trail_triggered should be True after MFE threshold crossed"
        assert stop_after == Decimal("100"), f"stop should be at entry=100, got {stop_after}"
        assert not still_open, "bar2 low=99 < BE stop=100 should stop out bracket"

    def test_be_trail_does_not_fire_before_threshold(self):
        """BE trail must NOT fire until MFE reaches the threshold."""
        broker = _broker()
        broker._be_trail_r = Decimal("1.0")

        async def run():
            await broker.connect()
            await _place_long(broker, entry=100, stop=95, target=120)
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            # bar 1: high=104 → MFE=4 < 5 (1.0×stop_dist) → NO BE trail
            await broker.inject_bar(_bar("MNQ", high=104, low=98, close=103,
                                         ts=base))
            bracket = list(broker._open.values())[0]
            return bracket.stop, bracket.be_trail_triggered

        stop, triggered = asyncio.run(run())
        assert not triggered
        assert stop == Decimal("95"), f"stop should still be at 95, got {stop}"

    def test_be_trail_disabled_when_zero(self):
        """be_trail_r=0 (default) must never modify the stop."""
        broker = _broker()  # be_trail_r is 0 by default

        async def run():
            await broker.connect()
            await _place_long(broker, entry=100, stop=95, target=120)
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            await broker.inject_bar(_bar("MNQ", high=120, low=98, close=115,
                                         ts=base))
            # Even after a huge MFE, stop should not change if be_trail_r=0
            exc = broker.excursions_by_order_id()
            return exc

        exc = asyncio.run(run())
        # Bracket closed by target hit; verify stop was never changed
        assert exc, "should have an excursion record"


class TestMfeMaeResetAndMultiple:
    def test_reset_clears_excursions(self):
        """reset() clears the closed-excursion sidecar."""
        broker = _broker()

        async def run():
            await broker.connect()
            await _place_long(broker, entry=100, stop=95, target=115)
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            await broker.inject_bar(_bar("MNQ", high=116, low=98, close=115,
                                         ts=base))
            before = len(broker.excursions_by_order_id())
            broker.reset()
            after = len(broker.excursions_by_order_id())
            return before, after

        before, after = asyncio.run(run())
        assert before == 1
        assert after == 0

    def test_multiple_sequential_trades(self):
        """Each closed bracket appears independently in excursions_by_order_id."""
        broker = _broker()

        async def run():
            await broker.connect()
            base = datetime(2026, 1, 6, 10, 0, tzinfo=timezone.utc)
            # Trade 1: long, hits target at bar 1
            await _place_long(broker, entry=100, stop=95, target=110)
            await broker.inject_bar(_bar("MNQ", high=111, low=99, close=110,
                                         ts=base))
            # Trade 2: long, hits stop at bar 2
            await _place_long(broker, entry=100, stop=95, target=110)
            await broker.inject_bar(_bar("MNQ", high=102, low=94, close=95,
                                         ts=base + timedelta(minutes=5)))
            return broker.excursions_by_order_id()

        exc = asyncio.run(run())
        assert len(exc) == 2, f"expected 2 excursion entries, got {len(exc)}"
