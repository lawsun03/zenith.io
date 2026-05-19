"""Tests for volume profile: config, profile construction, filter, target."""
from decimal import Decimal
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
