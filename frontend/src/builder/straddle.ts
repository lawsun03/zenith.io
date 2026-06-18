// Mock event-straddle engine. The user arms an OCO bracket a few bars before a
// news event: buy above / sell below, anchored either to price ± ticks or to a
// recent swing high/low ± offset. First side to trigger fills; the other cancels.
// Mirrors the bot's news-straddle. Throwaway prototype.

import type { Candle, Swing } from './mockData'
import type { NewsEvent } from './news'
import type { ChartMarker } from './BuilderChart'

export const TICK = 0.25

export interface StraddleConfig {
  enabled: boolean
  event: string           // 'CPI' | 'FOMC' | 'NFP' | 'all'
  armBars: number         // bars before the event to place the orders
  anchor: 'price' | 'swing'
  offsetTicks: number
  targetR: number
}
export const DEFAULT_STRADDLE: StraddleConfig = { enabled: false, event: 'all', armBars: 3, anchor: 'price', offsetTicks: 20, targetR: 3 }

export interface StraddleTrade { entry: number; exit: number; pnlR: number; win: boolean; side: 'long' | 'short' }
export interface StraddleLevel { t1: number; t2: number; price: number; color: string; label: string }

const recentSwing = (swings: Swing[], side: 'high' | 'low', before: number): Swing | null => {
  for (let s = swings.length - 1; s >= 0; s--) if (swings[s].side === side && swings[s].idx < before) return swings[s]
  return null
}

export function simulateStraddle(bars: Candle[], events: NewsEvent[], swings: Swing[], cfg: StraddleConfig, cursor: number) {
  const trades: StraddleTrade[] = []; const markers: ChartMarker[] = []; const levels: StraddleLevel[] = []
  if (!cfg.enabled) return { trades, markers, levels }
  const off = cfg.offsetTicks * TICK

  for (const ev of events) {
    if (cfg.event !== 'all' && cfg.event !== ev.name) continue
    const arm = ev.idx - cfg.armBars
    if (arm < 1 || arm > cursor || !bars[arm]) continue

    let longEntry: number, shortEntry: number
    if (cfg.anchor === 'swing') {
      const sh = recentSwing(swings, 'high', arm), sl = recentSwing(swings, 'low', arm)
      longEntry = (sh ? sh.price : bars[arm].close) + off
      shortEntry = (sl ? sl.price : bars[arm].close) - off
    } else {
      longEntry = bars[arm].close + off
      shortEntry = bars[arm].close - off
    }
    const watchEnd = Math.min(cursor, ev.idx + 8)
    levels.push({ t1: bars[arm].time, t2: bars[Math.min(watchEnd, bars.length - 1)].time, price: longEntry, color: 'rgba(62,224,165,0.6)', label: `${ev.name} buy` })
    levels.push({ t1: bars[arm].time, t2: bars[Math.min(watchEnd, bars.length - 1)].time, price: shortEntry, color: 'rgba(248,113,113,0.6)', label: `${ev.name} sell` })

    // watch for a trigger
    let filled: { side: 'long' | 'short'; entry: number; stop: number; target: number; at: number } | null = null
    const range = Math.max(off, longEntry - shortEntry)
    for (let j = arm + 1; j <= watchEnd && j < bars.length; j++) {
      if (bars[j].high >= longEntry) { filled = { side: 'long', entry: longEntry, stop: shortEntry, target: longEntry + cfg.targetR * range, at: j }; break }
      if (bars[j].low <= shortEntry) { filled = { side: 'short', entry: shortEntry, stop: longEntry, target: shortEntry - cfg.targetR * range, at: j }; break }
    }
    if (!filled) continue
    const long = filled.side === 'long'
    markers.push({ time: bars[filled.at].time, position: long ? 'belowBar' : 'aboveBar', shape: long ? 'arrowUp' : 'arrowDown', color: '#fbbf24', text: `${ev.name} ${long ? 'L' : 'S'}` })

    // resolve
    for (let k = filled.at + 1; k <= cursor && k < bars.length; k++) {
      const b = bars[k]
      const hitStop = long ? b.low <= filled.stop : b.high >= filled.stop
      const hitTgt = long ? b.high >= filled.target : b.low <= filled.target
      const exit = hitStop ? filled.stop : hitTgt ? filled.target : null
      if (exit != null) {
        const risk = Math.abs(filled.entry - filled.stop) || 1
        const pnlR = (long ? exit - filled.entry : filled.entry - exit) / risk
        trades.push({ entry: filled.entry, exit, pnlR, win: pnlR >= 0, side: filled.side })
        markers.push({ time: b.time, position: long ? 'aboveBar' : 'belowBar', shape: 'circle', color: pnlR >= 0 ? '#3ee0a5' : '#f87171', text: `${pnlR >= 0 ? '+' : ''}${pnlR.toFixed(1)}R` })
        break
      }
    }
  }
  return { trades, markers, levels }
}

export function straddleLabel(cfg: StraddleConfig): string {
  if (!cfg.enabled) return ''
  const a = cfg.anchor === 'swing' ? `swing±${cfg.offsetTicks}t` : `±${cfg.offsetTicks}t`
  return `straddle ${cfg.event} (${cfg.armBars}b before, ${a})`
}
