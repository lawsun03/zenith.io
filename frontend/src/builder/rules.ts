// Mock rule model + pure evaluator for the visual strategy builder.
// Throwaway prototype — no connection to the live engine.

import type { Candle, MacdPoint, Zone } from './mockData'

export type ElementKind = 'ema' | 'fvg' | 'ob' | 'rsi' | 'macd'

export interface ConditionDef {
  kind: string
  label: string
}

// Contextual menu options per chart element (what right-click offers).
export const CONDITION_CATALOG: Record<ElementKind, ConditionDef[]> = {
  ema: [
    { kind: 'bounce', label: 'price bounces off' },
    { kind: 'cross_up', label: 'price crosses above' },
    { kind: 'cross_down', label: 'price crosses below' },
  ],
  fvg: [
    { kind: 'enter', label: 'price enters' },
    { kind: 'reject', label: 'price rejects from' },
    { kind: 'fill', label: 'price fills' },
  ],
  ob: [
    { kind: 'tap', label: 'price taps' },
    { kind: 'reject', label: 'price rejects from' },
  ],
  rsi: [
    { kind: 'oversold', label: 'oversold (<35)' },
    { kind: 'overbought', label: 'overbought (>65)' },
    { kind: 'cross50', label: 'crosses 50' },
  ],
  macd: [
    { kind: 'bull_cross', label: 'bullish cross' },
    { kind: 'bear_cross', label: 'bearish cross' },
  ],
}

const ELEMENT_LABEL: Record<ElementKind, string> = {
  ema: 'EMA', fvg: 'FVG', ob: 'OB', rsi: 'RSI', macd: 'MACD',
}

export interface Condition {
  id: string
  element: ElementKind
  kind: string
  label: string // human chip text, e.g. "price enters FVG"
}

export function makeCondition(element: ElementKind, def: ConditionDef): Condition {
  return {
    id: `${element}_${def.kind}_${Math.random().toString(36).slice(2, 7)}`,
    element,
    kind: def.kind,
    label: `${def.label} ${ELEMENT_LABEL[element]}`,
  }
}

export type StopBasis = 'fvg' | 'swing' | 'points'

export interface Rule {
  side: 'long' | 'short'
  joiner: 'AND' | 'OR'
  conditions: Condition[]
  stopBasis: StopBasis
  stopPoints: number // used when stopBasis === 'points'
  targetR: number
}

export const DEFAULT_RULE: Rule = {
  side: 'long', joiner: 'AND', conditions: [],
  stopBasis: 'fvg', stopPoints: 3, targetR: 3,
}

export interface EvalCtx {
  bars: Candle[]
  emaArr: (number | null)[]
  rsiArr: (number | null)[]
  macdArr: (MacdPoint | null)[]
  fvgs: Zone[]
  obs: Zone[]
}

const recentBullFvg = (ctx: EvalCtx, i: number): Zone | null => {
  for (let z = ctx.fvgs.length - 1; z >= 0; z--) {
    const zn = ctx.fvgs[z]
    if (zn.side === 'bull' && zn.startIdx <= i && i - zn.startIdx <= 30) return zn
  }
  return null
}
const recentBullOb = (ctx: EvalCtx, i: number): Zone | null => {
  for (let z = ctx.obs.length - 1; z >= 0; z--) {
    const zn = ctx.obs[z]
    if (zn.side === 'bull' && zn.startIdx <= i && i - zn.startIdx <= 30) return zn
  }
  return null
}

// Does a single condition hold exactly at bar i? (no lookback here)
function heldAt(cond: Condition, ctx: EvalCtx, i: number): boolean {
  if (i < 1) return false
  const b = ctx.bars[i], pb = ctx.bars[i - 1]
  const e = ctx.emaArr[i], pe = ctx.emaArr[i - 1]
  switch (cond.element) {
    case 'ema': {
      if (e == null) return false
      if (cond.kind === 'bounce') return b.low <= e * 1.004 && b.close >= e * 0.998 && b.close >= b.open
      if (cond.kind === 'cross_up') return pe != null && pb.close <= pe && b.close > e
      if (cond.kind === 'cross_down') return pe != null && pb.close >= pe && b.close < e
      return false
    }
    case 'fvg': {
      const z = recentBullFvg(ctx, i)
      if (!z) return false
      const overlaps = b.low <= z.top && b.high >= z.bottom
      if (cond.kind === 'enter') return overlaps
      if (cond.kind === 'reject') return overlaps && b.close > z.top && b.close >= b.open
      if (cond.kind === 'fill') return b.low <= z.bottom
      return false
    }
    case 'ob': {
      const z = recentBullOb(ctx, i)
      if (!z) return false
      const overlaps = b.low <= z.top && b.high >= z.bottom
      if (cond.kind === 'tap') return overlaps
      if (cond.kind === 'reject') return overlaps && b.close > z.top
      return false
    }
    case 'rsi': {
      const r = ctx.rsiArr[i], prv = ctx.rsiArr[i - 1]
      if (r == null) return false
      if (cond.kind === 'oversold') return r < 35
      if (cond.kind === 'overbought') return r > 65
      if (cond.kind === 'cross50') return prv != null && prv < 50 && r >= 50
      return false
    }
    case 'macd': {
      const m = ctx.macdArr[i], pm = ctx.macdArr[i - 1]
      if (m == null || pm == null) return false
      if (cond.kind === 'bull_cross') return pm.macd <= pm.signal && m.macd > m.signal
      if (cond.kind === 'bear_cross') return pm.macd >= pm.signal && m.macd < m.signal
      return false
    }
  }
}

// A condition is "satisfied" at i if it held within the last LOOKBACK bars —
// so a multi-leg setup (e.g. EMA bounce + FVG entry forming a bar or two apart)
// still co-occurs. Pragmatic mock semantics, not the real engine.
const LOOKBACK = 3
function satisfied(cond: Condition, ctx: EvalCtx, i: number): boolean {
  for (let j = Math.max(1, i - LOOKBACK + 1); j <= i; j++) {
    if (heldAt(cond, ctx, j)) return true
  }
  return false
}

export function evaluateRule(rule: Rule, ctx: EvalCtx, i: number): boolean {
  if (rule.conditions.length === 0) return false
  const results = rule.conditions.map(c => satisfied(c, ctx, i))
  return rule.joiner === 'AND' ? results.every(Boolean) : results.some(Boolean)
}

// Stop price for a freshly-entered long at bar i, per the rule's stop basis.
export function stopPriceFor(rule: Rule, ctx: EvalCtx, i: number, entry: number): number {
  if (rule.stopBasis === 'points') return entry - rule.stopPoints
  if (rule.stopBasis === 'swing') {
    const lows = ctx.bars.slice(Math.max(0, i - 10), i + 1).map(b => b.low)
    return Math.min(...lows) - 0.2
  }
  // fvg: just below the most recent bull FVG, else fall back to points
  const z = recentBullFvg(ctx, i)
  return z ? z.bottom - 0.2 : entry - rule.stopPoints
}
