import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'

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

const COLS = '160px 80px 110px 130px 1fr 160px'

function StatusBadge({ entry }: { entry: DateEntry }) {
  const base: CSSProperties = {
    ...mono, fontSize: 10, letterSpacing: '0.15em',
    padding: '3px 8px', border: '1px solid',
    display: 'inline-flex', alignItems: 'center',
  }
  if (entry.has_report)
    return <span style={{ ...base, borderColor: C.bdh, color: C.ink }}>REPORT READY</span>
  if (entry.has_trades || entry.has_excursions)
    return <span style={{ ...base, borderColor: C.faint, color: C.dim }}>NO REPORT</span>
  return <span style={{ ...base, borderColor: C.bd, color: C.faint }}>DATA ONLY</span>
}

function DateRow({ entry, last }: { entry: DateEntry; last: boolean }) {
  const winRate = entry.trade_count > 0
    ? Math.round((entry.win_count / entry.trade_count) * 100)
    : null
  const pnlColor = entry.net_pnl > 0 ? C.green : entry.net_pnl < 0 ? C.red : C.dim
  const wlColor = winRate !== null ? (winRate >= 50 ? C.green : C.red) : C.faint

  return (
    <a
      href={`/trade-analysis/${entry.date}`}
      style={{
        display: 'grid', gridTemplateColumns: COLS,
        padding: '18px 20px',
        borderBottom: last ? 'none' : `1px solid ${C.bd}`,
        textDecoration: 'none', color: C.ink, alignItems: 'center',
      }}
      onMouseEnter={e => ((e.currentTarget as HTMLAnchorElement).style.background = C.surf)}
      onMouseLeave={e => ((e.currentTarget as HTMLAnchorElement).style.background = 'transparent')}
    >
      <span style={{ ...mono, fontSize: 13, fontWeight: 500 }}>{entry.date}</span>
      <span style={{ ...mono, fontSize: 12, color: C.dim }}>
        {entry.trade_count > 0 ? entry.trade_count : '—'}
      </span>
      <span style={{ ...mono, fontSize: 12, color: wlColor }}>
        {winRate !== null ? `${entry.win_count}W / ${entry.loss_count}L` : '—'}
      </span>
      <span style={{ ...mono, fontSize: 13, fontWeight: 500, color: entry.trade_count > 0 ? pnlColor : C.faint }}>
        {entry.trade_count > 0
          ? `${entry.net_pnl >= 0 ? '+' : ''}$${entry.net_pnl.toFixed(2)}`
          : '—'}
      </span>
      <span />
      <span style={{ textAlign: 'right' }}><StatusBadge entry={entry} /></span>
    </a>
  )
}

export function TradeAnalysisPage() {
  const [dates, setDates] = useState<DateEntry[]>([])
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => { injectFonts() }, [])

  const fetchDates = () => {
    setLoading(true)
    fetch('/api/trade-analysis')
      .then(r => {
        if (!r.ok || !r.headers.get('content-type')?.includes('application/json'))
          throw new Error(`Server returned ${r.status} — bot may need to restart to pick up new endpoints`)
        return r.json()
      })
      .then(d => { setDates(d); setLoading(false) })
      .catch(e => { setLoadErr(String(e)); setLoading(false) })
  }

  useEffect(() => { fetchDates() }, [])

  return (
    <div style={{ minHeight: '100vh', background: C.bg, color: C.ink }}>
      {/* Nav */}
      <div style={{
        borderBottom: `1px solid ${C.bd}`, padding: '20px 24px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ ...mono, fontSize: 11, letterSpacing: '0.3em', color: C.dim }}>TOPSTEP-BOT</span>
        <div style={{ display: 'flex', gap: 24 }}>
          <a href="/analytics"
            style={{ ...mono, fontSize: 11, letterSpacing: '0.2em', color: C.dim, textDecoration: 'none' }}
            onMouseEnter={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.ink)}
            onMouseLeave={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.dim)}>
            ANALYTICS
          </a>
          <a href="/"
            style={{ ...mono, fontSize: 11, letterSpacing: '0.2em', color: C.dim, textDecoration: 'none' }}
            onMouseEnter={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.ink)}
            onMouseLeave={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.dim)}>
            ← LIVE
          </a>
        </div>
      </div>

      <div style={{ maxWidth: 880, margin: '0 auto', padding: '48px 24px' }}>
        <div style={{ marginBottom: 48 }}>
          <div style={{ ...sans, fontSize: 60, fontWeight: 800, lineHeight: 1, letterSpacing: '-0.02em', marginBottom: 10 }}>
            TRADE<br />ANALYSIS
          </div>
          <div style={{ ...mono, fontSize: 11, color: C.dim, letterSpacing: '0.15em' }}>
            PER-SESSION REPORTS · CSV EXPORTS
          </div>
        </div>

        {loadErr && (
          <div style={{ border: `1px solid ${C.red}`, padding: '12px 16px', ...mono, fontSize: 12, color: C.red, marginBottom: 24, opacity: 0.8 }}>
            {loadErr}
          </div>
        )}
        {loading && <div style={{ ...mono, fontSize: 11, color: C.faint }}>Loading…</div>}
        {!loading && dates.length === 0 && !loadErr && (
          <div style={{ border: `1px solid ${C.bd}`, padding: 32, ...mono, fontSize: 12, color: C.faint, textAlign: 'center' }}>
            No trading days found. Run the bot to generate trade data.
          </div>
        )}

        {dates.length > 0 && (
          <>
            <div style={{
              display: 'grid', gridTemplateColumns: COLS,
              padding: '0 20px 10px', borderBottom: `1px solid ${C.bd}`,
              ...mono, fontSize: 10, letterSpacing: '0.2em', color: C.faint,
            }}>
              <span>DATE</span><span>TRADES</span><span>W / L</span><span>NET P&L</span>
              <span /><span style={{ textAlign: 'right' }}>STATUS</span>
            </div>
            <div style={{ border: `1px solid ${C.bd}`, borderTop: 'none', display: 'flex', flexDirection: 'column' }}>
              {dates.map((entry, i) => (
                <DateRow key={entry.date} entry={entry} last={i === dates.length - 1} />
              ))}
            </div>
          </>
        )}

        <div style={{ marginTop: 24, display: 'flex', justifyContent: 'flex-end' }}>
          <button
            onClick={fetchDates}
            style={{ ...mono, fontSize: 10, letterSpacing: '0.2em', color: C.faint, border: `1px solid ${C.bd}`, padding: '6px 14px', background: 'none', cursor: 'pointer' }}
            onMouseEnter={e => { const b = e.currentTarget as HTMLButtonElement; b.style.color = C.ink; b.style.borderColor = C.bdh }}
            onMouseLeave={e => { const b = e.currentTarget as HTMLButtonElement; b.style.color = C.faint; b.style.borderColor = C.bd }}
          >
            ↺ REFRESH
          </button>
        </div>
      </div>
    </div>
  )
}
