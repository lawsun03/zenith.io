import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { createChart, CandlestickSeries, createSeriesMarkers } from 'lightweight-charts'
import { Logo } from './Logo'
import { fmtBarTs } from '../utils/format'

// ---------------------------------------------------------------------
// Types (server: app/api/server.py's /api/trainer/* handlers)
// ---------------------------------------------------------------------

interface EnsembleSummary {
  id: string
  family: string
  created_at: string
  member_count: number
  sharpe_oos: number | null
  combine_payout_prob: number | null
}

interface BarJson {
  ts: string
  open: string
  high: string
  low: string
  close: string
  volume: number
}

interface CurrentDecision {
  index: number
  total: number
  instrument: string
  hypothesis_id: string
  regime_label: string | null
  decision_ts: string
  bars: BarJson[]
}

interface AnswerResult {
  score: { correct_setup: boolean; correct_direction: boolean | null; correct_stop: boolean | null }
  ir_ground_truth: {
    fired: boolean; near_miss: boolean; side: string | null
    stop_price: string | null; target_price: string | null
  }
  market_outcome_bars: BarJson[]
  done: boolean
  next: CurrentDecision | null
}

interface CompleteResult {
  session_id: string
  fidelity_score: number
  n_decisions: number
  setups_correctly_taken: number
  setups_missed: number
  false_positives: number
  direction_errors: number
  stop_placement_errors: number
  divergence: {
    trades_available: number
    trades_taken: number
    fraction_taken: string
    strategy_equity_r: string[]
    trainee_equity_r: string[]
  }
}

interface DrillSessionRow {
  id: string
  started_at: string
  fidelity_score: number
  n_decisions: number
}

// ---------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------

function toChartBar(b: BarJson) {
  return {
    time: Math.floor(new Date(b.ts).getTime() / 1000),
    open: Number(b.open), high: Number(b.high), low: Number(b.low), close: Number(b.close),
  }
}

function Sparkline({ values, color, height = 40 }: { values: number[]; color: string; height?: number }) {
  if (values.length === 0) {
    return <div className="text-[10px] text-faint font-mono">no data</div>
  }
  const w = 160
  const min = Math.min(0, ...values)
  const max = Math.max(0, ...values)
  const span = max - min || 1
  const pts = values.map((v, i) => {
    const x = values.length === 1 ? w : (i / (values.length - 1)) * w
    const y = height - ((v - min) / span) * height
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const zeroY = height - ((0 - min) / span) * height
  return (
    <svg width={w} height={height} className="overflow-visible">
      <line x1={0} y1={zeroY} x2={w} y2={zeroY} stroke="currentColor" className="text-border" strokeDasharray="2,2" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  )
}

// ---------------------------------------------------------------------
// Bar-by-bar replay chart — future hidden until answered
// ---------------------------------------------------------------------

function ReplayChart({ bars, outcomeBars }: { bars: BarJson[]; outcomeBars: BarJson[] }) {
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const el = containerRef.current
    if (!el || bars.length === 0) return

    const chart = createChart(el, {
      autoSize: true,
      height: 320,
      layout: { background: { color: 'transparent' }, textColor: '#6a85b0', fontFamily: "'JetBrains Mono', monospace", fontSize: 11 },
      grid: { vertLines: { color: 'rgba(255,255,255,0.03)' }, horzLines: { color: 'rgba(255,255,255,0.03)' } },
      crosshair: { vertLine: { color: 'rgba(110,231,183,0.35)' }, horzLine: { color: 'rgba(110,231,183,0.35)' } },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.05)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.05)', timeVisible: true, secondsVisible: false },
    })
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const series = chart.addSeries(CandlestickSeries as any, {
      upColor: '#6ee7b7', downColor: '#fca5a5',
      borderUpColor: '#6ee7b7', borderDownColor: '#fca5a5',
      wickUpColor: 'rgba(110,231,183,0.6)', wickDownColor: 'rgba(252,165,165,0.6)',
    })

    const visible = bars.map(toChartBar)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    series.setData(visible as any)

    const decisionTime = visible[visible.length - 1]?.time
    const markersApi = createSeriesMarkers(series as never)
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const markers: any[] = decisionTime !== undefined ? [{
      time: decisionTime, position: 'aboveBar', shape: 'arrowDown',
      color: '#fbbf24', text: 'YOU DECIDE HERE', size: 1.2,
    }] : []

    if (outcomeBars.length > 0) {
      const outcomeChart = outcomeBars.map(toChartBar)
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      series.setData([...visible, ...outcomeChart] as any)
      markers.push({
        time: outcomeChart[0].time, position: 'belowBar', shape: 'circle',
        color: '#3d5070', text: 'market continues (not scored)', size: 1,
      })
    }
    markers.sort((a, b) => a.time - b.time)
    markersApi.setMarkers(markers)

    chart.timeScale().fitContent()
    return () => { chart.remove() }
  }, [bars, outcomeBars])

  return (
    <div className="relative" style={{ height: 320 }}>
      <div ref={containerRef} className="absolute inset-0" />
    </div>
  )
}

