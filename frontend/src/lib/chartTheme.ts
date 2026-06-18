/*
 * Bridges the CSS theme tokens into lightweight-charts, which is configured
 * imperatively and can't read Tailwind classes. Values are pulled from the
 * --c-* custom properties on <html> at call time, so they reflect the active
 * theme. `observeChartTheme` re-applies when the theme attribute changes.
 */

function triple(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

const rgb = (name: string) => `rgb(${triple(name)})`
const rgba = (name: string, a: number) => `rgb(${triple(name)} / ${a})`

export interface ChartTheme {
  text: string
  grid: string
  axis: string
  cross: string
  crossLabelBg: string
  up: string
  down: string
  wickUp: string
  wickDown: string
  entry: string
}

export function readChartTheme(): ChartTheme {
  return {
    text:         rgba('--c-dim', 0.85),
    grid:         rgba('--c-grid', 0.04),
    axis:         rgba('--c-grid', 0.06),
    cross:        rgba('--c-accent', 0.4),
    crossLabelBg: rgb('--c-panel-hi'),
    up:           rgb('--c-up'),
    down:         rgb('--c-down'),
    wickUp:       rgba('--c-up', 0.6),
    wickDown:     rgba('--c-down', 0.6),
    entry:        rgba('--c-ink', 0.35),
  }
}

/**
 * Calls `apply(theme)` immediately and whenever the active theme changes.
 * Returns a disconnect fn. Used by the persistent live chart to re-theme in
 * place (no recreation, no data loss).
 */
export function observeChartTheme(apply: (t: ChartTheme) => void): () => void {
  apply(readChartTheme())
  const obs = new MutationObserver(() => apply(readChartTheme()))
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  return () => obs.disconnect()
}
