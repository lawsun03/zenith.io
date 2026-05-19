"""Tests for volume profile: config, profile construction, filter, target."""
from decimal import Decimal
from datetime import date
import pytest


# ── Task 1: Config roundtrip ──────────────────────────────────────────────────

def test_vp_config_fields_round_trip(tmp_path):
    """New VP fields must survive a save/load cycle."""
    from app.bot_config import BotConfig, StrategyParams, save_bot_config, load_bot_config

    cfg = BotConfig(strategy=StrategyParams(
        vp_enabled=False,
        vp_tick_size=Decimal("0.20"),
        vp_value_area_pct=0.68,
        vp_filter_tolerance=Decimal("3.0"),
        vp_hvn_threshold=2.0,
        vp_min_target_r=Decimal("1.5"),
    ))
    p = tmp_path / "cfg.json"
    save_bot_config(cfg, p)
    loaded = load_bot_config(p)

    assert loaded.strategy.vp_enabled is False
    assert loaded.strategy.vp_tick_size == Decimal("0.20")
    assert loaded.strategy.vp_value_area_pct == pytest.approx(0.68)
    assert loaded.strategy.vp_filter_tolerance == Decimal("3.0")
    assert loaded.strategy.vp_hvn_threshold == pytest.approx(2.0)
    assert loaded.strategy.vp_min_target_r == Decimal("1.5")


def test_vp_config_defaults():
    """VP fields must have sensible defaults that don't break existing configs."""
    from app.bot_config import StrategyParams

    s = StrategyParams()
    assert s.vp_enabled is True
    assert s.vp_tick_size == Decimal("0.10")
    assert s.vp_value_area_pct == pytest.approx(0.70)
    assert s.vp_filter_tolerance == Decimal("2.0")
    assert s.vp_hvn_threshold == pytest.approx(1.5)
    assert s.vp_min_target_r == Decimal("1.0")


# ── Task 2: Profile computation ───────────────────────────────────────────────

def test_compute_profile_none_on_empty_bins():
    """Empty bins must return None — no profile on a day with no bars."""
    from app.strategy.volume_profile import _compute_profile

    assert _compute_profile({}, date(2026, 5, 18), 0.70, 1.5) is None


def test_compute_profile_poc_is_highest_volume_bin():
    """POC must be the price level with the highest accumulated volume."""
    from app.strategy.volume_profile import _compute_profile

    bins = {Decimal(str(p)): 10 for p in range(1900, 1910)}
    bins[Decimal("1905")] = 100  # clear winner

    profile = _compute_profile(bins, date(2026, 5, 18), 0.70, 1.5)
    assert profile is not None
    assert profile.poc == Decimal("1905")


def test_compute_profile_value_area_contains_poc():
    """VAL <= POC <= VAH must always hold."""
    from app.strategy.volume_profile import _compute_profile

    bins = {Decimal(str(p)): 10 for p in range(1900, 1910)}
    bins[Decimal("1904")] = 80

    profile = _compute_profile(bins, date(2026, 5, 18), 0.70, 1.5)
    assert profile is not None
    assert profile.val <= profile.poc <= profile.vah


def test_compute_profile_value_area_covers_70_pct():
    """Bins between VAL and VAH must account for >= 70% of total volume."""
    from app.strategy.volume_profile import _compute_profile

    bins = {Decimal(str(p)): 10 for p in range(1900, 1910)}
    bins[Decimal("1905")] = 100  # total = 190

    profile = _compute_profile(bins, date(2026, 5, 18), 0.70, 1.5)
    assert profile is not None

    vol_in_va = sum(v for p, v in bins.items() if profile.val <= p <= profile.vah)
    total = sum(bins.values())
    assert vol_in_va / total >= 0.70


def test_compute_profile_hvns_above_threshold():
    """HVNs must be price levels where volume > mean * threshold."""
    from app.strategy.volume_profile import _compute_profile

    # 9 bins at 10, 1 bin at 50. mean = (9*10+50)/10 = 14. threshold=1.5 → cutoff=21.
    bins = {Decimal(str(p)): 10 for p in range(1900, 1910)}
    bins[Decimal("1905")] = 50

    profile = _compute_profile(bins, date(2026, 5, 18), 0.70, 1.5)
    assert profile is not None
    assert Decimal("1905") in profile.hvns
    assert Decimal("1904") not in profile.hvns


