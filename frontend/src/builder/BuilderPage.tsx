import { useEffect, useMemo, useRef, useState } from 'react'
import { generateBars, detectFVGs, detectOBs, detectIFVGs, detectSwings, atr, filterByAtr, type Timeframe } from './mockData'
import { computeIndicator, getDef, type Computed, type IndicatorDef } from './indicators'
import type { Drawing } from './drawings'
import { DEFAULT_RULE, makeCondition, evaluateRule, stopPriceFor, type Rule, type EvalCtx, type MenuTarget, type CondSource } from './rules'
import type { CondDef } from './indicators'
import { BuilderChart, type ChartMarker, type Tool, type PriceOverlay } from './BuilderChart'
import { GenericSubPanel } from './SubPanels'
import { RulePanel } from './RulePanel'
import { ContextMenu } from './ContextMenu'
import { IndicatorCatalog } from './IndicatorCatalog'
import { SESSION_PRESETS, DEFAULT_SESSIONS, inSession, sessionLabel, type SessionState } from './sessions'
import { NEWS_EVENTS, DEFAULT_NEWS, passesNews, newsLabel, backtestNews, type NewsFilter } from './news'
import { DEFAULT_STRADDLE, simulateStraddle, straddleLabel, type StraddleConfig } from './straddle'
import { runBacktest, PERIODS, type Period, type BTResult } from './backtest'
import type { Candle } from './mockData'

interface Trade { entry: number; exit: number; pnlR: number; win: boolean; side: 'long' | 'short' }
interface OpenTrade { entry: number; stop: number; target: number; side: 'long' | 'short' }

