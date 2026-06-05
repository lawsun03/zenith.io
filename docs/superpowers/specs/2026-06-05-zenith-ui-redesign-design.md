# Zenith — Dashboard UI Redesign

**Date:** 2026-06-05
**Status:** Approved design, ready for implementation plan
**Scope:** Frontend only. Live dashboard (`/`). No backend, broker, or data-layer changes.

---

## 1. Goal

Replace the green/matrix "hacker terminal" theme of the live dashboard with a
calm, modern **dark navy trading-terminal** aesthetic ("Zenith"), inspired by
the AIBots Dribbble reference. Rename the product from "topstep-bot" to
**Zenith**. Remove the non-functional `StrategyDebug` panel. Keep every existing
piece of functionality and live data wiring intact — this is a visual + layout
reskin, not a behavior change.

**Success criteria**
- Dashboard renders at `localhost:5173` with the Zenith theme, no console errors,
  `npm run build` clean (no TS errors).
- All live data still flows: metrics, chart (with markers/VP), signals, fills,
  reconciles, connection state, killzone, lockout, header actions.
- The bot does **not** require a restart to keep working — this is static-asset
  only; the bot serves the built `frontend/dist` on `:5175` (see
  [[feedback_frozen_bars_after_frontend_changes]] — run `npm run build`).
- No green matrix theme remains on the live dashboard.

**Out of scope (this spec):** the `/backtests` and `/analytics` pages. They are
separate routes/components and keep their current styling for now. Only shared
primitives they import (if any) must not break.

---

## 2. Visual language

Final approved mockup: `.superpowers/brainstorm/.../zenith-v11.html`.

### Palette (replaces the green theme in `tailwind.config.js`)

| Token        | Value                    | Use |
|--------------|--------------------------|-----|
| `bg`         | `#070b14`                | page background (deep navy-black) |
| `panel`      | `rgba(9,15,29,0.66)`     | glass card surface |
| `panel-hi`   | `rgba(11,19,37,0.78)`    | chart surface (slightly more opaque) |
| `border`     | `rgba(255,255,255,0.055)`| hairline card borders |
| `border-hi`  | `rgba(255,255,255,0.10)` | hover borders |
| `accent`     | `#2563eb`                | primary blue (active states, dots) |
| `accent-ink` | `#9cc4fd`                | readable blue text on dark |
| `good`       | `#3ee0a5`                | positive / up candles / healthy |
| `warn`       | `#fbbf24`                | caution / blocked |
| `danger`     | `#f87171`                | breach / stops / down candles |
| `ink`        | `#e9f1ff`                | primary text / values |
| `dim`        | `#a9bbd9`                | labels, secondary text (legible) |
| `faint`      | `#6c82a8`                | dimmest meta/timestamps (still legible) |

**Legibility rule (learned during design):** never use a text color darker than
`faint` (`#6c82a8`) for any text the user needs to read. The previous iteration
failed because tertiary text was near-invisible on the dark glass.

**Color discipline (the key to the calm look):** panels are flat — **no** top
accent stripes, **no** colored left-bars, **no** filled/bordered badges. Color
appears only where it carries meaning: P&L values, candle direction, and small
4-5px status dots. Everything else is monochrome `ink`/`dim`/`faint`.

### Typography

- **Body / UI:** `Barlow` weights 300/400/500. Default weight 400. Nothing bold
  unless semantically required (no 600/700 display text).
- **Numbers / labels / timestamps / chart:** `IBM Plex Mono` 400/500 for crisp
  tabular figures.
- Load via Google Fonts `<link>` in `frontend/index.html` (replacing the current
  JetBrains/Space Mono links). Update `tailwind.config.js` `fontFamily`:
  `sans → Barlow`, `mono → IBM Plex Mono`. Drop `display: Space Mono`.

### Background atmosphere

A fixed `z-index: 0` background layer (replaces `MatrixRain`):
- Radial ambient glow pools (blue top-left, violet bottom-right).
- Four hard-edged geometric triangles in the corners (`clip-path: polygon`,
  `conic-gradient`/`linear-gradient` fills) — the signature of the reference.
- A 1px horizontal light streak across the very top.
- Glass panels (`backdrop-filter: blur(14-20px)`) let the gradient bleed through.

This is a pure-CSS decorative component. No canvas, no animation loop (drop the
`MatrixRain` canvas entirely). One subtle entrance animation: panels fade-up with
staggered `animation-delay` on load.

---

## 3. Layout

Full-bleed header across the top; below it a **centered island** (max-width
1320px, max-height 820px, vertically centered) so the gradient background shows
on all sides and the content never stretches edge-to-edge on tall/zoomed-out
screens.

