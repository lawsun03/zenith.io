import { useEffect, useState } from 'react'
import type { BotConfig, StrategyConfig } from '../types'
import { useConfirm } from '../hooks/useConfirm'

interface AccountInfo {
  name: string
  balance: number
  can_trade: boolean
  simulated: boolean
}

interface Props {
  isOpen: boolean
  onClose: () => void
  config: BotConfig | null
  onSave: (config: BotConfig) => Promise<void>
  saving: boolean
  saveError: string | null
}

type FieldType = 'slider' | 'select'

interface FieldDef {
  key: string
  label: string
  type: FieldType
  section: 'bot' | 'strategy'
  min?: number
  max?: number
  step?: number
  hint: string
  options?: string[]
}

const INSTRUMENTS = ['MGC', 'MNQ', 'NQ', 'GC', 'MES', 'ES']
const TIMEFRAMES   = ['1min', '3min', '5min', '15min', '30min', '1h']

const FIELDS: FieldDef[] = [
  {
    key: 'instrument', label: 'Instrument', type: 'select', section: 'bot',
    options: INSTRUMENTS,
    hint: 'Contract to trade. Bars file auto-loads as bars_{SYMBOL}.csv',
  },
  {
    key: 'timeframes', label: 'Timeframe', type: 'select', section: 'bot',
    options: TIMEFRAMES,
    hint: 'Bar interval used for pattern detection and paper replay',
  },
  {
    key: 'replay_start_delay_s', label: 'Replay Start Delay (s)', type: 'slider', section: 'bot',
    min: 0, max: 30, step: 1,
    hint: 'Seconds the bot waits before replaying — open the dashboard during this window',
  },
  {
    key: 'replay_delay_ms', label: 'Replay Speed (ms/bar)', type: 'slider', section: 'bot',
    min: 0, max: 1000, step: 10,
    hint: '0 = instant  ·  50 ms = ~17 min  ·  100 ms = ~33 min for 20k bars',
  },
  {
    key: 'swing_lookback', label: 'Swing Lookback', type: 'slider', section: 'strategy',
    min: 1, max: 20, step: 1,
    hint: 'How many bars on each side must be lower (or higher) for the bot to call a price a "swing low" (or "swing high"). Lower = more swings including small wiggles. Higher = only major levels. Try: 2 for active trading, 5+ for slower setups.',
  },
  {
    key: 'min_penetration', label: 'Min Penetration ($)', type: 'slider', section: 'strategy',
    min: 0, max: 5, step: 0.05,
    hint: 'How far past a swing level price must move to count as a real sweep (stop hunt). Filters out tiny one-tick wicks. Try: 0.20 for MGC. Raise it if the bot fires on noise.',
  },
  {
    key: 'multi_bar_window', label: 'Multi-Bar Window', type: 'slider', section: 'strategy',
    min: 1, max: 20, step: 1,
    hint: 'How many bars a slow stop-hunt can span. 1 = single-bar sweeps only. Higher = catches grinds that take 3–5 bars to penetrate a level.',
  },
  {
    key: 'atr_period', label: 'ATR Period', type: 'slider', section: 'strategy',
    min: 5, max: 50, step: 1,
    hint: 'Bars to average for "what does normal range look like right now?". Shorter (5–10) reacts faster to changing volatility; longer (20+) smooths it out. 14 is standard.',
  },
  {
    key: 'body_atr_multiple', label: 'Body ATR Multiple', type: 'slider', section: 'strategy',
    min: 0, max: 5, step: 0.1,
    hint: 'How big the trigger candle\'s body must be vs recent ATR. 1.0 = body ≥ 1× ATR (normal impulse). 2.0 = need a strong move. Higher = stricter, fewer signals.',
  },
  {
    key: 'min_body_to_range_ratio', label: 'Body / Range Ratio', type: 'slider', section: 'strategy',
    min: 0, max: 1, step: 0.05,
    hint: 'How "solid" the trigger candle must be: body length ÷ full high-to-low range. 0.6 = body fills 60% of the candle. Filters doji/indecision wicks.',
  },
  {
    key: 'min_absolute_body', label: 'Min Body ($)', type: 'slider', section: 'strategy',
    min: 0, max: 10, step: 0.1,
    hint: 'Hard floor on the trigger candle\'s body size in dollars. Catches cases where ATR is tiny but the ratio still passes. Try: 1.0 for MGC.',
  },
  {
    key: 'displacement_window_bars', label: 'Displacement Window', type: 'slider', section: 'strategy',
    min: 1, max: 20, step: 1,
    hint: 'After a sweep, how many bars to wait for a strong move in the opposite direction. If nothing impulsive shows up in that window, the setup expires.',
  },
  {
    key: 'stop_buffer', label: 'Stop Buffer ($)', type: 'slider', section: 'strategy',
    min: 0, max: 5, step: 0.05,
    hint: 'Extra dollars added beyond the sweep extreme when placing the stop. Bigger buffer = wider stops, fewer noise stop-outs, worse risk/reward. Try: 0.30 for MGC.',
  },
  {
    key: 'r_multiple', label: 'R Multiple', type: 'slider', section: 'strategy',
    min: 0.5, max: 10, step: 0.1,
    hint: 'Reward-to-risk ratio: target distance ÷ stop distance. 2.0 = risk $50 to make $100. Higher targets = more profit per win but lower win rate. 2.0–3.0 is a common sweet spot.',
  },
  {
    key: 'trend_ema_period', label: 'Trend EMA Period', type: 'slider', section: 'strategy',
    min: 0, max: 200, step: 1,
    hint: '0 = disabled. When active, long signals require close > EMA, shorts require close < EMA. Warmup: filter inactive until N bars seen.',
  },
  {
    key: 'min_atr_filter', label: 'Min ATR Filter', type: 'slider', section: 'strategy',
    min: 0, max: 3.0, step: 0.1,
    hint: '0 = disabled. Minimum ATR required to take a trade. Raise to skip low-volatility dead markets (try 0.5–1.0 on MGC).',
  },
  {
    key: 'max_atr_filter', label: 'Max ATR Filter', type: 'slider', section: 'strategy',
    min: 0, max: 6.0, step: 0.1,
    hint: '0 = disabled. Maximum ATR allowed before blocking a trade. Lower to skip high-volatility whipsaw sessions (try 3.0–4.0 on MGC).',
  },
  {
    key: 'cooldown_bars_after_stop', label: 'Post-Stop Cooldown (bars)', type: 'slider', section: 'strategy',
    min: 0, max: 20, step: 1,
    hint: '0 = disabled. Bars to wait before taking another trade after a stop-loss. Prevents re-entering immediately into the same losing move.',
  },
  {
    key: 'min_penetration_atr_factor', label: 'Penetration ATR Factor', type: 'slider', section: 'strategy',
    min: 0, max: 0.5, step: 0.01,
    hint: '0 = disabled (uses fixed Min Penetration $). When active, required sweep penetration = this factor × current ATR — automatically scales with volatility.',
  },
  {
    key: 'vp_enabled', label: 'VP Filter Enabled', type: 'select', section: 'strategy',
    options: ['true', 'false'],
    hint: 'Enable volume profile filter + target override. false = uses fixed r_multiple only, ignoring prior session value area.',
  },
  {
    key: 'vp_value_area_pct', label: 'VP Value Area %', type: 'slider', section: 'strategy',
    min: 0.5, max: 0.9, step: 0.01,
    hint: 'Fraction of prior session volume defining the value area. 0.70 = the standard 70% rule. Higher = wider area, more signals pass.',
  },
  {
    key: 'vp_filter_tolerance', label: 'VP Tolerance ($)', type: 'slider', section: 'strategy',
    min: 0, max: 10, step: 0.1,
    hint: 'Price units outside the value area edge that still pass the filter. 0 = strict (inside VA only). 2.0 = loose (20 ticks beyond edge accepted).',
  },
  {
    key: 'vp_hvn_threshold', label: 'VP HVN Threshold', type: 'slider', section: 'strategy',
    min: 1.0, max: 4.0, step: 0.1,
    hint: 'A price level is a High Volume Node if its volume > mean × this value. Higher = fewer, more significant HVNs. Try 1.5–2.0.',
  },
  {
    key: 'vp_min_target_r', label: 'VP Min Target R', type: 'slider', section: 'strategy',
    min: 0.5, max: 3.0, step: 0.1,
    hint: 'A VP level must deliver at least this many R to be used as target. Too close levels are skipped; falls back to r_multiple if none qualify.',
  },
  {
    key: 'ifvg_entry_mode', label: 'iFVG Entry Mode', type: 'select', section: 'strategy',
    options: ['ifvg_edge', 'retrace_ce', 'close'],
    hint: 'Where to enter when an iFVG arms: ifvg_edge = at the inversion edge; retrace_ce = wait for retrace to the FVG midpoint (CE); close = on close back inside the FVG.',
  },
  {
    key: 'ifvg_stop_buffer_ticks', label: 'iFVG Stop Buffer (ticks)', type: 'slider', section: 'strategy',
    min: 0, max: 10, step: 0.5,
    hint: 'Ticks beyond the iFVG extreme for the stop loss. 1.0 = one tick past the high/low that defined the FVG. Add buffer to avoid tight stop-outs on wicks.',
  },
  {
    key: 'ifvg_sweep_window_bars', label: 'iFVG Sweep Window (bars)', type: 'slider', section: 'strategy',
    min: 1, max: 30, step: 1,
    hint: 'Bars since the sweep that still qualify as "recent" for the grader (Rule A). Beyond this cap, setups without a delivery FVG are capped at B and filtered.',
  },
  {
    key: 'ifvg_min_displacement_mult', label: 'iFVG Min Displacement Mult', type: 'slider', section: 'strategy',
    min: 0, max: 3.0, step: 0.1,
    hint: 'Fibonacci displacement quality (Rule E): reversal leg must be ≥ this multiple of the manipulation leg. 0 = disabled.',
  },
  {
    key: 'grader_min_grade', label: 'Grade Floor', type: 'select', section: 'strategy',
    options: ['F', 'D', 'C', 'B', 'A'],
    hint: 'Minimum setup grade required to trade. F = no floor (all structural-pass setups trade). C blocks D/F-grade setups. Today\'s scorecard: A≥75, B≥55, C≥35, D≥15.',
  },
  {
    key: 'ifvg_gapping_sack_enabled', label: 'Gapping-Sack Rule (Rule I)', type: 'select', section: 'strategy',
    options: ['true', 'false'],
    hint: 'Reject setups whose displacement printed 2+ overlapping same-side FVGs unless a 30min FVG contains them. Trend legs print exactly this pattern — false allows with-trend continuation entries.',
  },
  {
    key: 'ifvg_tp1_fraction', label: 'iFVG TP1 Fraction', type: 'slider', section: 'strategy',
    min: 0, max: 1, step: 0.05,
    hint: 'Fraction of position to close at the structural TP1 (nearest HTF swing in trade direction). 0.5 = half off. 0 = skip partial, hold full size to final target.',
  },
  {
    key: 'ifvg_be_after_tp1', label: 'iFVG Breakeven After TP1', type: 'select', section: 'strategy',
    options: ['true', 'false'],
    hint: 'Move stop to break-even after the structural TP1 fills. true = stop moves to entry price; false = stop stays at original level.',
  },
  {
    key: 'htf_bias_enabled', label: 'HTF Bias Filter', type: 'select', section: 'strategy',
    options: ['true', 'false'],
    hint: 'Block signals that fight the 4h swing-structure bias. Bullish 4h blocks shorts; bearish blocks longs. When the bias agrees, the VP value-area filter is bypassed.',
  },
  {
    key: 'htf_target_enabled', label: 'HTF Targets', type: 'select', section: 'strategy',
    options: ['true', 'false'],
    hint: 'Use a 4h FVG (then nearest 30min swing) as the take-profit target instead of the fixed R-multiple. Falls back to VP, then r_multiple.',
  },
  {
    key: 'htf_target_min_r', label: 'HTF Min Target R', type: 'slider', section: 'strategy',
    min: 1.0, max: 5.0, step: 0.1,
    hint: 'An HTF level must deliver at least this many R to be used as target. Below this, falls back to VP / fixed R.',
  },
]

