import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts'
import type { ChartCallbacks } from '../hooks/useStream'

const TF_SECONDS: Record<string, number> = {
  '1min': 60, '3min': 180, '5min': 300,
  '15min': 900, '30min': 1800, '1h': 3600,
}

interface Props {
  callbacksRef: React.MutableRefObject<ChartCallbacks>
  timeframe?: string
}

export function BarChart({ callbacksRef, timeframe }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const lastBarTimeRef = useRef<number | null>(null)
  const [countdown, setCountdown] = useState<string | null>(null)

  // Countdown ticker — time until the next bar boundary (next minute, next 5min, etc.)
  // Wall-clock based, so it's accurate even when REST bar delivery lags.
  useEffect(() => {
    const tfSecs = TF_SECONDS[timeframe ?? ''] ?? null
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
  }, [timeframe])

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
        background: { color: '#000000' },
        textColor:  '#00aa22',
        fontSize:   11,
      },
      grid: {
        vertLines: { color: '#001200' },
        horzLines: { color: '#001200' },
      },
      crosshair: {
        vertLine: { color: '#00aa22', labelBackgroundColor: '#040604' },
        horzLine: { color: '#00aa22', labelBackgroundColor: '#040604' },
      },
      rightPriceScale: { borderColor: '#003a00' },
      localization: {
        // Crosshair tooltip and time-axis label use PT 12-hour.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        timeFormatter: ((time: any) => fmtChartDateTime(Number(time))) as any,
      },
      timeScale: {
        borderColor:    '#003a00',
        timeVisible:    true,
        secondsVisible: false,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        tickMarkFormatter: ((time: any) => fmtChartTime(Number(time))) as any,
      },
    })

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const series = chart.addSeries(CandlestickSeries as any, {
      upColor:         '#00ff41',
      downColor:       '#ff3333',
      borderUpColor:   '#00ff41',
      borderDownColor: '#ff3333',
      wickUpColor:     '#00ff41',
      wickDownColor:   '#ff3333',
    })

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const markersPlugin = createSeriesMarkers(series as any, [])

    // Pre-populate the chart with historical bars so it's not empty on connect.
    fetch('/api/bars?limit=500')
      .then(r => r.json())
      .then(d => {
        if (Array.isArray(d.bars) && d.bars.length > 0) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          series.setData(d.bars as any)
          chart.timeScale().fitContent()
        }
      })
      .catch(() => {})

    callbacksRef.current = {
      onBar(bar) {
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
            color:    side === 'long' ? '#00ff41'  : '#ff3333',
            text:     side === 'long' ? 'BUY'      : 'SELL',
            size: 1,
          })
        } else {
          const win = pnl >= 0
          existing.push({
            time,
            position: side === 'long' ? 'aboveBar' : 'belowBar',
            shape:    'circle',
            color:    win ? '#00ff41' : '#ff3333',
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
      callbacksRef.current = {}
      chart.remove()
    }
  }, [callbacksRef])

  return (
    <div className="bg-panel border border-border">
      <div className="px-4 py-2 border-b border-border flex items-center justify-between">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Price Chart</span>
        {countdown !== null && (
          <span className="text-[10px] font-mono tabular-nums text-dim">
            next bar{' '}
            <span className={countdown === 'now' ? 'text-accent animate-pulse-soft' : 'text-ink'}>
              {countdown}
            </span>
          </span>
        )}
      </div>
      <div ref={containerRef} />
    </div>
  )
}
