import type { JournalItem, FillPayload } from '../types'
import { fmtBarTs, fmtMoney } from '../utils/format'

export function FillRow({ entry }: { entry: JournalItem }) {
  const f = entry.payload as FillPayload
  const pnl = Number(f.realized_pnl_delta)
  const pnlTone = pnl > 0 ? 'text-accent' : pnl < 0 ? 'text-danger' : 'text-dim'
  const kindTone = f.is_entry ? 'text-warn' : pnlTone
  return (
    <div className="px-4 py-3 text-xs flex items-baseline gap-3 hover:bg-grid">
      <span className="text-dim tabular-nums w-24 shrink-0">{fmtBarTs(entry.ts)}</span>
      <span className={`${kindTone} font-bold w-12 uppercase`}>{f.is_entry ? 'ENTRY' : 'EXIT'}</span>
      <span className="text-dim w-12 uppercase">{f.side}</span>
      <span className="text-ink tabular-nums">x{f.size} @ {f.fill_price}</span>
      <span className={`${pnlTone} flex-1 text-right tabular-nums`}>
        {f.is_entry ? '' : fmtMoney(f.realized_pnl_delta, { signed: true })}
      </span>
    </div>
  )
}
