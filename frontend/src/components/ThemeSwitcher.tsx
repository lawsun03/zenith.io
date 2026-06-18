import { useEffect, useRef, useState } from 'react'
import { useTheme, THEMES } from '../hooks/useTheme'

/**
 * Compact palette picker for the header. The menu is rendered with fixed
 * positioning so it escapes the dashboard's overflow-hidden ancestors
 * (an absolute dropdown would be clipped).
 */
export function ThemeSwitcher() {
  const { theme, setTheme } = useTheme()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  const active = THEMES.find(t => t.id === theme) ?? THEMES[0]

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(o => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Switch theme"
        className="flex items-center gap-1.5 text-[11px] text-dim px-2 py-1 rounded hover:bg-ink/[0.04] hover:text-ink focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent/60"
      >
        <span className="w-2.5 h-2.5 rounded-full shrink-0 border border-border-hi" style={{ background: active.swatch }} />
        <span className="tracking-wide">{active.label}</span>
        <span className={`text-[8px] transition-transform ${open ? 'rotate-180' : ''}`}>▾</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-40" onClick={() => setOpen(false)} />
          <div
            role="menu"
            className="fixed top-[50px] right-5 z-50 w-36 py-1 bg-panel-hi border border-border rounded-md shadow-lg shadow-black/40 animate-fade-up"
          >
            {THEMES.map(t => (
              <button
                key={t.id}
                role="menuitemradio"
                aria-checked={t.id === theme}
                onClick={() => { setTheme(t.id); setOpen(false) }}
                className={`flex items-center gap-2 w-full px-2.5 py-1.5 text-[11px] tracking-wide text-left hover:bg-ink/[0.05] focus-visible:outline focus-visible:outline-1 focus-visible:outline-accent/60 ${
                  t.id === theme ? 'text-ink' : 'text-dim'
                }`}
              >
                <span className="w-2.5 h-2.5 rounded-full shrink-0 border border-border-hi" style={{ background: t.swatch }} />
                <span className="flex-1">{t.label}</span>
                {t.id === theme && <span className="text-accent text-[10px]">✓</span>}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
