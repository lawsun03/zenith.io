from app.bot_config import StrategyParams

def test_sweep_level_config_defaults_off():
    sp = StrategyParams()
    assert sp.sweep_levels_tag_enabled is False
    assert sp.sweep_levels_tag_tolerance_ticks == 4
