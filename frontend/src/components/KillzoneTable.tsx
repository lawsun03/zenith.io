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

const KZ_LABEL: Record<string, string> = {
  london:    'LONDON',
  ny_am:     'NY AM',
  ny_pm:     'NY PM',
  london_ny: 'LONDON/NY',
  asia:      'ASIA',
  unknown:   'UNTAGGED',
}

interface KzStats {
  trades: number
  win_rate: number
  net_pnl: number
  avg_pnl: number
}

export function KillzoneTable({ killzones }: { killzones: Record<string, KzStats> | null }) {
  const sectionHeader = (
    <div style={{ marginBottom: 16 }}>
      <div style={{ ...mono, fontSize: 10, letterSpacing: '0.3em', color: C.faint, textTransform: 'uppercase' as const }}>
        BY KILLZONE
      </div>
    </div>
  )

  if (!killzones) {
    return (
      <div>
        {sectionHeader}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 1, background: C.bd, border: `1px solid ${C.bd}` }}>
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} style={{ background: C.surf, height: 80 }} />
          ))}
        </div>
      </div>
    )
  }

  const entries = Object.entries(killzones).filter(([, v]) => v.trades > 0)

  if (entries.length === 0 || (entries.length === 1 && entries[0][0] === 'unknown')) {
    return (
      <div>
        {sectionHeader}
        <div style={{ border: `1px solid ${C.bd}`, padding: '20px', ...mono, fontSize: 11, color: C.faint }}>
          Killzone tagging begins with current session data — older trades are untagged.
        </div>
      </div>
    )
  }

  return (
    <div>
      {sectionHeader}
      <div style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${entries.length}, 1fr)`,
        gap: 1,
        background: C.bd,
        border: `1px solid ${C.bd}`,
      }}>
        {entries.map(([kz, stats]) => {
          const winPct = (stats.win_rate * 100).toFixed(1)
          const pnlColor = stats.net_pnl >= 0 ? C.green : C.red
          const rateColor = stats.win_rate >= 0.55 ? C.green : stats.win_rate >= 0.45 ? C.ink : C.red
          return (
            <div key={kz} style={{ background: C.surf, padding: '20px', textAlign: 'center' as const }}>
              <div style={{ ...mono, fontSize: 9, letterSpacing: '0.25em', color: C.faint, textTransform: 'uppercase' as const, marginBottom: 10 }}>
                {KZ_LABEL[kz] ?? kz.toUpperCase()}
              </div>
              <div style={{ ...mono, fontSize: 24, fontWeight: 500, color: rateColor, letterSpacing: '-0.02em', lineHeight: 1, marginBottom: 6 }}>
                {winPct}%
              </div>
              <div style={{ ...mono, fontSize: 12, color: pnlColor, marginBottom: 4 }}>
                {stats.net_pnl >= 0 ? '+' : ''}${stats.net_pnl.toFixed(0)}
              </div>
              <div style={{ ...mono, fontSize: 10, color: C.faint }}>
                {stats.trades} trades
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
