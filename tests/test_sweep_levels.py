from app.bot_config import StrategyParams

def test_sweep_level_config_defaults_off():
    sp = StrategyParams()
    assert sp.sweep_levels_tag_enabled is False
    assert sp.sweep_levels_tag_tolerance_ticks == 4


from datetime import datetime, timezone
from decimal import Decimal
from app.sim.events import Bar
from app.strategy.sweep_levels import SweepLevelTracker

def _bar(et_hour, et_min, o, h, l, c, day="2026-06-16"):
    from zoneinfo import ZoneInfo
    ts_et = datetime.fromisoformat(f"{day}T{et_hour:02d}:{et_min:02d}:00").replace(tzinfo=ZoneInfo("America/New_York"))
    return Bar(instrument="MNQ", timeframe="5min", ts=ts_et.astimezone(timezone.utc),
               open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=100)

def test_daily_open_set_at_rth_and_no_lookahead():
    t = SweepLevelTracker()
    t.on_bar(_bar(9, 0, 21000, 21010, 20990, 21005))
    assert t.levels().get("daily_open") is None
    t.on_bar(_bar(9, 30, 21020, 21030, 21015, 21025))
    assert t.levels()["daily_open"] == Decimal("21020")

def test_asian_high_low_locked_after_session_close():
    t = SweepLevelTracker()
    t.on_bar(_bar(19, 0, 21000, 21050, 20990, 21010, day="2026-06-15"))
    t.on_bar(_bar(21, 0, 21010, 21080, 21005, 21070, day="2026-06-15"))
    t.on_bar(_bar(22, 30, 21070, 21075, 21060, 21065, day="2026-06-15"))
    assert t.levels()["asian_high"] == Decimal("21080")
    assert t.levels()["asian_low"] == Decimal("20990")

def test_tag_matches_within_tolerance_else_swing_only():
    # Pre-bar at 09:00 seeds weekly_open=21000 (distinct from daily_open=21020).
    # Ensures the tag test only matches daily_open, not weekly_open.
    t = SweepLevelTracker()
    t.on_bar(_bar(9, 0, 21000, 21010, 20990, 21005))
    t.on_bar(_bar(9, 30, 21020, 21030, 21015, 21025))
    tick = Decimal("0.25")
    assert t.tag(Decimal("21020.50"), tick, 4) == ["daily_open"]
    assert t.tag(Decimal("20800"), tick, 4) == ["swing_only"]

def test_tag_determinism_same_input_same_output():
    t = SweepLevelTracker()
    t.on_bar(_bar(9, 0, 21000, 21010, 20990, 21005))
    t.on_bar(_bar(9, 30, 21020, 21030, 21015, 21025))
    tick = Decimal("0.25")
    assert t.tag(Decimal("21021"), tick, 4) == t.tag(Decimal("21021"), tick, 4)


from app.strategy.composer import Signal

def test_signal_has_swept_level_type_field_default_none():
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(Signal)}
    assert "swept_level_type" in fields
    assert fields["swept_level_type"].default is None


# =====================================================================
# Composer integration tests: swept_level_type tagging end-to-end
#
# Why these tests matter (Rule 9):
#   The existing structural test only checks that the field exists.
#   These tests verify the BEHAVIOURAL contract:
#   - Default-off (sweep_levels_tag_enabled=False) must produce None,
#     not an empty list or any other sentinel — callers downstream rely
#     on None to mean "tagging not active".
#   - Enabled with a near-level sweep must produce the level name, not
#     ["swing_only"] — proves the tracker is wired through _build_signal
#     and the tolerance math is correct end-to-end.
#   - Enabled with a far sweep must produce ["swing_only"], not None —
#     proves the enabled path always returns a list, never None.
# =====================================================================

from datetime import timezone
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import SweepEvent, Swing
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _et_ts(h: int, m: int, day: str = "2026-06-16") -> datetime:
    return datetime.fromisoformat(f"{day}T{h:02d}:{m:02d}:00").replace(
        tzinfo=_ET
    ).astimezone(timezone.utc)


