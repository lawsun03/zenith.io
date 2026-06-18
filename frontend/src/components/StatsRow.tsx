import type { CSSProperties } from 'react'
import type { AnalyticsPerf } from '../pages/Analytics'

const C = {
  bg:    'rgb(var(--c-bg))',
  surf:  'rgb(var(--c-panel))',
  bd:    'rgb(var(--c-border))',
  bdh:   'rgb(var(--c-border-hi))',
  ink:   'rgb(var(--c-ink))',
  dim:   'rgb(var(--c-dim))',
  faint: 'rgb(var(--c-faint))',
  green: 'rgb(var(--c-accent))',
  red:   'rgb(var(--c-danger))',
}

const mono: CSSProperties = { fontFamily: "'JetBrains Mono', monospace" }

function StatCard({
  label, value, sub, tone = 'neutral',
}: {
  label: string
  value: string
  sub?: string
  tone?: 'green' | 'red' | 'neutral'
}) {
  const valueColor = tone === 'green' ? C.green : tone === 'red' ? C.red : C.ink
  return (
    <div style={{ background: C.surf, padding: '20px 20px 16px' }}>
      <div style={{ ...mono, fontSize: 9, letterSpacing: '0.25em', color: C.faint, textTransform: 'uppercase' as const, marginBottom: 10 }}>
        {label}
      </div>
      <div style={{ ...mono, fontSize: 22, fontWeight: 500, color: valueColor, letterSpacing: '-0.02em', lineHeight: 1 }}>
        {value}
      </div>
      {sub && (
        <div style={{ ...mono, fontSize: 10, color: C.dim, marginTop: 6 }}>{sub}</div>
      )}
    </div>
  )
}

export function StatsRow({ perf }: { perf: AnalyticsPerf | null }) {
  const gridStyle: CSSProperties = {
    display: 'grid',
    gridTemplateColumns: 'repeat(6, 1fr)',
    gap: 1,
    background: C.bd,
    border: `1px solid ${C.bd}`,
  }

  if (!perf || perf.error) {
    return (
      <div style={gridStyle}>
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} style={{ background: C.surf, height: 88 }} />
        ))}
      </div>
    )
  }

  const netTone = perf.net_pnl >= 0 ? 'green' : 'red'
  const rateTone = perf.win_rate >= 0.5 ? 'green' : 'red'
  const pfTone   = (perf.profit_factor ?? 0) >= 1 ? 'green' : 'red'
  const expTone  = perf.expectancy >= 0 ? 'green' : 'red'

  return (
    <div style={gridStyle}>
      <StatCard
        label="Net P&L"
        value={`${perf.net_pnl >= 0 ? '+' : ''}$${perf.net_pnl.toFixed(0)}`}
        sub="all time"
        tone={netTone}
      />
      <StatCard
        label="Win Rate"
        value={`${(perf.win_rate * 100).toFixed(1)}%`}
        sub={`${perf.winners ?? '?'}W / ${perf.losers ?? '?'}L`}
        tone={rateTone}
      />
      <StatCard
        label="Profit Factor"
        value={perf.profit_factor != null ? perf.profit_factor.toFixed(2) : '—'}
        sub="gross win / loss"
        tone={pfTone}
      />
      <StatCard
        label="Expectancy"
        value={`${perf.expectancy >= 0 ? '+' : ''}$${perf.expectancy.toFixed(1)}`}
        sub="per trade"
        tone={expTone}
      />
      <StatCard
        label="Avg Winner"
        value={perf.avg_winner != null ? `+$${perf.avg_winner.toFixed(0)}` : '—'}
        sub={`${perf.winners ?? 0} wins`}
        tone="green"
      />
      <StatCard
        label="Avg Loser"
        value={perf.avg_loser != null ? `-$${Math.abs(perf.avg_loser).toFixed(0)}` : '—'}
        sub={`${perf.losers ?? 0} losses`}
        tone="red"
      />
    </div>
  )
}
