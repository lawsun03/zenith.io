// Mock indicator engine for the visual strategy builder. A large catalog
// (TradingView-style) where the common studies compute for real and the long
// tail render as plausible generic overlays — enough to FEEL like the full
// library and be wired into conditions. Throwaway prototype, nothing live.

import type { Candle } from './mockData'
import { ema, sma, rsi as rsiCalc, macd as macdCalc } from './mockData'

export type Pane = 'price' | 'sub'
export type IKind = 'ma' | 'band' | 'osc' | 'trend' | 'vol' | 'vola'

export interface IndicatorDef {
  id: string
  name: string
  cat: string
  pane: Pane
  kind: IKind
  real?: boolean
}

export interface LinePoint { time: number; value: number }
export interface Computed {
  pane: Pane
  kind: IKind
  priceLines: { color: string; dash?: boolean; data: LinePoint[] }[]
  sub?: { series: (number | null)[]; guides: { y: number; color: string }[]; min: number; max: number }
  evalMain: (number | null)[]
  evalUpper?: (number | null)[]
  evalLower?: (number | null)[]
}

// --- catalog ---------------------------------------------------------------
const MA = (id: string, name: string): IndicatorDef => ({ id, name, cat: 'Moving Averages', pane: 'price', kind: 'ma' })
const OSC = (id: string, name: string): IndicatorDef => ({ id, name, cat: 'Oscillators', pane: 'sub', kind: 'osc' })
const BAND = (id: string, name: string): IndicatorDef => ({ id, name, cat: 'Bands & Channels', pane: 'price', kind: 'band' })
const VOL = (id: string, name: string): IndicatorDef => ({ id, name, cat: 'Volume', pane: 'sub', kind: 'vol' })
const TREND = (id: string, name: string): IndicatorDef => ({ id, name, cat: 'Trend', pane: 'price', kind: 'trend' })
const VOLA = (id: string, name: string): IndicatorDef => ({ id, name, cat: 'Volatility', pane: 'sub', kind: 'vola' })

export const CATALOG: IndicatorDef[] = [
  // Moving Averages
  MA('ema', 'Exponential MA'), MA('sma', 'Simple MA'), MA('wma', 'Weighted MA'),
  MA('dema', 'Double EMA'), MA('tema', 'Triple EMA'), MA('hma', 'Hull MA'),
  MA('vwma', 'Volume Weighted MA'), MA('smma', 'Smoothed MA'), MA('alma', 'Arnaud Legoux MA'),
  MA('kama', 'Kaufman Adaptive MA'), MA('lsma', 'Least Squares MA'), MA('zlema', 'Zero Lag EMA'),
  MA('t3', 'Tillson T3'), MA('mcginley', 'McGinley Dynamic'), MA('frama', 'Fractal Adaptive MA'),
  // Oscillators
  OSC('rsi', 'Relative Strength Index'), OSC('macd', 'MACD'), OSC('stoch', 'Stochastic'),
  OSC('stochrsi', 'Stochastic RSI'), OSC('cci', 'Commodity Channel Index'), OSC('williams_r', 'Williams %R'),
  OSC('roc', 'Rate of Change'), OSC('mom', 'Momentum'), OSC('uo', 'Ultimate Oscillator'),
  OSC('ao', 'Awesome Oscillator'), OSC('trix', 'TRIX'), OSC('dpo', 'Detrended Price'),
  OSC('fisher', 'Fisher Transform'), OSC('rvi', 'Relative Vigor Index'), OSC('cmo', 'Chande Momentum'),
  OSC('ppo', 'Percentage Price Osc'), OSC('coppock', 'Coppock Curve'), OSC('kst', 'Know Sure Thing'),
  OSC('smi', 'Stochastic Momentum'), OSC('elder', 'Elder Ray'), OSC('schaff', 'Schaff Trend Cycle'),
  // Bands & Channels
  BAND('bb', 'Bollinger Bands'), BAND('keltner', 'Keltner Channels'), BAND('donchian', 'Donchian Channels'),
  BAND('envelope', 'MA Envelope'), BAND('starc', 'STARC Bands'), BAND('price_channel', 'Price Channel'),
  BAND('bbw', 'Bollinger Bands Width'),
  // Volume
  VOL('volume', 'Volume'), VOL('obv', 'On Balance Volume'), VOL('vwap', 'VWAP'),
  VOL('mfi', 'Money Flow Index'), VOL('cmf', 'Chaikin Money Flow'), VOL('ad', 'Accumulation/Distribution'),
  VOL('eom', 'Ease of Movement'), VOL('pvt', 'Price Volume Trend'), VOL('vo', 'Volume Oscillator'),
  VOL('nvi', 'Negative Volume Index'), VOL('klinger', 'Klinger Oscillator'),
  // Trend
  TREND('adx', 'Average Directional Index'), TREND('psar', 'Parabolic SAR'), TREND('supertrend', 'Supertrend'),
  TREND('aroon', 'Aroon'), TREND('vortex', 'Vortex'), TREND('ichimoku', 'Ichimoku Cloud'),
  TREND('dmi', 'Directional Movement'), TREND('trend_str', 'Trend Strength'), TREND('linreg', 'Linear Regression'),
  TREND('zigzag', 'ZigZag'), TREND('chande_kroll', 'Chande Kroll Stop'),
  // Volatility
  VOLA('atr', 'Average True Range'), VOLA('stddev', 'Standard Deviation'), VOLA('chaikin_vol', 'Chaikin Volatility'),
  VOLA('hist_vol', 'Historical Volatility'), VOLA('mass', 'Mass Index'), VOLA('rvi_vol', 'Relative Volatility'),
  VOLA('ui', 'Ulcer Index'),
  // Bill Williams
  { id: 'alligator', name: 'Alligator', cat: 'Bill Williams', pane: 'price', kind: 'ma' },
  { id: 'fractals', name: 'Fractals', cat: 'Bill Williams', pane: 'price', kind: 'trend' },
  { id: 'gator', name: 'Gator Oscillator', cat: 'Bill Williams', pane: 'sub', kind: 'osc' },
  { id: 'bw_mfi', name: 'Market Facilitation', cat: 'Bill Williams', pane: 'sub', kind: 'vol' },
]

