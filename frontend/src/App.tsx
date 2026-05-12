import { useRef, useState } from 'react'
import { useStream } from './hooks/useStream'
import type { ChartCallbacks } from './hooks/useStream'
import { useConfig } from './hooks/useConfig'
import { useKillzone } from './hooks/useKillzone'
import { Header } from './components/Header'
import { LockoutBanner } from './components/LockoutBanner'
import { MetricsGrid } from './components/MetricsGrid'
import { BarChart } from './components/BarChart'
import { FeedSection } from './components/FeedSection'
import { SignalRow } from './components/SignalRow'
import { FillRow } from './components/FillRow'
import { ReconcileRow } from './components/ReconcileRow'
import { ConfigPanel } from './components/ConfigPanel'
import { BacktestsPage } from './components/BacktestsPage'
import { MatrixRain } from './components/MatrixRain'
import type { JournalItem } from './types'

export default function App() {
  // Simple path-based router. /backtests opens the standalone page;
  // anything else is the live dashboard. Open in a new tab to compare.
  const path = window.location.pathname
  if (path.startsWith('/backtests')) return <BacktestsPage />

  const chartCbRef = useRef<ChartCallbacks>({})
  const { status, signals, fills, reconciles, connState } = useStream(chartCbRef)
  const { config, saveConfig, saving, saveError } = useConfig()
  const [configOpen, setConfigOpen] = useState(false)
  const activeKillzone = useKillzone(config?.enabled_killzones)

  return (
    <div className="min-h-screen scanlines">
      <MatrixRain />
      {activeKillzone && <div className="h-[2px] bg-accent/50" />}
      <Header status={status} connState={connState} onConfigOpen={() => setConfigOpen(true)} mode={config?.mode} activeKillzone={activeKillzone} />
      <LockoutBanner lockout={status?.lockout ?? null} />
      <main className="p-6 flex flex-col gap-6 max-w-[1400px] mx-auto">
        <MetricsGrid status={status} />
        <BarChart callbacksRef={chartCbRef} timeframe={config?.timeframes?.[0]} />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-px bg-border border border-border">
          <FeedSection
            title="Signals"
            items={signals}
            renderItem={(item: JournalItem, i: number) => <SignalRow key={i} entry={item} />}
            empty="Waiting for the first signal"
          />
          <FeedSection
            title="Fills"
            items={fills}
            renderItem={(item: JournalItem, i: number) => <FillRow key={i} entry={item} />}
            empty="No fills yet"
          />
          <FeedSection
            title="Reconcile"
            items={reconciles}
            renderItem={(item: JournalItem, i: number) => <ReconcileRow key={i} entry={item} />}
            empty="Waiting for first reconcile"
          />
        </div>
        <footer className="pt-4 text-[10px] text-dim/60 tracking-widest text-center">
          READ-ONLY · LOCAL · NO-VPS COMPLIANT
        </footer>
      </main>
      <ConfigPanel
        isOpen={configOpen}
        onClose={() => setConfigOpen(false)}
        config={config}
        onSave={saveConfig}
        saving={saving}
        saveError={saveError}
      />
    </div>
  )
}
