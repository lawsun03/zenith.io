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
