import { useState } from 'react'
import type { StrategyStatePayload } from '../types'

function LabeledValue({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between px-3 py-1.5 border-b border-border last:border-0">
      <span className="text-[10px] text-dim font-mono tracking-wide">{label}</span>
      <span className="text-[10px] text-ink font-mono">{value}</span>
    </div>
  )
}

function PhaseSection({ phase }: { phase: NonNullable<StrategyStatePayload['phase']> }) {
  const nameLabel = phase.name.toUpperCase()
  const targetBadge = phase.target_reached
    ? <span className="ml-1.5 text-[9px] text-accent font-mono tracking-widest">TARGET</span>
    : null
  return (
    <div>
      <div className="px-3 pt-2 pb-1 text-[9px] text-faint font-mono tracking-widest uppercase flex items-center">
        Account Phase — {nameLabel}{targetBadge}
      </div>
      <LabeledValue label="balance" value={`$${parseFloat(phase.balance).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
      <LabeledValue label="MLL" value={phase.mll === 'None' ? '—' : `$${parseFloat(phase.mll).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
      <LabeledValue label="cushion" value={phase.cushion === 'None' ? '—' : `$${parseFloat(phase.cushion).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
      <LabeledValue label="today P&L" value={`$${parseFloat(phase.today_pnl).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
      <LabeledValue label="best day" value={`$${parseFloat(phase.best_day).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
      <LabeledValue label="winning days" value={String(phase.winning_days)} />
    </div>
  )
}

export function StrategyDebug({ state }: { state: StrategyStatePayload | null }) {
  const [open, setOpen] = useState(false)

  if (!state) return null

  const hasPhase = state.phase != null

  return (
    <div className="shrink-0">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-3 py-2 text-[10px] text-faint font-mono tracking-widest uppercase hover:text-dim transition-colors"
      >
        <span>Strategy Debug</span>
        <span className="text-[8px]">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="border-t border-border">
          {/* Grader */}
          {state.grade != null && (
            <div>
              <div className="px-3 pt-2 pb-1 text-[9px] text-faint font-mono tracking-widest uppercase">Grader</div>
              <LabeledValue label="grade" value={`${state.grade}${state.passes ? ' ✓' : ' ✗'}`} />
              {state.reason && <LabeledValue label="reason" value={state.reason} />}
              {state.active_fvgs_count != null && (
                <LabeledValue label="active FVGs" value={String(state.active_fvgs_count)} />
              )}
            </div>
          )}

          {/* Session range */}
          {(state.session_high != null || state.session_low != null) && (
            <div>
              <div className="px-3 pt-2 pb-1 text-[9px] text-faint font-mono tracking-widest uppercase">Session Range</div>
              {state.session_high != null && <LabeledValue label="high" value={state.session_high} />}
              {state.session_low != null && <LabeledValue label="low" value={state.session_low} />}
            </div>
          )}

          {/* Filters */}
          <div>
            <div className="px-3 pt-2 pb-1 text-[9px] text-faint font-mono tracking-widest uppercase">Filters</div>
            <LabeledValue label="macro window" value={state.in_macro_window ? 'yes' : 'no'} />
            <LabeledValue label="news blackout" value={state.news_blackout ? 'yes' : 'no'} />
          </div>

          {/* ORB State */}
          {state.orb_state != null && (
            <div>
              <div className="px-3 pt-2 pb-1 text-[9px] text-faint font-mono tracking-widest uppercase">ORB</div>
              <LabeledValue
                label="range"
                value={
                  state.orb_state.or_established
                    ? `${state.orb_state.or_low} – ${state.orb_state.or_high}`
                    : 'building…'
                }
              />
              <LabeledValue label="signals today" value={String(state.orb_state.fired)} />
            </div>
          )}

          {/* Account Phase */}
          {hasPhase && <PhaseSection phase={state.phase!} />}
          {!hasPhase && (
            <div>
              <div className="px-3 pt-2 pb-1 text-[9px] text-faint font-mono tracking-widest uppercase">Account Phase</div>
              <div className="px-3 py-1.5 text-[10px] text-faint font-mono">— (practice)</div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
