import { useEffect } from 'react'
import { conditionsFor, type CondDef } from './indicators'
import { drawingConditions } from './drawings'
import type { MenuTarget, CondSource } from './rules'

interface Props {
  target: MenuTarget
  x: number
  y: number
  onPick: (source: CondSource, def: CondDef, name: string) => void
  onClose: () => void
}

const DETECTOR_CONDS: Record<string, CondDef[]> = {
  fvg: [{ kind: 'enter', label: 'price enters' }, { kind: 'reject', label: 'price rejects from' }, { kind: 'fill', label: 'price fills' }],
  ob: [{ kind: 'tap', label: 'price taps' }, { kind: 'reject', label: 'price rejects from' }],
  ifvg: [{ kind: 'enter', label: 'price retests' }, { kind: 'reject', label: 'price rejects from' }, { kind: 'fill', label: 'price fills through' }],
  liquidity: [{ kind: 'sweep_high', label: 'sweeps prior high' }, { kind: 'sweep_low', label: 'sweeps prior low' }, { kind: 'break_high', label: 'breaks swing high' }, { kind: 'break_low', label: 'breaks swing low' }],
}

export function ContextMenu({ target, x, y, onPick, onClose }: Props) {
  useEffect(() => {
    const close = () => onClose()
    window.addEventListener('click', close); window.addEventListener('contextmenu', close)
    return () => { window.removeEventListener('click', close); window.removeEventListener('contextmenu', close) }
  }, [onClose])

  let defs: CondDef[]
  let source: CondSource
  if (target.kind === 'detector') { defs = DETECTOR_CONDS[target.det]; source = { type: 'detector', det: target.det } }
  else if (target.kind === 'indicator') { defs = conditionsFor(target.ikind); source = { type: 'indicator', instId: target.instId, ikind: target.ikind } }
  else { defs = drawingConditions(target.dtype); source = { type: 'drawing', drawingId: target.drawingId, dtype: target.dtype } }

  return (
    <div className="fixed z-[60] min-w-[188px] bg-bg/95 backdrop-blur-md border border-border rounded-[8px] shadow-2xl overflow-hidden animate-fade-up"
      style={{ left: Math.min(x, window.innerWidth - 200), top: Math.min(y, window.innerHeight - 180) }} onClick={e => e.stopPropagation()}>
      <div className="px-3 py-1.5 text-[9px] uppercase tracking-widest text-faint border-b border-border font-mono">{target.name} → add condition</div>
      {defs.map(def => (
        <button key={def.kind} onClick={() => onPick(source, def, target.name)}
          className="w-full text-left px-3 py-2 text-[11px] font-mono text-dim hover:text-accent-ink hover:bg-accent/10 transition-colors">
          {def.label}
        </button>
      ))}
    </div>
  )
}
