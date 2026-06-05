import { useEffect, useRef, useState } from 'react'
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
import { AnalyticsPage } from './pages/Analytics'
import { ForceSignalPanel } from './components/ForceSignalPanel'
import { MatrixRain } from './components/MatrixRain'
import { GlowOverlay } from './components/GlowOverlay'
import { StrategyDebug } from './components/StrategyDebug'
import type { JournalItem } from './types'

export default function App() {
  // Simple path-based router. /backtests opens the standalone page;
  // anything else is the live dashboard. Open in a new tab to compare.
  const path = window.location.pathname
  if (path.startsWith('/backtests')) return <BacktestsPage />
  if (path.startsWith('/analytics')) return <AnalyticsPage />

  const chartCbRef = useRef<ChartCallbacks>({})
  const { config, saveConfig, saving, saveError } = useConfig()

  const symbols = (config?.instruments?.length ?? 0) > 0
    ? config!.instruments!
    : config?.instrument ? [config.instrument] : ['MGC']
  const [activeSymbol, setActiveSymbol] = useState<string>('MGC')
  const activeSymbolRef = useRef(activeSymbol)
  useEffect(() => { activeSymbolRef.current = activeSymbol }, [activeSymbol])

  const { status, signals, fills, reconciles, strategyState, connState } = useStream(chartCbRef, activeSymbolRef)
  const [configOpen, setConfigOpen] = useState(false)
  const activeKillzone = useKillzone(config?.enabled_killzones)

  const isActive =
    connState === 'connected' &&
    !!activeKillzone &&
    !status?.lockout

  return (
    <div className="min-h-screen scanlines">
      <MatrixRain active={isActive} />
      <GlowOverlay active={isActive} />
      {activeKillzone && <div className="h-[2px] bg-accent/50" />}
      <Header status={status} connState={connState} onConfigOpen={() => setConfigOpen(true)} mode={config?.mode} activeKillzone={activeKillzone} />
      <LockoutBanner lockout={status?.lockout ?? null} />
      <main className="p-6 flex flex-col gap-6 max-w-[1400px] mx-auto">
        <MetricsGrid status={status} />
        {symbols.length > 1 && (
          <div className="flex gap-2">
            {symbols.map(sym => (
              <button
                key={sym}
                onClick={() => setActiveSymbol(sym)}
                className={`text-[10px] tracking-widest uppercase px-3 py-0.5 border transition-colors ${
                  sym === activeSymbol
                    ? 'text-accent border-accent/70 bg-accent/5'
                    : 'text-dim border-dim/30 hover:text-ink hover:border-dim/60'
                }`}
              >
                {sym}
              </button>
            ))}
          </div>
        )}
        <BarChart callbacksRef={chartCbRef} timeframe={config?.timeframes?.[0]} activeSymbol={activeSymbol} />
        {/* Feeds + strategy debug share one frame. On xl the debug panel sits
            as a 4th column beside the feeds (uses the horizontal space); on lg it
            drops to a full-width row below them; on mobile everything stacks. */}
        <div className="grid grid-cols-1 lg:grid-cols-3 xl:grid-cols-4 gap-px bg-border border border-border">
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
          <div className="bg-panel lg:col-span-3 xl:col-span-1">
            <StrategyDebug data={strategyState} />
          </div>
        </div>
        {config?.mode === 'live' && <ForceSignalPanel />}
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
