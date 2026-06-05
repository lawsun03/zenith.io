# Zenith UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reskin the live trading dashboard from the green/matrix theme to a calm dark-navy "Zenith" terminal: full-width header, centered floating island, gradient + geometric background, Barlow/IBM Plex Mono fonts, a tabbed Activity feed, and a full-width chart with VP and StrategyDebug removed.

**Architecture:** Pure frontend reskin + layout reorg. The data layer (`useStream` WebSocket, all REST endpoints) is unchanged except dropping the now-dead `onVpUpdate` chart callback. The Tailwind palette swap auto-restyles every component that uses theme tokens (`text-accent`, `bg-panel`, `border-border`, etc.); the remaining work is the layout shell, three new presentational components, and surgical edits to `BarChart`.

**Tech Stack:** React 18 + TypeScript, Vite, Tailwind CSS v3.4, lightweight-charts v5.2, Google Fonts (Barlow + IBM Plex Mono).

**Reference mockup:** `.superpowers/brainstorm/1198-1780686678/content/zenith-v11.html`
**Spec:** `docs/superpowers/specs/2026-06-05-zenith-ui-redesign-design.md`

**Verification model:** This is a visual redesign; there is no component test runner in this project. The verification gate for every task is: `cd frontend && npm run build` exits 0 (TypeScript typecheck + Vite build), plus a visual check at `localhost:5173` with no console errors. Run `npm run dev` once at the start and keep it running.

---

## File Structure

**New components**
- `frontend/src/components/ZenithBackground.tsx` — fixed CSS gradient + geometric-triangle atmosphere (replaces `MatrixRain` + `GlowOverlay`).
- `frontend/src/components/ConditionsPanel.tsx` — the relocated per-instrument setup checklist (killzone / sweep / displacement+FVG / HTF; **no VP row**), owns the `/api/setup_state` poll. Extracted out of `BarChart`.
- `frontend/src/components/ActivityRow.tsx` — one calm feed row, switches on `item.kind` (signal/fill/reconcile). Replaces `SignalRow`/`FillRow`/`ReconcileRow`.
- `frontend/src/components/ActivityFeed.tsx` — right-column panel with tabs All / Signals / Fills / Recon / Setup. Renders `ActivityRow` lists and `ConditionsPanel` for the Setup tab.

**Rewritten/edited**
- `frontend/index.html` — fonts + title.
- `frontend/tailwind.config.js` — Zenith palette + fonts.
- `frontend/src/index.css` — base background, scrollbars, slider; drop matrix utilities.
- `frontend/src/components/Header.tsx` — Zenith header (logic preserved).
- `frontend/src/components/MetricCard.tsx` + `MetricsGrid.tsx` — flat calm cards.
- `frontend/src/components/BarChart.tsx` — remove VP overlay + inline conditions; restyle chart; full width.
- `frontend/src/App.tsx` — new layout shell + page-head + grid; remove dead components.
- `frontend/src/hooks/useStream.ts` — drop `onVpUpdate`.

**Deleted**
- `frontend/src/components/MatrixRain.tsx`
- `frontend/src/components/GlowOverlay.tsx`
- `frontend/src/components/StrategyDebug.tsx`
- `frontend/src/components/FeedSection.tsx`
- `frontend/src/components/SignalRow.tsx`
- `frontend/src/components/FillRow.tsx`
- `frontend/src/components/ReconcileRow.tsx`

**Untouched:** `useConfig.ts`, `useKillzone.ts`, `ConfigPanel.tsx`, `ForceSignalPanel.tsx`, `LockoutBanner.tsx`, `BacktestsPage.tsx`, `pages/Analytics.tsx`, `utils/format.ts`, `types.ts`, all backend. (These auto-adopt the new palette via Tailwind tokens.)

---

## Task 1: Theme foundation — fonts, palette, base CSS

This task is the base everything else builds on. After it, the existing (old-layout) app already shifts to navy/blue because components use theme tokens.

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/tailwind.config.js`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Swap fonts + title in `index.html`**

Replace the `<title>` line and the Google Fonts `<link>` (currently JetBrains Mono + Space Mono) so the file reads:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Zenith</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Barlow:wght@300;400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
</head>
<body>
  <div id="root"></div>
  <script type="module" src="/src/main.tsx"></script>
</body>
</html>
```

- [ ] **Step 2: Rewrite the Tailwind theme**

