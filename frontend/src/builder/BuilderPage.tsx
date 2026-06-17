import { useEffect, useMemo, useRef, useState } from 'react'
import { generateBars, detectFVGs, detectOBs, type Timeframe } from './mockData'
import { computeIndicator, getDef, type Computed, type IndicatorDef } from './indicators'
import type { Drawing } from './drawings'
import { DEFAULT_RULE, makeCondition, evaluateRule, stopPriceFor, type Rule, type EvalCtx, type MenuTarget, type CondSource } from './rules'
import type { CondDef } from './indicators'
import { BuilderChart, type ChartMarker, type Tool, type PriceOverlay } from './BuilderChart'
import { GenericSubPanel } from './SubPanels'
import { RulePanel } from './RulePanel'
import { ContextMenu } from './ContextMenu'
import { IndicatorCatalog } from './IndicatorCatalog'

interface Trade { entry: number; exit: number; pnlR: number; win: boolean; side: 'long' | 'short' }
interface OpenTrade { entry: number; stop: number; target: number; side: 'long' | 'short' }

function simulate(ctx: EvalCtx, rule: Rule, cursor: number) {
  const bars = ctx.bars
  const trades: Trade[] = []; const markers: ChartMarker[] = []
  let open: OpenTrade | null = null
  for (let i = 1; i <= cursor && i < bars.length; i++) {
    const b = bars[i]
    if (open) {
      const long = open.side === 'long'
      const hitStop = long ? b.low <= open.stop : b.high >= open.stop
      const hitTgt = long ? b.high >= open.target : b.low <= open.target
      const exit = hitStop ? open.stop : hitTgt ? open.target : null
      if (exit != null) {
        const risk = Math.abs(open.entry - open.stop) || 1
        const pnlR = (long ? exit - open.entry : open.entry - exit) / risk
        trades.push({ entry: open.entry, exit, pnlR, win: pnlR >= 0, side: open.side })
        markers.push({ time: b.time, position: long ? 'aboveBar' : 'belowBar', shape: 'circle', color: pnlR >= 0 ? '#3ee0a5' : '#f87171', text: `${pnlR >= 0 ? '+' : ''}${pnlR.toFixed(1)}R` })
        open = null
      }
    } else if (evaluateRule(rule, ctx, i)) {
      const long = rule.side === 'long'; const entry = b.close
      const risk = Math.max(0.3, Math.abs(entry - stopPriceFor(rule, ctx, i, entry)))
      open = { entry, stop: long ? entry - risk : entry + risk, target: long ? entry + rule.targetR * risk : entry - rule.targetR * risk, side: rule.side }
      markers.push({ time: b.time, position: long ? 'belowBar' : 'aboveBar', shape: long ? 'arrowUp' : 'arrowDown', color: long ? '#3ee0a5' : '#f87171', text: long ? 'LONG' : 'SHORT' })
    }
  }
  return { trades, open, markers }
}

const WARMUP = 35
const TFS: Timeframe[] = ['1m', '5m', '15m', '1h']

