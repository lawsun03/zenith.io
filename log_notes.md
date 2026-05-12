# topstep-bot Log Observations

## [2026-05-12 18:30] Check-in

**Signals:** 17 signals fired across paper-replay session (18:04 run) — mix of NY AM, NY PM, and London killzones; both A_multi_bar and B_one_bar sweep types; ~10 short, ~7 long. Zero signals in the 01:18 replay (bar processing completed before any killzone windows opened, or replay was too fast to log individually).

**Orders placed:** 17 paper orders (PAPER-1 through PAPER-17), all limit entries. All placed via paper broker — no live TopstepX SDK calls in either session.

**Fills:** 17/17 entries filled; all 17 brackets subsequently closed (stop or target). 100% fill rate in paper mode.

**Brackets placed:** Yes — all 17 had stop + target both placed immediately after fill. No orphaned positions observed. Example: PAPER-1 entry=4831.8, stop=4827.90, target=4841.550 → closed at target (4841.550, P&L=+97.50).

**Errors/Warnings:**
- `asyncio.exceptions.CancelledError` on shutdown (×2 sessions, ×2 each) — this is the normal SIGINT teardown sequence from uvicorn/Starlette lifespan. Not an operational error.
- `Email notifications disabled (no SMTP env vars)` — expected, not an issue.
- No SDK errors, no broker rejections, no exceptions during trading activity.

**Notable patterns:**
- **CRITICAL: Both sessions today ran in paper/replay mode (`mode=paper`), not live TopstepX mode.** No live-mode session is present in today's logs. The order-fill problems Lawrence is investigating (with the real TopstepX broker/SDK) cannot be diagnosed from these logs — they only show the paper broker, which fills everything instantly and never touches the exchange.
- The paper execution pipeline is working correctly end-to-end: signal → limit order → fill event → bracket (stop + target) → close. If fills are failing in live mode, the bug is in `topstepx.py` / the `project-x-py` SDK path, not in the strategy or execution engine logic.
- Both sessions were short replays: the 01:18 session ran ~22 seconds (20001 bars at 0ms/bar delay then SIGINT); the 18:04 session ran ~13 minutes (replay + idle then SIGINT).
- To diagnose live fill failures, a live-mode session (`mode=live`) is needed. When that runs, look for: `place_limit_order` / `place_market_order` call results, any `ERROR` lines in `topstepx.py`, `_pending_brackets` entries that are populated but never cleared (entry submitted but fill event never received), and SDK timeout/exception messages.
