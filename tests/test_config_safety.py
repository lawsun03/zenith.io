"""Config-safety regressions (review findings #7, #11): fail loud on a bad config /
unknown engine instead of silently running the wrong strategy, and persist commission
so it doesn't reset to $0 live."""
import pytest

from app.bot_config import BotConfig, StrategyParams, load_bot_config, save_bot_config


def test_load_bot_config_strict_raises_on_bad_json(tmp_path):
    # WHY (#7): a corrupt config at STARTUP must fail loud, not silently run defaults
    # (a different strategy for a whole session).
    bad = tmp_path / "bot_config.json"
    bad.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(Exception):
        load_bot_config(bad, strict=True)
    # Runtime (non-strict) callers fall back loudly but don't crash the running bot.
    assert isinstance(load_bot_config(bad, strict=False), BotConfig)


def test_build_runner_raises_on_unknown_engine():
    # WHY (#7): an unknown engine must NOT silently fall through to iFVG.
    from app.main import _build_runner
    with pytest.raises(ValueError):
        _build_runner("MNQ", StrategyParams(engine="totally_bogus"), None, "5min", None)
    # The real default (ifvg) still builds.
    r = _build_runner("MNQ", StrategyParams(engine="ifvg"), None, "5min", None)
    assert r.instrument == "MNQ"


def test_save_load_roundtrips_commission(tmp_path):
    # WHY (#11): save_bot_config used to DROP commission_per_contract -> it reset to $0 on
    # reload (optimistic MLL marks). It must survive a round-trip.
    cfg = BotConfig(commission_per_contract=0.57)
    p = tmp_path / "bot_config.json"
    save_bot_config(cfg, p)
    assert load_bot_config(p).commission_per_contract == 0.57
