import { useEffect, useState } from 'react'

export type ThemeName = 'zenith' | 'matrix' | 'amber'

export const THEMES: { id: ThemeName; label: string; swatch: string }[] = [
  { id: 'zenith', label: 'Zenith', swatch: 'rgb(110 231 183)' },
  { id: 'matrix', label: 'Matrix', swatch: 'rgb(0 255 65)' },
  { id: 'amber',  label: 'Amber',  swatch: 'rgb(255 176 0)' },
]

const KEY = 'zenith.theme'

function read(): ThemeName {
  try {
    const t = localStorage.getItem(KEY)
    if (t === 'zenith' || t === 'matrix' || t === 'amber') return t
  } catch { /* localStorage unavailable */ }
  return 'zenith'
}

/**
 * Single source of truth for the active palette. Writes `data-theme` on
 * <html> (which swaps the CSS-variable tokens) and persists the choice.
 * Charts observe the same attribute rather than subscribing to this hook.
 */
export function useTheme() {
  const [theme, setTheme] = useState<ThemeName>(read)

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem(KEY, theme) } catch { /* ignore */ }
  }, [theme])

  return { theme, setTheme }
}
