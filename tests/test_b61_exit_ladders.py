"""B61 defining tests: BE-trail at 1.5R and partials at 2.0R / 2.5R trigger at the
declared threshold, not before."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

from app.sim.paper import PaperBroker
from app.sim.events import Bar, Fill


def _bar(ts, o, h, l, c, instrument="MNQ"):
    return Bar(
        instrument=instrument, timeframe="5min", ts=ts,
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=100,
    )


def _now(offset_min=0):
    return datetime(2026, 1, 6, 10, offset_min, tzinfo=timezone.utc)


def _make_broker(partial_profit_r="0", be_trail_r="0"):
    b = PaperBroker(
        starting_balance=Decimal("50000"),
        slippage_ticks_market=0,
        commission_per_side=Decimal("0"),
        partial_profit_r=Decimal(partial_profit_r),
    )
    b._be_trail_r = Decimal(be_trail_r)
    return b


async def _setup_long(b):
    """Place long: entry=100, stop=98, target=110 → stop_dist=2."""
    fills = []
    async def collect(f: Fill):
        fills.append(f)
    b.on_fill(collect)
    await b.connect()
    await b.place_bracket("MNQ", "long", 2,
                           Decimal("100"), Decimal("98"), Decimal("110"))
    return fills


class TestBeTrail1p5R:
    """BE-trail at 1.5R: stop_dist=2, threshold MFE=3 pts."""

    def test_does_not_fire_below_threshold(self):
        """MFE of 2.9 pts (just below 1.5R=3) must not trigger BE."""
        b = _make_broker(be_trail_r="1.5")

        async def run():
            await _setup_long(b)
            await b.inject_bar(_bar(_now(5), 100, 102.9, 99, 102))
            bracket = list(b._open.values())[0]
            return bracket.be_trail_triggered, bracket.stop

        triggered, stop = asyncio.run(run())
        assert not triggered, "BE must not trigger below 1.5R threshold"
        assert stop == Decimal("98"), f"stop unchanged at 98, got {stop}"

    def test_fires_at_1p5r_exactly(self):
        """MFE of exactly 3 pts (=1.5×stop_dist) moves stop to entry."""
        b = _make_broker(be_trail_r="1.5")

        async def run():
            await _setup_long(b)
            # high=103 → MFE=3=1.5R; low=101 stays above new BE stop=100
            await b.inject_bar(_bar(_now(5), 100, 103, 101, 102))
            bracket = list(b._open.values())[0]
            return bracket.be_trail_triggered, bracket.stop

        triggered, stop = asyncio.run(run())
        assert triggered, "BE must fire when MFE reaches 1.5R exactly"
        assert stop == Decimal("100"), f"stop should move to entry=100, got {stop}"


class TestPartial2p0R:
    """Partial at 2.0R: stop_dist=2, entry=100 → partial target=104, full target=110."""

    def test_does_not_fire_below_2p0r(self):
        """high=103.9 (just below 104) must not trigger partial exit."""
        b = _make_broker(partial_profit_r="2.0")

        async def run():
            fills = await _setup_long(b)
            await b.inject_bar(_bar(_now(5), 100, 103.9, 99.5, 103))
            exits = [f for f in fills if not f.is_entry]
            return exits

        exits = asyncio.run(run())
        assert len(exits) == 0, f"No partial should fire below 2.0R, got exits={exits}"

    def test_fires_at_2p0r(self):
        """high=104 (=2.0R) triggers partial: 1 contract at 104, stop moves to BE=100."""
        b = _make_broker(partial_profit_r="2.0")

        async def run():
            fills = await _setup_long(b)
            # high=104 triggers partial; low=101 stays above new BE stop=100
            await b.inject_bar(_bar(_now(5), 100, 104, 101, 103))
            bracket = list(b._open.values())[0]
            exits = [f for f in fills if not f.is_entry]
            return exits, bracket.stop, bracket.partial_filled

        exits, stop, partial_filled = asyncio.run(run())
        assert len(exits) == 1, f"One partial exit expected, got {len(exits)}"
        assert exits[0].size == 1, "Partial = half position (1 of 2 contracts)"
        assert exits[0].fill_price == Decimal("104"), f"Exit at 104, got {exits[0].fill_price}"
        assert partial_filled, "partial_filled flag must be True"
        assert stop == Decimal("100"), f"stop should move to BE=100 after partial, got {stop}"


class TestPartial2p5R:
    """Partial at 2.5R: stop_dist=2, entry=100 → partial target=105, full target=110."""

    def test_does_not_fire_below_2p5r(self):
        """high=104.9 (just below 105) must not trigger partial exit."""
        b = _make_broker(partial_profit_r="2.5")

        async def run():
            fills = await _setup_long(b)
            await b.inject_bar(_bar(_now(5), 100, 104.9, 99.5, 104))
            exits = [f for f in fills if not f.is_entry]
            return exits

        exits = asyncio.run(run())
        assert len(exits) == 0, f"No partial should fire below 2.5R, got exits={exits}"

    def test_fires_at_2p5r(self):
        """high=105 (=2.5R) triggers partial: 1 contract at 105, stop moves to BE=100."""
        b = _make_broker(partial_profit_r="2.5")

        async def run():
            fills = await _setup_long(b)
            # high=105 triggers partial; low=101 stays above new BE stop=100
            await b.inject_bar(_bar(_now(5), 100, 105, 101, 104))
            bracket = list(b._open.values())[0]
            exits = [f for f in fills if not f.is_entry]
            return exits, bracket.stop, bracket.partial_filled

        exits, stop, partial_filled = asyncio.run(run())
        assert len(exits) == 1, f"One partial exit expected, got {len(exits)}"
        assert exits[0].size == 1, "Partial = half position (1 of 2 contracts)"
        assert exits[0].fill_price == Decimal("105"), f"Exit at 105, got {exits[0].fill_price}"
        assert partial_filled, "partial_filled flag must be True"
        assert stop == Decimal("100"), f"stop should move to BE=100 after partial, got {stop}"
