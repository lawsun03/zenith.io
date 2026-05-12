import { useEffect, useState } from 'react'
import type { BotConfig } from '../types'

export function useConfig() {
  const [config, setConfig] = useState<BotConfig | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/config')
      .then(r => r.json())
      .then(setConfig)
      .catch(() => {})
  }, [])

  async function saveConfig(updated: BotConfig) {
    setSaving(true)
    setSaveError(null)
    try {
      const res = await fetch('/api/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updated),
      })
      if (!res.ok) throw new Error(await res.text())
      setConfig(await res.json())
    } catch (e) {
      setSaveError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return { config, saveConfig, saving, saveError }
}
