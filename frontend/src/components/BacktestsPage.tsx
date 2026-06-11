import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Logo } from './Logo'
import { createChart, LineSeries } from 'lightweight-charts'
import { fmtBarTs } from '../utils/format'
import { useConfirm } from '../hooks/useConfirm'

interface KillzoneStat {
  trades: number
  wins: number
  losses: number
  win_rate: number
  net_pnl: number
}

interface BacktestStats {
  trades: number
  wins: number
  losses: number
  win_rate: number
  net_pnl: string
  gross_win: string
  gross_loss: string
  avg_win: string
  avg_loss: string
  profit_factor: number | null
  max_drawdown: string
  expectancy?: string
  is_profitable?: boolean
  passed_combine?: boolean
  mll_breached?: boolean
  by_killzone?: Record<string, KillzoneStat>
  equity_curve?: [string, string][]
}

interface BacktestSummary {
  id: string
  label: string
  completed_at: string | null
  instrument: string
  timeframe: string
  start_date?: string | null
  end_date?: string | null
  bars_processed: number
  ending_balance: string
  stats: BacktestStats
  bookmarked?: boolean
}

interface GradeCriteria {
  mom: boolean
  tgt: boolean
  fvg: boolean
  pd: boolean
  del: boolean
}

interface Trade {
  entry_ts: string
  exit_ts: string
  side: string
  entry_price: string
  exit_price: string
  size: number
  realized_pnl: string
  hold_seconds?: number
  grade?: string
  criteria?: GradeCriteria
}

const GRADE_TIERS = ['A', 'B', 'C', 'D', 'F'] as const
type GradeTier = typeof GRADE_TIERS[number]

const CRITERIA_KEYS: Array<keyof GradeCriteria> = ['mom', 'tgt', 'fvg', 'pd', 'del']

interface StrategyParams {
  swing_lookback: number | string
  min_penetration: string
  multi_bar_window: number | string
  atr_period: number | string
  body_atr_multiple: string
  min_body_to_range_ratio: string
  min_absolute_body: string
  displacement_window_bars: number | string
  stop_buffer: string
  r_multiple: string
}

interface BacktestConfig {
  instrument: string | null
  timeframes: string[] | null
  replay_delay_ms?: number
  replay_start_delay_s?: number
  account_name?: string | null
  enabled_killzones?: string[]
  strategy: StrategyParams
}

interface BacktestDetail extends BacktestSummary {
  config: BacktestConfig
  starting_balance: string
  duration_seconds: number
  trades: Trade[]
  signals: unknown[]
  fills: unknown[]
  note?: string
}

const PARAM_LABELS: Record<string, string> = {
  swing_lookback: 'Swing Lookback',
  min_penetration: 'Min Penetration ($)',
  multi_bar_window: 'Multi-Bar Window',
  atr_period: 'ATR Period',
  body_atr_multiple: 'Body ATR Multiple',
  min_body_to_range_ratio: 'Body / Range Ratio',
  min_absolute_body: 'Min Body ($)',
  displacement_window_bars: 'Displacement Window',
  stop_buffer: 'Stop Buffer ($)',
  r_multiple: 'R Multiple',
  ifvg_entry_mode: 'iFVG Entry Mode',
  ifvg_rule_f_enabled: 'Rule F',
  target_clarity_mode: 'Target-Clarity Gate',
  htf_bias_enabled: 'HTF Bias Gate',
  htf_target_enabled: 'HTF Target Selection',
  trend_ema_period: 'Trend EMA Filter',
  vp_enabled: 'VP Filter',
  ifvg_macro_blackouts_enabled: 'Macro Blackouts',
  min_atr_filter: 'Min ATR Filter',
  max_atr_filter: 'Max ATR Filter',
  cooldown_bars_after_stop: 'Cooldown After Stop',
  min_penetration_atr_factor: 'Penetration ATR Factor',
  ifvg_stop_buffer_ticks: 'iFVG Stop Buffer (ticks)',
  ifvg_zone_max_age_bars: 'Zone Max Age (bars)',
  ifvg_sweep_window_bars: 'Sweep Window (bars)',
  ifvg_min_displacement_mult: 'Min Displacement Mult',
  ifvg_tp1_fraction: 'TP1 Fraction',
  ifvg_be_after_tp1: 'BE After TP1',
  ifvg_news_blackout: 'News Blackout',
  vp_tick_size: 'VP Tick Size',
  vp_value_area_pct: 'VP Value Area %',
  vp_filter_tolerance: 'VP Filter Tolerance',
  vp_hvn_threshold: 'VP HVN Threshold',
  vp_min_target_r: 'VP Min Target R',
  htf_bias_timeframe: 'HTF Bias Timeframe',
  htf_bias_lookback: 'HTF Bias Lookback',
  htf_target_min_r: 'HTF Target Min R',
  htf_swing_timeframe: 'HTF Swing Timeframe',
}

const TIMEFRAMES = ['1min', '3min', '5min', '15min', '30min', '1h']
const DATE_PRESETS: Array<{ label: string; days: number }> = [
  { label: '7d',  days: 7 },
  { label: '14d', days: 14 },
  { label: '30d', days: 30 },
  { label: 'Max', days: 0 },  // 0 = use availability.earliest
]

function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10)
}

function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return isoDate(d)
}

// Older saved runs predate start_date/end_date persistence; show "—" for them.
function fmtRange(start?: string | null, end?: string | null): string {
  if (!start || !end) return '—'
  return `${start} → ${end}`
}

interface Availability {
  earliest: string | null
  latest: string | null
  bars: number
  available: boolean
  reason?: string
  timeframe?: string
}

type StrategySection =
  | 'Entry / Signal'
  | 'Risk & Sizing'
  | 'Take-Profit'
  | 'Killzones'
  | 'Structure / IFVG'

const SECTION_ORDER: StrategySection[] = [
  'Entry / Signal',
  'Risk & Sizing',
  'Take-Profit',
  'Killzones',
  'Structure / IFVG',
]

interface StrategyField {
  key: string
  label: string
  section: StrategySection
  min?: number
  max?: number
  step?: number
  hint: string
  kind?: 'number' | 'select' | 'toggle' | 'list'  // default 'number'
  options?: string[]                               // for kind 'select'
}

const STRATEGY_FIELDS: StrategyField[] = [
  {
    key: 'swing_lookback', label: 'Swing Lookback', section: 'Entry / Signal', min: 1, max: 20, step: 1,
    hint: 'How many bars on each side must be lower (or higher) for the bot to call a price a "swing low" (or "swing high"). Lower = more swings, including small wiggles. Higher = only major levels. Try: 2 for active trading, 5+ for slower setups.',
  },
  {
    key: 'min_penetration', label: 'Min Penetration ($)', section: 'Entry / Signal', min: 0, max: 5, step: 0.05,
    hint: 'How far past a swing level price must move to count as a real sweep (stop hunt). Filters out tiny one-tick wicks. Try: 0.20 for MGC (~2 ticks). Raise it if the bot is firing on noise.',
  },
  {
    key: 'multi_bar_window', label: 'Multi-Bar Window', section: 'Entry / Signal', min: 1, max: 20, step: 1,
    hint: 'How many bars a slow stop-hunt can span before we stop calling it a sweep. 1 = single-bar sweeps only. Higher = catches grinds that take 3–5 bars to penetrate a level.',
  },
  {
    key: 'atr_period', label: 'ATR Period', section: 'Entry / Signal', min: 5, max: 50, step: 1,
    hint: 'How many bars to average for "what does normal range look like right now?". Shorter (5–10) reacts faster to changing volatility; longer (20+) smooths things out. 14 is standard.',
  },
  {
    key: 'body_atr_multiple', label: 'Body ATR Multiple', section: 'Entry / Signal', min: 0, max: 5, step: 0.1,
    hint: 'How big the trigger candle\'s body must be relative to recent ATR. 1.0 = body ≥ 1× ATR (normal impulse). 2.0 = need a strong move. Higher = stricter, fewer signals.',
  },
  {
    key: 'min_body_to_range_ratio', label: 'Body / Range Ratio', section: 'Entry / Signal', min: 0, max: 1, step: 0.05,
    hint: 'How "solid" the trigger candle must be: body length ÷ full high-to-low range. 0.6 = body fills 60% of the candle. Filters out doji/indecision wicks. Higher = only big-bodied moves.',
  },
  {
    key: 'min_absolute_body', label: 'Min Body ($)', section: 'Entry / Signal', min: 0, max: 10, step: 0.1,
    hint: 'Hard floor on the trigger candle\'s body size in dollars. Catches cases where ATR is tiny (overnight chop) but the relative ratio still passes. Try: 1.0 for MGC.',
  },
  {
    key: 'displacement_window_bars', label: 'Displacement Window', section: 'Entry / Signal', min: 1, max: 20, step: 1,
    hint: 'After a sweep happens, how many bars to wait for a strong move (displacement) in the opposite direction. If nothing impulsive shows up in that window, the setup expires.',
  },
  {
    key: 'stop_buffer', label: 'Stop Buffer ($)', section: 'Risk & Sizing', min: 0, max: 5, step: 0.05,
    hint: 'Extra dollars added beyond the sweep extreme when placing the stop. Bigger buffer = wider stops, fewer stop-outs from wick noise, but worse risk/reward. Try: 0.30 for MGC.',
  },
  {
    key: 'r_multiple', label: 'R Multiple', section: 'Take-Profit', min: 0.5, max: 10, step: 0.1,
    hint: 'Reward-to-risk ratio: target distance ÷ stop distance. 2.0 = risk $50 to make $100. Higher targets = more profit per win but lower win rate. 2.0–3.0 is a common sweet spot.',
  },
  {
    key: 'ifvg_entry_mode', label: 'iFVG Entry Mode', section: 'Entry / Signal', kind: 'select',
    options: ['ifvg_edge', 'retrace_ce', 'close'],
    hint: 'How to enter once a setup is graded. ifvg_edge/retrace_ce arm a zone and wait for price to retrace to the FVG edge / center (often missed in trends). close = enter immediately — far more trades, rides trends, higher variance.',
  },
  {
    key: 'ifvg_rule_f_enabled', label: 'Rule F (premature-liq cancel)', section: 'Entry / Signal', kind: 'toggle',
    hint: 'When ON, cancels an armed zone if the target is hit before the entry fills. In trends this voids the with-trend setups that work, so turning it OFF was the single biggest backtest improvement. (No effect in "close" mode — close never arms.)',
  },
  {
    key: 'target_clarity_mode', label: 'Target-Clarity Gate', section: 'Take-Profit', kind: 'select',
    options: ['reject', 'penalty', 'off'],
    hint: 'What to do when a setup has no structural target near an HTF swing. reject = drop it (cut ~85% of candidates). penalty = downgrade one grade notch. off = ignore the gate entirely (most trades).',
  },
  {
    key: 'htf_bias_enabled', label: 'HTF Bias Gate', section: 'Structure / IFVG', kind: 'toggle',
    hint: 'When ON, only takes setups that align with the 4h swing-structure bias (bullish = longs only, bearish = shorts only). Cuts trade count significantly but improves with-trend quality.',
  },
  {
    key: 'htf_target_enabled', label: 'HTF Target Selection', section: 'Take-Profit', kind: 'toggle',
    hint: 'When ON, uses HTF swing levels as targets instead of a fixed R multiple. Requires HTF structure to be built from replay bars — adds latency to the first few signals.',
  },
  {
    key: 'trend_ema_period', label: 'Trend EMA Filter', section: 'Entry / Signal', min: 0, max: 200, step: 1,
    hint: '0 = disabled (take all setups, both directions). N = only take signals aligned with the N-bar EMA trend. Your live config is 50 — this alone drops counter-trend setups. Set to 0 to see the full picture.',
  },
  {
    key: 'ifvg_macro_blackouts_enabled', label: 'Macro Blackouts', section: 'Killzones', kind: 'toggle',
    hint: 'When ON, blocks entries during economic release windows (08:30–09:10, 09:50–10:10, 10:50–11:10, 13:10–13:40, 15:15–15:45 ET). Turn OFF to trade through news windows.',
  },
  {
    key: 'min_atr_filter', label: 'Min ATR Filter', section: 'Entry / Signal', min: 0, max: 20, step: 0.1,
    hint: '0 = disabled. N = skip setups when the current ATR is below N (avoids entering in dead chop). Useful if your strategy fires junk signals during low-volatility overnight hours.',
  },
  {
    key: 'max_atr_filter', label: 'Max ATR Filter', section: 'Entry / Signal', min: 0, max: 50, step: 0.5,
    hint: '0 = disabled. N = skip setups when ATR exceeds N (avoids blowout moves where your stop math breaks down). Useful when news spikes inflate ATR well beyond normal range.',
  },
  {
    key: 'cooldown_bars_after_stop', label: 'Cooldown After Stop', section: 'Entry / Signal', min: 0, max: 20, step: 1,
    hint: '0 = disabled. N = suppress new signals for N bars after taking a stop-loss. Prevents immediately re-entering into the same adverse move.',
  },
  {
    key: 'min_penetration_atr_factor', label: 'Penetration ATR Factor', section: 'Entry / Signal', min: 0, max: 3, step: 0.05,
    hint: '0 = use the fixed Min Penetration ($) value. >0 = scale the required penetration by factor × ATR, so it tightens in quiet markets and widens in volatile ones.',
  },
  {
    key: 'ifvg_stop_buffer_ticks', label: 'iFVG Stop Buffer (ticks)', section: 'Risk & Sizing', min: 0, max: 10, step: 0.5,
    hint: 'Extra ticks beyond the iFVG extreme when placing the stop. Larger = wider stop, less noise-stopped, worse R/R.',
  },
  {
    key: 'ifvg_zone_max_age_bars', label: 'Zone Max Age (bars)', section: 'Structure / IFVG', min: 0, max: 480, step: 10,
    hint: 'Bars an armed zone stays valid before expiring unfilled. 0 = never expires. Prevents entries on days-old structure when price returns to a stale zone.',
  },
  {
    key: 'ifvg_sweep_window_bars', label: 'Sweep Window (bars)', section: 'Structure / IFVG', min: 1, max: 30, step: 1,
    hint: 'Rule A: how many bars back we look for a prior swing sweep before the iFVG formed. Larger window = more setups qualify; smaller = only recent, "clean" sweeps pass.',
  },
  {
    key: 'ifvg_min_displacement_mult', label: 'Min Displacement Mult', section: 'Structure / IFVG', min: 0, max: 3, step: 0.1,
    hint: 'Rule E: the displacement candle must be ≥ N × ATR to qualify. 1.0 = needs a full ATR-sized move. Lower = more setups but includes weak displacements. 0 = off.',
  },
  {
    key: 'ifvg_tp1_fraction', label: 'TP1 Fraction', section: 'Take-Profit', min: 0, max: 1, step: 0.05,
    hint: 'Fraction of position to close at the structural TP1 level (e.g. 0.5 = close half). Only relevant with multi-contract sizing; BE-only for 1-lots.',
  },
  {
    key: 'ifvg_be_after_tp1', label: 'BE After TP1', section: 'Take-Profit', kind: 'toggle',
    hint: 'When ON, moves stop to break-even after TP1 fills. Protects profits on the runner but reduces final win size on strong moves. With partials disabled, has no effect.',
  },
  {
    key: 'ifvg_news_blackout', label: 'News Blackout', section: 'Killzones', kind: 'list',
    hint: 'Comma-separated ISO date-time ranges to block entirely (e.g. a high-impact event day). Format: YYYY-MM-DDTHH:MM/YYYY-MM-DDTHH:MM. Usually empty.',
  },
  {
    key: 'htf_bias_timeframe', label: 'HTF Bias Timeframe', section: 'Structure / IFVG', kind: 'select',
    options: ['15min', '30min', '1h', '4h'],
    hint: 'Timeframe used to determine the higher-timeframe swing-structure bias. Only relevant when HTF Bias Gate is ON.',
  },
  {
    key: 'htf_bias_lookback', label: 'HTF Bias Lookback', section: 'Structure / IFVG', min: 1, max: 10, step: 1,
    hint: 'Number of swing points on the HTF chart used to determine trend direction. 3 = last 3 swings. Only relevant when HTF Bias Gate is ON.',
  },
  {
    key: 'htf_target_min_r', label: 'HTF Target Min R', section: 'Take-Profit', min: 0, max: 5, step: 0.5,
    hint: 'Minimum R an HTF swing level must deliver as a target to qualify. Lower HTF levels that are too close get skipped. Only relevant when HTF Target Selection is ON.',
  },
  {
    key: 'htf_swing_timeframe', label: 'HTF Swing Timeframe', section: 'Take-Profit', kind: 'select',
    options: ['15min', '30min', '1h', '4h'],
    hint: 'Fallback timeframe for swing-based target selection when HTF Target Selection is ON but no 4h level is available.',
  },
]

