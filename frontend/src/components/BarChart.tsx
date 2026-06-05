import { useEffect, useRef, useState, useCallback } from 'react'
import { createChart, CandlestickSeries } from 'lightweight-charts'
import type { ChartCallbacks } from '../hooks/useStream'
import type { VpProfile } from '../types'

interface SetupInstrument {
  instrument: string
  killzone: { active: boolean; name: string | null }
  sweeps_pending: { side: string; pattern: string; swept_price: string; sweep_extreme: string }[]
  displacement_candidate: { side: string; bar_ts: string } | null
  cooldown_bars_remaining: number
  atr: string | null
  vp: {
    enabled: boolean
    profile_available: boolean
    poc?: string
    vah?: string
    val?: string
    hvns?: string[]
    tolerance?: string
    min_target_r?: string
    session_date?: string
  }
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

const TF_SECONDS: Record<string, number> = {
  '1min': 60, '3min': 180, '5min': 300,
  '15min': 900, '30min': 1800, '1h': 3600,
  '4h': 14400, '1d': 86400,
}

const TF_LABELS: Record<string, string> = {
  '1min': '1m', '3min': '3m', '5min': '5m',
  '15min': '15m', '30min': '30m', '1h': '1h',
  '4h': '4h', '1d': '1D',
}

interface Props {
  callbacksRef: React.MutableRefObject<ChartCallbacks>
  timeframe?: string
  activeSymbol?: string
}

const CHART_HEIGHT = 320

const S = {
  row: (active: boolean, muted: boolean) => ({
    display: 'flex' as const,
    alignItems: 'flex-start' as const,
    opacity: muted ? 0.28 : 1,
    borderLeft: `3px solid ${active ? '#00ff41' : '#003a00'}`,
    paddingLeft: 10,
    paddingRight: 12,
    paddingTop: 6,
    paddingBottom: 6,
    background: active ? 'rgba(0,255,65,0.05)' : 'transparent',
    borderBottom: '1px solid #001200',
  }),
  dot: (active: boolean) => ({
    color: active ? '#00ff41' : '#1e4d1e',
    fontSize: 10,
    lineHeight: '20px',
    flexShrink: 0,
    marginRight: 8,
  }),
  label: (active: boolean) => ({
    color: active ? '#d4ffd4' : '#3d6b3d',
    fontSize: 13,
    lineHeight: '20px',
    fontWeight: active ? 700 : 400,
    fontFamily: "'JetBrains Mono', monospace",
  }),
  detail: {
    color: '#4a8f4a',
    fontSize: 11,
    lineHeight: '16px',
    marginTop: 2,
    fontFamily: "'JetBrains Mono', monospace",
  },
  subDetail: {
    color: '#2d6b2d',
    fontSize: 10,
    lineHeight: '15px',
    marginTop: 1,
    fontFamily: "'JetBrains Mono', monospace",
  },
}

function CheckItem({
  label, active, detail, muted,
}: { label: string; active: boolean; detail?: string; muted?: boolean }) {
  return (
    <div style={S.row(active, !!muted)}>
      <span style={S.dot(active)}>{active ? '▶' : '·'}</span>
      <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <span style={S.label(active)}>{label}</span>
        {detail && <span style={S.detail}>{detail}</span>}
      </div>
    </div>
  )
}

function VpCheckItem({ vp }: { vp: SetupInstrument['vp'] }) {
  if (!vp.enabled) {
    return <CheckItem label="VP filter" active={false} detail="Disabled" muted />
  }
  if (!vp.profile_available) {
    return (
      <CheckItem
        label="VP filter"
        active={false}
        detail="No prior session — bypassed"
      />
    )
  }

  const { val, vah, poc, hvns, tolerance, min_target_r } = vp
  const tol = tolerance ? ` ±${tolerance}` : ''

  return (
    <div style={S.row(true, false)}>
      <span style={S.dot(true)}>▶</span>
      <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <span style={S.label(true)}>VP filter</span>
        <span style={S.detail}>
          VA {parseFloat(val ?? '0').toFixed(1)} – {parseFloat(vah ?? '0').toFixed(1)}{tol}
        </span>
        <span style={S.subDetail}>
          POC {poc ? parseFloat(poc).toFixed(1) : '—'} · tgt ≥{min_target_r}R
        </span>
        {hvns && hvns.length > 0 && (
          <span style={S.subDetail}>
            HVN {hvns.slice(0, 3).map(h => parseFloat(h).toFixed(1)).join(' · ')}{hvns.length > 3 ? '…' : ''}
          </span>
        )}
      </div>
    </div>
  )
}

function HtfCheckItem({ htf }: { htf: NonNullable<SetupInstrument['htf']> }) {
  // Both off → muted single-row note.
  if (!htf.bias_enabled && !htf.target_enabled) {
    return <CheckItem label="HTF" active={false} detail="Disabled" muted />
  }

  // Enabled but the REST warm-up hasn't landed (or failed).
  const stillWarming =
    (htf.bias_enabled && !htf.bias_ready) ||
    (htf.target_enabled && !htf.target_ready)
  if (stillWarming) {
    return (
      <CheckItem
        label="HTF"
        active={false}
        detail={`Warming up (${htf.bias_timeframe}) — gate inert until ready`}
      />
    )
  }

  const decisive = htf.bias === 'bullish' || htf.bias === 'bearish'
  const biasLabel = !htf.bias_enabled
    ? 'off'
    : htf.bias === null
    ? '—'
    : htf.bias.toUpperCase()
  const biasDetail =
    htf.bias === 'bullish'
      ? `Blocks shorts · agrees-with-longs bypasses VP`
      : htf.bias === 'bearish'
      ? `Blocks longs · agrees-with-shorts bypasses VP`
      : htf.bias_enabled
      ? `Neutral — no block`
      : `Bias filter off`

  return (
    <div style={S.row(decisive, false)}>
      <span style={S.dot(decisive)}>{decisive ? '▶' : '·'}</span>
      <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
        <span style={S.label(decisive)}>
          HTF bias ({htf.bias_timeframe}): {biasLabel}
        </span>
        <span style={S.detail}>{biasDetail}</span>
        <span style={S.subDetail}>
          Targets: {htf.target_enabled ? (htf.target_ready ? 'on' : 'warming') : 'off'}
        </span>
      </div>
    </div>
  )
}


function isValidBar(b: { time: number; open: number; high: number; low: number; close: number }): boolean {
  return (
    typeof b.open  === 'number' && isFinite(b.open)  &&
    typeof b.high  === 'number' && isFinite(b.high)  &&
    typeof b.low   === 'number' && isFinite(b.low)   &&
    typeof b.close === 'number' && isFinite(b.close) &&
    typeof b.time  === 'number' && isFinite(b.time)
  )
}

export function BarChart({ callbacksRef, timeframe, activeSymbol }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const lastBarTimeRef = useRef<number | null>(null)
  const [countdown, setCountdown] = useState<string | null>(null)
  const [setupState, setSetupState] = useState<SetupState | null>(null)
  const [viewTf, setViewTf] = useState<string>(timeframe ?? '1min')
  const viewTfRef = useRef<string>(timeframe ?? '1min')
  // timeframeRef tracks the live prop value so closures created at mount don't
  // capture the undefined that exists before config loads.
  const timeframeRef = useRef<string | undefined>(timeframe)
  // activeSymbolRef so the forming-bar closure always reads the current symbol.
  const activeSymbolRef = useRef<string>(activeSymbol ?? '')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const seriesRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null)

