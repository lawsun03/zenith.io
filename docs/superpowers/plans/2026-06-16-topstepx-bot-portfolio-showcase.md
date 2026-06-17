# TopstepX Bot Portfolio Showcase Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Showcase the "Zenith" TopstepX trading bot as a polished case study in the `lawrence-portfolio` site, and sanitize the bot repo (`github.com/lawsun03/zenith.io`) so it can be made public.

**Architecture:** Portfolio `ProjectsApp` becomes a list→detail view (Option B): a project list driven by a data array, with the bot as the first full entry that opens a rich `BotCaseStudy` detail view. Screenshots are redacted of real money before use. Repo cleanup removes a committed `node_modules`, declutters the root, fixes the README, then visibility is flipped to public as the final step.

**Tech Stack:** React 19 + Vite (portfolio, inline styles + CSS custom properties, no Tailwind, no router lib); Python + Pillow (screenshot redaction); git (sanitization).

**Spec:** `docs/superpowers/specs/2026-06-16-topstepx-bot-portfolio-showcase-design.md`

**Path conventions:** `PORTFOLIO = C:\Users\Lawrence\Documents\lawrence-portfolio`, `BOT = C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot`.

---

## File Structure

**Portfolio (created/modified):**
- Create: `PORTFOLIO/src/data/projects.js` — project list data (one entry now, extensible).
- Create: `PORTFOLIO/src/components/apps/projects/BotCaseStudy.jsx` — rich detail view + local helpers (`Section`, `StatCard`, `Tag`).
- Modify: `PORTFOLIO/src/components/apps/ProjectsApp.jsx` — rewrite as list→detail router.
- Modify: `PORTFOLIO/src/hooks/useWindowManager.js:14` — enlarge the Projects window.
- Create: `PORTFOLIO/public/images/projects/*.png` — redacted screenshots (output of Task 6).

**Bot repo (modified, cleanup only — NO trading logic touched):**
- Create: `BOT/scripts/_redact_screenshots.py` — one-off Pillow redaction script (scratch, gitignored).
- Modify: `BOT/.gitignore` — add `frontend/node_modules/`.
- Modify: `BOT/README.md` — fix stale test count, add summary + screenshot.
- Move: root debug PNGs + `scripts/_*.py` into `BOT/docs/assets/` or gitignore.

---

## Notes for the executor

- The portfolio has **no test framework**. "Verify" for portfolio tasks means: `npm run build` exits 0 AND the dev server renders the feature with no console errors. This is the intended verification for this stack — do not add a test runner.
- The portfolio uses **inline styles + CSS variables** (see `src/index.css`: `--orange #ff6b35`, `--purple #b44aff`, `--green #22c55e`, `--gradient`, `--surface`, `--border`, `--text-secondary`, `--muted`, `--font-display` Syne, `--font-mono` Azeret Mono). Match this — no Tailwind, no styled-components.
- **No fabricated stats.** Task 1 includes a verification step; only verified numbers ship.

---

## Phase 1 — Portfolio showcase

### Task 1: Verify the stats, then create the project data module

**Files:**
- Create: `PORTFOLIO/src/data/projects.js`

- [ ] **Step 1: Verify the headline numbers against real sources**

Run (in `BOT`):
```bash
find tests -name 'test_*.py' | xargs grep -hcE '^\s*(async )?def test_' | awk '{s+=$1} END {print s" tests"}'
ls research/ | head        # confirm research-loop output dirs exist (equity_*, etc.)
git log --oneline | wc -l  # commit count, optional "X commits" stat
```
Expected: ~`819 tests`. Record the actual number. If it differs, use the actual value in Step 2. The PF / pass-rate figures below come from the project MEMORY (MNQ 5-min walk-forward PF ~1.15 / +$13.1k; CPI news-straddle PF ~6 on NQ; monthly-combine pass rate ~34%). If a `research/` output contradicts a figure, use the file's value or drop the stat. Do not invent numbers.

- [ ] **Step 2: Write the data module**

