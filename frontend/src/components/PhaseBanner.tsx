import type { BotConfig, StrategyStatePayload } from '../types'

interface Props {
  config: BotConfig
  phase: StrategyStatePayload['phase']
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

export function PhaseBanner({ config, phase }: Props) {
  const { account_phase, phase_shadow, account_name, risk_per_trade_pct, strategy, strategy_overrides } = config

  if (!account_phase || account_phase === 'practice') return null

  const phaseName = account_phase.toUpperCase()
  const isXfa = account_phase === 'xfa' || account_phase === 'funded'

  // r_multiple: use instrument override if present, else strategy fallback
  // Active symbol isn't passed here; take the first instrument override that has it.
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

  return (
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
        {account_name && (
          <span className="text-[10px] text-dim font-mono">{account_name}</span>
        )}
      </div>

      {/* right: labeled stats */}
      <div className="flex items-center gap-5 flex-wrap justify-end">
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
    </div>
  )
}
