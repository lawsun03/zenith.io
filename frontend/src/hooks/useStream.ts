import { useEffect, useRef, useState } from 'react'
import type { BarEvent, FeedWatchdogPayload, JournalItem, StatusPayload, StrategyStatePayload } from '../types'

export type ConnState = 'connecting' | 'connected' | 'disconnected'

export interface ChartCallbacks {
  onBar?: (bar: BarEvent) => void
  onFillMarker?: (time: number, isEntry: boolean, side: 'long' | 'short', pnl: number) => void
  onReset?: () => void
}

export function useStream(
  chartCbRef?: React.MutableRefObject<ChartCallbacks>,
  activeSymbolRef?: React.MutableRefObject<string>,
) {
  const [status, setStatus] = useState<StatusPayload | null>(null)
  const [signals, setSignals] = useState<JournalItem[]>([])
  const [fills, setFills] = useState<JournalItem[]>([])
  const [reconciles, setReconciles] = useState<JournalItem[]>([])
  const [strategyState, setStrategyState] = useState<StrategyStatePayload | null>(null)
  const [feedWatchdog, setFeedWatchdog] = useState<FeedWatchdogPayload | null>(null)
  const [connState, setConnState] = useState<ConnState>('connecting')
  const wsRef = useRef<WebSocket | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    let stopped = false

    async function bootstrap() {
      try {
        const [st, sig, fl, rc] = await Promise.all([
          fetch('/api/status').then(r => r.json()),
          fetch('/api/signals').then(r => r.json()),
          fetch('/api/fills').then(r => r.json()),
          fetch('/api/reconciles').then(r => r.json()),
        ])
        if (stopped) return
        setStatus(st)
        setSignals(sig.items ?? [])
        setFills(fl.items ?? [])
        setReconciles(rc.items ?? [])
      } catch {
        // WebSocket snapshot will populate.
      }
    }

    function connect() {
      if (stopped) return
      setConnState('connecting')
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const ws = new WebSocket(`${proto}//${window.location.host}/api/stream`)
      wsRef.current = ws

      ws.onopen = () => setConnState('connected')
      ws.onclose = () => {
        wsRef.current = null
        setConnState('disconnected')
        if (!stopped) timerRef.current = setTimeout(connect, 2000)
      }
      ws.onerror = () => { /* close handler reconnects */ }
      ws.onmessage = evt => {
        const msg = JSON.parse(evt.data)
        if (msg.kind === 'snapshot') {
          setStatus(msg.payload)
          return
        }
        if (msg.kind === 'reset') {
          setSignals([])
          setFills([])
          setReconciles([])
          chartCbRef?.current?.onReset?.()
          fetch('/api/status').then(r => r.json()).then(setStatus).catch(() => {})
          return
        }
        if (msg.kind === 'bar') {
          const p = msg.payload
          if (activeSymbolRef?.current && p.instrument && p.instrument !== activeSymbolRef.current) {
            return
          }
          chartCbRef?.current?.onBar?.({
            time: Math.floor(new Date(msg.ts).getTime() / 1000),
            open: Number(p.open), high: Number(p.high),
            low: Number(p.low),  close: Number(p.close),
          })
          return
        }
        if (msg.kind === 'feed_watchdog') {
          setFeedWatchdog(msg.payload as FeedWatchdogPayload)
          return
        }
        const item: JournalItem = { ts: msg.ts, kind: msg.kind, payload: msg.payload }
        if (msg.kind === 'signal') {
          setSignals(prev => [item, ...prev].slice(0, 50))
        } else if (msg.kind === 'fill') {
          setFills(prev => [item, ...prev].slice(0, 50))
          fetch('/api/status').then(r => r.json()).then(setStatus).catch(() => {})
          const p = msg.payload as { is_entry: boolean; side: 'long' | 'short'; realized_pnl_delta: string; instrument?: string }
          const fillIsActiveSymbol = !activeSymbolRef?.current || !p.instrument || p.instrument === activeSymbolRef.current
          if (fillIsActiveSymbol) {
            chartCbRef?.current?.onFillMarker?.(
              Math.floor(new Date(msg.ts).getTime() / 1000),
              p.is_entry,
              p.side,
              Number(p.realized_pnl_delta),
            )
          }
        } else if (msg.kind === 'reconcile') {
          setReconciles(prev => [item, ...prev].slice(0, 20))
          fetch('/api/status').then(r => r.json()).then(setStatus).catch(() => {})
        } else if (msg.kind === 'strategy_state') {
          const p = msg.payload as StrategyStatePayload
          if (!activeSymbolRef?.current || !p.instrument || p.instrument === activeSymbolRef.current) {
            setStrategyState(p)
          } else {
            // The phase block is account-level, not per-instrument — it must
            // update even when the chart shows a different symbol (e.g. chart
            // on MGC while the MNQ runner emits strategy_state).
            setStrategyState(prev =>
              prev ? { ...prev, phase: p.phase } : ({ instrument: p.instrument, phase: p.phase } as StrategyStatePayload))
          }
          return
        }
      }
    }

    bootstrap()
    connect()
    return () => {
      stopped = true
      if (timerRef.current) clearTimeout(timerRef.current)
      wsRef.current?.close()
    }
  }, [])

  return { status, signals, fills, reconciles, strategyState, feedWatchdog, connState }
}
