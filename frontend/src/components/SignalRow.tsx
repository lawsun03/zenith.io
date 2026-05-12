import type { JournalItem, SignalPayload } from '../types'
import { fmtBarTs } from '../utils/format'

export function SignalRow({ entry }: { entry: JournalItem }) {
  const sig = entry.payload as SignalPayload
  const out = sig.outcome
  const sideTone = sig.side === 'long' ? 'text-accent' : 'text-warn'
  const placedTone = out.placed ? 'text-accent' : 'text-dim'
  return (
    <div className="px-4 py-3 text-xs flex items-baseline gap-3 hover:bg-grid">
      <span className="text-dim tabular-nums w-24 shrink-0">{fmtBarTs(entry.ts)}</span>
      <span className={`${sideTone} font-bold w-12 uppercase`}>{sig.side}</span>
      <span className="text-dim w-16">{sig.killzone}</span>
      <span className="flex-1 text-ink/90 truncate" title={sig.rationale}>{sig.rationale}</span>
      <span className={`${placedTone} text-[10px] tracking-wider w-20 text-right`}>
        {out.placed ? `x ${out.allowed_size}` : out.reason}
      </span>
    </div>
  )
}
