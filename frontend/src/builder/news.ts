// Mock news/event calendar + filter for the builder. Events are placed at fixed
// bar indices in the synthetic series (stable across timeframes), so they double
// as the anchors for the event-straddle builder.

export interface NewsEvent { idx: number; name: string }

export const NEWS_EVENTS: NewsEvent[] = [
  { idx: 50, name: 'CPI' },
  { idx: 100, name: 'FOMC' },
  { idx: 150, name: 'NFP' },
]

export interface NewsFilter { mode: 'off' | 'only' | 'avoid'; windowBars: number }
export const DEFAULT_NEWS: NewsFilter = { mode: 'off', windowBars: 4 }

// True when bar index i is allowed to trade under the news filter.
export function passesNews(i: number, filter: NewsFilter, events: NewsEvent[] = NEWS_EVENTS): boolean {
  if (filter.mode === 'off') return true
  const near = events.some(e => Math.abs(i - e.idx) <= filter.windowBars)
  return filter.mode === 'only' ? near : !near
}

// Recurring events across a long backtest series.
export function backtestNews(n: number): NewsEvent[] {
  const names = ['CPI', 'FOMC', 'NFP']
  const out: NewsEvent[] = []
  for (let idx = 300, k = 0; idx < n; idx += 320, k++) out.push({ idx, name: names[k % 3] })
  return out
}

export function newsLabel(filter: NewsFilter): string {
  if (filter.mode === 'off') return ''
  return filter.mode === 'only' ? `only news ±${filter.windowBars}b` : `avoid news ±${filter.windowBars}b`
}