export const CATEGORIES = Array.from(new Set(CATALOG.map(d => d.cat)))
export const getDef = (id: string) => CATALOG.find(d => d.id === id)!

const PALETTE = ['#6ee7b7', '#7aa2f7', '#f59e0b', '#e879f9', '#34d399', '#fbbf24', '#60a5fa', '#fb7185']
export const colorFor = (id: string) => PALETTE[Math.abs([...id].reduce((a, c) => a + c.charCodeAt(0), 0)) % PALETTE.length]

// --- compute ---------------------------------------------------------------
const closes = (bars: Candle[]) => bars.map(b => b.close)
const toLine = (bars: Candle[], vals: (number | null)[], color: string, dash = false) =>
  ({ color, dash, data: bars.map((b, i) => ({ time: b.time, value: vals[i] })).filter(p => p.value != null) as LinePoint[] })

// generic 0-100 oscillator from close momentum (for the long tail)
function genericOsc(bars: Candle[], lag = 10): (number | null)[] {
  const c = closes(bars)
  const base = sma(c, 14)
  return c.map((v, i) => {
    if (base[i] == null || i < lag) return null
    const z = (v - (base[i] as number)) / (Math.abs(base[i] as number) * 0.01 + 0.001)
    return +Math.max(0, Math.min(100, 50 + z * 6)).toFixed(1)
  })
}
function wma(vals: number[], p: number): (number | null)[] {
  const out: (number | null)[] = []
  const denom = (p * (p + 1)) / 2
  for (let i = 0; i < vals.length; i++) {
    if (i < p - 1) { out.push(null); continue }
    let s = 0
    for (let k = 0; k < p; k++) s += vals[i - k] * (p - k)
    out.push(+(s / denom).toFixed(3))
  }
  return out
}

const SUB_GUIDE = (kind: IKind): { y: number; color: string }[] =>
  kind === 'osc' ? [{ y: 30, color: 'rgba(248,113,113,0.25)' }, { y: 70, color: 'rgba(110,231,183,0.25)' }] : [{ y: 50, color: 'rgba(255,255,255,0.08)' }]

