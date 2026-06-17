import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, LineSeries, createSeriesMarkers } from 'lightweight-charts'
import type { Candle, Zone } from './mockData'
import type { LinePoint, IKind } from './indicators'
import type { Drawing } from './drawings'
import type { MenuTarget } from './rules'

export interface ChartMarker { time: number; position: 'aboveBar' | 'belowBar'; shape: 'arrowUp' | 'arrowDown' | 'circle'; color: string; text: string }
export interface ActiveLines { entry?: number; stop?: number; target?: number }
export interface PriceOverlay { instId: string; name: string; ikind: IKind; lines: { color: string; dash?: boolean; data: LinePoint[] }[] }
export type Tool = 'cursor' | 'rect' | 'trend' | 'hline'

interface Props {
  bars: Candle[]
  overlays: PriceOverlay[]
  fvgs: Zone[]; obs: Zone[]; showFvg: boolean; showOb: boolean
  drawings: Drawing[]
  tool: Tool
  onAddDrawing: (d: Omit<Drawing, 'id'>) => void
  markers: ChartMarker[]
  lines: ActiveLines
  flashId: string | null
  onRequestMenu: (target: MenuTarget, x: number, y: number) => void
}

interface RectGeo { key: string; left: number; top: number; w: number; h: number; color: string; fill: string; target: MenuTarget; flash: boolean }
interface LineGeo { key: string; x1: number; y1: number; x2: number; y2: number; color: string; target: MenuTarget; label?: string; flash: boolean }

