export interface StrategyConfig {
  swing_lookback: number
  min_penetration: string
  multi_bar_window: number
  atr_period: number
  body_atr_multiple: string
  min_body_to_range_ratio: string
  min_absolute_body: string
  displacement_window_bars: number
  stop_buffer: string
  r_multiple: string
  trend_ema_period: number
  min_atr_filter: string   // "0" = disabled
  max_atr_filter: string   // "0" = disabled
  cooldown_bars_after_stop: number
  min_penetration_atr_factor: string  // "0" = disabled; >0 = factor × ATR
  // Volume profile
  vp_enabled: boolean
  vp_tick_size: string
  vp_value_area_pct: number
  vp_filter_tolerance: string
  vp_hvn_threshold: number
  vp_min_target_r: string
  // HTF confluence
  htf_bias_enabled: boolean
  htf_bias_timeframe: string
  htf_bias_lookback: number
  htf_target_enabled: boolean
  htf_target_min_r: string
  htf_swing_timeframe: string
}

export interface BotConfig {
  instrument: string
  timeframes: string[]
  replay_delay_ms: number
  replay_start_delay_s: number
  account_name: string | null
  entry_mode: string
  enabled_killzones: string[]
  signal_instrument: string | null
  contracts: number
  risk_per_trade_pct: number
  partial_profit_r: number
  mode?: string
  strategy: StrategyConfig
}

export interface StatusPayload {
  now: string
  account: {
    type: string
    size: number
    starting_balance: string
    daily_loss_limit: string
    max_contracts: number
    soft_buffer: string
  }
  equity: {
    current: string
    high_water: string
    realized_balance: string
  }
  limits: {
    mll_floor: string
    buffer_to_mll: string
    buffer_to_dll: string
    daily_pnl: string
    open_contracts: number
    mll_locked_at_starting_balance: boolean
  }
  lockout: { code: string; message: string } | null
  sync: { pending: number; poisoned: number; sent: number } | null
}

export interface SignalPayload {
  instrument: string
  side: 'long' | 'short'
  entry: string
  stop: string
  target: string
  killzone: string
  rationale: string
  outcome: { placed: boolean; reason: string; allowed_size: number }
}

export interface FillPayload {
  instrument: string
  side: string
  fill_price: string
  size: number
  is_entry: boolean
  realized_pnl_delta: string
}

export interface ReconcilePayload {
  broker_open_contracts: number
  internal_open_contracts: number
  drift_detected: boolean
  drift_kind: string | null
  notes: string
}

export interface JournalItem {
  ts: string
  kind: string
  payload: SignalPayload | FillPayload | ReconcilePayload
}

export interface BarEvent {
  time: number   // Unix seconds (UTCTimestamp for lightweight-charts)
  open: number
  high: number
  low: number
  close: number
}

export interface VpProfile {
  session_date: string
  poc: string
  vah: string
  val: string
  hvns: string[]
  total_volume: number
  bins: [string, number][]  // [price, volume] pairs sorted ascending
}
