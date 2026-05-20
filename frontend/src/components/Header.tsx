import { useEffect, useState } from 'react'
import type { StatusPayload } from '../types'
import type { ConnState } from '../hooks/useStream'

interface Props {
  status: StatusPayload | null
  connState: ConnState
  onConfigOpen: () => void
  mode?: string
  activeKillzone?: string | null
}

const DOT: Record<ConnState, string> = {
  connected:    'bg-accent animate-pulse-soft',
  connecting:   'bg-warn',
  disconnected: 'bg-danger',
}
const LABEL: Record<ConnState, string> = {
  connected:    'STREAMING',
  connecting:   'CONNECTING',
  disconnected: 'OFFLINE',
}

export function Header({ status, connState, onConfigOpen, mode, activeKillzone }: Props) {
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
    if (!confirm('Flatten all open positions now?')) return
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
    if (!confirm('Restart the bot? Open positions will remain on TopstepX. Any unsaved bot state will be lost.')) return
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
    <header className="border-b border-border px-6 py-4 flex items-center justify-between bg-bg">
      <div className="flex items-baseline gap-4">
        <span className="text-xs tracking-[0.4em] text-dim">TOPSTEP-BOT</span>
        <span className="text-xs text-dim">
          {status?.account
            ? `${(status.account.type || '').toUpperCase()} · $${Number(status.account.size).toLocaleString()}`
            : '-'}
        </span>
      </div>
      <div className="flex items-center gap-4">
        {activeKillzone && (
          <span
            className="text-[10px] tracking-widest text-accent border border-accent/50 px-2 py-0.5 animate-pulse-soft"
            title="Bot is inside a killzone window — entry signals are active"
          >
            ▶ {activeKillzone}
          </span>
        )}
        {syncLabel && (
          <span
            className={`text-[10px] tracking-widest ${syncTone}`}
            title={sync ? `pending=${sync.pending} poisoned=${sync.poisoned} sent=${sync.sent}` : ''}
          >
            {syncLabel}
          </span>
        )}
        <div className="flex items-center gap-2">
          <span className={`inline-block w-2 h-2 rounded-full ${DOT[connState]}`} />
          <span className="text-xs tracking-widest text-dim">{LABEL[connState]}</span>
          <span className="text-xs text-dim ml-4 tabular-nums">
            {now.toLocaleTimeString('en-US', {
              timeZone: 'America/Los_Angeles',
              hour: 'numeric',
              minute: '2-digit',
              second: '2-digit',
              hour12: true,
            })}
          </span>
        </div>
        {isLocked && (
          <div className="flex items-center gap-2">
            <span
              className="text-[10px] tracking-widest text-danger border border-danger/50 px-2 py-0.5"
              title={status?.lockout?.message ?? ''}
            >
              LOCKED: {status?.lockout?.code}
            </span>
            {isDriftLock && (
              <button
                onClick={handleClearLockout}
                className="text-[10px] tracking-widest uppercase border border-danger/30 text-danger/70 px-2 py-0.5 hover:bg-danger/10"
                title="Positions confirmed closed — clear the drift lockout"
              >
                Clear
              </button>
            )}
          </div>
        )}
        {mode === 'live' && (
          <div className="flex items-center gap-2">
            {flattenMsg && (
              <span className="text-[10px] text-danger tabular-nums">{flattenMsg}</span>
            )}
            <button
              onClick={handleFlatten}
              disabled={!!flattenMsg}
              className="text-[10px] tracking-widest uppercase border border-danger/50 text-danger px-3 py-1 hover:bg-danger/10 disabled:opacity-40"
              title="Close all open positions immediately"
            >
              Flatten All
            </button>
            {testMsg && (
              <span className="text-[10px] text-warn tabular-nums">{testMsg}</span>
            )}
            <button
              onClick={handleTestTrade}
              disabled={!!testMsg}
              className="text-[10px] tracking-widest uppercase border border-warn/50 text-warn px-3 py-1 hover:bg-warn/10 disabled:opacity-40"
              title="Place 1-contract long, flatten after 30s"
            >
              Test Trade
            </button>
            {restartMsg && (
              <span className="text-[10px] text-dim tabular-nums">{restartMsg}</span>
            )}
            <button
              onClick={handleRestart}
              disabled={!!restartMsg}
              className="text-[10px] tracking-widest uppercase border border-dim text-dim px-3 py-1 hover:bg-dim/10 disabled:opacity-40"
              title="Restart bot (applies account/timeframe changes)"
            >
              Restart
            </button>
          </div>
        )}
        <a
          href="/analytics"
          target="_blank"
          rel="noreferrer"
          className="text-dim hover:text-ink text-xs tracking-widest uppercase ml-2"
          title="Open analytics and Claude advisor"
        >
          Analytics &#x29C9;
        </a>
        <a
          href="/backtests"
          target="_blank"
          rel="noreferrer"
          className="text-dim hover:text-ink text-xs tracking-widest uppercase ml-2"
          title="Open backtests page in a new window"
        >
          Backtests &#x29C9;
        </a>
        <a
          href="/api/export/trades.csv"
          download="trades.csv"
          className="text-dim hover:text-ink text-xs tracking-widest uppercase ml-2"
          title="Export trades CSV"
        >
          &#8659; CSV
        </a>
        <button
          onClick={onConfigOpen}
          className="text-dim hover:text-ink text-sm ml-2"
          title="Configuration"
        >
          &#9881;
        </button>
      </div>
    </header>
  )
}