// ---------------------------------------------------------------------
// Setup screen
// ---------------------------------------------------------------------

function SetupScreen({ onStart }: { onStart: (params: {
  ensemble_id: string; start_date: string; end_date: string; n_decisions: number;
  regime_label: string | null; seed: number | null
}) => void }) {
  const [ensembles, setEnsembles] = useState<EnsembleSummary[]>([])
  const [regimeLabels, setRegimeLabels] = useState<string[]>([])
  const [ensembleId, setEnsembleId] = useState('')
  const [startDate, setStartDate] = useState('2010-06-06')
  const [endDate, setEndDate] = useState('2024-12-31')
  const [nDecisions, setNDecisions] = useState(20)
  const [regimeLabel, setRegimeLabel] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    fetch('/api/trainer/ensembles').then(r => r.json()).then((rows: EnsembleSummary[]) => {
      setEnsembles(rows)
      if (rows.length > 0) setEnsembleId(rows[0].id)
    }).catch(() => {})
    fetch('/api/trainer/regime-labels').then(r => r.json()).then(setRegimeLabels).catch(() => {})
  }, [])

  async function handleStart() {
    if (!ensembleId) { setError('no active ensemble to drill'); return }
    setLoading(true)
    setError(null)
    try {
      onStart({
        ensemble_id: ensembleId, start_date: startDate, end_date: endDate,
        n_decisions: nDecisions, regime_label: regimeLabel || null, seed: null,
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-panel-hi backdrop-blur-md border border-border rounded-[10px] p-5 max-w-[520px] mx-auto animate-fade-up">
      <div className="text-[10px] tracking-[0.3em] text-dim uppercase mb-4">Start a drill</div>

      {ensembles.length === 0 && (
        <div className="text-[11px] font-mono text-faint mb-4">
          no active ensembles yet — the research loop writes one here once a candidate family clears every gate
        </div>
      )}

      <div className="flex flex-col gap-3 font-mono text-[11px]">
        <label className="flex flex-col gap-1">
          <span className="text-dim">ensemble</span>
          <select value={ensembleId} onChange={e => setEnsembleId(e.target.value)}
            className="bg-panel border border-border rounded px-2 py-1.5 text-ink">
            {ensembles.map(e => (
              <option key={e.id} value={e.id}>
                {e.family} · {e.member_count} members · sharpe {e.sharpe_oos?.toFixed(2) ?? '—'}
              </option>
            ))}
          </select>
        </label>

        <div className="flex gap-3">
          <label className="flex flex-col gap-1 flex-1">
            <span className="text-dim">start date</span>
            <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)}
              className="bg-panel border border-border rounded px-2 py-1.5 text-ink" />
          </label>
          <label className="flex flex-col gap-1 flex-1">
            <span className="text-dim">end date</span>
            <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)}
              className="bg-panel border border-border rounded px-2 py-1.5 text-ink" />
          </label>
        </div>

        <div className="flex gap-3">
          <label className="flex flex-col gap-1 flex-1">
            <span className="text-dim"># decisions</span>
            <input type="number" min={1} max={200} value={nDecisions}
              onChange={e => setNDecisions(Number(e.target.value))}
              className="bg-panel border border-border rounded px-2 py-1.5 text-ink" />
          </label>
          <label className="flex flex-col gap-1 flex-1">
            <span className="text-dim">regime filter</span>
            <select value={regimeLabel} onChange={e => setRegimeLabel(e.target.value)}
              className="bg-panel border border-border rounded px-2 py-1.5 text-ink">
              <option value="">any</option>
              {regimeLabels.map(l => <option key={l} value={l}>{l}</option>)}
            </select>
          </label>
        </div>

        {error && <div className="text-danger">{error}</div>}

        <button onClick={handleStart} disabled={loading || !ensembleId}
          className="mt-2 text-[11px] tracking-wide uppercase px-3 py-2 rounded bg-accent/15 text-accent-ink border border-accent/40 hover:bg-accent/25 disabled:opacity-40 transition-colors">
          {loading ? 'starting…' : 'Start drill'}
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------
// Answer form — the three scored questions, in order
// ---------------------------------------------------------------------

