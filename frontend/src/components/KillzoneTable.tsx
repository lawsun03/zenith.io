interface KzStats {
  trades: number
  win_rate: number
  net_pnl: number
  avg_pnl: number
}

interface Props {
  killzones: Record<string, KzStats> | null
}

const KZ_LABEL: Record<string, string> = {
  london:    'London',
  ny_am:     'NY AM',
  ny_pm:     'NY PM',
  london_ny: 'London/NY',
  asia:      'Asia',
  unknown:   'Unknown',
}

export function KillzoneTable({ killzones }: Props) {
  if (!killzones) {
    return (
      <div className="bg-panel border border-border px-4 py-3 h-24 animate-pulse" />
    )
  }

  const entries = Object.entries(killzones).filter(([, v]) => v.trades > 0)

  if (entries.length === 0) {
    return (
      <div className="bg-panel border border-border px-4 py-3">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">By Killzone</span>
        <p className="text-dim text-xs mt-2">No trade data yet.</p>
      </div>
    )
  }

  return (
    <div className="bg-panel border border-border">
      <div className="px-4 py-2 border-b border-border">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">By Killzone</span>
      </div>
      <div className="grid gap-px bg-border" style={{ gridTemplateColumns: `repeat(${entries.length}, 1fr)` }}>
        {entries.map(([kz, stats]) => {
          const winPct = (stats.win_rate * 100).toFixed(1)
          const pnlColor = stats.net_pnl >= 0 ? 'text-accent' : 'text-danger'
          const rateColor = stats.win_rate >= 0.55 ? 'text-accent' : stats.win_rate >= 0.45 ? 'text-ink' : 'text-warn'
          return (
            <div key={kz} className="bg-panel px-4 py-3 text-center">
              <div className="text-[9px] tracking-[0.2em] text-dim uppercase mb-1">
                {KZ_LABEL[kz] ?? kz}
              </div>
              <div className={`text-lg font-mono tabular-nums ${rateColor}`}>{winPct}%</div>
              <div className={`text-xs font-mono tabular-nums ${pnlColor} mt-0.5`}>
                {stats.net_pnl >= 0 ? '+' : ''}${stats.net_pnl.toFixed(0)}
              </div>
              <div className="text-[9px] text-dim mt-0.5">{stats.trades} trades</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