  const pollSetupState = useCallback(() => {
    fetch('/api/setup_state')
      .then(r => r.json())
      .then((d: SetupState) => setSetupState(d))
      .catch(() => {})
  }, [])

  useEffect(() => {
    pollSetupState()
    const id = setInterval(pollSetupState, 3000)
    return () => clearInterval(id)
  }, [pollSetupState])

  // Keep viewTfRef in sync so the chart useEffect closure reads fresh values.
  useEffect(() => { viewTfRef.current = viewTf }, [viewTf])
  useEffect(() => { activeSymbolRef.current = activeSymbol ?? '' }, [activeSymbol])

  // Keep timeframeRef current. When timeframe first becomes defined (config
  // loaded after mount), also auto-select the bot's trading TF as the view.
  useEffect(() => {
    const prev = timeframeRef.current
    timeframeRef.current = timeframe
    if (timeframe && !prev) {
      setViewTf(timeframe)
    }
  }, [timeframe])

  const formingHot = (setupState?.instruments ?? []).some(
    i => i.displacement_candidate !== null && i.sweeps_pending.length > 0
  )

  // Countdown ticker — time until the next bar boundary (next minute, next 5min, etc.)
  // Wall-clock based, so it's accurate even when REST bar delivery lags.
  useEffect(() => {
    const tfSecs = TF_SECONDS[viewTf] ?? null
    if (!tfSecs) { setCountdown(null); return }
    const tick = () => {
      const nowSecs = Math.floor(Date.now() / 1000)
      const nextBoundary = (Math.floor(nowSecs / tfSecs) + 1) * tfSecs
      const remaining = nextBoundary - nowSecs
      const m = Math.floor(remaining / 60)
      const s = remaining % 60
      setCountdown(m > 0 ? `${m}:${String(s).padStart(2, '0')}` : `${s}s`)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [viewTf])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const PT = 'America/Los_Angeles'
    const fmtChartTime = (timeSecs: number) =>
      new Date(timeSecs * 1000).toLocaleTimeString('en-US', {
        timeZone: PT,
        hour: 'numeric',
        minute: '2-digit',
        hour12: true,
      })
    const fmtChartDateTime = (timeSecs: number) => {
      const d = new Date(timeSecs * 1000)
      const date = d.toLocaleDateString('en-US', {
        timeZone: PT,
        month: '2-digit',
        day: '2-digit',
      })
      return `${date} ${fmtChartTime(timeSecs)}`
    }

    const chart = createChart(el, {
      autoSize: true,
      height: 320,
      layout: {
        background: { color: '#000000' },
        textColor:  '#00aa22',
        fontSize:   11,
      },
      grid: {
        vertLines: { color: '#001200' },
        horzLines: { color: '#001200' },
      },
      crosshair: {
        vertLine: { color: '#00aa22', labelBackgroundColor: '#040604' },
        horzLine: { color: '#00aa22', labelBackgroundColor: '#040604' },
      },
      rightPriceScale: { borderColor: '#003a00' },
      localization: {
        // Crosshair tooltip and time-axis label use PT 12-hour.
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        timeFormatter: ((time: any) => fmtChartDateTime(Number(time))) as any,
      },
      timeScale: {
        borderColor:    '#003a00',
        timeVisible:    true,
        secondsVisible: false,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        tickMarkFormatter: ((time: any) => fmtChartTime(Number(time))) as any,
      },
    })

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const series = chart.addSeries(CandlestickSeries as any, {
      upColor:         '#00ff41',
      downColor:       '#ff3333',
      borderUpColor:   '#00ff41',
      borderDownColor: '#ff3333',
      wickUpColor:     '#00ff41',
      wickDownColor:   '#ff3333',
    })
    seriesRef.current = series
    chartRef.current = chart

    // Use legacy setMarkers API — createSeriesMarkers (v5 plugin) hooks into the
    // bar colorer during positioning and crashes when marker times have no bar.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const seriesAny = series as any
    let currentMarkers: any[] = []
    const markersPlugin = {
      markers: () => currentMarkers,
      setMarkers: (m: any[]) => { currentMarkers = m; seriesAny.setMarkers(m) },
    }

    // VP histogram canvas overlay — draws volume-by-price on the right side.
    let currentVpProfile: VpProfile | null = null

    const vpCanvas = document.createElement('canvas')
    Object.assign(vpCanvas.style, { position: 'absolute', top: '0', left: '0', pointerEvents: 'none', zIndex: '10' })
    el.style.position = 'relative'
    el.appendChild(vpCanvas)

    const drawHistogram = () => {
      // Use the chart's own canvas for dimensions — el.offsetHeight is 0 when
      // lightweight-charts uses autoSize (absolutely-positioned canvas inside el).
      const chartCanvas = el.querySelector('canvas')
      vpCanvas.width  = chartCanvas ? chartCanvas.offsetWidth  : (el.offsetWidth  || 800)
      vpCanvas.height = chartCanvas ? chartCanvas.offsetHeight : (el.offsetHeight || 320)
      const ctx = vpCanvas.getContext('2d')
      if (!ctx) return
      ctx.clearRect(0, 0, vpCanvas.width, vpCanvas.height)

      const profile = currentVpProfile
      if (!profile || !profile.bins.length) return

      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const s = series as any
      const poc = Number(profile.poc)
      const vah = Number(profile.vah)
      const val = Number(profile.val)
      const bins = profile.bins

      // Derive tick size from adjacent bin prices
      const tickSize = bins.length > 1 ? Math.abs(Number(bins[1][0]) - Number(bins[0][0])) : 0.1

      // Pixel height per price bin, derived from two priceToCoordinate calls
      const yAtPoc = s.priceToCoordinate(poc)
      const yAtPocPlusTick = s.priceToCoordinate(poc + tickSize)
      if (yAtPoc == null || yAtPocPlusTick == null) return
      const rowHeight = Math.max(1, Math.abs(yAtPocPlusTick - yAtPoc))

      const maxVol = Math.max(...bins.map(([, v]) => v))
      const maxHistWidth = 80   // max bar width from left edge
      const histLeft = 0        // anchor to left side of chart

      for (const [priceStr, volume] of bins) {
        const price = Number(priceStr)
        const yCenter = s.priceToCoordinate(price)
        if (yCenter == null) continue

        const barWidth = Math.max(1, (volume / maxVol) * maxHistWidth)

        if (Math.abs(price - poc) < tickSize * 0.5) {
          ctx.fillStyle = 'rgba(255,153,0,0.90)'   // POC — amber
        } else if (price >= val - tickSize * 0.1 && price <= vah + tickSize * 0.1) {
          ctx.fillStyle = 'rgba(0,180,220,0.35)'   // value area — cyan
        } else {
          ctx.fillStyle = 'rgba(0,110,0,0.50)'     // outside VA — dark green
        }

        ctx.fillRect(histLeft, yCenter - rowHeight / 2, barWidth, rowHeight)
      }

      // POC / VAH / VAL labels just right of the histogram
      ctx.font = 'bold 9px monospace'
      const labelX = maxHistWidth + 3
      const pocY = s.priceToCoordinate(poc)
      const vahY = s.priceToCoordinate(vah)
      const valY = s.priceToCoordinate(val)
      if (pocY != null) { ctx.fillStyle = 'rgba(255,153,0,1)';    ctx.fillText('POC', labelX, pocY + 3) }
      if (vahY != null) { ctx.fillStyle = 'rgba(0,204,255,0.9)';  ctx.fillText('VAH', labelX, vahY + 3) }
      if (valY != null) { ctx.fillStyle = 'rgba(0,204,255,0.9)';  ctx.fillText('VAL', labelX, valY + 3) }
    }

    const fetchAndDrawVp = () => {
      fetch('/api/vp/profile')
        .then(r => r.json())
        .then((p: VpProfile | null) => { currentVpProfile = p; drawHistogram() })
        .catch(() => {})
    }

    // Redraw when time axis changes; 200ms interval catches price-axis zoom.
    // 60s re-fetch handles the case where the engine wasn't ready at mount time.
    chart.timeScale().subscribeVisibleLogicalRangeChange(drawHistogram)
    const syncId = setInterval(drawHistogram, 200)
    const vpRefetchId = setInterval(fetchAndDrawVp, 60_000)

    // Pre-populate the chart with historical bars so it's not empty on connect.
    // Initial bar load — the [viewTf] effect can't do this because seriesRef
    // isn't set when it fires on mount. Subsequent TF/symbol switches are handled by [viewTf]/[activeSymbol].
    const instrParam = activeSymbol ? `&instrument=${activeSymbol}` : ''
    fetch(`/api/bars?timeframe=${viewTfRef.current}&limit=500${instrParam}`)
      .then(r => r.json())
      .then(d => {
        const validBars = Array.isArray(d.bars) ? d.bars.filter(isValidBar) : []
        if (validBars.length > 0) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          series.setData(validBars as any)
          chart.timeScale().fitContent()
        }
      })
      .catch(() => {})

    fetchAndDrawVp()

    // Forming bar poll — updates the live rightmost candle every 1s.
    const fetchFormingBar = () => {
      const sym = activeSymbolRef.current
      fetch(`/api/forming-bar${sym ? `?instrument=${sym}` : ''}`)
        .then(r => r.json())
        .then((b: { time: number; open: number; high: number; low: number; close: number } | null) => {
          if (!b || !isValidBar(b)) return
          // Forming bar only makes sense at the bot's trading TF.
          if (viewTfRef.current !== timeframeRef.current) return
          // Only show if forming bar is newer than (or same as) the last closed bar.
          if (lastBarTimeRef.current !== null && b.time < lastBarTimeRef.current) return
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          series.update({ time: b.time as any, open: b.open, high: b.high, low: b.low, close: b.close })
        })
        .catch(() => {})
    }
    fetchFormingBar()
    const formingBarId = setInterval(fetchFormingBar, 1000)

    callbacksRef.current = {
      onBar(bar) {
        // Only update chart when viewing the bot's trading TF.
        if (viewTfRef.current !== timeframeRef.current) return
        lastBarTimeRef.current = bar.time
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        series.update({ time: bar.time as any, open: bar.open, high: bar.high, low: bar.low, close: bar.close })
      },
      onFillMarker(time, isEntry, side, pnl) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const existing: any[] = [...markersPlugin.markers()]
        if (isEntry) {
          existing.push({
            time,
            position: side === 'long' ? 'belowBar' : 'aboveBar',
            shape:    side === 'long' ? 'arrowUp'  : 'arrowDown',
            color:    side === 'long' ? '#00ff41'  : '#ff3333',
            text:     side === 'long' ? 'BUY'      : 'SELL',
            size: 1,
          })
        } else {
          const win = pnl >= 0
          existing.push({
            time,
            position: side === 'long' ? 'aboveBar' : 'belowBar',
            shape:    'circle',
            color:    win ? '#00ff41' : '#ff3333',
            text:     (win ? '+' : '') + '$' + Math.abs(pnl).toFixed(0),
            size: 1,
          })
        }
        existing.sort((a, b) => (a.time as number) - (b.time as number))
        markersPlugin.setMarkers(existing)
      },
      onReset() {
        series.setData([])
        markersPlugin.setMarkers([])
      },
      onVpUpdate() {
        fetchAndDrawVp()
      },
    }