def test_compute_profile_session_date_preserved():
    """The session_date must be stored on the returned profile."""
    from app.strategy.volume_profile import _compute_profile

    bins = {Decimal("1900"): 100}
    d = date(2026, 5, 18)
    profile = _compute_profile(bins, d, 0.70, 1.5)
    assert profile is not None
    assert profile.session_date == d


# ── Task 3: Session management ────────────────────────────────────────────────

from datetime import datetime, timezone


def _bar(ts_utc: datetime, high: float, low: float, close: float, volume: int) -> "Bar":
    from app.broker.events import Bar
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts_utc,
        open=Decimal(str(close)), high=Decimal(str(high)),
        low=Decimal(str(low)), close=Decimal(str(close)),
        volume=volume,
    )


def test_no_prior_profile_on_first_day():
    """Tracker must not have a prior profile until a UTC date boundary is crossed."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams()

    tracker.on_bar(_bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc), 1902, 1899, 1901, 100), cfg)
    tracker.on_bar(_bar(datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc), 1903, 1900, 1902, 80), cfg)

    assert not tracker.has_prior_profile()


def test_prior_profile_set_after_date_boundary():
    """Feeding a bar with a new UTC date must finalize the prior session."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams()

    tracker.on_bar(_bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc), 1902, 1899, 1901, 100), cfg)
    tracker.on_bar(_bar(datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc), 1903, 1900, 1902, 80), cfg)
    assert not tracker.has_prior_profile()

    # Day 2 bar triggers finalization of day 1.
    tracker.on_bar(_bar(datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc), 1904, 1901, 1903, 60), cfg)
    assert tracker.has_prior_profile()


def test_bins_accumulate_volume_for_current_session():
    """After feeding bars on the same day, bins must contain nonzero volume."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams()

    tracker.on_bar(_bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc), 1902, 1900, 1901, 200), cfg)
    assert sum(tracker._bins.values()) > 0


def test_bins_reset_after_session_boundary():
    """Current session bins must reset when a new UTC date is seen."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams()

    tracker.on_bar(_bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc), 1902, 1900, 1901, 200), cfg)
    old_vol = sum(tracker._bins.values())

    # New day — bins should reset, then accumulate only the new bar's volume.
    tracker.on_bar(_bar(datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc), 1905, 1903, 1904, 50), cfg)
    new_vol = sum(tracker._bins.values())

    assert new_vol < old_vol  # new session has only one bar's volume


def test_accumulate_distributes_volume_to_correct_bins():
    """
    _accumulate must place volume at the correct tick-quantized price bins.
    Bar: high=1902.0, low=1900.0, close=1901.0, volume=30, tick_size=1.0
    → 3 bins: 1900, 1901, 1902. 10 volume each.
    """
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams(vp_tick_size=Decimal("1.0"))

    tracker.on_bar(_bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc), 1902, 1900, 1901, 30), cfg)

    assert tracker._bins.get(Decimal("1900"), 0) == 10
    assert tracker._bins.get(Decimal("1901"), 0) == 10
    assert tracker._bins.get(Decimal("1902"), 0) == 10


def test_accumulate_low_volume_bar_goes_to_close_bin():
    """
    When vol_per_bin == 0 (volume < n_bins), all volume must go to the close bin.
    Bar: high=1905.0, low=1900.0, volume=3, tick_size=1.0 → 6 bins → vol_per_bin=0 → close bin.
    """
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams(vp_tick_size=Decimal("1.0"))

    tracker.on_bar(_bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc), 1905, 1900, 1902, 3), cfg)

    # 6 bins (1900-1905), vol_per_bin = 3//6 = 0 → all 3 go to close bin (1902)
    assert tracker._bins.get(Decimal("1902"), 0) == 3
    # Other bins should have 0 volume
    assert tracker._bins.get(Decimal("1900"), 0) == 0