def _make_sweep_event(ts: datetime, sweep_extreme: str) -> tuple[Bar, SweepEvent]:
    """A Pattern B high sweep: pierced above 2403, closed back below."""
    b = Bar(
        instrument="MGC", timeframe="1min", ts=ts,
        open=Decimal("2401"), high=Decimal(sweep_extreme),
        low=Decimal("2401"), close=Decimal("2401.5"),
        volume=100,
    )
    swing = Swing(kind="high", price=Decimal("2403"), bar_ts=ts, confirmed_ts=ts)
    ev = SweepEvent(
        side="high",
        swept_swing=swing,
        pattern="B_one_bar",
        sweep_extreme=Decimal(sweep_extreme),
        completed_at=ts,
        sweep_bar=b,
    )
    return b, ev


def _make_bearish_disp_event(ts: datetime) -> tuple[Bar, DisplacementEvent]:
    """Bearish displacement with a bullish FVG for iFVG inversion (short setup)."""
    b = Bar(
        instrument="MGC", timeframe="1min", ts=ts,
        open=Decimal("2401"), high=Decimal("2401.2"),
        low=Decimal("2398.4"), close=Decimal("2398.5"),
        volume=100,
    )
    fvg = FairValueGap(
        side="bullish",
        low=Decimal("2399.0"),
        high=Decimal("2400.0"),
        created_at=ts,
    )
    ev = DisplacementEvent(
        side="bearish",
        displacement_bar=b,
        body_size=Decimal("2.5"),
        atr_at_event=Decimal("0.5"),
        body_to_atr=Decimal("5.0"),
        fvg=fvg,
    )
    return b, ev


def test_composer_default_off_swept_level_type_is_none():
    """
    With sweep_levels_tag_enabled=False (the default), an emitted signal
    must have swept_level_type=None — not an empty list, not ["swing_only"].
    None means "tagging inactive"; downstream consumers gate on this.
    """
    composer = SweepDisplacementComposer(ComposerConfig(
        instrument="MGC",
        displacement_window_bars=10,
        trend_ema_period=0,          # disable EMA filter — no warmup needed
        # Default: sweep_levels_tag_enabled=False
    ))

    ts_sweep = _et_ts(9, 31)
    ts_disp = _et_ts(9, 32)

    b_sweep, sweep_ev = _make_sweep_event(ts_sweep, "2403.5")
    composer.on_sweep(b_sweep, sweep_ev)
    composer.on_bar_close(b_sweep)

    b_disp, disp_ev = _make_bearish_disp_event(ts_disp)
    sig = composer.on_displacement(b_disp, disp_ev)

    assert sig is not None, "Expected a signal — check sweep/displacement wiring"
    assert sig.swept_level_type is None, (
        f"Default-off must produce None, got {sig.swept_level_type!r}"
    )


def test_composer_enabled_sweep_near_daily_open_tagged():
    """
    With sweep_levels_tag_enabled=True, a sweep extreme within tolerance
    of daily_open must tag the signal as ["daily_open"], not ["swing_only"].

    The daily_open is set by on_bar_close() when it receives the 9:30 bar.
    We set daily_open=2403.0 and sweep_extreme=2403.3 (0.3 pts away;
    MGC tick=0.10, tolerance=4 ticks = 0.40 pts → within tolerance).
    """
    composer = SweepDisplacementComposer(ComposerConfig(
        instrument="MGC",
        displacement_window_bars=10,
        trend_ema_period=0,
        sweep_levels_tag_enabled=True,
        sweep_levels_tag_tolerance_ticks=4,
    ))

    # Feed a 9:30 ET bar to seed daily_open=2403.0. The SweepLevelTracker
    # inside the composer receives this via on_bar_close().
    ts_open = _et_ts(9, 30)
    b_open = Bar(
        instrument="MGC", timeframe="1min", ts=ts_open,
        open=Decimal("2403.0"), high=Decimal("2403.5"),
        low=Decimal("2402.8"), close=Decimal("2403.2"),
        volume=100,
    )
    composer.on_bar_close(b_open)  # → daily_open = 2403.0

    # Sweep at 9:31 ET: extreme=2403.3, within 0.40 pts of daily_open=2403.0.
    ts_sweep = _et_ts(9, 31)
    b_sweep, sweep_ev = _make_sweep_event(ts_sweep, "2403.3")
    composer.on_sweep(b_sweep, sweep_ev)
    composer.on_bar_close(b_sweep)

    # Bearish displacement at 9:32 ET → should emit a short signal.
    ts_disp = _et_ts(9, 32)
    b_disp, disp_ev = _make_bearish_disp_event(ts_disp)
    sig = composer.on_displacement(b_disp, disp_ev)

    assert sig is not None, "Expected a signal — check sweep/displacement wiring"
    assert sig.swept_level_type is not None, (
        "Enabled tagging must return a list, not None"
    )
    assert "daily_open" in sig.swept_level_type, (
        f"Sweep extreme 2403.3 is within 4 ticks of daily_open 2403.0; "
        f"expected 'daily_open' in tag, got {sig.swept_level_type!r}"
    )


