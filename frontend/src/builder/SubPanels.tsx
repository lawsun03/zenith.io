import type { IKind } from './indicators'
import type { MenuTarget } from './rules'

interface Props {
  instId: string
  name: string
  ikind: IKind
  sub: { series: (number | null)[]; guides: { y: number; color: string }[]; min: number; max: number }
  cursor: number
  onRequestMenu: (t: MenuTarget, x: number, y: number) => void
  flash: boolean
}

export function GenericSubPanel({ instId, name, ikind, sub, cursor, onRequestMenu, flash }: Props) {
  const series = sub.series.slice(0, cursor + 1)
  const span = sub.max - sub.min || 1
  const ny = (v: number) => 100 - ((v - sub.min) / span) * 100
  const pts = series.map((v, i) => ({ i, v })).filter(p => p.v != null) as { i: number; v: number }[]
  const n = series.length || 1
  const path = pts.map(p => `${(p.i / (n - 1)) * 100},${ny(p.v)}`).join(' ')
  const last = pts.length ? pts[pts.length - 1].v : null
  return (
    <div
      className={`relative h-[56px] bg-panel-hi border rounded-[8px] overflow-hidden select-none shrink-0 ${flash ? 'border-accent' : 'border-border'}`}
      onContextMenu={e => { e.preventDefault(); onRequestMenu({ kind: 'indicator', instId, ikind, name }, e.clientX, e.clientY) }}
    >
      <div className="absolute top-1 left-2 text-[9px] font-mono text-faint z-10">
        {name}{last != null && <span className="text-dim ml-1">{last.toFixed(1)}</span>}
      </div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="w-full h-full">
        {sub.guides.map((g, i) => <line key={i} x1="0" y1={ny(g.y)} x2="100" y2={ny(g.y)} stroke={g.color} strokeWidth="0.5" />)}
        <polyline points={path} fill="none" stroke="#7aa2f7" strokeWidth="1" vectorEffect="non-scaling-stroke" />
      </svg>
      <div className="absolute bottom-1 right-2 text-[8px] font-mono text-faint/60 z-10">right-click ↗</div>
    </div>
  )
}
