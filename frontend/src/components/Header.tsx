import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { StatusPayload } from '../types'
import type { ConnState } from '../hooks/useStream'
import { useConfirm } from '../hooks/useConfirm'

interface Props {
  status: StatusPayload | null
  connState: ConnState
  onConfigOpen: () => void
  mode?: string
  activeKillzone?: string | null
}

const LABEL: Record<ConnState, string> = {
  connected:    'STREAMING',
  connecting:   'CONNECTING',
  disconnected: 'OFFLINE',
}

export function Header({ status, connState, onConfigOpen, mode, activeKillzone }: Props) {
  const { confirm, modal } = useConfirm()
  const [now, setNow] = useState(new Date())
  const [testMsg, setTestMsg] = useState<string | null>(null)
  const [flattenMsg, setFlattenMsg] = useState<string | null>(null)
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  async function handleTestTrade() {
    setTestMsg('placing...')
    try {
      const res = await fetch('/api/test-trade', { method: 'POST' })
      const body = await res.json()
      setTestMsg(body.message ?? (body.ok ? 'placed' : body.reason))
    } catch (e) {
      setTestMsg(String(e))
    }
    setTimeout(() => setTestMsg(null), 35000)
  }

  async function handleFlatten() {
    if (!await confirm('Flatten all open positions now?', { title: 'Flatten Positions', variant: 'danger', confirmLabel: 'Flatten' })) return
    setFlattenMsg('flattening...')
    try {
      const res = await fetch('/api/risk/flatten', { method: 'POST' })
      const body = await res.json()
      setFlattenMsg(body.ok ? 'flattened' : (body.reason ?? 'failed'))
    } catch (e) {
      setFlattenMsg(String(e))
    }
    setTimeout(() => setFlattenMsg(null), 5000)
  }

  async function handleClearLockout() {
    try {
      await fetch('/api/risk/clear-lockout', { method: 'POST' })
    } catch { /* ignore */ }
  }

  const [restartMsg, setRestartMsg] = useState<string | null>(null)
  async function handleRestart() {
    if (!await confirm('Restart the bot? Open positions will remain on TopstepX. Any unsaved bot state will be lost.', { title: 'Restart Bot', variant: 'warn', confirmLabel: 'Restart' })) return
    setRestartMsg('restarting...')
    try {
      await fetch('/api/restart', { method: 'POST' })
    } catch { /* expected: process exits before response completes */ }
    // The bot is down for ~3s while dev.ps1 respawns. The WebSocket will reconnect.
    setTimeout(() => setRestartMsg(null), 8000)
  }

  const isLocked = !!status?.lockout
  const isDriftLock = status?.lockout?.code === 'RECONCILE_DRIFT'
  const sync = status?.sync
  let syncTone = 'text-dim'
  let syncLabel = ''
  if (sync) {
    if (sync.poisoned > 0) { syncTone = 'text-danger'; syncLabel = `SYNC ${sync.pending}p ${sync.poisoned}!` }
    else if (sync.pending > 0) { syncTone = 'text-warn'; syncLabel = `SYNC ${sync.pending}p` }
    else { syncTone = 'text-accent/70'; syncLabel = 'SYNC' }
  }

  return (
    <>
    {modal}
    <header className="flex items-center px-7 h-[54px] shrink-0 bg-bg border-b border-border animate-fade-up">
      {/* identity */}
      <div className="flex items-center gap-3 pr-5 border-r border-border">
        <div className="w-7 h-7 border border-border-hi flex items-center justify-center text-xs font-medium text-ink shrink-0">
          Z
        </div>
        <div className="leading-none">
          <div className="text-[15px] font-medium text-ink tracking-tight">Zenith</div>
          <div className="text-[9px] text-faint font-mono tracking-wide mt-0.5">
            {status?.account
              ? `${(status.account.type || '').toUpperCase()} · $${Number(status.account.size).toLocaleString()}`
              : '—'}
          </div>
        </div>
      </div>

      {/* live status pills */}
      <div className="flex-1 flex items-center gap-3.5 px-5 min-w-0">
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-[3px] border text-[11px] ${
          connState === 'connected'
            ? 'border-good/30 bg-good/[0.07] text-good'
            : connState === 'connecting'
            ? 'border-warn/30 bg-warn/[0.07] text-warn'
            : 'border-danger/30 bg-danger/[0.07] text-danger'
        }`}>
          <span className={`w-[5px] h-[5px] rounded-full bg-current ${connState === 'connected' ? 'animate-blink' : ''}`} />
          {LABEL[connState]}
        </span>
        {activeKillzone && (
          <span
            className="inline-flex items-center gap-1.5 px-2.5 py-[3px] border border-accent/30 bg-accent/[0.12] text-[11px] text-accent-ink"
            title="Bot is inside a killzone window — entry signals are active"
          >
            ▶ {activeKillzone}
          </span>
        )}
        {syncLabel && (
          <span className={`text-[11px] font-mono ${syncTone}`} title={sync ? `pending=${sync.pending} poisoned=${sync.poisoned} sent=${sync.sent}` : ''}>
            {syncLabel}
          </span>
        )}
        <span className="text-[11px] text-dim font-mono tabular-nums">
          {now.toLocaleTimeString('en-US', { timeZone: 'America/Los_Angeles', hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true })} PT
        </span>
        {isLocked && (
          <span className="inline-flex items-center gap-2">
            <span className="text-[10px] tracking-wider text-danger border border-danger/40 px-2 py-0.5" title={status?.lockout?.message ?? ''}>
              LOCKED: {status?.lockout?.code}
            </span>
            {isDriftLock && (
              <button onClick={handleClearLockout} className="text-[10px] tracking-wider uppercase border border-danger/30 text-danger/80 px-2 py-0.5 hover:bg-danger/10">
                Clear
              </button>
            )}
          </span>
        )}
      </div>

      {/* controls + nav */}
      <div className="flex items-center gap-0.5 pl-5 border-l border-border shrink-0">
        {mode === 'live' && (
          <>
            {flattenMsg && <span className="text-[10px] text-danger font-mono mr-1">{flattenMsg}</span>}
            <button onClick={handleFlatten} disabled={!!flattenMsg}
              className="text-[11px] text-danger px-2.5 py-1 rounded hover:bg-danger/10 disabled:opacity-40" title="Close all open positions immediately">
              Flatten
            </button>
            {testMsg && <span className="text-[10px] text-warn font-mono mr-1">{testMsg}</span>}
            <button onClick={handleTestTrade} disabled={!!testMsg}
              className="text-[11px] text-warn px-2.5 py-1 rounded hover:bg-warn/10 disabled:opacity-40" title="Place 1-contract long, flatten after 30s">
              Test
            </button>
            {restartMsg && <span className="text-[10px] text-faint font-mono mr-1">{restartMsg}</span>}
            <button onClick={handleRestart} disabled={!!restartMsg}
              className="text-[11px] text-dim px-2.5 py-1 rounded hover:bg-white/5 disabled:opacity-40" title="Restart bot (applies account/timeframe changes)">
              Restart
            </button>
            <span className="w-px h-4 bg-border mx-1.5" />
          </>
        )}
        <Link to="/analytics" className="text-[11px] text-dim px-2.5 py-1 rounded hover:bg-white/[0.04] hover:text-ink" title="Open analytics">Analytics</Link>
        <Link to="/backtests" className="text-[11px] text-dim px-2.5 py-1 rounded hover:bg-white/[0.04] hover:text-ink" title="Open backtests">Backtests</Link>
        <Link to="/todos" className="text-[11px] text-dim px-2.5 py-1 rounded hover:bg-white/[0.04] hover:text-ink" title="Backlog">Backlog</Link>
        <a href="/api/export/trades.csv" download="trades.csv" className="text-[11px] text-dim px-2.5 py-1 rounded hover:bg-white/[0.04] hover:text-ink" title="Export trades CSV">↓ CSV</a>
        <button onClick={onConfigOpen} className="text-sm text-dim px-2 py-1 rounded hover:bg-white/[0.04] hover:text-ink" title="Configuration">⚙</button>
      </div>
    </header>
    </>
  )
}
