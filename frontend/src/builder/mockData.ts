// Mock-only synthetic data + simplified indicators for the visual strategy
// builder. Deterministic (seeded) so every load shows the same setups. NOTHING
// here touches the live engine, broker, or real market data.

export interface Candle {
  time: number // unix seconds
  open: number
  high: number
  low: number
  close: number
}

export interface Zone {
  startIdx: number // bar index where the zone becomes visible
  side: 'bull' | 'bear'
  top: number
  bottom: number
}

// --- deterministic PRNG (mulberry32) ---------------------------------------
function rng(seed: number) {
  let a = seed >>> 0
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export type Timeframe = '1m' | '5m' | '15m' | '1h'
export const TF_SECONDS: Record<Timeframe, number> = { '1m': 60, '5m': 300, '15m': 900, '1h': 3600 }

// Generate bars for a timeframe: a gently rising trend with periodic pullbacks
// that dip to the rising EMA and resolve with a strong bullish "displacement"
// candle (which naturally forms a bullish FVG just above the EMA) — so an
// "EMA bounce into FVG" long setup reliably exists for the demo. Each timeframe
// gets its own deterministic series (seed varies by TF) and bar spacing.
export function generateBars(tf: Timeframe = '5m', seed = 20260616): Candle[] {
  const tfSec = TF_SECONDS[tf]
  const rand = rng(seed + tfSec)
  const bars: Candle[] = []
  const start = Math.floor(Date.UTC(2026, 5, 16, 13, 30, 0) / 1000) // arbitrary
  let price = 100
  const n = 200
  // setup anchors: indices where a pullback+bounce sequence is injected
  const setups = new Set([28, 60, 96, 132, 168])

  for (let i = 0; i < n; i++) {
    const time = start + i * tfSec
    let drift = 0.06 + (rand() - 0.5) * 0.12 // mild up-drift + noise
    let range = 0.5 + rand() * 0.5

    // Pullback: 4 bars before a setup anchor, push price down toward the EMA.
    for (const s of setups) {
      if (i >= s - 4 && i < s) drift = -0.28 - rand() * 0.12
      if (i === s) {
        // displacement candle: strong bullish gap-up body -> creates a FVG
        drift = 2.4 + rand() * 0.8
        range = 0.4
      }
    }

    const open = price
    const close = +(open + drift).toFixed(2)
    const hi = Math.max(open, close) + range * rand()
    const lo = Math.min(open, close) - range * rand()
    bars.push({
      time,
      open: +open.toFixed(2),
      high: +hi.toFixed(2),
      low: +lo.toFixed(2),
      close,
    })
    price = close
  }
  return bars
}

// --- indicators (simplified but plausible) ---------------------------------

export function ema(bars: Candle[], period: number): (number | null)[] {
  const k = 2 / (period + 1)
  const out: (number | null)[] = []
  let prev: number | null = null
  bars.forEach((b, i) => {
    if (i < period - 1) { out.push(null); return }
    if (prev === null) {
      const slice = bars.slice(i - period + 1, i + 1)
      prev = slice.reduce((s, c) => s + c.close, 0) / period
    } else {
      prev = b.close * k + prev * (1 - k)
    }
    out.push(+prev.toFixed(3))
  })
  return out
}

export function sma(vals: number[], period: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < vals.length; i++) {
    sum += vals[i]
    if (i >= period) sum -= vals[i - period]
    out.push(i >= period - 1 ? +(sum / period).toFixed(3) : null)
  }
  return out
}

export function rsi(bars: Candle[], period = 14): (number | null)[] {
  const out: (number | null)[] = [null]
  let gain = 0, loss = 0
  for (let i = 1; i < bars.length; i++) {
    const ch = bars[i].close - bars[i - 1].close
    const g = Math.max(ch, 0), l = Math.max(-ch, 0)
    if (i <= period) {
      gain += g; loss += l
      if (i === period) {
        const rs = loss === 0 ? 100 : gain / loss
        out.push(+(100 - 100 / (1 + rs)).toFixed(1))
      } else out.push(null)
    } else {
      gain = (gain * (period - 1) + g) / period
      loss = (loss * (period - 1) + l) / period
      const rs = loss === 0 ? 100 : gain / loss
      out.push(+(100 - 100 / (1 + rs)).toFixed(1))
    }
  }
  return out
}

export interface MacdPoint { macd: number; signal: number; hist: number }
export function macd(bars: Candle[]): (MacdPoint | null)[] {
  const e12 = ema(bars, 12), e26 = ema(bars, 26)
  const line: (number | null)[] = bars.map((_, i) =>
    e12[i] != null && e26[i] != null ? (e12[i] as number) - (e26[i] as number) : null)
  // signal = EMA(9) of the macd line
  const k = 2 / 10
  let sig: number | null = null
  return line.map((v) => {
    if (v == null) return null
    sig = sig === null ? v : v * k + sig * (1 - k)
    return { macd: +v.toFixed(3), signal: +sig.toFixed(3), hist: +(v - sig).toFixed(3) }
  })
}

// Bullish FVG: bar[i-1].high < bar[i+1].low -> gap, visible from i+1.
// Bearish FVG: bar[i-1].low  > bar[i+1].high.
export function detectFVGs(bars: Candle[]): Zone[] {
  const zones: Zone[] = []
  for (let i = 1; i < bars.length - 1; i++) {
    if (bars[i - 1].high < bars[i + 1].low) {
      zones.push({ startIdx: i + 1, side: 'bull', bottom: bars[i - 1].high, top: bars[i + 1].low })
    } else if (bars[i - 1].low > bars[i + 1].high) {
      zones.push({ startIdx: i + 1, side: 'bear', top: bars[i - 1].low, bottom: bars[i + 1].high })
    }
  }
  return zones
}

// Bullish OB: a down candle immediately followed by a strong up candle that
// closes above the down candle's high. Zone = the down candle's range.
export function detectOBs(bars: Candle[]): Zone[] {
  const zones: Zone[] = []
  for (let i = 0; i < bars.length - 1; i++) {
    const a = bars[i], b = bars[i + 1]
    if (a.close < a.open && b.close > b.open && b.close > a.high) {
      zones.push({ startIdx: i + 1, side: 'bull', top: a.high, bottom: a.low })
    }
  }
  return zones
}
