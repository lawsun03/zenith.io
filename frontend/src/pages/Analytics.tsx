import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { StatsRow } from '../components/StatsRow'
import { KillzoneTable } from '../components/KillzoneTable'
import { TradesTable } from '../components/TradesTable'
import { ClaudeAdvisor } from '../components/ClaudeAdvisor'

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
const sans: CSSProperties = { fontFamily: "'Space Grotesk', sans-serif" }

interface KzStats {
  trades: number
  win_rate: number
  net_pnl: number
  avg_pnl: number
}

export interface AnalyticsPerf {
  total_trades: number
  winners?: number
  losers?: number
  win_rate: number
  net_pnl: number
  gross_profit?: number
  gross_loss?: number
  profit_factor: number | null
  expectancy: number
  avg_winner?: number
  avg_loser?: number
  max_drawdown: number
  error?: string
}

interface AnalyticsStats {
  performance: AnalyticsPerf
  killzones: Record<string, KzStats>
  recent_trades: Array<{
    ts: string
    instrument: string
    side: string
    killzone: string | null
    fill_price: number | null
    size: number | null
    realized_pnl: number | null
  }>
}

function NavLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      style={{ ...mono, fontSize: 11, letterSpacing: '0.2em', color: C.dim, textDecoration: 'none' }}
      onMouseEnter={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.ink)}
      onMouseLeave={e => ((e.currentTarget as HTMLAnchorElement).style.color = C.dim)}
    >
      {children}
    </Link>
  )
}

export function AnalyticsPage() {
  const [stats, setStats] = useState<AnalyticsStats | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchStats = () => {
    setLoading(true)
    fetch('/api/analytics/stats')
      .then(r => {
        if (!r.ok) throw new Error(`Server returned ${r.status}`)
        return r.json()
      })
      .then(d => { setStats(d); setLoading(false) })
      .catch(e => { setLoadErr(String(e)); setLoading(false) })
  }

  useEffect(() => { fetchStats() }, [])

  const perf = stats?.performance
  const tradeCount = perf && !perf.error ? perf.total_trades : null
  const subtitle = tradeCount != null
    ? `${tradeCount} TRADES · ALL TIME`
    : loading ? 'LOADING…' : 'NO DATA'

  return (
    <div style={{ minHeight: '100vh', background: C.bg, color: C.ink }}>
      {/* Nav */}
      <div style={{
        borderBottom: `1px solid ${C.bd}`, padding: '20px 24px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ ...mono, fontSize: 11, letterSpacing: '0.3em', color: C.dim }}>TOPSTEP-BOT</span>
        <div style={{ display: 'flex', gap: 24 }}>
          <NavLink to="/trade-analysis">TRADE ANALYSIS</NavLink>
          <NavLink to="/">← LIVE</NavLink>
        </div>
      </div>

      <div style={{ maxWidth: 1200, margin: '0 auto', padding: '48px 24px' }}>

        {/* Heading */}
        <div style={{ marginBottom: 48 }}>
          <div style={{ ...sans, fontSize: 60, fontWeight: 800, lineHeight: 1, letterSpacing: '-0.02em', marginBottom: 10 }}>
            PERFORMANCE<br />ANALYTICS
          </div>
          <div style={{ ...mono, fontSize: 11, color: C.dim, letterSpacing: '0.15em' }}>
            {subtitle}
          </div>
        </div>

        {loadErr && (
          <div style={{ border: `1px solid ${C.red}`, padding: '12px 16px', ...mono, fontSize: 12, color: C.red, marginBottom: 32, opacity: 0.85 }}>
            {loadErr}
          </div>
        )}

        <div style={{ display: 'flex', flexDirection: 'column', gap: 32 }}>
          <StatsRow perf={perf ?? null} />
          <KillzoneTable killzones={stats?.killzones ?? null} />
          <TradesTable trades={stats?.recent_trades ?? []} />
          <ClaudeAdvisor />
        </div>

        <div style={{ marginTop: 24, display: 'flex', justifyContent: 'flex-end' }}>
          <button
            onClick={fetchStats}
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