```javascript
// PORTFOLIO/src/data/projects.js
// One full entry now; the array shape is ready for future projects.
import BotCaseStudy from '../components/apps/projects/BotCaseStudy'

export const projects = [
  {
    id: 'zenith',
    title: 'Zenith',
    subtitle: 'Autonomous Futures Trading Bot',
    tagline: 'A local-first algo-trading system for the Topstep Combine — with an autonomous research loop that backtests and kills strategy ideas on its own.',
    accent: '#ff6b35',
    year: '2026',
    tags: ['Python', 'FastAPI', 'asyncio', 'React', 'TypeScript', 'SSE', 'project-x-py'],
    repo: 'https://github.com/lawsun03/zenith.io',
    Detail: BotCaseStudy,
  },
]
```

- [ ] **Step 3: Commit**

```bash
cd /c/Users/Lawrence/Documents/lawrence-portfolio
git add src/data/projects.js
git commit -m "feat(projects): add project data module with Zenith entry"
```

---

### Task 2: Build the BotCaseStudy detail component

**Files:**
- Create: `PORTFOLIO/src/components/apps/projects/BotCaseStudy.jsx`

- [ ] **Step 1: Write the component**

Full file (all 8 spec sections; DRY via local helpers). Screenshot `<img>` tags point at files produced in Task 6 — they will 404 until then; that is expected and fixed in Task 7.

