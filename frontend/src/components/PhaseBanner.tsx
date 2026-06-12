import { useConfirm } from '../hooks/useConfirm'
import { useEffect, useState } from 'react'
import type { BotConfig, StrategyStatePayload } from '../types'

interface Props {
  config: BotConfig
  phase: StrategyStatePayload['phase']
  onConfigChange: (updated: BotConfig) => void
}

interface AccountInfo {
  name: string
  balance: number
  can_trade: boolean
  simulated: boolean
}

function fmt$(raw: string): string {
  const n = parseFloat(raw)
  if (isNaN(n)) return '—'
  return n.toLocaleString('en-US', { maximumFractionDigits: 0 })
}

function fmtSigned(raw: string): string {
  const n = parseFloat(raw)
  if (isNaN(n)) return '—'
  const abs = Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: 0 })
  return (n >= 0 ? '+$' : '−$') + abs
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col items-start gap-0.5 shrink-0">
      <span className="text-[9px] text-dim font-mono tracking-wide uppercase">{label}</span>
      <span className="text-[12px] text-ink font-mono tabular-nums">{value}</span>
    </div>
  )
}

const PHASES = ['practice', 'combine', 'xfa', 'live'] as const
type Phase = typeof PHASES[number]

const PHASE_CONFIRM: Record<string, string> = {
  practice: 'Switch to PRACTICE mode? The governor will be disabled and no phase rules apply.',
  combine:  'Switch account phase to COMBINE? This builds a fresh rule tracker (balance resets to the configured start).',
  xfa:      'Switch account phase to XFA (funded)? This builds a fresh rule tracker for funded account rules.',
  live:     'Switch account phase to LIVE? This builds a fresh rule tracker for live account rules.',
}

