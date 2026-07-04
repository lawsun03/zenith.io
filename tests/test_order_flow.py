"""Order-flow tests — aggressor delta / CVD math, sidecar round-trip, and the
ORB order-flow gate.

These encode WHY the gate exists (CLAUDE.md Rule 9): a breakout that closes
beyond the OR edge on flat/opposite delta is a stop-run and must be suppressed;
the same breakout on confirming delta must pass. A test that only checked the
delta arithmetic could not catch a gate wired to the wrong side."""
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.events import Bar
from app.strategy.orb import ORBConfig, ORBDetector
from app.strategy.order_flow import (
    OrderFlowBar,
    OrderFlowSeries,
    aggregate_trades,
    minute_key,
    order_flow_gate,
    write_csv,
)

ET = ZoneInfo("America/New_York")


def _dt(h, m, s=0):
    return datetime(2024, 1, 2, h, m, s, tzinfo=timezone.utc)


class TestAggregate:
    def test_delta_is_buy_minus_sell(self):
        prints = [(_dt(14, 0, 1), 10, "A"), (_dt(14, 0, 2), 4, "B"),
                  (_dt(14, 0, 3), 6, "A")]
        bars = aggregate_trades(prints)
        assert len(bars) == 1
        b = bars[0]
        assert b.buy_vol == 16 and b.sell_vol == 4
        assert b.delta == 12          # 16 - 4
        assert b.cvd == 12            # first bucket
        assert b.total_vol == 20

    def test_neutral_side_counts_to_total_not_delta(self):
        # 'N' aggressor is unknown — it must not move delta but must show in volume.
        bars = aggregate_trades([(_dt(14, 0), 5, "A"), (_dt(14, 0), 7, "N")])
        assert bars[0].delta == 5
        assert bars[0].total_vol == 12

    def test_cvd_accumulates_across_minutes_in_time_order(self):
        # Deliberately unsorted input; buckets must emit chronologically and CVD
        # must be the running sum, not per-bucket delta.
        prints = [
            (_dt(14, 2), 3, "B"),   # minute 3: delta -3
            (_dt(14, 0), 10, "A"),  # minute 1: delta +10
            (_dt(14, 1), 2, "B"),   # minute 2: delta -2
        ]
        bars = aggregate_trades(prints)
        assert [b.delta for b in bars] == [10, -2, -3]
        assert [b.cvd for b in bars] == [10, 8, 5]

    def test_lowercase_side_codes(self):
        bars = aggregate_trades([(_dt(14, 0), 4, "a"), (_dt(14, 0), 1, "b")])
        assert bars[0].delta == 3


class TestSidecarRoundTrip:
    def test_write_then_load_and_lookup(self, tmp_path):
        src = aggregate_trades([(_dt(14, 0), 10, "A"), (_dt(14, 1), 4, "B")])
        path = tmp_path / "of_TEST.csv"
        assert write_csv(src, path) == 2
        series = OrderFlowSeries.from_csv(path)
        assert len(series) == 2
        # Lookup tolerates a differently-zoned / sub-minute timestamp.
        got = series.for_bar(datetime(2024, 1, 2, 9, 0, 30, tzinfo=ET))  # 14:00 UTC
        assert got is not None and got.delta == 10 and got.cvd == 10

    def test_lookup_miss_returns_none(self, tmp_path):
        path = tmp_path / "of.csv"
        write_csv(aggregate_trades([(_dt(14, 0), 1, "A")]), path)
        series = OrderFlowSeries.from_csv(path)
        assert series.for_bar(_dt(23, 59)) is None


class TestGate:
    def _of(self, delta):
        return OrderFlowBar(ts=_dt(14, 0), buy_vol=max(delta, 0),
                            sell_vol=max(-delta, 0), delta=delta, cvd=delta,
                            total_vol=abs(delta))

    def test_long_passes_on_positive_delta(self):
        allow, _ = order_flow_gate("long", self._of(50), min_delta=25)
        assert allow

    def test_long_blocked_when_delta_below_threshold(self):
        allow, reason = order_flow_gate("long", self._of(10), min_delta=25)
        assert not allow and "no buy aggression" in reason

    def test_long_blocked_on_opposite_delta(self):
        # Price closed above the OR high but net selling — the stop-run signature.
        allow, _ = order_flow_gate("long", self._of(-40), min_delta=0)
        assert not allow

    def test_short_passes_on_negative_delta(self):
        allow, _ = order_flow_gate("short", self._of(-50), min_delta=25)
        assert allow

    def test_short_blocked_on_positive_delta(self):
        allow, reason = order_flow_gate("short", self._of(40), min_delta=25)
        assert not allow and "no sell aggression" in reason

    def test_missing_data_is_graceful_allow(self):
        allow, reason = order_flow_gate("long", None, min_delta=999)
        assert allow and reason == "no_data"


def _bar(h, m, o, hi, lo, c, day=4):
    return Bar(instrument="MNQ", timeframe="5min",
               ts=datetime(2026, 3, day, h, m, tzinfo=ET),
               open=Decimal(o), high=Decimal(hi), low=Decimal(lo),
               close=Decimal(c), volume=100)


def _feed_range(det, day=4):
    det.on_bar(_bar(9, 30, "21005", "21015", "21000", "21010", day))
    det.on_bar(_bar(9, 35, "21010", "21020", "21005", "21018", day))
    det.on_bar(_bar(9, 40, "21018", "21019", "21008", "21012", day))


class TestORBGateIntegration:
    """The gate must change the ORB detector's output, not just compute a number."""

    BREAKOUT = None  # set per-test

    def _detector(self, of_delta, gate_enabled, min_delta=0):
        cfg = ORBConfig(instrument="MNQ", range_minutes=15,
                        of_gate_enabled=gate_enabled, of_min_delta=min_delta)
        det = ORBDetector(cfg)
        breakout = _bar(9, 50, "21015", "21030", "21014", "21028")  # long breakout
        det.of_ctx = OrderFlowSeries([
            OrderFlowBar(ts=minute_key(breakout.ts), buy_vol=0, sell_vol=0,
                         delta=of_delta, cvd=of_delta, total_vol=abs(of_delta))
        ])
        return det, breakout

    def test_gate_off_allows_breakout_regardless_of_delta(self):
        det, breakout = self._detector(of_delta=-500, gate_enabled=False)
        _feed_range(det)
        assert det.on_bar(breakout) is not None  # gate disabled → unaffected

    def test_gate_suppresses_breakout_on_opposite_delta(self):
        # Long breakout close beyond OR high, but heavy net selling → stop-run.
        det, breakout = self._detector(of_delta=-500, gate_enabled=True, min_delta=50)
        _feed_range(det)
        assert det.on_bar(breakout) is None

    def test_gate_allows_breakout_on_confirming_delta(self):
        det, breakout = self._detector(of_delta=+500, gate_enabled=True, min_delta=50)
        _feed_range(det)
        sig = det.on_bar(breakout)
        assert sig is not None and sig.side == "long"

    def test_gate_graceful_when_no_of_row_for_bar(self):
        # Gate enabled but the injected series has no row for the breakout minute.
        cfg = ORBConfig(instrument="MNQ", range_minutes=15,
                        of_gate_enabled=True, of_min_delta=50)
        det = ORBDetector(cfg)
        det.of_ctx = OrderFlowSeries([])  # empty → for_bar returns None
        _feed_range(det)
        assert det.on_bar(_bar(9, 50, "21015", "21030", "21014", "21028")) is not None