const inputClass =
  'w-full bg-bg border border-border text-ink text-sm px-3 py-2 font-mono focus:outline-none focus:border-accent'

const KILLZONES: { name: string; label: string; window: string }[] = [
  { name: 'asia',      label: 'Asia',       window: '4:00 PM – 7:00 PM PT (Tokyo morning)' },
  { name: 'london',    label: 'London',     window: '11:00 PM – 2:00 AM PT (London open)' },
  { name: 'london_ny', label: 'London/NY',  window: '3:00 AM – 5:30 AM PT (London/NY overlap)' },
  { name: 'ny_am',     label: 'NY AM',      window: '5:30 AM – 8:00 AM PT' },
  { name: 'ny_pm',     label: 'NY PM',      window: '10:00 AM – 1:00 PM PT' },
]

export function ConfigPanel({ isOpen, onClose, config, onSave, saving, saveError }: Props) {
  const { confirm, modal } = useConfirm()
  const [form, setForm] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [restartMsg, setRestartMsg] = useState<string | null>(null)
  const [reloading, setReloading] = useState(false)
  const [reloadMsg, setReloadMsg] = useState<string | null>(null)
  const [accounts, setAccounts] = useState<AccountInfo[]>([])
  const [enabledKillzones, setEnabledKillzones] = useState<string[]>(['london', 'ny_am', 'ny_pm'])
  // instrument → field → value (strings; the backend coerces on validation)
  const [overrides, setOverrides] = useState<Record<string, Record<string, string>>>({})
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [presets, setPresets] = useState<{ name: string; saved_at: string }[]>([])
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [savePresetName, setSavePresetName] = useState('')
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const [savingPreset, setSavingPreset] = useState(false)

  const emergencyInstruments: string[] =
    config?.instruments && config.instruments.length > 0
      ? config.instruments
      : Object.keys(config?.emergency_stop_distance ?? { MGC: 0, MNQ: 0, MES: 0 })

  const isLive = config?.mode === 'live'

  useEffect(() => {
    if (!config) return
    setForm({
      instrument:           config.instrument,
      timeframes:           config.timeframes[0] ?? '1min',
      replay_delay_ms:      String(config.replay_delay_ms ?? 0),
      replay_start_delay_s: String(config.replay_start_delay_s ?? 5),
      account_name:         config.account_name ?? '',
      entry_mode:           config.entry_mode ?? 'market',
      forming_bar_entries:  String(config.forming_bar_entries ?? false),
      contracts:            String(config.contracts ?? 1),
      risk_per_trade_pct:   String(config.risk_per_trade_pct ?? 0.25),
      partial_profit_r:     String(config.partial_profit_r ?? 0),
      max_entry_slippage_frac: String(config.max_entry_slippage_frac ?? 0),
      ...Object.fromEntries(
        emergencyInstruments.map(sym => [
          `emergency_stop_distance_${sym}`,
          String(config.emergency_stop_distance?.[sym] ?? 0),
        ])
      ),
      emergency_target_r:   String(config.emergency_target_r ?? 2.0),
      naked_grace_seconds:  String(config.naked_grace_seconds ?? 15.0),
      commission_per_contract: String(config.commission_per_contract ?? 0.0),
      signal_instrument:    config.signal_instrument ?? '',
      ...Object.fromEntries(
        Object.entries(config.strategy).map(([k, v]) => [k, String(v)])
      ),
    })
    setEnabledKillzones(config.enabled_killzones ?? ['london', 'ny_am', 'ny_pm'])
    setOverrides(Object.fromEntries(
      Object.entries(config.strategy_overrides ?? {}).map(([inst, ov]) => [
        inst,
        Object.fromEntries(Object.entries(ov).map(([k, v]) => [k, String(v)])),
      ])
    ))
  }, [config])

  useEffect(() => {
    if (!isLive || !isOpen) return
    fetch('/api/accounts')
      .then(r => r.json())
      .then(d => setAccounts(d.accounts ?? []))
      .catch(() => setAccounts([]))
  }, [isLive, isOpen])

  useEffect(() => {
    if (!isOpen) return
    fetch('/api/config/presets')
      .then(r => r.json())
      .then(setPresets)
      .catch(() => setPresets([]))
  }, [isOpen])

  async function handleSavePreset() {
    const name = savePresetName.trim()
    if (!name) return
    setSavingPreset(true)
    try {
      await fetch('/api/config/presets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
      const updated = await fetch('/api/config/presets').then(r => r.json())
      setPresets(updated)
      setSavePresetName('')
    } finally {
      setSavingPreset(false)
    }
  }

  async function handleApplyPreset(name: string) {
    if (!await confirm(`Load preset "${name}"? This will overwrite current config.`, { title: 'Load Preset', variant: 'accent', confirmLabel: 'Load' })) return
    await fetch(`/api/config/presets/${encodeURIComponent(name)}/apply`, { method: 'POST' })
    onClose()
  }

  async function handleDeletePreset(name: string) {
    await fetch(`/api/config/presets/${encodeURIComponent(name)}`, { method: 'DELETE' })
    setPresets(ps => ps.filter(p => p.name !== name))
  }

  const set = (key: string, value: string) => setForm(f => ({ ...f, [key]: value }))

  const handleSave = async () => {
    const strategy: StrategyConfig = {
      swing_lookback:           parseInt(form.swing_lookback)           || 2,
      min_penetration:          form.min_penetration                    || '0.20',
      multi_bar_window:         parseInt(form.multi_bar_window)         || 3,
      atr_period:               parseInt(form.atr_period)               || 14,
      body_atr_multiple:        form.body_atr_multiple                  || '1.0',
      min_body_to_range_ratio:  form.min_body_to_range_ratio            || '0.6',
      min_absolute_body:        form.min_absolute_body                  || '1.0',
      displacement_window_bars: parseInt(form.displacement_window_bars) || 5,
      stop_buffer:              form.stop_buffer                        || '0.30',
      r_multiple:               form.r_multiple                         || '2.5',
      trend_ema_period:         parseInt(form.trend_ema_period)         || 0,
      min_atr_filter:           form.min_atr_filter                     || '0',
      max_atr_filter:           form.max_atr_filter                     || '0',
      cooldown_bars_after_stop:    parseInt(form.cooldown_bars_after_stop)    || 0,
      min_penetration_atr_factor:  form.min_penetration_atr_factor            || '0',
      vp_enabled:               form.vp_enabled !== 'false',
      vp_tick_size:             form.vp_tick_size                       || '0.10',
      vp_value_area_pct:        parseFloat(form.vp_value_area_pct)      || 0.70,
      vp_filter_tolerance:      form.vp_filter_tolerance                || '2.0',
      vp_hvn_threshold:         parseFloat(form.vp_hvn_threshold)       || 1.5,
      vp_min_target_r:          form.vp_min_target_r                    || '1.0',
      htf_bias_enabled:         form.htf_bias_enabled === 'true',
      htf_bias_timeframe:       form.htf_bias_timeframe                 || '4h',
      htf_bias_lookback:        parseInt(form.htf_bias_lookback)        || 3,
      htf_target_enabled:       form.htf_target_enabled === 'true',
      htf_target_min_r:         form.htf_target_min_r                   || '2.0',
      htf_swing_timeframe:      form.htf_swing_timeframe                || '30min',
      ifvg_entry_mode:          form.ifvg_entry_mode                    || 'ifvg_edge',
      ifvg_stop_buffer_ticks:   form.ifvg_stop_buffer_ticks             || '1.0',
      ifvg_sweep_window_bars:   parseInt(form.ifvg_sweep_window_bars)   || 10,
      ifvg_min_displacement_mult: form.ifvg_min_displacement_mult       || '1.0',
      grader_min_grade:         form.grader_min_grade                   || 'F',
      ifvg_gapping_sack_enabled: form.ifvg_gapping_sack_enabled !== 'false',
      ifvg_macro_windows:       (form.ifvg_macro_windows || '')
                                  .split(',').map((s: string) => s.trim()).filter(Boolean),
      ifvg_news_blackout:       (form.ifvg_news_blackout || '')
                                  .split(',').map((s: string) => s.trim()).filter(Boolean),
      ifvg_rule_f_enabled:      form.ifvg_rule_f_enabled !== 'false',
      ifvg_tp1_fraction:        form.ifvg_tp1_fraction                   || '0.5',
      ifvg_be_after_tp1:        form.ifvg_be_after_tp1 !== 'false',
    }
    await onSave({
      instrument:           form.instrument?.trim().toUpperCase() || 'MGC',
      timeframes:           [form.timeframes || '1min'],
      replay_delay_ms:      parseInt(form.replay_delay_ms)      || 0,
      replay_start_delay_s: parseInt(form.replay_start_delay_s) || 5,
      account_name:         form.account_name?.trim() || null,
      entry_mode:           form.entry_mode || 'market',
      forming_bar_entries:  form.forming_bar_entries === 'true',
      contracts:            parseInt(form.contracts) || 1,
      risk_per_trade_pct:   parseFloat(form.risk_per_trade_pct) || 0,
      partial_profit_r:     parseFloat(form.partial_profit_r) || 0,
      max_entry_slippage_frac: parseFloat(form.max_entry_slippage_frac) || 0,
      emergency_stop_distance: Object.fromEntries(
        emergencyInstruments.map(sym => [
          sym,
          parseFloat(form[`emergency_stop_distance_${sym}`]) || 0,
        ])
      ),
      emergency_target_r:   parseFloat(form.emergency_target_r) || 2.0,
      naked_grace_seconds:  parseFloat(form.naked_grace_seconds) || 15.0,
      commission_per_contract: parseFloat(form.commission_per_contract) || 0.0,
      enabled_killzones:    enabledKillzones,
      signal_instrument:    form.signal_instrument?.trim().toUpperCase() || null,
      strategy,
      strategy_overrides: Object.fromEntries(
        Object.entries(overrides)
          .map(([inst, ov]) => [
            inst,
            Object.fromEntries(Object.entries(ov).filter(([k, v]) => k && v !== '')),
          ])
          .filter(([, ov]) => Object.keys(ov as object).length > 0)
      ),
    })
    setSaved(true)
    setTimeout(() => setSaved(false), 3000)
  }

  const handleReloadStrategy = async () => {
    setReloading(true)
    setReloadMsg(null)
    try {
      await handleSave()
      const res = await fetch('/api/strategy/reload', { method: 'POST' })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        setReloadMsg(body.reason || 'Reload failed')
      } else {
        setReloadMsg('Strategy reloaded!')
        setTimeout(() => setReloadMsg(null), 3000)
      }
    } catch (e) {
      setReloadMsg(String(e))
    } finally {
      setReloading(false)
    }
  }

  const handleRunBacktest = async () => {
    setRestarting(true)
    setRestartMsg(null)
    try {
      await handleSave()
      const res = await fetch('/api/replay/restart', { method: 'POST' })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        setRestartMsg(body.reason || 'Restart failed')
      } else {
        setRestartMsg('Restarting...')
        setTimeout(() => setRestartMsg(null), 3000)
      }
    } catch (e) {
      setRestartMsg(String(e))
    } finally {
      setRestarting(false)
    }
  }

  if (!isOpen) return null

  return (
    <>
      {modal}
      <div className="fixed inset-0 bg-black/50 z-40" onClick={onClose} />
      <aside className="fixed right-0 top-0 h-full w-[22rem] bg-panel border-l border-border z-50 flex flex-col">
        <header className="bg-panel border-b border-border px-5 py-4 flex items-center justify-between flex-shrink-0">
          <span className="text-xs tracking-[0.3em] text-dim uppercase">Configuration</span>
          <button onClick={onClose} className="text-dim hover:text-ink text-xl leading-none">&times;</button>
        </header>

        <div className="flex-1 overflow-y-auto bg-panel p-5 space-y-7">
          {/* Presets */}
          <section>
            <h3 className="text-[10px] tracking-[0.3em] text-accent uppercase mb-3">Presets</h3>
            {presets.length === 0 && (
              <p className="text-[10px] text-faint mb-3">No presets saved yet.</p>
            )}
            <div className="flex flex-wrap gap-1.5 mb-3">
              {presets.map(p => (
                <div key={p.name} className="flex items-center gap-1 border border-border px-2 py-1 text-[10px]">
                  <button
                    onClick={() => handleApplyPreset(p.name)}
                    className="text-dim hover:text-ink tracking-wide"
                    title={`Saved ${new Date(p.saved_at).toLocaleDateString()}`}
                  >
                    {p.name}
                  </button>
                  <button onClick={() => handleDeletePreset(p.name)} className="text-faint hover:text-danger ml-1">×</button>
                </div>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                type="text"
                placeholder="Preset name..."
                value={savePresetName}
                onChange={e => setSavePresetName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleSavePreset()}
                className="flex-1 bg-bg border border-border text-ink text-[10px] px-2 py-1.5 font-mono focus:outline-none focus:border-accent"
              />
              <button
                onClick={handleSavePreset}
                disabled={!savePresetName.trim() || savingPreset}
                className="border border-border text-dim text-[10px] tracking-widest uppercase px-3 py-1.5 hover:text-ink disabled:opacity-40"
              >
                Save
              </button>
            </div>
          </section>

          {isLive && (
            <section>
              <h3 className="text-[10px] tracking-[0.3em] text-accent uppercase mb-4">Account</h3>
              <div>
                <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                  Active Account
                </label>
                <select
                  value={form.account_name ?? ''}
                  onChange={e => setForm(f => ({ ...f, account_name: e.target.value }))}
                  className={inputClass + ' cursor-pointer'}
                >
                  <option value="" className="bg-panel">— select account —</option>
                  {accounts.map(a => (
                    <option key={a.name} value={a.name} className="bg-panel">
                      {a.name}  ${a.balance.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                      {a.simulated ? '  (sim)' : ''}
                    </option>
                  ))}
                </select>
                {accounts.length === 0 && (
                  <p className="text-[10px] text-dim/60 mt-1">Loading accounts…</p>
                )}
                <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                  Select the Combine, Express, or Practice account to trade on. Requires restart to take effect.
                </p>
              </div>
            </section>
          )}
          <section>
            <h3 className="text-[10px] tracking-[0.3em] text-accent uppercase mb-4">
              Active Killzones
            </h3>
            <p className="text-[10px] text-dim/80 mb-3 leading-relaxed">
              The bot only places entry orders during these windows. Exits run 24/5 via the bracket regardless.
            </p>
            <div className="space-y-2">
              {KILLZONES.map(kz => {
                const on = enabledKillzones.includes(kz.name)
                return (
                  <label
                    key={kz.name}
                    className={`flex items-center justify-between px-3 py-2 border cursor-pointer ${
                      on ? 'border-accent bg-accent/5' : 'border-border bg-bg/40'
                    }`}
                  >
                    <div>
                      <div className={`text-xs ${on ? 'text-accent' : 'text-ink'}`}>{kz.label}</div>
                      <div className="text-[10px] text-dim font-mono mt-0.5">{kz.window}</div>
                    </div>
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={e => {
                        setEnabledKillzones(prev =>
                          e.target.checked
                            ? [...prev, kz.name]
                            : prev.filter(n => n !== kz.name)
                        )
                      }}
                      className="accent-accent"
                    />
                  </label>
                )
              })}
            </div>
            {enabledKillzones.length === 0 && (
              <p className="text-[10px] text-danger mt-2">
                All killzones disabled — the bot will never place entries.
              </p>
            )}
          </section>

          {(['bot', 'strategy'] as const).map(section => (
            <section key={section}>
              <h3 className="text-[10px] tracking-[0.3em] text-accent uppercase mb-4">
                {section === 'bot' ? 'Bot & Simulation' : 'Strategy Parameters'}
              </h3>
              <div className="space-y-4">
                {section === 'bot' && (
                  <>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-2">
                        Entry Mode
                      </label>
                      <div className="flex gap-0">
                        {(['market', 'limit'] as const).map(mode => (
                          <button
                            key={mode}
                            type="button"
                            onClick={() => set('entry_mode', mode)}
                            className={`flex-1 text-[10px] tracking-widest uppercase px-3 py-2 border ${
                              form.entry_mode === mode
                                ? 'border-accent bg-accent/10 text-accent'
                                : 'border-border text-dim hover:text-ink'
                            } ${mode === 'market' ? 'border-r-0' : ''}`}
                          >
                            {mode === 'market' ? 'Market Fill' : 'Limit Entry'}
                          </button>
                        ))}
                      </div>
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        {form.entry_mode === 'limit'
                          ? 'Limit order at the FVG level. Stop + target placed after fill. Stays working until cancelled — no timeout.'
                          : 'Market order fills immediately. Stop + target placed after fill is confirmed. No SDK bracket wrapper.'}
                      </p>
                    </div>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-2">
                        Entry Confirmation
                      </label>
                      <div className="flex gap-0">
                        {(['false', 'true'] as const).map(v => (
                          <button
                            key={v}
                            type="button"
                            onClick={() => set('forming_bar_entries', v)}
                            className={`flex-1 text-[10px] tracking-widest uppercase px-3 py-2 border ${
                              (form.forming_bar_entries ?? 'false') === v
                                ? 'border-accent bg-accent/10 text-accent'
                                : 'border-border text-dim hover:text-ink'
                            } ${v === 'false' ? 'border-r-0' : ''}`}
                          >
                            {v === 'false' ? 'Closed Bar' : 'Forming Bar'}
                          </button>
                        ))}
                      </div>
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        {form.forming_bar_entries === 'true'
                          ? 'Enters mid-bar the moment the forming bar touches the inversion price. NOT covered by the backtest validation.'
                          : 'Waits for the confirmation bar to close before entering — the path the walk-forward validated. Hot-applied.'}
                      </p>
                    </div>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        Contracts Per Signal
                      </label>
                      <div className="flex items-center gap-2">
                        <input
                          type="range"
                          min={1}
                          max={30}
                          step={1}
                          value={parseInt(form.contracts ?? '1') || 1}
                          onChange={e => set('contracts', e.target.value)}
                          className="flex-1 slider-accent"
                        />
                        <input
                          type="number"
                          min={1}
                          max={30}
                          step={1}
                          value={form.contracts ?? '1'}
                          onChange={e => set('contracts', e.target.value)}
                          className="w-20 bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                        />
                      </div>
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Number of contracts placed per signal. Hot-applied immediately — no restart needed.
                      </p>
                    </div>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        Risk % Per Trade (0 = off)
                      </label>
                      <input
                        type="number"
                        min={0}
                        max={5}
                        step={0.05}
                        value={form.risk_per_trade_pct ?? '0.25'}
                        onChange={e => set('risk_per_trade_pct', e.target.value)}
                        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Percent of account equity risked per trade. Size = budget ÷ stop distance, capped at max contracts. 0 disables (uses fixed contracts). Hot-applied — no restart.
                      </p>
                    </div>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        Partial Profit (R, 0 = off)
                      </label>
                      <input
                        type="number"
                        min={0}
                        max={5}
                        step={0.25}
                        value={form.partial_profit_r ?? '0'}
                        onChange={e => set('partial_profit_r', e.target.value)}
                        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Take half off at this R-multiple then move the stop to break-even. For 1-contract entries, the scale-out is skipped but the stop still moves to break-even at this level. 0 disables. Hot-applied — affects the next entry.
                      </p>
                    </div>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        Max Entry Slippage (× stop dist, 0 = off)
                      </label>
                      <input
                        type="number"
                        min={0}
                        max={2}
                        step={0.1}
                        value={form.max_entry_slippage_frac ?? '0'}
                        onChange={e => set('max_entry_slippage_frac', e.target.value)}
                        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Abort guard: if a market entry fills beyond this fraction of the stop distance past the signal price, flatten immediately instead of bracketing (the tightened stop would sit inside the retrace zone). 0.5 = abort when slip exceeds half the stop. Hot-applied.
                      </p>
                    </div>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        Naked Grace Period (s)
                      </label>
                      <input
                        type="number"
                        min={0}
                        max={120}
                        step={1}
                        value={form.naked_grace_seconds ?? '15'}
                        onChange={e => set('naked_grace_seconds', e.target.value)}
                        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Seconds a position may be naked (no stop/target) before the reconciler places emergency protection. Suppresses false alarms during the fill→bracket race. Hot-applied.
                      </p>
                    </div>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        Commission / Contract
                      </label>
                      <input
                        type="number"
                        min={0}
                        step={0.01}
                        value={form.commission_per_contract ?? '0'}
                        onChange={e => set('commission_per_contract', e.target.value)}
                        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Cost per contract per fill side (entry + exit charged separately). Deducted from realized P&amp;L so daily P&amp;L matches broker net. Hot-applied.
                      </p>
                    </div>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        Emergency Target R
                      </label>
                      <input
                        type="number"
                        min={0.5}
                        max={10}
                        step={0.5}
                        value={form.emergency_target_r ?? '2.0'}
                        onChange={e => set('emergency_target_r', e.target.value)}
                        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Target distance for an emergency bracket = this R × emergency stop distance. Hot-applied.
                      </p>
                    </div>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-2">
                        Emergency Stop Distance (pts)
                      </label>
                      <div className="space-y-2">
                        {emergencyInstruments.map(sym => (
                          <div key={sym} className="flex items-center gap-2">
                            <span className="text-[10px] text-dim font-mono w-10">{sym}</span>
                            <input
                              type="number"
                              min={0.1}
                              max={200}
                              step={0.1}
                              value={form[`emergency_stop_distance_${sym}`] ?? ''}
                              onChange={e => set(`emergency_stop_distance_${sym}`, e.target.value)}
                              className="flex-1 bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                            />
                          </div>
                        ))}
                      </div>
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Price points from broker avg entry for an emergency re-attached stop, per instrument. Hot-applied.
                      </p>
                    </div>
                  </>
                )}
                {section === 'strategy' && (
                  <>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        Macro Windows (NY time, comma-separated)
                      </label>
                      <textarea
                        rows={2}
                        value={form.ifvg_macro_windows ?? ''}
                        onChange={e => set('ifvg_macro_windows', e.target.value)}
                        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono focus:outline-none focus:border-accent resize-none"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        NY local windows near economic releases — adds grade bonus context only, does not block signals.
                      </p>
                    </div>
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        News Blackout (UTC ISO intervals, comma-separated)
                      </label>
                      <textarea
                        rows={2}
                        value={form.ifvg_news_blackout ?? ''}
                        onChange={e => set('ifvg_news_blackout', e.target.value)}
                        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono focus:outline-none focus:border-accent resize-none"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        UTC ISO 8601 intervals during which all signals are blocked (e.g. 2026-06-06T12:30/2026-06-06T13:00). Empty = no blackout.
                      </p>
                    </div>
                  </>
                )}
                {FIELDS.filter(f => f.section === section).flatMap(field => {
                  const el = (
                    <div key={field.key}>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        {field.label}
                      </label>
                      {field.type === 'select' ? (
                        <select
                          value={form[field.key] ?? ''}
                          onChange={e => set(field.key, e.target.value)}
                          className={inputClass + ' cursor-pointer'}
                        >
                          {field.options!.map(o => (
                            <option key={o} value={o} className="bg-panel">{o}</option>
                          ))}
                        </select>
                      ) : (
                        <div className="flex items-center gap-2">
                          <input
                            type="range"
                            min={field.min}
                            max={field.max}
                            step={field.step ?? 1}
                            value={Number(form[field.key] ?? field.min ?? 0)}
                            onChange={e => set(field.key, e.target.value)}
                            className="flex-1 slider-accent"
                          />
                          <input
                            type="number"
                            min={field.min}
                            max={field.max}
                            step={field.step ?? 1}
                            value={form[field.key] ?? ''}
                            onChange={e => set(field.key, e.target.value)}
                            className="w-20 bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                          />
                        </div>
                      )}
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">{field.hint}</p>
                    </div>
                  )
                  if (field.key !== 'instrument') return [el]
                  return [
                    el,
                    <div key="signal_instrument">
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">Signal Instrument</label>
                      <input
                        className={inputClass}
                        value={form.signal_instrument ?? ''}
                        onChange={e => set('signal_instrument', e.target.value)}
                        placeholder="blank = same as instrument (e.g. GC)"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Leave blank to use the same instrument for signals and execution. Set to GC to read structure off full Gold while trading MGC. Requires restart.
                      </p>
                    </div>,
                  ]
                })}
              </div>
            </section>
          ))}

          {(config?.instruments?.length ?? 0) > 0 && (
            <section>
              <h3 className="text-[10px] tracking-[0.3em] text-accent uppercase mb-3">
                Per-Instrument Overrides
              </h3>
              <p className="text-[10px] text-dim/80 mb-3 leading-relaxed">
                Override individual strategy parameters for one instrument (e.g. a wider stop buffer on MNQ).
                Unset fields use the base strategy values above. Applied on Save &amp; Reload Strategy.
              </p>
              <div className="space-y-4">
                {config!.instruments!.map(sym => {
                  const ov = overrides[sym] ?? {}
                  const usedKeys = Object.keys(ov)
                  const strategyKeys = FIELDS.filter(f => f.section === 'strategy').map(f => f.key)
                  return (
                    <div key={sym}>
                      <div className="text-[10px] tracking-wider text-ink uppercase mb-1.5">{sym}</div>
                      {usedKeys.length === 0 && (
                        <p className="text-[10px] text-faint mb-1.5">No overrides — uses base strategy.</p>
                      )}
                      <div className="space-y-1.5">
                        {usedKeys.map(key => (
                          <div key={key} className="flex items-center gap-1.5">
                            <select
                              value={key}
                              onChange={e => setOverrides(o => {
                                const next = { ...(o[sym] ?? {}) }
                                const val = next[key]
                                delete next[key]
                                next[e.target.value] = val
                                return { ...o, [sym]: next }
                              })}
                              className="flex-1 bg-bg border border-border text-ink text-[10px] px-2 py-1.5 font-mono focus:outline-none focus:border-accent cursor-pointer"
                            >
                              {strategyKeys.map(k => (
                                <option key={k} value={k} className="bg-panel" disabled={k !== key && k in ov}>{k}</option>
                              ))}
                            </select>
                            <input
                              value={ov[key]}
                              onChange={e => setOverrides(o => ({
                                ...o, [sym]: { ...(o[sym] ?? {}), [key]: e.target.value },
                              }))}
                              className="w-20 bg-bg border border-border text-ink text-[10px] px-2 py-1.5 font-mono tabular-nums focus:outline-none focus:border-accent"
                            />
                            <button
                              onClick={() => setOverrides(o => {
                                const next = { ...(o[sym] ?? {}) }
                                delete next[key]
                                return { ...o, [sym]: next }
                              })}
                              className="text-faint hover:text-danger text-sm leading-none px-1"
                              title="Remove override"
                            >
                              ×
                            </button>
                          </div>
                        ))}
                      </div>
                      <button
                        onClick={() => {
                          const free = strategyKeys.find(k => !(k in ov))
                          if (!free) return
                          setOverrides(o => ({ ...o, [sym]: { ...(o[sym] ?? {}), [free]: '' } }))
                        }}
                        className="mt-1.5 border border-border text-dim text-[10px] tracking-widest uppercase px-2 py-1 hover:text-ink"
                      >
                        + Override
                      </button>
                    </div>
                  )
                })}
              </div>
            </section>
          )}
        </div>

        <footer className="bg-panel border-t border-border p-5 flex-shrink-0 space-y-3">
          {saveError && <p className="text-[10px] text-danger">{saveError}</p>}
          {restartMsg && (
            <p className={`text-[10px] ${restartMsg === 'Restarting...' ? 'text-accent' : 'text-danger'}`}>
              {restartMsg}
            </p>
          )}
          {reloadMsg && (
            <p className={`text-[10px] ${reloadMsg === 'Strategy reloaded!' ? 'text-accent' : 'text-danger'}`}>
              {reloadMsg}
            </p>
          )}
          <div className="flex gap-3">
            <button
              onClick={handleSave}
              disabled={saving}
              className="flex-1 bg-accent/10 border border-accent text-accent text-xs tracking-widest uppercase px-4 py-2 hover:bg-accent/20 disabled:opacity-50"
            >
              {saving ? 'Saving...' : saved ? 'Saved!' : 'Save'}
            </button>
            <button
              onClick={onClose}
              className="flex-1 border border-border text-dim text-xs tracking-widest uppercase px-4 py-2 hover:text-ink hover:border-ink"
            >
              Close
            </button>
          </div>
          <button
            onClick={handleReloadStrategy}
            disabled={reloading || saving}
            className="w-full border border-accent/50 text-accent/80 text-xs tracking-widest uppercase px-4 py-2 hover:bg-accent/10 disabled:opacity-50"
          >
            {reloading ? 'Reloading...' : 'Save & Reload Strategy'}
          </button>
          <button
            onClick={handleRunBacktest}
            disabled={restarting || saving}
            className="w-full border border-border text-dim/60 text-xs tracking-widest uppercase px-4 py-2 hover:bg-bg disabled:opacity-50"
          >
            {restarting ? 'Starting...' : 'Save & Run Backtest'}
          </button>
          <p className="text-[10px] text-dim text-center">
            Contracts &amp; mode: instant · Strategy: Reload · Instrument/TF: restart
          </p>
          {form.instrument && (
            <p className="text-[10px] text-dim/60 text-center font-mono">
              bars_{form.instrument.toUpperCase()}.csv
            </p>
          )}
        </footer>
      </aside>
    </>
  )
}
