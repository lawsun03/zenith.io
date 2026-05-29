import { useState } from 'react'
import type { StrategyStatePayload } from '../types'

interface Props {
  data: StrategyStatePayload | null
}

function CriterionRow({ label, value, badge }: { label: string; value: boolean | undefined; badge?: string }) {
  const mark =
    value === true  ? <span className="text-[#00ff41]">✓</span> :
    value === false ? <span className="text-red-500">✗</span> :
                      <span className="text-dim">—</span>
  return (
    <div className="flex items-center justify-between py-[3px]">
      <span className="text-dim">{label}</span>
      <span className="flex items-center gap-1">
        {badge && <span className="text-[9px] tracking-wider text-dim/70 bg-grid px-1">{badge}</span>}
        {mark}
      </span>
    </div>
  )
}

function GradeBadge({ grade, passes }: { grade?: string; passes?: boolean }) {
  if (!grade) return <span className="text-dim text-xs">—</span>
  const isGood = grade === 'A+' || grade === 'A' || grade === 'A-'
  const colorClass = isGood ? 'text-[#00ff41]' : 'text-red-500'
  return (
    <div className="flex items-baseline gap-3">
      <span className={`text-2xl font-bold tabular-nums ${colorClass}`}>{grade}</span>
      {passes !== undefined && (
        <span className={`text-[10px] tracking-widest uppercase ${passes ? 'text-[#00ff41]' : 'text-red-500'}`}>
          {passes ? 'PASS' : 'FAIL'}
        </span>
      )}
    </div>
  )
}

function MomentumBadge({ quality }: { quality?: string }) {
  if (!quality) return <span className="text-dim">—</span>
  if (quality === 'strong') return <span className="text-[#00ff41]">strong</span>
  if (quality === 'decent') return <span className="text-yellow-400">decent</span>
  return <span className="text-red-500">weak</span>
}

export function StrategyDebug({ data }: Props) {
  const [open, setOpen] = useState(false)

  return (
    <section className="bg-panel">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full border-b border-border px-4 py-2 flex items-baseline justify-between hover:bg-grid transition-colors"
      >
        <h2 className="text-[10px] tracking-[0.3em] text-dim uppercase">Strategy Debug</h2>
        <span className="text-dim text-[10px]">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="px-4 py-3 text-xs space-y-4">

          {/* Grade */}
          <div className="space-y-1">
            <GradeBadge grade={data?.grade} passes={data?.passes} />
            {data?.reason && (
              <p className="text-dim text-[11px] truncate" title={data.reason}>{data.reason}</p>
            )}
          </div>

          {/* Criteria */}
          <div className="border-t border-border/40 pt-3 space-y-0">
            <div className="text-[9px] tracking-widest text-dim/60 uppercase mb-2">Criteria</div>
            <CriterionRow
              label="Sweep present"
              value={data?.has_delivery_fvg !== undefined ? data.has_delivery_fvg : undefined}
            />
            <CriterionRow
              label="iFVG inverted"
              value={data?.has_delivery_fvg}
              badge={data?.delivery_fvg_side ?? undefined}
            />
            <CriterionRow
              label="Target clear"
              value={data?.target_clear}
            />
            <CriterionRow
              label="FVG singular"
              value={data?.fvg_singular}
              badge={
                data?.singularity_timeframe && data.singularity_timeframe !== 'none'
                  ? data.singularity_timeframe
                  : undefined
              }
            />
            <CriterionRow
              label="P/D correct"
              value={data?.premium_discount_ok}
            />
            <CriterionRow
              label="30min delivery FVG"
              value={data?.has_delivery_fvg}
              badge={
                data?.delivery_fvg_side
                  ? `${data.delivery_fvg_side}${data.delivery_fvg_in_pd ? ' P/D' : ''}`
                  : undefined
              }
            />
            <CriterionRow
              label="BPR confluence"
              value={data?.bpr_confluence}
              badge={data?.bpr_timeframe ?? undefined}
            />
          </div>

          {/* Quality */}
          <div className="border-t border-border/40 pt-3 space-y-1">
            <div className="text-[9px] tracking-widest text-dim/60 uppercase mb-2">Quality</div>
            <div className="flex items-center justify-between py-[3px]">
              <span className="text-dim">Momentum</span>
              <MomentumBadge quality={data?.momentum_quality} />
            </div>
            <div className="flex items-center justify-between py-[3px]">
              <span className="text-dim">Fib extension</span>
              <span className="flex items-center gap-2">
                {data?.fib_extension && (
                  <span className="text-dim/70 tabular-nums">{data.fib_extension}</span>
                )}
                {data?.fib_displacement_ok === true && <span className="text-[#00ff41]">ok</span>}
                {data?.fib_displacement_ok === false && <span className="text-red-500">low</span>}
                {data?.fib_displacement_ok === undefined && <span className="text-dim">—</span>}
              </span>
            </div>
            <div className="flex items-center justify-between py-[3px]">
              <span className="text-dim">Recent sweep</span>
              {data?.recent_sweep_ok === true && <span className="text-[#00ff41]">ok</span>}
              {data?.recent_sweep_ok === false && <span className="text-red-500">stale</span>}
              {data?.recent_sweep_ok === undefined && <span className="text-dim">—</span>}
            </div>
          </div>

          {/* Session */}
          <div className="border-t border-border/40 pt-3 space-y-1">
            <div className="text-[9px] tracking-widest text-dim/60 uppercase mb-2">Session</div>
            {(data?.session_high || data?.session_low) && (
              <div className="flex items-center justify-between py-[3px]">
                <span className="text-dim">H / L</span>
                <span className="text-ink tabular-nums text-[11px]">
                  {data.session_high ?? '—'} / {data.session_low ?? '—'}
                </span>
              </div>
            )}
            <div className="flex items-center justify-between py-[3px]">
              <span className="text-dim">Window</span>
              {data?.in_session_window === true && <span className="text-[#00ff41]">in-session</span>}
              {data?.in_session_window === false && <span className="text-dim">outside</span>}
              {data?.in_session_window === undefined && <span className="text-dim">—</span>}
            </div>
            <div className="flex items-center justify-between py-[3px]">
              <span className="text-dim">Macro</span>
              {data?.in_macro_window === true && <span className="text-yellow-400">active</span>}
              {data?.in_macro_window === false && <span className="text-dim">inactive</span>}
              {data?.in_macro_window === undefined && <span className="text-dim">—</span>}
            </div>
            <div className="flex items-center justify-between py-[3px]">
              <span className="text-dim">News</span>
              {data?.news_blackout === true && <span className="text-red-500 tracking-widest text-[10px]">BLACKOUT</span>}
              {data?.news_blackout === false && <span className="text-dim">clear</span>}
              {data?.news_blackout === undefined && <span className="text-dim">—</span>}
            </div>
          </div>

        </div>
      )}
    </section>
  )
}