const STRATEGY_DEFAULTS: Record<string, string> = {
  swing_lookback:           '2',
  min_penetration:          '0.20',
  multi_bar_window:         '3',
  atr_period:               '14',
  body_atr_multiple:        '1.0',
  min_body_to_range_ratio:  '0.6',
  min_absolute_body:        '1.0',
  displacement_window_bars: '5',
  stop_buffer:              '0.30',
  r_multiple:               '2.5',
  ifvg_entry_mode:          'close',
  ifvg_rule_f_enabled:      'true',
  target_clarity_mode:      'reject',
  htf_bias_enabled:              'false',
  htf_target_enabled:            'false',
  trend_ema_period:              '50',
  vp_enabled:                    'false',
  ifvg_macro_blackouts_enabled:  'true',
  min_atr_filter:                '0',
  max_atr_filter:                '0',
  cooldown_bars_after_stop:      '0',
  min_penetration_atr_factor:    '0',
  ifvg_stop_buffer_ticks:        '1.0',
  ifvg_zone_max_age_bars:        '120',
  ifvg_sweep_window_bars:        '10',
  ifvg_min_displacement_mult:    '1.0',
  ifvg_tp1_fraction:             '0.5',
  ifvg_be_after_tp1:             'true',
  ifvg_news_blackout:            '',
  vp_tick_size:                  '0.10',
  vp_value_area_pct:             '0.7',
  vp_filter_tolerance:           '2.0',
  vp_hvn_threshold:              '1.5',
  vp_min_target_r:               '1.0',
  htf_bias_timeframe:            '4h',
  htf_bias_lookback:             '3',
  htf_target_min_r:              '2.0',
  htf_swing_timeframe:           '30min',
}

type DataSource = 'local' | 'databento' | 'static'

// Standard 2-year Databento CSV files per instrument (constant reference dataset).
const STATIC_BARS_MAP: Record<string, string> = {
  MGC: 'bars/bars_MGC_GCv_2024_2026.csv',
  MNQ: 'bars/bars_MNQ_NQv_2024_2026.csv',
  MES: 'bars/bars_MES_ESv_2024_2026.csv',
  MCL: 'bars/bars_MCL_CLv_2024_2026.csv',
}

// TP-system params varied in A/B comparison. partial_profit_r is a top-level
// config field; the rest live in the strategy dict. Both are sent on the run
// request (partial_profit_r at top level, the others inside `strategy`).
const TP_AB_FIELDS = [
  'r_multiple',
  'partial_profit_r',
  'ifvg_tp1_fraction',
  'ifvg_be_after_tp1',
  'htf_target_enabled',
  'htf_target_min_r',
  'target_clarity_mode',
] as const

const TP_AB_LABELS: Record<string, string> = {
  r_multiple: 'R Multiple',
  partial_profit_r: 'Partial Profit R',
  ifvg_tp1_fraction: 'TP1 Fraction',
  ifvg_be_after_tp1: 'BE After TP1',
  htf_target_enabled: 'HTF Target Selection',
  htf_target_min_r: 'HTF Target Min R',
  target_clarity_mode: 'Target-Clarity Gate',
}

// Distinct colors for overlaying variant equity curves (matrix-theme friendly).
const VARIANT_COLORS = ['#00ff41', '#38bdf8', '#fbbf24', '#f87171', '#c084fc', '#fb923c']

const VARIANT_LETTERS = ['A', 'B', 'C', 'D', 'E', 'F']

interface ABVariant {
  // Overrides for TP_AB_FIELDS only; absent keys inherit the base form value.
  overrides: Record<string, string>
  name?: string
  rationale?: string
}

interface ABResult {
  instrument: string
  letter: string
  label: string
  id: string | null
  stats: BacktestStats | null
  startingBalance: string
  status: 'pending' | 'running' | 'done' | 'error'
}

// Databento-supported instruments for the A/B matrix. Fetched from the backend
// (/api/databento/symbols); this is the fallback if that call fails.
const DEFAULT_AB_INSTRUMENTS = ['MES', 'MGC', 'MNQ']

const FONTS_ID = 'bt-grotesk-fonts'
function injectFonts() {
  if (document.getElementById(FONTS_ID)) return
  const link = document.createElement('link')
  link.id = FONTS_ID
  link.rel = 'stylesheet'
  link.href = 'https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@700;800&family=JetBrains+Mono:wght@400;500&display=swap'
  document.head.appendChild(link)
}

