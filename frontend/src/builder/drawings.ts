// User-drawn objects for the visual strategy builder (mock). Rectangles (manual
// FVG zones), trendlines, and horizontal levels — each becomes a trade condition.

import type { Candle } from './mockData'
import type { CondDef } from './indicators'

export type DrawingType = 'rect' | 'trend' | 'hline'

export interface Drawing {
  id: string
  type: DrawingType
  // time/price anchors. rect: opposite corners; trend: two points; hline: p1 only.
  t1: number; p1: number
  t2?: number; p2?: number
}

export const DRAWING_LABEL: Record<DrawingType, string> = { rect: 'Rectangle', trend: 'Trendline', hline: 'Horizontal Line' }

export function drawingConditions(type: DrawingType): CondDef[] {
  if (type === 'rect') return [
    { kind: 'enter', label: 'price enters' },
    { kind: 'reject', label: 'price rejects from' },
    { kind: 'fill', label: 'price fills' },
  ]
  if (type === 'trend') return [
    { kind: 'cross', label: 'price crosses' },
    { kind: 'bounce', label: 'price bounces off' },
  ]
  return [
    { kind: 'cross_up', label: 'price crosses above' },
    { kind: 'cross_down', label: 'price crosses below' },
    { kind: 'reach', label: 'price reaches' },
  ]
}

// value of a trendline at an arbitrary time (linear, projected beyond anchors)
function trendValueAt(d: Drawing, t: number): number {
  if (d.t2 == null || d.p2 == null || d.t2 === d.t1) return d.p1
  return d.p1 + (d.p2 - d.p1) * ((t - d.t1) / (d.t2 - d.t1))
}

export function drawingHeld(d: Drawing, predKind: string, bars: Candle[], i: number): boolean {
  if (i < 1) return false
  const b = bars[i], pb = bars[i - 1]
  if (d.type === 'rect') {
    const top = Math.max(d.p1, d.p2 ?? d.p1), bottom = Math.min(d.p1, d.p2 ?? d.p1)
    const overlaps = b.low <= top && b.high >= bottom
    if (predKind === 'enter') return overlaps
    if (predKind === 'reject') return overlaps && b.close > top && b.close >= b.open
    if (predKind === 'fill') return b.low <= bottom
    return false
  }
  if (d.type === 'hline') {
    const p = d.p1
    if (predKind === 'cross_up') return pb.close <= p && b.close > p
    if (predKind === 'cross_down') return pb.close >= p && b.close < p
    if (predKind === 'reach') return b.low <= p && b.high >= p
    return false
  }
  // trend
  const line = trendValueAt(d, b.time), pline = trendValueAt(d, pb.time)
  if (predKind === 'cross') return (pb.close - pline) * (b.close - line) < 0
  if (predKind === 'bounce') return b.low <= line * 1.003 && b.close >= line * 0.998 && b.close >= b.open
  return false
}
