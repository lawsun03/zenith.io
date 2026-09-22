# B92 — news_straddle LIVE resting-OCO build

**Session:** wk7-b92 · 2026-06-15 · Opus · verdict: **shipped (default-off)**

## Goal
Deploy the B89-validated `news_straddle` engine live for CPI using **resting
exchange stop entries** (Lawrence-approved design 2026-06-14). The B89 engine's
signal logic was already oracle-parity-tested (47/47); the bar-driven path fills
at bar close, which the PaperBroker can model but a live resting order cannot.
B92 adds the live order-routing + wall-clock scheduling. Default-off; Lawrence
decides go-live.

## What was built (all default-off; bot_config.json untouched)

### 1. Broker — `TopstepXBroker.place_oco_stop_entries` (`app/sim/topstepx.py`)
Places **two resting stop ENTRY orders**:
- `buy_stop` (side BUY, above the range) → fills LONG on an upside break.
- `sell_stop` (side SELL, below the range) → fills SHORT on a downside break.

SDK verification (CLAUDE.md Rule 1/8, real money): read
`project_x_py/order_manager/order_types.py:147` — `place_stop_order(contract_id,
side, size, stop_price, account_id)` is "a market order triggered at stop price",
direction-agnostic, and routes through the low-level `place_order` (NOT
`place_bracket_order`, which carries the documented 60s-wait trap). A buy-stop
with no position opens long; a sell-stop opens short.

Each leg is pre-registered in the existing `_pending_brackets` dict with offsets:
- Long: `stop_offset = -R`, `target_offset = +R·tp_r` → on fill at `buy_stop`,
  stop = broken range high (tight, R=offset), target = 3R.
- Short: mirror.

`partial_r` is forced to `0` — the validated straddle (B89) takes the full target,
no scale-out. On fill, the proven `_place_bracket_after_fill` attaches stop+target.
If either leg can't be placed, the surviving leg is cancelled (no single naked
resting entry).

### 2. OCO cancel-sibling-on-fill (`_cancel_oco_sibling`, hooked in `_on_fill_event`)
New `_oco_entry_siblings: dict[str,str]` links the two legs. When a leg's entry
fill is dispatched (the definitive `_pending_brackets` branch), the sibling is
cancelled AND popped from `_pending_brackets` so a late/raced sibling fill can
never attach a second, opposite bracket.

### 3. Wall-clock scheduler — `NewsStraddleScheduler` (`app/notifications/news_straddle_scheduler.py`)
Buffers 1-min bars, sleeps until `arm_lead_seconds` (default 120s) before each
event, locks the pre-range, computes `buy_stop = high+offset` /
`sell_stop = low-offset`, and calls `place_oco_stop_entries`. One straddle per
event; never re-arms a fired/skipped event. Async-timeout loop mirroring
`EndOfDayScheduler`; injectable `now_fn` for testability.

**Window note (honesty / Rule 12):** the B85/B89 oracle locked the range over
`[release-15min, release)`. To be working before the print, the scheduler locks
the **same-length** window shifted earlier by the lead:
`[arm_time-range_minutes, arm_time)` where `arm_time = release - arm_lead`. The
straddle *mechanism* (breakout of the pre-news consolidation) is unchanged; exact
P&L re-validation against the shifted window is a documented follow-up (B95).

### 4. Wiring + observability (Rule 13)
- `app/main.py`: scheduler constructed only when `engine=="news_straddle"` AND
  `news_straddle_live_enabled`; bar buffer via `broker.on_bar`; started after the
  reconciler, stopped in `finally`. A loud `WARNING` logs that the live path is on.
- New config (default-off): `news_straddle_live_enabled=False`,
  `news_straddle_contracts=1`, `news_straddle_arm_lead_seconds=120`.
- SSE: `strategy_state.news_straddle` carries `scheduler.state()` (per-event
  status + locked range). The grader-centric publisher was guarded with `getattr`
  so the news_straddle runner (no `displacement`/`composer._zones`) no longer
  crashes it — a latent B89 gap fixed here.
- Frontend: `StrategyDebug.tsx` renders a "News Straddle" section (offset/target,
  contracts, recent armed/skipped events with locked range). `npm run build` clean.

## Verification
- TDD: `tests/test_news_straddle_live.py` — 11 defining-behavior tests (RED before
  implementation): two-stop placement with correct sides/prices, tight-stop+3R
  offsets, sibling linkage, cancel-sibling-on-fill + de-registration, non-OCO
  no-op, **end-to-end money math** (buy fill @125 → stop 110 / target 170 through
  the real `_place_bracket_after_fill`), scheduler arming from pre-range, skip on
  too-few-bars, no-re-arm, state shape, config default-off.
- Full suite: **782 passed, 2 skipped, 0 failures** (was 771). Frontend build clean.

## Remaining (manual, for Lawrence)
The success criterion "paper run on a CPI day" needs a **real 08:30 CPI print** —
not executable this weekend (market closed; next CPI per `data/news_events.csv` is
a future date). The chain is proven deterministically end-to-end in tests. To
go live: set `news_straddle_live_enabled=true`, `engine="news_straddle"`, confirm
`data/news_events.csv` has the upcoming release, watch the dashboard arm ~2 min
pre-release on the next CPI day in **paper** mode before any funded enable.