    return () => {
      clearInterval(syncId)
      clearInterval(vpRefetchId)
      clearInterval(formingBarId)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(drawHistogram)
      if (el.contains(vpCanvas)) el.removeChild(vpCanvas)
      callbacksRef.current = {}
      seriesRef.current = null
      chartRef.current = null
      chart.remove()
    }
  }, [callbacksRef])

  // Re-populate the chart whenever the viewed timeframe or active symbol changes.
  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return
    const instrParam = activeSymbol ? `&instrument=${activeSymbol}` : ''
    fetch(`/api/bars?timeframe=${viewTf}&limit=500${instrParam}`)
      .then(r => r.json())
      .then(d => {
        if (!seriesRef.current || !chartRef.current) return
        const validBars = Array.isArray(d.bars) ? d.bars.filter(isValidBar) : []
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        seriesRef.current.setData(validBars.length > 0 ? validBars as any : [])
        // Reset forming-bar anchor so off-TF/off-symbol bars don't show stale data.
        lastBarTimeRef.current = null
        if (validBars.length > 0) chartRef.current.timeScale().fitContent()
      })
      .catch(() => {})
  }, [viewTf, activeSymbol])

  const inst = setupState?.available ? (setupState.instruments[0] ?? null) : null

  return (
    <div className="bg-panel border border-border">
      {/* Header bar */}
      <div className="px-4 py-2 border-b border-border flex items-center justify-between">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Price Chart</span>
        <div className="flex items-center gap-3">
          {/* TF selector */}
          <div className="flex items-center gap-0.5">
            {Object.keys(TF_SECONDS).map(tf => (
              <button
                key={tf}
                onClick={() => setViewTf(tf)}
                className={`text-[10px] font-mono px-1.5 py-0.5 rounded-sm transition-colors ${viewTf === tf ? 'text-accent bg-accent/10' : 'text-dim hover:text-ink'}`}
              >
                {TF_LABELS[tf]}{tf === timeframe ? '·' : ''}
              </button>
            ))}
          </div>
          {formingHot && (
            <span className="flex items-center gap-1 text-[10px] font-mono text-warn animate-pulse">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-warn" />
              SETUP
            </span>
          )}
          {countdown !== null && (
            <span className="text-[10px] font-mono tabular-nums text-dim">
              next bar{' '}
              <span className={countdown === 'now' ? 'text-accent animate-pulse-soft' : 'text-ink'}>
                {countdown}
              </span>
            </span>
          )}
        </div>
      </div>

      {/* Chart + signal panel side by side */}
      <div style={{ display: 'flex' }}>
        {/* Price chart — 4/5 width */}
        <div style={{ flex: 4, position: 'relative', minWidth: 0 }}>
          <div ref={containerRef} />
        </div>

        {/* Signal conditions — 1/5 width */}
        <div style={{
          flex: 1,
          borderLeft: '1px solid #003a00',
          background: '#000',
          height: CHART_HEIGHT,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          fontFamily: "'JetBrains Mono', monospace",
        }}>
          {/* Panel header */}
          <div style={{
            padding: '7px 12px 6px',
            borderBottom: '1px solid #003a00',
            color: '#00aa22',
            fontSize: 9,
            letterSpacing: '0.4em',
            textTransform: 'uppercase' as const,
            flexShrink: 0,
          }}>
            Conditions
          </div>

          {/* Checklist items */}
          <div style={{ flex: 1, overflowY: 'auto' as const }}>
            {inst ? (
              <>
                <CheckItem
                  label={inst.killzone.active ? (inst.killzone.name ?? 'Killzone') : 'Killzone'}
                  active={inst.killzone.active}
                  detail={inst.killzone.active ? 'Session open' : 'Outside hours'}
                />
                <CheckItem
                  label="Sweep"
                  active={inst.sweeps_pending.length > 0}
                  detail={
                    inst.sweeps_pending.length > 0
                      ? `${inst.sweeps_pending[0].side === 'high' ? 'High' : 'Low'} @ ${inst.sweeps_pending[0].swept_price}`
                      : undefined
                  }
                  muted={!inst.killzone.active}
                />
                <CheckItem
                  label="Displ + FVG"
                  active={inst.displacement_candidate !== null}
                  detail={
                    inst.displacement_candidate
                      ? inst.displacement_candidate.side
                      : inst.sweeps_pending.length > 0
                      ? 'Waiting…'
                      : undefined
                  }
                  muted={inst.sweeps_pending.length === 0}
                />
                {inst.htf && <HtfCheckItem htf={inst.htf} />}
                <VpCheckItem vp={inst.vp} />
                {inst.cooldown_bars_remaining > 0 && (
                  <CheckItem
                    label="Cooldown"
                    active={false}
                    detail={`${inst.cooldown_bars_remaining} bar${inst.cooldown_bars_remaining !== 1 ? 's' : ''} · suppressed`}
                  />
                )}
              </>
            ) : (
              <div style={{ padding: '12px', color: '#1a3d1a', fontSize: 11 }}>
                Waiting…
              </div>
            )}
          </div>

          {/* ATR footer */}
          {inst?.atr && (
            <div style={{
              padding: '5px 12px',
              borderTop: '1px solid #003a00',
              color: '#00aa22',
              fontSize: 10,
              letterSpacing: '0.1em',
              flexShrink: 0,
            }}>
              ATR {parseFloat(inst.atr).toFixed(2)}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
