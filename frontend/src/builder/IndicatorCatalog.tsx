import { useMemo, useState } from 'react'
import { CATALOG, CATEGORIES, type IndicatorDef } from './indicators'

interface Props {
  active: Set<string> // def ids already added
  onAdd: (def: IndicatorDef) => void
  onClose: () => void
}

export function IndicatorCatalog({ active, onAdd, onClose }: Props) {
  const [q, setQ] = useState('')
  const [cat, setCat] = useState<string>('All')

  const filtered = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return CATALOG.filter(d =>
      (cat === 'All' || d.cat === cat) &&
      (ql === '' || d.name.toLowerCase().includes(ql) || d.cat.toLowerCase().includes(ql)))
  }, [q, cat])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div className="w-[640px] max-h-[78vh] bg-bg border border-border rounded-[12px] shadow-2xl flex flex-col overflow-hidden animate-fade-up" onClick={e => e.stopPropagation()}>
        {/* header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <span className="text-[13px] font-medium text-ink">Indicators &amp; Studies</span>
          <button onClick={onClose} className="text-faint hover:text-ink text-[16px] leading-none">×</button>
        </div>
        {/* search */}
        <div className="px-4 py-2.5 border-b border-border">
          <input autoFocus value={q} onChange={e => setQ(e.target.value)} placeholder="Search 90+ indicators…"
            className="w-full bg-panel-hi border border-border rounded-[8px] px-3 py-2 text-[12px] font-mono text-ink placeholder:text-faint outline-none focus:border-accent/50" />
        </div>
        <div className="flex min-h-0 flex-1">
          {/* categories */}
          <div className="w-[150px] shrink-0 border-r border-border overflow-y-auto py-1.5">
            {['All', ...CATEGORIES].map(c => (
              <button key={c} onClick={() => setCat(c)}
                className={`w-full text-left px-3 py-1.5 text-[10px] font-mono transition-colors ${cat === c ? 'text-accent-ink bg-accent/10' : 'text-dim hover:text-ink'}`}>
                {c}
              </button>
            ))}
          </div>
          {/* list */}
          <div className="flex-1 overflow-y-auto py-1.5">
            {filtered.length === 0 && <div className="text-[11px] text-faint font-mono px-4 py-6 text-center">no match</div>}
            {filtered.map(d => {
              const on = active.has(d.id)
              return (
                <button key={d.id} onClick={() => onAdd(d)}
                  className="w-full flex items-center justify-between px-4 py-2 hover:bg-accent/8 transition-colors group">
                  <div className="text-left">
                    <div className="text-[12px] font-mono text-ink group-hover:text-accent-ink">{d.name}</div>
                    <div className="text-[9px] text-faint font-mono">{d.cat} · {d.pane === 'price' ? 'overlay' : 'pane'}</div>
                  </div>
                  <span className={`text-[10px] font-mono ${on ? 'text-accent' : 'text-faint group-hover:text-accent'}`}>{on ? 'added ✓' : '+ add'}</span>
                </button>
              )
            })}
          </div>
        </div>
        <div className="px-4 py-2 border-t border-border text-[9px] font-mono text-faint">
          {CATALOG.length} studies · common ones compute for real, the rest render as mock overlays · right-click any on the chart to add a condition
        </div>
      </div>
    </div>
  )
}