```jsx
// PORTFOLIO/src/components/apps/projects/BotCaseStudy.jsx
import React from 'react'

const ORANGE = '#ff6b35', PURPLE = '#b44aff', GREEN = '#22c55e'

const Section = ({ icon, title, color = ORANGE, children }) => (
  <section style={{ marginBottom: 26 }}>
    <h4 style={{
      fontFamily: 'var(--font-mono)', fontSize: '0.6rem', color,
      letterSpacing: '0.12em', textTransform: 'uppercase', marginBottom: 10,
    }}>{icon} {title}</h4>
    {children}
  </section>
)

const Tag = ({ children, color = ORANGE }) => (
  <span style={{
    fontFamily: 'var(--font-mono)', fontSize: '0.54rem', padding: '4px 9px',
    background: `${color}10`, border: `1px solid ${color}25`, borderRadius: 5,
    color, display: 'inline-block', lineHeight: 1,
  }}>{children}</span>
)

const StatCard = ({ value, label, color = ORANGE }) => (
  <div style={{
    padding: '14px 12px', background: 'rgba(255,255,255,0.03)',
    border: '1px solid var(--border)', borderRadius: 8, textAlign: 'center',
  }}>
    <div style={{ fontFamily: 'var(--font-display)', fontSize: '1.35rem', fontWeight: 800, color }}>{value}</div>
    <div style={{ fontSize: '0.55rem', color: 'var(--muted)', marginTop: 3 }}>{label}</div>
  </div>
)

const P = ({ children }) => (
  <p style={{ fontSize: '0.74rem', color: 'var(--text-secondary)', lineHeight: 1.8, marginBottom: 10 }}>{children}</p>
)

const Shot = ({ src, caption }) => (
  <figure style={{ margin: 0 }}>
    <img src={src} alt={caption} loading="lazy" style={{
      width: '100%', borderRadius: 8, border: '1px solid var(--border)', display: 'block',
    }} />
    <figcaption style={{ fontSize: '0.55rem', color: 'var(--muted)', marginTop: 5, fontFamily: 'var(--font-mono)' }}>{caption}</figcaption>
  </figure>
)

// Verify these against Task 1 before shipping. All are BACKTEST/engineering metrics — no live $.
const STATS = [
  { value: '819', label: 'automated tests', color: GREEN },
  { value: '5 yr', label: 'clean tick data (Databento)', color: ORANGE },
  { value: 'PF 1.15', label: 'MNQ 5m walk-forward', color: ORANGE },
  { value: '~34%', label: 'monthly-combine pass rate (sim)', color: PURPLE },
]

const STRATEGIES = [
  { name: 'Liquidity Sweep + Displacement', color: ORANGE, desc: 'Tracks swing structure, detects stop-runs past old highs/lows, then enters on the displacement/iFVG reversal leg.' },
  { name: 'Opening-Range Breakout (ORB)', color: PURPLE, desc: 'Shipped MNQ strategy: 15-minute opening range, breakout entry, 2.5R target.' },
  { name: 'CPI News Straddle', color: GREEN, desc: 'Confirmed edge: pre-news range straddle with OCO bracket around CPI releases. Backtested PF ~6 on NQ.' },
]

export default function BotCaseStudy() {
  return (
    <div style={{ padding: 28, fontFamily: 'var(--font-sans)' }}>
      {/* 1. Hero */}
      <h2 style={{ fontFamily: 'var(--font-display)', fontSize: '1.6rem', fontWeight: 800, letterSpacing: '-0.02em', marginBottom: 4 }}>
        <span style={{ background: 'var(--gradient)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>Zenith</span>
        <span style={{ color: 'var(--muted)', fontSize: '0.9rem', fontWeight: 500 }}>  ·  Autonomous Futures Trading Bot</span>
      </h2>
      <p style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.7, marginBottom: 12, maxWidth: 620 }}>
        A local-first algorithmic trading system for the Topstep $50K Combine, plus an autonomous
        research agent that backtests new strategy ideas over a weekend and kills the losers on its own.
      </p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5, marginBottom: 24 }}>
        {['Python', 'FastAPI', 'asyncio', 'React', 'TypeScript', 'SSE', 'project-x-py'].map(t => <Tag key={t}>{t}</Tag>)}
      </div>

      {/* 2. The problem */}
      <Section icon="◆" title="The Problem">
        <P>Topstep funds traders who pass a "Combine" with strict, automatically-enforced rules: a trailing
        max-loss limit that ratchets up with your equity, a hard daily loss limit, and a ban on VPS/cloud
        execution — the bot must run on the same machine you trade from. The challenge is an always-on
        system that respects every rule deterministically, recovers from disconnects, and never silently
        drifts from the broker's true state.</P>
      </Section>

      {/* 3. Architecture */}
      <Section icon="▲" title="Architecture" color={PURPLE}>
        <P>Each closed price bar flows through one deterministic pipeline:</P>
        <div style={{
          fontFamily: 'var(--font-mono)', fontSize: '0.6rem', color: 'var(--text-secondary)',
          background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8,
          padding: '12px 14px', lineHeight: 1.9, marginBottom: 10,
        }}>
          bar → strategy (signal) → pre-trade risk gate → broker<br />
          fill event → bracket-after-fill (stop + target placed on confirmed fill)<br />
          reconciler → periodic broker-vs-internal-state diff → lockout on mismatch
        </div>
        <P>A FastAPI backend streams live state to a React dashboard over Server-Sent Events. Orders use a
        "bracket-after-fill" pattern: the protective stop and target are placed only once the entry fill is
        confirmed, avoiding a SDK convenience method whose hardcoded 60-second wait breaks live trading.</P>
      </Section>

      {/* 4. Strategies */}
      <Section icon="✦" title="Strategies">
        <div style={{ display: 'grid', gap: 8 }}>
          {STRATEGIES.map(s => (
            <div key={s.name} style={{ padding: '12px 14px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)', borderRadius: 8 }}>
              <div style={{ fontSize: '0.7rem', fontWeight: 700, color: s.color, marginBottom: 3 }}>{s.name}</div>
              <div style={{ fontSize: '0.66rem', color: 'var(--text-secondary)', lineHeight: 1.6 }}>{s.desc}</div>
            </div>
          ))}
        </div>
      </Section>

      {/* 5. Research loop (priority) */}
      <Section icon="⟳" title="The Research Loop" color={GREEN}>
        <P>The most interesting part isn't any single strategy — it's the system that finds them. An
        autonomous agent runs <strong style={{ color: 'var(--text)' }}>serial headless backtests</strong> against
        five years of clean tick data, pulling ideas from a file-based "blackboard" queue, scoring each with
        walk-forward analysis, and writing findings back. Dozens of strategy ideas were tested this way —
        and <strong style={{ color: 'var(--text)' }}>most were rejected</strong>. The value is in the
        discipline: VWAP mean-reversion, chop breakouts, and fib filters were all killed by out-of-sample
        validation before they could ever risk real money. Only confirmed edges ship.</P>
      </Section>

      {/* 6. Results / data (priority) */}
      <Section icon="∎" title="Results — Backtested" color={ORANGE}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8, marginBottom: 8 }}>
          {STATS.map(s => <StatCard key={s.label} {...s} />)}
        </div>
        <p style={{ fontSize: '0.55rem', color: 'var(--muted)', fontFamily: 'var(--font-mono)' }}>
          All figures are backtest / walk-forward results on out-of-sample data. PF = profit factor.
        </p>
      </Section>

      {/* 7. Screenshots */}
      <Section icon="❏" title="Screenshots">
        <div style={{ display: 'grid', gap: 14 }}>
          <Shot src="/images/projects/dashboard.png" caption="Live dashboard — real-time SSE stream, chart, metrics, activity feed" />
          <Shot src="/images/projects/backtest.png" caption="Backtest engine — parameter sweeps and walk-forward runs" />
          <Shot src="/images/projects/config.png" caption="Config panel — hot-applied strategy parameters, no restart" />
        </div>
      </Section>

      {/* 8. Links */}
      <Section icon="↗" title="Source" color={PURPLE}>
        <a href="https://github.com/lawsun03/zenith.io" target="_blank" rel="noreferrer" style={{
          fontFamily: 'var(--font-mono)', fontSize: '0.66rem', color: ORANGE, textDecoration: 'none',
          border: `1px solid ${ORANGE}40`, borderRadius: 6, padding: '7px 14px', display: 'inline-block',
        }}>github.com/lawsun03/zenith.io →</a>
      </Section>
    </div>
  )
}
```

