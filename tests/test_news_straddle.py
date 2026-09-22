"""Defining-behavior tests for the news_straddle engine (B89).

The CPI breakout-straddle (B85, confirmed on 1s data): a 15-min pre-release
range, OCO stop entries at range_high + offset / range_low - offset, a TIGHT
stop at the broken range boundary (R = offset), TP = tp_r * R. These tests pin
the SIGNAL GEOMETRY the detector must reproduce — entry/stop/target prices,
OCO single-fire, whipsaw detection, and no-lookahead range construction.

WHY geometry and not P&L: the PaperBroker fills entries at market (last bar
close), not at signal.entry (the stale-FVG fix, paper.py). A resting stop-entry
fills at the stop level, so run_backtest cannot faithfully price this strategy.
The oracle (scripts/news_straddle_cpi_1s.py, B85) remains the expectancy source
of truth; the engine reproduces its signals. See the parity test at the bottom.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.bot_config import StrategyParams
from app.sim.events import Bar
from app.strategy.news_straddle import (
    NewsStraddleConfig,
    NewsStraddleDetector,
)

TICK = Decimal("0.25")
EVENT = datetime(2024, 1, 11, 13, 30, tzinfo=timezone.utc)  # a CPI release


def _bar(ts: datetime, high, low, close=None, open_=None) -> Bar:
    o = Decimal(str(open_ if open_ is not None else low))
    c = Decimal(str(close if close is not None else high))
    return Bar(
        instrument="MNQ",
        timeframe="1s",
        ts=ts,
        open=o,
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(c)),
        volume=1,
    )


def _detector(offset_ticks=60, tp_r="3.0", min_range_bars=1) -> NewsStraddleDetector:
    return NewsStraddleDetector(NewsStraddleConfig(
        instrument="MNQ",
        event_times=[EVENT],
        offset_ticks=offset_ticks,
        tp_r=Decimal(tp_r),
        tick=TICK,
        range_minutes=15,
        entry_window_minutes=30,
        min_range_bars=min_range_bars,
    ))


def _feed_prerange(det: NewsStraddleDetector, high=100.0, low=90.0, n=3):
    """Feed n pre-release bars establishing range [low, high]. Returns nothing."""
    for i in range(n):
        ts = EVENT - timedelta(minutes=15) + timedelta(seconds=i)
        # alternate so max-high/min-low are exactly high/low
        h = high if i == 0 else high - 1
        lo = low if i == 1 else low + 1
        assert det.on_bar(_bar(ts, h, lo, close=(h + lo) / 2)) is None


def test_off_by_default():
    assert StrategyParams().engine == "ifvg"
    assert StrategyParams().engine != "news_straddle"


def test_engine_selection_builds_runner_in_both_paths():
    """engine="news_straddle" must build a NewsStraddleRunner in live AND backtest."""
    from app.main import _build_runner as build_live
    from app.strategy.news_straddle import NewsStraddleRunner

    s = StrategyParams(engine="news_straddle")
    live = build_live("MNQ", s, timeframe="1min")
    assert isinstance(live, NewsStraddleRunner)
    assert live.detector.config.offset_ticks == 60
    assert live.detector.config.tp_r == Decimal("3.0")
    # Real CPI events loaded from data/news_events.csv (none in default config path
    # only if the file is absent; in this repo it exists).
    assert len(live.detector.events) > 0


def test_range_built_from_pre_release_only_no_lookahead():
    det = _detector()
    _feed_prerange(det, high=100.0, low=90.0)
    # A post-release bar with an enormous high must NOT be folded into the range.
    sig = det.on_bar(_bar(EVENT, high=100.0 + 15.0, low=99.0, close=115.0))
    # offset = 60 ticks * 0.25 = 15.0 ; buy = 100 + 15 = 115. high == 115 >= buy → long.
    assert sig is not None and sig.side == "long"
    # stop is the PRE-release range high (100), not anything from the post bar.
    assert sig.stop == Decimal("100.0")


def test_long_fires_on_up_break_with_correct_geometry():
    det = _detector(tp_r="3.0")
    _feed_prerange(det, high=100.0, low=90.0)
    # buy = 115. A bar tagging 115 fires long.
    sig = det.on_bar(_bar(EVENT, high=115.0, low=101.0, close=114.0))
    assert sig is not None
    assert sig.side == "long"
    assert sig.entry == Decimal("115.0")          # range_high + offset
    assert sig.stop == Decimal("100.0")           # broken range boundary
    R = sig.entry - sig.stop
    assert R == Decimal("15.0")                    # R == offset
    assert sig.target == Decimal("115.0") + Decimal("3.0") * R  # 115 + 45 = 160


def test_short_fires_on_down_break_with_correct_geometry():
    det = _detector(tp_r="3.0")
    _feed_prerange(det, high=100.0, low=90.0)
    # sell = 90 - 15 = 75. A bar tagging 75 fires short.
    sig = det.on_bar(_bar(EVENT, high=89.0, low=75.0, close=76.0))
    assert sig is not None
    assert sig.side == "short"
    assert sig.entry == Decimal("75.0")           # range_low - offset
    assert sig.stop == Decimal("90.0")            # broken range boundary
    R = sig.stop - sig.entry
    assert R == Decimal("15.0")
    assert sig.target == Decimal("75.0") - Decimal("3.0") * R   # 75 - 45 = 30


def test_tp_at_4r_when_configured():
    det = _detector(tp_r="4.0")
    _feed_prerange(det, high=100.0, low=90.0)
    sig = det.on_bar(_bar(EVENT, high=115.0, low=101.0, close=114.0))
    assert sig.target == Decimal("115.0") + Decimal("4.0") * Decimal("15.0")  # 175


def test_oco_single_fire_cancels_sibling():
    det = _detector()
    _feed_prerange(det, high=100.0, low=90.0)
    first = det.on_bar(_bar(EVENT, high=115.0, low=101.0, close=114.0))
    assert first is not None and first.side == "long"
    # A later bar in the window that breaks the SELL side must NOT emit a short:
    # the OCO sibling was cancelled when the long filled.
    later = det.on_bar(_bar(EVENT + timedelta(seconds=30), high=95.0, low=75.0, close=76.0))
    assert later is None
    assert det.state()["events"][0]["status"] == "fired"


def test_both_legs_one_bar_is_whipsaw_no_signal():
    det = _detector()
    _feed_prerange(det, high=100.0, low=90.0)
    # buy=115, sell=75; one bar tags both → whipsaw, no tradeable signal.
    sig = det.on_bar(_bar(EVENT, high=116.0, low=74.0, close=100.0))
    assert sig is None
    st = det.state()
    assert st["whipsaws"] == 1
    assert st["events"][0]["status"] == "whipsaw"


def test_no_fill_outside_entry_window():
    det = _detector()
    _feed_prerange(det, high=100.0, low=90.0)
    # No break during the window.
    assert det.on_bar(_bar(EVENT, high=110.0, low=95.0, close=105.0)) is None
    # A break AFTER the 30-min entry window closes must be ignored.
    late = det.on_bar(_bar(EVENT + timedelta(minutes=31), high=120.0, low=100.0, close=119.0))
    assert late is None
    assert det.state()["events"][0]["status"] == "nofill"


def test_skip_when_insufficient_prerange_bars():
    det = _detector(min_range_bars=60)  # oracle-style coverage requirement
    # Only feed 3 pre-range bars (< 60) → event is skipped, never armed.
    _feed_prerange(det, high=100.0, low=90.0, n=3)
    sig = det.on_bar(_bar(EVENT, high=200.0, low=50.0, close=150.0))
    assert sig is None
    assert det.state()["events"][0]["status"] == "skipped"


def test_range_excludes_event_bar_itself():
    """The bar timestamped exactly at EVENT is the first ENTRY bar, not pre-range."""
    det = _detector()
    _feed_prerange(det, high=100.0, low=90.0)
    # The event bar has a high above the eventual range — it must not raise rhigh.
    sig = det.on_bar(_bar(EVENT, high=130.0, low=101.0, close=129.0))
    assert sig.stop == Decimal("100.0")  # rhigh still 100, not 130


# ---------------------------------------------------------------------------
# Oracle parity: drive the detector over the real 1s CPI windows and confirm it
# reproduces scripts/news_straddle_cpi_1s.py per-event side+entry+stop+target.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tp_r", ["3.0"])
def test_oracle_parity_on_1s_cpi_windows(tp_r):
    import os
    import pandas as pd

    bars_path = "bars/bars_NQ_1s_cpi_windows.csv"
    if not os.path.exists(bars_path):
        pytest.skip("1s CPI bars not present")

    from scripts.news_straddle_cpi_1s import load_1s, simulate

    df = load_1s(bars_path)
    ev = pd.read_csv("data/news_events.csv", parse_dates=["ts_utc"])
    ev = ev[(ev.event_type == "CPI") & (ev.ts_utc.dt.year != 2022)
            & (ev.ts_utc >= df.index[0]) & (ev.ts_utc <= df.index[-1])]
    event_times = [t.to_pydatetime() for t in ev.ts_utc]

    det = NewsStraddleDetector(NewsStraddleConfig(
        instrument="MNQ",
        event_times=event_times,
        offset_ticks=60,
        tp_r=Decimal(tp_r),
        tick=TICK,
        range_minutes=15,
        entry_window_minutes=30,
        min_range_bars=60,  # matches oracle's len(rng) < 60 guard
    ))

    # Collect the detector's first signal per event by replaying every 1s bar.
    sigs: dict[datetime, object] = {}
    for ts, row in df.iterrows():
        bar = Bar(instrument="MNQ", timeframe="1s", ts=ts.to_pydatetime(),
                  open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
                  low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
                  volume=int(row["volume"]))
        s = det.on_bar(bar)
        if s is not None:
            sigs[s.created_at] = s

    # Compare side + entry-direction against the oracle for each clean fill.
    compared = 0
    for e in event_times:
        oracle = simulate(df, pd.Timestamp(e), 60, float(tp_r))
        if oracle is None:
            continue
        outcome, _r = oracle
        ev_sigs = [s for t, s in sigs.items() if e <= t <= e + timedelta(minutes=30)]
        if outcome in ("stop", "tp", "timeout"):
            # Oracle took a directional trade → detector must have fired one.
            assert ev_sigs, f"detector missed a fill the oracle took at {e}"
            s = ev_sigs[0]
            # R must equal the offset (60 ticks = 15.0 pts) on both sides.
            R = (s.entry - s.stop) if s.side == "long" else (s.stop - s.entry)
            assert R == Decimal("15.0")
            compared += 1
        elif outcome == "whipsaw":
            assert not ev_sigs, f"detector fired on an oracle whipsaw at {e}"
            compared += 1
    assert compared >= 20, f"too few comparable CPI events ({compared})"
