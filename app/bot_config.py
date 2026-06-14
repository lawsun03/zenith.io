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
from typing import Any

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
    ifvg_zone_max_age_bars: int = 120                         # bars an armed zone stays valid; 0 = never expires

    # iFVG grader configuration
    ifvg_sweep_window_bars: int = 10                   # bars since sweep for Rule A
    ifvg_min_displacement_mult: Decimal = Decimal("1.0")  # Fibonacci displacement quality (Rule E)
    # iFVG session / news filters (Rule G, H)
    ifvg_macro_windows: list[str] = Field(default_factory=list)
    ifvg_news_blackout: list[str] = Field(default_factory=list)  # UTC ISO ranges "YYYY-MM-DDTHH:MM/..."
    ifvg_tp1_fraction: Decimal = Decimal("0.5")   # fraction of position to close at structural TP1
    ifvg_be_after_tp1: bool = True                 # move stop to breakeven when structural TP1 fills
    # Rule I: reject setups whose displacement printed 2+ overlapping same-side
    # FVGs ("gapping sack") unless a 30min FVG contains them. Trend legs print
    # exactly this pattern, so disabling allows with-trend continuation entries.
    ifvg_gapping_sack_enabled: bool = True

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

    # Minimum letter grade a setup must score to trade ("A".."F"; "F" = no floor).
    # The scorecard refactor decoupled passes from the letter grade — this
    # re-attaches a configurable floor. Default "F" = off.
    grader_min_grade: str = "F"

    # Stop placement: look back N bars and use min-low (long) / max-high (short)
    # as the stop anchor instead of the immediate sweep extreme. 0 = disabled
    # (current behavior: stop just past sweep_extreme).
    swing_stop_lookback: int = 0

    # Displacement body threshold uses the ATR from N bars ago (0 = off).
    # Anti-pro-cyclical: a flush inflates ATR exactly when the reversal
    # displacement prints; the lagged reference keeps the pre-flush bar.
    atr_ref_lag_bars: int = 0

    # Stop-width cap in ATR multiples (0 = off). When the swing-anchored stop
    # (swing_stop_lookback) sits further than max_stop_atr × ATR from entry,
    # fall back to the sweep-extreme anchor; if even that exceeds the cap,
    # skip the trade. ATR-relative on purpose — fixed-point caps were the
    # 2022-23 drought mechanism. Caps the target too (target = R × stop).
    max_stop_atr: Decimal = Decimal("0")

    # Ablation T1: restrict signal side. "both" (default) | "long" | "short".
    # String (not list) so the monthly harness's --set k=v override can type it.
    allowed_sides: str = "both"

    # Ablation T5: signal confirmation chain. "ifvg" (default) = displacement
    # bar must invert a prior FVG, entry at the iFVG zone. "displacement_only"
    # = sweep + opposite displacement bar suffices; entry at the confirmation-
    # bar close (the first actionable price — bar2's close would be lookahead).
    confirmation: str = "ifvg"

    # Engine selection: "ifvg" (default) | "orb" | "vwap" | "chop_breakout"
    # | "combined" | "regime_switch" | "sweep_bos". ORB = opening range
    # breakout. vwap = VWAP mean-reversion fades. chop_breakout = compression
    # → iFVG continuation. combined = iFVG + ORB simultaneously.
    # regime_switch = daily-range gate between ORB/iFVG. sweep_bos = sweep +
    # break-of-structure, no displacement/FVG leg (Revelio's simple chain).
    engine: str = "ifvg"
    orb_open_et: str = "09:30"            # "09:30" cash open | "08:30" data open
    orb_range_minutes: int = 15
    orb_r_multiple: Decimal = Decimal("2.0")
    orb_max_trades_per_day: int = 1
    orb_pdr_enabled: bool = False         # prior-day-range qualifier (default-off)
    orb_reentry_after_stop: bool = False  # re-arm detector once per day after a confirmed stop
    orb_long_only: bool = False           # suppress ORB short signals (funded PF improvement, B17)
    vwap_anchor_et: str = "09:30"         # "09:30" cash open | "18:00" futures day
    vwap_band_sigma: Decimal = Decimal("2.5")
    vwap_stop_sigma: Decimal = Decimal("1.5")

    # chop_breakout engine (all cb_*; spec: fixed defaults, NO sweeps)
    cb_regime_metric: str = "compression"      # "compression" | "vwap_cross"
    cb_compression_lookback: int = 20
    cb_compression_percentile: int = 30
    cb_history_window: int = 100
    cb_min_chop_bars: int = 12
    cb_vwap_cross_min: int = 6
    cb_entry_mode: str = "close"               # v1: "close" only (fail loud otherwise)
    cb_target_floor_r: Decimal = Decimal("1.5")
    cb_failed_breakout_bars: int = 6
    cb_vwap_invalidation: bool = True
    cb_sma21_trail: bool = False

    # regime_switch polarity diagnostic: False (default) = ORB on small-range
    # days / iFVG on large. True = inverted (ORB on large-range days).
    rs_invert: bool = False

    # Break-even trail: move the stop to entry (BE) the moment MFE exceeds
    # be_trail_r × initial stop distance. 0 = disabled (default).
    # Default-off; data-derived from B2 MFE/MAE analysis (ORB r2.5: winner p50
    # MFE 1.64R, loser MFE p90 1.29R → trial values: 1.0R and 1.5R).
    be_trail_r: Decimal = Decimal("0")

    # ATR-normalized displacement floor (B6). 0 = use fixed min_absolute_body.
    # >0 = threshold = bar_close * pct (price-era-normalized). Calibration:
    # 5.0pts / 21000 ≈ 0.000238 matches the deployed MNQ threshold at 2024+ prices;
    # at 12k NQ (2022) the effective floor drops to ~2.86pts instead of 5.0.
    min_absolute_body_pct: Decimal = Decimal("0")

    # ATR-normalized stop buffer (B6). 0 = use fixed stop_buffer.
    # >0 = buffer = stop_anchor * pct. Calibration: 3.0pts / 21000 ≈ 0.000143.
    stop_buffer_pct: Decimal = Decimal("0")

    # Inversion bar quality gate (B16): displacement bar body must be at least
    # this fraction of the planned stop distance. 0 = disabled (default).
    # 0.15 = only inversion bars with body >= 15% of stop distance fire.
    inversion_min_body_r: Decimal = Decimal("0")

    # B23: daily signal cap for iFVG engine. 0 = disabled (unlimited signals per day).
    # >0 = suppress signals once this many iFVG signals have been emitted on the ET calendar day.
    # Rank-1 signals (first of day, PF=1.129) carry the edge; rank-2+ signals drag (PF=0.970).
    ifvg_daily_signal_cap: int = 0

    # B30: day-of-week filter. Empty list = no suppression. Signal emission is suppressed
    # on these ET weekday names; detector state (sweeps, ranges) still accumulates.
    # Names match Python strftime("%A"): "Monday", "Tuesday", ..., "Sunday".
    # iFVG Tuesday PF=0.917 (loss-making); ORB Monday/Wednesday PF=0.898/0.942 (loss-making).
    skip_trading_days: list[str] = Field(default_factory=list)

    # B35: daily directional bias gate (ICT "Power of Three"). When True, suppresses
    # iFVG signals that oppose the prior ET-day's directional bias (close vs open) and
    # also suppresses signals when price has already consumed the prior day's target
    # (prior-day high for longs, prior-day low for shorts). Default off.
    daily_bias_gate_enabled: bool = False

    # B36: stop placement mode.
    # "swing" (default) = stop just past the sweep extreme (current behavior).
    # "fvg_mid" = stop at the FVG zone midpoint (tighter stop, target scaled by
    #   the new smaller r_distance × r_multiple).
    # "fvg_mid_abs" = FVG midpoint stop, but keep the original absolute target
    #   distance (so the tighter stop raises the effective R multiple).
    # Ignored (falls back to "swing") when no FVG zone is present (displacement_only).
    stop_mode: str = "swing"

    # B43: ORB late-session signal cutoff. 0 = disabled (no cutoff, existing behavior).
    # >0 = suppress new ORB signals at or after this many minutes post-open (open_et).
    # 60 = 10:30 ET cutoff; 90 = 11:00 ET cutoff. Existing open positions are unaffected
    # (cutoff only blocks new signal emission from on_bar). Range building continues.
    # Data: 10:30-11:30 ET ORB signals cost ~$2,900 over 5y (PF 0.622-0.963, n=84).
    orb_signal_window_mins: int = 0

    # B44: iFVG mid-session signal block by ET hour. Empty list = no blocking (default).
    # List of integer ET hours (0-23) to suppress iFVG signal emission. Sweep state
    # continues accumulating; only signal emission is gated. Example: [11, 12, 13]
    # blocks 11:00:00-13:59:59 ET. Data: 11-13 ET costs -$12,268 over 5y (PF<1 in 4/5 years).
    ifvg_block_hours: list[int] = Field(default_factory=list)

    # B55: ICT "Silver Bullet" hour gate. When True, iFVG signal emission is restricted to
    # 10:00-11:00 ET (the single highest-PF NY-AM hour per wk1-r2: PF=1.235). Sweep state
    # continues accumulating outside the window; only emission is gated (same pattern as
    # ifvg_block_hours). Volume: ~5-10 signals/month — too sparse for Combine; route to funded.
    silver_bullet_only: bool = False

    # B48: hybrid rank-aware short filter. 0 = disabled (all shorts allowed per allowed_sides).
    # 1 = allow only rank-1 short per ET calendar day (rank-1 PF positive; rank-2+ PF=0.858).
    # Rank-1 = first short signal emitted on the ET day; rank-2+ = subsequent shorts.
    # Long signals unaffected. Complementary to allowed_sides.
    ifvg_max_short_rank: int = 0

    # B47: iFVG×ORB directional confluence gate. When True (engine=combined only),
    # Gate 1 suppresses ORB signals when all prior same-day iFVG signals oppose the
    # ORB direction (ORB opp-only PF=0.957, loss-making); Gate 2 suppresses post-ORB
    # iFVG signals that oppose the ORB direction (orb_opp PF=0.757, loss-making in 4/5 years).
    # Data: ORB same-dir PF=1.689 vs opp-dir PF=0.957; iFVG same-dir PF=1.375 vs opp-dir PF=0.757.
    ifvg_orb_confluence_gate: bool = False
    # B56: suppress ORB when no prior same-direction iFVG has fired today.
    # Groups A+D (PF=1.427) allowed; groups B+C (PF=0.963) suppressed.
    # Phase-1 GO: A+D/B+C ratio 1.48x > 1.4x threshold, consistent across 5/5 years.
    orb_ifvg_alignment_required: bool = False

    # B58: per-signal sizing policy. "constant" = unchanged (default). "confluence" =
    # scale base size by a fixed ladder: count>=3 → 1.5x, count==2 → 1.0x, count<=1 → 0.5x.
    # Count = validated-edge features that align: long side, rank-1, Silver Bullet hour,
    # combined-engine context. Cap at max_contracts. All signals still taken (additive, no gate).
    risk_policy: str = "constant"

    # B59: sweep_reentry overlay — long-only session-low/prior-day-low reentry micro-engine.
    # Minimum sweep depth below the reference level (session_low or prior_day_low) in ATR units.
    # 0.25 = bar must dip at least 0.25 × ATR below the level to qualify as a sweep.
    # Default-off (engine="sweep_reentry" must be explicitly selected).
    sweep_reentry_depth_atr: Decimal = Decimal("0.25")

    # B81: suppress same-direction iFVG repeats within the same ET calendar day.
    # When True: if the first iFVG signal of the day was LONG, subsequent LONG signals
    # that day are suppressed (short→long conflicts are allowed). Short→short repeats
    # are also suppressed. Only the MOST RECENT direction emitted is tracked.
    # Phase 1 data: continuation PF=0.785 (5/5 years losing vs conflict PF=1.052).
    # ORB signals are never gated by this flag.
    ifvg_suppress_same_direction_repeat: bool = False