export function PhaseBanner({ config, phase, onConfigChange }: Props) {
  const { confirm, modal } = useConfirm()
  const { account_phase, phase_shadow, account_name, risk_per_trade_pct, strategy, strategy_overrides } = config

  const isLive = config.mode === 'live'
  const [accounts, setAccounts] = useState<AccountInfo[]>([])

  useEffect(() => {
    if (!isLive) return
    fetch('/api/accounts')
      .then(r => r.json())
      .then(d => setAccounts(d.accounts ?? []))
      .catch(() => setAccounts([]))
  }, [isLive])

  async function handleAccountChange(name: string) {
    if (name === account_name) return
    const ok = await confirm(
      `Switch trading account to ${name}? The bot binds its data/order connection at startup — the change is saved now but ONLY takes effect after a restart. Restart now?`,
      { title: 'Switch Account', variant: 'warn', confirmLabel: 'Save & Restart' }
    )
    if (!ok) return
    try {
      const res = await fetch('/api/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_name: name }),
      })
      if (!res.ok) return
      onConfigChange(await res.json())
      await fetch('/api/restart', { method: 'POST' }).catch(() => {/* process exits before response */})
    } catch { /* ignore */ }
  }

  const currentPhase: Phase = (account_phase as Phase) ?? 'practice'
  const isPractice = currentPhase === 'practice'
  const isXfa = currentPhase === 'xfa'

  // r_multiple: use instrument override if present, else strategy fallback
  let rMultiple = strategy?.r_multiple ?? '—'
  if (strategy_overrides) {
    const overrideValues = Object.values(strategy_overrides)
    const firstR = overrideValues.find(o => o.r_multiple != null)?.r_multiple
    if (firstR != null) rMultiple = firstR
  }

  const targetReached = phase?.target_reached ?? false
  const awaiting = phase == null

  const balance    = awaiting ? '—' : fmt$(phase.balance)
  const mll        = awaiting ? '—' : (phase.mll === 'None' ? '—' : fmt$(phase.mll))
  const cushion    = awaiting ? '—' : (phase.cushion === 'None' ? '—' : fmt$(phase.cushion))
  const today      = awaiting ? '—' : fmtSigned(phase.today_pnl)
  const bestDay    = awaiting ? '—' : fmtSigned(phase.best_day)
  const winDays    = awaiting ? '—' : String(phase.winning_days)

  async function handlePhaseSwitch(p: Phase) {
    if (p === currentPhase) return
    const msg = PHASE_CONFIRM[p] ?? `Switch account phase to ${p.toUpperCase()}?`
    const ok = await confirm(msg, { title: 'Switch Phase', variant: 'warn', confirmLabel: 'Switch' })
    if (!ok) return
    try {
      const res = await fetch('/api/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ account_phase: p }),
      })
      if (!res.ok) return
      onConfigChange(await res.json())
    } catch { /* ignore */ }
  }

  async function handleShadowToggle() {
    const nextShadow = !phase_shadow
    const msg = nextShadow
      ? 'Shadow on = tracker simulates a fresh account and ignores broker balance (dry-run).'
      : 'Shadow off = reconcile to broker truth at next restart.'
    const ok = await confirm(msg, { title: 'Toggle Shadow', variant: 'warn', confirmLabel: nextShadow ? 'Enable Shadow' : 'Disable Shadow' })
    if (!ok) return
    try {
      const res = await fetch('/api/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phase_shadow: nextShadow }),
      })
      if (!res.ok) return
      onConfigChange(await res.json())
    } catch { /* ignore */ }
  }

  // Account select — live mode only; falls back to plain text when no accounts loaded
  const accountSelect = (() => {
    if (!account_name) return null
    if (!isLive || accounts.length === 0) {
      return <span className="text-[10px] text-faint font-mono">{account_name}</span>
    }
    const fmtOption = (a: AccountInfo) =>
      `${a.name}  ·  $${a.balance.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
    return (
      <select
        value={account_name ?? ''}
        onChange={e => handleAccountChange(e.target.value)}
        className="text-[10px] text-dim font-mono bg-transparent border border-border/50 px-1 py-0.5 cursor-pointer hover:border-border focus:outline-none"
      >
        {accounts.map(a => (
          <option key={a.name} value={a.name} className="bg-panel text-ink">
            {fmtOption(a)}
          </option>
        ))}
        {/* fallback option if current account not in list */}
        {!accounts.find(a => a.name === account_name) && (
          <option value={account_name} className="bg-panel text-ink">{account_name}</option>
        )}
      </select>
    )
  })()

  // Phase segmented control
  const phaseControl = (
    <div className="flex items-center gap-2 shrink-0">
      <div className="flex items-center border border-border divide-x divide-border">
        {PHASES.map(p => (
          <button
            key={p}
            onClick={() => handlePhaseSwitch(p)}
            className={`text-[9px] font-mono tracking-widest uppercase px-2 py-1 transition-colors ${
              p === currentPhase
                ? 'bg-accent/10 text-accent-ink'
                : 'text-faint hover:text-dim'
            }`}
          >
            {p}
          </button>
        ))}
      </div>
      {/* shadow pill — relevant for non-practice phases but always visible */}
      <button
        onClick={handleShadowToggle}
        className={`text-[9px] font-mono tracking-widest uppercase px-2 py-1 border transition-colors ${
          phase_shadow
            ? 'border-warn/50 text-warn bg-warn/[0.07] hover:bg-warn/[0.12]'
            : 'border-border text-faint hover:text-dim'
        }`}
      >
        shadow
      </button>
    </div>
  )

  // Practice-mode slim variant
  if (isPractice) {
    return (
      <>
        {modal}
        <div className="shrink-0 border-b border-border bg-panel px-7 py-2 flex items-center justify-between gap-6">
          <div className="flex items-center gap-2.5">
            <span className="text-[11px] font-bold text-faint tracking-widest font-mono">PRACTICE</span>
            <span className="text-[9px] text-dim font-mono">governor off</span>
            {accountSelect}
          </div>
          {phaseControl}
        </div>
      </>
    )
  }

  const phaseName = account_phase!.toUpperCase()

  return (
    <>
      {modal}
      <div className="shrink-0 border-b border-border bg-panel px-7 py-2.5 flex items-center justify-between gap-6">
        {/* left: phase label + account */}
        <div className="flex items-center gap-2.5 shrink-0">
          <span className="text-[11px] font-bold text-ink tracking-widest font-mono">{phaseName}</span>
          {phase_shadow && (
            <span className="text-[9px] font-mono tracking-widest px-1.5 py-0.5 border border-warn/50 text-warn bg-warn/[0.07]">
              SHADOW
            </span>
          )}
          {targetReached && (
            <span className="text-[9px] font-mono tracking-widest px-1.5 py-0.5 border border-accent/60 text-accent bg-accent/[0.07] animate-pulse-soft">
              TARGET REACHED — STOP
            </span>
          )}
          {accountSelect}
        </div>

        {/* center: labeled stats */}
        <div className="flex items-center gap-5 flex-wrap justify-end flex-1">
          <Stat label="balance" value={balance === '—' ? '—' : `$${balance}`} />
          <Stat label="MLL" value={mll === '—' ? '—' : `$${mll}`} />
          <Stat label="cushion" value={cushion === '—' ? '—' : `$${cushion}`} />
          <Stat label="today" value={today} />
          <Stat label="best day" value={bestDay} />
          {isXfa && <Stat label="win days" value={winDays} />}
          <div className="w-px h-5 bg-border shrink-0" />
          <Stat label="risk/trade" value={`${risk_per_trade_pct}%`} />
          <Stat label="target" value={`${rMultiple}R`} />
          {awaiting && (
            <span className="text-[9px] text-faint font-mono italic">awaiting first bar</span>
          )}
        </div>

        {/* right: phase toggle */}
        {phaseControl}
      </div>
    </>
  )
}
