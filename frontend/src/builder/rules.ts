// Mock rule model + pure evaluator. Conditions reference a "source": a built-in
// FVG/OB detector, a catalog indicator instance, or a user drawing. Throwaway.

import type { Candle, Zone, Swing } from './mockData'
import type { Computed, IKind, CondDef } from './indicators'
import { indicatorHeld } from './indicators'
import type { Drawing, DrawingType } from './drawings'
import { drawingHeld } from './drawings'

export type Detector = 'fvg' | 'ob' | 'ifvg' | 'liquidity'
export type CondSource =
  | { type: 'detector'; det: Detector }
  | { type: 'indicator'; instId: string; ikind: IKind }
  | { type: 'drawing'; drawingId: string; dtype: DrawingType }

// what the right-click context menu was opened on (carries display name)
export type MenuTarget =
  | { kind: 'detector'; det: Detector; name: string }
  | { kind: 'indicator'; instId: string; ikind: IKind; name: string }
  | { kind: 'drawing'; drawingId: string; dtype: DrawingType; name: string }

export interface Condition {
  id: string
  label: string
  source: CondSource
  predKind: string
}

let _seq = 0
export function makeCondition(source: CondSource, def: CondDef, elementName: string): Condition {
  return { id: `c${_seq++}`, source, predKind: def.kind, label: `${def.label} · ${elementName}` }
}

export type StopBasis = 'fvg' | 'swing' | 'points'
export interface Rule {
  side: 'long' | 'short'
  joiner: 'AND' | 'OR'
  conditions: Condition[]
  stopBasis: StopBasis
  stopPoints: number
  targetR: number
}
export const DEFAULT_RULE: Rule = { side: 'long', joiner: 'AND', conditions: [], stopBasis: 'fvg', stopPoints: 3, targetR: 3 }

export interface EvalCtx {
  bars: Candle[]
  fvgs: Zone[]
  obs: Zone[]
  ifvgs: Zone[]
  swings: Swing[]
  liqLookback: number
  computed: Record<string, Computed>
  drawings: Record<string, Drawing>
}

const recentBull = (zones: Zone[], i: number): Zone | null => {
  for (let z = zones.length - 1; z >= 0; z--) {
    const zn = zones[z]
    if (zn.side === 'bull' && zn.startIdx <= i && i - zn.startIdx <= 30) return zn
  }
  return null
}

function recentSwing(swings: Swing[], side: 'high' | 'low', i: number, lookback: number): Swing | null {
  for (let s = swings.length - 1; s >= 0; s--) {
    const sw = swings[s]
    if (sw.side === side && sw.idx + lookback <= i && sw.idx < i && i - sw.idx <= 40) return sw
  }
  return null
}

function detectorHeld(det: Detector, predKind: string, ctx: EvalCtx, i: number): boolean {
  if (i < 1) return false
  const b = ctx.bars[i], pb = ctx.bars[i - 1]
  if (det === 'liquidity') {
    if (predKind === 'sweep_high' || predKind === 'break_high') {
      const sw = recentSwing(ctx.swings, 'high', i, ctx.liqLookback); if (!sw) return false
      if (predKind === 'sweep_high') return b.high > sw.price && b.close < sw.price
      return pb.close <= sw.price && b.close > sw.price
    }
    const sw = recentSwing(ctx.swings, 'low', i, ctx.liqLookback); if (!sw) return false
    if (predKind === 'sweep_low') return b.low < sw.price && b.close > sw.price
    return pb.close >= sw.price && b.close < sw.price // break_low
  }
  const zones = det === 'fvg' ? ctx.fvgs : det === 'ob' ? ctx.obs : ctx.ifvgs
  const z = recentBull(zones, i)
  if (!z) return false
  const overlaps = b.low <= z.top && b.high >= z.bottom
  switch (predKind) {
    case 'enter': case 'tap': return overlaps
    case 'reject': return overlaps && b.close > z.top && b.close >= b.open
    case 'fill': return b.low <= z.bottom
  }
  return false
}

function heldAt(cond: Condition, ctx: EvalCtx, i: number): boolean {
  const s = cond.source
  if (s.type === 'detector') return detectorHeld(s.det, cond.predKind, ctx, i)
  if (s.type === 'indicator') {
    const c = ctx.computed[s.instId]
    return c ? indicatorHeld(c, cond.predKind, ctx.bars, i) : false
  }
  const d = ctx.drawings[s.drawingId]
  return d ? drawingHeld(d, cond.predKind, ctx.bars, i) : false
}

const LOOKBACK = 3
function satisfied(cond: Condition, ctx: EvalCtx, i: number): boolean {
  for (let j = Math.max(1, i - LOOKBACK + 1); j <= i; j++) if (heldAt(cond, ctx, j)) return true
  return false
}

export function evaluateRule(rule: Rule, ctx: EvalCtx, i: number): boolean {
  if (rule.conditions.length === 0) return false
  const r = rule.conditions.map(c => satisfied(c, ctx, i))
  return rule.joiner === 'AND' ? r.every(Boolean) : r.some(Boolean)
}

export function stopPriceFor(rule: Rule, ctx: EvalCtx, i: number, entry: number): number {
  if (rule.stopBasis === 'points') return entry - rule.stopPoints
  if (rule.stopBasis === 'swing') {
    const lows = ctx.bars.slice(Math.max(0, i - 10), i + 1).map(b => b.low)
    return Math.min(...lows) - 0.2
  }
  const z = recentBull(ctx.fvgs, i)
  return z ? z.bottom - 0.2 : entry - rule.stopPoints
}
