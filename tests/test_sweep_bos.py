"""sweep_bos engine: sweep -> break of structure (close beyond the nearest
opposing swing), no displacement/FVG requirement. Revelio's simple chain."""
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.events import Bar
from app.strategy.sweep_bos import SweepBOSConfig, SweepBOSDetector

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 3, 4, 9, 30, tzinfo=ET)


def bar(i, lo, hi, c=None, o=None):
    lo, hi = Decimal(lo), Decimal(hi)
    c = Decimal(c) if c else (lo + hi) / 2
    o = Decimal(o) if o else c
    return Bar(instrument="MNQ", timeframe="5min", ts=T0 + timedelta(minutes=5 * i),
               open=o, high=hi, low=lo, close=c, volume=100)


def _cfg(**kw):
    kw.setdefault("bos_window_bars", 10)
    return SweepBOSConfig(instrument="MNQ", stop_buffer=Decimal("3.0"),
                          r_multiple=Decimal("2.5"), **kw)


def _build_structure(det):
    """Feed bars that confirm a swing low at 20990 and a swing high at 21010,
    then sweep the high. Returns next bar index."""
    seq = [
        bar(0, "20996", "21004"),
        bar(1, "20994", "21002"),
        bar(2, "20990", "20998"),   # swing low candidate (20990)
        bar(3, "20996", "21004"),
        bar(4, "21000", "21008"),   # confirms swing low (lookback 2)
        bar(5, "21004", "21010"),   # swing high candidate (21010)
        bar(6, "20998", "21006"),
        bar(7, "20996", "21004"),   # confirms swing high
    ]
    for b in seq:
        assert det.on_bar(b) is None
    # sweep the swing high: penetrate 21010 and close back below
    assert det.on_bar(bar(8, "21002", "21013", c="21005")) is None
    return 9


class TestSweepBOS:
    def test_bos_after_sweep_fires_short(self):
        det = SweepBOSDetector(_cfg())
        i = _build_structure(det)
        # close BELOW the swing low 20990 within the window -> BOS -> SHORT
        sig = det.on_bar(bar(i, "20984", "21004", c="20986"))
        assert sig is not None
        assert sig.side == "short"
        assert sig.entry == Decimal("20986")            # BOS bar close
        assert sig.stop == Decimal("21016")             # sweep extreme 21013 + 3.0
        # target = entry - 2.5R, R = 30
        assert sig.target == Decimal("20911")

    def test_no_bos_no_signal(self):
        det = SweepBOSDetector(_cfg())
        i = _build_structure(det)
        # stays above the swing low -> no signal
        for k in range(5):
            assert det.on_bar(bar(i + k, "20995", "21005", c="21000")) is None

    def test_bos_without_sweep_no_signal(self):
        det = SweepBOSDetector(_cfg())
        seq = [
            bar(0, "20996", "21004"),
            bar(1, "20994", "21002"),
            bar(2, "20990", "20998"),
            bar(3, "20996", "21004"),
            bar(4, "21000", "21008"),
            bar(5, "21004", "21010"),
            bar(6, "20998", "21006"),
            bar(7, "20996", "21004"),
        ]
        for b in seq:
            det.on_bar(b)
        # no sweep happened; closing below the swing low alone must NOT fire
        assert det.on_bar(bar(8, "20984", "21000", c="20986")) is None

    def test_sweep_expires_after_window(self):
        det = SweepBOSDetector(_cfg(bos_window_bars=3))
        i = _build_structure(det)
        for k in range(3):  # age the sweep out
            assert det.on_bar(bar(i + k, "20995", "21005", c="21000")) is None
        assert det.on_bar(bar(i + 3, "20984", "21004", c="20986")) is None


class TestEngineSelection:
    def test_build_runner_returns_sweep_bos(self):
        from app.backtest.runner import BacktestConfig, _build_runner
        from app.bot_config import StrategyParams
        from app.strategy.sweep_bos import SweepBOSRunner

        s = StrategyParams(engine="sweep_bos", r_multiple=Decimal("3.5"),
                           stop_buffer=Decimal("3.0"))
        cfg = BacktestConfig(instrument="MNQ", bars=iter([]),
                             timeframe="5min", strategy_params=s,
                             enabled_killzones=["all"])
        runner = _build_runner(cfg)
        assert isinstance(runner, SweepBOSRunner)
        assert runner.detector.config.r_multiple == Decimal("3.5")
        assert runner.detector.config.bos_window_bars == 10  # ifvg_sweep_window_bars
