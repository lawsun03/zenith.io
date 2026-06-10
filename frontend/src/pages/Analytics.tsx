import { useEffect, useState } from 'react'
import { StatsRow } from '../components/StatsRow'
import { KillzoneTable } from '../components/KillzoneTable'
import { TradesTable } from '../components/TradesTable'
import { ClaudeAdvisor } from '../components/ClaudeAdvisor'

interface KzStats {
  trades: number
  win_rate: number
  net_pnl: number
  avg_pnl: number
}

interface AnalyticsStats {
  performance: {
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
  killzones: Record<string, KzStats>
  recent_trades: Array<{
    ts: string
    side: string
    killzone: string | null
    fill_price: number | null
    size: number | null
    realized_pnl: number | null
  }>
}

export function AnalyticsPage() {
  const [stats, setStats] = useState<AnalyticsStats | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/analytics/stats')
      .then(r => r.json())
      .then(setStats)
      .catch(e => setLoadErr(String(e)))
  }, [])

  return (
    <div className="min-h-screen bg-bg">
      {/* Nav bar */}
      <header className="border-b border-border px-6 py-4 flex items-center justify-between">
        <span className="text-xs tracking-[0.4em] text-dim">TOPSTEP-BOT · ANALYTICS</span>
        <div className="flex items-center gap-4">
          <a
            href="/trade-analysis"
            className="text-dim hover:text-ink text-xs tracking-widest uppercase"
          >
            Trade Analysis
          </a>
          <a
            href="/"
            className="text-dim hover:text-ink text-xs tracking-widest uppercase"
          >
            ← Live
          </a>
        </div>
      </header>

      <main className="p-6 flex flex-col gap-6 max-w-[1400px] mx-auto">
        {loadErr && (
          <div className="border border-danger/40 bg-panel px-4 py-3 text-danger text-xs font-mono">
            Failed to load stats: {loadErr}
          </div>
        )}

        <ClaudeAdvisor />
        <StatsRow perf={stats?.performance ?? null} />
        <KillzoneTable killzones={stats?.killzones ?? null} />
        <TradesTable trades={stats?.recent_trades ?? []} />
      </main>
    </div>
  )
}
