"""
Mutable bot configuration — persisted to bot_config.json.

Separate from AppConfig (which is env-only and frozen at startup).
This file owns: instrument, timeframes, and all strategy parameters.

The JSON file is read at startup and written by the dashboard API.
Changes take effect on the next run.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, Field


class StrategyParams(BaseModel):
    swing_lookback: int = 2
    min_penetration: Decimal = Decimal("0.20")
    multi_bar_window: int = 3
    atr_period: int = 14
    body_atr_multiple: Decimal = Decimal("1.0")
    min_body_to_range_ratio: Decimal = Decimal("0.6")
    min_absolute_body: Decimal = Decimal("1.0")
    displacement_window_bars: int = 5
    stop_buffer: Decimal = Decimal("0.30")
    r_multiple: Decimal = Decimal("2.5")


class BotConfig(BaseModel):
    instrument: str | None = None   # None → fall back to TOPSTEP_BOT_INSTRUMENT env var
    timeframes: list[str] | None = None  # None → fall back to TOPSTEP_BOT_TIMEFRAMES env var
    replay_delay_ms: int = 0        # ms to sleep between bars in paper replay (0 = full speed)
    replay_start_delay_s: int = 5   # seconds to wait before replay begins (lets browser connect)
    account_name: str | None = None  # live mode: TopstepX account name to trade on
    entry_mode: str = "market"      # "market" or "limit" (custom limit+bracket-after-fill)
    enabled_killzones: list[str] = Field(
        default_factory=lambda: ["london", "ny_am", "ny_pm"],
    )
    strategy: StrategyParams = Field(default_factory=StrategyParams)


def load_bot_config(path: Path) -> BotConfig:
    if not path.exists():
        return BotConfig()
    try:
        return BotConfig.model_validate(json.loads(path.read_text()))
    except Exception:
        return BotConfig()


def save_bot_config(config: BotConfig, path: Path) -> None:
    def _conv(v):
        return str(v) if isinstance(v, Decimal) else v

    data = {
        "instrument": config.instrument,
        "timeframes": config.timeframes,
        "replay_delay_ms": config.replay_delay_ms,
        "replay_start_delay_s": config.replay_start_delay_s,
        "account_name": config.account_name,
        "entry_mode": config.entry_mode,
        "enabled_killzones": config.enabled_killzones,
        "strategy": {k: _conv(v) for k, v in config.strategy.model_dump().items()},
    }
    path.write_text(json.dumps(data, indent=2))
