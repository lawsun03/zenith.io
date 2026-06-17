export interface Indicators {
  ema: boolean
  rsi: boolean
  macd: boolean
  fvg: boolean
  ob: boolean
}

interface Props {
  indicators: Indicators
  emaPeriod: number
  onToggle: (key: keyof Indicators) => void
  onEmaPeriod: (p: number) => void
}

const ITEMS: { key: keyof Indicators; label: string; sub: string }[] = [
  { key: 'ema', label: 'EMA', sub: 'trend line' },
  { key: 'fvg', label: 'FVG', sub: 'fair value gaps' },
  { key: 'ob', label: 'OB', sub: 'order blocks' },
  { key: 'rsi', label: 'RSI', sub: 'momentum' },
  { key: 'macd', label: 'MACD', sub: 'convergence' },
]

export function IndicatorPalette({ indicators, emaPeriod, onToggle, onEmaPeriod }: Props) {
  return (
    <div className="w-[150px] shrink-0 flex flex-col gap-1.5">
      <div className="text-[9px] uppercase tracking-widest text-faint font-mono px-1 pb-1">Indicators</div>
      {ITEMS.map(it => {
        const on = indicators[it.key]
        return (
          <div key={it.key}>
            <button
              onClick={() => onToggle(it.key)}
              className={`w-full flex items-center justify-between px-2.5 py-2 rounded-[7px] border transition-colors text-left ${
                on ? 'border-accent/50 bg-accent/10' : 'border-border bg-panel-hi hover:border-dim/40'
              }`}
            >
              <div>
                <div className={`text-[11px] font-mono ${on ? 'text-accent-ink' : 'text-dim'}`}>{it.label}</div>
                <div className="text-[8px] text-faint font-mono">{it.sub}</div>
              </div>
              <span className={`text-[13px] leading-none ${on ? 'text-accent' : 'text-faint'}`}>{on ? '−' : '+'}</span>
            </button>
            {it.key === 'ema' && on && (
              <div className="flex items-center gap-1 px-2 pt-1">
                <span className="text-[8px] text-faint font-mono">period</span>
                <input
                  type="number" value={emaPeriod} min={2} max={200}
                  onChange={e => onEmaPeriod(Math.max(2, Math.min(200, parseInt(e.target.value) || 20)))}
                  className="w-12 bg-bg border border-border rounded px-1 py-0.5 text-[10px] font-mono text-ink"
                />
              </div>
            )}
          </div>
        )
      })}
      <div className="text-[8px] text-faint/70 font-mono px-1 pt-2 leading-relaxed">
        add an indicator, then right-click it on the chart to turn it into a trade condition.
      </div>
    </div>
  )
}
