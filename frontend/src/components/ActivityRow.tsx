import type { JournalItem, SignalPayload, FillPayload, ReconcilePayload } from '../types'
import { fmtBarTs, fmtMoney } from '../utils/format'

const DOT: Record<string, string> = {
  signal: 'bg-accent', fill: 'bg-good', reconcile: 'bg-faint',
}

function Shell({ dot, type, ts, main, mainCls, sub, amt, amtCls }: {
  dot: string; type: string; ts: string; main: string; mainCls?: string
  sub?: string; amt?: string; amtCls?: string
}) {
  return (
    <div className="px-5 py-3.5 hover:bg-white/[0.018] transition-colors">
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`w-[5px] h-[5px] rounded-full shrink-0 ${dot}`} />
        <span className="text-[9px] tracking-[0.08em] uppercase text-faint font-mono">{type}</span>
        <span className="ml-auto text-[10px] text-faint font-mono tabular-nums">{ts}</span>
      </div>
      <div className="flex items-baseline justify-between gap-2.5 pl-3">
        <div className="min-w-0">
          <div className={`text-[13px] leading-snug ${mainCls ?? 'text-ink'}`}>{main}</div>
          {sub && <div className="text-[10px] text-faint font-mono mt-0.5">{sub}</div>}
        </div>
        {amt && <div className={`text-[13px] font-medium font-mono tabular-nums whitespace-nowrap ${amtCls ?? 'text-dim'}`}>{amt}</div>}
      </div>
    </div>
  )
}

export function ActivityRow({ item }: { item: JournalItem }) {
  const ts = fmtBarTs(item.ts)

  if (item.kind === 'signal') {
    const s = item.payload as SignalPayload
    return (
      <Shell
        dot={DOT.signal} type="Signal" ts={ts}
        main={`${s.side === 'long' ? 'Long' : 'Short'} · ${s.killzone}`}
        sub={s.rationale}
        amt={s.outcome.placed ? `×${s.outcome.allowed_size}` : s.outcome.reason}
        amtCls={s.outcome.placed ? 'text-accent-ink' : 'text-faint'}
      />
    )
  }

  if (item.kind === 'fill') {
    const f = item.payload as FillPayload
    const pnl = Number(f.realized_pnl_delta)
    const amtCls = f.is_entry ? 'text-ink' : pnl > 0 ? 'text-good' : pnl < 0 ? 'text-danger' : 'text-dim'
    return (
      <Shell
        dot={f.is_entry ? 'bg-good' : pnl < 0 ? 'bg-danger' : 'bg-good'}
        type={f.is_entry ? 'Entry' : 'Exit'} ts={ts}
        main={`${f.side === 'long' ? 'Long' : 'Short'} ×${f.size}`}
        mainCls={f.is_entry ? 'text-good' : pnl < 0 ? 'text-danger' : 'text-ink'}
        sub={`@ ${f.fill_price}`}
        amt={f.is_entry ? f.fill_price : fmtMoney(f.realized_pnl_delta, { signed: true })}
        amtCls={amtCls}
      />
    )
  }

  // reconcile
  const r = item.payload as ReconcilePayload
  return (
    <Shell
      dot={r.drift_detected ? 'bg-danger' : 'bg-faint'}
      type="Reconcile" ts={ts}
      main={r.drift_detected ? `Drift ${r.drift_kind ?? ''}` : 'OK — no drift'}
      mainCls={r.drift_detected ? 'text-danger' : 'text-ink'}
      sub={`broker ${r.broker_open_contracts} · internal ${r.internal_open_contracts}`}
      amt={r.drift_detected ? '!' : '✓'}
      amtCls={r.drift_detected ? 'text-danger' : 'text-good'}
    />
  )
}
