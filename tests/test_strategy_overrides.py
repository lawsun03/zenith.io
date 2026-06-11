"""
Per-instrument strategy overrides (strategy_overrides on BotConfig).

Why these matter: an override that silently fails to apply means an
instrument trades with the wrong stop distance — real money placed at the
wrong price. Each test guards one link in the config → runner chain.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from app.bot_config import (
    BotConfig,
    StrategyParams,
    load_bot_config,
    save_bot_config,
    strategy_for,
)
from app.main import _build_runner


def _cfg_with_mnq_override() -> BotConfig:
    return BotConfig(
        instruments=["MGC", "MNQ", "MES"],
        strategy_overrides={
            "MNQ": {"stop_buffer": "3.0", "min_absolute_body": "5.0"},
        },
    )


def test_override_applies_only_to_named_instrument():
    cfg = _cfg_with_mnq_override()
    mnq = strategy_for(cfg, "MNQ")
    assert mnq.stop_buffer == Decimal("3.0")
    assert mnq.min_absolute_body == Decimal("5.0")
    # Non-overridden fields keep base values.
    assert mnq.r_multiple == cfg.strategy.r_multiple
    # Other instruments get the base params object untouched.
    assert strategy_for(cfg, "MGC") is cfg.strategy
    assert strategy_for(cfg, "MES") is cfg.strategy


def test_unknown_override_key_fails_loud():
    cfg = BotConfig(strategy_overrides={"MNQ": {"stop_bufer": "3.0"}})
    with pytest.raises(Exception):
        strategy_for(cfg, "MNQ")


def test_invalid_override_value_fails_loud():
    cfg = BotConfig(strategy_overrides={"MNQ": {"stop_buffer": "not-a-number"}})
    with pytest.raises(Exception):
        strategy_for(cfg, "MNQ")


def test_overrides_round_trip_through_save_load(tmp_path: Path):
    p = tmp_path / "cfg.json"
    save_bot_config(_cfg_with_mnq_override(), p)
    loaded = load_bot_config(p)
    assert loaded.strategy_overrides == {
        "MNQ": {"stop_buffer": "3.0", "min_absolute_body": "5.0"},
    }
    assert strategy_for(loaded, "MNQ").stop_buffer == Decimal("3.0")


def test_runner_built_with_merged_params_uses_override_stop_buffer():
    # The composer's stop_buffer is what actually positions the stop order —
    # if the merge doesn't reach it, the override is cosmetic.
    cfg = _cfg_with_mnq_override()
    mnq_runner = _build_runner("MNQ", strategy_for(cfg, "MNQ"))
    mgc_runner = _build_runner("MGC", strategy_for(cfg, "MGC"))
    assert mnq_runner.composer.config.stop_buffer == Decimal("3.0")
    assert mnq_runner.displacement.config.min_absolute_body == Decimal("5.0")
    assert mgc_runner.composer.config.stop_buffer == cfg.strategy.stop_buffer
    # Runner carries its own merged cfg (engine reads it per instrument).
    assert mnq_runner.strategy_cfg.stop_buffer == Decimal("3.0")
