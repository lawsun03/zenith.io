import { useEffect, useRef, useState } from 'react'
import type { BarEvent, JournalItem, StatusPayload } from '../types'

export type ConnState = 'connecting' | 'connected' | 'disconnected'

export interface ChartCallbacks {
  onBar?: (bar: BarEvent) => void
  onFillMarker?: (time: number, isEntry: boolean, side: 'long' | 'short', pnl: number) => void
  onReset?: () => void
  onVpUpdate?: () => void
}

export function useStream(chartCbRef?: React.MutableRefObject<ChartCallbacks>) {
  const [status, setStatus] = useState<StatusPayload | null>(null)
  const [signals, setSignals] = useState<JournalItem[]>([])
  const [fills, setFills] = useState<JournalItem[]>([])
  const [reconciles, setReconciles] = useState<JournalItem[]>([])
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

      let lastBarUtcDate: string | null = null

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
          const barUtcDate = msg.ts.slice(0, 10)
          if (lastBarUtcDate !== null && barUtcDate !== lastBarUtcDate) {
            chartCbRef?.current?.onVpUpdate?.()
          }
          lastBarUtcDate = barUtcDate
          chartCbRef?.current?.onBar?.({
            time: Math.floor(new Date(msg.ts).getTime() / 1000),
            open: Number(p.open), high: Number(p.high),
            low: Number(p.low),  close: Number(p.close),
          })
          return
        }
        const item: JournalItem = { ts: msg.ts, kind: msg.kind, payload: msg.payload }
        if (msg.kind === 'signal') {
          setSignals(prev => [item, ...prev].slice(0, 50))
        } else if (msg.kind === 'fill') {
          setFills(prev => [item, ...prev].slice(0, 50))
          fetch('/api/status').then(r => r.json()).then(setStatus).catch(() => {})
          const p = msg.payload as { is_entry: boolean; side: 'long' | 'short'; realized_pnl_delta: string }
          chartCbRef?.current?.onFillMarker?.(
            Math.floor(new Date(msg.ts).getTime() / 1000),
            p.is_entry,
            p.side,
            Number(p.realized_pnl_delta),
          )
        } else if (msg.kind === 'reconcile') {
          setReconciles(prev => [item, ...prev].slice(0, 20))
          fetch('/api/status').then(r => r.json()).then(setStatus).catch(() => {})
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

  return { status, signals, fills, reconciles, connState }
}
