"""chop_breakout: spec-mandated tests, one per defining behavior."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.broker.events import Bar
from app.strategy.chop_breakout import ChopBreakoutConfig, ChopBreakoutDetector
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing

T0 = datetime(2026, 3, 4, 14, 0, tzinfo=timezone.utc)


def bar(i, lo, hi, c=None, o=None, vol=100):
    lo, hi = Decimal(lo), Decimal(hi)
    c = Decimal(c) if c else (lo + hi) / 2
    o = Decimal(o) if o else c
    return Bar(instrument="MNQ", timeframe="5min", ts=T0 + timedelta(minutes=5 * i),
               open=o, high=hi, low=lo, close=c, volume=vol)


def _cfg(**kw):
    return ChopBreakoutConfig(instrument="MNQ", **kw)


def feed_history(det, n=130, i0=0):
    """Wide oscillating bars: 20-bar range â‰ˆ 70 â†’ the percentile history."""
    for i in range(n):
        base = 21000 + (30 if i % 2 else -30)
        det.on_bar(bar(i0 + i, str(base - 5), str(base + 5)))
    return i0 + n


def tight(i, width=4):
    return bar(i, str(21000 - width // 2), str(21000 + width // 2))


class TestGate:
    def test_chop_at_exactly_min_chop_bars(self):
        det = ChopBreakoutDetector(_cfg())
        i = feed_history(det)
        seen = []
        for k in range(40):
            det.on_bar(tight(i + k))
            seen.append((det._streak, det.state))
        for streak, state in seen:
            if streak <= 11:
                assert state == "idle"
        assert "chop" in [s for _, s in seen]
        first_chop = next(s for s in seen if s[1] == "chop")
        assert first_chop[0] == 12  # CHOP first appears exactly at streak 12

    def test_boundaries_widen_never_shrink(self):
        det = ChopBreakoutDetector(_cfg())
        i = feed_history(det)
        k = 0
        while det.state != "chop":
            det.on_bar(tight(i + k))
            k += 1
            assert k < 60, "never entered chop"
        hi0, lo0 = det.chop_high, det.chop_low
        det.on_bar(bar(i + k, str(lo0), str(hi0 + 2)))      # higher high, compressed
        assert det.chop_high == hi0 + 2 and det.chop_low == lo0
        det.on_bar(bar(i + k + 1, str(lo0 + 1), str(hi0)))  # inside bar
        assert det.chop_high == hi0 + 2 and det.chop_low == lo0  # never shrink


BOUNDS = (Decimal("21010"), Decimal("20990"))


def _in_chop(det, hi="21010", lo="20990"):
    det.state = "chop"
    det.chop_high, det.chop_low = Decimal(hi), Decimal(lo)
    return det


def _disp_event(i, side, close, fvg):
    d = bar(i, str(Decimal(close) - 2), str(Decimal(close) + 2), close)
    return d, DisplacementEvent(side=side, displacement_bar=d,
                                body_size=Decimal("20"), atr_at_event=Decimal("5"),
                                body_to_atr=Decimal("4"), fvg=fvg)


def _swing(kind, price, i=0):
    ts = T0 + timedelta(minutes=5 * i)
    return Swing(kind=kind, price=Decimal(price), bar_ts=ts, confirmed_ts=ts)


class TestTrigger:
    def test_breakout_without_inversion_no_signal(self):
        det = _in_chop(ChopBreakoutDetector(_cfg()))
        b, ev = _disp_event(200, "bullish", "21030", fvg=None)
        assert det.on_displacement(b, ev, *BOUNDS) is None

    def test_breakout_with_inversion_at_boundary_signals_continuation(self):
        det = _in_chop(ChopBreakoutDetector(_cfg()))
        det._liq15._swings.append(_swing("high", "21120"))
        fvg = FairValueGap(side="bearish", low=Decimal("21002"),
                           high=Decimal("21008"), created_at=T0)
        b, ev = _disp_event(200, "bullish", "21030", fvg=fvg)
        sig = det.on_displacement(b, ev, *BOUNDS)
        assert sig is not None
        assert sig.side == "long"                      # continuation, not reversal
        assert sig.entry == b.close
        # stop = tighter of fvg far side (21002) vs chop mid (21000) -> 21002
        assert sig.stop == Decimal("21002")
        assert sig.target == Decimal("21120")          # nearest 15m swing high

    def test_fvg_outside_boundary_no_signal(self):
        det = _in_chop(ChopBreakoutDetector(_cfg()))
        det._liq15._swings.append(_swing("high", "21120"))
        fvg = FairValueGap(side="bearish", low=Decimal("21015"),
                           high=Decimal("21020"), created_at=T0)  # above chop_high
        b, ev = _disp_event(200, "bullish", "21030", fvg=fvg)
        assert det.on_displacement(b, ev, *BOUNDS) is None


class TestFloorRule:
    def _setup(self, swing_price):
        det = _in_chop(ChopBreakoutDetector(_cfg()))
        det._liq15._swings.append(_swing("high", swing_price))
        fvg = FairValueGap(side="bearish", low=Decimal("21002"),
                           high=Decimal("21008"), created_at=T0)
        b, ev = _disp_event(200, "bullish", "21030", fvg=fvg)
        return det, b, ev

    def test_swing_below_floor_no_trade(self):
        # R = 28 (21030-21002); floor 1.5R = 42 -> swing must be >= 21072
        det, b, ev = self._setup("21069")   # 1.39R
        assert det.on_displacement(b, ev, *BOUNDS) is None

    def test_swing_above_floor_trades(self):
        det, b, ev = self._setup("21075")   # 1.61R
        assert det.on_displacement(b, ev, *BOUNDS) is not None


class TestExitRules:
    def _entered(self, vwap_invalidation=False):
        det = _in_chop(ChopBreakoutDetector(_cfg(vwap_invalidation=vwap_invalidation)))
        det._trade = {"side": "long", "entry": Decimal("21030"), "r": Decimal("28"),
                      "bars": 0, "trail_armed": False,
                      "chop_high": Decimal("21010"), "chop_low": Decimal("20990")}
        return det

    def test_failed_breakout_at_bar5_exits(self):
        det = self._entered()
        for i in range(4):  # bars 1-4 stay outside the range
            det._manage_trade(bar(300 + i, "21020", "21040"))
            assert det.exit_request is None
        det._manage_trade(bar(304, "20995", "21008", c="21000"))  # bar 5: back inside
        assert det.exit_request == "failed_breakout"

    def test_no_forced_exit_at_bar7(self):
        det = self._entered()
        for i in range(6):  # bars 1-6 outside
            det._manage_trade(bar(300 + i, "21020", "21040"))
        det._manage_trade(bar(306, "20995", "21008", c="21000"))  # bar 7: inside
        assert det.exit_request is None

    def test_vwap_invalidation_long_close_below(self):
        det = self._entered(vwap_invalidation=True)
        det._vwap.on_bar(bar(299, "21050", "21060", c="21055"))  # vwap â‰ˆ 21055
        det._manage_trade(bar(300, "21020", "21040", c="21030"))  # close < vwap
        assert det.exit_request == "vwap_invalidation"


class TestBreakoutBarOrdering:
    def test_displacement_bar_does_not_absorb_its_own_breakout(self):
        """The displacement bar (b2) widens chop bounds one bar before its
        event arrives (on b3 close). The breakout must be judged against the
        PRE-b2 range or no breakout can ever fire (close <= own high)."""
        det = ChopBreakoutDetector(_cfg())
        i = feed_history(det)
        k = 0
        while det.state != "chop":
            det.on_bar(tight(i + k))
            k += 1
            assert k < 60, "never entered chop"
        det._liq15._swings.append(_swing("high", "21150"))
        hi0 = det.chop_high
        fvg = FairValueGap(side="bearish", low=Decimal("21000"),
                           high=Decimal("21006"), created_at=T0)

        # b2: big bullish bar closing above the pre-existing chop high; the
        # detector sees no event yet (real detector fires one bar later).
        b2 = bar(i + k, "20998", str(hi0 + 30), c=str(hi0 + 25))
        events = {0: None, 1: DisplacementEvent(
            side="bullish", displacement_bar=b2, body_size=Decimal("25"),
            atr_at_event=Decimal("5"), body_to_atr=Decimal("5"), fvg=fvg)}
        calls = {"n": -1}

        class _DispStub:
            def on_bar(self, _bar):
                calls["n"] += 1
                return events.get(calls["n"])

        det.disp = _DispStub()
        assert det.on_bar(b2) is None          # event not visible yet
        b3 = bar(i + k + 1, str(hi0 + 20), str(hi0 + 32), c=str(hi0 + 28))
        sig = det.on_bar(b3)                   # event for b2 arrives now
        assert sig is not None and sig.side == "long"


class TestBreakoutBarBreaksGate:
    def test_signal_survives_gate_break_by_breakout_bar(self):
        """A strong breakout bar (b2) blows up the rolling 20-bar range and
        breaks the compression gate on b2 itself â€” one bar BEFORE its
        displacement event arrives. The signal must still fire: the chop
        state/bounds as of b2's open are what the breakout is judged
        against."""
        det = ChopBreakoutDetector(_cfg())
        # Saturate the range history with TIGHT bars so the P30 threshold is
        # realistic (small) and a big bar genuinely breaks the gate.
        i = 0
        for k in range(140):
            det.on_bar(tight(i + k, width=4 + (k % 3)))  # ranges 4-6
        i += 140
        k = 0
        while det.state != "chop":
            det.on_bar(tight(i + k))
            k += 1
            assert k < 60, "never entered chop"
        det._liq15._swings.append(_swing("high", "21150"))
        hi0 = det.chop_high
        fvg = FairValueGap(side="bearish", low=Decimal("21000"),
                           high=Decimal("21004"), created_at=T0)

        b2 = bar(i + k, "20998", str(hi0 + 40), c=str(hi0 + 35))  # huge bar
        events = {0: None, 1: DisplacementEvent(
            side="bullish", displacement_bar=b2, body_size=Decimal("35"),
            atr_at_event=Decimal("4"), body_to_atr=Decimal("8"), fvg=fvg)}
        calls = {"n": -1}

        class _DispStub:
            def on_bar(self, _bar):
                calls["n"] += 1
                return events.get(calls["n"])

        det.disp = _DispStub()
        assert det.on_bar(b2) is None
        assert det.state == "idle"             # gate broken by b2 â€” the trap
        b3 = bar(i + k + 1, str(hi0 + 30), str(hi0 + 42), c=str(hi0 + 38))
        sig = det.on_bar(b3)
        assert sig is not None and sig.side == "long"


class TestEngineExitChannel:
    def test_exit_request_flattens_open_position(self):
        import asyncio
        from dataclasses import dataclass as dc, field as f
        from app.broker.paper import PaperBroker
        from app.execution.engine import ExecutionEngine
        from app.risk.config import fifty_k_combine
        from app.risk.state import RiskState

        @dc
        class _Stub:
            instrument: str = "MNQ"
            timeframe: str = "5min"
            strategy_cfg: object = None
            vp: object = None
            composer: object = f(default_factory=lambda: type(
                "C", (), {"on_stop_loss": lambda self: None})())
            grader: object = None
            signal_instrument: str = ""
            last_reject: object = None
            exit_request: object = None

            def on_bar(self, bar):
                return None

        async def run():
            br = PaperBroker(starting_balance=Decimal("50000"),
                             slippage_ticks_market=0)
            rs = RiskState(config=fifty_k_combine())
            stub = _Stub()
            eng = ExecutionEngine(broker=br, risk_state=rs, runners=[stub],
                                  replay_mode=True, contracts=1)
            await br.connect()
            await eng.start()
            await br.inject_bar(bar(0, "20990", "21010", c="21000"))
            res = await br.place_bracket("MNQ", "long", 1,
                                         entry=Decimal("21000"),
                                         stop=Decimal("20980"),
                                         target=Decimal("21100"))
            assert res.success and len(br._open) == 1
            stub.exit_request = "failed_breakout"
            await br.inject_bar(bar(1, "20995", "21005", c="21000"))
            assert len(br._open) == 0  # flattened by the exit channel
            await eng.stop()

        asyncio.run(run())


class TestEngineSelection:
    def test_build_runner_returns_chop_runner(self):
        from app.backtest.runner import BacktestConfig, _build_runner
        from app.bot_config import StrategyParams
        from app.strategy.chop_breakout import ChopBreakoutRunner

        s = StrategyParams(engine="chop_breakout", min_absolute_body=Decimal("5.0"))
        cfg = BacktestConfig(instrument="MNQ", bars=iter([]),
                             timeframe="5min", strategy_params=s)
        runner = _build_runner(cfg)
        assert isinstance(runner, ChopBreakoutRunner)
        assert runner.detector.config.min_chop_bars == 12
        assert runner.detector.disp.config.min_absolute_body == Decimal("5.0")


class TestDeterminism:
    def test_same_bars_identical_signals(self):
        def run():
            det = ChopBreakoutDetector(_cfg())
            out = []
            for i in range(400):
                # deterministic pseudo-chop: wide, then tight, then trend out
                if i < 200:
                    base = 21000 + (30 if i % 2 else -30)
                    b = bar(i, str(base - 5), str(base + 5))
                elif i < 320:
                    b = tight(i)
                else:
                    base = 21000 + (i - 320) * 3
                    b = bar(i, str(base - 4), str(base + 8), c=str(base + 6))
                sig = det.on_bar(b)
                if sig is not None:
                    out.append(repr(sig))
                if det.exit_request:
                    out.append(det.exit_request)
                    det.exit_request = None
            return out
        assert run() == run()
