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
    from app.sim.events import Bar
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


# ── Task 4: Filter ────────────────────────────────────────────────────────────

def _signal(side: str, entry: float, stop: float) -> "Signal":
    from app.strategy.composer import Signal
    return Signal(
        instrument="MGC", side=side,  # type: ignore[arg-type]
        entry=Decimal(str(entry)), stop=Decimal(str(stop)),
        target=Decimal("0"),
        created_at=datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc),
        killzone="ny_am", sweep_pattern="B_one_bar",
        sweep_extreme=Decimal(str(stop)),
        fvg_low=None, fvg_high=None,
        rationale="test signal",
    )


def _profile(poc: float, vah: float, val: float, hvns: list[float] | None = None) -> "VolumeProfile":
    from app.strategy.volume_profile import VolumeProfile
    return VolumeProfile(
        session_date=date(2026, 5, 18),
        poc=Decimal(str(poc)),
        vah=Decimal(str(vah)),
        val=Decimal(str(val)),
        hvns=[Decimal(str(h)) for h in (hvns or [])],
        total_volume=1000,
    )


def test_long_inside_value_area_passes():
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("long", 1900, 1897), _profile(1900, 1905, 1895), Decimal("2.0")) is True


def test_long_within_tolerance_above_vah_passes():
    """Entry 1.0 above VAH is within tolerance=2.0 — must pass."""
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("long", 1906, 1903), _profile(1900, 1905, 1895), Decimal("2.0")) is True


def test_long_above_vah_plus_tolerance_rejected():
    """Entry 2.1 above VAH exceeds tolerance=2.0 — buying into clear resistance."""
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("long", 1907.1, 1904), _profile(1900, 1905, 1895), Decimal("2.0")) is False


def test_long_below_val_discount_zone_passes():
    """Entry below VAL on a long is a discount — should never be rejected."""
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("long", 1890, 1888), _profile(1900, 1905, 1895), Decimal("2.0")) is True


def test_short_inside_value_area_passes():
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("short", 1900, 1903), _profile(1900, 1905, 1895), Decimal("2.0")) is True


def test_short_above_vah_premium_zone_passes():
    """Short entry above VAH is a premium zone — should pass."""
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("short", 1910, 1913), _profile(1900, 1905, 1895), Decimal("2.0")) is True


def test_short_below_val_minus_tolerance_rejected():
    """Entry 2.1 below VAL — shorting into clear support below value."""
    from app.strategy.volume_profile import _filter
    assert _filter(_signal("short", 1892.9, 1896), _profile(1900, 1905, 1895), Decimal("2.0")) is False


# ── Task 5: Target selection + apply ─────────────────────────────────────────

def test_long_target_picks_nearest_vp_level_above_entry():
    """
    For a long, the nearest VP level above entry that clears min_r must win.
    POC at 1900 is only 2/3 R away (< 1.0R), so it's skipped.
    HVN at 1905 is 7/3 R — picked.
    """
    from app.strategy.volume_profile import _pick_target
    from app.bot_config import StrategyParams

    # entry=1898, stop=1895 → R=3
    prof = _profile(poc=1900, vah=1910, val=1890, hvns=[1905, 1915])
    cfg = StrategyParams(vp_min_target_r=Decimal("1.0"), r_multiple=Decimal("2.5"))
    sig = _signal("long", 1898, 1895)

    target, label = _pick_target(sig, prof, cfg)
    assert target == Decimal("1905")
    assert "HVN" in label


def test_long_target_fallback_when_no_level_clears_min_r():
    """
    If no VP level above entry delivers >= min_r, fall back to r_multiple.
    entry=1898, stop=1895, R=3, vah=1899 delivers only 1/3R < 1.0R → fallback.
    """
    from app.strategy.volume_profile import _pick_target
    from app.bot_config import StrategyParams

    prof = _profile(poc=1892, vah=1899, val=1888)
    cfg = StrategyParams(vp_min_target_r=Decimal("1.0"), r_multiple=Decimal("2.5"))
    sig = _signal("long", 1898, 1895)

    target, label = _pick_target(sig, prof, cfg)
    assert target == Decimal("1898") + Decimal("3") * Decimal("2.5")
    assert "no VP level" in label


def test_short_target_picks_nearest_vp_level_below_entry():
    """
    For a short, nearest level below entry that clears min_r.
    entry=1902, stop=1905, R=3. VAL=1895 is 7/3R ≥ 1.0R → picked.
    """
    from app.strategy.volume_profile import _pick_target
    from app.bot_config import StrategyParams

    prof = _profile(poc=1908, vah=1912, val=1895)
    cfg = StrategyParams(vp_min_target_r=Decimal("1.0"), r_multiple=Decimal("2.5"))
    sig = _signal("short", 1902, 1905)

    target, label = _pick_target(sig, prof, cfg)
    assert target == Decimal("1895")
    assert "VAL" in label


def test_apply_returns_none_when_filtered():
    """apply() must return None when the filter rejects the signal."""
    from app.strategy.volume_profile import VolumeProfileTracker, VolumeProfile

    tracker = VolumeProfileTracker()
    from app.bot_config import StrategyParams
    cfg = StrategyParams()

    # Inject a prior profile directly.
    tracker._prior = VolumeProfile(
        session_date=date(2026, 5, 18),
        poc=Decimal("1900"), vah=Decimal("1905"), val=Decimal("1895"),
        hvns=[], total_volume=1000,
    )

    # Long entry at 1910 — 5 above VAH (1905), tolerance 2.0 → rejected.
    sig = _signal("long", 1910, 1907)
    result = tracker.apply(sig, cfg)
    assert result is None


def test_apply_replaces_target_and_appends_rationale():
    """apply() must return a new Signal with VP-derived target and updated rationale."""
    from app.strategy.volume_profile import VolumeProfileTracker, VolumeProfile
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams(vp_min_target_r=Decimal("1.0"), r_multiple=Decimal("2.5"))
    tracker._prior = VolumeProfile(
        session_date=date(2026, 5, 18),
        poc=Decimal("1900"), vah=Decimal("1910"), val=Decimal("1890"),
        hvns=[Decimal("1905")], total_volume=1000,
    )

    # entry=1898, stop=1895, R=3. HVN=1905 is 7/3R ≥ 1.0 → target=1905.
    sig = _signal("long", 1898, 1895)
    result = tracker.apply(sig, cfg)

    assert result is not None
    assert result.target == Decimal("1905")
    assert "VP" in result.rationale
    assert result.rationale != sig.rationale  # rationale was updated


def test_apply_passthrough_when_no_prior_profile():
    """apply() must return the signal unchanged when no prior profile exists."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    assert not tracker.has_prior_profile()

    sig = _signal("long", 1900, 1897)
    result = tracker.apply(sig, StrategyParams())
    assert result is sig  # exact same object, untouched


def test_accumulate_conserves_total_volume():
    """Total volume in bins must equal bar.volume after accumulate — no leakage."""
    from app.strategy.volume_profile import VolumeProfileTracker
    from app.bot_config import StrategyParams

    tracker = VolumeProfileTracker()
    cfg = StrategyParams(vp_tick_size=Decimal("0.10"))

    # 100 volume across 8 bins (100 // 8 = 12 remainder 4 — tests remainder path)
    b = _bar(datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc),
             high=1900.7, low=1900.0, close=1900.3, volume=100)
    tracker.on_bar(b, cfg)
    assert sum(tracker._bins.values()) == 100