def test_composer_enabled_sweep_far_from_levels_tags_swing_only():
    """
    With sweep_levels_tag_enabled=True, a sweep extreme far from all known
    levels must produce ["swing_only"], not None. The list is always returned
    when tagging is enabled; None means "disabled".
    """
    composer = SweepDisplacementComposer(ComposerConfig(
        instrument="MGC",
        displacement_window_bars=10,
        trend_ema_period=0,
        sweep_levels_tag_enabled=True,
        sweep_levels_tag_tolerance_ticks=4,
    ))

    # Seed daily_open=2403.0 as above.
    ts_open = _et_ts(9, 30)
    b_open = Bar(
        instrument="MGC", timeframe="1min", ts=ts_open,
        open=Decimal("2403.0"), high=Decimal("2403.5"),
        low=Decimal("2402.8"), close=Decimal("2403.2"),
        volume=100,
    )
    composer.on_bar_close(b_open)

    # Sweep extreme=2450.0 — far from daily_open=2403.0 (47 pts >> 0.40 tol).
    # The Swing price must also be below the sweep extreme for a high sweep.
    ts_sweep = _et_ts(9, 31)
    b_sweep = Bar(
        instrument="MGC", timeframe="1min", ts=ts_sweep,
        open=Decimal("2445"), high=Decimal("2450"),
        low=Decimal("2444"), close=Decimal("2445.5"),
        volume=100,
    )
    swing = Swing(kind="high", price=Decimal("2445"), bar_ts=ts_sweep, confirmed_ts=ts_sweep)
    sweep_ev = SweepEvent(
        side="high",
        swept_swing=swing,
        pattern="B_one_bar",
        sweep_extreme=Decimal("2450"),
        completed_at=ts_sweep,
        sweep_bar=b_sweep,
    )
    composer.on_sweep(b_sweep, sweep_ev)
    composer.on_bar_close(b_sweep)

    # Bearish displacement — stop will be above sweep extreme 2450.
    ts_disp = _et_ts(9, 32)
    b_disp = Bar(
        instrument="MGC", timeframe="1min", ts=ts_disp,
        open=Decimal("2445"), high=Decimal("2445.2"),
        low=Decimal("2442"), close=Decimal("2442.5"),
        volume=100,
    )
    fvg = FairValueGap(
        side="bullish",
        low=Decimal("2443.0"),
        high=Decimal("2444.0"),
        created_at=ts_disp,
    )
    disp_ev = DisplacementEvent(
        side="bearish",
        displacement_bar=b_disp,
        body_size=Decimal("2.5"),
        atr_at_event=Decimal("0.5"),
        body_to_atr=Decimal("5.0"),
        fvg=fvg,
    )
    sig = composer.on_displacement(b_disp, disp_ev)

    assert sig is not None, "Expected a signal"
    assert sig.swept_level_type == ["swing_only"], (
        f"Sweep far from all levels must tag as ['swing_only'], "
        f"got {sig.swept_level_type!r}"
    )