export function BuilderPage() {
  const [tf, setTf] = useState<Timeframe>('5m')
  const bars = useMemo(() => generateBars(tf), [tf])
  const fvgs = useMemo(() => detectFVGs(bars), [bars])
  const obs = useMemo(() => detectOBs(bars), [bars])

  const [activeIds, setActiveIds] = useState<string[]>(['ema'])
  const [showFvg, setShowFvg] = useState(true)
  const [showOb, setShowOb] = useState(false)
  const [drawings, setDrawings] = useState<Drawing[]>([])
  const [rule, setRule] = useState<Rule>(DEFAULT_RULE)
  const [tool, setTool] = useState<Tool>('cursor')
  const [cursor, setCursor] = useState(WARMUP)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(220)
  const [menu, setMenu] = useState<{ target: MenuTarget; x: number; y: number } | null>(null)
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [flashId, setFlashId] = useState<string | null>(null)
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const seq = useRef(0)

  const computed = useMemo(() => {
    const m: Record<string, Computed> = {}
    activeIds.forEach(id => { const def = getDef(id); if (def) m[id] = computeIndicator(def, bars, 20) })
    return m
  }, [activeIds, bars])

  const drawingsMap = useMemo(() => Object.fromEntries(drawings.map(d => [d.id, d])), [drawings])
  const ctx: EvalCtx = useMemo(() => ({ bars, fvgs, obs, computed, drawings: drawingsMap }), [bars, fvgs, obs, computed, drawingsMap])
  const sim = useMemo(() => simulate(ctx, rule, cursor), [ctx, rule, cursor])
  const revealed = useMemo(() => bars.slice(0, cursor + 1), [bars, cursor])

  const priceOverlays: PriceOverlay[] = useMemo(() => {
    const lastT = bars[cursor]?.time ?? Infinity
    return activeIds.map(getDef).filter(d => d && d.pane === 'price').map(d => ({
      instId: d!.id, name: d!.name, ikind: d!.kind,
      lines: (computed[d!.id]?.priceLines ?? []).map(ln => ({ ...ln, data: ln.data.filter(p => p.time <= lastT) })),
    }))
  }, [activeIds, computed, bars, cursor])
  const subInsts = useMemo(() => activeIds.map(getDef).filter(d => d && d.pane === 'sub') as IndicatorDef[], [activeIds])

  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => setCursor(c => { if (c >= bars.length - 1) { setPlaying(false); return c } return c + 1 }), speed)
    return () => clearInterval(id)
  }, [playing, speed, bars.length])

  // timeframe change: regen invalidates time-anchored drawings
  useEffect(() => { setDrawings([]); setRule(r => ({ ...r, conditions: r.conditions.filter(c => c.source.type !== 'drawing') })); setCursor(WARMUP) }, [tf])

  const doFlash = (id: string) => { setFlashId(id); if (flashTimer.current) clearTimeout(flashTimer.current); flashTimer.current = setTimeout(() => setFlashId(null), 650) }

  const addCondition = (source: CondSource, def: CondDef, name: string) => {
    setRule(r => ({ ...r, conditions: [...r.conditions, makeCondition(source, def, name)] }))
    if (source.type === 'detector') { if (source.det === 'fvg') setShowFvg(true); else setShowOb(true); doFlash(source.det) }
    else if (source.type === 'indicator') doFlash(source.instId)
    else doFlash(source.drawingId)
    setMenu(null)
  }
  const addIndicator = (def: IndicatorDef) => { setActiveIds(ids => ids.includes(def.id) ? ids : [...ids, def.id]); doFlash(def.id) }
  const removeIndicator = (id: string) => { setActiveIds(ids => ids.filter(x => x !== id)); setRule(r => ({ ...r, conditions: r.conditions.filter(c => !(c.source.type === 'indicator' && c.source.instId === id)) })) }
  const addDrawing = (d: Omit<Drawing, 'id'>) => { const id = `dw${seq.current++}`; setDrawings(ds => [...ds, { ...d, id }]); setTool('cursor'); doFlash(id) }

  const wins = sim.trades.filter(t => t.win).length
  const totalR = sim.trades.reduce((s, t) => s + t.pnlR, 0)
  const winRate = sim.trades.length ? Math.round((wins / sim.trades.length) * 100) : 0
  const atEnd = cursor >= bars.length - 1

  const TOOL_BTN = (t: Tool, label: string) => (
    <button onClick={() => setTool(tool === t ? 'cursor' : t)}
      className={`flex-1 text-[10px] font-mono px-1.5 py-1.5 rounded-[6px] border transition-colors ${tool === t ? 'border-accent bg-accent/10 text-accent-ink' : 'border-border text-dim hover:text-ink'}`}>{label}</button>
  )

  return (
    <div className="h-screen w-screen bg-bg text-ink flex flex-col overflow-hidden font-sans">
      {/* header */}
      <div className="flex items-center justify-between px-5 py-2.5 border-b border-border shrink-0">
        <div className="flex items-baseline gap-3">
          <a href="/" className="text-[11px] text-faint font-mono hover:text-dim">← dashboard</a>
          <span className="text-sm font-medium text-ink">Strategy Builder</span>
          <span className="text-[9px] font-mono uppercase tracking-widest text-amber-400/80 border border-amber-400/30 rounded px-1.5 py-0.5">mock · nothing live</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-0.5">
            {TFS.map(t => (
              <button key={t} onClick={() => setTf(t)} className={`text-[10px] font-mono px-2 py-0.5 rounded transition-colors ${tf === t ? 'text-accent-ink bg-accent/15' : 'text-faint hover:text-dim'}`}>{t}</button>
            ))}
          </div>
          <span className="text-[10px] font-mono text-faint">synthetic MNQ · {bars.length} bars</span>
        </div>
      </div>

      {/* body */}
      <div className="flex-1 min-h-0 flex gap-4 p-4">
        {/* left rail */}
        <div className="w-[168px] shrink-0 flex flex-col gap-3 overflow-y-auto">
          <div>
            <button onClick={() => setCatalogOpen(true)} className="w-full text-[11px] font-mono px-2.5 py-2 rounded-[7px] border border-accent/50 bg-accent/10 text-accent-ink hover:bg-accent/20 transition-colors">＋ Indicators…</button>
          </div>
          <div className="flex flex-col gap-1">
            <div className="text-[9px] uppercase tracking-widest text-faint font-mono px-1">Active ({activeIds.length})</div>
            {activeIds.map(id => {
              const d = getDef(id)
              return (
                <div key={id} className="flex items-center justify-between px-2 py-1.5 rounded-[6px] border border-border bg-panel-hi">
                  <div className="min-w-0">
                    <div className="text-[10px] font-mono text-dim truncate">{d?.name}</div>
                    <div className="text-[8px] text-faint font-mono">{d?.pane === 'price' ? 'overlay' : 'pane'}</div>
                  </div>
                  <button onClick={() => removeIndicator(id)} className="text-faint hover:text-red-300 text-[12px] leading-none ml-1">×</button>
                </div>
              )
            })}
          </div>
          <div className="flex flex-col gap-1">
            <div className="text-[9px] uppercase tracking-widest text-faint font-mono px-1">Detectors</div>
            <button onClick={() => setShowFvg(v => !v)} className={`flex items-center justify-between px-2.5 py-1.5 rounded-[6px] border text-left ${showFvg ? 'border-accent/50 bg-accent/10 text-accent-ink' : 'border-border bg-panel-hi text-dim'}`}><span className="text-[10px] font-mono">FVG</span><span className="text-[11px]">{showFvg ? '−' : '+'}</span></button>
            <button onClick={() => setShowOb(v => !v)} className={`flex items-center justify-between px-2.5 py-1.5 rounded-[6px] border text-left ${showOb ? 'border-accent/50 bg-accent/10 text-accent-ink' : 'border-border bg-panel-hi text-dim'}`}><span className="text-[10px] font-mono">OB</span><span className="text-[11px]">{showOb ? '−' : '+'}</span></button>
          </div>
          <div className="flex flex-col gap-1">
            <div className="text-[9px] uppercase tracking-widest text-faint font-mono px-1">Draw</div>
            <div className="flex gap-1">{TOOL_BTN('rect', '▭ rect')}{TOOL_BTN('trend', '╱ line')}</div>
            <div className="flex gap-1">{TOOL_BTN('hline', '— level')}<button onClick={() => setDrawings([])} className="flex-1 text-[10px] font-mono px-1.5 py-1.5 rounded-[6px] border border-border text-faint hover:text-red-300">clear</button></div>
            <div className="text-[8px] text-faint/70 font-mono px-1 pt-1 leading-relaxed">draw a rectangle for an FVG, then right-click it → "price enters".</div>
          </div>
        </div>

        {/* center */}
        <div className="flex-1 min-w-0 flex flex-col gap-2.5">
          <div className="flex-1 min-h-0 bg-panel-hi border border-border rounded-[10px] overflow-hidden">
            <BuilderChart bars={revealed} overlays={priceOverlays} fvgs={fvgs} obs={obs} showFvg={showFvg} showOb={showOb}
              drawings={drawings} tool={tool} onAddDrawing={addDrawing}
              markers={sim.markers} lines={sim.open ? { entry: sim.open.entry, stop: sim.open.stop, target: sim.open.target } : {}}
              flashId={flashId} onRequestMenu={(t, x, y) => setMenu({ target: t, x, y })} />
          </div>
          {subInsts.map(d => computed[d.id]?.sub && (
            <GenericSubPanel key={d.id} instId={d.id} name={d.name} ikind={d.kind} sub={computed[d.id].sub!} cursor={cursor} flash={flashId === d.id} onRequestMenu={(t, x, y) => setMenu({ target: t, x, y })} />
          ))}

          {/* sim controls */}
          <div className="bg-panel-hi border border-border rounded-[10px] px-4 py-2.5 flex items-center gap-4 shrink-0">
            <button onClick={() => { if (atEnd) setCursor(WARMUP); setPlaying(p => !p) }} className="text-[11px] font-mono uppercase tracking-wider px-3 py-1.5 rounded-[6px] border border-accent/50 bg-accent/10 text-accent-ink hover:bg-accent/20 transition-colors w-[64px]">{playing ? '❚❚ pause' : atEnd ? '↻ replay' : '▶ play'}</button>
            <button onClick={() => { setPlaying(false); setCursor(c => Math.min(bars.length - 1, c + 1)) }} className="text-[11px] font-mono px-2.5 py-1.5 rounded-[6px] border border-border text-dim hover:text-ink transition-colors">step ▶</button>
            <button onClick={() => { setPlaying(false); setCursor(WARMUP) }} className="text-[11px] font-mono px-2.5 py-1.5 rounded-[6px] border border-border text-faint hover:text-dim transition-colors">reset</button>
            <div className="flex items-center gap-2"><span className="text-[9px] text-faint font-mono">speed</span><input type="range" min={40} max={500} step={20} value={520 - speed} onChange={e => setSpeed(520 - parseInt(e.target.value))} className="slider-accent w-24" /></div>
            <div className="flex-1 h-1 bg-bg rounded-full overflow-hidden"><div className="h-full bg-accent/50" style={{ width: `${(cursor / (bars.length - 1)) * 100}%` }} /></div>
            <span className="text-[10px] font-mono text-faint tabular-nums">bar {cursor + 1}/{bars.length}</span>
          </div>

          {/* trade log */}
          <div className="bg-panel-hi border border-border rounded-[10px] shrink-0 max-h-[140px] flex flex-col">
            <div className="flex items-center justify-between px-4 py-1.5 border-b border-border">
              <span className="text-[9px] uppercase tracking-widest text-faint font-mono">Mock trades</span>
              <div className="flex items-center gap-4 font-mono text-[10px]">
                <span className="text-dim">{sim.trades.length} trades</span><span className="text-dim">win {winRate}%</span>
                <span className={totalR >= 0 ? 'text-accent-ink' : 'text-red-300'}>{totalR >= 0 ? '+' : ''}{totalR.toFixed(1)}R</span>
                {sim.open && <span className="text-amber-400 animate-pulse-soft">● in trade</span>}
              </div>
            </div>
            <div className="overflow-y-auto px-4 py-1.5 flex flex-col-reverse gap-0.5">
              {sim.trades.length === 0 && !sim.open && <div className="text-[10px] text-faint font-mono italic py-3 text-center">build a rule, then press ▶ play — fired trades appear here</div>}
              {sim.trades.map((t, i) => (
                <div key={i} className="flex items-center justify-between text-[10px] font-mono"><span className="text-faint">#{i + 1} {t.side} @ {t.entry.toFixed(2)}</span><span className={t.win ? 'text-accent-ink' : 'text-red-300'}>{t.pnlR >= 0 ? '+' : ''}{t.pnlR.toFixed(1)}R</span></div>
              ))}
            </div>
          </div>
        </div>

        <RulePanel rule={rule} onChange={setRule} />
      </div>

      {catalogOpen && <IndicatorCatalog active={new Set(activeIds)} onAdd={addIndicator} onClose={() => setCatalogOpen(false)} />}
      {menu && <ContextMenu target={menu.target} x={menu.x} y={menu.y} onPick={addCondition} onClose={() => setMenu(null)} />}
    </div>
  )
}
