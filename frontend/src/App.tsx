import { useEffect, useRef, useState } from 'react'
import { useStream } from './hooks/useStream'
import type { ChartCallbacks } from './hooks/useStream'
import { useConfig } from './hooks/useConfig'
import { useKillzone } from './hooks/useKillzone'
import type { StrategyConfig, Position } from './types'

function ConfigFlags({ strategy, partialR }: { strategy: StrategyConfig; partialR?: string }) {
  const flag = (label: string, on: boolean, detail?: string) => (
    <span key={label} className={`font-mono text-[9px] tracking-widest px-1.5 py-0.5 border ${
      on ? 'border-accent/50 text-accent' : 'border-border text-faint'
    }`}>
      {label}{detail ? `:${detail}` : on ? ':on' : ':off'}
    </span>
  )
  const ema = strategy.trend_ema_period > 0
  const macroOn = strategy.ifvg_macro_windows?.length > 0
  const partial = partialR && parseFloat(partialR) > 0
  return (
    <div className="flex items-center gap-1">
      {flag('VP', strategy.vp_enabled)}
      {flag('HTF', strategy.htf_target_enabled)}
      {flag('RuleF', strategy.ifvg_rule_f_enabled ?? false)}
      {flag('EMA', ema, ema ? String(strategy.trend_ema_period) : undefined)}
      {flag('Macro', macroOn)}
      {flag('Partials', !!partial, partial ? `${partialR}R` : undefined)}
    </div>
  )
}
import { Header } from './components/Header'
import { LockoutBanner } from './components/LockoutBanner'
import { MetricsGrid } from './components/MetricsGrid'
import { BarChart } from './components/BarChart'
import { ActivityFeed } from './components/ActivityFeed'
import { ConfigPanel } from './components/ConfigPanel'
import { BacktestsPage } from './components/BacktestsPage'
import { AnalyticsPage } from './pages/Analytics'
import { TodosPage } from './pages/Todos'
import { TradeAnalysisPage } from './pages/TradeAnalysis'
import { TradeAnalysisDetailPage } from './pages/TradeAnalysisDetail'
import { OpenPositions } from './components/OpenPositions'

export default function App() {
  // Path-based router — /backtests and /analytics are standalone pages.
  const path = window.location.pathname
  if (path.startsWith('/backtests')) return <BacktestsPage />
  if (path.startsWith('/analytics')) return <AnalyticsPage />
  if (path.startsWith('/todos')) return <TodosPage />
  const taDetail = path.match(/^\/trade-analysis\/(\d{4}-\d{2}-\d{2})$/)
  if (taDetail) return <TradeAnalysisDetailPage date={taDetail[1]} />
  if (path.startsWith('/trade-analysis')) return <TradeAnalysisPage />

  const chartCbRef = useRef<ChartCallbacks>({})
  const { config, saveConfig, saving, saveError } = useConfig()

  const symbols = (config?.instruments?.length ?? 0) > 0
    ? config!.instruments!
    : config?.instrument ? [config.instrument] : ['MGC']
  const [activeSymbol, setActiveSymbol] = useState<string>('MGC')
  const activeSymbolRef = useRef(activeSymbol)
  useEffect(() => { activeSymbolRef.current = activeSymbol }, [activeSymbol])

  const { status, signals, fills, reconciles, connState } = useStream(chartCbRef, activeSymbolRef)
  const [configOpen, setConfigOpen] = useState(false)

  const [positions, setPositions] = useState<Position[]>([])
  useEffect(() => {
    const poll = () => {
      fetch('/api/positions')
        .then(r => r.json())
        .then(d => setPositions(d.positions ?? []))
        .catch(() => {})
    }
    poll()
    const id = setInterval(poll, 2000)
    return () => clearInterval(id)
  }, [])
  const activePosition = positions.find(p => p.instrument === activeSymbol) ?? null
  const activeKillzone = useKillzone(config?.enabled_killzones)

  const now = new Date()
  const dateLabel = now.toLocaleDateString('en-US', {
    timeZone: 'America/Los_Angeles', weekday: 'long', month: 'long', day: 'numeric',
  })
  const yearLabel = now.toLocaleDateString('en-US', { timeZone: 'America/Los_Angeles', year: 'numeric' })

  const modeLabel = config?.mode === 'live' ? 'live' : 'paper'
  const contractsLabel = config ? `${config.entry_mode} · ${config.contracts} contracts` : ''

  return (
    <div className="min-h-screen flex flex-col h-screen overflow-hidden">

      <div className="flex flex-col h-screen">
        <Header status={status} connState={connState} onConfigOpen={() => setConfigOpen(true)} mode={config?.mode} activeKillzone={activeKillzone} />
        <LockoutBanner lockout={status?.lockout ?? null} />

        <div className="flex-1 min-h-0 flex justify-center items-center overflow-hidden">
          <div className="w-full max-w-[1320px] h-full max-h-[820px] px-7 pt-[22px] pb-[26px] flex flex-col min-h-0 overflow-hidden">

            {/* page head */}
            <div className="flex items-end justify-between px-0.5 pb-5 shrink-0 animate-fade-up">
              <div>
                <div className="text-[22px] text-ink tracking-tight">Live Dashboard</div>
                <div className="text-[11px] text-faint font-mono mt-1">
                  <span className="text-accent-ink">iFVG · Combined Strategy</span> · {modeLabel}
                </div>
              </div>
              <div className="text-[11px] text-dim font-mono">{dateLabel} <span className="text-faint">·</span> {yearLabel}</div>
            </div>

            {/* body grid */}
            <div className="flex-1 min-h-0 grid grid-cols-[1fr_312px] gap-5 overflow-hidden">

              <div className="flex flex-col gap-4 min-h-0 overflow-hidden">
                {/* symbol row */}
                <div className="flex items-center justify-between px-0.5 shrink-0 animate-fade-up">
                  <div className="flex gap-[22px]">
                    {symbols.map(sym => (
                      <button
                        key={sym}
                        onClick={() => setActiveSymbol(sym)}
                        className={`text-[13px] tracking-wide pb-0.5 relative transition-colors ${
                          sym === activeSymbol ? 'text-ink' : 'text-faint hover:text-dim'
                        }`}
                      >
                        {sym}
                        {sym === activeSymbol && <span className="absolute left-0 right-0 -bottom-[7px] h-0.5 bg-accent" />}
                      </button>
                    ))}
                  </div>
                  <div className="flex flex-col items-end gap-1">
                    {contractsLabel && <span className="text-[10px] text-faint font-mono">{config?.timeframes?.[0]} · {contractsLabel}</span>}
                    {config?.strategy && <ConfigFlags strategy={config.strategy} partialR={String(config.partial_profit_r ?? '')} />}
                  </div>
                </div>

                <MetricsGrid status={status} />

                <BarChart callbacksRef={chartCbRef} timeframe={config?.timeframes?.[0]} activeSymbol={activeSymbol} position={activePosition} />
              </div>

              <div className="flex flex-col gap-4 min-h-0 overflow-hidden">
                <OpenPositions positions={positions} />
                <ActivityFeed signals={signals} fills={fills} reconciles={reconciles} activeSymbol={activeSymbol} />
              </div>

            </div>
          </div>
        </div>

      </div>

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
