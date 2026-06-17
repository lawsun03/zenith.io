# TopstepX Bot — Portfolio Showcase + Repo Sanitization

**Date:** 2026-06-16
**Status:** Approved design, ready for implementation plan
**Author:** Lawrence Sun (with Claude)

## Goal

Present the TopstepX algo-trading bot ("Zenith") as a polished, recruiter-facing
project in the `lawrence-portfolio` site, and sanitize the bot repo so it can be
linked publicly. The headline narrative is **engineering rigor** — especially the
autonomous research loop — not live trading P&L.

Two repos are involved:

- **Portfolio** (`C:\Users\Lawrence\Documents\lawrence-portfolio`) — the main
  creative deliverable. React + Vite "LawrenceOS" desktop-metaphor site.
- **Bot** (`C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot`) — repo
  `github.com/lawsun03/zenith.io`. Cleanup/sanitization only; **no trading logic,
  config, or live behavior is touched.**

## Non-goals

- No changes to the bot's strategy, broker, risk, or execution code.
- No new state-management libs, routers, or dependencies in either repo.
- No live account numbers, P&L, equity, or account IDs displayed anywhere.
- No fabricated statistics — every number shown traces to a real file or run.

## Framing decision (confirmed)

"Research & rigor, no live $." Lead with backtest stats and the research-loop
story. Redact real money from all screenshots.

## Part A — Portfolio showcase (Option B: list + detail)

The portfolio uses inline styles with CSS custom properties (no Tailwind).
Design tokens (from `src/index.css`): `--orange #ff6b35`, `--purple #b44aff`,
`--green #22c55e`, `--gradient` (orange→purple), `--surface`, `--border`,
`--text-secondary`, `--muted`; fonts `--font-display` (Syne), `--font-mono`
(Azeret Mono), `--font-sans` (system). All new UI matches this exactly, following
the conventions in `AboutApp.jsx` (mono uppercase section headers, `Tag` chips,
hover-lift cards).

### Components / files

1. **`src/data/projects.js`** (new) — array of project objects. One full entry
   now (the bot); shape is future-proof for more entries:
   ```
   { id, title, tagline, accent, tags: [], detail: <component or data> }
   ```
2. **`src/components/apps/ProjectsApp.jsx`** (rewrite) — thin list+detail router.
   Renders project cards from `projects.js`; clicking a card sets local
   `useState` to show the detail view; a back arrow returns to the list. No
   router library. Replaces the current "Coming Soon" placeholder.
3. **`src/components/apps/projects/BotCaseStudy.jsx`** (new) — the rich,
   scrollable detail view for the bot. Self-contained, inline-styled, local
   `Tag` and `StatCard` helpers.

### Case-study sections (in `BotCaseStudy.jsx`)

1. **Hero** — "Zenith — Autonomous Futures Trading Bot" + tagline + tag chips
   (Python, FastAPI, asyncio, React, TypeScript, SSE, project-x-py).
2. **The problem** — Topstep Combine constraints: trailing max-loss limit, daily
   loss limit, no-VPS rule (must run locally). Why this is a real engineering
   problem, not a toy.
3. **Architecture** — styled blocks (not an image): `bar → strategy → pretrade
   risk gate → broker`, the bracket-after-fill order pattern, the SSE live
   dashboard, the reconciler (broker-vs-internal-state diff).
4. **Strategies** — short cards: liquidity sweep + displacement/iFVG, ORB
   (opening-range breakout), and the confirmed **CPI news-straddle** edge.
5. **Research loop** (priority) — autonomous weekend agent running *serial
   headless backtests* against 5 years of clean Databento data, a file
   "blackboard" queuing dozens of strategy experiments (the "B-items"), each
   validated by walk-forward analysis, with **most ideas killed**. Framed as
   disciplined research: the value is in what got rejected.
6. **Results / data** (priority) — `StatCard` grid + one small inline SVG viz
   (bar or equity sparkline). See "Stats to verify" below.
7. **Screenshots** — gallery of redacted PNGs (see Part C).
8. **Tech stack + links** — GitHub link to `github.com/lawsun03/zenith.io`.

### Stats to feature — MUST be verified before writing

Do not hardcode any number until confirmed against a real source during
implementation:

| Stat | Candidate value | Source to verify against |
|------|-----------------|--------------------------|
| Test suite size | 819 test functions | `grep` of `tests/` (verified 2026-06-16) |
| MNQ 5-min walk-forward | PF ~1.15, ~+$13.1k | backtest output CSV / MEMORY |
| News-straddle edge | PF ~6 (NQ) | straddle backtest output |
| Monthly-combine pass rate | ~34% (10/29 months) | combine-sim output |
| Strategies tested vs shipped | count of B-items vs live | `research/` dir + MEMORY |

If a number can't be traced to a file, it is omitted or labeled clearly. The
test-count line is verified (819); the README's "65" is stale and gets fixed in
Part B.

## Part B — Bot repo sanitization

Secrets hygiene is already good (verified 2026-06-16): `.env` is untracked and
gitignored, `.env.example` exists. Remaining work:

1. **Re-confirm no secrets committed** — scan `git ls-files` and `git log` for
   keys/tokens before any push. Fail loud if anything is found.
2. **README polish** — fix the stale "All 65 tests should pass" line (now 819),
   add a one-paragraph project summary + one embedded screenshot, make it
   recruiter-readable. Keep existing setup instructions.
3. **Untrack `frontend/node_modules`** — 3,723 files are committed (verified
   2026-06-16). `git rm -r --cached frontend/node_modules`, add it to
   `.gitignore`, commit. Must happen before the repo goes public.
4. **Clean the repo root** — the loose debug PNGs (`account-switcher-*.png`,
   `dashboard-before-config.png`, `backtests-page.png`, `config-panel-open.png`,
   `dark-brutalist-preview.png`, `design-options.png`) and scratch scripts
   (`scripts/_*.py`) clutter the tree. Move screenshots to a `docs/` or `assets/`
   folder (or gitignore), so a visitor sees a clean root. Read/cleanup only.
5. **Repo visibility — make public (confirmed).** `github.com/lawsun03/zenith.io`
   is made public as the **final** step, after items 1–4. `gh` CLI is not
   installed locally (verified 2026-06-16), so this is a manual step by Lawrence
   (GitHub web UI → Settings → Change visibility) or via `gh` if installed.
   Secrets scan of tracked files came back clean (only env-var *names* and
   placeholders, no real values).

## Part C — Screenshots (redacted)

1. Source from existing PNGs in the bot repo root. Strongest picks: live
   dashboard, backtest engine page, config panel.
2. **Redact real money** with a Python/Pillow pass: blur or black-box the
   balance, equity, daily-P&L, best-day, and account-ID regions before use.
3. Save redacted copies to `lawrence-portfolio/public/images/projects/`.
4. Show Lawrence the redacted versions before they're finalized.

## Success criteria

- Portfolio `npm run build` is clean; Projects window shows list → bot detail →
  back, with no console errors.
- Every displayed stat traces to a real file or run (no fabrication).
- No real account numbers visible in any screenshot.
- Bot repo: no secrets, clean root, README renders well and reflects 819 tests.

## Confirmed decisions

- GitHub link: `github.com/lawsun03/zenith.io`.
- Repo will be made **public** (final step, post-cleanup).

## Open items to confirm during implementation

- Exact verified stats for the results grid (see Part A table).
- Final screenshot selection after redaction review.
