import { useEffect, useMemo, useRef, useState } from 'react'
import { generateBars, ema, rsi, macd, detectFVGs, detectOBs } from './mockData'
import { DEFAULT_RULE, makeCondition, evaluateRule, stopPriceFor, type Rule, type EvalCtx, type ElementKind } from './rules'
import type { ConditionDef } from './rules'
import { BuilderChart, type ChartMarker } from './BuilderChart'
import { RsiPanel, MacdPanel } from './SubPanels'
import { IndicatorPalette, type Indicators } from './IndicatorPalette'
import { RulePanel } from './RulePanel'
import { ContextMenu } from './ContextMenu'

interface Trade { entryIdx: number; exitIdx: number; entry: number; exit: number; pnlR: number; win: boolean; side: 'long' | 'short' }
interface OpenTrade { entryIdx: number; entry: number; stop: number; target: number; side: 'long' | 'short' }

function simulate(ctx: EvalCtx, rule: Rule, cursor: number) {
  const bars = ctx.bars
  const trades: Trade[] = []
  const markers: ChartMarker[] = []
  let open: OpenTrade | null = null

  for (let i = 1; i <= cursor && i < bars.length; i++) {
    const b = bars[i]
    if (open) {
      const long = open.side === 'long'
      const hitStop = long ? b.low <= open.stop : b.high >= open.stop
      const hitTgt = long ? b.high >= open.target : b.low <= open.target
      let exit: number | null = null
      if (hitStop) exit = open.stop          // pessimistic: stop first
      else if (hitTgt) exit = open.target
      if (exit != null) {
        const risk = Math.abs(open.entry - open.stop) || 1
        const pnlR = ((long ? exit - open.entry : open.entry - exit) / risk)
        const win = pnlR >= 0
        trades.push({ entryIdx: open.entryIdx, exitIdx: i, entry: open.entry, exit, pnlR, win, side: open.side })
        markers.push({ time: b.time, position: long ? 'aboveBar' : 'belowBar', shape: 'circle', color: win ? '#3ee0a5' : '#f87171', text: `${pnlR >= 0 ? '+' : ''}${pnlR.toFixed(1)}R` })
        open = null
      }
    } else if (evaluateRule(rule, ctx, i)) {
      const long = rule.side === 'long'
      const entry = b.close
      const stopLong = stopPriceFor(rule, ctx, i, entry)
      const risk = Math.max(0.3, Math.abs(entry - stopLong))
      const stop = long ? entry - risk : entry + risk
      const target = long ? entry + rule.targetR * risk : entry - rule.targetR * risk
      open = { entryIdx: i, entry, stop, target, side: rule.side }
      markers.push({ time: b.time, position: long ? 'belowBar' : 'aboveBar', shape: long ? 'arrowUp' : 'arrowDown', color: long ? '#3ee0a5' : '#f87171', text: long ? 'LONG' : 'SHORT' })
    }
  }
  return { trades, open, markers }
}

const WARMUP = 35

