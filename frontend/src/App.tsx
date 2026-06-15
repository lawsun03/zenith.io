import { useEffect, useRef, useState } from 'react'
import { BrowserRouter, Routes, Route, useParams } from 'react-router-dom'
import { useStream } from './hooks/useStream'
import type { ChartCallbacks } from './hooks/useStream'
import { useConfig } from './hooks/useConfig'
import { useKillzone } from './hooks/useKillzone'
import type { StrategyConfig, Position } from './types'

function ConfigFlags({ strategy, partialR }: { strategy: StrategyConfig; partialR?: string }) {
  const ema = strategy.trend_ema_period > 0
  const macroOn = strategy.ifvg_macro_windows?.length > 0
  const partial = partialR && parseFloat(partialR) > 0

  const chips: { label: string; detail?: string }[] = []
  if (strategy.vp_enabled)                   chips.push({ label: 'VP' })
  if (strategy.htf_target_enabled)           chips.push({ label: 'HTF' })
  if (strategy.ifvg_rule_f_enabled)          chips.push({ label: 'RuleF' })
  if (ema)                                   chips.push({ label: 'EMA', detail: String(strategy.trend_ema_period) })
  if (macroOn)                               chips.push({ label: 'Macro' })
  if (partial)                               chips.push({ label: 'Partials', detail: `${partialR}R` })

  if (chips.length === 0) return null

  return (
    <div className="flex items-center gap-1">
      {chips.map(c => (
        <span key={c.label} className="font-mono text-[9px] tracking-widest px-1.5 py-0.5 border border-accent/50 text-accent">
          {c.label}{c.detail ? `:${c.detail}` : ':on'}
        </span>
      ))}
    </div>
  )
}
import { Header } from './components/Header'
import { LockoutBanner } from './components/LockoutBanner'
import { PhaseBanner } from './components/PhaseBanner'
import { MetricsGrid } from './components/MetricsGrid'
import { BarChart } from './components/BarChart'
import { ActivityFeed } from './components/ActivityFeed'
import { ConfigPanel } from './components/ConfigPanel'
import { BacktestsPage } from './components/BacktestsPage'
import { ResearchPage } from './components/ResearchPage'
import { AnalyticsPage } from './pages/Analytics'
import { TodosPage } from './pages/Todos'
import { TradeAnalysisPage } from './pages/TradeAnalysis'
import { TradeAnalysisDetailPage } from './pages/TradeAnalysisDetail'
import { OpenPositions } from './components/OpenPositions'
import { StrategyDebug } from './components/StrategyDebug'

function LiveDashboard() {
  const chartCbRef = useRef<ChartCallbacks>({})
  const { config, setConfig, saveConfig, saving, saveError } = useConfig()

  const symbols = (config?.instruments?.length ?? 0) > 0
    ? config!.instruments!
    : config?.instrument ? [config.instrument] : ['MGC']
  const [activeSymbol, setActiveSymbol] = useState<string>('MGC')
  const activeSymbolRef = useRef(activeSymbol)
  useEffect(() => { activeSymbolRef.current = activeSymbol }, [activeSymbol])

  const { status, signals, fills, reconciles, strategyState, connState } = useStream(chartCbRef, activeSymbolRef)
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
  const sizeLabel = config
    ? (config.risk_per_trade_pct > 0
        ? `risk ${config.risk_per_trade_pct}%/trade`
        : `${config.contracts} contracts`)
    : ''
  const contractsLabel = config ? `${config.entry_mode} · ${sizeLabel}` : ''

  return (
    <div className="min-h-screen flex flex-col h-screen overflow-hidden">

      <div className="flex flex-col h-screen">
        <Header status={status} connState={connState} onConfigOpen={() => setConfigOpen(true)} mode={config?.mode} activeKillzone={activeKillzone} />
        <LockoutBanner lockout={status?.lockout ?? null} />
        {config && <PhaseBanner config={config} phase={strategyState?.phase ?? null} onConfigChange={setConfig} />}

        <div className="flex-1 min-h-0 flex justify-center items-center overflow-hidden">
          <div className="w-full max-w-[1320px] h-full max-h-[820px] px-7 pt-[22px] pb-[26px] flex flex-col min-h-0 overflow-hidden">

            {/* page head */}
            <div className="flex items-end justify-between px-0.5 pb-5 shrink-0 animate-fade-up">
              <div>
                <div className="text-[36px] font-extrabold text-ink tracking-tight leading-none">LIVE DASHBOARD</div>
                <div className="text-[11px] text-faint font-mono mt-2">
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
                <StrategyDebug state={strategyState} />
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

function TADetail() {
  const { date } = useParams<{ date: string }>()
  return <TradeAnalysisDetailPage date={date ?? ''} />
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/backtests" element={<BacktestsPage />} />
        <Route path="/research" element={<ResearchPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/todos" element={<TodosPage />} />
        <Route path="/trade-analysis/:date" element={<TADetail />} />
        <Route path="/trade-analysis" element={<TradeAnalysisPage />} />
        <Route path="/*" element={<LiveDashboard />} />
      </Routes>
    </BrowserRouter>
  )
}