function simulate(ctx: EvalCtx, rule: Rule, cursor: number, passesFilters: (b: Candle, i: number) => boolean) {
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
    } else if (passesFilters(b, i) && evaluateRule(rule, ctx, i)) {
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

function Stat({ label, value, good }: { label: string; value: string; good?: boolean }) {
  return (
    <div className="bg-panel-hi border border-border rounded-[8px] py-2">
      <div className={`text-[14px] font-mono ${good === undefined ? 'text-ink' : good ? 'text-accent-ink' : 'text-red-300'}`}>{value}</div>
      <div className="text-[8px] text-faint font-mono uppercase tracking-widest">{label}</div>
    </div>
  )
}

function EquityCurve({ equity }: { equity: number[] }) {
  if (equity.length < 2) return <div className="h-16 flex items-center justify-center text-[10px] text-faint font-mono border border-border rounded-[8px]">no closed trades</div>
  const min = Math.min(0, ...equity), max = Math.max(0, ...equity), span = max - min || 1
  const pts = equity.map((v, i) => `${(i / (equity.length - 1)) * 100},${100 - ((v - min) / span) * 100}`).join(' ')
  const zeroY = 100 - ((0 - min) / span) * 100
  return (
    <div className="h-16 bg-panel-hi border border-border rounded-[8px] overflow-hidden">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="w-full h-full">
        <line x1="0" y1={zeroY} x2="100" y2={zeroY} stroke="rgba(255,255,255,0.1)" strokeWidth="0.5" />
        <polyline points={pts} fill="none" stroke={equity[equity.length - 1] >= 0 ? '#6ee7b7' : '#f87171'} strokeWidth="1" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  )
}

export function BuilderPage() {
  const [tf, setTf] = useState<Timeframe>('5m')
  const bars = useMemo(() => generateBars(tf), [tf])
  const obs = useMemo(() => detectOBs(bars), [bars])
  const atrArr = useMemo(() => atr(bars), [bars])
  const [fvgMinAtr, setFvgMinAtr] = useState(0)
  const fvgs = useMemo(() => filterByAtr(detectFVGs(bars), atrArr, fvgMinAtr), [bars, atrArr, fvgMinAtr])
  const ifvgs = useMemo(() => filterByAtr(detectIFVGs(bars), atrArr, fvgMinAtr), [bars, atrArr, fvgMinAtr])

  const [activeIds, setActiveIds] = useState<string[]>(['ema'])
  const [showFvg, setShowFvg] = useState(true)
  const [showOb, setShowOb] = useState(false)
  const [showIfvg, setShowIfvg] = useState(false)
  const [showLiquidity, setShowLiquidity] = useState(false)
  const [liqLookback, setLiqLookback] = useState(5)
  const swings = useMemo(() => detectSwings(bars, liqLookback), [bars, liqLookback])
  const [drawings, setDrawings] = useState<Drawing[]>([])
  const [rule, setRule] = useState<Rule>(DEFAULT_RULE)
  const [sessions, setSessions] = useState<SessionState>(DEFAULT_SESSIONS)
  const [news, setNews] = useState<NewsFilter>(DEFAULT_NEWS)
  const [straddle, setStraddle] = useState<StraddleConfig>(DEFAULT_STRADDLE)
  const [tool, setTool] = useState<Tool>('cursor')
  const [cursor, setCursor] = useState(WARMUP)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(220)
  const [menu, setMenu] = useState<{ target: MenuTarget; x: number; y: number } | null>(null)
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [btOpen, setBtOpen] = useState(false)
  const [btPeriod, setBtPeriod] = useState<Period>('1y')
  const [btRunning, setBtRunning] = useState(false)
  const [btProgress, setBtProgress] = useState(0)
  const [btEta, setBtEta] = useState(0)
  const [btResult, setBtResult] = useState<BTResult | null>(null)
  const [btBars, setBtBars] = useState<Candle[]>([])
  const [replaying, setReplaying] = useState(false)
  const [replayCursor, setReplayCursor] = useState(WARMUP)
  const [replayPlaying, setReplayPlaying] = useState(false)
  const [replaySpeed, setReplaySpeed] = useState(50)
  const [flashId, setFlashId] = useState<string | null>(null)
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const seq = useRef(0)

  const computed = useMemo(() => {
    const m: Record<string, Computed> = {}
    activeIds.forEach(id => { const def = getDef(id); if (def) m[id] = computeIndicator(def, bars, 20) })
    return m
  }, [activeIds, bars])

  const drawingsMap = useMemo(() => Object.fromEntries(drawings.map(d => [d.id, d])), [drawings])
  const ctx: EvalCtx = useMemo(() => ({ bars, fvgs, obs, ifvgs, swings, liqLookback, computed, drawings: drawingsMap }), [bars, fvgs, obs, ifvgs, swings, liqLookback, computed, drawingsMap])
  const passesFilters = useMemo(() => (b: Candle, i: number) => inSession(b, sessions) && passesNews(i, news), [sessions, news])
  const sim = useMemo(() => simulate(ctx, rule, cursor, passesFilters), [ctx, rule, cursor, passesFilters])
  const straddleSim = useMemo(() => simulateStraddle(bars, NEWS_EVENTS, swings, straddle, cursor), [bars, swings, straddle, cursor])
  const allTrades = useMemo(() => [...sim.trades, ...straddleSim.trades], [sim, straddleSim])
  const allMarkers = useMemo(() => [...sim.markers, ...straddleSim.markers], [sim, straddleSim])
  const filterChips = useMemo(() => {
    const c: string[] = []
    const s = sessionLabel(sessions); if (s) c.push(`session: ${s}`)
    const n = newsLabel(news); if (n) c.push(n)
    const st = straddleLabel(straddle); if (st) c.push(st)
    return c
  }, [sessions, news, straddle])
  const newsLines = useMemo(() => NEWS_EVENTS.filter(e => e.idx <= cursor && bars[e.idx]).map(e => ({ time: bars[e.idx].time, name: e.name })), [bars, cursor])
  const revealed = useMemo(() => bars.slice(0, cursor + 1), [bars, cursor])
  const visibleSwings = useMemo(() => swings.filter(s => s.idx + liqLookback <= cursor), [swings, liqLookback, cursor])

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
    if (source.type === 'detector') {
      if (source.det === 'fvg') setShowFvg(true)
      else if (source.det === 'ob') setShowOb(true)
      else if (source.det === 'ifvg') setShowIfvg(true)
      else if (source.det === 'liquidity') setShowLiquidity(true)
      doFlash(source.det)
    }
    else if (source.type === 'indicator') doFlash(source.instId)
    else doFlash(source.drawingId)
    setMenu(null)
  }
  const addIndicator = (def: IndicatorDef) => { setActiveIds(ids => ids.includes(def.id) ? ids : [...ids, def.id]); doFlash(def.id) }
  const removeIndicator = (id: string) => { setActiveIds(ids => ids.filter(x => x !== id)); setRule(r => ({ ...r, conditions: r.conditions.filter(c => !(c.source.type === 'indicator' && c.source.instId === id)) })) }
  const addDrawing = (d: Omit<Drawing, 'id'>) => { const id = `dw${seq.current++}`; setDrawings(ds => [...ds, { ...d, id }]); setTool('cursor'); doFlash(id) }

  const runBT = async () => {
    setBtRunning(true); setBtProgress(0); setBtResult(null)
    await new Promise(r => setTimeout(r, 30))
    const longBars = generateBars(tf, 20260616, PERIODS[btPeriod])
    const atrL = atr(longBars)
    const ctxL: EvalCtx = {
      bars: longBars,
      fvgs: filterByAtr(detectFVGs(longBars), atrL, fvgMinAtr),
      obs: detectOBs(longBars),
      ifvgs: filterByAtr(detectIFVGs(longBars), atrL, fvgMinAtr),
      swings: detectSwings(longBars, liqLookback),
      liqLookback,
      computed: Object.fromEntries(activeIds.map(id => [id, computeIndicator(getDef(id), longBars, 20)]).filter(([, c]) => c)) as Record<string, Computed>,
      drawings: {},
    }
    const btEvents = backtestNews(longBars.length)
    const passesBT = (b: Candle, i: number) => inSession(b, sessions) && passesNews(i, news, btEvents)
    const res = await runBacktest(longBars, ctxL, rule, passesBT, straddle, btEvents, ctxL.swings, (frac, eta) => { setBtProgress(frac); setBtEta(eta) })
    setBtResult(res); setBtBars(longBars); setBtRunning(false)
  }

  const startReplay = () => { setBtOpen(false); setReplaying(true); setReplayCursor(WARMUP); setReplayPlaying(true) }
  const replayOverlay = useMemo<PriceOverlay[]>(() => {
    if (!replaying || !btBars.length) return []
    const c = computeIndicator(getDef('ema'), btBars, 20)
    const lastT = btBars[replayCursor]?.time ?? Infinity
    return [{ instId: 'ema', name: 'EMA', ikind: 'ma', lines: c.priceLines.map(ln => ({ ...ln, data: ln.data.filter(p => p.time <= lastT) })) }]
  }, [replaying, btBars, replayCursor])
  const replayMarkers = useMemo<ChartMarker[]>(() => {
    if (!replaying || !btResult) return []
    const m: ChartMarker[] = []
    for (const t of btResult.trades) {
      if (t.entryIdx < 0 || !btBars[t.entryIdx] || !btBars[t.exitIdx]) continue
      if (t.entryIdx <= replayCursor) { const long = t.side === 'long'; m.push({ time: btBars[t.entryIdx].time, position: long ? 'belowBar' : 'aboveBar', shape: long ? 'arrowUp' : 'arrowDown', color: long ? '#3ee0a5' : '#f87171', text: long ? 'L' : 'S' }) }
      if (t.exitIdx <= replayCursor) m.push({ time: btBars[t.exitIdx].time, position: t.side === 'long' ? 'aboveBar' : 'belowBar', shape: 'circle', color: t.win ? '#3ee0a5' : '#f87171', text: `${t.pnlR >= 0 ? '+' : ''}${t.pnlR.toFixed(1)}R` })
    }
    return m
  }, [replaying, btResult, btBars, replayCursor])
  const replayStats = useMemo(() => {
    if (!btResult) return { n: 0, r: 0 }
    let n = 0, r = 0
    for (const t of btResult.trades) if (t.entryIdx >= 0 && t.exitIdx <= replayCursor) { n++; r += t.pnlR }
    return { n, r: +r.toFixed(1) }
  }, [btResult, replayCursor])

  useEffect(() => {
    if (!replaying || !replayPlaying) return
    const id = setInterval(() => setReplayCursor(c => { if (c >= btBars.length - 1) { setReplayPlaying(false); return c } return c + 1 }), replaySpeed)
    return () => clearInterval(id)
  }, [replaying, replayPlaying, replaySpeed, btBars.length])

  const wins = allTrades.filter(t => t.win).length
  const totalR = allTrades.reduce((s, t) => s + t.pnlR, 0)
  const winRate = allTrades.length ? Math.round((wins / allTrades.length) * 100) : 0
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
          <button onClick={() => setBtOpen(true)} className="text-[10px] font-mono px-2.5 py-1 rounded-[6px] border border-accent/50 bg-accent/10 text-accent-ink hover:bg-accent/20 transition-colors">⚡ Backtest</button>
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
            <button onClick={() => setShowIfvg(v => !v)} className={`flex items-center justify-between px-2.5 py-1.5 rounded-[6px] border text-left ${showIfvg ? 'border-accent/50 bg-accent/10 text-accent-ink' : 'border-border bg-panel-hi text-dim'}`}><span className="text-[10px] font-mono">iFVG</span><span className="text-[11px]">{showIfvg ? '−' : '+'}</span></button>
            {(showFvg || showIfvg) && (
              <div className="flex items-center gap-1.5 px-1 pt-0.5">
                <span className="text-[8px] text-faint font-mono">min size</span>
                <input type="range" min={0} max={20} value={Math.round(fvgMinAtr * 10)} onChange={e => setFvgMinAtr(parseInt(e.target.value) / 10)} className="slider-accent flex-1" />
                <span className="text-[9px] text-dim font-mono tabular-nums w-9">{fvgMinAtr === 0 ? 'off' : `${fvgMinAtr.toFixed(1)}×A`}</span>
              </div>
            )}
            <button onClick={() => setShowOb(v => !v)} className={`flex items-center justify-between px-2.5 py-1.5 rounded-[6px] border text-left ${showOb ? 'border-accent/50 bg-accent/10 text-accent-ink' : 'border-border bg-panel-hi text-dim'}`}><span className="text-[10px] font-mono">OB</span><span className="text-[11px]">{showOb ? '−' : '+'}</span></button>
            <button onClick={() => setShowLiquidity(v => !v)} className={`flex items-center justify-between px-2.5 py-1.5 rounded-[6px] border text-left ${showLiquidity ? 'border-accent/50 bg-accent/10 text-accent-ink' : 'border-border bg-panel-hi text-dim'}`}><span className="text-[10px] font-mono">Liquidity</span><span className="text-[11px]">{showLiquidity ? '−' : '+'}</span></button>
            {showLiquidity && (
              <div className="flex items-center gap-1.5 px-1 pt-0.5">
                <span className="text-[8px] text-faint font-mono">swing lookback</span>
                <input type="range" min={2} max={12} value={liqLookback} onChange={e => setLiqLookback(parseInt(e.target.value))} className="slider-accent flex-1" />
                <span className="text-[9px] text-dim font-mono tabular-nums w-3">{liqLookback}</span>
              </div>
            )}
          </div>
          <div className="flex flex-col gap-1">
            <div className="text-[9px] uppercase tracking-widest text-faint font-mono px-1">Draw</div>
            <div className="flex gap-1">{TOOL_BTN('rect', '▭ rect')}{TOOL_BTN('trend', '╱ line')}</div>
            <div className="flex gap-1">{TOOL_BTN('hline', '— level')}<button onClick={() => setDrawings([])} className="flex-1 text-[10px] font-mono px-1.5 py-1.5 rounded-[6px] border border-border text-faint hover:text-red-300">clear</button></div>
            <div className="text-[8px] text-faint/70 font-mono px-1 pt-1 leading-relaxed">draw a rectangle for an FVG, then right-click it → "price enters".</div>
          </div>
          <div className="flex flex-col gap-1">
            <div className="text-[9px] uppercase tracking-widest text-faint font-mono px-1">Sessions</div>
            <div className="grid grid-cols-2 gap-1">
              {SESSION_PRESETS.map(w => (
                <button key={w.name} onClick={() => setSessions(s => ({ ...s, presets: { ...s.presets, [w.name]: !s.presets[w.name] } }))}
                  className={`text-[9px] font-mono px-1.5 py-1 rounded-[6px] border ${sessions.presets[w.name] ? 'border-accent bg-accent/10 text-accent-ink' : 'border-border text-dim hover:text-ink'}`}>{w.name}</button>
              ))}
            </div>
            <button onClick={() => setSessions(s => ({ ...s, customOn: !s.customOn }))}
              className={`text-[9px] font-mono px-1.5 py-1 rounded-[6px] border text-left ${sessions.customOn ? 'border-accent bg-accent/10 text-accent-ink' : 'border-border text-dim'}`}>custom window {sessions.customOn ? 'on' : 'off'}</button>
            {sessions.customOn && (
              <div className="flex items-center gap-1">
                <input value={sessions.from} onChange={e => setSessions(s => ({ ...s, from: e.target.value }))} className="w-12 bg-bg border border-border rounded px-1 py-0.5 text-[9px] font-mono text-ink" />
                <span className="text-[9px] text-faint">–</span>
                <input value={sessions.to} onChange={e => setSessions(s => ({ ...s, to: e.target.value }))} className="w-12 bg-bg border border-border rounded px-1 py-0.5 text-[9px] font-mono text-ink" />
                <span className="text-[8px] text-faint">ET</span>
              </div>
            )}
          </div>
          <div className="flex flex-col gap-1">
            <div className="text-[9px] uppercase tracking-widest text-faint font-mono px-1">News</div>
            <div className="flex gap-0">
              {(['off', 'only', 'avoid'] as const).map(m => (
                <button key={m} onClick={() => setNews(n => ({ ...n, mode: m }))}
                  className={`flex-1 text-[9px] font-mono px-1 py-1 border ${news.mode === m ? 'border-accent bg-accent/10 text-accent-ink' : 'border-border text-dim hover:text-ink'} ${m === 'off' ? 'rounded-l-[6px]' : m === 'avoid' ? 'rounded-r-[6px] border-l-0' : 'border-l-0'}`}>{m}</button>
              ))}
            </div>
            {news.mode !== 'off' && (
              <div className="flex items-center gap-1.5 px-1">
                <span className="text-[8px] text-faint font-mono">±window</span>
                <input type="range" min={1} max={10} value={news.windowBars} onChange={e => setNews(n => ({ ...n, windowBars: parseInt(e.target.value) }))} className="slider-accent flex-1" />
                <span className="text-[9px] text-dim font-mono w-3">{news.windowBars}</span>
              </div>
            )}
            <div className="text-[8px] text-faint/70 font-mono px-1 leading-relaxed">CPI / FOMC / NFP marked on the chart. "only" trades around them; "avoid" stays out.</div>
          </div>
          <div className="flex flex-col gap-1">
            <div className="text-[9px] uppercase tracking-widest text-faint font-mono px-1">Event Straddle</div>
            <button onClick={() => setStraddle(s => ({ ...s, enabled: !s.enabled }))}
              className={`text-[10px] font-mono px-2 py-1.5 rounded-[6px] border text-left ${straddle.enabled ? 'border-accent/50 bg-accent/10 text-accent-ink' : 'border-border bg-panel-hi text-dim'}`}>{straddle.enabled ? '● armed' : '○ off'}</button>
            {straddle.enabled && (<>
              <div className="grid grid-cols-2 gap-1">
                {(['all', 'CPI', 'FOMC', 'NFP'] as const).map(ev => (
                  <button key={ev} onClick={() => setStraddle(s => ({ ...s, event: ev }))} className={`text-[9px] font-mono px-1 py-1 rounded-[5px] border ${straddle.event === ev ? 'border-accent bg-accent/10 text-accent-ink' : 'border-border text-dim hover:text-ink'}`}>{ev}</button>
                ))}
              </div>
              <div className="flex gap-0">
                {(['price', 'swing'] as const).map(a => (
                  <button key={a} onClick={() => setStraddle(s => ({ ...s, anchor: a }))} className={`flex-1 text-[9px] font-mono px-1 py-1 border ${straddle.anchor === a ? 'border-accent bg-accent/10 text-accent-ink' : 'border-border text-dim'} ${a === 'price' ? 'rounded-l-[5px]' : 'rounded-r-[5px] border-l-0'}`}>{a === 'price' ? '± price' : '± swing'}</button>
                ))}
              </div>
              <div className="flex items-center gap-1 text-[9px] font-mono text-faint">
                <span>offset</span><input type="number" value={straddle.offsetTicks} onChange={e => setStraddle(s => ({ ...s, offsetTicks: parseInt(e.target.value) || 0 }))} className="w-10 bg-bg border border-border rounded px-1 py-0.5 text-ink" /><span>ticks</span>
              </div>
              <div className="flex items-center gap-1 text-[9px] font-mono text-faint">
                <span>arm</span><input type="number" value={straddle.armBars} onChange={e => setStraddle(s => ({ ...s, armBars: parseInt(e.target.value) || 1 }))} className="w-8 bg-bg border border-border rounded px-1 py-0.5 text-ink" /><span>b · tgt</span><input type="number" value={straddle.targetR} onChange={e => setStraddle(s => ({ ...s, targetR: parseFloat(e.target.value) || 3 }))} className="w-8 bg-bg border border-border rounded px-1 py-0.5 text-ink" /><span>R</span>
              </div>
            </>)}
          </div>
        </div>

        {/* center */}
        <div className="flex-1 min-w-0 flex flex-col gap-2.5">
          <div className="flex-1 min-h-0 bg-panel-hi border border-border rounded-[10px] overflow-hidden">
            <BuilderChart
              bars={replaying ? btBars.slice(0, replayCursor + 1) : revealed}
              overlays={replaying ? replayOverlay : priceOverlays}
              fvgs={replaying ? [] : fvgs} obs={replaying ? [] : obs} ifvgs={replaying ? [] : ifvgs}
              showFvg={!replaying && showFvg} showOb={!replaying && showOb} showIfvg={!replaying && showIfvg}
              swings={replaying ? [] : visibleSwings} showLiquidity={!replaying && showLiquidity} news={replaying ? [] : newsLines}
              drawings={replaying ? [] : drawings} tool={replaying ? 'cursor' : tool} onAddDrawing={addDrawing}
              markers={replaying ? replayMarkers : allMarkers} straddleLevels={replaying ? [] : straddleSim.levels}
              lines={replaying || !sim.open ? {} : { entry: sim.open.entry, stop: sim.open.stop, target: sim.open.target }}
              flashId={flashId} onRequestMenu={(t, x, y) => setMenu({ target: t, x, y })} />
          </div>
          {subInsts.map(d => computed[d.id]?.sub && (
            <GenericSubPanel key={d.id} instId={d.id} name={d.name} ikind={d.kind} sub={computed[d.id].sub!} cursor={cursor} flash={flashId === d.id} onRequestMenu={(t, x, y) => setMenu({ target: t, x, y })} />
          ))}

          {/* sim controls / replay controls */}
          {!replaying ? (
          <div className="bg-panel-hi border border-border rounded-[10px] px-4 py-2.5 flex items-center gap-4 shrink-0">
            <button onClick={() => { if (atEnd) setCursor(WARMUP); setPlaying(p => !p) }} className="text-[11px] font-mono uppercase tracking-wider px-3 py-1.5 rounded-[6px] border border-accent/50 bg-accent/10 text-accent-ink hover:bg-accent/20 transition-colors w-[64px]">{playing ? '❚❚ pause' : atEnd ? '↻ replay' : '▶ play'}</button>
            <button onClick={() => { setPlaying(false); setCursor(c => Math.min(bars.length - 1, c + 1)) }} className="text-[11px] font-mono px-2.5 py-1.5 rounded-[6px] border border-border text-dim hover:text-ink transition-colors">step ▶</button>
            <button onClick={() => { setPlaying(false); setCursor(WARMUP) }} className="text-[11px] font-mono px-2.5 py-1.5 rounded-[6px] border border-border text-faint hover:text-dim transition-colors">reset</button>
            <div className="flex items-center gap-2"><span className="text-[9px] text-faint font-mono">speed</span><input type="range" min={40} max={500} step={20} value={520 - speed} onChange={e => setSpeed(520 - parseInt(e.target.value))} className="slider-accent w-24" /></div>
            <div className="flex-1 h-1 bg-bg rounded-full overflow-hidden"><div className="h-full bg-accent/50" style={{ width: `${(cursor / (bars.length - 1)) * 100}%` }} /></div>
            <span className="text-[10px] font-mono text-faint tabular-nums">bar {cursor + 1}/{bars.length}</span>
          </div>
          ) : (
          <div className="bg-panel-hi border border-accent/40 rounded-[10px] px-4 py-2.5 flex items-center gap-4 shrink-0">
            <span className="text-[10px] font-mono uppercase tracking-widest text-accent-ink">⏵ Replay</span>
            <button onClick={() => { if (replayCursor >= btBars.length - 1) setReplayCursor(WARMUP); setReplayPlaying(p => !p) }}
              className="text-[11px] font-mono uppercase tracking-wider px-3 py-1.5 rounded-[6px] border border-accent/50 bg-accent/10 text-accent-ink hover:bg-accent/20 w-[64px]">{replayPlaying ? '❚❚ pause' : replayCursor >= btBars.length - 1 ? '↻ restart' : '▶ play'}</button>
            <button onClick={() => { setReplayPlaying(false); setReplayCursor(c => Math.min(btBars.length - 1, c + 1)) }} className="text-[11px] font-mono px-2.5 py-1.5 rounded-[6px] border border-border text-dim hover:text-ink">step ▶</button>
            <div className="flex items-center gap-2"><span className="text-[9px] text-faint font-mono">speed</span><input type="range" min={10} max={300} step={10} value={310 - replaySpeed} onChange={e => setReplaySpeed(310 - parseInt(e.target.value))} className="slider-accent w-24" /></div>
            <div className="flex-1 h-1 bg-bg rounded-full overflow-hidden"><div className="h-full bg-accent/50" style={{ width: `${(replayCursor / (btBars.length - 1)) * 100}%` }} /></div>
            <span className="text-[10px] font-mono text-dim">{replayStats.n} trades · <span className={replayStats.r >= 0 ? 'text-accent-ink' : 'text-red-300'}>{replayStats.r >= 0 ? '+' : ''}{replayStats.r}R</span></span>
            <span className="text-[10px] font-mono text-faint tabular-nums">bar {replayCursor + 1}/{btBars.length}</span>
            <button onClick={() => { setReplaying(false); setReplayPlaying(false) }} className="text-[10px] font-mono px-2.5 py-1.5 rounded-[6px] border border-border text-faint hover:text-red-300">exit replay</button>
          </div>
          )}

          {/* trade log */}
          <div className="bg-panel-hi border border-border rounded-[10px] shrink-0 max-h-[140px] flex flex-col">
            <div className="flex items-center justify-between px-4 py-1.5 border-b border-border">
              <span className="text-[9px] uppercase tracking-widest text-faint font-mono">Mock trades</span>
              <div className="flex items-center gap-4 font-mono text-[10px]">
                <span className="text-dim">{allTrades.length} trades</span><span className="text-dim">win {winRate}%</span>
                <span className={totalR >= 0 ? 'text-accent-ink' : 'text-red-300'}>{totalR >= 0 ? '+' : ''}{totalR.toFixed(1)}R</span>
                {sim.open && <span className="text-amber-400 animate-pulse-soft">● in trade</span>}
              </div>
            </div>
            <div className="overflow-y-auto px-4 py-1.5 flex flex-col-reverse gap-0.5">
              {allTrades.length === 0 && !sim.open && <div className="text-[10px] text-faint font-mono italic py-3 text-center">build a rule, then press ▶ play — fired trades appear here</div>}
              {allTrades.map((t, i) => (
                <div key={i} className="flex items-center justify-between text-[10px] font-mono"><span className="text-faint">#{i + 1} {t.side} @ {t.entry.toFixed(2)}</span><span className={t.win ? 'text-accent-ink' : 'text-red-300'}>{t.pnlR >= 0 ? '+' : ''}{t.pnlR.toFixed(1)}R</span></div>
              ))}
            </div>
          </div>
        </div>

        <RulePanel rule={rule} onChange={setRule} filters={filterChips} />
      </div>

      {catalogOpen && <IndicatorCatalog active={new Set(activeIds)} onAdd={addIndicator} onClose={() => setCatalogOpen(false)} />}
      {menu && <ContextMenu target={menu.target} x={menu.x} y={menu.y} onPick={addCondition} onClose={() => setMenu(null)} />}

      {btOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={() => !btRunning && setBtOpen(false)}>
          <div className="w-[480px] bg-bg border border-border rounded-[12px] shadow-2xl p-5 flex flex-col gap-4 animate-fade-up" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <span className="text-[13px] font-medium text-ink">Backtest <span className="text-[10px] text-faint font-mono ml-1">your current strategy on a long synthetic series</span></span>
              <button onClick={() => !btRunning && setBtOpen(false)} className="text-faint hover:text-ink text-[16px] leading-none">×</button>
            </div>
            <div className="flex gap-1">
              {(Object.keys(PERIODS) as Period[]).map(p => (
                <button key={p} onClick={() => setBtPeriod(p)} disabled={btRunning}
                  className={`flex-1 px-2 py-2 rounded-[7px] border ${btPeriod === p ? 'border-accent bg-accent/10 text-accent-ink' : 'border-border text-dim hover:text-ink'}`}>
                  <div className="text-[12px] font-mono">{p}</div>
                  <div className="text-[8px] text-faint font-mono">{PERIODS[p].toLocaleString()} bars</div>
                </button>
              ))}
            </div>
            {!btRunning && !btResult && (
              <button onClick={runBT} className="text-[12px] font-mono px-3 py-2.5 rounded-[8px] border border-accent/50 bg-accent/10 text-accent-ink hover:bg-accent/20 transition-colors">▶ Run backtest</button>
            )}
            {btRunning && (
              <div className="flex flex-col gap-2">
                <div className="flex justify-between text-[10px] font-mono text-dim"><span>processing… {Math.round(btProgress * 100)}%</span><span>ETA {(btEta / 1000).toFixed(1)}s</span></div>
                <div className="h-2 bg-panel-hi rounded-full overflow-hidden"><div className="h-full bg-accent transition-all" style={{ width: `${btProgress * 100}%` }} /></div>
              </div>
            )}
            {btResult && !btRunning && (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-4 gap-2 text-center">
                  <Stat label="trades" value={String(btResult.trades.length)} />
                  <Stat label="win %" value={`${btResult.winRate}%`} />
                  <Stat label="net R" value={`${btResult.netR >= 0 ? '+' : ''}${btResult.netR}`} good={btResult.netR >= 0} />
                  <Stat label="max DD" value={`${btResult.maxDD}R`} good={false} />
                </div>
                <EquityCurve equity={btResult.equity} />
                <div className="text-[9px] font-mono text-faint">ran {btResult.bars.toLocaleString()} bars in {(btResult.ms / 1000).toFixed(2)}s · {btPeriod} · equity in R</div>
                <div className="flex gap-2">
                  <button onClick={runBT} className="flex-1 text-[11px] font-mono px-2 py-2 rounded-[7px] border border-border text-dim hover:text-ink transition-colors">↻ re-run</button>
                  <button onClick={startReplay} className="flex-1 text-[11px] font-mono px-2 py-2 rounded-[7px] border border-accent/50 bg-accent/10 text-accent-ink hover:bg-accent/20 transition-colors">▶ Replay trades</button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