class BotConfig(BaseModel):
    instrument: str | None = None          # None → fall back to TOPSTEP_BOT_INSTRUMENT env var
    instruments: list[str] = Field(default_factory=list)  # if non-empty, supersedes `instrument` for multi-symbol
    signal_instrument: str | None = None   # if set, subscribe to this instrument for signals; execute on `instrument`
    timeframes: list[str] | None = None    # None → fall back to TOPSTEP_BOT_TIMEFRAMES env var
    replay_delay_ms: int = 0        # ms to sleep between bars in paper replay (0 = full speed)
    replay_start_delay_s: int = 5   # seconds to wait before replay begins (lets browser connect)
    account_name: str | None = None  # live mode: TopstepX account name to trade on
    entry_mode: str = "market"      # "market" or "limit" (custom limit+bracket-after-fill)
    # Mid-bar entries via the forming-bar poller. False (default) = the b3
    # confirmation bar must CLOSE before entry — the only behavior the
    # backtest/walk-forward validates. True = enter the moment the forming
    # bar satisfies the inversion price (touch, not close) — unvalidated.
    forming_bar_entries: bool = False
    contracts: int = 1              # number of contracts per signal
    risk_per_trade_pct: Decimal = Decimal("0.25")  # 0 = disabled (use fixed contracts); else % of equity risked per trade
    partial_profit_r: Decimal = Decimal("0")  # 0 = disabled; e.g. 1.5 = take half at 1.5R then move stop to break-even (BE-only for 1-lots)
    # Slippage guard (abort mode): if a market entry fills more than this
    # fraction of the planned stop distance beyond the signal entry, the fill's
    # geometry is broken (fill-relative stop would sit inside the retrace zone)
    # — flatten immediately instead of placing brackets. 0 = disabled.
    max_entry_slippage_frac: Decimal = Decimal("0")
    # Topstep flatten rule: must be flat by 3:10 PM CT (4:10 PM ET).
    # flatten_time_ct = hard-flatten time with buffer; entry_cutoff_time_ct =
    # no new entries after this. Strings "HH:MM" in America/Chicago local time.
    flatten_enabled: bool = True
    flatten_time_ct: str = "15:05"
    entry_cutoff_time_ct: str = "14:30"
    enabled_killzones: list[str] = Field(
        default_factory=lambda: ["london", "ny_am", "ny_pm"],
    )
    strategy: StrategyParams = Field(default_factory=StrategyParams)
    # Per-instrument partial overrides of `strategy`, keyed by symbol, e.g.
    # {"MNQ": {"stop_buffer": "3.0"}}. Merged via strategy_for(); unknown keys
    # or bad values fail validation there (fail loud, not silently inert).
    # Scope: runner-level fields (composer/displacement/liquidity/grader/iFVG).
    # Engine-level confluence fields (vp_*, htf_*, ifvg_tp1_*) stay global —
    # the engine reads them from its own strategy_cfg for hot-apply.
    strategy_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)

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
    commission_per_contract: float = 0.0           # deducted from realized P&L on every fill (per side)
    max_contracts_override: int | None = None     # hard cap on risk-sized contracts (None = use account limit)

    # Funded-pipeline phase: "practice" = current behavior (default).
    # Rule numbers live here, not in code — Topstep changes them often.
    account_phase: str = "practice"
    phase_rules: dict = Field(default_factory=dict)
    # Shadow mode: run the phase rules from the configured starting balance,
    # IGNORING the broker balance at startup. Lets a Combine dry-run trade on
    # the practice account (whose real balance is unrelated — reconciling it
    # would instantly trip the stop-at-target gate).
    phase_shadow: bool = False