```
┌──────────────────────────────────────────────────────────────┐  ← header: full width
│ [Z] Zenith   ● Streaming  ▶ London   09:41 PT   acct   nav... │
└──────────────────────────────────────────────────────────────┘
        ┌──────────────── centered island (max 1320×820) ──────────────┐
        │  Live Dashboard                          Thursday, Jun 5 2026 │  ← page-head
        │  iFVG · Combined Strategy · paper/live                        │
        │  ┌────────────────────────────────┐ ┌──────────────────────┐ │
        │  │ MGC  MNQ  MES    1min·market   │ │ ACTIVITY             │ │  ← left col + right feed
        │  ├────┬────┬────┬────────────────┤ │ All Signals Fills    │ │
        │  │MLL │DLL │P&L │ Open           │ │ Recon Setup          │ │  ← metrics row (4 cards)
        │  ├────┴────┴────┴────────────────┤ ├──────────────────────┤ │
        │  │                                │ │ ● Entry   09:41:02   │ │
        │  │   TradingView chart            │ │   Long MGC ×2  2677  │ │
        │  │   (full width, flex:1)         │ │ ● Signal  09:40:58   │ │
        │  │                                │ │   ...                │ │
        │  └────────────────────────────────┘ └──────────────────────┘ │
        └──────────────────────────────────────────────────────────────┘
```

Grid: left column `1fr`, right column `312px`, gap `20px`. Left column is a
flex-column: sym-row (fixed) → metrics (fixed) → chart (flex:1).

---

## 4. Component-by-component plan

The data layer is unchanged: `useStream` (WebSocket `/api/stream`) still returns
`{ status, signals, fills, reconciles, strategyState, connState }`, and the same
REST endpoints (`/api/status`, `/api/bars`, `/api/forming-bar`, `/api/setup_state`,
`/api/vp/profile`, etc.) are used as-is. We restyle and reorganize the
presentation only.

### 4.1 `App.tsx`
- Replace `MatrixRain` + `GlowOverlay` + `scanlines` with the new CSS background
  component (`<ZenithBackground />`) and the centered-island layout shell.
- Add the **page-head** row (title "Live Dashboard" + strategy breadcrumb + date).
- Restructure body into the `1fr / 312px` grid described above.
- Remove the `StrategyDebug` import and usage entirely.
- Keep the path router (`/backtests`, `/analytics`) untouched.
- Keep the symbol-switcher state (`activeSymbol`) — move the buttons into the new
  `sym-row` above the chart, restyled as plain underlined text tabs.

### 4.2 `Header.tsx`
- Full-width bar, navy glass, `border-b`. Logo mark (gradient rounded square "Z")
  + "Zenith" wordmark + mono sub-line.
- Status pills: `Streaming/Connecting/Offline` (from `connState`) and the active
  killzone — restyled as rounded pills with a pulsing dot.
- Keep the clock (PT), account label, sync badge, lockout badge + clear button.
- Keep all action buttons (Flatten/Test Trade/Restart in live mode, Analytics,
  Backtests, CSV, config gear) — restyle as quiet text buttons; danger actions
  stay red-tinted.

### 4.3 `MetricsGrid.tsx` + `MetricCard.tsx`
- Same 4 metrics (Buffer MLL, Buffer DLL, Daily P&L, Open) and the same
  tone logic (good/warn/bad/neutral).
- New flat card: tiny status dot + mono uppercase label, large light-weight
  value, mono sub-line. **Remove** the top accent stripe and any pulse/glow.
- Tone maps to the dot color and the value color only.

### 4.4 `BarChart.tsx`
- **Keep chart logic** (lightweight-charts v5 `addSeries(CandlestickSeries)`,
  forming-bar poll, markers, TF selector, countdown, setup-state poll). Only
  restyle:
  - chart `layout.background` → transparent (so the glass/gradient shows),
    `textColor` → `faint`, grid lines → `rgba(255,255,255,0.02)`,
    crosshair → blue, candles up=`good` down=`danger`.
  - Outer frame → glass panel; header bar → "MGC · Micro Gold · 1min" + live price.
- **Move the inline "Conditions" panel OUT of BarChart** into the Activity feed
  as a new **"Setup" tab** (see 4.5). The chart becomes full-width.