export function BuilderPage() {
  const bars = useMemo(() => generateBars(), [])
  const [emaPeriod, setEmaPeriod] = useState(20)
  const emaArr = useMemo(() => ema(bars, emaPeriod), [bars, emaPeriod])
  const rsiArr = useMemo(() => rsi(bars), [bars])
  const macdArr = useMemo(() => macd(bars), [bars])
  const fvgs = useMemo(() => detectFVGs(bars), [bars])
  const obs = useMemo(() => detectOBs(bars), [bars])

  const [indicators, setIndicators] = useState<Indicators>({ ema: true, rsi: false, macd: false, fvg: true, ob: false })
  const [rule, setRule] = useState<Rule>(DEFAULT_RULE)
  const [cursor, setCursor] = useState(WARMUP)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(220) // ms per bar
  const [menu, setMenu] = useState<{ element: ElementKind; x: number; y: number } | null>(null)
  const [flash, setFlash] = useState<ElementKind | null>(null)
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  const ctx: EvalCtx = useMemo(() => ({ bars, emaArr, rsiArr, macdArr, fvgs, obs }), [bars, emaArr, rsiArr, macdArr, fvgs, obs])
  const sim = useMemo(() => simulate(ctx, rule, cursor), [ctx, rule, cursor])

  // play loop
  useEffect(() => {
    if (!playing) return
    const id = setInterval(() => {
      setCursor(c => {
        if (c >= bars.length - 1) { setPlaying(false); return c }
        return c + 1
      })
    }, speed)
    return () => clearInterval(id)
  }, [playing, speed, bars.length])

  const revealed = useMemo(() => bars.slice(0, cursor + 1), [bars, cursor])

  const addCondition = (element: ElementKind, def: ConditionDef) => {
    setRule(r => ({ ...r, conditions: [...r.conditions, makeCondition(element, def)] }))
    // ensure the referenced indicator is visible
    setIndicators(ind => ({ ...ind, [element === 'fvg' ? 'fvg' : element === 'ob' ? 'ob' : element]: true }))
    setMenu(null)
    setFlash(element)
    if (flashTimer.current) clearTimeout(flashTimer.current)
    flashTimer.current = setTimeout(() => setFlash(null), 650)
  }

  const requestMenu = (element: ElementKind, x: number, y: number) => setMenu({ element, x, y })

  const wins = sim.trades.filter(t => t.win).length
  const totalR = sim.trades.reduce((s, t) => s + t.pnlR, 0)
  const winRate = sim.trades.length ? Math.round((wins / sim.trades.length) * 100) : 0
  const atEnd = cursor >= bars.length - 1

  return (
    <div className="h-screen w-screen bg-bg text-ink flex flex-col overflow-hidden font-sans">
      {/* header */}
      <div className="flex items-center justify-between px-5 py-2.5 border-b border-border shrink-0">
        <div className="flex items-baseline gap-3">
          <a href="/" className="text-[11px] text-faint font-mono hover:text-dim">← dashboard</a>
          <span className="text-sm font-medium text-ink">Strategy Builder</span>
          <span className="text-[9px] font-mono uppercase tracking-widest text-amber-400/80 border border-amber-400/30 rounded px-1.5 py-0.5">mock · nothing live</span>
        </div>
        <span className="text-[10px] font-mono text-faint">synthetic MNQ · 5m · {bars.length} bars</span>
      </div>

      {/* body */}
      <div className="flex-1 min-h-0 flex gap-4 p-4">
        <IndicatorPalette indicators={indicators} emaPeriod={emaPeriod} onToggle={k => setIndicators(i => ({ ...i, [k]: !i[k] }))} onEmaPeriod={setEmaPeriod} />

        {/* center */}
        <div className="flex-1 min-w-0 flex flex-col gap-2.5">
          <div className="flex-1 min-h-0 bg-panel-hi border border-border rounded-[10px] overflow-hidden">
            <BuilderChart
              bars={revealed} emaArr={emaArr} showEma={indicators.ema} emaPeriod={emaPeriod}
              fvgs={fvgs} obs={obs} showFvg={indicators.fvg} showOb={indicators.ob}
              markers={sim.markers} lines={sim.open ? { entry: sim.open.entry, stop: sim.open.stop, target: sim.open.target } : {}}
              flash={flash} onRequestMenu={requestMenu}
            />
          </div>
          {indicators.rsi && <RsiPanel values={rsiArr.slice(0, cursor + 1)} onRequestMenu={requestMenu} flash={flash} />}
          {indicators.macd && <MacdPanel values={macdArr.slice(0, cursor + 1)} onRequestMenu={requestMenu} flash={flash} />}

          {/* simulator controls */}
          <div className="bg-panel-hi border border-border rounded-[10px] px-4 py-2.5 flex items-center gap-4 shrink-0">
            <button onClick={() => { if (atEnd) setCursor(WARMUP); setPlaying(p => !p) }}
              className="text-[11px] font-mono uppercase tracking-wider px-3 py-1.5 rounded-[6px] border border-accent/50 bg-accent/10 text-accent-ink hover:bg-accent/20 transition-colors w-[64px]">
              {playing ? '❚❚ pause' : atEnd ? '↻ replay' : '▶ play'}
            </button>
            <button onClick={() => { setPlaying(false); setCursor(c => Math.min(bars.length - 1, c + 1)) }}
              className="text-[11px] font-mono px-2.5 py-1.5 rounded-[6px] border border-border text-dim hover:text-ink transition-colors">step ▶</button>
            <button onClick={() => { setPlaying(false); setCursor(WARMUP) }}
              className="text-[11px] font-mono px-2.5 py-1.5 rounded-[6px] border border-border text-faint hover:text-dim transition-colors">reset</button>
            <div className="flex items-center gap-2">
              <span className="text-[9px] text-faint font-mono">speed</span>
              <input type="range" min={40} max={500} step={20} value={520 - speed} onChange={e => setSpeed(520 - parseInt(e.target.value))} className="slider-accent w-24" />
            </div>
            <div className="flex-1 h-1 bg-bg rounded-full overflow-hidden">
              <div className="h-full bg-accent/50" style={{ width: `${((cursor) / (bars.length - 1)) * 100}%` }} />
            </div>
            <span className="text-[10px] font-mono text-faint tabular-nums">bar {cursor + 1}/{bars.length}</span>
          </div>

          {/* trade log + stats */}
          <div className="bg-panel-hi border border-border rounded-[10px] shrink-0 max-h-[150px] flex flex-col">
            <div className="flex items-center justify-between px-4 py-1.5 border-b border-border">
              <span className="text-[9px] uppercase tracking-widest text-faint font-mono">Mock trades</span>
              <div className="flex items-center gap-4 font-mono text-[10px]">
                <span className="text-dim">{sim.trades.length} trades</span>
                <span className="text-dim">win {winRate}%</span>
                <span className={totalR >= 0 ? 'text-accent-ink' : 'text-red-300'}>{totalR >= 0 ? '+' : ''}{totalR.toFixed(1)}R</span>
                {sim.open && <span className="text-amber-400 animate-pulse-soft">● in trade</span>}
              </div>
            </div>
            <div className="overflow-y-auto px-4 py-1.5 flex flex-col-reverse gap-0.5">
              {sim.trades.length === 0 && !sim.open && (
                <div className="text-[10px] text-faint font-mono italic py-3 text-center">
                  build a rule, then press ▶ play — fired trades appear here
                </div>
              )}
              {sim.trades.map((t, i) => (
                <div key={i} className="flex items-center justify-between text-[10px] font-mono">
                  <span className="text-faint">#{i + 1} {t.side} @ {t.entry.toFixed(2)}</span>
                  <span className={t.win ? 'text-accent-ink' : 'text-red-300'}>{t.pnlR >= 0 ? '+' : ''}{t.pnlR.toFixed(1)}R</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <RulePanel rule={rule} onChange={setRule} />
      </div>

      {menu && <ContextMenu element={menu.element} x={menu.x} y={menu.y} onPick={addCondition} onClose={() => setMenu(null)} />}
    </div>
  )
}
