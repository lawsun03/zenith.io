import type { Rule, StopBasis } from './rules'

interface Props {
  rule: Rule
  onChange: (r: Rule) => void
}

const STOP_LABEL: Record<StopBasis, string> = { fvg: 'below FVG', swing: 'swing low', points: 'fixed pts' }

export function RulePanel({ rule, onChange }: Props) {
  const set = (patch: Partial<Rule>) => onChange({ ...rule, ...patch })
  const removeCond = (id: string) => set({ conditions: rule.conditions.filter(c => c.id !== id) })

  return (
    <div className="w-[290px] shrink-0 flex flex-col gap-3">
      <div className="text-[9px] uppercase tracking-widest text-faint font-mono px-1">Entry Rule</div>

      <div className="bg-panel-hi border border-border rounded-[9px] p-3 flex flex-col gap-2.5">
        {/* side */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-faint font-mono w-10">side</span>
          <div className="flex gap-0">
            {(['long', 'short'] as const).map(s => (
              <button key={s} onClick={() => set({ side: s })}
                className={`text-[10px] font-mono uppercase tracking-wider px-3 py-1 border ${
                  rule.side === s ? (s === 'long' ? 'border-accent bg-accent/10 text-accent-ink' : 'border-red-400/60 bg-red-400/10 text-red-300') : 'border-border text-faint hover:text-dim'
                } ${s === 'long' ? 'rounded-l-[6px] border-r-0' : 'rounded-r-[6px]'}`}>
                {s}
              </button>
            ))}
          </div>
        </div>

        {/* conditions as a WHEN / AND·OR sentence */}
        <div className="border-t border-border pt-2">
          {rule.conditions.length === 0 ? (
            <div className="text-[10px] text-faint font-mono italic py-3 text-center leading-relaxed">
              no conditions yet —<br />right-click an indicator or zone on the chart
            </div>
          ) : (
            <div className="flex flex-col gap-1.5">
              {rule.conditions.map((c, i) => (
                <div key={c.id} className="flex items-center gap-1.5">
                  <span className="text-[9px] font-mono text-faint w-9 shrink-0 text-right">
                    {i === 0 ? 'WHEN' : (
                      <button onClick={() => set({ joiner: rule.joiner === 'AND' ? 'OR' : 'AND' })}
                        className="text-accent hover:text-accent-ink uppercase">{rule.joiner}</button>
                    )}
                  </span>
                  <div className="flex-1 flex items-center justify-between bg-accent/8 border border-accent/25 rounded-[6px] px-2 py-1">
                    <span className="text-[11px] font-mono text-accent-ink">{c.label}</span>
                    <button onClick={() => removeCond(c.id)} className="text-faint hover:text-red-300 text-[12px] leading-none ml-2">×</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* exit */}
      <div className="text-[9px] uppercase tracking-widest text-faint font-mono px-1">Exit</div>
      <div className="bg-panel-hi border border-border rounded-[9px] p-3 flex flex-col gap-2.5">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-faint font-mono w-10">stop</span>
          <div className="flex gap-0 flex-1">
            {(['fvg', 'swing', 'points'] as const).map(b => (
              <button key={b} onClick={() => set({ stopBasis: b })}
                className={`flex-1 text-[9px] font-mono px-1.5 py-1 border ${
                  rule.stopBasis === b ? 'border-accent bg-accent/10 text-accent-ink' : 'border-border text-faint hover:text-dim'
                } ${b === 'fvg' ? 'rounded-l-[6px]' : b === 'points' ? 'rounded-r-[6px] border-l-0' : 'border-l-0'}`}>
                {STOP_LABEL[b]}
              </button>
            ))}
          </div>
        </div>
        {rule.stopBasis === 'points' && (
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-faint font-mono w-10">pts</span>
            <input type="number" value={rule.stopPoints} min={0.5} step={0.5}
              onChange={e => set({ stopPoints: parseFloat(e.target.value) || 3 })}
              className="w-16 bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] font-mono text-ink" />
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-faint font-mono w-10">target</span>
          <input type="number" value={rule.targetR} min={0.5} step={0.5}
            onChange={e => set({ targetR: parseFloat(e.target.value) || 3 })}
            className="w-16 bg-bg border border-border rounded px-1.5 py-0.5 text-[10px] font-mono text-ink" />
          <span className="text-[10px] text-faint font-mono">R</span>
        </div>
      </div>

      {/* readable summary */}
      <div className="bg-bg/60 border border-border rounded-[9px] p-3">
        <div className="text-[9px] uppercase tracking-widest text-faint font-mono mb-1.5">reads as</div>
        <p className="text-[11px] font-mono text-dim leading-relaxed">
          {rule.conditions.length === 0
            ? <span className="text-faint italic">build a condition to see the rule…</span>
            : <>enter <span className="text-accent-ink">{rule.side}</span> when{' '}
                {rule.conditions.map((c, i) => (
                  <span key={c.id}>{i > 0 && <span className="text-accent"> {rule.joiner.toLowerCase()} </span>}<span className="text-ink">{c.label.toLowerCase()}</span></span>
                ))}
                ; stop {STOP_LABEL[rule.stopBasis]}{rule.stopBasis === 'points' ? ` (${rule.stopPoints})` : ''}, target {rule.targetR}R.</>}
        </p>
      </div>
    </div>
  )
}
