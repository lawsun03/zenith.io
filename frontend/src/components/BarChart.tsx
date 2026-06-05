import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries } from 'lightweight-charts'
import type { ChartCallbacks } from '../hooks/useStream'

const TF_SECONDS: Record<string, number> = {
  '1min': 60, '3min': 180, '5min': 300,
  '15min': 900, '30min': 1800, '1h': 3600,
  '4h': 14400, '1d': 86400,
}

const TF_LABELS: Record<string, string> = {
  '1min': '1m', '3min': '3m', '5min': '5m',
  '15min': '15m', '30min': '30m', '1h': '1h',
  '4h': '4h', '1d': '1D',
}

const INSTRUMENT_NAMES: Record<string, string> = {
  MGC: 'Micro Gold', MNQ: 'Micro Nasdaq', MES: 'Micro S&P',
  GC: 'Gold', NQ: 'Nasdaq', ES: 'S&P 500',
}

interface Props {
  callbacksRef: React.MutableRefObject<ChartCallbacks>
  timeframe?: string
  activeSymbol?: string
}

function isValidBar(b: { time: number; open: number; high: number; low: number; close: number }): boolean {
  return (
    typeof b.open  === 'number' && isFinite(b.open)  &&
    typeof b.high  === 'number' && isFinite(b.high)  &&
    typeof b.low   === 'number' && isFinite(b.low)   &&
    typeof b.close === 'number' && isFinite(b.close) &&
    typeof b.time  === 'number' && isFinite(b.time)
  )
}