export function BacktestsPage() {
  const { confirm, modal } = useConfirm()
  const [list, setList] = useState<BacktestSummary[]>([])
  const [selected, setSelected] = useState<BacktestDetail | null>(null)
  const [running, setRunning] = useState(false)
  const [label, setLabel] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [pollKey, setPollKey] = useState(0)
  const [timeframe, setTimeframe] = useState('5min')
  const [startDate, setStartDate] = useState(daysAgo(30))
  const [endDate, setEndDate] = useState(isoDate(new Date()))
  const [availability, setAvailability] = useState<Availability | null>(null)
  const [availLoading, setAvailLoading] = useState(false)
  const [strategy, setStrategy] = useState<Record<string, string>>(STRATEGY_DEFAULTS)
  const [strategyDirty, setStrategyDirty] = useState(false)
  const [searchId, setSearchId] = useState<string | null>(null)
  const [searchProgress, setSearchProgress] = useState<{
    total: number; completed: number; labels: string[]; running: boolean
  } | null>(null)
  const [noteText, setNoteText] = useState('')
  const [noteSaving, setNoteSaving] = useState(false)
  const [dataSource, setDataSource] = useState<DataSource>('local')
  const [bentoStartDate, setBentoStartDate] = useState(daysAgo(365))
  const [bentoEndDate, setBentoEndDate] = useState(isoDate(new Date()))
  const [partialR, setPartialR] = useState('0')
  const [bentoMeta, setBentoMeta] = useState<{
    cost: number
    cachedThrough: string | null
    willFetch: number
  } | null>(null)
  const [bentoLoading, setBentoLoading] = useState(false)
  const [gradeFilter, setGradeFilter] = useState<string | null>(null)

  // A/B test mode: compare TP-system variants side by side.
  const [abMode, setAbMode] = useState(false)
  const [abVariants, setAbVariants] = useState<ABVariant[]>([{ overrides: {} }, { overrides: {} }])
  const [abResults, setAbResults] = useState<ABResult[] | null>(null)
  const [abRunning, setAbRunning] = useState(false)
  // A/B runs on Databento across its own calendar range + selected instruments.
  const [abStartDate, setAbStartDate] = useState(daysAgo(365))
  const [abEndDate, setAbEndDate] = useState(isoDate(new Date()))
  const [abInstruments, setAbInstruments] = useState<string[]>([])
  const [supportedSymbols, setSupportedSymbols] = useState<string[]>(DEFAULT_AB_INSTRUMENTS)

  // Filtering and sorting for the saved-runs list.
  type SortKey = 'pnl_desc' | 'pnl_asc' | 'trades_desc' | 'win_rate_desc' | 'profit_factor_desc' | 'date_desc'
  const [sortBy, setSortBy] = useState<SortKey>('pnl_desc')
  const [filterTf, setFilterTf] = useState<string>('all')
  const [minTrades, setMinTrades] = useState<number>(0)
  const [minWinRate, setMinWinRate] = useState<number>(0)
  const [onlyProfitable, setOnlyProfitable] = useState<boolean>(false)
  const [balancedPreset, setBalancedPreset] = useState<boolean>(false)
  const [bookmarkedOnly, setBookmarkedOnly] = useState<boolean>(false)

  useEffect(() => { injectFonts() }, [])

  // Load the live bot's current strategy as the initial set, so the page
  // opens with the "match my config" baseline rather than dataclass defaults.
  useEffect(() => {
    fetch('/api/config')
      .then(r => r.json())
      .then(d => {
        if (d?.strategy && typeof d.strategy === 'object') {
          const next: Record<string, string> = {}
          for (const k of Object.keys(STRATEGY_DEFAULTS)) {
            if (k === 'ifvg_macro_blackouts_enabled') continue
            const val = d.strategy[k] ?? STRATEGY_DEFAULTS[k]
            next[k] = Array.isArray(val) ? val.join(', ') : String(val)
          }
          // Derive macro toggle from the real list field
          const mw = d.strategy.ifvg_macro_windows
          next['ifvg_macro_blackouts_enabled'] = (Array.isArray(mw) && mw.length === 0) ? 'false' : 'true'
          setStrategy(next)
        }
        if (d?.partial_profit_r != null) setPartialR(String(d.partial_profit_r))
      })
      .catch(() => {})
  }, [])

  function setStratField(k: string, v: string) {
    setStrategy(s => ({ ...s, [k]: v }))
    setStrategyDirty(true)
  }

  // Populate the A/B instrument picker from the backend's Databento symbol map,
  // and default the selection to the live bot's instrument.
  useEffect(() => {
    fetch('/api/databento/symbols')
      .then(r => r.json())
      .then(d => {
        const syms: string[] = Array.isArray(d?.symbols) && d.symbols.length ? d.symbols : DEFAULT_AB_INSTRUMENTS
        setSupportedSymbols(syms)
        return resolveSymbol().then(sym => {
          const def = sym.toUpperCase()
          setAbInstruments([syms.includes(def) ? def : syms[0]])
        })
      })
      .catch(() => {
        setSupportedSymbols(DEFAULT_AB_INSTRUMENTS)
        setAbInstruments([DEFAULT_AB_INSTRUMENTS[0]])
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function toggleAbInstrument(sym: string) {
    setAbInstruments(prev =>
      prev.includes(sym) ? prev.filter(s => s !== sym) : [...prev, sym]
    )
  }

  // Build the strategy override payload from the current form state. `tpOverrides`
  // lets A/B variants replace specific TP params without mutating form state.
  // Returns the strategy dict plus the resolved top-level partial_profit_r.
  function buildRunPayload(tpOverrides: Record<string, string> = {}): {
    strategy: Record<string, unknown>
    partialProfitR: string
  } {
    const eff = (k: string): string =>
      tpOverrides[k] ?? strategy[k] ?? STRATEGY_DEFAULTS[k]
    const stratPayload: Record<string, unknown> = {}
    for (const f of STRATEGY_FIELDS) {
      if (f.key === 'ifvg_macro_blackouts_enabled') continue
      stratPayload[f.key] = eff(f.key)
    }
    const macroEnabled = eff('ifvg_macro_blackouts_enabled') === 'true'
    if (!macroEnabled) stratPayload['ifvg_macro_windows'] = []
    const LIST_FIELDS = ['ifvg_news_blackout'] as const
    for (const lf of LIST_FIELDS) {
      const raw = String(stratPayload[lf] ?? '').trim()
      stratPayload[lf] = raw ? raw.split(',').map(s => s.trim()).filter(Boolean) : []
    }
    // partial_profit_r is a top-level config field, not a strategy key.
    const partialProfitR = tpOverrides['partial_profit_r'] ?? partialR
    return { strategy: stratPayload, partialProfitR }
  }

  function resetStrategyToConfig() {
    fetch('/api/config')
      .then(r => r.json())
      .then(d => {
        const next: Record<string, string> = {}
        for (const k of Object.keys(STRATEGY_DEFAULTS)) {
          if (k === 'ifvg_macro_blackouts_enabled') continue
          const val = d.strategy?.[k] ?? STRATEGY_DEFAULTS[k]
          next[k] = Array.isArray(val) ? val.join(', ') : String(val)
        }
        const mw = d.strategy?.ifvg_macro_windows
        next['ifvg_macro_blackouts_enabled'] = (Array.isArray(mw) && mw.length === 0) ? 'false' : 'true'
        setStrategy(next)
        if (d?.partial_profit_r != null) setPartialR(String(d.partial_profit_r))
        setStrategyDirty(false)
      })
  }

  async function switchToDataSource(src: DataSource) {
    setDataSource(src)
    setBentoMeta(null)
    if (src !== 'databento') return
    setBentoLoading(true)
    try {
      const symbol = await resolveSymbol()
      const res = await fetch('/api/databento/fetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start: bentoStartDate, end: bentoEndDate, symbol, dry_run: true }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const body = await res.json()
      if (body.ok) {
        setBentoMeta({ cost: body.cost_estimate ?? 0, cachedThrough: body.cached_through ?? null, willFetch: body.days_fetched ?? 0 })
      } else {
        setMsg(`Databento: ${body.reason}`)
        setDataSource('local')
      }
    } catch {
      setMsg('Databento probe failed')
      setDataSource('local')
    } finally {
      setBentoLoading(false)
    }
  }

  const GRADE_STYLES: Record<string, { color: string; badge: string }> = {
    'A': { color: 'ring-1 ring-accent text-accent bg-accent/10',        badge: 'bg-accent text-bg' },
    'B': { color: 'ring-1 ring-accent/60 text-accent/80 bg-accent/5',   badge: 'bg-accent/70 text-bg' },
    'C': { color: 'ring-1 ring-warn/60 text-warn bg-warn/5',            badge: 'bg-warn text-bg' },
    'D': { color: 'ring-1 ring-danger/50 text-danger/70 bg-danger/5',   badge: 'bg-danger/60 text-bg' },
    'F': { color: 'ring-1 ring-danger/70 text-danger bg-danger/10',     badge: 'bg-danger text-bg' },
  }
  function gradeColor(g: string): string {
    return GRADE_STYLES[g]?.color ?? 'ring-1 ring-border text-dim'
  }
  function gradeBadge(g: string | undefined): string {
    return GRADE_STYLES[g ?? '']?.badge ?? 'bg-dim/20 text-dim'
  }

  async function resolveSymbol(): Promise<string> {
    const cfg = await fetch('/api/config').then(r => r.json())
    return (cfg?.instrument ?? 'MGC') as string
  }

  useEffect(() => {
    fetch('/api/backtest/list')
      .then(r => r.json())
      .then(d => setList(d.backtests ?? []))
      .catch(() => {})
  }, [pollKey])

  // Probe TopstepX for the data window every time the timeframe changes.
  useEffect(() => {
    setAvailLoading(true)
    fetch(`/api/bars/availability?timeframe=${timeframe}`)
      .then(r => r.json())
      .then((d: Availability) => {
        setAvailability(d)
        // Snap start/end into the available window if outside it
        if (d.earliest) {
          const earliestDate = d.earliest.slice(0, 10)
          if (startDate < earliestDate) setStartDate(earliestDate)
        }
        if (d.latest) {
          const latestDate = d.latest.slice(0, 10)
          if (endDate > latestDate) setEndDate(latestDate)
        }
      })
      .catch(() => setAvailability(null))
      .finally(() => setAvailLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [timeframe])

  const earliestStr = availability?.earliest?.slice(0, 10) ?? null
  const latestStr   = availability?.latest?.slice(0, 10) ?? null
  const availableDays = (() => {
    if (!earliestStr || !latestStr) return null
    const a = new Date(earliestStr).getTime()
    const b = new Date(latestStr).getTime()
    return Math.max(1, Math.round((b - a) / 86400000))
  })()

  function applyPreset(days: number) {
    if (days === 0) {
      // "Max": clamp to whatever TopstepX has
      if (earliestStr) setStartDate(earliestStr)
      if (latestStr)  setEndDate(latestStr)
      return
    }
    let candidate = daysAgo(days)
    if (earliestStr && candidate < earliestStr) candidate = earliestStr
    setStartDate(candidate)
    setEndDate(latestStr ?? isoDate(new Date()))
  }

  function presetDisabled(days: number): boolean {
    if (days === 0) return !earliestStr
    return availableDays != null && days > availableDays
  }

  // Auto-refresh while a run is in progress
  useEffect(() => {
    if (!running) return
    const id = setInterval(() => setPollKey(k => k + 1), 3000)
    return () => clearInterval(id)
  }, [running])

  async function startRun() {
    setRunning(true)
    try {
      const symbol = await resolveSymbol()

      if (dataSource === 'databento') {
        setMsg('Fetching bars from Databento…')
        const bentoRes = await fetch('/api/databento/fetch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ start: bentoStartDate, end: bentoEndDate, symbol, dry_run: false }),
        })
        const bentoBody = await bentoRes.json()
        if (!bentoBody.ok) {
          setMsg(`Databento fetch failed: ${bentoBody.reason}`)
          setRunning(false)
          return
        }
      } else {
        setMsg('Fetching historical bars…')
      }

      const beforeCount = list.length
      // Strings preserve decimal precision; the backend coerces via Pydantic.
      const { strategy: stratPayload, partialProfitR } = buildRunPayload()
      const runBody: Record<string, unknown> = {
        label: label || null,
        timeframe,
        strategy: stratPayload,
      }
      if (partialProfitR !== '0') runBody.partial_profit_r = partialProfitR
      if (dataSource === 'databento') {
        runBody.bars_path = `bars/bars_${symbol.toUpperCase()}.csv`
      } else if (dataSource === 'static') {
        runBody.bars_path = STATIC_BARS_MAP[symbol.toUpperCase()] ?? `bars/bars_${symbol.toUpperCase()}.csv`
      } else {
        runBody.start_date = startDate
        runBody.end_date = endDate
      }
      const res = await fetch('/api/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(runBody),
      })
      const body = await res.json()
      if (!body.ok) {
        setMsg(body.reason ?? 'Failed to start')
        setRunning(false)
        return
      }
      const rangeStr = dataSource === 'databento'
        ? `${bentoStartDate} → ${bentoEndDate}`
        : dataSource === 'static'
        ? '2024 → 2026 (static)'
        : `${startDate} → ${endDate}`
      setMsg(`Running (pid ${body.pid}) — ${timeframe}  ${rangeStr}`)
      const watch = setInterval(async () => {
        const d = await fetch('/api/backtest/list').then(r => r.json())
        if ((d.backtests?.length ?? 0) > beforeCount) {
          clearInterval(watch)
          setRunning(false)
          setMsg('Backtest complete')
          setList(d.backtests)
          setTimeout(() => setMsg(null), 4000)
        }
      }, 2000)
    } catch (e) {
      setMsg(String(e))
      setRunning(false)
    }
  }

  function setVariantOverride(idx: number, key: string, value: string) {
    setAbVariants(vs => vs.map((v, i) =>
      i === idx ? { ...v, overrides: { ...v.overrides, [key]: value } } : v
    ))
  }

  function clearVariantOverride(idx: number, key: string) {
    setAbVariants(vs => vs.map((v, i) => {
      if (i !== idx) return v
      const next = { ...v.overrides }
      delete next[key]
      return { ...v, overrides: next }
    }))
  }

  function addVariant() {
    setAbVariants(vs => vs.length >= VARIANT_LETTERS.length ? vs : [...vs, { overrides: {} }])
  }

  function removeVariant(idx: number) {
    setAbVariants(vs => vs.length <= 2 ? vs : vs.filter((_, i) => i !== idx))
  }

  async function loadTJRVariants() {
    try {
      const res = await fetch('/api/ab-variants')
      if (!res.ok) throw new Error(await res.text())
      const data: Array<{ name: string; rationale: string; overrides: Record<string, string> }> = await res.json()
      setAbVariants(data.map(v => ({ overrides: v.overrides, name: v.name, rationale: v.rationale })))
    } catch (e) {
      setMsg(`Failed to load TJR variants: ${e}`)
    }
  }

  // Resolve the effective TP value a variant will run with (override or base form).
  function variantTpValue(v: ABVariant, key: string): string {
    return v.overrides[key] ?? strategy[key] ?? (key === 'partial_profit_r' ? partialR : STRATEGY_DEFAULTS[key])
  }

  // A/B runs on Databento as a full matrix: each TP variant × each selected
  // instrument. Bars are fetched once per instrument, then one isolated backtest
  // per (instrument, variant). Labels encode both so list-polling can match back.
  async function runABTest() {
    if (abInstruments.length < 1) {
      setMsg('Select at least one instrument for the A/B matrix.')
      return
    }
    if (abStartDate > abEndDate) {
      setMsg('A/B start date must be on or before the end date.')
      return
    }
    setAbRunning(true)
    setMsg('Starting A/B matrix…')

    const base = (label || `${abStartDate}→${abEndDate}`).slice(0, 60)
    // Seed one pending result cell per (instrument, variant) combo.
    const runs: ABResult[] = []
    for (const instr of abInstruments) {
      for (let i = 0; i < abVariants.length; i++) {
        runs.push({
          instrument: instr,
          letter: VARIANT_LETTERS[i],
          label: `A/B: ${base} — ${instr} — Variant ${VARIANT_LETTERS[i]}`,
          id: null,
          stats: null,
          startingBalance: '50000',
          status: 'pending',
        })
      }
    }
    setAbResults(runs)

    // Labels of runs that actually started — only these are polled for results.
    const startedLabels = new Set<string>()

    try {
      for (const instr of abInstruments) {
        const sym = instr.toUpperCase()
        // Fetch Databento bars once per instrument before running its variants.
        setMsg(`Fetching ${sym} bars from Databento…`)
        let fetchOk = false
        try {
          const bentoRes = await fetch('/api/databento/fetch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol: sym, start: abStartDate, end: abEndDate, dry_run: false }),
          })
          const bentoBody = await bentoRes.json()
          fetchOk = !!bentoBody.ok
          if (!fetchOk) {
            setMsg(`${sym}: Databento fetch failed — ${bentoBody.reason ?? 'unknown'} (skipping)`)
          }
        } catch (e) {
          setMsg(`${sym}: Databento fetch error — ${String(e)} (skipping)`)
        }

        if (!fetchOk) {
          // Mark every variant cell for this instrument as errored and skip it.
          setAbResults(prev => prev?.map(r =>
            r.instrument === sym && r.status === 'pending' ? { ...r, status: 'error' } : r
          ) ?? null)
          continue
        }

        for (let i = 0; i < abVariants.length; i++) {
          const r = runs.find(x => x.instrument === sym && x.letter === VARIANT_LETTERS[i])!
          setMsg(`Running ${sym} · Variant ${VARIANT_LETTERS[i]}…`)
          const { strategy: stratPayload, partialProfitR } = buildRunPayload(abVariants[i].overrides)
          // NOTE: deliberately do NOT send start_date/end_date here. The backtest
          // endpoint routes start_date+end_date to a *broker* fetch (live-mode
          // only) and ignores bars_path; the date range is already applied by the
          // Databento fetch above (the CSV only holds bars for that range). This
          // mirrors the working single-run Databento flow.
          const runBody: Record<string, unknown> = {
            label: r.label,
            timeframe,
            strategy: stratPayload,
            bars_path: `bars/bars_${sym}.csv`,
            instrument: sym,
          }
          if (partialProfitR !== '0') runBody.partial_profit_r = partialProfitR
          const res = await fetch('/api/backtest/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(runBody),
          })
          const body = await res.json()
          if (body.ok) startedLabels.add(r.label)
          setAbResults(prev => prev?.map(x =>
            x.label === r.label ? { ...x, status: body.ok ? 'running' : 'error' } : x
          ) ?? null)
          if (!body.ok) {
            setMsg(`${sym} · Variant ${VARIANT_LETTERS[i]} failed to start: ${body.reason ?? 'unknown'}`)
          }
        }
      }

      setMsg('A/B matrix running — collecting results…')
      // Poll the list; backfill stats as each labeled run lands. Stop when all
      // started runs are found or after a generous timeout.
      const targets = startedLabels
      if (targets.size === 0) {
        setAbRunning(false)
        setMsg('A/B matrix: no runs started.')
        return
      }
      const deadline = Date.now() + 10 * 60 * 1000
      const matched = new Set<string>()
      const poll = setInterval(async () => {
        try {
          const d = await fetch('/api/backtest/list').then(r => r.json())
          const items: BacktestSummary[] = d.backtests ?? []
          setList(items)
          for (const it of items) {
            if (targets.has(it.label) && !matched.has(it.label)) {
              matched.add(it.label)
              const detail = await fetch(`/api/backtest/${it.id}`).then(r => r.json())
              setAbResults(prev => prev?.map(r =>
                r.label === it.label
                  ? { ...r, id: it.id, stats: detail.stats, startingBalance: detail.starting_balance ?? '50000', status: 'done' }
                  : r
              ) ?? null)
            }
          }
          if (matched.size >= targets.size || Date.now() > deadline) {
            clearInterval(poll)
            setAbRunning(false)
            setMsg(matched.size >= targets.size
              ? 'A/B matrix complete'
              : `A/B matrix timed out — ${matched.size}/${targets.size} runs completed`)
            setTimeout(() => setMsg(null), 5000)
          }
        } catch { /* keep polling */ }
      }, 2500)
    } catch (e) {
      setMsg(String(e))
      setAbRunning(false)
    }
  }

  async function loadDetail(id: string) {
    const data = await fetch(`/api/backtest/${id}`).then(r => r.json())
    setSelected(data)
    setNoteText(data.note ?? '')
  }

  function loadParamsFromSelected() {
    if (!selected) return
    const src = selected.config.strategy as unknown as Record<string, unknown>
    const next: Record<string, string> = {}
    for (const k of Object.keys(STRATEGY_DEFAULTS)) {
      next[k] = String(src[k] ?? STRATEGY_DEFAULTS[k])
    }
    setStrategy(next)
    setTimeframe(selected.timeframe)
    setStrategyDirty(true)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  async function saveNote() {
    if (!selected) return
    setNoteSaving(true)
    try {
      await fetch(`/api/backtest/${selected.id}/note`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: noteText }),
      })
      setSelected(prev => prev ? { ...prev, note: noteText } : prev)
    } catch { /* swallow */ }
    setNoteSaving(false)
  }

  async function deleteRun(id: string) {
    await fetch(`/api/backtest/${id}`, { method: 'DELETE' })
    setList(list.filter(b => b.id !== id))
    if (selected?.id === id) setSelected(null)
  }

  async function clearUnbookmarked() {
    if (!await confirm('Delete all unbookmarked runs? This cannot be undone.', { title: 'Clear Runs', variant: 'danger', confirmLabel: 'Delete' })) return
    const res = await fetch('/api/backtest/clear-unbookmarked', { method: 'DELETE' })
    const body = await res.json()
    if (body.ok) {
      setList(prev => prev.filter(b => b.bookmarked))
      if (selected && !selected.bookmarked) setSelected(null)
    }
  }

  async function toggleBookmark(id: string, e?: React.MouseEvent) {
    e?.stopPropagation()  // don't trigger row select
    try {
      const res = await fetch(`/api/backtest/${id}/bookmark`, { method: 'POST' })
      const body = await res.json()
      if (body.ok) {
        setList(prev => prev.map(b =>
          b.id === id ? { ...b, bookmarked: body.bookmarked } : b
        ))
      }
    } catch { /* swallow */ }
  }

  async function startRandomSearch() {
    setMsg('Starting random search: 15 × 1min + 15 × 5min = 30 backtests…')
    try {
      const res = await fetch('/api/backtest/random-search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          count_per_timeframe: 15,
          timeframes: ['1min', '5min'],
          start_date: startDate,
          end_date: endDate,
          concurrency: 2,
        }),
      })
      const body = await res.json()
      if (!body.ok) {
        setMsg(body.reason ?? 'Failed to start random search')
        return
      }
      setSearchId(body.search_id)
      setSearchProgress({ total: body.total, completed: 0, labels: [], running: true })
      setMsg(`Search ${body.search_id} running — fetching bars and spawning 30 backtests`)
    } catch (e) {
      setMsg(String(e))
    }
  }

  // Poll search progress and the list while a search is active.
  useEffect(() => {
    if (!searchId) return
    const id = setInterval(async () => {
      try {
        const [p, l] = await Promise.all([
          fetch(`/api/backtest/random-search/${searchId}`).then(r => r.json()),
          fetch('/api/backtest/list').then(r => r.json()),
        ])
        setSearchProgress({
          total: p.total, completed: p.completed, labels: p.labels ?? [], running: p.running,
        })
        setList(l.backtests ?? [])
        if (!p.running) {
          clearInterval(id)
          setMsg(`Search ${searchId} complete: ${p.completed}/${p.total} runs done`)
        }
      } catch { /* keep polling */ }
    }, 3000)
    return () => clearInterval(id)
  }, [searchId])

  useEffect(() => {
    setGradeFilter(null)
  }, [selected?.id])

  useEffect(() => {
    if (dataSource !== 'databento') return
    setBentoMeta(null)
    setBentoLoading(true)
    resolveSymbol().then(symbol =>
      fetch('/api/databento/fetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start: bentoStartDate, end: bentoEndDate, symbol, dry_run: true }),
      })
        .then(r => r.json())
        .then(body => {
          if (body.ok) setBentoMeta({ cost: body.cost_estimate ?? 0, cachedThrough: body.cached_through ?? null, willFetch: body.days_fetched ?? 0 })
        })
        .catch(() => {})
        .finally(() => setBentoLoading(false))
    )
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataSource, bentoStartDate, bentoEndDate])

  // Identify the best backtest from this search (or overall if no search yet).
  const searchLabelSet = new Set(searchProgress?.labels ?? [])
  const candidateList = searchProgress
    ? list.filter(b => searchLabelSet.has(b.label))
    : list
  const bestRun = candidateList.length === 0 ? null
    : [...candidateList].sort(
        (a, b) => parseFloat(b.stats.net_pnl) - parseFloat(a.stats.net_pnl)
      )[0]

  // Apply user-facing filters + sort to the saved-runs list.
  const filteredList = (() => {
    let out = [...list]
    if (filterTf !== 'all') out = out.filter(b => b.timeframe === filterTf)
    if (minTrades > 0)      out = out.filter(b => b.stats.trades >= minTrades)
    if (minWinRate > 0)     out = out.filter(b => b.stats.win_rate >= minWinRate)
    if (onlyProfitable)     out = out.filter(b => parseFloat(b.stats.net_pnl) > 0)
    if (bookmarkedOnly)     out = out.filter(b => b.bookmarked)

    if (balancedPreset) {
      // "Balanced" = decent activity, positive expectancy, not a coin flip.
      out = out.filter(b =>
        b.stats.trades >= 10 &&
        b.stats.win_rate >= 40 &&
        parseFloat(b.stats.net_pnl) > 0 &&
        (b.stats.profit_factor == null || b.stats.profit_factor >= 1.2)
      )
    }

    const cmp = (a: BacktestSummary, b: BacktestSummary): number => {
      switch (sortBy) {
        case 'pnl_desc':           return parseFloat(b.stats.net_pnl) - parseFloat(a.stats.net_pnl)
        case 'pnl_asc':            return parseFloat(a.stats.net_pnl) - parseFloat(b.stats.net_pnl)
        case 'trades_desc':        return b.stats.trades - a.stats.trades
        case 'win_rate_desc':      return b.stats.win_rate - a.stats.win_rate
        case 'profit_factor_desc':
          return (b.stats.profit_factor ?? 0) - (a.stats.profit_factor ?? 0)
        case 'date_desc':
          return (b.completed_at ?? '').localeCompare(a.completed_at ?? '')
      }
    }
    return out.sort(cmp)
  })()

  const gradeStats = useMemo(() => {
    type Bucket = { wins: number; grossWin: number; grossLoss: number; count: number }
    const buckets: Record<string, Bucket> = {}
    for (const t of selected?.trades ?? []) {
      const pnl = parseFloat(t.realized_pnl)
      const g = t.grade ?? ''
      const b = buckets[g] ??= { wins: 0, grossWin: 0, grossLoss: 0, count: 0 }
      b.count++
      if (pnl > 0) { b.wins++; b.grossWin += pnl }
      else if (pnl < 0) { b.grossLoss += Math.abs(pnl) }
    }
    return GRADE_TIERS.reduce((acc, g) => {
      const b = buckets[g] ?? { wins: 0, grossWin: 0, grossLoss: 0, count: 0 }
      acc[g] = {
        count: b.count,
        winRate: b.count > 0 ? Math.round(b.wins / b.count * 100) : 0,
        profitFactor: b.grossLoss > 0 ? Math.round(b.grossWin / b.grossLoss * 100) / 100 : null,
      }
      return acc
    }, {} as Record<GradeTier, { count: number; winRate: number; profitFactor: number | null }>)
  }, [selected?.trades])

  const displayedTrades = useMemo(() => {
    if (!selected?.trades) return []
    if (!gradeFilter) return selected.trades
    return selected.trades.filter(t => t.grade === gradeFilter)
  }, [selected?.trades, gradeFilter])

  function resetFilters() {
    setFilterTf('all')
    setMinTrades(0)
    setMinWinRate(0)
    setOnlyProfitable(false)
    setBalancedPreset(false)
    setBookmarkedOnly(false)
    setSortBy('pnl_desc')
  }

  async function applyConfig() {
    if (!selected) return
    const ok = await confirm(
      'Apply strategy params to live bot and hot-reload? The bot keeps running; open positions, risk state, and broker connection are untouched. Strategy internal state resets — next bar rebuilds it.',
      { title: 'Apply Config', variant: 'warn', confirmLabel: 'Apply' }
    )
    if (!ok) return
    setMsg('Saving config and reloading strategy...')
    try {
      const current = await fetch('/api/config').then(r => r.json())
      const merged = {
        instrument: current.instrument,
        timeframes: current.timeframes,
        replay_delay_ms: current.replay_delay_ms,
        replay_start_delay_s: current.replay_start_delay_s,
        account_name: current.account_name ?? null,
        strategy: selected.config.strategy,
      }
      const patchRes = await fetch('/api/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(merged),
      })
      if (!patchRes.ok) throw new Error(await patchRes.text())

      const reloadRes = await fetch('/api/strategy/reload', { method: 'POST' })
      const reloadBody = await reloadRes.json()
      if (!reloadBody.ok) {
        setMsg(`Config saved but reload failed: ${reloadBody.reason}. Restart the bot manually to pick it up.`)
      } else {
        setMsg('Config applied and strategy reloaded. Live bot is now using the new params.')
      }
      setTimeout(() => setMsg(null), 6000)
    } catch (e) {
      setMsg('Apply failed: ' + String(e))
    }
  }

  return (
    <>
    {modal}
    <div className="min-h-screen bg-bg scanlines" style={{ fontFamily: 'inherit' }}>
      {/* Nav */}
      <div style={{
        borderBottom: '1px solid #1d2a42', padding: '20px 24px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: 10, textDecoration: 'none' }}>
          <Logo size={16} />
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, letterSpacing: '0.3em', color: '#6a85b0' }}>ZENITH</span>
        </Link>
        <div style={{ display: 'flex', gap: 24 }}>
          <Link to="/analytics"
            style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, letterSpacing: '0.2em', color: '#6a85b0', textDecoration: 'none' }}
            onMouseEnter={e => ((e.currentTarget as HTMLAnchorElement).style.color = '#e8f0ff')}
            onMouseLeave={e => ((e.currentTarget as HTMLAnchorElement).style.color = '#6a85b0')}>
            ANALYTICS
          </Link>
          <Link to="/"
            style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, letterSpacing: '0.2em', color: '#6a85b0', textDecoration: 'none' }}
            onMouseEnter={e => ((e.currentTarget as HTMLAnchorElement).style.color = '#e8f0ff')}
            onMouseLeave={e => ((e.currentTarget as HTMLAnchorElement).style.color = '#6a85b0')}>
            ← LIVE
          </Link>
        </div>
      </div>

      <main className="p-6 max-w-[1400px] mx-auto space-y-6">
        {/* Title */}
        <div style={{ marginBottom: 40 }}>
          <div style={{ fontFamily: "'Space Grotesk', sans-serif", fontSize: 60, fontWeight: 800, lineHeight: 1, letterSpacing: '-0.02em', marginBottom: 10, color: '#e8f0ff' }}>
            BACKTEST<br />ENGINE
          </div>
          <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: '#6a85b0', letterSpacing: '0.15em' }}>
            STRATEGY SIMULATION · PARAMETER SEARCH
          </div>
        </div>
        <section className="bg-panel border border-border p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[10px] tracking-[0.3em] text-accent uppercase">
              {abMode ? 'TP A/B Test' : 'Run New Backtest'}
            </h2>
            <div className="flex items-center gap-px bg-border border border-border">
              <button
                onClick={() => setAbMode(false)}
                className={`text-[10px] tracking-widest uppercase px-3 py-1 ${
                  !abMode ? 'bg-accent/10 text-accent' : 'bg-panel text-dim hover:text-ink'
                }`}
              >
                Single
              </button>
              <button
                onClick={() => setAbMode(true)}
                className={`text-[10px] tracking-widest uppercase px-3 py-1 ${
                  abMode ? 'bg-accent/10 text-accent' : 'bg-panel text-dim hover:text-ink'
                }`}
              >
                A/B Test
              </button>
            </div>
          </div>
          <p className="text-[11px] text-dim mb-4 leading-relaxed">
            {abMode ? (
              <>Run the base strategy config against 2+ variants that differ only in their
              take-profit parameters, over the same bars and date window. Each variant runs
              as its own isolated backtest and is saved to the list, then compared side by side.</>
            ) : (
              <>Pulls historical bars from TopstepX for the date range you pick, runs the
              current strategy config against them in a separate process, and saves the
              full stats. The live bot keeps running untouched.</>
            )}
          </p>

          <div className="mb-3 px-3 py-2 border border-border bg-bg/40">
            <div className="text-[10px] tracking-wider text-dim uppercase mb-1">
              Data Window (TopstepX, {timeframe})
            </div>
            <div className="text-[11px] font-mono tabular-nums text-ink">
              {availLoading ? (
                <span className="text-dim">Probing…</span>
              ) : !availability?.available ? (
                <span className="text-warn">
                  {availability?.reason ?? 'Unknown availability — pick any range and we’ll try'}
                </span>
              ) : earliestStr && latestStr ? (
                <span>
                  {earliestStr} → {latestStr}{' '}
                  <span className="text-dim">
                    ({availableDays} day{availableDays === 1 ? '' : 's'}, {availability.bars?.toLocaleString()} bars)
                  </span>
                </span>
              ) : (
                <span className="text-danger">No bars available</span>
              )}
            </div>
            <div className="text-[10px] text-dim/80 mt-1 leading-relaxed">
              TopstepX only retains data for the active contract — about 50 days. Older periods
              would require a third-party source (FirstRate / Databento).
            </div>
          </div>

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
            <div>
              <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">Timeframe</label>
              <select
                value={timeframe}
                onChange={e => setTimeframe(e.target.value)}
                disabled={running}
                className="w-full bg-bg border border-border text-ink text-sm px-3 py-2 font-mono cursor-pointer focus:outline-none focus:border-accent"
              >
                {TIMEFRAMES.map(t => (
                  <option key={t} value={t} className="bg-panel">{t}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">Start date</label>
              <input
                type="date"
                value={startDate}
                min={earliestStr ?? undefined}
                max={latestStr ?? undefined}
                onChange={e => setStartDate(e.target.value)}
                disabled={running}
                className="w-full bg-bg border border-border text-ink text-sm px-3 py-2 font-mono focus:outline-none focus:border-accent"
              />
            </div>
            <div>
              <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">End date</label>
              <input
                type="date"
                value={endDate}
                min={earliestStr ?? undefined}
                max={latestStr ?? undefined}
                onChange={e => setEndDate(e.target.value)}
                disabled={running}
                className="w-full bg-bg border border-border text-ink text-sm px-3 py-2 font-mono focus:outline-none focus:border-accent"
              />
            </div>
            <div>
              <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">Quick range</label>
              <div className="flex gap-1 flex-wrap">
                {DATE_PRESETS.map(p => {
                  const disabled = running || presetDisabled(p.days)
                  return (
                    <button
                      key={p.label}
                      onClick={() => applyPreset(p.days)}
                      disabled={disabled}
                      title={disabled && p.days > 0 ? `Only ${availableDays} days available` : undefined}
                      className="text-[10px] tracking-widest uppercase border border-border text-dim hover:text-ink hover:border-ink px-2 py-1 disabled:opacity-30 disabled:cursor-not-allowed"
                    >
                      {p.label}
                    </button>
                  )
                })}
              </div>
            </div>
          </div>

          <details open className="mb-4 border border-border">
            <summary className="cursor-pointer px-3 py-2 bg-bg/40 text-[10px] tracking-[0.3em] text-accent uppercase flex items-center justify-between">
              <span>
                Strategy Parameters
                {strategyDirty && <span className="ml-2 text-warn normal-case tracking-normal">· edited</span>}
              </span>
              <button
                onClick={e => { e.preventDefault(); resetStrategyToConfig() }}
                className="text-[10px] tracking-widest uppercase text-dim hover:text-ink normal-case"
                title="Reload from the live bot's bot_config.json"
              >
                Reset to bot config
              </button>
            </summary>
            <div className="p-4 space-y-px bg-border">
              {SECTION_ORDER.map(section => {
                const fields = STRATEGY_FIELDS.filter(f => f.section === section)
                if (fields.length === 0) return null
                return (
                  <details key={section} open className="bg-panel">
                    <summary className="cursor-pointer px-3 py-2 bg-bg/40 text-[10px] tracking-[0.3em] text-dim uppercase hover:text-ink">
                      {section} <span className="text-dim/50 normal-case tracking-normal">({fields.length})</span>
                    </summary>
                    <div className="p-3 grid grid-cols-1 md:grid-cols-2 gap-3">
                      {fields.map(f => (
                        <div key={f.key}>
                          <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                            {f.label}
                          </label>
                          <StrategyFieldInput
                            field={f}
                            value={strategy[f.key] ?? STRATEGY_DEFAULTS[f.key]}
                            onChange={v => setStratField(f.key, v)}
                            disabled={running}
                          />
                          <p className="text-[10px] text-dim/70 mt-1 leading-relaxed">{f.hint}</p>
                        </div>
                      ))}
                      {section === 'Take-Profit' && (
                        <div>
                          <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                            Partial Profit R
                          </label>
                          <div className="flex items-center gap-2">
                            <input
                              type="range"
                              min={0}
                              max={5}
                              step={0.25}
                              value={Number(partialR)}
                              onChange={e => setPartialR(e.target.value)}
                              disabled={running}
                              className="flex-1 slider-accent"
                            />
                            <input
                              type="number"
                              min={0}
                              max={5}
                              step={0.25}
                              value={partialR}
                              onChange={e => setPartialR(e.target.value)}
                              disabled={running}
                              className="w-20 bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                            />
                          </div>
                          <p className="text-[10px] text-dim/70 mt-1 leading-relaxed">
                            R level to take a partial exit (half position). 0 = disabled. E.g. 1.5 = close half at 1.5R then move stop to break-even.
                          </p>
                        </div>
                      )}
                    </div>
                  </details>
                )
              })}
            </div>
          </details>

          {!abMode && (
          <>
          {/* Data source toggle */}
          <div className="border border-border bg-bg/30 p-3">
            <div className="text-[9px] tracking-widest text-dim uppercase mb-2">Data Source</div>
            <div className="flex items-center gap-2 mb-3">
              <button
                onClick={() => switchToDataSource('local')}
                disabled={running}
                className={`text-[11px] tracking-widest uppercase px-3 py-1 border transition-colors ${
                  dataSource === 'local'
                    ? 'border-accent text-accent bg-accent/10'
                    : 'border-border text-dim hover:text-ink'
                }`}
              >
                Local CSV
              </button>
              <button
                onClick={() => switchToDataSource('databento')}
                disabled={running}
                className={`text-[11px] tracking-widest uppercase px-3 py-1 border transition-colors ${
                  dataSource === 'databento'
                    ? 'border-warn text-warn bg-warn/10'
                    : 'border-border text-dim hover:text-ink'
                }`}
              >
                Databento
              </button>
              <button
                onClick={() => switchToDataSource('static')}
                disabled={running}
                className={`text-[11px] tracking-widest uppercase px-3 py-1 border transition-colors ${
                  dataSource === 'static'
                    ? 'border-accent text-accent bg-accent/10'
                    : 'border-border text-dim hover:text-ink'
                }`}
              >
                2yr Static
              </button>
              {bentoLoading && <span className="text-[10px] text-dim ml-2">probing…</span>}
              {dataSource === 'databento' && bentoMeta && !bentoLoading && (
                <span className="text-[10px] text-dim ml-2 font-mono">
                  est. <span className="text-warn">${bentoMeta.cost.toFixed(2)}</span>
                  {bentoMeta.cachedThrough && (
                    <> · cached through <span className="text-ink">{bentoMeta.cachedThrough}</span></>
                  )}
                  {bentoMeta.willFetch === 0 && (
                    <> · <span className="text-accent">full cache hit</span></>
                  )}
                </span>
              )}
            </div>
            {dataSource === 'static' && (
              <div className="pt-2 border-t border-border/50">
                <p className="text-[10px] font-mono text-dim">
                  Uses pre-downloaded 2yr CSV · Jan 2024 – May 2026
                </p>
              </div>
            )}
            {dataSource === 'databento' && (
              <div className="grid grid-cols-2 gap-3 pt-2 border-t border-border/50">
                <div>
                  <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">Databento start</label>
                  <input
                    type="date"
                    value={bentoStartDate}
                    max={bentoEndDate}
                    onChange={e => setBentoStartDate(e.target.value)}
                    disabled={running}
                    className="w-full bg-bg border border-border text-ink text-sm px-3 py-2 font-mono focus:outline-none focus:border-warn"
                  />
                </div>
                <div>
                  <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">Databento end</label>
                  <input
                    type="date"
                    value={bentoEndDate}
                    min={bentoStartDate}
                    max={isoDate(new Date())}
                    onChange={e => setBentoEndDate(e.target.value)}
                    disabled={running}
                    className="w-full bg-bg border border-border text-ink text-sm px-3 py-2 font-mono focus:outline-none focus:border-warn"
                  />
                </div>
              </div>
            )}
          </div>

          <div className="flex items-center gap-3">
            <input
              type="text"
              value={label}
              onChange={e => setLabel(e.target.value)}
              placeholder="Optional label (e.g. swing=3, R=3, 30d-5min)"
              disabled={running}
              className="flex-1 bg-bg border border-border text-ink text-sm px-3 py-2 font-mono focus:outline-none focus:border-accent"
            />
            <button
              onClick={startRun}
              disabled={running || searchProgress?.running}
              className="bg-accent/10 border border-accent text-accent text-xs tracking-widest uppercase px-5 py-2 hover:bg-accent/20 disabled:opacity-50"
            >
              {running ? 'Running…' : 'Run Backtest'}
            </button>
            <button
              onClick={startRandomSearch}
              disabled={running || searchProgress?.running}
              className="bg-warn/10 border border-warn text-warn text-xs tracking-widest uppercase px-5 py-2 hover:bg-warn/20 disabled:opacity-50"
              title="Run 15 random parameter combos on 1min + 15 on 5min, then highlight the best PnL"
            >
              {searchProgress?.running ? 'Searching…' : 'Random Search ×30'}
            </button>
          </div>

          {searchProgress && (
            <div className="mt-3">
              <div className="flex items-center justify-between text-[10px] tracking-widest text-dim uppercase">
                <span>Random Search Progress</span>
                <span className="font-mono tabular-nums">
                  {searchProgress.completed} / {searchProgress.total}
                  {bestRun && (
                    <span className="ml-3 text-accent">
                      best: {parseFloat(bestRun.stats.net_pnl) >= 0 ? '+' : ''}${parseFloat(bestRun.stats.net_pnl).toFixed(0)}
                    </span>
                  )}
                </span>
              </div>
              <div className="h-1 bg-bg mt-1 border border-border">
                <div
                  className="h-full bg-warn transition-all"
                  style={{ width: `${(searchProgress.completed / Math.max(1, searchProgress.total)) * 100}%` }}
                />
              </div>
            </div>
          )}
          </>
          )}

          {abMode && (
            <ABPanel
              variants={abVariants}
              results={abResults}
              running={abRunning}
              startDate={abStartDate}
              endDate={abEndDate}
              onStartDate={setAbStartDate}
              onEndDate={setAbEndDate}
              supportedSymbols={supportedSymbols}
              selectedInstruments={abInstruments}
              onToggleInstrument={toggleAbInstrument}
              onAddVariant={addVariant}
              onRemoveVariant={removeVariant}
              onSetOverride={setVariantOverride}
              onClearOverride={clearVariantOverride}
              variantTpValue={variantTpValue}
              onRun={runABTest}
              onLoadPreset={loadTJRVariants}
            />
          )}

          {msg && <p className="text-[11px] text-accent mt-3">{msg}</p>}
        </section>

        <div className="grid grid-cols-1 lg:grid-cols-5 gap-px bg-border">
          <section className="lg:col-span-2 bg-panel">
            <div className="px-4 py-2 border-b border-border flex items-center justify-between">
              <span className="text-[10px] tracking-[0.3em] text-dim uppercase">
                Saved Runs ({filteredList.length}{filteredList.length !== list.length && <> of {list.length}</>})
              </span>
              <div className="flex items-center gap-3">
                <button
                  onClick={clearUnbookmarked}
                  className="text-[10px] tracking-widest text-danger/60 hover:text-danger uppercase"
                  title="Delete all runs that are not bookmarked"
                >
                  Clear unbookmarked
                </button>
                <button
                  onClick={resetFilters}
                  className="text-[10px] tracking-widest text-dim hover:text-ink uppercase"
                  title="Clear all filters and sort by Net PnL"
                >
                  Reset
                </button>
              </div>
            </div>

            <div className="px-3 py-3 border-b border-border space-y-2 bg-bg/30">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[9px] tracking-widest text-dim uppercase mb-1">Sort by</label>
                  <select
                    value={sortBy}
                    onChange={e => setSortBy(e.target.value as SortKey)}
                    className="w-full bg-bg border border-border text-ink text-[11px] px-2 py-1 font-mono cursor-pointer focus:outline-none focus:border-accent"
                  >
                    <option value="pnl_desc"           className="bg-panel">Net P&L (high → low)</option>
                    <option value="pnl_asc"            className="bg-panel">Net P&L (low → high)</option>
                    <option value="trades_desc"        className="bg-panel">Most trades</option>
                    <option value="win_rate_desc"      className="bg-panel">Highest win rate</option>
                    <option value="profit_factor_desc" className="bg-panel">Highest profit factor</option>
                    <option value="date_desc"          className="bg-panel">Newest first</option>
                  </select>
                </div>
                <div>
                  <label className="block text-[9px] tracking-widest text-dim uppercase mb-1">Timeframe</label>
                  <select
                    value={filterTf}
                    onChange={e => setFilterTf(e.target.value)}
                    className="w-full bg-bg border border-border text-ink text-[11px] px-2 py-1 font-mono cursor-pointer focus:outline-none focus:border-accent"
                  >
                    <option value="all"   className="bg-panel">All</option>
                    <option value="1min"  className="bg-panel">1min</option>
                    <option value="3min"  className="bg-panel">3min</option>
                    <option value="5min"  className="bg-panel">5min</option>
                    <option value="15min" className="bg-panel">15min</option>
                    <option value="30min" className="bg-panel">30min</option>
                    <option value="1h"    className="bg-panel">1h</option>
                  </select>
                </div>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block text-[9px] tracking-widest text-dim uppercase mb-1">Min trades</label>
                  <input
                    type="number"
                    min={0}
                    value={minTrades}
                    onChange={e => setMinTrades(parseInt(e.target.value) || 0)}
                    className="w-full bg-bg border border-border text-ink text-[11px] px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                  />
                </div>
                <div>
                  <label className="block text-[9px] tracking-widest text-dim uppercase mb-1">Min win %</label>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={minWinRate}
                    onChange={e => setMinWinRate(parseFloat(e.target.value) || 0)}
                    className="w-full bg-bg border border-border text-ink text-[11px] px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                  />
                </div>
              </div>
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  onClick={() => setOnlyProfitable(!onlyProfitable)}
                  className={`text-[10px] tracking-widest uppercase border px-2 py-1 ${
                    onlyProfitable ? 'border-accent text-accent bg-accent/10' : 'border-border text-dim hover:text-ink'
                  }`}
                >
                  Profitable only
                </button>
                <button
                  onClick={() => setBalancedPreset(!balancedPreset)}
                  title="Pre-filter: ≥10 trades, ≥40% win rate, PnL > 0, profit factor ≥ 1.2"
                  className={`text-[10px] tracking-widest uppercase border px-2 py-1 ${
                    balancedPreset ? 'border-accent text-accent bg-accent/10' : 'border-border text-dim hover:text-ink'
                  }`}
                >
                  Balanced
                </button>
                <button
                  onClick={() => setBookmarkedOnly(!bookmarkedOnly)}
                  title="Show only bookmarked runs"
                  className={`text-[10px] tracking-widest uppercase border px-2 py-1 ${
                    bookmarkedOnly ? 'border-warn text-warn bg-warn/10' : 'border-border text-dim hover:text-ink'
                  }`}
                >
                  ★ Bookmarked
                </button>
              </div>
            </div>

            <div className="max-h-[60vh] overflow-y-auto feed">
              {filteredList.length === 0 ? (
                <div className="px-4 py-6 text-[11px] text-dim">
                  {list.length === 0 ? 'No backtests yet.' : 'No runs match your filters.'}
                </div>
              ) : filteredList.map(b => {
                const net = parseFloat(b.stats.net_pnl)
                const positive = net >= 0
                const isBest = bestRun?.id === b.id
                return (
                  <div
                    key={b.id}
                    onClick={() => loadDetail(b.id)}
                    role="button"
                    tabIndex={0}
                    className={`block w-full text-left px-4 py-3 border-b border-border hover:bg-bg/40 cursor-pointer ${
                      selected?.id === b.id ? 'bg-bg/40' : ''
                    } ${isBest ? 'border-l-2 border-l-accent' : ''}`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-mono text-ink truncate flex items-center gap-2">
                        <span
                          onClick={e => toggleBookmark(b.id, e)}
                          title={b.bookmarked ? 'Remove bookmark' : 'Bookmark this run'}
                          className={`cursor-pointer text-sm leading-none ${
                            b.bookmarked ? 'text-warn' : 'text-dim/40 hover:text-dim'
                          }`}
                        >
                          ★
                        </span>
                        {isBest && <span className="text-[9px] tracking-widest text-accent">BEST</span>}
                        {b.label}
                      </span>
                      <span className={`text-xs font-mono tabular-nums ${
                        positive ? 'text-accent' : 'text-danger'
                      }`}>
                        {positive ? '+' : ''}${parseFloat(b.stats.net_pnl).toFixed(0)}
                      </span>
                    </div>
                    <div className="flex items-center justify-between mt-1 text-[10px] text-dim font-mono">
                      <span>{b.stats.trades} trades · {b.stats.win_rate}% win</span>
                      <span>{b.instrument} · {b.timeframe}</span>
                    </div>
                    <div className="mt-0.5 text-[9px] text-dim/60 font-mono tabular-nums">
                      {fmtRange(b.start_date, b.end_date)}
                    </div>
                  </div>
                )
              })}
            </div>
          </section>

          <section className="lg:col-span-3 bg-panel">
            <div className="px-4 py-2 border-b border-border flex items-center justify-between">
              <span className="text-[10px] tracking-[0.3em] text-dim uppercase">
                Detail {selected ? `· ${selected.label}` : ''}
              </span>
              {selected && (
                <div className="flex items-center gap-3">
                  <button
                    onClick={loadParamsFromSelected}
                    className="text-[10px] tracking-widest uppercase border border-warn text-warn px-3 py-1 hover:bg-warn/10"
                    title="Copy this run's strategy params + timeframe into the form above so you can tweak and re-run"
                  >
                    Load Params ↑
                  </button>
                  <button
                    onClick={applyConfig}
                    className="text-[10px] tracking-widest uppercase border border-accent text-accent px-3 py-1 hover:bg-accent/10"
                    title="Push this backtest's strategy params to the live bot config"
                  >
                    Apply to Bot
                  </button>
                  <button
                    onClick={() => deleteRun(selected.id)}
                    className="text-[10px] tracking-widest uppercase text-danger/70 hover:text-danger"
                  >
                    Delete
                  </button>
                </div>
              )}
            </div>
            {!selected ? (
              <div className="px-4 py-6 text-[11px] text-dim">
                Select a backtest from the list to see full stats and trades.
              </div>
            ) : (
              <div className="p-4 space-y-4">
                <div className="grid grid-cols-4 gap-2 text-[11px] font-mono tabular-nums">
                  <Stat label="Net P&L" value={`$${selected.stats.net_pnl}`} highlight={parseFloat(selected.stats.net_pnl) >= 0 ? 'accent' : 'danger'} />
                  <Stat label="Win Rate" value={`${selected.stats.win_rate}%`} />
                  <Stat label="Trades" value={selected.stats.trades.toString()} />
                  <Stat label="Profit Factor" value={selected.stats.profit_factor?.toFixed(2) ?? '∞'} />
                  <Stat label="Wins / Losses" value={`${selected.stats.wins} / ${selected.stats.losses}`} />
                  <Stat label="Avg Win" value={`$${selected.stats.avg_win}`} />
                  <Stat label="Avg Loss" value={`$${selected.stats.avg_loss}`} />
                  <Stat label="Max Drawdown" value={`$${selected.stats.max_drawdown}`} />
                  <Stat label="Expectancy" value={selected.stats.expectancy != null ? `$${parseFloat(selected.stats.expectancy).toFixed(2)}` : '—'} highlight={selected.stats.expectancy != null && parseFloat(selected.stats.expectancy) > 0 ? 'accent' : undefined} />
                  <Stat label="Combine" value={selected.stats.passed_combine == null ? '—' : selected.stats.passed_combine ? 'PASS' : 'FAIL'} highlight={selected.stats.passed_combine ? 'accent' : selected.stats.passed_combine === false ? 'danger' : undefined} />
                  <Stat label="MLL Breach" value={selected.stats.mll_breached == null ? '—' : selected.stats.mll_breached ? 'YES' : 'NO'} highlight={selected.stats.mll_breached ? 'danger' : 'accent'} />
                  <Stat label="Bars" value={selected.bars_processed.toString()} />
                  <Stat label="Starting" value={`$${selected.starting_balance}`} />
                  <Stat label="Ending" value={`$${selected.ending_balance}`} />
                  <Stat label="Timeframe" value={selected.timeframe} />
                  <Stat label="Duration" value={`${selected.duration_seconds}s`} />
                  <Stat label="Date Range" value={fmtRange(selected.start_date, selected.end_date)} />
                </div>

                {selected.stats.equity_curve && selected.stats.equity_curve.length > 1 && (
                  <EquityCurve curve={selected.stats.equity_curve} startingBalance={selected.starting_balance} />
                )}

                {selected.stats.by_killzone && Object.keys(selected.stats.by_killzone).length > 0 && (
                  <KillzoneBreakdown byKillzone={selected.stats.by_killzone} />
                )}

                <div>
                  <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">
                    Strategy Parameters (used for this run)
                  </div>
                  <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-[11px] font-mono tabular-nums">
                    {Object.entries(selected.config.strategy ?? {}).map(([k, v]) => (
                      <div key={k} className="bg-bg border border-border px-3 py-2 flex items-center justify-between gap-2">
                        <span className="text-[10px] uppercase tracking-wider text-dim">
                          {PARAM_LABELS[k] ?? k}
                        </span>
                        <span className="text-ink">{String(v)}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {selected.config.enabled_killzones && (
                  <div>
                    <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">
                      Killzones (used for this run)
                    </div>
                    <div className="flex gap-2 flex-wrap">
                      {selected.config.enabled_killzones.map(kz => (
                        <span key={kz} className="text-[10px] tracking-widest uppercase border border-border text-dim px-2 py-1 font-mono">
                          {kz}
                        </span>
                      ))}
                    </div>
                    <p className="text-[10px] text-dim/60 mt-1">
                      If your live killzones differ from these, a retest will trade different bars and produce different results.
                    </p>
                  </div>
                )}

                <div>
                  <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">Notes</div>
                  <textarea
                    value={noteText}
                    onChange={e => setNoteText(e.target.value)}
                    placeholder="Add notes about this run — what you changed, what to try next, observations…"
                    rows={3}
                    className="w-full bg-bg border border-border text-ink text-xs px-3 py-2 font-mono focus:outline-none focus:border-accent resize-none"
                  />
                  <button
                    onClick={saveNote}
                    disabled={noteSaving}
                    className="mt-1 text-[10px] tracking-widest uppercase border border-border text-dim hover:text-ink px-3 py-1 disabled:opacity-50"
                  >
                    {noteSaving ? 'Saving…' : 'Save Note'}
                  </button>
                </div>

                {/* Grade scorecard tiles */}
                {selected.trades.some(t => t.grade) && (
                  <div>
                    <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">
                      Grade Breakdown <span className="text-dim/50 normal-case tracking-normal">· click to filter</span>
                    </div>
                    <div className="grid grid-cols-5 gap-px bg-border border border-border mb-3">
                      {GRADE_TIERS.map(g => {
                        const s = gradeStats[g] ?? { count: 0, winRate: 0, profitFactor: null }
                        const isActive = gradeFilter === g
                        return (
                          <button
                            key={g}
                            onClick={() => setGradeFilter(isActive ? null : g)}
                            className={`p-2 text-center bg-panel transition-colors ${
                              isActive ? gradeColor(g) : 'text-dim hover:text-ink'
                            } ${s.count === 0 ? 'opacity-30 cursor-default' : 'cursor-pointer'}`}
                            disabled={s.count === 0}
                          >
                            <div className={`text-base font-bold font-mono ${isActive ? '' : 'text-inherit'}`}>{g}</div>
                            <div className="text-[9px] text-dim mt-0.5">{s.count} trade{s.count !== 1 ? 's' : ''}</div>
                            {s.count > 0 && (
                              <>
                                <div className="text-[10px] font-mono">{s.winRate}% WR</div>
                                <div className="text-[10px] font-mono">
                                  {s.profitFactor !== null ? `PF ${s.profitFactor.toFixed(1)}` : 'PF —'}
                                </div>
                              </>
                            )}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                )}

                {/* Trade list */}
                <div>
                  <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">
                    Trades
                    {gradeFilter && (
                      <span className="text-warn normal-case tracking-normal ml-2">
                        · {gradeFilter} only ({displayedTrades.length} of {selected.trades.length})
                      </span>
                    )}
                    {!gradeFilter && ` (${selected.trades.length})`}
                  </div>
                  <div className="max-h-[40vh] overflow-y-auto feed border border-border divide-y divide-border">
                    {displayedTrades.map((t, i) => {
                      const pnl = parseFloat(t.realized_pnl)
                      return (
                        <div key={i} className="px-3 py-2 text-[11px] font-mono">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3">
                              <span className="text-dim">{fmtBarTs(t.entry_ts)}</span>
                              <span className={t.side === 'long' ? 'text-accent' : 'text-danger'}>
                                {t.side.toUpperCase()}
                              </span>
                              <span className="text-dim tabular-nums">
                                {t.entry_price} → {t.exit_price}
                              </span>
                            </div>
                            <div className="flex items-center gap-2">
                              <span className={`tabular-nums font-bold ${pnl >= 0 ? 'text-accent' : 'text-danger'}`}>
                                {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                              </span>
                              {t.grade && (
                                <span className={`text-[9px] px-1.5 py-0.5 rounded-sm font-bold ${gradeBadge(t.grade)}`}>
                                  {t.grade}
                                </span>
                              )}
                            </div>
                          </div>
                          {t.criteria && (
                            <div className="flex gap-3 mt-1 text-[9px]">
                              {CRITERIA_KEYS.map(k => (
                                <span key={k} className={t.criteria![k] ? 'text-accent' : 'text-danger'}>
                                  {k}{t.criteria![k] ? '✓' : '✗'}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>
                      )
                    })}
                    {displayedTrades.length === 0 && (
                      <div className="px-3 py-4 text-[11px] text-dim">No trades match the current filter.</div>
                    )}
                  </div>
                </div>
              </div>
            )}
          </section>
        </div>
      </main>
    </div>
    </>
  )
}

function KillzoneBreakdown({ byKillzone }: { byKillzone: Record<string, KillzoneStat> }) {
  const rows = Object.entries(byKillzone).sort((a, b) => b[1].net_pnl - a[1].net_pnl)
  return (
    <div>
      <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">By Killzone</div>
      <div className="border border-border overflow-x-auto">
        <table className="w-full text-[11px] font-mono tabular-nums">
          <thead>
            <tr className="text-[9px] tracking-widest text-dim uppercase bg-bg/40">
              <th className="text-left px-3 py-1.5 font-normal">Killzone</th>
              <th className="text-right px-3 py-1.5 font-normal">Trades</th>
              <th className="text-right px-3 py-1.5 font-normal">Win %</th>
              <th className="text-right px-3 py-1.5 font-normal">W / L</th>
              <th className="text-right px-3 py-1.5 font-normal">Net P&L</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map(([kz, s]) => {
              const positive = s.net_pnl >= 0
              return (
                <tr key={kz} className="text-ink">
                  <td className="px-3 py-1.5 uppercase tracking-wider text-dim">{kz}</td>
                  <td className="px-3 py-1.5 text-right">{s.trades}</td>
                  <td className="px-3 py-1.5 text-right">{s.win_rate}%</td>
                  <td className="px-3 py-1.5 text-right">{s.wins} / {s.losses}</td>
                  <td className={`px-3 py-1.5 text-right ${positive ? 'text-accent' : 'text-danger'}`}>
                    {positive ? '+' : ''}${s.net_pnl.toFixed(2)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function EquityCurve({ curve, startingBalance }: { curve: [string, string][]; startingBalance: string }) {
  const start = parseFloat(startingBalance)
  const equities = curve.map(([, eq]) => parseFloat(eq))
  const min = Math.min(...equities)
  const max = Math.max(...equities)
  const range = max - min || 1
  const W = 600, H = 80
  const pts = equities.map((eq, i) => {
    const x = (i / (equities.length - 1)) * W
    const y = H - ((eq - min) / range) * (H - 4) - 2
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const zeroY = H - ((start - min) / range) * (H - 4) - 2
  const finalEq = equities[equities.length - 1]
  const positive = finalEq >= start

  return (
    <div>
      <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">Equity Curve</div>
      <div className="bg-bg border border-border p-2">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-20" preserveAspectRatio="none">
          <line x1="0" y1={zeroY.toFixed(1)} x2={W} y2={zeroY.toFixed(1)}
            stroke="#333" strokeWidth="1" strokeDasharray="4 4" />
          <polyline points={pts} fill="none"
            stroke={positive ? '#00ff41' : '#ff4444'} strokeWidth="1.5" />
        </svg>
        <div className="flex justify-between text-[9px] text-dim font-mono mt-1">
          <span>${min.toFixed(0)}</span>
          <span className={positive ? 'text-accent' : 'text-danger'}>
            ${finalEq.toFixed(2)} ({positive ? '+' : ''}{(finalEq - start).toFixed(2)})
          </span>
          <span>${max.toFixed(0)}</span>
        </div>
      </div>
    </div>
  )
}

function StrategyFieldInput({
  field, value, onChange, disabled,
}: {
  field: StrategyField
  value: string
  onChange: (v: string) => void
  disabled?: boolean
}) {
  if (field.kind === 'select') {
    return (
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono focus:outline-none focus:border-accent"
      >
        {field.options!.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    )
  }
  if (field.kind === 'toggle') {
    const on = value === 'true'
    return (
      <button
        type="button"
        onClick={() => onChange(on ? 'false' : 'true')}
        disabled={disabled}
        className={`text-[11px] tracking-widest uppercase px-3 py-1 border transition-colors ${
          on ? 'border-accent text-accent bg-accent/10' : 'border-border text-dim hover:text-ink'
        }`}
      >
        {on ? 'ON' : 'OFF'}
      </button>
    )
  }
  if (field.kind === 'list') {
    return (
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        placeholder="comma-separated, or leave blank"
        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono focus:outline-none focus:border-accent"
      />
    )
  }
  return (
    <div className="flex items-center gap-2">
      <input
        type="range"
        min={field.min}
        max={field.max}
        step={field.step}
        value={Number(value || field.min || 0)}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        className="flex-1 slider-accent"
      />
      <input
        type="number"
        min={field.min}
        max={field.max}
        step={field.step}
        value={value ?? ''}
        onChange={e => onChange(e.target.value)}
        disabled={disabled}
        className="w-20 bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
      />
    </div>
  )
}

function Stat({ label, value, highlight }: { label: string; value: string; highlight?: 'accent' | 'danger' }) {
  const colorClass = highlight === 'accent'
    ? 'text-accent'
    : highlight === 'danger'
      ? 'text-danger'
      : 'text-ink'
  return (
    <div className="bg-bg border border-border px-3 py-2">
      <div className="text-[9px] tracking-widest text-dim uppercase">{label}</div>
      <div className={`text-sm mt-1 ${colorClass}`}>{value}</div>
    </div>
  )
}

// Field definitions for the A/B variant editor. Most TP fields reuse their
// STRATEGY_FIELDS entry; partial_profit_r is a top-level config field (not in
// the strategy dict) so it gets an inline def here.
const TP_AB_FIELD_DEFS: Record<string, StrategyField> = (() => {
  const byKey = Object.fromEntries(STRATEGY_FIELDS.map(f => [f.key, f]))
  const defs: Record<string, StrategyField> = {}
  for (const key of TP_AB_FIELDS) {
    defs[key] = byKey[key] ?? {
      key,
      label: TP_AB_LABELS[key] ?? key,
      section: 'Take-Profit',
      min: 0, max: 5, step: 0.5,
      hint: '',
    }
  }
  return defs
})()

interface ABPanelProps {
  variants: ABVariant[]
  results: ABResult[] | null
  running: boolean
  startDate: string
  endDate: string
  onStartDate: (v: string) => void
  onEndDate: (v: string) => void
  supportedSymbols: string[]
  selectedInstruments: string[]
  onToggleInstrument: (sym: string) => void
  onAddVariant: () => void
  onRemoveVariant: (idx: number) => void
  onSetOverride: (idx: number, key: string, value: string) => void
  onClearOverride: (idx: number, key: string) => void
  variantTpValue: (v: ABVariant, key: string) => string
  onRun: () => void
  onLoadPreset: () => void
}

function ABPanel({
  variants, results, running,
  startDate, endDate, onStartDate, onEndDate,
  supportedSymbols, selectedInstruments, onToggleInstrument,
  onAddVariant, onRemoveVariant, onSetOverride, onClearOverride,
  variantTpValue, onRun, onLoadPreset,
}: ABPanelProps) {
  const combos = selectedInstruments.length * variants.length
  return (
    <div className="space-y-4">
      <p className="text-[11px] text-dim leading-relaxed">
        Runs on <span className="text-accent">Databento</span> data across the selected
        instruments and date range — a full matrix of each TP variant × each instrument.
        Non-TP strategy params stay identical to the form above; only the take-profit
        overrides differ per variant.
      </p>

      {/* Databento date range for the A/B matrix. */}
      <div className="border border-border bg-bg/30 p-3">
        <div className="text-[9px] tracking-widest text-dim uppercase mb-2">Databento Date Range</div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">Start</label>
            <input
              type="date"
              value={startDate}
              max={endDate}
              onChange={e => onStartDate(e.target.value)}
              disabled={running}
              className="w-full bg-bg border border-border text-ink text-sm px-3 py-2 font-mono focus:outline-none focus:border-warn"
            />
          </div>
          <div>
            <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">End</label>
            <input
              type="date"
              value={endDate}
              min={startDate}
              max={isoDate(new Date())}
              onChange={e => onEndDate(e.target.value)}
              disabled={running}
              className="w-full bg-bg border border-border text-ink text-sm px-3 py-2 font-mono focus:outline-none focus:border-warn"
            />
          </div>
        </div>
      </div>

      {/* Instrument multi-select — the matrix runs every variant on each. */}
      <div className="border border-border bg-bg/30 p-3">
        <div className="text-[9px] tracking-widest text-dim uppercase mb-2">
          Instruments
          <span className="text-faint ml-2 normal-case tracking-normal">select one or more</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {supportedSymbols.map(sym => {
            const on = selectedInstruments.includes(sym)
            return (
              <button
                key={sym}
                onClick={() => onToggleInstrument(sym)}
                disabled={running}
                className={`text-[11px] font-mono tracking-widest uppercase px-3 py-1.5 border disabled:opacity-50 ${
                  on
                    ? 'border-accent text-accent bg-accent/10'
                    : 'border-border text-dim hover:text-ink'
                }`}
              >
                {sym}
              </button>
            )
          })}
        </div>
        {selectedInstruments.length === 0 && (
          <p className="text-[10px] text-warn mt-2">Select at least one instrument.</p>
        )}
      </div>

      {/* Variant editor — one column per variant, editing only TP params. */}
      <div className="border border-border bg-bg/30 p-3">
        <div className="flex items-center justify-between mb-3">
          <div className="text-[9px] tracking-widest text-dim uppercase">
            Take-Profit Variants
            <span className="text-faint ml-2 normal-case tracking-normal">
              base config from form above · blank = inherit
            </span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onLoadPreset}
              disabled={running}
              className="text-[10px] tracking-widest uppercase px-3 py-1 border border-accent/40 text-accent/80 hover:text-accent hover:border-accent disabled:opacity-40"
              title="Load the 6 TJR-derived TP variant presets"
            >
              Load TJR Variants
            </button>
            <button
              onClick={onAddVariant}
              disabled={running || variants.length >= VARIANT_LETTERS.length}
              className="text-[10px] tracking-widest uppercase px-3 py-1 border border-border text-dim hover:text-ink disabled:opacity-40"
            >
              + Variant
            </button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[11px] font-mono">
            <thead>
              <tr className="text-[9px] tracking-widest text-dim uppercase">
                <th className="text-left px-2 py-1 font-normal">TP Param</th>
                {variants.map((v, i) => (
                  <th key={i} className="text-left px-2 py-1 font-normal" style={{ color: VARIANT_COLORS[i] }}>
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div>{v.name ?? `Variant ${VARIANT_LETTERS[i]}`}</div>
                        {v.rationale && (
                          <div className="text-[8px] text-faint normal-case tracking-normal font-sans mt-0.5 max-w-[160px] leading-tight" title={v.rationale}>
                            {v.rationale.length > 60 ? v.rationale.slice(0, 57) + '…' : v.rationale}
                          </div>
                        )}
                      </div>
                      {variants.length > 2 && (
                        <button
                          onClick={() => onRemoveVariant(i)}
                          disabled={running}
                          className="text-faint hover:text-danger normal-case shrink-0"
                          title="Remove variant"
                        >
                          ✕
                        </button>
                      )}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {TP_AB_FIELDS.map(key => {
                const field = TP_AB_FIELD_DEFS[key]
                return (
                  <tr key={key} className="align-top">
                    <td className="px-2 py-2 text-dim whitespace-nowrap">{TP_AB_LABELS[key] ?? field.label}</td>
                    {variants.map((v, i) => {
                      const overridden = key in v.overrides
                      return (
                        <td key={i} className="px-2 py-2 min-w-[160px]">
                          <StrategyFieldInput
                            field={field}
                            value={variantTpValue(v, key)}
                            onChange={val => onSetOverride(i, key, val)}
                            disabled={running}
                          />
                          {overridden && (
                            <button
                              onClick={() => onClearOverride(i, key)}
                              disabled={running}
                              className="text-[9px] text-faint hover:text-dim mt-1 uppercase tracking-wider"
                            >
                              reset to base
                            </button>
                          )}
                        </td>
                      )
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={onRun}
          disabled={running || selectedInstruments.length === 0}
          className="bg-accent/10 border border-accent text-accent text-xs tracking-widest uppercase px-5 py-2 hover:bg-accent/20 disabled:opacity-50"
        >
          {running ? 'Running A/B…' : `Run A/B Matrix (${combos})`}
        </button>
        {running && (
          <span className="text-[10px] text-dim font-mono">
            {(results ?? []).filter(r => r.status === 'done').length} / {combos} complete
          </span>
        )}
      </div>

      {results && <ABMatrixResults results={results} running={running} />}
    </div>
  )
}

// Group matrix results by instrument and render one comparison table +
// equity-curve overlay per instrument, stacked vertically.
function ABMatrixResults({ results, running }: { results: ABResult[]; running: boolean }) {
  // Preserve first-seen instrument order.
  const order: string[] = []
  for (const r of results) if (!order.includes(r.instrument)) order.push(r.instrument)
  return (
    <div className="space-y-6">
      {order.map(instr => {
        const group = results.filter(r => r.instrument === instr)
        return (
          <div key={instr} className="border border-border bg-bg/20 p-3 space-y-4">
            <div className="flex items-center gap-2">
              <span className="text-[11px] tracking-[0.3em] text-accent uppercase font-mono">{instr}</span>
              {running && (
                <span className="text-[10px] text-faint font-mono">
                  {group.filter(r => r.status === 'done').length}/{group.length}
                </span>
              )}
            </div>
            <ABComparison results={group} />
            {group.some(r => r.stats?.equity_curve?.length) && (
              <ABEquityOverlay results={group} />
            )}
          </div>
        )
      })}
    </div>
  )
}

// Side-by-side metrics table: one column per variant, best value per row highlighted.
function ABComparison({ results }: { results: ABResult[] }) {
  const num = (s: string | null | undefined) => (s == null ? NaN : parseFloat(s))
  type Row = {
    label: string
    get: (s: BacktestStats) => number | null
    fmt: (s: BacktestStats) => string
    better: 'high' | 'low'
  }
  const rows: Row[] = [
    { label: 'Net P&L',      get: s => num(s.net_pnl),       fmt: s => `${num(s.net_pnl) >= 0 ? '+' : ''}$${num(s.net_pnl).toFixed(0)}`, better: 'high' },
    { label: 'Win Rate',     get: s => s.win_rate,           fmt: s => `${s.win_rate}%`,                                                better: 'high' },
    { label: 'Trades',       get: s => s.trades,             fmt: s => String(s.trades),                                                better: 'high' },
    { label: 'Profit Factor',get: s => s.profit_factor ?? null, fmt: s => s.profit_factor == null ? '—' : s.profit_factor.toFixed(2),  better: 'high' },
    { label: 'Expectancy',   get: s => num(s.expectancy),    fmt: s => s.expectancy == null ? '—' : `$${num(s.expectancy).toFixed(2)}`, better: 'high' },
    { label: 'Max Drawdown', get: s => num(s.max_drawdown),  fmt: s => `$${num(s.max_drawdown).toFixed(0)}`,                             better: 'low' },
  ]
  const done = results.filter(r => r.stats)
  const bestIdx = (row: Row): number => {
    let best = -1, bestVal = NaN
    results.forEach((r, i) => {
      if (!r.stats) return
      const v = row.get(r.stats)
      if (v == null || isNaN(v)) return
      if (isNaN(bestVal) || (row.better === 'high' ? v > bestVal : v < bestVal)) {
        bestVal = v; best = i
      }
    })
    return best
  }
  return (
    <div>
      <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">Comparison</div>
      <div className="border border-border overflow-x-auto">
        <table className="w-full text-[11px] font-mono tabular-nums">
          <thead>
            <tr className="text-[9px] tracking-widest text-dim uppercase bg-bg/40">
              <th className="text-left px-3 py-1.5 font-normal">Metric</th>
              {results.map((r, i) => (
                <th key={i} className="text-right px-3 py-1.5 font-normal" style={{ color: VARIANT_COLORS[i] }}>
                  {r.letter}
                  {r.status !== 'done' && (
                    <span className="text-faint ml-1 normal-case">
                      {r.status === 'error' ? '(err)' : r.status === 'pending' ? '(…)' : '(run)'}
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map(row => {
              const best = done.length > 1 ? bestIdx(row) : -1
              return (
                <tr key={row.label} className="text-ink">
                  <td className="px-3 py-1.5 text-dim uppercase tracking-wider">{row.label}</td>
                  {results.map((r, i) => (
                    <td
                      key={i}
                      className={`px-3 py-1.5 text-right ${i === best ? 'text-accent font-medium bg-accent/5' : ''}`}
                    >
                      {r.stats ? row.fmt(r.stats) : '—'}
                    </td>
                  ))}
                </tr>
              )
            })}
            {/* Combine pass + MLL breach are optional flags. */}
            <tr className="text-ink">
              <td className="px-3 py-1.5 text-dim uppercase tracking-wider">Combine</td>
              {results.map((r, i) => (
                <td key={i} className="px-3 py-1.5 text-right">
                  {r.stats?.passed_combine == null ? '—'
                    : r.stats.passed_combine ? <span className="text-accent">PASS</span>
                    : <span className="text-danger">FAIL</span>}
                </td>
              ))}
            </tr>
            <tr className="text-ink">
              <td className="px-3 py-1.5 text-dim uppercase tracking-wider">MLL Breach</td>
              {results.map((r, i) => (
                <td key={i} className="px-3 py-1.5 text-right">
                  {r.stats?.mll_breached == null ? '—'
                    : r.stats.mll_breached ? <span className="text-danger">YES</span>
                    : <span className="text-accent">no</span>}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  )
}

// Overlay each variant's equity curve on a single chart, one colored line each.
function ABEquityOverlay({ results }: { results: ABResult[] }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const chart = createChart(el, {
      autoSize: true,
      height: 240,
      layout: {
        background: { color: 'transparent' },
        textColor: '#6c82a8',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.03)' },
        horzLines: { color: 'rgba(255,255,255,0.03)' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.05)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.05)', timeVisible: false },
    })

    results.forEach((r, i) => {
      const curve = r.stats?.equity_curve
      if (!curve || curve.length === 0) return
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const series = chart.addSeries(LineSeries as any, {
        color: VARIANT_COLORS[i],
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      })
      // Equity curve timestamps may collide across variants; lightweight-charts
      // requires strictly ascending unique times, so index by bar ordinal.
      const data = curve.map(([, eq], j) => ({ time: (j + 1) as never, value: parseFloat(eq) }))
      series.setData(data)
    })
    chart.timeScale().fitContent()

    return () => { chart.remove() }
  }, [results])

  return (
    <div>
      <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-2">Equity Curves</div>
      <div className="bg-bg border border-border p-2">
        <div ref={containerRef} className="w-full" style={{ height: 240 }} />
        <div className="flex flex-wrap gap-3 mt-2 px-1">
          {results.map((r, i) => (
            r.stats?.equity_curve?.length ? (
              <div key={i} className="flex items-center gap-1.5 text-[10px] font-mono">
                <span className="inline-block w-3 h-0.5" style={{ background: VARIANT_COLORS[i] }} />
                <span className="text-dim">
                  {r.letter}
                  {r.stats && (
                    <span className="ml-1 text-faint">
                      {parseFloat(r.stats.net_pnl) >= 0 ? '+' : ''}${parseFloat(r.stats.net_pnl).toFixed(0)}
                    </span>
                  )}
                </span>
              </div>
            ) : null
          ))}
        </div>
      </div>
    </div>
  )
}
