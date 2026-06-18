// Mock backtest runner: a chunked single forward pass over a long synthetic
// series so the UI can show progress + an ETA without freezing. Reuses the same
// rule evaluator, stop logic, and straddle engine as the live builder.

import type { Candle, Swing } from './mockData'
import type { EvalCtx, Rule } from './rules'
import { evaluateRule, stopPriceFor } from './rules'
import type { NewsEvent } from './news'
import { simulateStraddle, type StraddleConfig } from './straddle'

export const PERIODS = { '6m': 3000, '1y': 6000, '3y': 18000, '5y': 30000 } as const
export type Period = keyof typeof PERIODS

export interface BTTrade { entryIdx: number; exitIdx: number; entry: number; exit: number; pnlR: number; win: boolean; side: 'long' | 'short'; kind: 'rule' | 'straddle' }
export interface BTResult { trades: BTTrade[]; equity: number[]; netR: number; winRate: number; maxDD: number; bars: number; ms: number }

export async function runBacktest(
  bars: Candle[],
  ctx: EvalCtx,
  rule: Rule,
  passesFilters: (b: Candle, i: number) => boolean,
  straddleCfg: StraddleConfig,
  straddleEvents: NewsEvent[],
  swings: Swing[],
  onProgress: (frac: number, etaMs: number) => void,
): Promise<BTResult> {
  const N = bars.length
  const t0 = performance.now()
  const trades: BTTrade[] = []
  let open: { side: 'long' | 'short'; entry: number; stop: number; target: number; at: number } | null = null
  const CHUNK = 1500

  for (let i = 1; i < N; i++) {
    const b = bars[i]
    if (open) {
      const long = open.side === 'long'
      const hitStop = long ? b.low <= open.stop : b.high >= open.stop
      const hitTgt = long ? b.high >= open.target : b.low <= open.target
      const exit = hitStop ? open.stop : hitTgt ? open.target : null
      if (exit != null) {
        const risk = Math.abs(open.entry - open.stop) || 1
        const pnlR = (long ? exit - open.entry : open.entry - exit) / risk
        trades.push({ entryIdx: open.at, exitIdx: i, entry: open.entry, exit, pnlR, win: pnlR >= 0, side: open.side, kind: 'rule' })
        open = null
      }
    } else if (passesFilters(b, i) && evaluateRule(rule, ctx, i)) {
      const long = rule.side === 'long'; const entry = b.close
      const risk = Math.max(0.3, Math.abs(entry - stopPriceFor(rule, ctx, i, entry)))
      open = { side: rule.side, entry, stop: long ? entry - risk : entry + risk, target: long ? entry + rule.targetR * risk : entry - rule.targetR * risk, at: i }
    }
    if (i % CHUNK === 0) {
      const elapsed = performance.now() - t0
      onProgress(i / N, (elapsed / i) * (N - i))
      await new Promise(r => setTimeout(r, 0)) // yield to the UI
    }
  }

  if (straddleCfg.enabled) {
    const ss = simulateStraddle(bars, straddleEvents, swings, straddleCfg, N - 1)
    ss.trades.forEach(t => trades.push({ entryIdx: -1, exitIdx: -1, entry: t.entry, exit: t.exit, pnlR: t.pnlR, win: t.win, side: t.side, kind: 'straddle' }))
  }

  trades.sort((a, b) => (a.exitIdx < 0 ? 1 : b.exitIdx < 0 ? -1 : a.exitIdx - b.exitIdx))
  let bal = 0, peak = 0, maxDD = 0
  const equity: number[] = []
  for (const t of trades) { bal += t.pnlR; equity.push(+bal.toFixed(2)); peak = Math.max(peak, bal); maxDD = Math.min(maxDD, bal - peak) }
  const wins = trades.filter(t => t.win).length
  onProgress(1, 0)
  return { trades, equity, netR: +bal.toFixed(1), winRate: trades.length ? Math.round(wins / trades.length * 100) : 0, maxDD: +maxDD.toFixed(1), bars: N, ms: performance.now() - t0 }
}
