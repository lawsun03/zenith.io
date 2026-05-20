import { useEffect, useState } from 'react'

const KZ_WINDOWS: Record<string, { start: number; end: number; label: string }> = {
  asia:      { start: 19 * 60,      end: 22 * 60,      label: 'Asia' },
  london:    { start: 2 * 60,       end: 5 * 60,       label: 'London' },
  london_ny: { start: 6 * 60,       end: 8 * 60 + 30,  label: 'London/NY' },
  ny_am:     { start: 8 * 60 + 30,  end: 11 * 60,      label: 'NY AM' },
  ny_pm:     { start: 13 * 60,      end: 16 * 60,      label: 'NY PM' },
}

function compute(enabledKillzones: string[]): string | null {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date())
  const hour = parseInt(parts.find(p => p.type === 'hour')?.value ?? '0')
  const min  = parseInt(parts.find(p => p.type === 'minute')?.value ?? '0')
  const totalMin = hour * 60 + min
  for (const name of enabledKillzones) {
    const zone = KZ_WINDOWS[name]
    if (zone && totalMin >= zone.start && totalMin < zone.end) return zone.label
  }
  return null
}

export function useKillzone(enabledKillzones: string[] | undefined): string | null {
  const [active, setActive] = useState<string | null>(() => compute(enabledKillzones ?? []))
  useEffect(() => {
    setActive(compute(enabledKillzones ?? []))
    const id = setInterval(() => setActive(compute(enabledKillzones ?? [])), 30_000)
    return () => clearInterval(id)
  }, [enabledKillzones])
  return active
}
