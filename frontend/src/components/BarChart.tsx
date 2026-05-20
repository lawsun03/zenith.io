import { useEffect, useRef, useState, useCallback } from 'react'
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts'
import type { ChartCallbacks } from '../hooks/useStream'
import type { VpProfile } from '../types'

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
  const [formingHot, setFormingHot] = useState(false)  // displacement + pending sweep

  const pollFormingStatus = useCallback(() => {
    fetch('/api/forming/status')
      .then(r => r.json())
      .then((d: Record<string, { has_displacement_candidate: boolean; awaiting_sweeps: number }> | null) => {
        if (!d) { setFormingHot(false); return }
        const hot = Object.values(d).some(v => v.has_displacement_candidate && v.awaiting_sweeps > 0)
        setFormingHot(hot)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    pollFormingStatus()
    const id = setInterval(pollFormingStatus, 5000)
    return () => clearInterval(id)
  }, [pollFormingStatus])

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

    // VP histogram canvas overlay — draws volume-by-price on the right side.
    let currentVpProfile: VpProfile | null = null

    const vpCanvas = document.createElement('canvas')
    Object.assign(vpCanvas.style, { position: 'absolute', top: '0', left: '0', pointerEvents: 'none', zIndex: '10' })
    el.style.position = 'relative'
    el.appendChild(vpCanvas)

    const drawHistogram = () => {
      // Use the chart's own canvas for dimensions — el.offsetHeight is 0 when
      // lightweight-charts uses autoSize (absolutely-positioned canvas inside el).
      const chartCanvas = el.querySelector('canvas')
      vpCanvas.width  = chartCanvas ? chartCanvas.offsetWidth  : (el.offsetWidth  || 800)
      vpCanvas.height = chartCanvas ? chartCanvas.offsetHeight : (el.offsetHeight || 320)
      const ctx = vpCanvas.getContext('2d')
      if (!ctx) return
      ctx.clearRect(0, 0, vpCanvas.width, vpCanvas.height)

      const profile = currentVpProfile
      if (!profile || !profile.bins.length) return

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const s = series as any
      const poc = Number(profile.poc)
      const vah = Number(profile.vah)
      const val = Number(profile.val)
      const bins = profile.bins

      // Derive tick size from adjacent bin prices
      const tickSize = bins.length > 1 ? Math.abs(Number(bins[1][0]) - Number(bins[0][0])) : 0.1

      // Pixel height per price bin, derived from two priceToCoordinate calls
      const yAtPoc = s.priceToCoordinate(poc)
      const yAtPocPlusTick = s.priceToCoordinate(poc + tickSize)
      if (yAtPoc == null || yAtPocPlusTick == null) return
      const rowHeight = Math.max(1, Math.abs(yAtPocPlusTick - yAtPoc))

      const maxVol = Math.max(...bins.map(([, v]) => v))
      const maxHistWidth = 80   // max bar width from left edge
      const histLeft = 0        // anchor to left side of chart

      for (const [priceStr, volume] of bins) {
        const price = Number(priceStr)
        const yCenter = s.priceToCoordinate(price)
        if (yCenter == null) continue

        const barWidth = Math.max(1, (volume / maxVol) * maxHistWidth)

        if (Math.abs(price - poc) < tickSize * 0.5) {
          ctx.fillStyle = 'rgba(255,153,0,0.90)'   // POC — amber
        } else if (price >= val - tickSize * 0.1 && price <= vah + tickSize * 0.1) {
          ctx.fillStyle = 'rgba(0,180,220,0.35)'   // value area — cyan
        } else {
          ctx.fillStyle = 'rgba(0,110,0,0.50)'     // outside VA — dark green
        }

        ctx.fillRect(histLeft, yCenter - rowHeight / 2, barWidth, rowHeight)
      }

      // POC / VAH / VAL labels just right of the histogram
      ctx.font = 'bold 9px monospace'
      const labelX = maxHistWidth + 3
      const pocY = s.priceToCoordinate(poc)
      const vahY = s.priceToCoordinate(vah)
      const valY = s.priceToCoordinate(val)
      if (pocY != null) { ctx.fillStyle = 'rgba(255,153,0,1)';    ctx.fillText('POC', labelX, pocY + 3) }
      if (vahY != null) { ctx.fillStyle = 'rgba(0,204,255,0.9)';  ctx.fillText('VAH', labelX, vahY + 3) }
      if (valY != null) { ctx.fillStyle = 'rgba(0,204,255,0.9)';  ctx.fillText('VAL', labelX, valY + 3) }
    }

    const fetchAndDrawVp = () => {
      fetch('/api/vp/profile')
        .then(r => r.json())
        .then((p: VpProfile | null) => { currentVpProfile = p; drawHistogram() })
        .catch(() => {})
    }

    // Redraw when time axis changes; 200ms interval catches price-axis zoom.
    // 60s re-fetch handles the case where the engine wasn't ready at mount time.
    chart.timeScale().subscribeVisibleLogicalRangeChange(drawHistogram)
    const syncId = setInterval(drawHistogram, 200)
    const vpRefetchId = setInterval(fetchAndDrawVp, 60_000)

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

    fetchAndDrawVp()

    // Forming bar poll — updates the live rightmost candle every 1s.
    const fetchFormingBar = () => {
      fetch('/api/forming-bar')
        .then(r => r.json())
        .then((b: { time: number; open: number; high: number; low: number; close: number } | null) => {
          if (!b) return
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
      onVpUpdate() {
        fetchAndDrawVp()
      },
    }

    return () => {
      clearInterval(syncId)
      clearInterval(vpRefetchId)
      clearInterval(formingBarId)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(drawHistogram)
      if (el.contains(vpCanvas)) el.removeChild(vpCanvas)
      callbacksRef.current = {}
      chart.remove()
    }
  }, [callbacksRef])

  return (
    <div className="bg-panel border border-border">
      <div className="px-4 py-2 border-b border-border flex items-center justify-between">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Price Chart</span>
        <div className="flex items-center gap-3">
          {formingHot && (
            <span className="flex items-center gap-1 text-[10px] font-mono text-yellow-400 animate-pulse">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-400" />
              SETUP
            </span>
          )}
          {countdown !== null && (
            <span className="text-[10px] font-mono tabular-nums text-dim">
              next bar{' '}
              <span className={countdown === 'now' ? 'text-accent animate-pulse-soft' : 'text-ink'}>
                {countdown}
              </span>
            </span>
          )}
        </div>
      </div>
      <div ref={containerRef} />
    </div>
  )
}