export function computeIndicator(def: IndicatorDef, bars: Candle[], period = 20): Computed {
  const c = closes(bars)
  const col = colorFor(def.id)

  // ---- price-pane single MAs ----
  if (def.pane === 'price' && def.kind === 'ma') {
    let v: (number | null)[]
    if (def.id === 'sma') v = sma(c, period)
    else if (def.id === 'wma') v = wma(c, period)
    else if (def.id === 'ema') v = ema(bars, period)
    else v = ema(bars, period) // plausible generic for the MA long tail
    return { pane: 'price', kind: 'ma', priceLines: [toLine(bars, v, col)], evalMain: v }
  }

  // ---- bands ----
  if (def.kind === 'band') {
    const basis = sma(c, period)
    const dev = c.map((_, i) => {
      if (i < period - 1) return null
      const slice = c.slice(i - period + 1, i + 1)
      const m = slice.reduce((a, b) => a + b, 0) / period
      return Math.sqrt(slice.reduce((a, b) => a + (b - m) ** 2, 0) / period)
    })
    const mult = def.id === 'keltner' ? 1.5 : 2
    const upper = basis.map((b, i) => (b != null && dev[i] != null ? +(b + mult * (dev[i] as number)).toFixed(3) : null))
    const lower = basis.map((b, i) => (b != null && dev[i] != null ? +(b - mult * (dev[i] as number)).toFixed(3) : null))
    return {
      pane: 'price', kind: 'band',
      priceLines: [toLine(bars, upper, col, true), toLine(bars, basis, col), toLine(bars, lower, col, true)],
      evalMain: basis, evalUpper: upper, evalLower: lower,
    }
  }

  // ---- trend lines on price (psar/supertrend/etc -> a trailing line) ----
  if (def.pane === 'price' && def.kind === 'trend') {
    const v = ema(bars, Math.max(5, Math.round(period / 2)))
    return { pane: 'price', kind: 'trend', priceLines: [toLine(bars, v, col, true)], evalMain: v }
  }

  // ---- sub-pane (oscillators / volume / volatility) ----
  let series: (number | null)[]
  if (def.id === 'rsi') series = rsiCalc(bars)
  else if (def.id === 'macd') series = macdCalc(bars).map(m => (m ? m.macd : null))
  else if (def.id === 'williams_r') series = c.map((_, i) => {
    if (i < 14) return null
    const s = bars.slice(i - 13, i + 1)
    const hh = Math.max(...s.map(b => b.high)), ll = Math.min(...s.map(b => b.low))
    return hh === ll ? -50 : +(((hh - bars[i].close) / (hh - ll)) * -100 + 100).toFixed(1)
  })
  else if (def.id === 'roc' || def.id === 'mom') series = c.map((v, i) => (i < 10 ? null : +(((v - c[i - 10]) / c[i - 10]) * 100 + 50).toFixed(1)))
  else if (def.id === 'stoch' || def.id === 'stochrsi') series = c.map((_, i) => {
    if (i < 14) return null
    const s = bars.slice(i - 13, i + 1)
    const hh = Math.max(...s.map(b => b.high)), ll = Math.min(...s.map(b => b.low))
    return hh === ll ? 50 : +(((bars[i].close - ll) / (hh - ll)) * 100).toFixed(1)
  })
  else series = genericOsc(bars)

  const min = def.kind === 'osc' ? 0 : Math.min(...(series.filter(v => v != null) as number[]), 0)
  const max = def.kind === 'osc' ? 100 : Math.max(...(series.filter(v => v != null) as number[]), 1)
  return { pane: 'sub', kind: def.kind, priceLines: [], evalMain: series, sub: { series, guides: SUB_GUIDE(def.kind), min, max } }
}

// --- conditions ------------------------------------------------------------
export interface CondDef { kind: string; label: string }
export function conditionsFor(kind: IKind): CondDef[] {
  if (kind === 'band') return [
    { kind: 'touch_lower', label: 'price touches lower band' },
    { kind: 'touch_upper', label: 'price touches upper band' },
    { kind: 'break_upper', label: 'price breaks above' },
    { kind: 'break_lower', label: 'price breaks below' },
  ]
  if (kind === 'osc') return [
    { kind: 'oversold', label: 'oversold' },
    { kind: 'overbought', label: 'overbought' },
    { kind: 'cross_mid', label: 'crosses midline' },
    { kind: 'rising', label: 'turning up' },
    { kind: 'falling', label: 'turning down' },
  ]
  if (kind === 'vol' || kind === 'vola') return [
    { kind: 'rising', label: 'rising' },
    { kind: 'falling', label: 'falling' },
    { kind: 'above_avg', label: 'above its average' },
  ]
  // ma / trend
  return [
    { kind: 'cross_up', label: 'price crosses above' },
    { kind: 'cross_down', label: 'price crosses below' },
    { kind: 'bounce', label: 'price bounces off' },
  ]
}

// generic predicate on a computed indicator at bar i
export function indicatorHeld(c: Computed, predKind: string, bars: Candle[], i: number): boolean {
  if (i < 1) return false
  const b = bars[i], pb = bars[i - 1]
  const v = c.evalMain[i], pv = c.evalMain[i - 1]
  switch (predKind) {
    case 'cross_up': return v != null && pv != null && pb.close <= pv && b.close > v
    case 'cross_down': return v != null && pv != null && pb.close >= pv && b.close < v
    case 'bounce': return v != null && b.low <= v * 1.003 && b.close >= v * 0.998 && b.close >= b.open
    case 'touch_lower': return c.evalLower?.[i] != null && b.low <= (c.evalLower[i] as number)
    case 'touch_upper': return c.evalUpper?.[i] != null && b.high >= (c.evalUpper[i] as number)
    case 'break_upper': return c.evalUpper?.[i] != null && b.close > (c.evalUpper[i] as number)
    case 'break_lower': return c.evalLower?.[i] != null && b.close < (c.evalLower[i] as number)
    case 'oversold': return v != null && v < 35
    case 'overbought': return v != null && v > 65
    case 'cross_mid': return v != null && pv != null && pv < 50 && v >= 50
    case 'rising': return v != null && pv != null && v > pv
    case 'falling': return v != null && pv != null && v < pv
    case 'above_avg': {
      const avg = sma(c.evalMain.map(x => x ?? 0), 14)[i]
      return v != null && avg != null && v > avg
    }
  }
  return false
}
