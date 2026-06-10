import type { Position } from '../types'

function fmt(v: string) {
  const n = parseFloat(v)
  return isNaN(n) ? '—' : n.toFixed(1)
}

export function OpenPositions({ positions }: { positions: Position[] }) {
  if (positions.length === 0) return null

  return (
    <div className="bg-panel border border-border shrink-0 animate-fade-up">
      <div className="px-4 pt-3 pb-1">
        <div className="text-[10px] font-mono tracking-[0.12em] uppercase text-faint mb-2">Open Positions</div>
        {positions.map((p, i) => {
          const isLong = p.side === 'long'
          return (
            <div key={i} className="flex items-center gap-3 py-1.5 border-t border-border/50 first:border-t-0">
              <span className={`text-[11px] font-mono font-bold w-8 ${isLong ? 'text-accent' : 'text-danger'}`}>
                {p.instrument}
              </span>
              <span className={`text-[9px] tracking-widest uppercase px-1 border ${
                isLong ? 'border-accent/40 text-accent' : 'border-danger/40 text-danger'
              }`}>
                {p.side} {p.size}
              </span>
              <div className="flex-1 flex items-center justify-end gap-2 font-mono text-[10px]">
                <span className="text-dim">E <span className="text-ink">{fmt(p.entry)}</span></span>
                <span className="text-dim">SL <span className="text-danger">{fmt(p.stop)}</span></span>
                {p.partial && (
                  <span className="text-dim">P1 <span className="text-warn">{fmt(p.partial)}</span></span>
                )}
                <span className="text-dim">TP <span className="text-accent">{fmt(p.target)}</span></span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
