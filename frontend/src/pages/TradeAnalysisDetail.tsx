import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { Logo } from '../components/Logo'

const FONTS_ID = 'ta-grotesk-fonts'
function injectFonts() {
  if (document.getElementById(FONTS_ID)) return
  const link = document.createElement('link')
  link.id = FONTS_ID
  link.rel = 'stylesheet'
  link.href = 'https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@700;800&family=JetBrains+Mono:wght@400;500&display=swap'
  document.head.appendChild(link)
}

// Theme palette — mirrors tailwind.config.js blue tones
const C = {
  bg:      '#070c1a',
  surf:    '#0d1628',
  bd:      '#1d2a42',
  bdh:     '#2c3e5c',
  ink:     '#e8f0ff',
  dim:     '#6a85b0',
  faint:   '#3d5070',
  green:   '#6ee7b7',
  red:     '#fca5a5',
}

interface DateEntry {
  date: string
  has_trades: boolean
  has_excursions: boolean
  has_rejections: boolean
  has_report: boolean
  trade_count: number
  win_count: number
  loss_count: number
  net_pnl: number
}

const mono: CSSProperties = { fontFamily: "'JetBrains Mono', monospace" }
const sans: CSSProperties = { fontFamily: "'Space Grotesk', sans-serif" }

// ── Markdown renderer ────────────────────────────────────────────────────────

function renderInline(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = []
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g
  let last = 0, i = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index))
    const raw = m[0]
    if (raw.startsWith('**')) {
      parts.push(
        <strong key={i++} style={{ color: C.ink, fontWeight: 600 }}>
          {raw.slice(2, -2)}
        </strong>
      )
    } else if (raw.startsWith('`')) {
      parts.push(
        <code key={i++} style={{ ...mono, color: C.ink, background: C.surf, padding: '1px 4px', fontSize: '0.9em', borderRadius: 2 }}>
          {raw.slice(1, -1)}
        </code>
      )
    }
    last = m.index + raw.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

