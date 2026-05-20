interface PerformanceStats {
  total_trades: number
  winners?: number
  losers?: number
  win_rate: number
  net_pnl: number
  profit_factor: number | null
  expectancy: number
  max_drawdown: number
  error?: string
}

interface Props {
  perf: PerformanceStats | null
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-panel border border-border px-4 py-3">
      <div className="text-[9px] tracking-[0.25em] text-dim uppercase mb-1">{label}</div>
      <div className="text-xl font-mono text-ink tabular-nums">{value}</div>
      {sub && <div className="text-[9px] text-dim mt-0.5">{sub}</div>}
    </div>
  )
}

export function StatsRow({ perf }: Props) {
  if (!perf || perf.error) {
    return (
      <div className="grid grid-cols-5 gap-px bg-border border border-border">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="bg-panel px-4 py-3 h-16 animate-pulse" />
        ))}
      </div>
    )
  }

  return (
    <div className="grid grid-cols-5 gap-px bg-border border border-border">
      <StatCard
        label="Net P&L"
        value={`${perf.net_pnl >= 0 ? '+' : ''}$${perf.net_pnl.toFixed(0)}`}
        sub="all sessions"
      />
      <StatCard
        label="Win Rate"
        value={`${(perf.win_rate * 100).toFixed(1)}%`}
        sub={`${perf.winners ?? '?'} / ${perf.total_trades} trades`}
      />
      <StatCard
        label="Profit Factor"
        value={perf.profit_factor != null ? perf.profit_factor.toFixed(2) : '—'}
        sub="gross win / loss"
      />
      <StatCard
        label="Expectancy"
        value={`${perf.expectancy >= 0 ? '+' : ''}$${perf.expectancy.toFixed(1)}`}
        sub="per trade"
      />
      <StatCard
        label="Max Drawdown"
        value={`$${perf.max_drawdown.toFixed(0)}`}
        sub="from peak"
      />
    </div>
  )
}
