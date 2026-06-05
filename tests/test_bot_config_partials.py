from decimal import Decimal
from pathlib import Path

from app.bot_config import BotConfig, load_bot_config, save_bot_config


def test_partial_profit_r_default_disabled():
    assert BotConfig().partial_profit_r == Decimal("0")


def test_partial_profit_r_round_trip(tmp_path: Path):
    p = tmp_path / "cfg.json"
    save_bot_config(BotConfig(partial_profit_r=Decimal("1.5")), p)
    assert load_bot_config(p).partial_profit_r == Decimal("1.5")
    # Ensure it is serialized as a string (Decimal-safe), not a float.
    assert '"partial_profit_r": "1.5"' in p.read_text()
