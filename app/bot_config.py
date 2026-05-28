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
    trend_ema_period: int = 50  # 0 = disabled; N = only take signals with the N-bar EMA trend
    min_atr_filter: Decimal = Decimal("0")  # 0 = no floor; N = require ATR >= N before entering
    max_atr_filter: Decimal = Decimal("0")  # 0 = no ceiling; N = require ATR <= N before entering
    cooldown_bars_after_stop: int = 0       # 0 = disabled; N = bars to suppress signals after a stop
    min_penetration_atr_factor: Decimal = Decimal("0")  # 0 = use fixed min_penetration; >0 = factor × ATR

    # Volume profile filter + target
    vp_enabled: bool = True
    vp_tick_size: Decimal = Decimal("0.10")       # price quantization for bins
    vp_value_area_pct: float = 0.70               # fraction of volume defining value area
    vp_filter_tolerance: Decimal = Decimal("2.0") # price units outside VA edge still accepted
    vp_hvn_threshold: float = 1.5                 # volume × mean to qualify as HVN
    vp_min_target_r: Decimal = Decimal("1.0")     # minimum R a VP level must deliver as target

    # Higher-timeframe confluence (both default off → no behavior change)
    htf_bias_enabled: bool = False          # Part A: 4h swing-structure bias gate
    htf_bias_timeframe: str = "4h"          # timeframe for bias (NOTE: "4h" not "4hr")
    htf_bias_lookback: int = 3              # swing lookback on the bias timeframe
    htf_target_enabled: bool = False        # Part B: HTF target selection
    htf_target_min_r: Decimal = Decimal("2.0")  # min R an HTF level must deliver
    htf_swing_timeframe: str = "30min"      # fallback swing-target timeframe
    kz_levels_enabled: bool = True          # sweep KZ session H/L levels in parallel with swing sweeps


class BotConfig(BaseModel):
    instrument: str | None = None   # None → fall back to TOPSTEP_BOT_INSTRUMENT env var
    timeframes: list[str] | None = None  # None → fall back to TOPSTEP_BOT_TIMEFRAMES env var
    replay_delay_ms: int = 0        # ms to sleep between bars in paper replay (0 = full speed)
    replay_start_delay_s: int = 5   # seconds to wait before replay begins (lets browser connect)
    account_name: str | None = None  # live mode: TopstepX account name to trade on
    entry_mode: str = "market"      # "market" or "limit" (custom limit+bracket-after-fill)
    contracts: int = 1              # number of contracts per signal
    risk_per_trade_pct: Decimal = Decimal("0.25")  # 0 = disabled (use fixed contracts); else % of equity risked per trade
    partial_profit_r: Decimal = Decimal("0")  # 0 = disabled; e.g. 1.5 = take half at 1.5R then move stop to break-even (BE-only for 1-lots)
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
        "contracts": config.contracts,
        "risk_per_trade_pct": _conv(config.risk_per_trade_pct),
        "partial_profit_r": _conv(config.partial_profit_r),
        "enabled_killzones": config.enabled_killzones,
        "strategy": {k: _conv(v) for k, v in config.strategy.model_dump().items()},
    }
    path.write_text(json.dumps(data, indent=2))
