import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Logo } from './Logo'

interface Finding {
  ts: string
  session: string
  item: string
  variant: string
  objective: 'combine' | 'funded' | 'dataset' | 'infra'
  metrics: Record<string, unknown>
  verdict: 'candidate' | 'rejected' | 'dataset' | 'shipped'
  learned: string
  links: { doc?: string | null; runs?: string[]; src?: string | null }
}

interface ResearchState {
  findings: Finding[]
  backlog_md: string
  journal_md: string
  lessons_md: string
  ledger: string
  loop_log_tail: string
  stopped: boolean
}

const VERDICT_STYLE: Record<string, string> = {
  candidate: 'text-accent-ink bg-accent/15',
  shipped: 'text-accent-ink bg-accent/15',
  rejected: 'text-danger bg-danger/15',
  dataset: 'text-warn bg-warn/10',
  infra: 'text-dim bg-white/5',
}

function fmtMetrics(m: Record<string, unknown>): string {
  return Object.entries(m)
    .map(([k, v]) => `${k}=${v}`)
    .join('  ')
}

function MarkdownBlock({ text }: { text: string }) {
  // Deliberately plain: monospace pre keeps the terminal aesthetic and avoids
  // a markdown dependency. The files are written to be readable raw.
  return (
    <pre className="text-[11px] font-mono text-dim whitespace-pre-wrap leading-relaxed">
      {text || '(empty)'}
    </pre>
  )
}

export function ResearchPage() {
  const [state, setState] = useState<ResearchState | null>(null)
  const [tab, setTab] = useState<'all' | 'combine' | 'funded'>('all')
  const [panel, setPanel] = useState<'journal' | 'backlog' | 'lessons' | 'loop'>('journal')

  useEffect(() => {
    const load = () =>
      fetch('/api/research/state')
        .then(r => r.json())
        .then(setState)
        .catch(() => {})
    load()
    const id = setInterval(load, 30_000) // sessions append every ~20-60 min
    return () => clearInterval(id)
  }, [])

  const findings = useMemo(() => {
    const all = [...(state?.findings ?? [])].reverse() // newest first
    if (tab === 'all') return all
    return all.filter(f => f.objective === tab)
  }, [state?.findings, tab])

  const sessions = state?.findings?.length ?? 0
  const spent = state?.ledger?.match(/TOTAL SPENT: \$([\d.]+)/)?.[1] ?? '0.00'

  return (
    <div className="min-h-screen px-5 py-4 max-w-[1100px] mx-auto">
      {/* header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <Logo />
          <span className="text-sm text-ink font-medium">Research</span>
          <span className="text-[10px] font-mono text-faint">autonomous loop</span>
        </div>
        <div className="flex items-center gap-4 text-[11px] font-mono">
          <span className="text-faint">
            findings <span className="text-ink tabular-nums">{sessions}</span>
          </span>
          <span className="text-faint">
            databento <span className="text-ink tabular-nums">${spent}</span>/<span className="text-dim">$20</span>
          </span>
          <span className={state?.stopped ? 'text-danger' : 'text-accent-ink'}>
            {state?.stopped ? 'LOOP STOPPED' : 'LOOP ARMED'}
          </span>
          <Link to="/backtests" className="text-dim hover:text-ink transition-colors">backtests</Link>
          <Link to="/trainer" className="text-dim hover:text-ink transition-colors">trainer</Link>
          <Link to="/" className="text-dim hover:text-ink transition-colors">dashboard</Link>
        </div>
      </div>

      {/* findings leaderboard */}
      <div className="bg-panel-hi backdrop-blur-md border border-border rounded-[10px] overflow-hidden mb-4 animate-fade-up">
        <div className="flex items-center justify-between px-[18px] py-2.5 border-b border-border">
          <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Findings</span>
          <div className="flex gap-0.5">
            {(['all', 'combine', 'funded'] as const).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`text-[10px] font-mono px-2 py-0.5 rounded transition-colors ${tab === t ? 'text-accent-ink bg-accent/15' : 'text-faint hover:text-dim'}`}
              >{t}</button>
            ))}
          </div>
        </div>
        <div className="max-h-[44vh] overflow-y-auto divide-y divide-border">
          {findings.map((f, i) => (
            <div key={i} className="px-[18px] py-2.5 text-[11px] font-mono">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2 min-w-0">
                  <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold shrink-0 ${VERDICT_STYLE[f.verdict] ?? 'text-dim'}`}>
                    {f.verdict.toUpperCase()}
                  </span>
                  <span className="text-faint shrink-0">{f.item}</span>
                  <span className="text-ink truncate">{f.variant}</span>
                </div>
                <span className="text-faint shrink-0">{f.ts.slice(5, 16).replace('T', ' ')}</span>
              </div>
              <div className="text-dim mt-1 tabular-nums">{fmtMetrics(f.metrics)}</div>
              <div className="text-dim mt-1 font-sans text-[12px] leading-snug">{f.learned}</div>
              {(f.links?.runs?.length || f.links?.src) ? (
                <div className="mt-1 flex gap-3">
                  {f.links.runs?.map(r => (
                    <Link key={r} to="/backtests" className="text-accent hover:text-accent-ink">run:{r}</Link>
                  ))}
                  {f.links.src && (
                    <a href={f.links.src} target="_blank" rel="noreferrer" className="text-faint hover:text-dim">source</a>
                  )}
                </div>
              ) : null}
            </div>
          ))}
          {findings.length === 0 && (
            <div className="px-[18px] py-6 text-center text-[11px] font-mono text-faint">
              no findings yet — the loop writes here after each session
            </div>
          )}
        </div>
      </div>

      {/* journal / backlog / lessons / loop */}
      <div className="bg-panel-hi backdrop-blur-md border border-border rounded-[10px] overflow-hidden animate-fade-up">
        <div className="flex items-center gap-0.5 px-[18px] py-2.5 border-b border-border">
          {(['journal', 'backlog', 'lessons', 'loop'] as const).map(p => (
            <button
              key={p}
              onClick={() => setPanel(p)}
              className={`text-[10px] font-mono px-2 py-0.5 rounded transition-colors ${panel === p ? 'text-accent-ink bg-accent/15' : 'text-faint hover:text-dim'}`}
            >{p}</button>
          ))}
        </div>
        <div className="px-[18px] py-3 max-h-[40vh] overflow-y-auto">
          {panel === 'journal' && <MarkdownBlock text={state?.journal_md ?? ''} />}
          {panel === 'backlog' && <MarkdownBlock text={state?.backlog_md ?? ''} />}
          {panel === 'lessons' && <MarkdownBlock text={state?.lessons_md ?? ''} />}
          {panel === 'loop' && (
            <MarkdownBlock text={(state?.ledger ?? '') + '\n\n--- loop.log tail ---\n' + (state?.loop_log_tail ?? '')} />
          )}
        </div>
      </div>
    </div>
  )
}
