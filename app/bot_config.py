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

    # iFVG entry configuration
    ifvg_entry_mode: str = "ifvg_edge"                        # "ifvg_edge" | "retrace_ce" | "close"
    ifvg_rule_f_enabled: bool = True                          # Rule F: cancel armed zone if TP1 hit before entry
    ifvg_stop_buffer_ticks: Decimal = Decimal("1.0")          # ticks beyond iFVG extreme for stop

    # iFVG grader configuration
    ifvg_sweep_window_bars: int = 10                   # bars since sweep for Rule A
    ifvg_min_displacement_mult: Decimal = Decimal("1.0")  # Fibonacci displacement quality (Rule E)
    # iFVG session / news filters (Rule G, H)
    ifvg_session_windows: list[str] = Field(
        default_factory=lambda: ["09:00-11:00", "02:00-05:00"],
    )
    ifvg_macro_windows: list[str] = Field(
        default_factory=lambda: ["08:30-09:10", "09:50-10:10", "10:50-11:10", "13:10-13:40", "15:15-15:45"],
    )
    ifvg_news_blackout: list[str] = Field(default_factory=list)  # UTC ISO ranges "YYYY-MM-DDTHH:MM/..."
    ifvg_tp1_fraction: Decimal = Decimal("0.5")   # fraction of position to close at structural TP1
    ifvg_be_after_tp1: bool = True                 # move stop to breakeven when structural TP1 fills

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

    # Grader "no structural target" gate: "reject" (cap B, original), "penalty"
    # (downgrade one notch so strong setups still trade), or "off" (ignore).
    target_clarity_mode: str = "reject"


class BotConfig(BaseModel):
    instrument: str | None = None          # None → fall back to TOPSTEP_BOT_INSTRUMENT env var
    instruments: list[str] = Field(default_factory=list)  # if non-empty, supersedes `instrument` for multi-symbol
    signal_instrument: str | None = None   # if set, subscribe to this instrument for signals; execute on `instrument`
    timeframes: list[str] | None = None    # None → fall back to TOPSTEP_BOT_TIMEFRAMES env var
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

    # --- Exit-coverage monitor (naked-position protection) ---
    emergency_stop_distance: dict[str, Decimal] = Field(
        default_factory=lambda: {
            "MGC": Decimal("3.0"),
            "MNQ": Decimal("40.0"),
            "MES": Decimal("5.0"),
        }
    )  # price points from broker avg entry for an emergency re-attached stop
    emergency_target_r: Decimal = Decimal("2.0")   # target dist = R × stop dist
    naked_grace_seconds: float = 15.0              # suppress fill→bracket race


def load_bot_config(path: Path) -> BotConfig:
    if not path.exists():
        return BotConfig()
    try:
        return BotConfig.model_validate(json.loads(path.read_text(encoding="utf-8-sig")))
    except Exception:
        return BotConfig()


def save_bot_config(config: BotConfig, path: Path) -> None:
    def _conv(v):
        return str(v) if isinstance(v, Decimal) else v

    data = {
        "instrument": config.instrument,
        "instruments": config.instruments,
        "signal_instrument": config.signal_instrument,
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