- [ ] **Step 2: Verify it compiles**

Run:
```bash
cd /c/Users/Lawrence/Documents/lawrence-portfolio && npm run build
```
Expected: build exits 0 (the component imports cleanly even though images 404 at runtime).

- [ ] **Step 3: Commit**

```bash
git add src/components/apps/projects/BotCaseStudy.jsx
git commit -m "feat(projects): add Zenith bot case-study detail view"
```

---

### Task 3: Rewrite ProjectsApp as a list→detail router

**Files:**
- Modify: `PORTFOLIO/src/components/apps/ProjectsApp.jsx` (full replace)

- [ ] **Step 1: Replace the file contents**

```jsx
// PORTFOLIO/src/components/apps/ProjectsApp.jsx
import React, { useState } from 'react'
import { projects } from '../../data/projects'

function ProjectCard({ project, onOpen }) {
  return (
    <button onClick={onOpen} style={{
      textAlign: 'left', cursor: 'pointer', width: '100%',
      padding: 16, background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border)',
      borderRadius: 10, transition: 'all 0.25s', color: 'inherit', font: 'inherit',
    }}
      onMouseEnter={e => { e.currentTarget.style.borderColor = project.accent; e.currentTarget.style.transform = 'translateY(-2px)' }}
      onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.transform = 'none' }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <h4 style={{ fontFamily: 'var(--font-display)', fontSize: '1rem', fontWeight: 700, color: '#fff' }}>
          {project.title} <span style={{ color: 'var(--muted)', fontWeight: 500, fontSize: '0.72rem' }}>· {project.subtitle}</span>
        </h4>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.55rem', color: 'var(--muted)' }}>{project.year}</span>
      </div>
      <p style={{ fontSize: '0.68rem', color: 'var(--text-secondary)', lineHeight: 1.6, marginBottom: 10 }}>{project.tagline}</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
        {project.tags.slice(0, 6).map(t => (
          <span key={t} style={{ fontFamily: 'var(--font-mono)', fontSize: '0.52rem', padding: '3px 8px', background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--muted)' }}>{t}</span>
        ))}
      </div>
    </button>
  )
}

export default function ProjectsApp() {
  const [openId, setOpenId] = useState(null)
  const active = projects.find(p => p.id === openId)

  if (active) {
    const Detail = active.Detail
    return (
      <div style={{ height: '100%' }}>
        <button onClick={() => setOpenId(null)} style={{
          margin: '14px 0 0 18px', cursor: 'pointer', background: 'transparent',
          border: '1px solid var(--border)', borderRadius: 6, color: 'var(--text-secondary)',
          fontFamily: 'var(--font-mono)', fontSize: '0.6rem', padding: '5px 11px',
        }}>← Projects</button>
        <Detail />
      </div>
    )
  }

  return (
    <div style={{ padding: 24, fontFamily: 'var(--font-sans)' }}>
      <h3 style={{ fontFamily: 'var(--font-display)', fontSize: '1.1rem', fontWeight: 700, marginBottom: 4 }}>Projects</h3>
      <p style={{ fontSize: '0.68rem', color: 'var(--muted)', marginBottom: 20 }}>What I've been building.</p>
      <div style={{ display: 'grid', gap: 12 }}>
        {projects.map(p => <ProjectCard key={p.id} project={p} onOpen={() => setOpenId(p.id)} />)}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Verify build**

Run: `cd /c/Users/Lawrence/Documents/lawrence-portfolio && npm run build`
Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add src/components/apps/ProjectsApp.jsx
git commit -m "feat(projects): list -> detail router replacing placeholder"
```

