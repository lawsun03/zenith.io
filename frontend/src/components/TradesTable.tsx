import type { CSSProperties } from 'react'

const C = {
  bg:    '#070c1a',
  surf:  '#0d1628',
  bd:    '#1d2a42',
  bdh:   '#2c3e5c',
  ink:   '#e8f0ff',
  dim:   '#6a85b0',
  faint: '#3d5070',
  green: '#6ee7b7',
  red:   '#fca5a5',
}

const mono: CSSProperties = { fontFamily: "'JetBrains Mono', monospace" }

const INSTR_COLOR: Record<string, string> = {
  MGC: '#fbbf24',
  MNQ: '#60a5fa',
  MES: '#a78bfa',
}

const PT = 'America/Los_Angeles'

function fmtDate(ts: string) {
  try {
    return new Date(ts).toLocaleDateString('en-US', { timeZone: PT, month: '2-digit', day: '2-digit' })
  } catch { return ts.slice(0, 10) }
}

function fmtTime(ts: string) {
  try {
    return new Date(ts).toLocaleTimeString('en-US', { timeZone: PT, hour: 'numeric', minute: '2-digit', hour12: true })
  } catch { return ts.slice(11, 16) }
}

const COLS = '70px 90px 60px 55px 80px 55px 90px'

interface Trade {
  ts: string
  instrument: string
  side: string
  killzone: string | null
  fill_price: number | null
  size: number | null
  realized_pnl: number | null
}

function TradeRow({ trade, last }: { trade: Trade; last: boolean }) {
  const pnl = trade.realized_pnl ?? 0
  const pnlColor = pnl > 0 ? C.green : pnl < 0 ? C.red : C.dim
  const sideColor = trade.side === 'long' ? C.green : C.red
  const instrColor = INSTR_COLOR[trade.instrument] ?? C.dim

  return (
    <div
      style={{
        display: 'grid', gridTemplateColumns: COLS,
        padding: '13px 20px',
        borderBottom: last ? 'none' : `1px solid ${C.bd}`,
        alignItems: 'center',
      }}
      onMouseEnter={e => ((e.currentTarget as HTMLDivElement).style.background = C.surf)}
      onMouseLeave={e => ((e.currentTarget as HTMLDivElement).style.background = 'transparent')}
    >
      <span style={{ ...mono, fontSize: 11, color: C.dim }}>{fmtDate(trade.ts)}</span>
      <span style={{ ...mono, fontSize: 11, color: C.dim }}>{fmtTime(trade.ts)}</span>
      <span style={{ ...mono, fontSize: 11, fontWeight: 500, color: instrColor }}>{trade.instrument || '—'}</span>
      <span style={{ ...mono, fontSize: 11, textTransform: 'uppercase' as const, color: sideColor }}>{trade.side}</span>
      <span style={{ ...mono, fontSize: 11, color: C.dim }}>{trade.killzone?.toUpperCase() ?? '—'}</span>
      <span style={{ ...mono, fontSize: 11, color: C.dim, textAlign: 'right' as const }}>{trade.size ?? '—'}</span>
      <span style={{ ...mono, fontSize: 12, fontWeight: 500, color: pnlColor, textAlign: 'right' as const }}>
        {pnl !== 0 ? `${pnl > 0 ? '+' : ''}$${pnl.toFixed(0)}` : '—'}
      </span>
    </div>
  )
}

export function TradesTable({ trades }: { trades: Trade[] }) {
  const sectionHeader = (
    <div style={{ marginBottom: 16 }}>
      <div style={{ ...mono, fontSize: 10, letterSpacing: '0.3em', color: C.faint, textTransform: 'uppercase' as const }}>
        RECENT TRADES
      </div>
    </div>
  )

  if (trades.length === 0) {
    return (
      <div>
        {sectionHeader}
        <div style={{ border: `1px solid ${C.bd}`, padding: '20px', ...mono, fontSize: 11, color: C.faint }}>
          No closed trades recorded yet.
        </div>
      </div>
    )
  }

  const visible = trades.slice(0, 50)

  return (
    <div>
      {sectionHeader}
      <div style={{ border: `1px solid ${C.bd}` }}>
        {/* Header row */}
        <div style={{
          display: 'grid', gridTemplateColumns: COLS,
          padding: '0 20px 10px', borderBottom: `1px solid ${C.bd}`,
          ...mono, fontSize: 9, letterSpacing: '0.2em', color: C.faint,
          textTransform: 'uppercase' as const,
        }}>
          <span>DATE</span>
          <span>TIME</span>
          <span>INSTR</span>
          <span>SIDE</span>
          <span>ZONE</span>
          <span style={{ textAlign: 'right' as const }}>SIZE</span>
          <span style={{ textAlign: 'right' as const }}>P&amp;L</span>
        </div>
        {visible.map((t, i) => (
          <TradeRow key={i} trade={t} last={i === visible.length - 1} />
        ))}
      </div>
    </div>
  )
}
