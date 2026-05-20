interface Trade {
  ts: string
  side: string
  killzone: string | null
  fill_price: number | null
  size: number | null
  realized_pnl: number | null
}

interface Props {
  trades: Trade[]
}

const PT = 'America/Los_Angeles'

function fmtTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString('en-US', {
      timeZone: PT,
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    })
  } catch {
    return ts.slice(11, 16)
  }
}

function fmtDate(ts: string): string {
  try {
    return new Date(ts).toLocaleDateString('en-US', {
      timeZone: PT,
      month: '2-digit',
      day: '2-digit',
    })
  } catch {
    return ts.slice(0, 10)
  }
}

export function TradesTable({ trades }: Props) {
  if (trades.length === 0) {
    return (
      <div className="bg-panel border border-border px-4 py-3">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Recent Trades</span>
        <p className="text-dim text-xs mt-2">No closed trades yet.</p>
      </div>
    )
  }

  return (
    <div className="bg-panel border border-border">
      <div className="px-4 py-2 border-b border-border">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Recent Trades</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="border-b border-border">
              <th className="px-4 py-2 text-left text-[9px] tracking-[0.2em] text-dim uppercase">Date</th>
              <th className="px-4 py-2 text-left text-[9px] tracking-[0.2em] text-dim uppercase">Time (PT)</th>
              <th className="px-4 py-2 text-left text-[9px] tracking-[0.2em] text-dim uppercase">Side</th>
              <th className="px-4 py-2 text-left text-[9px] tracking-[0.2em] text-dim uppercase">Zone</th>
              <th className="px-4 py-2 text-right text-[9px] tracking-[0.2em] text-dim uppercase">Size</th>
              <th className="px-4 py-2 text-right text-[9px] tracking-[0.2em] text-dim uppercase">P&L</th>
            </tr>
          </thead>
          <tbody>
            {trades.slice(0, 50).map((t, i) => {
              const pnl = t.realized_pnl ?? 0
              const pnlColor = pnl >= 0 ? 'text-accent' : 'text-danger'
              const sideColor = t.side === 'long' ? 'text-accent' : 'text-danger'
              return (
                <tr key={i} className="border-b border-border/40 hover:bg-border/20">
                  <td className="px-4 py-1.5 text-dim">{fmtDate(t.ts)}</td>
                  <td className="px-4 py-1.5 text-dim tabular-nums">{fmtTime(t.ts)}</td>
                  <td className={`px-4 py-1.5 uppercase ${sideColor}`}>{t.side}</td>
                  <td className="px-4 py-1.5 text-dim uppercase">{t.killzone ?? '—'}</td>
                  <td className="px-4 py-1.5 text-right text-ink tabular-nums">{t.size ?? '—'}</td>
                  <td className={`px-4 py-1.5 text-right tabular-nums ${pnlColor}`}>
                    {pnl >= 0 ? '+' : ''}${pnl.toFixed(0)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
