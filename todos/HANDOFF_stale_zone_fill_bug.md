# Handoff: Stale Armed-Zone Fill Bug (parity post-mortem follow-up)

> **RESOLVED 2026-06-11.** Open question answered: all six parity configs ran
> `ifvg_entry_mode: "close"` — the armed-zone path was bypassed; the staleness
> source was `_find_inverted_fvg` matching FVGs price had closed through long
> ago (no on-this-bar check, no consumption). Fixed: prev-close condition in
> `displacement.py`; zone expiry (`ifvg_zone_max_age_bars`, default 120) in
> `armed_zone.py`; PaperBroker stale-entry refusal (`max_entry_slippage_frac`).
> Parity re-run: +$21,360 fantasy → −$870 (live was −$488). See the resolution
> section in `trade_analysis/2026-06-10_parity.md`. All prior backtest results
> (incl. MNQ 5min walk-forward) need re-running with the fixed signal path.

Context: `trade_analysis/2026-06-10_parity.md` found backtest entries recorded
~160 pts outside the bar range (e.g. MGC 4361.20 entry vs 4197.8–4202.6 bar),
inflating backtest P&L by ~$21k. This doc captures the verified diagnosis so
the fix session can start from a precise target.

## Verified findings (read-only review, 2026-06-11)

### 1. Root cause: `ArmedZoneTracker` has no expiry
`app/strategy/armed_zone.py`
- `created_at` is stored on `ArmedZone` but **never checked anywhere**.
- Invalidation is one-sided (body-close through the FAR edge only):
  - long: dies only if `bar.close < fvg_low`
  - short: dies only if `bar.close > fvg_high`
- If price moves AWAY from the zone (e.g. rallies above a long zone), the zone
  stays `pending` forever. When price later trades back through the stale
  `entry_price` (`bar.low <= entry_price` for longs), it returns `"filled"`.
- The engine (`app/execution/engine.py` ~line 199) then builds the execution
  signal with `entry=zone.entry_price, stop=zone.stop_price` — days-old
  structure. Repeated identical entry prices on consecutive trades (MNQ
  28782.50 at 18:18 and 18:32) are this mechanism re-firing.
- This also explains most of the **31 backtest-only trades** in the parity run:
  live process restarts clear `_pending_signal`/tracker state; the backtest
  replays continuously so stale zones survive and fire.

### 2. A partial mitigation already landed in `PaperBroker.place_bracket`
`app/sim/paper.py` (~line 216, comment dated 2026-06-10):
- Market fills now anchor to `_last_bar_close[instrument]` + slippage instead
  of the signal's entry. `inject_bar` updates `_last_bar_close` BEFORE
  resolving brackets / fanning out, so ordering is correct.
- **Incomplete because:**
  a) the stale trade still occurs — it just fills at market now;
  b) stop/target are re-anchored as offsets: `stop = slipped_entry + (stop - entry)`
     — the offset was computed from days-old structure, so risk geometry is
     arbitrary relative to current price;
  c) the journal/grader still record `zone.entry_price` as the signal entry.
- VERIFY whether the parity numbers in the 06-10 doc were produced before or
  after this paper.py change landed (same-day). Re-run parity after the zone
  fix either way.

### 3. Open question: which path produced the stale parity entries?
`bot_config.json` runs `ifvg_entry_mode: "close"`, which BYPASSES the
armed-zone path (`_arm_or_return` returns the signal immediately). Either:
- the parity backtests ran in `ifvg_edge`/`retrace_ce` mode (check the
  `backtests/parity_*_0610.json` config blobs), or
- there is a second staleness source in the close-mode / forming-bar path
  (`try_signal_from_forming`) that also needs fixing.
Resolve this FIRST — it determines whether the zone-expiry fix covers the bug.

## Proposed fix set

1. **Zone expiry** in `ArmedZoneTracker.on_bar`: invalidate when
   `bar.ts - zone.created_at > max_age` (config field, e.g.
   `ifvg_zone_max_age_bars` or minutes; ICT convention suggests same-session /
   same-killzone validity). Return a distinct status or reuse `"invalidated"`
   with a logged reason so the rejection ledger captures it.
2. **Distance guard (optional but cheap):** invalidate if price has moved
   > N × zone height (or N × ATR) beyond the entry before filling — catches
   the "price ran away then came back" case even within the age window.
3. **Reject stale fills at the broker boundary:** in `place_bracket`, if
   `abs(entry - market) > threshold`, refuse/log instead of silently
   re-anchoring. Live broker path needs the same guard (`max_entry_slippage_frac`
   is currently "0" = disabled in bot_config.json).
4. **Tests** (Rule 9 — encode intent):
   - zone armed at T, price moves away, returns at T+N past max_age → NOT filled,
     invalidated with reason=expired.
   - zone within max_age → fills as before (regression guard on existing
     `tests/test_armed_zone.py` / `test_ifvg_inversion.py`).
   - paper broker refuses entry when signal entry is > threshold from market.
5. **Re-run the 06-10 parity check** (`backtests/parity_*_0610.json` configs,
   same bars CSVs) and update `trade_analysis/2026-06-10_parity.md` with
   before/after match rates. Success = backtest-only trade count drops sharply
   and no recorded entry sits outside its fill bar's range.

## After this fix (priority order from the strategy review)
1. Re-run MNQ 5min walk-forward **with risk limits ON and Combine-realistic
   sizing** — test-period maxDD ($6.8–7.8k) vs the 50K Combine's ~$2k trailing
   max loss is the binding constraint, not PF.
2. Turn on quality gates: raise `grader_min_grade` from "F", implement the
   fib >= 1.0 gate (identified 06-08 as the dominant losing cluster).
3. Slippage: set a real `max_entry_slippage_frac` or move MNQ to limit entries
   (06-09 logged +13 to +43 pt market slippage; 9/20 trades stopped_then_target).
