// Session / time-interval filter for the mock builder. Gates entries to chosen
// windows (ICT-style killzone presets + a custom range), evaluated in ET.

import type { Candle } from './mockData'

export interface SessionWindow { name: string; from: number; to: number } // minutes from ET midnight
const hm = (h: number, m: number) => h * 60 + m

export const SESSION_PRESETS: SessionWindow[] = [
  { name: 'Asia', from: hm(19, 0), to: hm(22, 0) },
  { name: 'London', from: hm(2, 0), to: hm(5, 0) },
  { name: 'NY AM', from: hm(8, 30), to: hm(11, 0) },
  { name: 'NY PM', from: hm(13, 0), to: hm(16, 0) },
]

export interface SessionState {
  presets: Record<string, boolean>
  customOn: boolean
  from: string // "HH:MM"
  to: string
}
export const DEFAULT_SESSIONS: SessionState = { presets: {}, customOn: false, from: '09:30', to: '11:00' }

const parseHM = (s: string) => { const [h, m] = s.split(':').map(Number); return (h || 0) * 60 + (m || 0) }

// ET minutes-from-midnight for a bar (DST-correct via Intl).
function etMinutes(bar: Candle): number {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(new Date(bar.time * 1000))
  const h = Number(parts.find(p => p.type === 'hour')!.value) % 24
  const m = Number(parts.find(p => p.type === 'minute')!.value)
  return h * 60 + m
}

const within = (t: number, from: number, to: number) => from <= to ? (t >= from && t < to) : (t >= from || t < to)

export function anySessionEnabled(s: SessionState): boolean {
  return s.customOn || SESSION_PRESETS.some(w => s.presets[w.name])
}

// True when the bar is inside an enabled window. If nothing is enabled, the
// filter is inactive (always true).
export function inSession(bar: Candle, s: SessionState): boolean {
  if (!anySessionEnabled(s)) return true
  const t = etMinutes(bar)
  for (const w of SESSION_PRESETS) if (s.presets[w.name] && within(t, w.from, w.to)) return true
  if (s.customOn && within(t, parseHM(s.from), parseHM(s.to))) return true
  return false
}

export function sessionLabel(s: SessionState): string {
  const names = SESSION_PRESETS.filter(w => s.presets[w.name]).map(w => w.name)
  if (s.customOn) names.push(`${s.from}-${s.to}`)
  return names.join(', ')
}