export function BarChart({ callbacksRef, timeframe, activeSymbol }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const lastBarTimeRef = useRef<number | null>(null)
  const [countdown, setCountdown] = useState<string | null>(null)
  const [viewTf, setViewTf] = useState<string>(timeframe ?? '1min')
  const viewTfRef = useRef<string>(timeframe ?? '1min')
  // timeframeRef tracks the live prop value so closures created at mount don't
  // capture the undefined that exists before config loads.
  const timeframeRef = useRef<string | undefined>(timeframe)
  // activeSymbolRef so the forming-bar closure always reads the current symbol.
  const activeSymbolRef = useRef<string>(activeSymbol ?? '')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const seriesRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null)

  // Keep viewTfRef in sync so the chart useEffect closure reads fresh values.
  useEffect(() => { viewTfRef.current = viewTf }, [viewTf])
  useEffect(() => { activeSymbolRef.current = activeSymbol ?? '' }, [activeSymbol])

  // Keep timeframeRef current. When timeframe first becomes defined (config
  // loaded after mount), also auto-select the bot's trading TF as the view.
  useEffect(() => {
    const prev = timeframeRef.current
    timeframeRef.current = timeframe
    if (timeframe && !prev) {
      setViewTf(timeframe)
    }
  }, [timeframe])

  // Countdown ticker — time until the next bar boundary (next minute, next 5min, etc.)
  // Wall-clock based, so it's accurate even when REST bar delivery lags.
  useEffect(() => {
    const tfSecs = TF_SECONDS[viewTf] ?? null
    if (!tfSecs) { setCountdown(null); return }
    const tick = () => {
      const nowSecs = Math.floor(Date.now() / 1000)
      const nextBoundary = (Math.floor(nowSecs / tfSecs) + 1) * tfSecs
      const remaining = nextBoundary - nowSecs
      const m = Math.floor(remaining / 60)
      const s = remaining % 60
      setCountdown(m > 0 ? `${m}:${String(s).padStart(2, '0')}` : `${s}s`)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [viewTf])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const PT = 'America/Los_Angeles'
    const fmtChartTime = (timeSecs: number) =>
      new Date(timeSecs * 1000).toLocaleTimeString('en-US', {
        timeZone: PT,
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
      })
    const fmtChartDateTime = (timeSecs: number) => {
      const d = new Date(timeSecs * 1000)
      const date = d.toLocaleDateString('en-US', {
        timeZone: PT,
        month: '2-digit',
        day: '2-digit',
      })
      return `${date} ${fmtChartTime(timeSecs)}`
    }

    const chart = createChart(el, {
      autoSize: true,
      height: 320,
      layout: {
        background: { color: 'transparent' },
        textColor:  '#6c82a8',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize:   11,
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.03)' },
        horzLines: { color: 'rgba(255,255,255,0.03)' },
      },
      crosshair: {
        vertLine: { color: 'rgba(37,99,235,0.4)', labelBackgroundColor: '#1e3a8a' },
        horzLine: { color: 'rgba(37,99,235,0.4)', labelBackgroundColor: '#1e3a8a' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.05)' },
      localization: {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        timeFormatter: ((time: any) => fmtChartDateTime(Number(time))) as any,
      },
      timeScale: {
        borderColor:    'rgba(255,255,255,0.05)',
        timeVisible:    true,
        secondsVisible: false,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        tickMarkFormatter: ((time: any) => fmtChartTime(Number(time))) as any,
      },
    })

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const series = chart.addSeries(CandlestickSeries as any, {
      upColor:         '#3ee0a5',
      downColor:       '#f87171',
      borderUpColor:   '#3ee0a5',
      borderDownColor: '#f87171',
      wickUpColor:     'rgba(62,224,165,0.6)',
      wickDownColor:   'rgba(248,113,113,0.6)',
    })
    seriesRef.current = series
    chartRef.current = chart

    // Use legacy setMarkers API — createSeriesMarkers (v5 plugin) hooks into the
    // bar colorer during positioning and crashes when marker times have no bar.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const seriesAny = series as any
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let currentMarkers: any[] = []
    const markersPlugin = {
      markers: () => currentMarkers,
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      setMarkers: (m: any[]) => { currentMarkers = m; seriesAny.setMarkers(m) },
    }

    // Pre-populate the chart with historical bars so it's not empty on connect.
    // Initial bar load — the [viewTf] effect can't do this because seriesRef
    // isn't set when it fires on mount. Subsequent TF/symbol switches are handled by [viewTf]/[activeSymbol].
    const instrParam = activeSymbol ? `&instrument=${activeSymbol}` : ''
    fetch(`/api/bars?timeframe=${viewTfRef.current}&limit=500${instrParam}`)
      .then(r => r.json())
      .then(d => {
        const validBars = Array.isArray(d.bars) ? d.bars.filter(isValidBar) : []
        if (validBars.length > 0) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          series.setData(validBars as any)
          chart.timeScale().fitContent()
        }
      })
      .catch(() => {})

    // Forming bar poll — updates the live rightmost candle every 1s.
    const fetchFormingBar = () => {
      const sym = activeSymbolRef.current
      fetch(`/api/forming-bar${sym ? `?instrument=${sym}` : ''}`)
        .then(r => r.json())
        .then((b: { time: number; open: number; high: number; low: number; close: number } | null) => {
          if (!b || !isValidBar(b)) return
          // Forming bar only makes sense at the bot's trading TF.
          if (viewTfRef.current !== timeframeRef.current) return
          // Only show if forming bar is newer than (or same as) the last closed bar.
          if (lastBarTimeRef.current !== null && b.time < lastBarTimeRef.current) return
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          series.update({ time: b.time as any, open: b.open, high: b.high, low: b.low, close: b.close })
        })
        .catch(() => {})
    }
    fetchFormingBar()
    const formingBarId = setInterval(fetchFormingBar, 1000)

    callbacksRef.current = {
      onBar(bar) {
        // Only update chart when viewing the bot's trading TF.
        if (viewTfRef.current !== timeframeRef.current) return
        lastBarTimeRef.current = bar.time
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        series.update({ time: bar.time as any, open: bar.open, high: bar.high, low: bar.low, close: bar.close })
      },
      onFillMarker(time, isEntry, side, pnl) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const existing: any[] = [...markersPlugin.markers()]
        if (isEntry) {
          existing.push({
            time,
            position: side === 'long' ? 'belowBar' : 'aboveBar',
            shape:    side === 'long' ? 'arrowUp'  : 'arrowDown',
            color:    side === 'long' ? '#3ee0a5'  : '#f87171',
            text:     side === 'long' ? 'BUY'      : 'SELL',
            size: 1,
          })
        } else {
          const win = pnl >= 0
          existing.push({
            time,
            position: side === 'long' ? 'aboveBar' : 'belowBar',
            shape:    'circle',
            color:    win ? '#3ee0a5' : '#f87171',
            text:     (win ? '+' : '') + '$' + Math.abs(pnl).toFixed(0),
            size: 1,
          })
        }
        existing.sort((a, b) => (a.time as number) - (b.time as number))
        markersPlugin.setMarkers(existing)
      },
      onReset() {
        series.setData([])
        markersPlugin.setMarkers([])
      },
    }

    return () => {
      clearInterval(formingBarId)
      callbacksRef.current = {}
      seriesRef.current = null
      chartRef.current = null
      chart.remove()
    }
  }, [callbacksRef])

  // Re-populate the chart whenever the viewed timeframe or active symbol changes.
  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return
    const instrParam = activeSymbol ? `&instrument=${activeSymbol}` : ''
    fetch(`/api/bars?timeframe=${viewTf}&limit=500${instrParam}`)
      .then(r => r.json())
      .then(d => {
        if (!seriesRef.current || !chartRef.current) return
        const validBars = Array.isArray(d.bars) ? d.bars.filter(isValidBar) : []
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        seriesRef.current.setData(validBars.length > 0 ? validBars as any : [])
        // Reset forming-bar anchor so off-TF/off-symbol bars don't show stale data.
        lastBarTimeRef.current = null
        if (validBars.length > 0) chartRef.current.timeScale().fitContent()
      })
      .catch(() => {})
  }, [viewTf, activeSymbol])

  const instLabel = activeSymbol ? (INSTRUMENT_NAMES[activeSymbol] ?? activeSymbol) : ''
  return (
    <div className="flex-1 min-h-0 bg-panel-hi backdrop-blur-md border border-border rounded-[10px] overflow-hidden flex flex-col animate-fade-up">
      {/* header bar */}
      <div className="flex items-center justify-between px-[18px] py-2.5 border-b border-border shrink-0">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium text-ink">{activeSymbol ?? '—'}</span>
          <span className="text-[10px] text-faint font-mono">{instLabel}{instLabel && ' · '}{TF_LABELS[viewTf] ?? viewTf}</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-0.5">
            {Object.keys(TF_SECONDS).map(tf => (
              <button
                key={tf}
                onClick={() => setViewTf(tf)}
                className={`text-[10px] font-mono px-1.5 py-0.5 rounded transition-colors ${viewTf === tf ? 'text-accent-ink bg-accent/15' : 'text-faint hover:text-dim'}`}
              >
                {TF_LABELS[tf]}{tf === timeframe ? '·' : ''}
              </button>
            ))}
          </div>
          {countdown !== null && (
            <span className="text-[10px] font-mono tabular-nums text-faint">
              next{' '}
              <span className={countdown === 'now' ? 'text-accent-ink animate-pulse-soft' : 'text-dim'}>{countdown}</span>
            </span>
          )}
        </div>
      </div>
      {/* chart canvas */}
      <div className="flex-1 min-h-0 relative">
        <div ref={containerRef} className="absolute inset-0" />
      </div>
    </div>
  )
}
