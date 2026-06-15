import { useEffect, useState } from 'react'

interface SetupInstrument {
  instrument: string
  killzone: { active: boolean; name: string | null }
  sweeps_pending: { side: string; pattern: string; swept_price: string; sweep_extreme: string }[]
  displacement_candidate: { side: string; bar_ts: string } | null
  cooldown_bars_remaining: number
  atr: string | null
  htf?: {
    bias_enabled: boolean
    bias_ready: boolean
    bias: 'bullish' | 'bearish' | 'neutral' | null
    target_enabled: boolean
    target_ready: boolean
    bias_timeframe: string
  }
}

interface SetupState {
  available: boolean
  instruments: SetupInstrument[]
}

function Row({ label, active, detail, muted }: { label: string; active: boolean; detail?: string; muted?: boolean }) {
  return (
    <div className={`flex items-start gap-2.5 px-5 py-2.5 border-b border-border ${muted ? 'opacity-40' : ''}`}>
      <span className={`text-[5px] leading-[18px] shrink-0 ${active ? 'text-accent-ink' : 'text-faint'}`}>●</span>
      <div className="flex flex-col min-w-0">
        <span className={`text-xs ${active ? 'text-ink' : 'text-dim'}`}>{label}</span>
        {detail && <span className="text-[10px] text-faint font-mono mt-0.5">{detail}</span>}
      </div>
    </div>
  )
}

function HtfRow({ htf }: { htf: NonNullable<SetupInstrument['htf']> }) {
  if (!htf.bias_enabled && !htf.target_enabled) {
    return <Row label="HTF" active={false} detail="disabled" muted />
  }
  const stillWarming = (htf.bias_enabled && !htf.bias_ready) || (htf.target_enabled && !htf.target_ready)
  if (stillWarming) {
    return <Row label="HTF" active={false} detail={`warming up (${htf.bias_timeframe})`} />
  }
  const decisive = htf.bias === 'bullish' || htf.bias === 'bearish'
  const biasLabel = !htf.bias_enabled ? 'off' : htf.bias === null ? '—' : htf.bias.toUpperCase()
  const detail =
    htf.bias === 'bullish' ? 'blocks shorts'
    : htf.bias === 'bearish' ? 'blocks longs'
    : htf.bias_enabled ? 'neutral — no block'
    : 'bias filter off'
  return <Row label={`HTF bias (${htf.bias_timeframe}): ${biasLabel}`} active={decisive} detail={detail} />
}

export function ConditionsPanel({ activeSymbol }: { activeSymbol?: string }) {
  const [setupState, setSetupState] = useState<SetupState | null>(null)

  useEffect(() => {
    const poll = () => {
      fetch('/api/setup_state').then(r => r.json()).then((d: SetupState) => setSetupState(d)).catch(() => {})
    }
    poll()
    const id = setInterval(poll, 3000)
    return () => clearInterval(id)
  }, [])

  const instruments = setupState?.available ? setupState.instruments : []
  const inst = instruments.find(i => i.instrument === activeSymbol) ?? instruments[0] ?? null

  if (!inst) {
    return <div className="px-5 py-4 text-faint text-xs">Waiting…</div>
  }

  return (
    <div>
      <Row
        label={inst.killzone.active ? (inst.killzone.name ?? 'Killzone') : 'Killzone'}
        active={inst.killzone.active}
        detail={inst.killzone.active ? 'session open' : 'outside hours'}
      />
      <Row
        label="Sweep"
        active={inst.sweeps_pending.length > 0}
        detail={inst.sweeps_pending.length > 0
          ? `${inst.sweeps_pending[0].side === 'high' ? 'High' : 'Low'} @ ${inst.sweeps_pending[0].swept_price}`
          : undefined}
        muted={!inst.killzone.active}
      />
      <Row
        label="Displ + FVG"
        active={inst.displacement_candidate !== null}
        detail={inst.displacement_candidate
          ? inst.displacement_candidate.side
          : inst.sweeps_pending.length > 0 ? 'waiting…' : undefined}
        muted={inst.sweeps_pending.length === 0}
      />
      {inst.htf && <HtfRow htf={inst.htf} />}
      {inst.cooldown_bars_remaining > 0 && (
        <Row label="Cooldown" active={false} detail={`${inst.cooldown_bars_remaining} bar${inst.cooldown_bars_remaining !== 1 ? 's' : ''} · suppressed`} />
      )}
      {inst.atr && (
        <div className="px-5 py-2 text-[10px] text-faint font-mono tracking-wide">ATR {parseFloat(inst.atr).toFixed(2)}</div>
      )}
    </div>
  )
}
