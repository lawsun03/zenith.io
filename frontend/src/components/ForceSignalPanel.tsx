import { useState } from 'react'

interface Result {
  ok: boolean
  placed: boolean
  reason: string
  allowed_size?: number
  broker_order_id?: string
  signal?: { side: string; entry: string; stop: string; target: string }
}

export function ForceSignalPanel() {
  const [side, setSide] = useState<'long' | 'short'>('long')
  const [entry, setEntry] = useState('')
  const [stopDist, setStopDist] = useState('2.0')
  const [rMultiple, setRMultiple] = useState('2.0')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<Result | null>(null)

  async function fire() {
    if (!entry) return
    setLoading(true)
    setResult(null)
    try {
      const res = await fetch('/api/debug/force-signal', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ side, entry, stop_distance: stopDist, r_multiple: rMultiple }),
      })
      setResult(await res.json())
    } catch (e) {
      setResult({ ok: false, placed: false, reason: String(e) })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="border border-border bg-panel">
      <div className="px-4 py-2 border-b border-border">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Force Signal</span>
        <span className="ml-3 text-[9px] text-dim/50 uppercase tracking-widest">debug · live mode only</span>
      </div>
      <div className="p-4 flex flex-wrap items-end gap-3">
        {/* Side toggle */}
        <div>
          <div className="text-[9px] tracking-widest text-dim uppercase mb-1">Side</div>
          <div className="flex">
            <button
              onClick={() => setSide('long')}
              className={`px-3 py-1 text-[10px] tracking-widest uppercase border-y border-l border-border ${
                side === 'long' ? 'bg-accent/20 text-accent border-accent' : 'text-dim hover:text-ink'
              }`}
            >
              Long
            </button>
            <button
              onClick={() => setSide('short')}
              className={`px-3 py-1 text-[10px] tracking-widest uppercase border border-border ${
                side === 'short' ? 'bg-danger/20 text-danger border-danger' : 'text-dim hover:text-ink'
              }`}
            >
              Short
            </button>
          </div>
        </div>

        {/* Entry */}
        <div>
          <div className="text-[9px] tracking-widest text-dim uppercase mb-1">Entry Price</div>
          <input
            type="number"
            value={entry}
            onChange={e => setEntry(e.target.value)}
            placeholder="e.g. 4720.0"
            className="w-28 bg-bg border border-border text-ink text-xs px-2 py-1 font-mono focus:outline-none focus:border-accent"
          />
        </div>

        {/* Stop distance */}
        <div>
          <div className="text-[9px] tracking-widest text-dim uppercase mb-1">Stop Dist (pts)</div>
          <input
            type="number"
            value={stopDist}
            onChange={e => setStopDist(e.target.value)}
            className="w-20 bg-bg border border-border text-ink text-xs px-2 py-1 font-mono focus:outline-none focus:border-accent"
          />
        </div>

        {/* R multiple */}
        <div>
          <div className="text-[9px] tracking-widest text-dim uppercase mb-1">R Multiple</div>
          <input
            type="number"
            value={rMultiple}
            onChange={e => setRMultiple(e.target.value)}
            className="w-16 bg-bg border border-border text-ink text-xs px-2 py-1 font-mono focus:outline-none focus:border-accent"
          />
        </div>

        {/* Fire button */}
        <button
          onClick={fire}
          disabled={loading || !entry}
          className={`px-4 py-1 text-[10px] tracking-widest uppercase border ${
            side === 'long'
              ? 'border-accent text-accent hover:bg-accent/10'
              : 'border-danger text-danger hover:bg-danger/10'
          } disabled:opacity-40 disabled:cursor-not-allowed`}
        >
          {loading ? 'Firing...' : `Fire ${side.toUpperCase()}`}
        </button>
      </div>

      {/* Result */}
      {result && (
        <div className={`mx-4 mb-4 p-3 border text-xs font-mono ${
          result.placed ? 'border-accent/40 bg-accent/5 text-accent' : 'border-danger/40 bg-danger/5 text-danger'
        }`}>
          <div className="font-bold mb-1">{result.placed ? '✓ PLACED' : '✗ NOT PLACED'}</div>
          <div>reason: {result.reason}</div>
          {result.allowed_size != null && <div>size: {result.allowed_size}</div>}
          {result.broker_order_id && <div>order_id: {result.broker_order_id}</div>}
          {result.signal && (
            <div className="mt-1 text-dim">
              entry={result.signal.entry} stop={result.signal.stop} target={result.signal.target}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