---

### Task 4: Enlarge the Projects window

**Files:**
- Modify: `PORTFOLIO/src/hooks/useWindowManager.js:14`

- [ ] **Step 1: Edit the projects window dimensions**

Find line 14 (the `projects:` entry) and change `w: 520, h: 440` to `w: 860, h: 620`:

```javascript
    projects: { id: 'projects', title: 'Projects', icon: '📁', open: false, minimized: false, maximized: false, focused: false, x: 120, y: 45, w: 860, h: 620, zIndex: 1 },
```

- [ ] **Step 2: Verify build**

Run: `cd /c/Users/Lawrence/Documents/lawrence-portfolio && npm run build`
Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add src/hooks/useWindowManager.js
git commit -m "feat(projects): widen Projects window for case-study content"
```

---

### Task 5: Visual verification in the dev server

- [ ] **Step 1: Run the dev server and open Projects**

Run: `cd /c/Users/Lawrence/Documents/lawrence-portfolio && npm run dev`
Open the printed localhost URL, click the Projects dock icon. Confirm: project list shows the Zenith card; clicking it opens the case study; the `← Projects` button returns to the list; scrolling works; no console errors. Screenshots will show as broken images until Task 7 — that is expected here.

- [ ] **Step 2: Note any layout issues** for fixing before Phase 2 finalize. No commit (verification only).

---

## Phase 2 — Screenshots (redacted)

### Task 6: Redact real money from screenshots

**Files:**
- Create: `BOT/scripts/_redact_screenshots.py` (scratch, gitignored)
- Create: `PORTFOLIO/public/images/projects/dashboard.png`, `backtest.png`, `config.png`

- [ ] **Step 1: Confirm Pillow is available**

Run (in `BOT`): `.venv\Scripts\python.exe -c "import PIL; print(PIL.__version__)"`
If it errors: `.venv\Scripts\python.exe -m pip install pillow`

- [ ] **Step 2: Write the redaction script**

The dashboard screenshot (`dashboard-before-config.png`, 1920×~900) exposes balance, equity, daily P&L, best-day, and the account ID. Black-box those regions. Coordinates below are starting estimates for that image — Step 3 verifies and adjusts them.

```python
# BOT/scripts/_redact_screenshots.py  (one-off; not committed)
from PIL import Image, ImageDraw
from pathlib import Path

BOT = Path(__file__).resolve().parents[1]
OUT = Path(r"C:\Users\Lawrence\Documents\lawrence-portfolio\public\images\projects")
OUT.mkdir(parents=True, exist_ok=True)

def redact(src, dst, boxes):
    img = Image.open(BOT / src).convert("RGB")
    d = ImageDraw.Draw(img)
    for (x0, y0, x1, y1) in boxes:
        d.rectangle([x0, y0, x1, y1], fill=(10, 12, 20))
    img.save(OUT / dst)
    print(f"wrote {dst}  ({img.size[0]}x{img.size[1]})")

