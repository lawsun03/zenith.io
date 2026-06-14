# Research Loop Protocol — read this FIRST, every session

You are one session of an autonomous weekend research loop on Lawrence's
TopstepX trading bot. Your job: ONE backlog item per session, executed with the
discipline below, then exit. State lives in files; the next session continues.

## Hard guardrails (violating any of these is a failed session)

1. **NEVER edit `bot_config.json` or `.env`. NEVER enable anything on the live
   bot.** All strategy work lands as default-off switches/configs + a written
   recommendation. Lawrence decides deployments on Monday.
2. **Full test suite green before every commit**: `& .venv\Scripts\python.exe
   -m pytest tests -q` must pass (564+ tests, 0 failures as of 2026-06-12).
   New behavior needs a defining-behavior test (house rule).
3. **Databento budget:** `research/databento_ledger.txt` holds the running
   total; HARD CAP $20.00. Before ANY fetch: run with `--estimate-only`, add
   estimate to the ledger total, abort if it would exceed the cap. Append every
   actual spend as a line `YYYY-MM-DD $X.XX <symbol> <range>`. TopstepX
   fetches (`scripts/fetch_bars.py`) are free — prefer them for recent data.
4. **Frozen holdout = calendar year 2022.** Exploratory work may NOT evaluate
   on 2022. A finished candidate (one you'd recommend) gets exactly ONE
   confirmatory 2022 run, recorded in the journal. 2021 H2 / 2023 / 2024 /
   2025-26 are open. (2024 = original train year; 2025-26 = original test.)
5. **Stop rules:** a variant losing on BOTH its objective metric and PF vs its
   baseline is rejected — no parameter rescue, no tuning sweeps. Fixed,
   pre-declared defaults only. Both periods (2024 + 2025-26) always reported
   for Combine-objective work; the full 5y file for funded-objective work.
6. **Git:** commit per completed item, house message style, end with
   `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`. Never amend,
   never force-push, never skip hooks. Current branch only.
7. **PowerShell trap:** never regex-edit repo files via `-replace`/Set-Content
   (mangles Unicode). Use the Edit/Write tools or a python script. No double
   quotes inside PowerShell here-string commit messages.

## Bot-keeper duty (run at session start, before anything else)

Check `Invoke-RestMethod http://127.0.0.1:5175/api/status`. If unreachable:
run the run-bot skill's documented kill/restart sequence (kill dev.ps1 parents
then app.main pythons by PID — NEVER kill by port; then
`Start-Process powershell -ArgumentList @("-NoExit","-Command",'.\dev.ps1 -Live
2>&1 | Tee-Object -Append -FilePath ".\logs\$(Get-Date -Format
''yyyy-MM-dd'').log"')`), verify port 5175, and post to Discord. If reachable:
note one health line in the journal entry. Market hours: closed Fri 13:00 PT →
Sun 15:00 PT; stale bars during closure are normal.

## Session contract (in order)

1. Read `research/PROTOCOL.md` (this), `research/LESSONS.md`,
   `research/BACKLOG.md`, and the last ~3 entries of `research/JOURNAL.md`.
2. Bot-keeper health check.
3. Claim the top item whose status is `pending` (set it `in-progress — session
   <timestamp>` in BACKLOG.md). **Stale claims:** an item marked `in-progress`
   with NO matching completed entry in JOURNAL.md is an orphan from a crashed
   session — reclaim it (update the timestamp) and continue; do not skip it.
   If the last 2 completed items were build items, and a research/ideation
   item exists or session count % 3 == 0, do a RESEARCH session instead (see
   below).
4. Execute. Subagents encouraged for parallel analysis (Explore for code/data
   recon, general-purpose for heavy analysis) — e.g., dispatch one to analyze
   trade lists while you build.
5. Record results, in this order:
   a. Append a structured entry to `research/findings.json` (schema below).
   b. Append a JOURNAL.md entry: what ran, headline numbers, verdict, 2-sentence
      plain-English "what we learned", next-session pointer.
   c. Significant results → a `trade_analysis/YYYY-MM-DD_<topic>.md` doc.
   d. New durable lesson → append to LESSONS.md (one line + why).
   e. Update BACKLOG.md status (`done — <verdict>` / new items appended).
6. Run the full test suite; commit everything.
7. Post a Discord summary (webhook URL in env `TOPSTEP_BOT_DISCORD_WEBHOOK_URL`;
   read it from .env if not in env): 3-6 lines, headline numbers, verdict.
   Use a simple JSON POST via python/httpx — content field, no embeds needed.
8. Exit promptly. Do not start a second item.

## Research/ideation sessions (every ~3rd session)

