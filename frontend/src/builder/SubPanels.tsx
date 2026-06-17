import type { MacdPoint } from './mockData'
import type { ElementKind } from './rules'

interface Common {
  onRequestMenu: (element: ElementKind, x: number, y: number) => void
  flash: ElementKind | null
}

function frame(flash: boolean) {
  return `relative h-[58px] bg-panel-hi border border-border rounded-[8px] overflow-hidden select-none ${flash ? 'ring-2 ring-accent' : ''}`
}

export function RsiPanel({ values, onRequestMenu, flash }: Common & { values: (number | null)[] }) {
  const pts = values.map((v, i) => ({ i, v })).filter(p => p.v != null) as { i: number; v: number }[]
  const n = values.length || 1
  const path = pts.map(p => `${(p.i / (n - 1)) * 100},${100 - p.v}`).join(' ')
  const last = pts.length ? pts[pts.length - 1].v : null
  return (
    <div
      className={frame(flash === 'rsi')}
      onContextMenu={e => { e.preventDefault(); onRequestMenu('rsi', e.clientX, e.clientY) }}
    >
      <div className="absolute top-1 left-2 text-[9px] font-mono text-faint z-10">RSI(14){last != null && <span className="text-dim ml-1">{last.toFixed(0)}</span>}</div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="w-full h-full">
        <line x1="0" y1="35" x2="100" y2="35" stroke="rgba(248,113,113,0.25)" strokeWidth="0.5" />
        <line x1="0" y1="65" x2="100" y2="65" stroke="rgba(110,231,183,0.25)" strokeWidth="0.5" />
        <polyline points={path} fill="none" stroke="#7aa2f7" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="absolute bottom-1 right-2 text-[8px] font-mono text-faint/60 z-10">right-click ↗</div>
    </div>
  )
}

export function MacdPanel({ values, onRequestMenu, flash }: Common & { values: (MacdPoint | null)[] }) {
  const pts = values.map((v, i) => ({ i, v })).filter(p => p.v != null) as { i: number; v: MacdPoint }[]
  const n = values.length || 1
  const all = pts.flatMap(p => [p.v.macd, p.v.signal, p.v.hist])
  const max = Math.max(0.001, ...all.map(Math.abs))
  const y = (val: number) => 50 - (val / max) * 45
  const macdPath = pts.map(p => `${(p.i / (n - 1)) * 100},${y(p.v.macd)}`).join(' ')
  const sigPath = pts.map(p => `${(p.i / (n - 1)) * 100},${y(p.v.signal)}`).join(' ')
  return (
    <div
      className={frame(flash === 'macd')}
      onContextMenu={e => { e.preventDefault(); onRequestMenu('macd', e.clientX, e.clientY) }}
    >
      <div className="absolute top-1 left-2 text-[9px] font-mono text-faint z-10">MACD(12,26,9)</div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="w-full h-full">
        <line x1="0" y1="50" x2="100" y2="50" stroke="rgba(255,255,255,0.08)" strokeWidth="0.5" />
        {pts.map(p => (
          <line key={p.i} x1={(p.i / (n - 1)) * 100} y1="50" x2={(p.i / (n - 1)) * 100} y2={y(p.v.hist)}
            stroke={p.v.hist >= 0 ? 'rgba(62,224,165,0.5)' : 'rgba(248,113,113,0.5)'} strokeWidth="1" vectorEffect="non-scaling-stroke" />
        ))}
        <polyline points={macdPath} fill="none" stroke="#6ee7b7" strokeWidth="1" vectorEffect="non-scaling-stroke" />
        <polyline points={sigPath} fill="none" stroke="#f59e0b" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="absolute bottom-1 right-2 text-[8px] font-mono text-faint/60 z-10">right-click ↗</div>
    </div>
  )
}
