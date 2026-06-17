import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, LineSeries, createSeriesMarkers } from 'lightweight-charts'
import type { Candle, Zone } from './mockData'
import type { ElementKind } from './rules'

export interface ChartMarker {
  time: number
  position: 'aboveBar' | 'belowBar'
  shape: 'arrowUp' | 'arrowDown' | 'circle'
  color: string
  text: string
}
export interface ActiveLines { entry?: number; stop?: number; target?: number }

interface Props {
  bars: Candle[]            // revealed bars (up to the sim cursor)
  emaArr: (number | null)[] // full ema, aligned to all bars
  showEma: boolean
  emaPeriod: number
  fvgs: Zone[]
  obs: Zone[]
  showFvg: boolean
  showOb: boolean
  markers: ChartMarker[]
  lines: ActiveLines
  flash: ElementKind | null
  onRequestMenu: (element: ElementKind, x: number, y: number) => void
}

interface OverlayRect { key: string; left: number; top: number; w: number; h: number; side: 'bull' | 'bear'; kind: 'fvg' | 'ob' }

export function BuilderChart(props: Props) {
  const { bars, emaArr, showEma, fvgs, obs, showFvg, showOb, markers, lines, flash, onRequestMenu } = props
  const wrapRef = useRef<HTMLDivElement>(null)
  const elRef = useRef<HTMLDivElement>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const candleRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const emaRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markersApiRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const priceLinesRef = useRef<any[]>([])
  const [rects, setRects] = useState<OverlayRect[]>([])

  // --- create chart once -----------------------------------------------------
  useEffect(() => {
    const el = elRef.current
    if (!el) return
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#6c82a8', fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 },
      grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
      crosshair: { vertLine: { color: 'rgba(110,231,183,0.35)' }, horzLine: { color: 'rgba(110,231,183,0.35)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.05)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.05)', timeVisible: true, secondsVisible: false },
    })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const candle = chart.addSeries(CandlestickSeries as any, {
      upColor: '#3ee0a5', downColor: '#f87171', borderUpColor: '#3ee0a5',
      borderDownColor: '#f87171', wickUpColor: 'rgba(62,224,165,0.6)', wickDownColor: 'rgba(248,113,113,0.6)',
    })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const emaLine = chart.addSeries(LineSeries as any, { color: '#6ee7b7', lineWidth: 2, priceLineVisible: false, lastValueVisible: false })
    chartRef.current = chart
    candleRef.current = candle
    emaRef.current = emaLine
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    markersApiRef.current = createSeriesMarkers(candle as any)

    const redraw = () => drawOverlays()
    chart.timeScale().subscribeVisibleLogicalRangeChange(redraw)
    const ro = new ResizeObserver(redraw)
    ro.observe(el)

    // right-click hit-testing on the price pane
    const onCtx = (e: MouseEvent) => {
      e.preventDefault()
      const rect = el.getBoundingClientRect()
      const x = e.clientX - rect.left, y = e.clientY - rect.top
      const el2 = hitTest(x, y)
      if (el2) onRequestMenu(el2, e.clientX, e.clientY)
    }
    el.addEventListener('contextmenu', onCtx)

    return () => {
      el.removeEventListener('contextmenu', onCtx)
      ro.disconnect()
      chart.remove()
      chartRef.current = candleRef.current = emaRef.current = markersApiRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // hit-test cursor against EMA line / FVG / OB zones
  function hitTest(x: number, y: number): ElementKind | null {
    const candle = candleRef.current
    if (!candle) return null
    const price = candle.coordinateToPrice(y)
    if (price == null) return null
    // EMA: is cursor near the ema value at the rightmost revealed bar?
    if (showEma && emaRef.current) {
      const lastEma = [...emaArr].reverse().find(v => v != null) as number | undefined
      if (lastEma != null) {
        const yEma = candle.priceToCoordinate(lastEma)
        if (yEma != null && Math.abs(yEma - y) < 10) return 'ema'
      }
    }
    // zones: cursor inside a drawn box
    for (const r of rects) {
      if (x >= r.left && x <= r.left + r.w && y >= r.top && y <= r.top + r.h) {
        return r.kind === 'fvg' ? 'fvg' : 'ob'
      }
    }
    return null
  }

  function drawOverlays() {
    const chart = chartRef.current, candle = candleRef.current, el = elRef.current
    if (!chart || !candle || !el) return
    const w = el.clientWidth
    const out: OverlayRect[] = []
    const lastTime = bars.length ? bars[bars.length - 1].time : 0
    const push = (zs: Zone[], kind: 'fvg' | 'ob', on: boolean) => {
      if (!on) return
      zs.forEach((z, idx) => {
        if (z.startIdx >= bars.length) return
        const x0 = chart.timeScale().timeToCoordinate(bars[z.startIdx].time as never)
        const yTop = candle.priceToCoordinate(z.top)
        const yBot = candle.priceToCoordinate(z.bottom)
        if (x0 == null || yTop == null || yBot == null) return
        out.push({ key: `${kind}-${idx}-${z.startIdx}`, left: x0, top: yTop, w: Math.max(8, w - x0), h: Math.max(2, yBot - yTop), side: z.side, kind })
      })
    }
    // only zones up to the last revealed bar
    push(fvgs.filter(z => bars[z.startIdx] && bars[z.startIdx].time <= lastTime), 'fvg', showFvg)
    push(obs.filter(z => bars[z.startIdx] && bars[z.startIdx].time <= lastTime), 'ob', showOb)
    setRects(out)
  }

  // --- push data on prop change ---------------------------------------------
  useEffect(() => {
    const candle = candleRef.current, emaLine = emaRef.current, chart = chartRef.current
    if (!candle || !emaLine || !chart) return
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    candle.setData(bars.map(b => ({ time: b.time as any, open: b.open, high: b.high, low: b.low, close: b.close })))
    const emaData = bars
      .map((b, i) => ({ time: b.time, value: emaArr[i] }))
      .filter(d => showEma && d.value != null)
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .map(d => ({ time: d.time as any, value: d.value as number }))
    emaLine.setData(emaData)
    drawOverlays()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, emaArr, showEma, showFvg, showOb, fvgs, obs])

  // markers
  useEffect(() => {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    try { markersApiRef.current?.setMarkers(markers.slice().sort((a, b) => a.time - b.time) as any) } catch {}
  }, [markers])

  // price lines for the open trade
  useEffect(() => {
    const candle = candleRef.current
    if (!candle) return
    priceLinesRef.current.forEach(l => { try { candle.removePriceLine(l) } catch {} })
    priceLinesRef.current = []
    const add = (price: number | undefined, color: string, title: string, style: number) => {
      if (price == null) return
      priceLinesRef.current.push(candle.createPriceLine({ price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }))
    }
    add(lines.entry, 'rgba(255,255,255,0.4)', 'E', 1)
    add(lines.stop, '#f87171', 'SL', 2)
    add(lines.target, '#6ee7b7', 'TP', 2)
  }, [lines])

  return (
    <div ref={wrapRef} className="relative w-full h-full">
      <div ref={elRef} className="absolute inset-0" />
      {/* zone overlay layer */}
      <div className="absolute inset-0 pointer-events-none">
        {rects.map(r => (
          <div
            key={r.key}
            className="absolute"
            style={{
              left: r.left, top: r.top, width: r.w, height: r.h,
              background: r.kind === 'ob'
                ? 'rgba(245,158,11,0.10)'
                : r.side === 'bull' ? 'rgba(110,231,183,0.10)' : 'rgba(248,113,113,0.10)',
              borderTop: `1px solid ${r.kind === 'ob' ? 'rgba(245,158,11,0.4)' : r.side === 'bull' ? 'rgba(110,231,183,0.45)' : 'rgba(248,113,113,0.45)'}`,
              borderBottom: `1px solid ${r.kind === 'ob' ? 'rgba(245,158,11,0.4)' : r.side === 'bull' ? 'rgba(110,231,183,0.45)' : 'rgba(248,113,113,0.45)'}`,
              boxShadow: flash && ((flash === 'fvg' && r.kind === 'fvg') || (flash === 'ob' && r.kind === 'ob')) ? '0 0 0 2px #6ee7b7 inset' : undefined,
              transition: 'box-shadow 0.2s',
            }}
          />
        ))}
      </div>
      {/* right-click hint */}
      <div className="absolute bottom-2 left-3 text-[9px] text-faint font-mono pointer-events-none">
        right-click the EMA line or a zone → add as condition
      </div>
    </div>
  )
}
