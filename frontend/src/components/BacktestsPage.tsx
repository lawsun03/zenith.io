import { useEffect, useMemo, useState } from 'react'
import { fmtBarTs } from '../utils/format'

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
}

interface BacktestSummary {
  id: string
  label: string
  completed_at: string | null
  instrument: string
  timeframe: string
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
  pnl: string
  grade?: string
  criteria?: GradeCriteria
}

const GRADE_TIERS = ['A+', 'A', 'A-', 'B', 'B-'] as const
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

interface Availability {
  earliest: string | null
  latest: string | null
  bars: number
  available: boolean
  reason?: string
  timeframe?: string
}

interface StrategyField {
  key: string
  label: string
  min: number
  max: number
  step: number
  hint: string
}

const STRATEGY_FIELDS: StrategyField[] = [
  {
    key: 'swing_lookback', label: 'Swing Lookback', min: 1, max: 20, step: 1,
    hint: 'How many bars on each side must be lower (or higher) for the bot to call a price a "swing low" (or "swing high"). Lower = more swings, including small wiggles. Higher = only major levels. Try: 2 for active trading, 5+ for slower setups.',
  },
  {
    key: 'min_penetration', label: 'Min Penetration ($)', min: 0, max: 5, step: 0.05,
    hint: 'How far past a swing level price must move to count as a real sweep (stop hunt). Filters out tiny one-tick wicks. Try: 0.20 for MGC (~2 ticks). Raise it if the bot is firing on noise.',
  },
  {
    key: 'multi_bar_window', label: 'Multi-Bar Window', min: 1, max: 20, step: 1,
    hint: 'How many bars a slow stop-hunt can span before we stop calling it a sweep. 1 = single-bar sweeps only. Higher = catches grinds that take 3–5 bars to penetrate a level.',
  },
  {
    key: 'atr_period', label: 'ATR Period', min: 5, max: 50, step: 1,
    hint: 'How many bars to average for "what does normal range look like right now?". Shorter (5–10) reacts faster to changing volatility; longer (20+) smooths things out. 14 is standard.',
  },
  {
    key: 'body_atr_multiple', label: 'Body ATR Multiple', min: 0, max: 5, step: 0.1,
    hint: 'How big the trigger candle\'s body must be relative to recent ATR. 1.0 = body ≥ 1× ATR (normal impulse). 2.0 = need a strong move. Higher = stricter, fewer signals.',
  },
  {
    key: 'min_body_to_range_ratio', label: 'Body / Range Ratio', min: 0, max: 1, step: 0.05,
    hint: 'How "solid" the trigger candle must be: body length ÷ full high-to-low range. 0.6 = body fills 60% of the candle. Filters out doji/indecision wicks. Higher = only big-bodied moves.',
  },
  {
    key: 'min_absolute_body', label: 'Min Body ($)', min: 0, max: 10, step: 0.1,
    hint: 'Hard floor on the trigger candle\'s body size in dollars. Catches cases where ATR is tiny (overnight chop) but the relative ratio still passes. Try: 1.0 for MGC.',
  },
  {
    key: 'displacement_window_bars', label: 'Displacement Window', min: 1, max: 20, step: 1,
    hint: 'After a sweep happens, how many bars to wait for a strong move (displacement) in the opposite direction. If nothing impulsive shows up in that window, the setup expires.',
  },
  {
    key: 'stop_buffer', label: 'Stop Buffer ($)', min: 0, max: 5, step: 0.05,
    hint: 'Extra dollars added beyond the sweep extreme when placing the stop. Bigger buffer = wider stops, fewer stop-outs from wick noise, but worse risk/reward. Try: 0.30 for MGC.',
  },
  {
    key: 'r_multiple', label: 'R Multiple', min: 0.5, max: 10, step: 0.1,
    hint: 'Reward-to-risk ratio: target distance ÷ stop distance. 2.0 = risk $50 to make $100. Higher targets = more profit per win but lower win rate. 2.0–3.0 is a common sweet spot.',
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
}

type DataSource = 'local' | 'databento'

export function BacktestsPage() {
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
  const [bentoMeta, setBentoMeta] = useState<{
    cost: number
    cachedThrough: string | null
    willFetch: number
  } | null>(null)
  const [bentoLoading, setBentoLoading] = useState(false)
  const [gradeFilter, setGradeFilter] = useState<string | null>(null)

  // Filtering and sorting for the saved-runs list.
  type SortKey = 'pnl_desc' | 'pnl_asc' | 'trades_desc' | 'win_rate_desc' | 'profit_factor_desc' | 'date_desc'
  const [sortBy, setSortBy] = useState<SortKey>('pnl_desc')
  const [filterTf, setFilterTf] = useState<string>('all')
  const [minTrades, setMinTrades] = useState<number>(0)
  const [minWinRate, setMinWinRate] = useState<number>(0)
  const [onlyProfitable, setOnlyProfitable] = useState<boolean>(false)
  const [balancedPreset, setBalancedPreset] = useState<boolean>(false)
  const [bookmarkedOnly, setBookmarkedOnly] = useState<boolean>(false)

  // Load the live bot's current strategy as the initial set, so the page
  // opens with the "match my config" baseline rather than dataclass defaults.
  useEffect(() => {
    fetch('/api/config')
      .then(r => r.json())
      .then(d => {
        if (d?.strategy && typeof d.strategy === 'object') {
          const next: Record<string, string> = {}
          for (const k of Object.keys(STRATEGY_DEFAULTS)) {
            next[k] = String(d.strategy[k] ?? STRATEGY_DEFAULTS[k])
          }
          setStrategy(next)
        }
      })
      .catch(() => {})
  }, [])

  function setStratField(k: string, v: string) {
    setStrategy(s => ({ ...s, [k]: v }))
    setStrategyDirty(true)
  }

  function resetStrategyToConfig() {
    fetch('/api/config')
      .then(r => r.json())
      .then(d => {
        const next: Record<string, string> = {}
        for (const k of Object.keys(STRATEGY_DEFAULTS)) {
          next[k] = String(d.strategy?.[k] ?? STRATEGY_DEFAULTS[k])
        }
        setStrategy(next)
        setStrategyDirty(false)
      })
  }

  async function switchToDataSource(src: DataSource) {
    setDataSource(src)
    setBentoMeta(null)
    if (src !== 'databento') return
    setBentoLoading(true)
    try {
      const cfg = await fetch('/api/config').then(r => r.json())
      const symbol = cfg?.instrument ?? 'MGC'
      const res = await fetch('/api/databento/fetch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start: startDate, end: endDate, symbol, dry_run: true }),
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

  function gradeColor(g: string): string {
    switch (g) {
      case 'A+': return 'ring-1 ring-accent text-accent bg-accent/10'
      case 'A':  return 'ring-1 ring-accent/60 text-accent/80 bg-accent/5'
      case 'A-': return 'ring-1 ring-warn/60 text-warn bg-warn/5'
      case 'B':  return 'ring-1 ring-warn/30 text-warn/60 bg-warn/5'
      case 'B-': return 'ring-1 ring-danger/40 text-danger/70 bg-danger/5'
      default:   return 'ring-1 ring-border text-dim'
    }
  }

  function gradeBadge(g: string | undefined): string {
    switch (g) {
      case 'A+': return 'bg-accent text-bg'
      case 'A':  return 'bg-accent/70 text-bg'
      case 'A-': return 'bg-warn text-bg'
      case 'B':  return 'bg-warn/60 text-bg'
      case 'B-': return 'bg-danger/70 text-bg'
      default:   return 'bg-dim/20 text-dim'
    }
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
      if (dataSource !== 'databento') {
        setMsg('Fetching historical bars…')
      }
      if (dataSource === 'databento') {
        setMsg('Fetching bars from Databento…')
        const cfg = await fetch('/api/config').then(r => r.json())
        const symbol = cfg?.instrument ?? 'MGC'
        const bentoRes = await fetch('/api/databento/fetch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ start: startDate, end: endDate, symbol, dry_run: false }),
        })
        const bentoBody = await bentoRes.json()
        if (!bentoBody.ok) {
          setMsg(`Databento fetch failed: ${bentoBody.reason}`)
          setRunning(false)
          return
        }
      }
      const beforeCount = list.length
      // Build the strategy override payload. Strings preserve decimal
      // precision; the backend will coerce them through Pydantic.
      const stratPayload: Record<string, string | number> = {}
      for (const f of STRATEGY_FIELDS) {
        stratPayload[f.key] = strategy[f.key] ?? STRATEGY_DEFAULTS[f.key]
      }
      const res = await fetch('/api/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          label: label || null,
          timeframe,
          start_date: startDate,
          end_date: endDate,
          strategy: stratPayload,
        }),
      })
      const body = await res.json()
      if (!body.ok) {
        setMsg(body.reason ?? 'Failed to start')
        setRunning(false)
        return
      }
      setMsg(`Running (pid ${body.pid}) — ${timeframe}  ${startDate} → ${endDate}`)
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
    if (!confirm('Delete all unbookmarked runs? This cannot be undone.')) return
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
    if (dataSource === 'databento') {
      setBentoMeta(null)
    }
  }, [startDate, endDate])

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
    if (!selected?.trades) return {} as Record<GradeTier, { count: number; winRate: number; profitFactor: number | null }>
    return GRADE_TIERS.reduce((acc, g) => {
      const gt = selected.trades.filter(t => t.grade === g)
      const wins = gt.filter(t => parseFloat(t.pnl) > 0)
      const losses = gt.filter(t => parseFloat(t.pnl) < 0)
      const grossWin = wins.reduce((s, t) => s + parseFloat(t.pnl), 0)
      const grossLoss = Math.abs(losses.reduce((s, t) => s + parseFloat(t.pnl), 0))
      acc[g] = {
        count: gt.length,
        winRate: gt.length > 0 ? Math.round(wins.length / gt.length * 100) : 0,
        profitFactor: grossLoss > 0 ? Math.round((grossWin / grossLoss) * 100) / 100 : null,
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
    const ok = window.confirm(
      'Apply this backtest’s strategy parameters to the live bot config and hot-reload the strategy?\n\n' +
      'The bot keeps running; the strategy runner is rebuilt with the new params. Open positions, risk state, and broker connection are untouched. The strategy\'s internal state (ATR window, recent swings) resets — next bar rebuilds it.'
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
    <div className="min-h-screen bg-bg scanlines">
      <header className="border-b border-border px-6 py-4 flex items-center justify-between">
        <div className="flex items-baseline gap-4">
          <span className="text-xs tracking-[0.4em] text-dim">TOPSTEP-BOT</span>
          <span className="text-xs tracking-[0.3em] text-accent">BACKTESTS</span>
        </div>
        <a href="/" className="text-xs tracking-widest text-dim hover:text-ink uppercase">
          ← Dashboard
        </a>
      </header>

      <main className="p-6 max-w-[1400px] mx-auto space-y-6">
        <section className="bg-panel border border-border p-5">
          <h2 className="text-[10px] tracking-[0.3em] text-accent uppercase mb-4">
            Run New Backtest
          </h2>
          <p className="text-[11px] text-dim mb-4 leading-relaxed">
            Pulls historical bars from TopstepX for the date range you pick, runs the
            current strategy config against them in a separate process, and saves the
            full stats. The live bot keeps running untouched.
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
            <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
              {STRATEGY_FIELDS.map(f => (
                <div key={f.key}>
                  <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                    {f.label}
                  </label>
                  <div className="flex items-center gap-2">
                    <input
                      type="range"
                      min={f.min}
                      max={f.max}
                      step={f.step}
                      value={Number(strategy[f.key] ?? f.min)}
                      onChange={e => setStratField(f.key, e.target.value)}
                      disabled={running}
                      className="flex-1 slider-accent"
                    />
                    <input
                      type="number"
                      min={f.min}
                      max={f.max}
                      step={f.step}
                      value={strategy[f.key] ?? ''}
                      onChange={e => setStratField(f.key, e.target.value)}
                      disabled={running}
                      className="w-20 bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                    />
                  </div>
                  <p className="text-[10px] text-dim/70 mt-1 leading-relaxed">{f.hint}</p>
                </div>
              ))}
            </div>
          </details>

          {/* Data source toggle */}
          <div className="border border-border bg-bg/30 p-3">
            <div className="text-[9px] tracking-widest text-dim uppercase mb-2">Data Source</div>
            <div className="flex items-center gap-2">
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
                  <Stat label="Bars" value={selected.bars_processed.toString()} />
                  <Stat label="Starting" value={`$${selected.starting_balance}`} />
                  <Stat label="Ending" value={`$${selected.ending_balance}`} />
                  <Stat label="Duration" value={`${selected.duration_seconds}s`} />
                </div>

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
                      const pnl = parseFloat(t.pnl)
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
