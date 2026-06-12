import { useCallback, useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts'

interface TradeChartTrade {
  entry_ts: string
  exit_ts: string
  side: string
  entry_price: string
  exit_price: string
  size: number
  realized_pnl: string
  hold_seconds?: number
  grade?: string
}

interface ChartPayload {
  trade: TradeChartTrade
  bars: { time: number; open: number; high: number; low: number; close: number }[]
  index: number
  total: number
  timeframe: string
  tf_secs: number
}

interface Props {
  runId: string
  tradeIndex: number
  onNavigate: (i: number) => void
  onClose: () => void
}

function fmtHold(secs?: number): string {
  if (!secs && secs !== 0) return '-'
  const m = Math.round(secs / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}

/** Trade-replay chart: one backtest trade rendered in bar context with
 *  entry/exit markers and price lines (TradeZella-style review view). */
export function TradeChart({ runId, tradeIndex, onNavigate, onClose }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [payload, setPayload] = useState<ChartPayload | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setError(null)
    fetch(`/api/backtest/${runId}/trade-chart?i=${tradeIndex}&pad=60`)
      .then(r => r.json())
      .then(d => {
        if (cancelled) return
        if (d.error) setError(d.error)
        else setPayload(d)
      })
      .catch(e => { if (!cancelled) setError(String(e)) })
    return () => { cancelled = true }
  }, [runId, tradeIndex])

  // Keyboard navigation: left/right step trades, Esc closes.
  const total = payload?.total ?? 0
  const onKey = useCallback((e: KeyboardEvent) => {
    if (e.key === 'ArrowLeft' && tradeIndex > 0) onNavigate(tradeIndex - 1)
    else if (e.key === 'ArrowRight' && tradeIndex < total - 1) onNavigate(tradeIndex + 1)
    else if (e.key === 'Escape') onClose()
  }, [tradeIndex, total, onNavigate, onClose])
  useEffect(() => {
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onKey])

  useEffect(() => {
    const el = containerRef.current
    if (!el || !payload || payload.bars.length === 0) return

    const PT = 'America/Los_Angeles'
    const fmtTime = (timeSecs: number) =>
      new Date(timeSecs * 1000).toLocaleTimeString('en-US', {
        timeZone: PT, hour: 'numeric', minute: '2-digit', hour12: true,
      })

    const chart = createChart(el, {
      autoSize: true,
      height: 360,
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
      crosshair: {
        vertLine: { color: 'rgba(37,99,235,0.4)', labelBackgroundColor: '#1e3a8a' },
        horzLine: { color: 'rgba(37,99,235,0.4)', labelBackgroundColor: '#1e3a8a' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.05)' },
      timeScale: {
        borderColor: 'rgba(255,255,255,0.05)',
        timeVisible: true,
        secondsVisible: false,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        tickMarkFormatter: ((t: any) => fmtTime(Number(t))) as any,
      },
    })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const series = chart.addSeries(CandlestickSeries as any, {
      upColor: '#3ee0a5', downColor: '#f87171',
      borderUpColor: '#3ee0a5', borderDownColor: '#f87171',
      wickUpColor: 'rgba(62,224,165,0.6)', wickDownColor: 'rgba(248,113,113,0.6)',
    })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    series.setData(payload.bars as any)

    const t = payload.trade
    const pnl = parseFloat(t.realized_pnl)
    const win = pnl >= 0
    const entryPrice = parseFloat(t.entry_price)
    const exitPrice = parseFloat(t.exit_price)
    const tfSecs = payload.tf_secs
    const entryBar = Math.floor(new Date(t.entry_ts).getTime() / 1000 / tfSecs) * tfSecs
    const exitBar = Math.floor(new Date(t.exit_ts).getTime() / 1000 / tfSecs) * tfSecs
    const isLong = t.side === 'long'

    const markersApi = createSeriesMarkers(series as never)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const markers: any[] = [
      {
        time: entryBar,
        position: isLong ? 'belowBar' : 'aboveBar',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        color: '#f59e0b',
        text: `${isLong ? 'LONG' : 'SHORT'} @ ${entryPrice}`,
        size: 1.5,
      },
      {
        time: exitBar,
        position: isLong ? 'aboveBar' : 'belowBar',
        shape: 'circle',
        color: win ? '#3ee0a5' : '#f87171',
        text: `${win ? '+' : '-'}$${Math.abs(pnl).toFixed(0)}`,
        size: 1.5,
      },
    ]
    markers.sort((a, b) => a.time - b.time)
    try { markersApi.setMarkers(markers) } catch { /* same-bar entry/exit */ }

    if (!isNaN(entryPrice))
      series.createPriceLine({ price: entryPrice, color: 'rgba(255,255,255,0.35)', lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: 'E' })
    if (!isNaN(exitPrice))
      series.createPriceLine({ price: exitPrice, color: win ? '#3ee0a5' : '#f87171', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'X' })

    chart.timeScale().fitContent()
    return () => { chart.remove() }
  }, [payload])

  const t = payload?.trade
  const pnl = t ? parseFloat(t.realized_pnl) : 0
  const win = pnl >= 0

  return (
    <div className="bg-panel-hi backdrop-blur-md border border-border rounded-[10px] overflow-hidden flex flex-col animate-fade-up">
      {/* trade-replay ribbon */}
      <div className="flex items-center justify-between px-[18px] py-2.5 border-b border-border shrink-0">
        <div className="flex items-baseline gap-3 font-mono text-[11px]">
          <span className="text-faint">TRADE REPLAY</span>
          {t && (
            <>
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-semibold ${t.side === 'long' ? 'text-accent-ink bg-accent/15' : 'text-danger bg-danger/15'}`}>
                {t.side.toUpperCase()} x{t.size}
              </span>
              <span className="text-dim tabular-nums">{t.entry_price} {'->'} {t.exit_price}</span>
              <span className={`tabular-nums font-semibold ${win ? 'text-accent-ink' : 'text-danger'}`}>
                {win ? '+' : '-'}${Math.abs(pnl).toFixed(2)}
              </span>
              <span className="text-faint">hold {fmtHold(t.hold_seconds)}</span>
              {t.grade && <span className="text-faint">grade {t.grade}</span>}
            </>
          )}
        </div>
        <div className="flex items-center gap-2 font-mono text-[11px]">
          <button
            onClick={() => onNavigate(tradeIndex - 1)}
            disabled={tradeIndex <= 0}
            className="px-1.5 py-0.5 rounded text-dim hover:text-ink disabled:opacity-30 transition-colors"
            title="previous trade (left arrow)"
          >&lt; prev</button>
          <span className="text-faint tabular-nums">{tradeIndex + 1}/{total || '?'}</span>
          <button
            onClick={() => onNavigate(tradeIndex + 1)}
            disabled={tradeIndex >= total - 1}
            className="px-1.5 py-0.5 rounded text-dim hover:text-ink disabled:opacity-30 transition-colors"
            title="next trade (right arrow)"
          >next &gt;</button>
          <button
            onClick={onClose}
            className="ml-2 px-1.5 py-0.5 rounded text-faint hover:text-danger transition-colors"
            title="close (Esc)"
          >x</button>
        </div>
      </div>
      {/* chart */}
      <div className="relative" style={{ height: 360 }}>
        {error
          ? <div className="absolute inset-0 flex items-center justify-center font-mono text-[11px] text-danger">{error}</div>
          : <div ref={containerRef} className="absolute inset-0" />}
      </div>
    </div>
  )
}