function AnswerForm({ onSubmit, disabled }: {
  onSubmit: (isSetup: boolean, direction: string | null, stopPrice: string | null) => void
  disabled: boolean
}) {
  const [isSetup, setIsSetup] = useState<boolean | null>(null)
  const [direction, setDirection] = useState<string | null>(null)
  const [stopPrice, setStopPrice] = useState('')

  useEffect(() => { setIsSetup(null); setDirection(null); setStopPrice('') }, [disabled])

  const canSubmit = isSetup === false || (isSetup === true && direction !== null && stopPrice.trim() !== '')

  return (
    <div className="flex flex-col gap-3 font-mono text-[11px]">
      <div>
        <div className="text-dim mb-1.5">1 — is this a setup?</div>
        <div className="flex gap-1.5">
          <button disabled={disabled} onClick={() => setIsSetup(true)}
            className={`px-3 py-1.5 rounded border transition-colors ${isSetup === true ? 'border-accent/50 bg-accent/15 text-accent-ink' : 'border-border text-dim hover:text-ink'}`}>
            YES
          </button>
          <button disabled={disabled} onClick={() => setIsSetup(false)}
            className={`px-3 py-1.5 rounded border transition-colors ${isSetup === false ? 'border-accent/50 bg-accent/15 text-accent-ink' : 'border-border text-dim hover:text-ink'}`}>
            NO
          </button>
        </div>
      </div>

      {isSetup === true && (
        <>
          <div>
            <div className="text-dim mb-1.5">2 — which direction?</div>
            <div className="flex gap-1.5">
              <button disabled={disabled} onClick={() => setDirection('long')}
                className={`px-3 py-1.5 rounded border transition-colors ${direction === 'long' ? 'border-accent/50 bg-accent/15 text-accent-ink' : 'border-border text-dim hover:text-ink'}`}>
                LONG
              </button>
              <button disabled={disabled} onClick={() => setDirection('short')}
                className={`px-3 py-1.5 rounded border transition-colors ${direction === 'short' ? 'border-danger/50 bg-danger/15 text-danger' : 'border-border text-dim hover:text-ink'}`}>
                SHORT
              </button>
            </div>
          </div>
          <div>
            <div className="text-dim mb-1.5">3 — where does the stop go?</div>
            <input disabled={disabled} value={stopPrice} onChange={e => setStopPrice(e.target.value)}
              placeholder="price" className="bg-panel border border-border rounded px-2 py-1.5 text-ink w-32" />
          </div>
        </>
      )}

      <button
        disabled={disabled || !canSubmit || isSetup === null}
        onClick={() => onSubmit(isSetup as boolean, isSetup ? direction : null, isSetup ? stopPrice : null)}
        className="mt-1 text-[11px] tracking-wide uppercase px-3 py-2 rounded bg-accent/15 text-accent-ink border border-accent/40 hover:bg-accent/25 disabled:opacity-40 transition-colors self-start"
      >
        Submit answer
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------
// Reveal — scored against the IR, market outcome shown separately
// ---------------------------------------------------------------------

function RevealPanel({ result }: { result: AnswerResult }) {
  const { score, ir_ground_truth: truth } = result
  const Badge = ({ ok }: { ok: boolean | null }) => ok === null
    ? <span className="text-faint">n/a</span>
    : <span className={ok ? 'text-accent-ink' : 'text-danger'}>{ok ? 'CORRECT' : 'WRONG'}</span>

  return (
    <div className="flex flex-col gap-3">
      <div className="bg-panel border border-border rounded-[8px] p-3 font-mono text-[11px]">
        <div className="text-[10px] tracking-[0.2em] text-dim uppercase mb-2">Score — graded against the IR's rules</div>
        <div className="flex justify-between py-0.5"><span className="text-dim">setup</span><Badge ok={score.correct_setup} /></div>
        <div className="flex justify-between py-0.5"><span className="text-dim">direction</span><Badge ok={score.correct_direction} /></div>
        <div className="flex justify-between py-0.5"><span className="text-dim">stop</span><Badge ok={score.correct_stop} /></div>
        <div className="mt-2 pt-2 border-t border-border text-dim">
          IR says: {truth.fired
            ? `${truth.side} · stop ${truth.stop_price} · target ${truth.target_price}`
            : truth.near_miss ? 'near miss — armed but not confirmed' : 'no setup'}
        </div>
      </div>

      <div className="bg-panel/50 border border-dashed border-border rounded-[8px] p-3 font-mono text-[11px]">
        <div className="text-[10px] tracking-[0.2em] text-faint uppercase mb-1">
          Market outcome — information only, never part of the score
        </div>
        <div className="text-faint">
          {result.market_outcome_bars.length > 0
            ? `price continued through ${result.market_outcome_bars.length} more bars — see chart`
            : 'no further bars in this session'}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------
// Complete screen — divergence + notes
// ---------------------------------------------------------------------

function CompleteScreen({ result, sessions }: {
  result: CompleteResult
  sessions: DrillSessionRow[]
}) {
  const div = result.divergence
  const strategyR = div.strategy_equity_r.map(Number)
  const trainR = div.trainee_equity_r.map(Number)
  const trend = sessions.map(s => s.fidelity_score)

  return (
    <div className="bg-panel-hi backdrop-blur-md border border-border rounded-[10px] p-5 max-w-[640px] mx-auto animate-fade-up flex flex-col gap-4">
      <div className="text-[10px] tracking-[0.3em] text-dim uppercase">Session complete</div>

      <div className="flex items-baseline gap-4 font-mono">
        <span className="text-[28px] text-ink font-semibold tabular-nums">{(result.fidelity_score * 100).toFixed(1)}%</span>
        <span className="text-[11px] text-dim">fidelity over {result.n_decisions} decisions</span>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-[11px]">
        <div className="flex justify-between"><span className="text-dim">setups taken</span><span className="text-ink">{result.setups_correctly_taken}</span></div>
        <div className="flex justify-between"><span className="text-dim">setups missed</span><span className="text-ink">{result.setups_missed}</span></div>
        <div className="flex justify-between"><span className="text-dim">false positives</span><span className="text-ink">{result.false_positives}</span></div>
        <div className="flex justify-between"><span className="text-dim">direction errors</span><span className="text-ink">{result.direction_errors}</span></div>
        <div className="flex justify-between"><span className="text-dim">stop errors</span><span className="text-ink">{result.stop_placement_errors}</span></div>
      </div>

      <div className="border-t border-border pt-3">
        <div className="text-[10px] tracking-[0.2em] text-faint uppercase mb-2">Divergence — informational, not scored</div>
        <div className="font-mono text-[11px] text-dim mb-2">
          took {div.trades_taken}/{div.trades_available} of the strategy's real trades
          ({(Number(div.fraction_taken) * 100).toFixed(0)}%)
        </div>
        <div className="flex gap-6">
          <div>
            <div className="text-[10px] text-dim font-mono mb-1">strategy equity (R)</div>
            <Sparkline values={strategyR} color="#6ee7b7" />
          </div>
          <div>
            <div className="text-[10px] text-dim font-mono mb-1">your equity (R)</div>
            <Sparkline values={trainR} color="#fbbf24" />
          </div>
        </div>
      </div>

      {sessions.length > 1 && (
        <div className="border-t border-border pt-3">
          <div className="text-[10px] tracking-[0.2em] text-faint uppercase mb-2">Fidelity trend</div>
          <Sparkline values={trend} color="#a7f3d0" />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------
// Notes — collected before /complete, since that's the only call the
// ledger's notes column is ever written through (drill_sessions.notes).
// ---------------------------------------------------------------------

function NotesScreen({ onFinish }: { onFinish: (notes: string) => void }) {
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)

  return (
    <div className="bg-panel-hi backdrop-blur-md border border-border rounded-[10px] p-5 max-w-[520px] mx-auto animate-fade-up flex flex-col gap-2">
      <div className="text-[10px] tracking-[0.3em] text-dim uppercase">Last one — before the tally</div>
      <div className="font-mono text-[11px] text-dim">What did you feel on the ones you got wrong?</div>
      <textarea value={notes} onChange={e => setNotes(e.target.value)} disabled={submitting}
        rows={4} className="bg-panel border border-border rounded px-2 py-1.5 text-ink font-mono text-[11px]" />
      <button
        disabled={submitting}
        onClick={() => { setSubmitting(true); onFinish(notes) }}
        className="self-start mt-1 text-[11px] tracking-wide uppercase px-3 py-2 rounded bg-accent/15 text-accent-ink border border-accent/40 hover:bg-accent/25 disabled:opacity-40 transition-colors"
      >
        {submitting ? 'finishing…' : 'Finish session'}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------

type Phase = 'setup' | 'drilling' | 'revealed' | 'notes' | 'complete'

export function TrainerPage() {
  const [phase, setPhase] = useState<Phase>('setup')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [ensembleId, setEnsembleId] = useState<string | null>(null)
  const [current, setCurrent] = useState<CurrentDecision | null>(null)
  const [reveal, setReveal] = useState<AnswerResult | null>(null)
  const [completeResult, setCompleteResult] = useState<CompleteResult | null>(null)
  const [sessions, setSessions] = useState<DrillSessionRow[]>([])
  const [error, setError] = useState<string | null>(null)

  const handleStart = useCallback(async (params: {
    ensemble_id: string; start_date: string; end_date: string; n_decisions: number;
    regime_label: string | null; seed: number | null
  }) => {
    setError(null)
    const r = await fetch('/api/trainer/sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(params),
    })
    const body = await r.json()
    if (!r.ok) { setError(body.error ?? 'failed to start session'); return }
    setSessionId(body.session_id)
    setEnsembleId(params.ensemble_id)
    setCurrent(body.current)
    setPhase('drilling')
  }, [])

  const handleAnswer = useCallback(async (isSetup: boolean, direction: string | null, stopPrice: string | null) => {
    if (!sessionId) return
    const r = await fetch(`/api/trainer/sessions/${sessionId}/answer`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_setup: isSetup, direction, stop_price: stopPrice }),
    })
    const body: AnswerResult = await r.json()
    setReveal(body)
    setPhase('revealed')
  }, [sessionId])

  const handleNext = useCallback(() => {
    if (!reveal) return
    if (reveal.done) {
      setPhase('notes')
    } else {
      setCurrent(reveal.next)
      setReveal(null)
      setPhase('drilling')
    }
  }, [reveal])

  async function finishSession(notes: string) {
    if (!sessionId) return
    const r = await fetch(`/api/trainer/sessions/${sessionId}/complete`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notes }),
    })
    const body: CompleteResult = await r.json()
    setCompleteResult(body)
    setPhase('complete')
    if (ensembleId) {
      fetch(`/api/trainer/sessions?ensemble_id=${ensembleId}`).then(r2 => r2.json()).then(setSessions).catch(() => {})
    }
  }

  function handleRestart() {
    setPhase('setup')
    setSessionId(null)
    setCurrent(null)
    setReveal(null)
    setCompleteResult(null)
  }

  return (
    <div className="min-h-screen px-5 py-4 max-w-[1100px] mx-auto">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <Logo />
          <span className="text-sm text-ink font-medium">Trainer</span>
          <span className="text-[10px] font-mono text-faint">replay drill</span>
        </div>
        <div className="flex items-center gap-4 text-[11px] font-mono">
          <Link to="/research" className="text-dim hover:text-ink transition-colors">research</Link>
          <Link to="/" className="text-dim hover:text-ink transition-colors">dashboard</Link>
        </div>
      </div>

      {error && (
        <div className="max-w-[520px] mx-auto mb-4 font-mono text-[11px] text-danger">{error}</div>
      )}

      {phase === 'setup' && <SetupScreen onStart={handleStart} />}

      {(phase === 'drilling' || phase === 'revealed') && current && (
        <div className="bg-panel-hi backdrop-blur-md border border-border rounded-[10px] overflow-hidden animate-fade-up">
          <div className="flex items-center justify-between px-[18px] py-2.5 border-b border-border font-mono text-[11px]">
            <span className="text-dim">{current.instrument} · {fmtBarTs(current.decision_ts)}{current.regime_label ? ` · ${current.regime_label}` : ''}</span>
            <span className="text-faint tabular-nums">{current.index + 1}/{current.total}</span>
          </div>
          <ReplayChart bars={current.bars} outcomeBars={reveal?.market_outcome_bars ?? []} />
          <div className="p-[18px] border-t border-border">
            {phase === 'drilling' && <AnswerForm onSubmit={handleAnswer} disabled={false} />}
            {phase === 'revealed' && reveal && (
              <div className="flex flex-col gap-3">
                <RevealPanel result={reveal} />
                <button onClick={handleNext}
                  className="self-start text-[11px] tracking-wide uppercase px-3 py-2 rounded bg-accent/15 text-accent-ink border border-accent/40 hover:bg-accent/25 transition-colors">
                  {reveal.done ? 'Finish session' : 'Next decision'}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {phase === 'notes' && <NotesScreen onFinish={finishSession} />}

      {phase === 'complete' && completeResult && (
        <>
          <CompleteScreen result={completeResult} sessions={sessions} />
          <div className="text-center mt-4">
            <button onClick={handleRestart} className="text-[11px] font-mono text-dim hover:text-ink transition-colors">
              start another drill
            </button>
          </div>
        </>
      )}
    </div>
  )
}
