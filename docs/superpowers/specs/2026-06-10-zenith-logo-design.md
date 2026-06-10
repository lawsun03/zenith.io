# Zenith Logo — Design

**Date:** 2026-06-10
**Status:** Approved

## Goal

Give the app a real logo mark on the header of every page, replacing the placeholder "Z" box and the plain `TOPSTEP-BOT` text labels.

## The mark

"Equity curve cresting" (option C, chosen from 4 mockups): a rising polyline in dim blue (`#6a85b0`), with the final rising leg and a dot at the peak in accent mint (`#6ee7b7`). Inline SVG, viewBox `0 0 28 28`:

- Base line: `3,23 8,16 12,18.5 18,7 24,12` — stroke `#6a85b0`, width 2.2, square caps/miter joins
- Accent leg: `12,18.5 18,7` — stroke `#6ee7b7`, width 2.2
- Peak dot: circle at `18,7`, r 2.6, fill `#6ee7b7`

## Components

1. **`frontend/src/components/Logo.tsx`** — `<Logo size={n} />` renders the SVG. Single source of truth.
2. **`Header.tsx`** — keep the 28px bordered box; replace the "Z" character with `<Logo size={18} />`.
3. **Nav bars on Analytics, Backtests, Todos, TradeAnalysis, TradeAnalysisDetail** — replace the `TOPSTEP-BOT` span with a `Link to="/"` containing `<Logo size={16} />` + `ZENITH` in the same mono letterspaced style each page already uses.
4. **Favicon** — same mark as `frontend/public/favicon.svg`, referenced from `index.html`.

## Out of scope

No backend changes, no config, no new dependencies, no raster assets.

## Verification

- `npm run build` clean (TS + Vite)
- Visually confirm the mark renders on all 5 routes (`/`, `/analytics`, `/backtests`, `/todos`, `/trade-analysis`) and the favicon in the tab