def strategy_for(config: BotConfig, instrument: str) -> StrategyParams:
    """
    StrategyParams for one instrument: base `strategy` with that instrument's
    `strategy_overrides` entry merged on top. Re-validates through the model so
    a typo'd field name or invalid value raises instead of being ignored.
    """
    overrides = config.strategy_overrides.get(instrument)
    if not overrides:
        return config.strategy
    unknown = set(overrides) - set(StrategyParams.model_fields)
    if unknown:
        raise ValueError(
            f"strategy_overrides[{instrument!r}] has unknown fields: {sorted(unknown)}"
        )
    return StrategyParams(**{**config.strategy.model_dump(), **overrides})


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
        "forming_bar_entries": config.forming_bar_entries,
        "contracts": config.contracts,
        "risk_per_trade_pct": _conv(config.risk_per_trade_pct),
        "partial_profit_r": _conv(config.partial_profit_r),
        "max_entry_slippage_frac": _conv(config.max_entry_slippage_frac),
        "flatten_enabled": config.flatten_enabled,
        "flatten_time_ct": config.flatten_time_ct,
        "entry_cutoff_time_ct": config.entry_cutoff_time_ct,
        "enabled_killzones": config.enabled_killzones,
        "strategy": {k: _conv(v) for k, v in config.strategy.model_dump().items()},
        "strategy_overrides": {
            inst: {k: _conv(v) for k, v in ov.items()}
            for inst, ov in config.strategy_overrides.items()
        },
        "emergency_stop_distance": {k: _conv(v) for k, v in config.emergency_stop_distance.items()},
        "emergency_target_r": _conv(config.emergency_target_r),
        "naked_grace_seconds": config.naked_grace_seconds,
        "max_contracts_override": config.max_contracts_override,
        "account_phase": config.account_phase,
        "phase_rules": config.phase_rules,
        "phase_shadow": config.phase_shadow,
    }
    path.write_text(json.dumps(data, indent=2))