Goal: replenish the backlog with NEW, testable strategy hypotheses.
- Sources: WebSearch/WebFetch (futures intraday strategies, prop-firm passing
  strategies, academic intraday momentum/reversal literature, NQ-specific
  writeups) AND our own data (gate-trace mining, excursion distributions,
  per-killzone/per-regime cuts of `bars/bars_MNQ_dbv_2021_2026.csv`).
- Read LESSONS.md first. Do NOT re-propose rejected mechanism classes without
  genuinely new evidence. Documented prior: external claims have failed to
  transfer here 3-for-3 (Revelio) — every found idea is a hypothesis to
  falsify cheaply, not a recipe.
- Output: 1-3 new BACKLOG items in house mini-spec format: mechanism, exact
  deterministic rules, fixed defaults (NO tunables), defining-behavior tests,
  success criteria vs which baseline, source links. Append at rank order you
  judge correct, journal the reasoning, commit.

## Implementation pattern for new engines (use it verbatim)

Standalone detector + runner shim, exactly like `app/strategy/orb.py` /
`vwap.py` / `sweep_bos.py`: detector class consuming closed `Bar`s and emitting
the existing `Signal` dataclass; a `<Name>Runner` dataclass duck-typing the
engine surface (`instrument, timeframe, strategy_cfg, vp=None,
signal_instrument, last_reject, composer(_NoopComposer), grader(SetupGrader),
on_bar`); selection via `StrategyParams.engine="<name>"` wired in BOTH
`app/backtest/runner.py:_build_runner` and `app/main.py:_build_runner`;
TDD via a `tests/test_<name>.py`; benchmark via the harnesses below.
Closed-bar confirmation only (the forming-bar lesson). Post-entry rule exits
go through the engine's `runner.exit_request` channel (already supported).

## Benchmarks & metrics (the two objectives)

- **Combine objective:** `scripts/run_monthly_combine.py --bars <csv>
  --instrument MNQ --timeframe 5min --risk-pct 1.25 [--set k=v ...]
  [--save-id <id> --save-label "<label>"]`. Metric: monthly pass rate + run PF
  + MLL fails + per-side PF. Baseline: control 6/17 test, 4/12 2024, PF
  1.31/1.37 (registry `ab_control_*`).
- **Funded/XFA objective:** `scripts/equity_export.py --bars
  bars/bars_MNQ_dbv_2021_2026.csv --risk-pct <r> [--set ...] --out
  research/equity_<tag>.csv` then `scripts/funded_sim.py research/equity_<tag>.csv
  --haircut 200` (also report haircut 0 and 400 for sensitivity). Metrics: XFA
  net payouts, account busts, days-to-first-payout, plus Combine-chain
  passes/busts. Sizing is part of the search space here (risk-pct affects bust
  rate directly). No baseline exists yet — B1 establishes it.
- Save Combine-objective runs to the UI registry with `--save-id` so they
  render in the dashboard (`backtests/<id>.json`).

## findings.json entry schema (append to the JSON array)

{"ts": "<iso>", "session": "<short id>", "item": "B1", "variant": "orb r2.5",
 "objective": "funded" | "combine" | "dataset" | "infra",
 "metrics": {"pass_test": "4/17", "pf": 1.21, "xfa_net": 23566, "busts": 8},
 "verdict": "candidate" | "rejected" | "dataset" | "shipped",
 "learned": "<2 sentences, plain English>",
 "links": {"doc": "trade_analysis/....md", "runs": ["save_id1"], "src": "<url|null>"}}

Keep the file valid JSON (read, append to array, write).

## Expectations

Most sessions end in clean rejections or dataset increments — the parameter
plateau is real and documented. That is fine and useful. The compounding value:
the funded-objective frontier, the excursion dataset, live-parity forensics,
and the occasional structural win (ORB-class). Honesty over optimism: report
numbers that embarrass the hypothesis.

## Model tagging (targeted-Opus, Lawrence 2026-06-14)

The wrapper picks the model per session from the top `[pending]` backlog item's
header: a `model:opus` tag runs that session on Opus 4.8, otherwise Sonnet.
Sonnet keeps quota plentiful (~17 sessions/window vs ~3-4 on Opus), so reserve
Opus for work where its extra reasoning actually pays.

When you CREATE backlog items (ideation sessions), tag the hard ones. Append
`model:opus` inside the header's status bracket, e.g.
`## B## — Title  [pending — model:opus]`. Tag an item Opus only if its execution
includes BUILDING or REFACTORING a real engine / scoring / sizing code path, a
cross-instrument port, or multi-feature OOS-validated modeling. Do NOT tag:
Phase-1 data-mining gates, no-code/benchmark items, config sweeps, or items with
a high prior of clean rejection — those are Sonnet work. When in doubt, leave it
untagged (Sonnet). The tag drives only model choice, never correctness; a
mistag just means one item runs on the wrong-sized model.

Otherwise the model is not your concern in-session — just do the work.