Replace the whole `theme.extend` block in `frontend/tailwind.config.js` so the file reads:

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Barlow', 'sans-serif'],
        mono: ['IBM Plex Mono', 'monospace'],
      },
      colors: {
        bg:       '#070b14',
        panel:    'rgba(9,15,29,0.66)',
        'panel-hi':'rgba(11,19,37,0.78)',
        border:   'rgba(255,255,255,0.055)',
        'border-hi':'rgba(255,255,255,0.10)',
        ink:      '#e9f1ff',
        dim:      '#a9bbd9',
        faint:    '#6c82a8',
        accent:   '#2563eb',
        'accent-ink':'#9cc4fd',
        good:     '#3ee0a5',
        warn:     '#fbbf24',
        danger:   '#f87171',
        grid:     'rgba(255,255,255,0.03)',
      },
      keyframes: {
        'pulse-soft': { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0.85' } },
        'fade-up':    { from: { opacity: '0', transform: 'translateY(8px)' }, to: { opacity: '1', transform: 'translateY(0)' } },
        blink:        { '0%, 100%': { opacity: '1' }, '50%': { opacity: '0.3' } },
      },
      animation: {
        'pulse-soft': 'pulse-soft 2.4s ease-in-out infinite',
        'fade-up':    'fade-up 0.5s cubic-bezier(0.22,1,0.36,1) forwards',
        blink:        'blink 2.2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
```

Note: `dim` keeps its name but is now light blue-grey (legible). `ink`/`accent`/`good`/`warn`/`danger` keep their names so every component using those tokens reskins automatically. New tokens: `faint`, `accent-ink`, `panel-hi`, `border-hi`.

- [ ] **Step 3: Rewrite `index.css` base + utilities**

Replace the entire contents of `frontend/src/index.css` with:

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  html, body {
    background-color: #070b14;
    font-family: 'Barlow', sans-serif;
    font-weight: 400;
    font-feature-settings: "tnum" 1;
    color: #e9f1ff;
  }
}

@layer utilities {
  /* danger hatch (used by LockoutBanner) — re-tinted to the new danger red */
  .hatch {
    background-image: repeating-linear-gradient(
      45deg,
      rgba(248,113,113,0.08) 0,
      rgba(248,113,113,0.08) 4px,
      transparent 4px,
      transparent 12px
    );
  }

  .feed::-webkit-scrollbar        { width: 3px; }
  .feed::-webkit-scrollbar-track  { background: transparent; }
  .feed::-webkit-scrollbar-thumb  { background: rgba(255,255,255,0.08); border-radius: 2px; }

  .slider-accent {
    -webkit-appearance: none; appearance: none;
    height: 4px; background: rgba(37,99,235,0.15); border-radius: 2px; outline: none;
  }
  .slider-accent::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 14px; height: 14px; border-radius: 50%;
    background: #2563eb; cursor: pointer; border: 2px solid #070b14;
  }
  .slider-accent::-moz-range-thumb {
    width: 14px; height: 14px; border-radius: 50%;
    background: #2563eb; cursor: pointer; border: 2px solid #070b14;
  }
}
```

This drops the matrix-only `.scanlines` and `.screen-glow-*` utilities (their consumers are deleted in later tasks) and re-tints `.hatch` + `.slider-accent`.

- [ ] **Step 4: Build to verify the theme compiles**

Run: `cd frontend && npm run build`
Expected: exits 0, no TS errors. (The old layout still renders; it now uses navy/blue tokens. Some components still reference hardcoded greens — fixed in later tasks.)

- [ ] **Step 5: Commit**

```bash
git add frontend/index.html frontend/tailwind.config.js frontend/src/index.css
git commit -m "feat(ui): Zenith theme foundation — fonts, palette, base CSS"
```

---

## Task 2: ZenithBackground component

**Files:**
- Create: `frontend/src/components/ZenithBackground.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/ZenithBackground.tsx` with:

```tsx
/**
 * Fixed, non-interactive atmospheric background: ambient blue/violet glow pools
 * plus four hard-edged geometric corner triangles and a top light streak.
 * Sits at z-index 0; all app content sits above it. Replaces the MatrixRain
 * canvas — pure CSS, no animation loop, no per-frame work.
 */
export function ZenithBackground() {
  return (
    <div aria-hidden className="fixed inset-0 z-0 overflow-hidden pointer-events-none">
      {/* ambient glow pools */}
      <div
        className="absolute inset-0"
        style={{
          background:
            'radial-gradient(ellipse 60% 50% at -10% 5%, rgba(20,62,200,0.42) 0%, transparent 56%),' +
            'radial-gradient(ellipse 48% 52% at 112% 100%, rgba(88,28,135,0.26) 0%, transparent 54%),' +
            'radial-gradient(ellipse 35% 35% at 50% 50%, rgba(7,12,23,0.5) 0%, transparent 72%)',
        }}
      />
      {/* top-right royal-blue triangle */}
      <div
        className="absolute"
        style={{
          top: -120, right: -100, width: 460, height: 460,
          background: 'conic-gradient(from 220deg at 100% 0%, rgba(20,62,200,0.66) 0deg, rgba(29,78,216,0.42) 36deg, transparent 64deg)',
          clipPath: 'polygon(100% 0%, 100% 100%, 0% 0%)',
        }}
      />
      <div
        className="absolute"
        style={{
          top: -60, right: -50, width: 250, height: 250,
          background: 'linear-gradient(150deg, rgba(96,165,250,0.24) 0%, transparent 54%)',
          clipPath: 'polygon(100% 0%, 100% 100%, 0% 0%)',
        }}
      />
      {/* bottom-right violet triangle */}
      <div
        className="absolute"
        style={{
          bottom: -110, right: -80, width: 380, height: 380,
          background: 'conic-gradient(from 40deg at 100% 100%, rgba(76,29,149,0.46) 0deg, transparent 72deg)',
          clipPath: 'polygon(100% 100%, 0% 100%, 100% 0%)',
        }}
      />
      {/* bottom-left echo */}
      <div
        className="absolute"
        style={{
          bottom: -80, left: -60, width: 240, height: 240,
          background: 'linear-gradient(50deg, rgba(20,62,200,0.18) 0%, transparent 56%)',
          clipPath: 'polygon(0% 100%, 100% 100%, 0% 0%)',
        }}
      />
      {/* top light streak */}
      <div
        className="absolute top-0 left-0 right-0"
        style={{
          height: 1,
          background: 'linear-gradient(90deg, transparent 0%, rgba(37,99,235,0.42) 32%, rgba(99,102,241,0.30) 66%, transparent 100%)',
        }}
      />
    </div>
  )
}
```

- [ ] **Step 2: Build to verify it compiles**

Run: `cd frontend && npm run build`
Expected: exits 0. (Not yet rendered; wired in Task 8.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ZenithBackground.tsx
git commit -m "feat(ui): ZenithBackground gradient + geometric atmosphere"
```

---

## Task 3: Header

Rewrite the header markup to the Zenith style while preserving every handler and read-out (test trade, flatten, restart, clear lockout, sync badge, lockout badge, clock, killzone pill, nav links, config gear).

**Files:**
- Modify: `frontend/src/components/Header.tsx`

- [ ] **Step 1: Replace the component**

Replace the entire return-JSX of `frontend/src/components/Header.tsx` (keep all the hooks/handlers above the `return` exactly as they are — `now`, `testMsg`, `flattenMsg`, `restartMsg`, `handleTestTrade`, `handleFlatten`, `handleClearLockout`, `handleRestart`, `isLocked`, `isDriftLock`, `sync`, `syncTone`, `syncLabel`). Replace only the `return (...)` block with:

```tsx
  return (
    <header className="flex items-center px-7 h-[54px] shrink-0 bg-[rgba(7,11,22,0.85)] backdrop-blur-xl border-b border-border animate-fade-up">
      {/* identity */}
      <div className="flex items-center gap-3 pr-5 border-r border-border">
        <div
          className="w-7 h-7 rounded-md flex items-center justify-center text-xs font-medium text-white shrink-0"
          style={{ background: 'linear-gradient(135deg,#2563eb 0%,#7c3aed 100%)', boxShadow: '0 0 16px rgba(37,99,235,0.4)' }}
        >
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
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-[3px] rounded-full border text-[11px] ${
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
            className="inline-flex items-center gap-1.5 px-2.5 py-[3px] rounded-full border border-accent/30 bg-accent/[0.12] text-[11px] text-accent-ink"
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
            <span className="text-[10px] tracking-wider text-danger border border-danger/40 px-2 py-0.5 rounded" title={status?.lockout?.message ?? ''}>
              LOCKED: {status?.lockout?.code}
            </span>
            {isDriftLock && (
              <button onClick={handleClearLockout} className="text-[10px] tracking-wider uppercase border border-danger/30 text-danger/80 px-2 py-0.5 rounded hover:bg-danger/10">
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
        <a href="/analytics" target="_blank" rel="noreferrer" className="text-[11px] text-dim px-2.5 py-1 rounded hover:bg-white/5 hover:text-ink" title="Open analytics">Analytics ↗</a>
        <a href="/backtests" target="_blank" rel="noreferrer" className="text-[11px] text-dim px-2.5 py-1 rounded hover:bg-white/5 hover:text-ink" title="Open backtests">Backtests ↗</a>
        <a href="/api/export/trades.csv" download="trades.csv" className="text-[11px] text-dim px-2.5 py-1 rounded hover:bg-white/5 hover:text-ink" title="Export trades CSV">↓ CSV</a>
        <button onClick={onConfigOpen} className="text-sm text-dim px-2 py-1 rounded hover:bg-white/5 hover:text-ink" title="Configuration">⚙</button>
      </div>
    </header>
  )
```

Note: the `DOT` const is no longer used (the pill uses `bg-current`); leave the `LABEL` const in place (still used). If TypeScript flags `DOT` as unused, delete the `DOT` const declaration.

- [ ] **Step 2: Build to verify**

Run: `cd frontend && npm run build`
Expected: exits 0. If it fails on unused `DOT`, remove the `const DOT = {...}` block and rebuild.

- [ ] **Step 3: Visual check**

At `localhost:5173`: the header is full-width navy glass with the gradient "Z" mark, "Zenith" wordmark, status pill, killzone pill, clock, and right-side controls. (The body below is still old-layout — that's fine.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Header.tsx
git commit -m "feat(ui): Zenith full-width header"
```

---

## Task 4: Metric cards

**Files:**
- Modify: `frontend/src/components/MetricCard.tsx`
- Modify: `frontend/src/components/MetricsGrid.tsx`

- [ ] **Step 1: Rewrite `MetricCard.tsx`**

Replace the entire contents of `frontend/src/components/MetricCard.tsx` with:

```tsx
type Tone = 'neutral' | 'good' | 'warn' | 'bad'

const VALUE_CLS: Record<Tone, string> = {
  neutral: 'text-ink',
  good:    'text-good',
  warn:    'text-warn',
  bad:     'text-danger',
}

const DOT_CLS: Record<Tone, string> = {
  neutral: 'bg-faint',
  good:    'bg-good',
  warn:    'bg-warn',
  bad:     'bg-danger',
}

interface Props {
  label: string
  primary: string
  secondary?: string
  tone?: Tone
  // Accepted for call-site compatibility; intentionally unused (the values are
  // held steady — motion on the main numbers hurts legibility).
  pulse?: boolean
}

export function MetricCard({ label, primary, secondary, tone = 'neutral' }: Props) {
  return (
    <div className="bg-panel backdrop-blur-md border border-border rounded-[10px] px-[18px] py-4 hover:border-border-hi transition-colors">
      <div className="flex items-center gap-[7px] mb-[9px]">
        <span className={`w-[5px] h-[5px] rounded-full shrink-0 ${DOT_CLS[tone]}`} />
        <span className="text-[9px] tracking-[0.1em] text-faint uppercase font-mono">{label}</span>
      </div>
      <div className={`text-2xl font-medium leading-none tabular-nums ${VALUE_CLS[tone]}`}>
        {primary}
      </div>
      {secondary && <div className="text-[10px] text-faint mt-[7px] font-mono">{secondary}</div>}
    </div>
  )
}
```

- [ ] **Step 2: Restyle the grid wrapper in `MetricsGrid.tsx`**

In `frontend/src/components/MetricsGrid.tsx`, change the outer grid `className` and the empty-state, leaving all the tone-logic untouched. Replace the empty-state return:

```tsx
  if (!status) {
    return <div className="bg-panel border border-border rounded-[10px] p-5 text-faint text-sm">Awaiting state…</div>
  }
```

and replace the grid wrapper element:

```tsx
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-[11px]">
```

(Note: drop the old `gap-px bg-border border border-border` pattern — cards now have their own borders and float with a real gap.)

- [ ] **Step 3: Build to verify**

Run: `cd frontend && npm run build`
Expected: exits 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/MetricCard.tsx frontend/src/components/MetricsGrid.tsx
git commit -m "feat(ui): flat Zenith metric cards"
```

---

## Task 5: ConditionsPanel (extract from BarChart, drop VP)

Move the per-instrument setup checklist into its own component so the chart can go full-width and the checklist can live in the Activity feed's Setup tab. **The VP row is removed.**

**Files:**
- Create: `frontend/src/components/ConditionsPanel.tsx`

- [ ] **Step 1: Create the component**

Create `frontend/src/components/ConditionsPanel.tsx` with the checklist logic ported from `BarChart` (killzone / sweep / displacement+FVG / HTF / cooldown), restyled, **with no `VpCheckItem`**, and selecting the instrument matching `activeSymbol`:

```tsx
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
```

- [ ] **Step 2: Build to verify**

Run: `cd frontend && npm run build`
Expected: exits 0. (Not yet rendered; consumed by ActivityFeed in Task 7.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ConditionsPanel.tsx
git commit -m "feat(ui): extract ConditionsPanel (VP row removed)"
```

---

## Task 6: BarChart — remove VP overlay, restyle, full width

Strip the VP histogram overlay and the inline conditions panel; restyle the chart to the Zenith palette; make the chart fill its frame.

**Files:**
- Modify: `frontend/src/components/BarChart.tsx`

- [ ] **Step 1: Remove the VP `VpProfile` import and conditions sub-components**

At the top of `BarChart.tsx`, change the imports to drop `VpProfile` (and drop `useCallback`, which becomes unused once `pollSetupState` is deleted):

```tsx
import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries } from 'lightweight-charts'
import type { ChartCallbacks } from '../hooks/useStream'
```

Delete these now-unused blocks entirely:
- the `SetupInstrument`, `SetupState` interfaces
- the `S` style object
- the `CheckItem`, `VpCheckItem`, `HtfCheckItem` functions
- the `pollSetupState` callback + its `useEffect`
- the `setupState`/`setSetupState` state and the `formingHot` const
- the `inst` const near the bottom

Keep: `TF_SECONDS`, `TF_LABELS`, `Props`, `isValidBar`, the chart `useEffect`, the countdown `useEffect`, the viewTf/timeframe/activeSymbol refs and effects, `seriesRef`, `chartRef`.

Add an instrument-name map below `TF_LABELS`:

```tsx
const INSTRUMENT_NAMES: Record<string, string> = {
  MGC: 'Micro Gold', MNQ: 'Micro Nasdaq', MES: 'Micro S&P',
  GC: 'Gold', NQ: 'Nasdaq', ES: 'S&P 500',
}
```

Also remove `CHART_HEIGHT` if it is only used by the deleted conditions column (it is — delete the `const CHART_HEIGHT = 320` line).

- [ ] **Step 2: Remove all VP overlay code inside the chart `useEffect`**

Inside the main chart `useEffect`, delete:
- the `let currentVpProfile: VpProfile | null = null` line
- the entire `vpCanvas` creation + append block
- the entire `drawHistogram` function
- the entire `fetchAndDrawVp` function
- `chart.timeScale().subscribeVisibleLogicalRangeChange(drawHistogram)`
- `const syncId = setInterval(drawHistogram, 200)`
- `const vpRefetchId = setInterval(fetchAndDrawVp, 60_000)`
- the `fetchAndDrawVp()` call after the initial bar load
- in `callbacksRef.current`, delete the entire `onVpUpdate() { ... }` method
- in the cleanup `return () => {...}`, delete `clearInterval(syncId)`, `clearInterval(vpRefetchId)`, `chart.timeScale().unsubscribeVisibleLogicalRangeChange(drawHistogram)`, and the `if (el.contains(vpCanvas)) el.removeChild(vpCanvas)` line.

- [ ] **Step 3: Restyle the chart theme**

Replace the `createChart(el, {...})` options object with:

```tsx
    const chart = createChart(el, {
      autoSize: true,
      height: 320,
      layout: {
        background: { color: 'transparent' },
        textColor:  '#6c82a8',
        fontFamily: "'IBM Plex Mono', monospace",
        fontSize:   11,
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.03)' },
        horzLines: { color: 'rgba(255,255,255,0.03)' },
      },
      crosshair: {
        vertLine: { color: 'rgba(37,99,235,0.4)', labelBackgroundColor: '#1e3a8a' },
        horzLine: { color: 'rgba(37,99,235,0.4)', labelBackgroundColor: '#1e3a8a' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.05)' },
      localization: {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        timeFormatter: ((time: any) => fmtChartDateTime(Number(time))) as any,
      },
      timeScale: {
        borderColor:    'rgba(255,255,255,0.05)',
        timeVisible:    true,
        secondsVisible: false,
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        tickMarkFormatter: ((time: any) => fmtChartTime(Number(time))) as any,
      },
    })
```

And the candlestick series colors:

```tsx
    const series = chart.addSeries(CandlestickSeries as any, {
      upColor:         '#3ee0a5',
      downColor:       '#f87171',
      borderUpColor:   '#3ee0a5',
      borderDownColor: '#f87171',
      wickUpColor:     'rgba(62,224,165,0.6)',
      wickDownColor:   'rgba(248,113,113,0.6)',
    })
```

- [ ] **Step 4: Replace the component's return JSX (chart only, full width)**

Replace the entire `return (...)` at the bottom of the component with:

```tsx
  const instLabel = activeSymbol ? (INSTRUMENT_NAMES[activeSymbol] ?? activeSymbol) : ''
  return (
    <div className="flex-1 min-h-0 bg-panel-hi backdrop-blur-md border border-border rounded-[10px] overflow-hidden flex flex-col animate-fade-up">
      {/* header bar */}
      <div className="flex items-center justify-between px-[18px] py-2.5 border-b border-border shrink-0">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-medium text-ink">{activeSymbol ?? '—'}</span>
          <span className="text-[10px] text-faint font-mono">{instLabel}{instLabel && ' · '}{TF_LABELS[viewTf] ?? viewTf}</span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-0.5">
            {Object.keys(TF_SECONDS).map(tf => (
              <button
                key={tf}
                onClick={() => setViewTf(tf)}
                className={`text-[10px] font-mono px-1.5 py-0.5 rounded transition-colors ${viewTf === tf ? 'text-accent-ink bg-accent/15' : 'text-faint hover:text-dim'}`}
              >
                {TF_LABELS[tf]}{tf === timeframe ? '·' : ''}
              </button>
            ))}
          </div>
          {countdown !== null && (
            <span className="text-[10px] font-mono tabular-nums text-faint">
              next{' '}
              <span className={countdown === 'now' ? 'text-accent-ink animate-pulse-soft' : 'text-dim'}>{countdown}</span>
            </span>
          )}
        </div>
      </div>
      {/* chart canvas */}
      <div className="flex-1 min-h-0 relative">
        <div ref={containerRef} className="absolute inset-0" />
      </div>
    </div>
  )
```

- [ ] **Step 5: Build to verify**

Run: `cd frontend && npm run build`
Expected: exits 0. (`useState` is still used by `countdown` and `viewTf`. If TS reports any other unused symbol, remove that specific import and rebuild.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/BarChart.tsx
git commit -m "feat(ui): full-width Zenith chart, remove VP overlay + inline conditions"
```

---

## Task 7: ActivityRow + ActivityFeed

Merge the three feeds into one tabbed panel; add the Setup tab that renders `ConditionsPanel`.

**Files:**
- Create: `frontend/src/components/ActivityRow.tsx`
- Create: `frontend/src/components/ActivityFeed.tsx`

- [ ] **Step 1: Create `ActivityRow.tsx`**

```tsx
import type { JournalItem, SignalPayload, FillPayload, ReconcilePayload } from '../types'
import { fmtBarTs, fmtMoney } from '../utils/format'

const DOT: Record<string, string> = {
  signal: 'bg-accent', fill: 'bg-good', reconcile: 'bg-faint',
}

function Shell({ dot, type, ts, main, mainCls, sub, amt, amtCls }: {
  dot: string; type: string; ts: string; main: string; mainCls?: string
  sub?: string; amt?: string; amtCls?: string
}) {
  return (
    <div className="px-5 py-3.5 hover:bg-white/[0.018] transition-colors">
      <div className="flex items-center gap-2 mb-1.5">
        <span className={`w-[5px] h-[5px] rounded-full shrink-0 ${dot}`} />
        <span className="text-[9px] tracking-[0.08em] uppercase text-faint font-mono">{type}</span>
        <span className="ml-auto text-[10px] text-faint font-mono tabular-nums">{ts}</span>
      </div>
      <div className="flex items-baseline justify-between gap-2.5 pl-3">
        <div className="min-w-0">
          <div className={`text-[13px] leading-snug ${mainCls ?? 'text-ink'}`}>{main}</div>
          {sub && <div className="text-[10px] text-faint font-mono mt-0.5">{sub}</div>}
        </div>
        {amt && <div className={`text-[13px] font-medium font-mono tabular-nums whitespace-nowrap ${amtCls ?? 'text-dim'}`}>{amt}</div>}
      </div>
    </div>
  )
}

export function ActivityRow({ item }: { item: JournalItem }) {
  const ts = fmtBarTs(item.ts)

  if (item.kind === 'signal') {
    const s = item.payload as SignalPayload
    return (
      <Shell
        dot={DOT.signal} type="Signal" ts={ts}
        main={`${s.side === 'long' ? 'Long' : 'Short'} · ${s.killzone}`}
        sub={s.rationale}
        amt={s.outcome.placed ? `×${s.outcome.allowed_size}` : s.outcome.reason}
        amtCls={s.outcome.placed ? 'text-accent-ink' : 'text-faint'}
      />
    )
  }

  if (item.kind === 'fill') {
    const f = item.payload as FillPayload
    const pnl = Number(f.realized_pnl_delta)
    const amtCls = f.is_entry ? 'text-ink' : pnl > 0 ? 'text-good' : pnl < 0 ? 'text-danger' : 'text-dim'
    return (
      <Shell
        dot={f.is_entry ? 'bg-good' : pnl < 0 ? 'bg-danger' : 'bg-good'}
        type={f.is_entry ? 'Entry' : 'Exit'} ts={ts}
        main={`${f.side === 'long' ? 'Long' : 'Short'} ×${f.size}`}
        mainCls={f.is_entry ? 'text-good' : pnl < 0 ? 'text-danger' : 'text-ink'}
        sub={`@ ${f.fill_price}`}
        amt={f.is_entry ? f.fill_price : fmtMoney(f.realized_pnl_delta, { signed: true })}
        amtCls={amtCls}
      />
    )
  }

  // reconcile
  const r = item.payload as ReconcilePayload
  return (
    <Shell
      dot={r.drift_detected ? 'bg-danger' : 'bg-faint'}
      type="Reconcile" ts={ts}
      main={r.drift_detected ? `Drift ${r.drift_kind ?? ''}` : 'OK — no drift'}
      mainCls={r.drift_detected ? 'text-danger' : 'text-ink'}
      sub={`broker ${r.broker_open_contracts} · internal ${r.internal_open_contracts}`}
      amt={r.drift_detected ? '!' : '✓'}
      amtCls={r.drift_detected ? 'text-danger' : 'text-good'}
    />
  )
}
```

- [ ] **Step 2: Create `ActivityFeed.tsx`**

```tsx
import { useState } from 'react'
import type { JournalItem } from '../types'
import { ActivityRow } from './ActivityRow'
import { ConditionsPanel } from './ConditionsPanel'

type Tab = 'all' | 'signals' | 'fills' | 'recon' | 'setup'

const TABS: { id: Tab; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'signals', label: 'Signals' },
  { id: 'fills', label: 'Fills' },
  { id: 'recon', label: 'Recon' },
  { id: 'setup', label: 'Setup' },
]

interface Props {
  signals: JournalItem[]
  fills: JournalItem[]
  reconciles: JournalItem[]
  activeSymbol?: string
}

export function ActivityFeed({ signals, fills, reconciles, activeSymbol }: Props) {
  const [tab, setTab] = useState<Tab>('all')

  const merged = [...signals, ...fills, ...reconciles]
    .sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime())
    .slice(0, 60)

  const list =
    tab === 'all' ? merged
    : tab === 'signals' ? signals
    : tab === 'fills' ? fills
    : tab === 'recon' ? reconciles
    : []

  return (
    <div className="bg-panel backdrop-blur-xl border border-border rounded-[10px] flex flex-col overflow-hidden animate-fade-up">
      <div className="px-4 pt-4 shrink-0">
        <div className="text-[10px] font-mono tracking-[0.12em] uppercase text-faint mb-3">Activity</div>
        <div className="flex gap-4 border-b border-border">
          {TABS.map(t => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`text-[11px] pb-2.5 -mb-px border-b-2 transition-colors ${
                tab === t.id ? 'text-accent-ink border-accent' : 'text-faint border-transparent hover:text-dim'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="feed flex-1 overflow-y-auto">
        {tab === 'setup' ? (
          <ConditionsPanel activeSymbol={activeSymbol} />
        ) : list.length === 0 ? (
          <div className="px-5 py-5 text-faint text-xs">No entries yet</div>
        ) : (
          list.map((item, i) => <ActivityRow key={`${item.kind}-${item.ts}-${i}`} item={item} />)
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 3: Build to verify**

Run: `cd frontend && npm run build`
Expected: exits 0. (Not yet rendered; wired in Task 8.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/ActivityRow.tsx frontend/src/components/ActivityFeed.tsx
git commit -m "feat(ui): tabbed Activity feed with Setup tab"
```

---

## Task 8: App.tsx assembly + useStream cleanup

Wire the new layout shell, page-head, centered island, and components; remove the dead `onVpUpdate` callback.

**Files:**
- Modify: `frontend/src/hooks/useStream.ts`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Drop `onVpUpdate` from `useStream.ts`**

In `frontend/src/hooks/useStream.ts`, remove `onVpUpdate` from the `ChartCallbacks` interface:

```tsx
export interface ChartCallbacks {
  onBar?: (bar: BarEvent) => void
  onFillMarker?: (time: number, isEntry: boolean, side: 'long' | 'short', pnl: number) => void
  onReset?: () => void
}
```

Then in the `bar` message handler, delete the day-rollover VP refresh — remove these lines:

```tsx
          const barUtcDate = msg.ts.slice(0, 10)
          if (lastBarUtcDate !== null && barUtcDate !== lastBarUtcDate) {
            chartCbRef?.current?.onVpUpdate?.()
          }
          lastBarUtcDate = barUtcDate
```

and remove the now-unused `let lastBarUtcDate: string | null = null` declaration earlier in `connect()`.

- [ ] **Step 2: Rewrite `App.tsx`**

Replace the entire contents of `frontend/src/App.tsx` with:

```tsx
import { useEffect, useRef, useState } from 'react'
import { useStream } from './hooks/useStream'
import type { ChartCallbacks } from './hooks/useStream'
import { useConfig } from './hooks/useConfig'
import { useKillzone } from './hooks/useKillzone'
import { Header } from './components/Header'
import { LockoutBanner } from './components/LockoutBanner'
import { MetricsGrid } from './components/MetricsGrid'
import { BarChart } from './components/BarChart'
import { ActivityFeed } from './components/ActivityFeed'
import { ConfigPanel } from './components/ConfigPanel'
import { BacktestsPage } from './components/BacktestsPage'
import { AnalyticsPage } from './pages/Analytics'
import { ForceSignalPanel } from './components/ForceSignalPanel'
import { ZenithBackground } from './components/ZenithBackground'

export default function App() {
  // Path-based router — /backtests and /analytics are standalone pages.
  const path = window.location.pathname
  if (path.startsWith('/backtests')) return <BacktestsPage />
  if (path.startsWith('/analytics')) return <AnalyticsPage />

  const chartCbRef = useRef<ChartCallbacks>({})
  const { config, saveConfig, saving, saveError } = useConfig()

  const symbols = (config?.instruments?.length ?? 0) > 0
    ? config!.instruments!
    : config?.instrument ? [config.instrument] : ['MGC']
  const [activeSymbol, setActiveSymbol] = useState<string>('MGC')
  const activeSymbolRef = useRef(activeSymbol)
  useEffect(() => { activeSymbolRef.current = activeSymbol }, [activeSymbol])

  const { status, signals, fills, reconciles, connState } = useStream(chartCbRef, activeSymbolRef)
  const [configOpen, setConfigOpen] = useState(false)
  const activeKillzone = useKillzone(config?.enabled_killzones)

  const now = new Date()
  const dateLabel = now.toLocaleDateString('en-US', {
    timeZone: 'America/Los_Angeles', weekday: 'long', month: 'long', day: 'numeric',
  })
  const yearLabel = now.toLocaleDateString('en-US', { timeZone: 'America/Los_Angeles', year: 'numeric' })

  const modeLabel = config?.mode === 'live' ? 'live' : 'paper'
  const contractsLabel = config ? `${config.entry_mode} · ${config.contracts} contracts` : ''

  return (
    <div className="min-h-screen flex flex-col h-screen overflow-hidden">
      <ZenithBackground />

      <div className="relative z-10 flex flex-col h-screen">
        <Header status={status} connState={connState} onConfigOpen={() => setConfigOpen(true)} mode={config?.mode} activeKillzone={activeKillzone} />
        <LockoutBanner lockout={status?.lockout ?? null} />

        <div className="flex-1 min-h-0 flex justify-center items-center overflow-hidden">
          <div className="w-full max-w-[1320px] h-full max-h-[820px] px-7 pt-[22px] pb-[26px] flex flex-col min-h-0 overflow-hidden">

            {/* page head */}
            <div className="flex items-end justify-between px-0.5 pb-5 shrink-0 animate-fade-up">
              <div>
                <div className="text-[22px] text-ink tracking-tight">Live Dashboard</div>
                <div className="text-[11px] text-faint font-mono mt-1">
                  <span className="text-accent-ink">iFVG · Combined Strategy</span> · {modeLabel}
                </div>
              </div>
              <div className="text-[11px] text-dim font-mono">{dateLabel} <span className="text-faint">·</span> {yearLabel}</div>
            </div>

            {/* body grid */}
            <div className="flex-1 min-h-0 grid grid-cols-[1fr_312px] gap-5 overflow-hidden">

              <div className="flex flex-col gap-4 min-h-0 overflow-hidden">
                {/* symbol row */}
                <div className="flex items-center justify-between px-0.5 shrink-0 animate-fade-up">
                  <div className="flex gap-[22px]">
                    {symbols.map(sym => (
                      <button
                        key={sym}
                        onClick={() => setActiveSymbol(sym)}
                        className={`text-[13px] tracking-wide pb-0.5 relative transition-colors ${
                          sym === activeSymbol ? 'text-ink' : 'text-faint hover:text-dim'
                        }`}
                      >
                        {sym}
                        {sym === activeSymbol && <span className="absolute left-0 right-0 -bottom-[7px] h-0.5 bg-accent rounded" />}
                      </button>
                    ))}
                  </div>
                  {contractsLabel && <span className="text-[10px] text-faint font-mono">{config?.timeframes?.[0]} · {contractsLabel}</span>}
                </div>

                <MetricsGrid status={status} />

                <BarChart callbacksRef={chartCbRef} timeframe={config?.timeframes?.[0]} activeSymbol={activeSymbol} />
              </div>

              <ActivityFeed signals={signals} fills={fills} reconciles={reconciles} activeSymbol={activeSymbol} />

            </div>
          </div>
        </div>

        {config?.mode === 'live' && (
          <div className="relative z-10 px-7 pb-4 shrink-0">
            <ForceSignalPanel />
          </div>
        )}
      </div>

      <ConfigPanel
        isOpen={configOpen}
        onClose={() => setConfigOpen(false)}
        config={config}
        onSave={saveConfig}
        saving={saving}
        saveError={saveError}
      />
    </div>
  )
}
```

Note: `MetricsGrid` is now constrained to the left column (full width of it) — its internal `grid-cols-2 lg:grid-cols-4` still produces a 4-across row at this width. `strategyState` is intentionally no longer destructured from `useStream` (StrategyDebug is gone); leaving it unused in the hook return is fine.

- [ ] **Step 3: Build to verify**

Run: `cd frontend && npm run build`
Expected: exits 0.

- [ ] **Step 4: Visual check at `localhost:5173`**

Confirm: full-width header; centered island with the gradient showing on both sides and below; page title; symbol tabs; 4 metric cards; full-width chart drawing candles; Activity feed on the right with working All/Signals/Fills/Recon/Setup tabs; no console errors. Click a symbol tab → chart + conditions follow. Click Setup tab → conditions checklist (no VP row).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useStream.ts frontend/src/App.tsx
git commit -m "feat(ui): assemble Zenith dashboard layout"
```

---

## Task 9: Delete dead files + final verification

**Files:**
- Delete: `MatrixRain.tsx`, `GlowOverlay.tsx`, `StrategyDebug.tsx`, `FeedSection.tsx`, `SignalRow.tsx`, `FillRow.tsx`, `ReconcileRow.tsx`

- [ ] **Step 1: Confirm nothing imports the dead files**

Run (from repo root):
```bash
cd frontend && npx tsc --noEmit
```
Expected: exits 0 (App.tsx no longer imports any of them; they are now orphaned).

Also grep to be safe:
```bash
grep -rE "MatrixRain|GlowOverlay|StrategyDebug|FeedSection|SignalRow|FillRow|ReconcileRow" frontend/src
```
Expected: no matches (or only the files' own definitions). If a match appears in another component, stop and resolve before deleting.

- [ ] **Step 2: Delete the orphaned files**

```bash
git rm frontend/src/components/MatrixRain.tsx \
       frontend/src/components/GlowOverlay.tsx \
       frontend/src/components/StrategyDebug.tsx \
       frontend/src/components/FeedSection.tsx \
       frontend/src/components/SignalRow.tsx \
       frontend/src/components/FillRow.tsx \
       frontend/src/components/ReconcileRow.tsx
```

- [ ] **Step 3: Final build**

Run: `cd frontend && npm run build`
Expected: exits 0, clean.

- [ ] **Step 4: Final visual pass + ship the bundle**

At `localhost:5173`: full Zenith dashboard, no console errors, no green theme anywhere on the live dashboard. Then build the production bundle the bot serves (see [[feedback_frozen_bars_after_frontend_changes]] — the bot serves `frontend/dist` on `:5175`, so the dashboard won't update there until rebuilt):

```bash
cd frontend && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add -A frontend
git commit -m "chore(ui): remove dead matrix-theme components"
```

---

## Self-review notes (already reconciled)

- **Spec coverage:** palette/fonts (T1), background (T2), header (T3), metrics (T4), conditions-without-VP (T5), full-width chart + VP-overlay removal (T6), tabbed feed + Setup (T7), layout shell + page-head + island + StrategyDebug removal + onVpUpdate removal (T8), dead-file deletion (T9). All spec sections map to a task.
- **Deferred (noted, not built):** live price number in the chart header (would add per-tick state); reskin of `/analytics` and `/backtests` pages (separate routes, auto-adopt palette tokens but keep their layout); `ForceSignalPanel` keeps its current markup (auto-reskinned by tokens) rendered below the island in live mode.
- **Type consistency:** `ChartCallbacks` loses `onVpUpdate` in T8 and T6 simultaneously (T6 removes the producer, T8 removes the interface member + consumer) — build only stays green once both are done, which is why T6 deletes the callback method and T8 deletes the type; if executing strictly task-by-task, T6's build passes because removing a method that satisfies an optional interface member is legal, and T8 then tightens the interface. `VpProfile` import removed from BarChart in T6; the type still exists in `types.ts` for any other consumer.