function MarkdownTable({ lines }: { lines: string[] }) {
  const rows = lines.filter(l => !l.match(/^\|[-| :]+\|?\s*$/))
  const parsed = rows.map(r =>
    r.split('|').filter((_, i, arr) => i > 0 && i < arr.length - 1).map(c => c.trim())
  )
  if (parsed.length === 0) return null
  const [head, ...body] = parsed
  return (
    <div style={{ overflowX: 'auto', margin: '16px 0' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', ...mono, fontSize: 11 }}>
        <thead>
          <tr>
            {head.map((cell, i) => (
              <th key={i} style={{
                textAlign: 'left', padding: '6px 10px', border: `1px solid ${C.bd}`,
                color: C.faint, fontWeight: 500, letterSpacing: '0.1em', fontSize: 10,
              }}>{cell}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {body.map((row, ri) => (
            <tr key={ri}
              onMouseEnter={e => { Array.from((e.currentTarget as HTMLTableRowElement).cells).forEach(td => { td.style.background = C.surf }) }}
              onMouseLeave={e => { Array.from((e.currentTarget as HTMLTableRowElement).cells).forEach(td => { td.style.background = 'transparent' }) }}
            >
              {row.map((cell, ci) => (
                <td key={ci} style={{ padding: '6px 10px', border: `1px solid ${C.bd}`, color: C.dim, verticalAlign: 'top' }}>
                  {renderInline(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function MarkdownDoc({ content }: { content: string }) {
  const lines = content.split('\n')
  const nodes: React.ReactNode[] = []
  let i = 0, key = 0

  while (i < lines.length) {
    const line = lines[i]

    if (line.startsWith('# ')) {
      nodes.push(
        <h1 key={key++} style={{ ...sans, fontSize: 26, fontWeight: 700, color: C.ink, marginTop: 24, marginBottom: 14, paddingBottom: 8, borderBottom: `1px solid ${C.bd}` }}>
          {line.slice(2)}
        </h1>
      )
      i++; continue
    }
    if (line.startsWith('## ')) {
      nodes.push(
        <h2 key={key++} style={{ ...mono, fontSize: 11, color: C.dim, letterSpacing: '0.15em', textTransform: 'uppercase', marginTop: 28, marginBottom: 8, paddingBottom: 6, borderBottom: `1px solid ${C.bd}` }}>
          {line.slice(3)}
        </h2>
      )
      i++; continue
    }
    if (line.startsWith('### ')) {
      nodes.push(
        <h3 key={key++} style={{ ...mono, fontSize: 11, color: C.faint, letterSpacing: '0.1em', textTransform: 'uppercase', marginTop: 18, marginBottom: 4 }}>
          {line.slice(4)}
        </h3>
      )
      i++; continue
    }
    if (line.match(/^---+\s*$/)) {
      nodes.push(<hr key={key++} style={{ border: 'none', borderTop: `1px solid ${C.bd}`, margin: '20px 0' }} />)
      i++; continue
    }
    if (line.startsWith('|')) {
      const tableLines: string[] = []
      while (i < lines.length && lines[i].startsWith('|')) { tableLines.push(lines[i]); i++ }
      nodes.push(<MarkdownTable key={key++} lines={tableLines} />)
      continue
    }
    if (line.match(/^[-*] /)) {
      const items: string[] = []
      while (i < lines.length && lines[i].match(/^[-*] /)) { items.push(lines[i].slice(2)); i++ }
      nodes.push(
        <ul key={key++} style={{ margin: '10px 0', paddingLeft: 0, listStyle: 'none' }}>
          {items.map((item, li) => (
            <li key={li} style={{ ...mono, fontSize: 12, color: C.dim, lineHeight: 1.7, display: 'flex', gap: 8, marginBottom: 2 }}>
              <span style={{ color: C.faint, flexShrink: 0 }}>·</span>
              <span>{renderInline(item)}</span>
            </li>
          ))}
        </ul>
      )
      continue
    }
    if (line.match(/^\d+\. /)) {
      const items: string[] = []
      while (i < lines.length && lines[i].match(/^\d+\. /)) { items.push(lines[i].replace(/^\d+\. /, '')); i++ }
      nodes.push(
        <ol key={key++} style={{ margin: '10px 0', paddingLeft: 0, listStyle: 'none' }}>
          {items.map((item, li) => (
            <li key={li} style={{ ...mono, fontSize: 12, color: C.dim, lineHeight: 1.7, display: 'flex', gap: 10, marginBottom: 4 }}>
              <span style={{ color: C.faint, flexShrink: 0, width: 16, textAlign: 'right' }}>{li + 1}.</span>
              <span>{renderInline(item)}</span>
            </li>
          ))}
        </ol>
      )
      continue
    }
    if (line.trim() === '') { i++; continue }
    nodes.push(
      <p key={key++} style={{ ...mono, fontSize: 12, color: C.dim, lineHeight: 1.8, marginBottom: 10 }}>
        {renderInline(line)}
      </p>
    )
    i++
  }

  return <div>{nodes}</div>
}

// ── Analysis runner ──────────────────────────────────────────────────────────

function AnalysisRunner({ date, onDone }: { date: string; onDone: () => void }) {
  const [running, setRunning] = useState(false)
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [text])

  const run = async () => {
    setRunning(true); setText(''); setError(null); setDone(false)
    const ctrl = new AbortController()
    abortRef.current = ctrl
    try {
      const resp = await fetch(`/api/trade-analysis/${date}/run`, { method: 'POST', signal: ctrl.signal })
      if (!resp.ok || !resp.body) { setError(`HTTP ${resp.status}`); setRunning(false); return }
      const reader = resp.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done: rdone, value } = await reader.read()
        if (rdone) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n'); buf = lines.pop() ?? ''
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const ev = JSON.parse(line.slice(6))
            if (ev.type === 'text_delta') setText(p => p + ev.delta)
            if (ev.type === 'error') setError(ev.message)
            if (ev.type === 'done') { setDone(true); onDone() }
          } catch { /* skip malformed */ }
        }
      }
    } catch (e: unknown) {
      if (e instanceof Error && e.name !== 'AbortError') setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  if (!running && !done) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, padding: '56px 0' }}>
        <div style={{ ...mono, fontSize: 10, color: C.faint, letterSpacing: '0.25em' }}>
          NO REPORT GENERATED FOR THIS DATE
        </div>
        <button
          onClick={run}
          style={{
            ...mono, fontSize: 11, letterSpacing: '0.2em', fontWeight: 700,
            background: C.ink, color: C.bg, border: 'none',
            padding: '10px 24px', cursor: 'pointer',
          }}
          onMouseEnter={e => ((e.currentTarget as HTMLButtonElement).style.opacity = '0.85')}
          onMouseLeave={e => ((e.currentTarget as HTMLButtonElement).style.opacity = '1')}
        >
          ▶ RUN ANALYSIS
        </button>
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {running && (
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', paddingBottom: 10, borderBottom: `1px solid ${C.bd}` }}>
          <span style={{ ...mono, fontSize: 10, color: C.green, letterSpacing: '0.15em' }}>
            ● GENERATING…
          </span>
          <button
            onClick={() => abortRef.current?.abort()}
            style={{ ...mono, fontSize: 10, color: C.faint, border: `1px solid ${C.bd}`, padding: '3px 10px', background: 'none', cursor: 'pointer' }}
            onMouseEnter={e => ((e.currentTarget as HTMLButtonElement).style.color = C.red)}
            onMouseLeave={e => ((e.currentTarget as HTMLButtonElement).style.color = C.faint)}
          >
            CANCEL
          </button>
        </div>
      )}
      {error && (
        <div style={{ border: `1px solid ${C.red}`, padding: '12px 16px', ...mono, fontSize: 12, color: C.red, opacity: 0.85 }}>
          {error}
        </div>
      )}
      {text && (
        <div ref={scrollRef} style={{ overflowY: 'auto', maxHeight: '70vh' }}>
          <MarkdownDoc content={text} />
        </div>
      )}
    </div>
  )
}

// ── Detail page ──────────────────────────────────────────────────────────────

export function TradeAnalysisDetailPage({ date }: { date: string }) {
  const [entry, setEntry] = useState<DateEntry | null>(null)
  const [report, setReport] = useState<string | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [hasReport, setHasReport] = useState<boolean | null>(null)

  useEffect(() => { injectFonts() }, [])

  useEffect(() => {
    fetch('/api/trade-analysis')
      .then(r => r.json())
      .then((list: DateEntry[]) => {
        const found = list.find(d => d.date === date)
        if (!found) {
          setEntry({ date, has_trades: false, has_excursions: false, has_rejections: false, has_report: false, trade_count: 0, win_count: 0, loss_count: 0, net_pnl: 0 })
          setHasReport(false)
        } else {
          setEntry(found)
          setHasReport(found.has_report)
        }
      })
      .catch(e => setLoadErr(String(e)))
  }, [date])

  useEffect(() => {
    if (hasReport) {
      fetch(`/api/trade-analysis/${date}/report`)
        .then(r => r.json())
        .then(d => { if (d.error) setLoadErr(d.error); else setReport(d.content) })
        .catch(e => setLoadErr(String(e)))
    }
  }, [date, hasReport])

  const handleDone = () => {
    setHasReport(true)
    if (entry) setEntry({ ...entry, has_report: true })
    fetch(`/api/trade-analysis/${date}/report`)
      .then(r => r.json())
      .then(d => { if (!d.error) setReport(d.content) })
      .catch(() => {})
  }

  const winRate = entry && entry.trade_count > 0
    ? Math.round((entry.win_count / entry.trade_count) * 100)
    : null
  const pnlPos = entry && entry.net_pnl > 0
  const pnlNeg = entry && entry.net_pnl < 0

  return (
    <div style={{ minHeight: '100vh', background: C.bg, color: C.ink }}>
      {/* Sticky nav */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 10, background: C.bg,
        borderBottom: `1px solid ${C.bd}`, padding: '16px 24px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <Link to="/trade-analysis"
          style={{ ...mono, fontSize: 11, letterSpacing: '0.2em', color: C.dim, textDecoration: 'none' }}
          onMouseEnter={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.ink)}
          onMouseLeave={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.dim)}>
          ← TRADE ANALYSIS
        </Link>
        <Link to="/" style={{ display: 'flex', alignItems: 'center', gap: 10, textDecoration: 'none' }}>
          <Logo size={16} />
          <span style={{ ...mono, fontSize: 10, letterSpacing: '0.3em', color: C.faint }}>ZENITH</span>
        </Link>
      </div>

      <div style={{ maxWidth: 800, margin: '0 auto', padding: '56px 24px' }}>
        {loadErr && (
          <div style={{ border: `1px solid ${C.red}`, padding: '12px 16px', ...mono, fontSize: 12, color: C.red, marginBottom: 32, opacity: 0.85 }}>
            {loadErr}
          </div>
        )}

        {/* Date heading */}
        <div style={{ ...sans, fontSize: 72, fontWeight: 800, lineHeight: 1, letterSpacing: '-0.03em', marginBottom: 20 }}>
          {date.slice(0, 4)}<br />{date.slice(5)}
        </div>

        {/* Rule */}
        <div style={{ width: '100%', height: 2, background: C.bdh, marginBottom: 28 }} />

        {/* Stats */}
        {entry && entry.trade_count > 0 && (
          <div style={{ display: 'flex', gap: 40, flexWrap: 'wrap', marginBottom: 32 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ ...mono, fontSize: 10, letterSpacing: '0.2em', color: C.faint }}>TRADES</span>
              <span style={{ ...mono, fontSize: 16 }}>{entry.trade_count}</span>
            </div>
            {winRate !== null && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ ...mono, fontSize: 10, letterSpacing: '0.2em', color: C.faint }}>WIN RATE</span>
                <span style={{ ...mono, fontSize: 16, color: winRate >= 50 ? C.green : C.red }}>{winRate}%</span>
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ ...mono, fontSize: 10, letterSpacing: '0.2em', color: C.faint }}>NET P&L</span>
              <span style={{ ...mono, fontSize: 16, color: pnlPos ? C.green : pnlNeg ? C.red : C.dim }}>
                {entry.net_pnl >= 0 ? '+' : ''}${entry.net_pnl.toFixed(2)}
              </span>
            </div>
            {winRate !== null && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <span style={{ ...mono, fontSize: 10, letterSpacing: '0.2em', color: C.faint }}>W / L</span>
                <span style={{ ...mono, fontSize: 16 }}>{entry.win_count} / {entry.loss_count}</span>
              </div>
            )}
          </div>
        )}

        {/* CSV downloads */}
        {entry && (entry.has_trades || entry.has_excursions || entry.has_rejections) && (
          <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 40 }}>
            {entry.has_trades && (
              <a href={`/api/trade-analysis/${date}/csv/trades`} download
                style={{ ...mono, fontSize: 11, letterSpacing: '0.12em', color: C.faint, textDecoration: 'none', border: `1px solid ${C.bd}`, padding: '6px 12px' }}
                onMouseEnter={e => { const a = e.currentTarget as HTMLAnchorElement; a.style.color = C.ink; a.style.borderColor = C.bdh }}
                onMouseLeave={e => { const a = e.currentTarget as HTMLAnchorElement; a.style.color = C.faint; a.style.borderColor = C.bd }}>
                ↓ TRADES.CSV
              </a>
            )}
            {entry.has_excursions && (
              <a href={`/api/trade-analysis/${date}/csv/excursions`} download
                style={{ ...mono, fontSize: 11, letterSpacing: '0.12em', color: C.faint, textDecoration: 'none', border: `1px solid ${C.bd}`, padding: '6px 12px' }}
                onMouseEnter={e => { const a = e.currentTarget as HTMLAnchorElement; a.style.color = C.ink; a.style.borderColor = C.bdh }}
                onMouseLeave={e => { const a = e.currentTarget as HTMLAnchorElement; a.style.color = C.faint; a.style.borderColor = C.bd }}>
                ↓ EXCURSIONS.CSV
              </a>
            )}
            {entry.has_rejections && (
              <a href={`/api/trade-analysis/${date}/csv/rejections`} download
                style={{ ...mono, fontSize: 11, letterSpacing: '0.12em', color: C.faint, textDecoration: 'none', border: `1px solid ${C.bd}`, padding: '6px 12px' }}
                onMouseEnter={e => { const a = e.currentTarget as HTMLAnchorElement; a.style.color = C.ink; a.style.borderColor = C.bdh }}
                onMouseLeave={e => { const a = e.currentTarget as HTMLAnchorElement; a.style.color = C.faint; a.style.borderColor = C.bd }}>
                ↓ REJECTIONS.CSV
              </a>
            )}
          </div>
        )}

        {/* Report area */}
        <div style={{ borderTop: `1px solid ${C.bd}`, paddingTop: 32 }}>
          {hasReport === null && <div style={{ ...mono, fontSize: 11, color: C.faint }}>Loading…</div>}
          {hasReport === false && entry && <AnalysisRunner date={date} onDone={handleDone} />}
          {hasReport === true && report === null && <div style={{ ...mono, fontSize: 11, color: C.faint }}>Loading report…</div>}
          {report && <MarkdownDoc content={report} />}
        </div>
      </div>
    </div>
  )
}