- **Remove the VP histogram overlay entirely** from the chart: delete the
  `vpCanvas` element, `drawHistogram`, `fetchAndDrawVp`, the
  `subscribeVisibleLogicalRangeChange(drawHistogram)` hook, the `syncId`/
  `vpRefetchId` intervals, the `onVpUpdate` callback, and the `currentVpProfile`
  state. The `/api/vp/profile` fetch and `VpProfile` import go away from this
  component. (Backend endpoint is left in place, just unused by the chart.)
- **Remove the VP filter row** (`VpCheckItem`) from the relocated conditions
  content as well.

### 4.5 Activity feed (replaces the 3 `FeedSection`s + `StrategyDebug`)
- New right-column panel with text tabs: **All · Signals · Fills · Recon · Setup**.
- **All** = merged, time-sorted stream of signals + fills + reconciles.
- **Signals / Fills / Recon** = filtered views of the same data
  (`signals`, `fills`, `reconciles` from `useStream`).
- **Setup** = the relocated Conditions checklist (killzone, sweep, displacement+FVG,
  HTF bias, cooldown — **no VP row**), fed by the existing `/api/setup_state` poll.
- Row design (calm): status dot + mono uppercase type + timestamp on row 1;
  primary text + mono sub-detail + right-aligned value/amount on row 2. No left
  bars, no bordered badges. Color only on the dot and the P&L amount.
- Reuse the mapping logic from `SignalRow`/`FillRow`/`ReconcileRow` for what text
  each item shows; restyle their markup to the new row. These three components can
  be folded into a single `ActivityRow` that switches on `item.kind`.

### 4.6 Delete / retire
- `StrategyDebug.tsx` — delete (user: "isn't doing anything").
- `MatrixRain.tsx` — delete (replaced by `ZenithBackground`).
- `GlowOverlay.tsx` — delete unless a piece is reused; the green glow states go away.
- `scanlines` / green `index.css` utilities — remove the matrix-specific bits;
  keep/replace scrollbar + slider styling in the new palette.

---

## 5. Files touched

**Edit:** `frontend/index.html` (fonts, title), `frontend/tailwind.config.js`
(palette + fonts), `frontend/src/index.css` (background, scrollbars, remove green
utils), `frontend/src/App.tsx`, `frontend/src/components/Header.tsx`,
`MetricsGrid.tsx`, `MetricCard.tsx`, `BarChart.tsx`.

**Add:** `frontend/src/components/ZenithBackground.tsx`,
`frontend/src/components/ActivityFeed.tsx` (+ `ActivityRow.tsx`),
`frontend/src/components/PageHead.tsx` (or inline in App).

**Delete:** `StrategyDebug.tsx`, `MatrixRain.tsx`, `GlowOverlay.tsx`,
`FeedSection.tsx` + `SignalRow/FillRow/ReconcileRow.tsx` (folded into ActivityRow).

**Light edit:** `useStream.ts` — drop the now-dead `onVpUpdate` from the
`ChartCallbacks` interface and its call site in the `bar` handler (VP overlay
removed). No other behavior change.

**Unchanged:** `useConfig.ts`, `useKillzone.ts`, `types.ts` (may add small types
for ActivityRow; keep `VpProfile` type even if unused, or remove if no other
consumer), `ConfigPanel.tsx`, `ForceSignalPanel.tsx`, `BacktestsPage.tsx`,
`Analytics.tsx`, all backend.

---

## 6. Open questions / assumptions

1. **Conditions placement** — assumed "Setup" tab in the Activity feed (keeps the
   chart clean, preserves observability). If you'd rather keep it beside the chart,
   that's a layout swap, not a data change.
2. **`/analytics` & `/backtests`** — left on their current styling this pass. They
   open in new tabs; reskinning them can be a follow-up.
3. **Product name** — "Zenith" in the header wordmark and `index.html` `<title>`.
   Not renaming the repo, Python package, or config files.
4. **VP** — removed from the dashboard entirely: both the chart histogram overlay
   and the conditions-checklist row. The `/api/vp/profile` backend endpoint stays
   (unused by the UI). `useStream`'s `onVpUpdate` chart callback becomes dead and
   is dropped from the `ChartCallbacks` interface + its call site in the `bar`
   handler.

---

## 7. Verification

- `cd frontend && npm run build` → no TS errors.
- `npm run dev` → dashboard at `:5173`, Zenith theme, no console errors.
- Confirm live: metrics update, chart draws + forming bar ticks, a signal/fill
  appears in the feed and as a chart marker, killzone pill reflects `useKillzone`,
  connection pill reflects `connState`, Flatten/Restart still work in live mode.
- `npm run build` so the bot serves the new bundle on `:5175` without a restart
  ([[feedback_frozen_bars_after_frontend_changes]]).