export function BuilderChart(props: Props) {
  const { bars, overlays, fvgs, obs, showFvg, showOb, drawings, tool, onAddDrawing, markers, lines, flashId, onRequestMenu } = props
  const elRef = useRef<HTMLDivElement>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null); const candleRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const overlaySeriesRef = useRef<any[]>([]); const markersApiRef = useRef<any>(null); const priceLinesRef = useRef<any[]>([])
  const [rects, setRects] = useState<RectGeo[]>([])
  const [lineGeos, setLineGeos] = useState<LineGeo[]>([])
  const [draft, setDraft] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null)
  // live refs so the static chart-effect closures read current props
  const liveRef = useRef(props); liveRef.current = props

  // px <-> data
  const xToTime = (x: number): number | null => {
    const t = chartRef.current?.timeScale().coordinateToTime(x)
    if (t == null) { return liveRef.current.bars.length ? liveRef.current.bars[liveRef.current.bars.length - 1].time : null }
    // snap to nearest bar
    let best = liveRef.current.bars[0]?.time ?? null, bd = Infinity
    for (const b of liveRef.current.bars) { const d = Math.abs(b.time - (t as number)); if (d < bd) { bd = d; best = b.time } }
    return best
  }
  const yToPrice = (y: number): number | null => candleRef.current?.coordinateToPrice(y) ?? null

  // --- create chart once ---
  useEffect(() => {
    const el = elRef.current; if (!el) return
    const chart = createChart(el, {
      autoSize: true,
      layout: { background: { color: 'transparent' }, textColor: '#6c82a8', fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 },
      grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
      crosshair: { vertLine: { color: 'rgba(110,231,183,0.3)' }, horzLine: { color: 'rgba(110,231,183,0.3)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.05)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.05)', timeVisible: true, secondsVisible: false },
    })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const candle = chart.addSeries(CandlestickSeries as any, { upColor: '#3ee0a5', downColor: '#f87171', borderUpColor: '#3ee0a5', borderDownColor: '#f87171', wickUpColor: 'rgba(62,224,165,0.6)', wickDownColor: 'rgba(248,113,113,0.6)' })
    chartRef.current = chart; candleRef.current = candle
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    markersApiRef.current = createSeriesMarkers(candle as any)
    const redraw = () => drawGeo()
    chart.timeScale().subscribeVisibleLogicalRangeChange(redraw)
    const ro = new ResizeObserver(redraw); ro.observe(el)

    const onCtx = (e: MouseEvent) => {
      e.preventDefault()
      const r = el.getBoundingClientRect(); const x = e.clientX - r.left, y = e.clientY - r.top
      const hit = hitTest(x, y)
      if (hit) onRequestMenu(hit, e.clientX, e.clientY)
    }
    el.addEventListener('contextmenu', onCtx)
    return () => { el.removeEventListener('contextmenu', onCtx); ro.disconnect(); chart.remove(); chartRef.current = candleRef.current = markersApiRef.current = null; overlaySeriesRef.current = [] }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function hitTest(x: number, y: number): MenuTarget | null {
    for (const r of rects) if (x >= r.left && x <= r.left + r.w && y >= r.top && y <= r.top + r.h) return r.target
    for (const l of lineGeos) {
      const d = distToSeg(x, y, l.x1, l.y1, l.x2, l.y2)
      if (d < 7) return l.target
    }
    return null
  }

  function drawGeo() {
    const chart = chartRef.current, candle = candleRef.current, el = elRef.current
    if (!chart || !candle || !el) return
    const { bars, fvgs, obs, showFvg, showOb, drawings, flashId } = liveRef.current
    const w = el.clientWidth
    const lastTime = bars.length ? bars[bars.length - 1].time : 0
    const tx = (t: number) => chart.timeScale().timeToCoordinate(t as never)
    const py = (p: number) => candle.priceToCoordinate(p)

    const rg: RectGeo[] = []
    const zoneRect = (zs: Zone[], on: boolean, det: 'fvg' | 'ob') => {
      if (!on) return
      zs.forEach((z, idx) => {
        if (!bars[z.startIdx] || bars[z.startIdx].time > lastTime) return
        const x0 = tx(bars[z.startIdx].time), yt = py(z.top), yb = py(z.bottom)
        if (x0 == null || yt == null || yb == null) return
        const c = det === 'ob' ? '245,158,11' : z.side === 'bull' ? '110,231,183' : '248,113,113'
        rg.push({ key: `${det}-${idx}`, left: x0, top: yt, w: Math.max(8, w - x0), h: Math.max(2, yb - yt), color: `rgba(${c},0.45)`, fill: `rgba(${c},0.10)`, target: { kind: 'detector', det, name: det.toUpperCase() }, flash: false })
      })
    }
    zoneRect(fvgs.filter(z => bars[z.startIdx] && bars[z.startIdx].time <= lastTime), showFvg, 'fvg')
    zoneRect(obs.filter(z => bars[z.startIdx] && bars[z.startIdx].time <= lastTime), showOb, 'ob')

    const lg: LineGeo[] = []
    drawings.forEach(d => {
      const flash = flashId === d.id
      if (d.type === 'rect') {
        const x1 = tx(Math.min(d.t1, d.t2 ?? d.t1)), x2 = tx(Math.max(d.t1, d.t2 ?? d.t1))
        const yt = py(Math.max(d.p1, d.p2 ?? d.p1)), yb = py(Math.min(d.p1, d.p2 ?? d.p1))
        if (x1 == null || x2 == null || yt == null || yb == null) return
        rg.push({ key: d.id, left: x1, top: yt, w: Math.max(6, x2 - x1), h: Math.max(4, yb - yt), color: 'rgba(124,162,247,0.6)', fill: 'rgba(124,162,247,0.12)', target: { kind: 'drawing', drawingId: d.id, dtype: 'rect', name: 'Rectangle' }, flash })
      } else if (d.type === 'hline') {
        const yy = py(d.p1); if (yy == null) return
        lg.push({ key: d.id, x1: 0, y1: yy, x2: w, y2: yy, color: '#e879f9', target: { kind: 'drawing', drawingId: d.id, dtype: 'hline', name: 'Horizontal Line' }, label: d.p1.toFixed(2), flash })
      } else {
        const lx1 = tx(d.t1), lx2 = tx(d.t2 ?? d.t1), ly1 = py(d.p1), ly2 = py(d.p2 ?? d.p1)
        if (lx1 == null || lx2 == null || ly1 == null || ly2 == null) return
        lg.push({ key: d.id, x1: lx1, y1: ly1, x2: lx2, y2: ly2, color: '#7aa2f7', target: { kind: 'drawing', drawingId: d.id, dtype: 'trend', name: 'Trendline' }, flash })
      }
    })
    setRects(rg); setLineGeos(lg)
  }

  // overlays (price-pane indicator lines) — recreate on change
  useEffect(() => {
    const chart = chartRef.current; if (!chart) return
    overlaySeriesRef.current.forEach(s => { try { chart.removeSeries(s) } catch {} })
    overlaySeriesRef.current = []
    overlays.forEach(ov => ov.lines.forEach(ln => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const s = chart.addSeries(LineSeries as any, { color: ln.color, lineWidth: ln.dash ? 1 : 2, lineStyle: ln.dash ? 2 : 0, priceLineVisible: false, lastValueVisible: false })
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      s.setData(ln.data.map(d => ({ time: d.time as any, value: d.value })))
      overlaySeriesRef.current.push(s)
    }))
  }, [overlays])

  // candle data + redraw geometry
  useEffect(() => {
    const candle = candleRef.current; if (!candle) return
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    candle.setData(bars.map(b => ({ time: b.time as any, open: b.open, high: b.high, low: b.low, close: b.close })))
    drawGeo()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bars, fvgs, obs, showFvg, showOb, drawings, flashId])

  useEffect(() => { try { markersApiRef.current?.setMarkers(markers.slice().sort((a, b) => a.time - b.time)) } catch {} }, [markers])

  useEffect(() => {
    const candle = candleRef.current; if (!candle) return
    priceLinesRef.current.forEach(l => { try { candle.removePriceLine(l) } catch {} }); priceLinesRef.current = []
    const add = (p: number | undefined, color: string, title: string, style: number) => { if (p != null) priceLinesRef.current.push(candle.createPriceLine({ price: p, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title })) }
    add(lines.entry, 'rgba(255,255,255,0.4)', 'E', 1); add(lines.stop, '#f87171', 'SL', 2); add(lines.target, '#6ee7b7', 'TP', 2)
  }, [lines])

  // drawing gestures
  const drawing = tool !== 'cursor'
  const onDown = (e: React.PointerEvent) => {
    if (!drawing) return
    const el = elRef.current!; const r = el.getBoundingClientRect()
    const x = e.clientX - r.left, y = e.clientY - r.top
    if (tool === 'hline') { const p = yToPrice(y); const t = xToTime(x); if (p != null && t != null) onAddDrawing({ type: 'hline', t1: t, p1: p }); return }
    setDraft({ x1: x, y1: y, x2: x, y2: y })
  }
  const onMove = (e: React.PointerEvent) => {
    if (!draft) return
    const r = elRef.current!.getBoundingClientRect()
    setDraft({ ...draft, x2: e.clientX - r.left, y2: e.clientY - r.top })
  }
  const onUp = () => {
    if (!draft) return
    const t1 = xToTime(draft.x1), p1 = yToPrice(draft.y1), t2 = xToTime(draft.x2), p2 = yToPrice(draft.y2)
    if (t1 != null && p1 != null && t2 != null && p2 != null) onAddDrawing({ type: tool === 'trend' ? 'trend' : 'rect', t1, p1, t2, p2 })
    setDraft(null)
  }

  return (
    <div className="relative w-full h-full">
      <div ref={elRef} className="absolute inset-0" />
      {/* rect overlays */}
      <div className="absolute inset-0 pointer-events-none">
        {rects.map(r => (
          <div key={r.key} className="absolute" style={{ left: r.left, top: r.top, width: r.w, height: r.h, background: r.fill, borderTop: `1px solid ${r.color}`, borderBottom: `1px solid ${r.color}`, boxShadow: r.flash ? '0 0 0 2px #6ee7b7 inset' : undefined }} />
        ))}
      </div>
      {/* line overlays (trend / hline) */}
      <svg className="absolute inset-0 w-full h-full pointer-events-none">
        {lineGeos.map(l => (
          <g key={l.key}>
            <line x1={l.x1} y1={l.y1} x2={l.x2} y2={l.y2} stroke={l.color} strokeWidth={l.flash ? 3 : 1.5} strokeDasharray={l.label ? '4 3' : undefined} />
            {l.label && <text x={l.x2 - 4} y={l.y1 - 4} fontSize="9" fill={l.color} textAnchor="end" fontFamily="monospace">{l.label}</text>}
          </g>
        ))}
        {draft && tool === 'rect' && <rect x={Math.min(draft.x1, draft.x2)} y={Math.min(draft.y1, draft.y2)} width={Math.abs(draft.x2 - draft.x1)} height={Math.abs(draft.y2 - draft.y1)} fill="rgba(124,162,247,0.15)" stroke="#7aa2f7" />}
        {draft && tool === 'trend' && <line x1={draft.x1} y1={draft.y1} x2={draft.x2} y2={draft.y2} stroke="#7aa2f7" strokeWidth="1.5" />}
      </svg>
      {/* legend — right-clickable indicator chips */}
      <div className="absolute top-2 left-2 flex flex-col gap-1 z-10">
        {overlays.map(ov => (
          <button key={ov.instId}
            onContextMenu={e => { e.preventDefault(); onRequestMenu({ kind: 'indicator', instId: ov.instId, ikind: ov.ikind, name: ov.name }, e.clientX, e.clientY) }}
            className={`flex items-center gap-1.5 px-1.5 py-0.5 rounded bg-bg/70 border ${flashId === ov.instId ? 'border-accent' : 'border-transparent hover:border-border'}`}>
            <span className="w-2.5 h-[2px]" style={{ background: ov.lines[0]?.color }} />
            <span className="text-[9px] font-mono text-dim">{ov.name}</span>
          </button>
        ))}
      </div>
      {/* draw-capture layer: sits above the chart and only intercepts pointer
          events while a drawing tool is active, so it doesn't fight lightweight-
          charts' own pan/crosshair handling in cursor mode. */}
      <div className="absolute inset-0" style={{ zIndex: 20, pointerEvents: drawing ? 'auto' : 'none', cursor: drawing ? 'crosshair' : 'default' }}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} />
      <div className="absolute bottom-2 left-3 text-[9px] text-faint font-mono pointer-events-none" style={{ zIndex: 21 }}>
        {drawing ? `drawing ${tool} — drag on the chart` : 'right-click a zone, drawing, or legend chip → add condition'}
      </div>
    </div>
  )
}

function distToSeg(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1, dy = y2 - y1
  const len2 = dx * dx + dy * dy || 1
  let t = ((px - x1) * dx + (py - y1) * dy) / len2
  t = Math.max(0, Math.min(1, t))
  const cx = x1 + t * dx, cy = y1 + t * dy
  return Math.hypot(px - cx, py - cy)
}
