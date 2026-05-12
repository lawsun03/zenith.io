import type { JournalItem, ReconcilePayload } from '../types'
import { fmtBarTs } from '../utils/format'

export function ReconcileRow({ entry }: { entry: JournalItem }) {
  const r = entry.payload as ReconcilePayload
  const driftTone = r.drift_detected ? 'text-danger' : 'text-dim'
  return (
    <div className="px-4 py-3 text-xs flex items-baseline gap-3 hover:bg-grid">
      <span className="text-dim tabular-nums w-24 shrink-0">{fmtBarTs(entry.ts)}</span>
      <span className={`${driftTone} font-bold w-20 uppercase`}>
        {r.drift_detected ? `DRIFT ${r.drift_kind ?? ''}` : 'OK'}
      </span>
      <span className="text-dim text-[10px]">
        broker={r.broker_open_contracts} int={r.internal_open_contracts}
      </span>
      <span className="flex-1 text-right text-ink/70 truncate" title={r.notes ?? ''}>
        {r.notes ?? ''}
      </span>
    </div>
  )
}
