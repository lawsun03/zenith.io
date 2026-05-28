from datetime import datetime, timezone, time as dtime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.broker.events import Bar
from app.bot_config import StrategyParams
from app.strategy.kz_levels import KillzoneLevelTracker
from app.strategy.killzone import Killzone, london_open, ny_am

ET = ZoneInfo("America/New_York")


def _bar(ts: datetime, h, l, c) -> Bar:
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts,
        open=Decimal(str(c)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=1,
    )


def _ts(hour: int, minute: int = 0, tz=ET) -> datetime:
    """2026-05-28 at the given hour:minute in tz."""
    return datetime(2026, 5, 28, hour, minute, tzinfo=tz)


# London open zone: 02:00–05:00 ET
LONDON = london_open()
NY_AM  = ny_am()


def test_accumulates_high_low_during_zone():
    """H/L accumulates while the killzone is active."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams()
    zones = [LONDON]

    tracker.on_bar(_bar(_ts(2, 0), h=101, l=99, c=100), zones, cfg)
    tracker.on_bar(_bar(_ts(2, 1), h=103, l=98, c=101), zones, cfg)
    # Zone is still open — levels not finalized yet, no sweeps possible.
    assert not tracker._kz_ranges


def test_finalizes_level_after_zone_closes():
    """Once the killzone closes, H/L is locked into _kz_ranges."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams()

    tracker.on_bar(_bar(_ts(2, 0), h=101, l=99, c=100), [LONDON], cfg)
    tracker.on_bar(_bar(_ts(2, 1), h=103, l=98, c=101), [LONDON], cfg)
    # Bar after London ends (05:00 ET)
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)

    assert "London" in tracker._kz_ranges
    high, low = tracker._kz_ranges["London"]
    assert high == Decimal("103")
    assert low == Decimal("98")


def test_pattern_b_sweep_of_kz_high():
    """One-bar sweep: bar.high >= kz_high + min_pen AND bar.close < kz_high."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams(min_penetration=Decimal("0.20"))

    # Accumulate London H/L: high=103, low=98
    tracker.on_bar(_bar(_ts(2, 0), h=103, l=98, c=100), [LONDON], cfg)
    # Close London zone
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)

    # Pattern B: tag high (103.3 >= 103 + 0.2) and close below (102.5 < 103)
    sweeps = tracker.on_bar(_bar(_ts(9, 0), h=103.3, l=102, c=102.5), [LONDON, NY_AM], cfg)
    assert len(sweeps) == 1
    s = sweeps[0]
    assert s.side == "high"
    assert s.pattern == "B_one_bar"
    assert s.swept_swing.price == Decimal("103")


def test_pattern_b_sweep_of_kz_low():
    """One-bar sweep of a KZ low level."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams(min_penetration=Decimal("0.20"))

    tracker.on_bar(_bar(_ts(2, 0), h=103, l=98, c=100), [LONDON], cfg)
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)

    # Pattern B low: bar.low <= kz_low - min_pen AND bar.close > kz_low
    sweeps = tracker.on_bar(_bar(_ts(9, 0), h=98.5, l=97.7, c=98.3), [LONDON, NY_AM], cfg)
    assert len(sweeps) == 1
    assert sweeps[0].side == "low"
    assert sweeps[0].swept_swing.price == Decimal("98")


def test_pattern_a_sweep_multi_bar():
    """Multi-bar sweep: tag doesn't close back same bar, but closes back next bar."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams(min_penetration=Decimal("0.20"), multi_bar_window=3)

    tracker.on_bar(_bar(_ts(2, 0), h=103, l=98, c=100), [LONDON], cfg)
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)

    # Tag bar: high crosses (103.3 >= 103.2) but close DOES NOT confirm (close=103.1 >= 103)
    sweeps1 = tracker.on_bar(_bar(_ts(9, 0), h=103.3, l=102, c=103.1), [LONDON, NY_AM], cfg)
    assert len(sweeps1) == 0  # no sweep yet

    # Close-back bar: close falls back below kz_high
    sweeps2 = tracker.on_bar(_bar(_ts(9, 1), h=103.0, l=101, c=102.5), [LONDON, NY_AM], cfg)
    assert len(sweeps2) == 1
    assert sweeps2[0].pattern == "A_multi_bar"


def test_level_consumed_after_sweep():
    """Once swept, a KZ level is removed and does not produce a second sweep."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams(min_penetration=Decimal("0.20"))

    tracker.on_bar(_bar(_ts(2, 0), h=103, l=98, c=100), [LONDON], cfg)
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)

    tracker.on_bar(_bar(_ts(9, 0), h=103.3, l=102, c=102.5), [LONDON, NY_AM], cfg)
    # Second sweep attempt — level should be gone.
    sweeps = tracker.on_bar(_bar(_ts(9, 1), h=103.3, l=102, c=102.5), [LONDON, NY_AM], cfg)
    assert len(sweeps) == 0


def test_signal_source_defaults_to_swing():
    """Existing callers that don't pass source get 'swing' tag — no regression."""
    from app.strategy.composer import Signal
    from decimal import Decimal
    from datetime import datetime, timezone
    sig = Signal(
        instrument="MGC", side="long",
        entry=Decimal("100"), stop=Decimal("98"), target=Decimal("105"),
        created_at=datetime.now(timezone.utc),
        killzone="NY AM", sweep_pattern="B_one_bar",
        sweep_extreme=Decimal("97.5"),
        fvg_low=Decimal("99"), fvg_high=Decimal("100.5"),
        rationale="test",
    )
    assert sig.source == "swing"


def test_signal_source_kz_level_tag():
    """source='kz_level' can be explicitly set."""
    from app.strategy.composer import Signal
    from decimal import Decimal
    from datetime import datetime, timezone
    sig = Signal(
        instrument="MGC", side="short",
        entry=Decimal("103"), stop=Decimal("105"), target=Decimal("98"),
        created_at=datetime.now(timezone.utc),
        killzone="London", sweep_pattern="B_one_bar",
        sweep_extreme=Decimal("103.5"),
        fvg_low=Decimal("102.5"), fvg_high=Decimal("103"),
        rationale="test",
        source="kz_level",
    )
    assert sig.source == "kz_level"


def test_daily_reset_clears_all_state():
    """On a new UTC date, all ranges and pending state resets."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams()

    # Accumulate and finalize London level on day 1.
    tracker.on_bar(_bar(_ts(2, 0), h=103, l=98, c=100), [LONDON], cfg)
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)
    assert "London" in tracker._kz_ranges

    # Bar on next UTC day → reset.
    next_day = datetime(2026, 5, 29, 10, 0, tzinfo=timezone.utc)
    tracker.on_bar(_bar(next_day, h=100, l=99, c=100), [LONDON], cfg)
    assert not tracker._kz_ranges
