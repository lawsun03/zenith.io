import { useEffect } from 'react'
import { CONDITION_CATALOG, type ConditionDef, type ElementKind } from './rules'

interface Props {
  element: ElementKind
  x: number
  y: number
  onPick: (element: ElementKind, def: ConditionDef) => void
  onClose: () => void
}

const TITLE: Record<ElementKind, string> = {
  ema: 'EMA', fvg: 'Fair Value Gap', ob: 'Order Block', rsi: 'RSI', macd: 'MACD',
}

export function ContextMenu({ element, x, y, onPick, onClose }: Props) {
  useEffect(() => {
    const close = () => onClose()
    window.addEventListener('click', close)
    window.addEventListener('contextmenu', close)
    return () => { window.removeEventListener('click', close); window.removeEventListener('contextmenu', close) }
  }, [onClose])

  return (
    <div
      className="fixed z-50 min-w-[176px] bg-bg/95 backdrop-blur-md border border-border rounded-[8px] shadow-2xl overflow-hidden animate-fade-up"
      style={{ left: Math.min(x, window.innerWidth - 190), top: Math.min(y, window.innerHeight - 160) }}
      onClick={e => e.stopPropagation()}
    >
      <div className="px-3 py-1.5 text-[9px] uppercase tracking-widest text-faint border-b border-border font-mono">
        {TITLE[element]} → add condition
      </div>
      {CONDITION_CATALOG[element].map(def => (
        <button
          key={def.kind}
          onClick={() => onPick(element, def)}
          className="w-full text-left px-3 py-2 text-[11px] font-mono text-dim hover:text-accent-ink hover:bg-accent/10 transition-colors"
        >
          {def.label} <span className="text-faint">{TITLE[element] === 'EMA' || TITLE[element] === 'RSI' || TITLE[element] === 'MACD' ? element.toUpperCase() : ''}</span>
        </button>
      ))}
    </div>
  )
}