# Account ID (top-left under "Zenith"), and the metrics strip
# (BALANCE / MLL / CUSHION / TODAY / BEST DAY) + Equity/Realized in the
# DAILY P&L and OPEN cards. Adjust in Step 3.
redact("dashboard-before-config.png", "dashboard.png", [
    (150, 70, 320, 92),      # account id row
    (1060, 60, 1330, 100),   # BALANCE/MLL/CUSHION/TODAY/BEST DAY values
    (1040, 330, 1230, 372),  # realized equity line in DAILY P&L card
    (1040, 330, 1230, 372),  # OPEN card equity/HW (overlaps; refine in step 3)
])
redact("backtests-page.png", "backtest.png", [])   # no live $; copy as-is
redact("config-panel-open.png", "config.png", [])  # no live $; copy as-is
```

- [ ] **Step 3: Run it and visually confirm redaction**

Run: `.venv\Scripts\python.exe scripts\_redact_screenshots.py`
Open `PORTFOLIO/public/images/projects/dashboard.png` and confirm **every** real dollar figure and the account ID are covered. If any value is still visible, adjust the box coordinates and re-run. Do not proceed until clean. (Use the `screenshot`/`observe-ui` skills or just open the PNG.)

- [ ] **Step 4: Present the redacted images to Lawrence for sign-off**

Show the three output PNGs. Wait for confirmation that no sensitive data is visible before committing.

- [ ] **Step 5: Commit the images (portfolio repo only)**

```bash
cd /c/Users/Lawrence/Documents/lawrence-portfolio
git add public/images/projects/
git commit -m "feat(projects): add redacted Zenith screenshots"
```
Do NOT commit `_redact_screenshots.py` to the bot repo — it is covered by the `scripts/_*.py` ignore added in Task 9.

---

### Task 7: Confirm the gallery renders

- [ ] **Step 1: Rebuild and view**

Run: `cd /c/Users/Lawrence/Documents/lawrence-portfolio && npm run build && npm run dev`
Open Projects → Zenith → scroll to Screenshots. Confirm all three images load (no broken-image icons) and are readable. The `<img src>` paths in `BotCaseStudy.jsx` (`/images/projects/dashboard.png` etc.) already match Task 6 outputs — no code change expected. If a path mismatches, fix it in `BotCaseStudy.jsx` and rebuild.

- [ ] **Step 2: Commit (only if BotCaseStudy was edited)**

```bash
git add src/components/apps/projects/BotCaseStudy.jsx
git commit -m "fix(projects): align screenshot paths"
```

---

## Phase 3 — Bot repo sanitization (NO trading logic touched)

### Task 8: Untrack the committed node_modules

**Files:**
- Modify: `BOT/.gitignore`

- [ ] **Step 1: Confirm it is tracked**

Run (in `BOT`): `git ls-files | grep -c 'frontend/node_modules/'`
Expected: a large number (~3723).

- [ ] **Step 2: Add to .gitignore if absent**

Check: `grep -n 'node_modules' .gitignore`
If `frontend/node_modules` is not covered, append a line `frontend/node_modules/` to `BOT/.gitignore` (use the Edit/Write tool — NOT PowerShell Set-Content, per the repo's Unicode trap note).

- [ ] **Step 3: Remove from the index (keeps files on disk)**

```bash
git rm -r --cached frontend/node_modules >/dev/null
git ls-files | grep -c 'frontend/node_modules/'   # expect 0
```

- [ ] **Step 4: Commit**

```bash
git add .gitignore
git commit -m "chore: stop tracking frontend/node_modules"
```

---

### Task 9: Declutter the repo root

**Files:**
- Move: root debug PNGs and `scripts/_*.py` out of the tracked tree.

- [ ] **Step 1: Add ignore rules for scratch artifacts**

Append to `BOT/.gitignore` (via Edit/Write tool):
```
# scratch / debug artifacts
/*.png
.playwright-mcp/
scripts/_*.py
research/STOP
```

- [ ] **Step 2: Untrack any of these that are currently tracked**

```bash
git ls-files | grep -E '^[^/]+\.png$|^scripts/_.*\.py$' | xargs -r git rm --cached
```
(If the command prints nothing, none were tracked — fine; the ignore rules still keep them out going forward.)

- [ ] **Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: ignore root debug screenshots and scratch scripts"
```

---

### Task 10: Polish the README

**Files:**
- Modify: `BOT/README.md`

- [ ] **Step 1: Fix the stale test count**

In `BOT/README.md`, change the line `All 65 tests should pass.` to the verified number from Task 1:
```
All 819 tests should pass.
```

- [ ] **Step 2: Add a project summary + screenshot near the top**

Insert immediately after the `# topstep-bot` heading (use the Edit tool):
```markdown
> **Zenith** — a local-first algorithmic trading bot for the Topstep $50K Combine,
> built in Python (FastAPI/asyncio) with a React + TypeScript live dashboard. Includes
> an autonomous research loop that backtests strategy ideas against 5 years of tick
> data and rejects the losers via walk-forward validation. 819 automated tests.

![Live dashboard](docs/assets/dashboard.png)
```

- [ ] **Step 3: Place the screenshot the README references**

```bash
mkdir -p docs/assets
cp "C:/Users/Lawrence/Documents/lawrence-portfolio/public/images/projects/dashboard.png" docs/assets/dashboard.png
```
(Use the redacted image so the README also has no live $.)

- [ ] **Step 4: Verify the README renders**

Run: `git add README.md docs/assets/dashboard.png && git status`
Confirm `docs/assets/dashboard.png` is staged (it must NOT be caught by the `/*.png` ignore — it is in `docs/`, so it is fine). Eyeball the README markdown for the image link path.

- [ ] **Step 5: Commit**

```bash
git commit -m "docs: recruiter-facing README summary, screenshot, fix test count"
```

---

### Task 11: Final secrets re-scan

- [ ] **Step 1: Scan tracked files for real secret values**

Run (in `BOT`):
```bash
git grep -nIE 'sk-ant-[A-Za-z0-9]{20,}|PROJECT_X_API_KEY\s*=\s*[A-Za-z0-9]{16,}|[A-Za-z0-9_-]{40,}' -- . ':(exclude)*.csv' ':(exclude)package-lock.json' ':(exclude)*.lock' | grep -vE 'example|\.\.\.|placeholder|your_|integrity'
```
Expected: **no output**. Any hit is a real secret — stop and remove it before continuing. (Prior scan on 2026-06-16 was clean: only env-var names and `sk-ant-api03-...` placeholders.)

- [ ] **Step 2: Push the cleaned history**

```bash
git push origin master
```
Expected: push succeeds.

---

### Task 12: Make the repo public (final step)

- [ ] **Step 1: Flip visibility**

`gh` CLI is not installed locally. Lawrence does this manually: GitHub → `lawsun03/zenith.io` → Settings → General → Danger Zone → Change visibility → Public. (Or, if `gh` gets installed: `gh repo edit lawsun03/zenith.io --visibility public`.)

- [ ] **Step 2: Final verification checklist**

- Visit `github.com/lawsun03/zenith.io` logged-out: repo loads, root is clean (no `node_modules`, no loose PNGs), README shows the summary + dashboard image.
- Portfolio: Projects → Zenith → all sections render, screenshots load, GitHub link opens the now-public repo, `npm run build` is clean.
- No real account numbers visible anywhere.

---

## Self-Review (completed at write time)

- **Spec coverage:** Part A → Tasks 1–5, 7. Part B (sanitization) → Tasks 8–12. Part C (screenshots) → Task 6. Stats-verification requirement → Task 1 Step 1. "Make public last" → Task 12 (after 8–11). All spec sections mapped.
- **Placeholder scan:** No TBD/TODO. Redaction box coordinates are explicit estimates with a mandatory verify-and-adjust step (Task 6 Step 3) — not a placeholder. Stats carry a verify step rather than being assumed.
- **Type/name consistency:** `projects.js` exports `projects` with `Detail` (capitalized for JSX use); `ProjectsApp` reads `active.Detail`. `BotCaseStudy` is the default export imported by `projects.js`. Image paths `/images/projects/{dashboard,backtest,config}.png` match Task 6 outputs. Consistent throughout.
