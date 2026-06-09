# Exit-Coverage Monitor — Design

**Date:** 2026-06-08
**Status:** Approved design, pending implementation plan
**Category:** risk
**Supersedes:** the `open_contracts` persistence idea (rejected — see Background)

---

## Background & motivation

A position open on the exchange with no protective stop is the worst state the bot
can be in: unbounded downside, silently. This can happen several ways:

- **Bracket placement failed** after an entry filled (network error, SDK exception).
- **Exchange-side cancel** — a stop/target cancelled outside the bot's knowledge.
- **Partial-fill / placement race** — entry filled but brackets not yet placed.
- **Post-restart orphan** — bot restarts mid-position; `_exit_groups` is cleared, so
  internal tracking no longer knows the position has (or lacks) protection.

The original ticket proposed persisting `RiskState.open_contracts` across restarts so
the reconciler would recognize an inherited position. That was **rejected**: making the
bot *recognize* a position without verifying its protection just converts a loud failure
(contract-count drift → flatten + lockout) into a silent one (counts match → no action,
position left naked). The robust fix is to monitor the thing that actually matters —
**does every open contract have an exit strategy on the exchange?** — rather than to
reconstruct internal bookkeeping.

This design is **complementary** to the existing reconciler checks, not a replacement:

| Check | Catches | Action |
|---|---|---|
| Contract-count drift (exists) | We disagree with broker on *how many* contracts | Flatten all + session lockout |
| **Exit-coverage (this design)** | Counts agree, but a position has *no stop and/or no target* | Escalating: grace → re-attach missing legs → flatten if stop unrecoverable |
| Balance drift (exists) | Realized balance diverges | Adopt broker truth, notify |

Contract-drift runs **first and takes precedence**: if we don't even agree on the
position size, flattening everything is correct and the exit-coverage check is moot.
Exit-coverage only runs on ticks where `broker_contracts == internal_contracts`.

---

## Detection

**Ground truth is the exchange**, not internal tracking. Internal tracking
(`_exit_groups` etc.) is wiped by a restart and can't see exchange-side cancels.

New broker-protocol method:

```python
async def exit_coverage(self, instrument: str) -> ExitCoverage: ...
```

returning a small dataclass:

```python
@dataclass(frozen=True)
class ExitCoverage:
    instrument: str
    position_size: int      # absolute contracts open (0 = flat)
    covered_stop: int       # sum of working stop-order sizes on the closing side
    covered_target: int     # sum of working limit-order sizes on the closing side
```

`TopstepXBroker.exit_coverage`:
1. `position_size` from `get_positions()` (already used by the reconciler) — absolute size
   for `instrument`, closing side derived from position direction (long → SELL/Ask close,
   short → BUY/Bid close).
2. `await self._suite.orders.search_open_orders(contract_id=...)` (SDK, confirmed available
   — `order_manager/core.py:651`, returns `list[Order]`).
3. Filter to working orders on the **closing side**: `status == 1` (Open) and
   `side == close_side`.
   - `covered_stop` = Σ `size` where `type in {3 StopLimit, 4 Stop, 5 TrailingStop}`.
   - `covered_target` = Σ `size` where `type == 1 Limit`.

A position is **fully covered** iff `covered_stop >= position_size` AND
`covered_target >= position_size`. The sum-based check is robust to the partial-exit
ladder (partial leg + final leg sum to full size; after a partial fills, the reduced
position is still covered by the remaining legs).

`PaperBroker.exit_coverage` returns full coverage for any open position (paper brackets are
simulated and never naked), so backtests and unit tests don't false-alarm.

---

## Reconciler integration

- New `drift_kind = "naked_position"`.
- `ReconcileReport` gains `naked_instruments: list[str]` for dashboard/test inspection.
- A **naked grace window** mirrors the existing order-placement grace: the first tick a
  position is seen naked only records a per-instrument "naked since" timestamp and logs
  WARN — no action. This absorbs the normal fill→bracket-placement race. Reuse the same
  `grace_period_after_order_seconds` semantics or a dedicated
  `naked_grace_seconds` (default 15s).

Tick flow (only reached when `broker_contracts == internal_contracts`, i.e. after the
contract-count check passes):

```
for each open position p:
    cov = await broker.exit_coverage(p.instrument)
    if cov is fully covered:
        clear p.instrument from _naked_since
        continue
    if p.instrument not in _naked_since:
        _naked_since[p.instrument] = now; log WARN; continue   # grace
    if now - _naked_since[p.instrument] < naked_grace_seconds:
        continue                                               # still in grace
    await _remediate_naked(cov)                                # escalate
```

---

## Remediation — escalating, per naked instrument

Compute the gaps:

- `stop_gap   = position_size - covered_stop`
- `target_gap = position_size - covered_target`

Then, **re-attach only the missing leg(s):**

1. **Stop (capital protection — mandatory).** If `stop_gap > 0`, place a stop for
   `stop_gap` contracts on the closing side at
   `avg_price ± emergency_stop_distance[instrument]`
   (− for long i.e. stop below, + for short). `avg_price` from the broker position.
   - If the stop order is **rejected or raises → `await broker.flatten(instrument)`**
     (instrument-scoped) as the last resort. Downside-unprotected is the real emergency.
2. **Target (no capital risk — best-effort).** If `target_gap > 0`, place a limit for
   `target_gap` contracts on the closing side at
   `avg_price ± (emergency_target_r × emergency_stop_distance[instrument])`
   (+ for long, − for short).
   - If the target placement fails → log ERROR + notify, **do not flatten**. A missing
     target is not a capital risk.

Every detection and every remediation step logs at ERROR and fires the existing reconciler
notifier (email/Discord). Once both legs exist, the next tick sees full coverage and clears
the naked state — no "flagged forever" loop.

**Note on emergency orders & internal tracking:** emergency re-attached orders are placed
directly via the suite and are **not** registered in `_exit_groups` (we don't have the
original entry context, and the partial/BE state machine should not drive them). They are
plain protective orders. This is intentional and documented so a future reader doesn't
"fix" the missing registration. Their OCO behavior is the exchange's responsibility; if one
fills, the position closes and the next tick sees the remaining leg as over-cover on a flat
position (harmless — `position_size == 0` short-circuits the check).

---

## Configuration

New `BotConfig` fields (flat, matching existing convention):

```python
emergency_stop_distance: dict[str, Decimal] = {
    "MGC": Decimal("3.0"),
    "MNQ": Decimal("40.0"),
    "MES": Decimal("5.0"),
}                                                   # price points from avg entry
emergency_target_r: Decimal = Decimal("2.0")        # target = target_r × stop distance
naked_grace_seconds: float = 15.0                   # suppress placement-race false positives
```

Per-instrument stop distance because tick scale/volatility differ across MGC/MNQ/MES. A
single R-multiple for the target avoids a second per-instrument dict. An instrument missing
from `emergency_stop_distance` falls back to flatten-only (no safe distance to guess →
don't invent one).

Per CLAUDE.md Rule 10/Rule 3: persisted in `save_bot_config`, returned in `GET /api/config`,
hot-applied in `PATCH /api/config` (reconciler config is read live or re-pushed), added to
frontend `types.ts` and the config form.

---

## Observability (Rule 12 — fail loud)

- `ExitCoverage` shortfalls log at WARN (grace) then ERROR (acting).
- Each remediation step (re-attach stop, re-attach target, flatten) logs ERROR with
  instrument, sizes, prices.
- Notifier fires on first action per naked event.
- `ReconcileReport.naked_instruments` exposes current state for the dashboard's reconciler
  panel.

---

## Testing (Rule 9 — tests encode WHY)

`exit_coverage` calc (parametrized **across MGC / MNQ / MES**):
- position 2, one working stop size 2 + one limit size 2 → fully covered, no action.
- position 2, stop size 2, **no limit** → target_gap=2 → re-attach target only, no flatten.
- position 2, limit size 2, **no stop** → stop_gap=2 → re-attach stop; flatten NOT called.
- position 2, **no working orders** → both gaps; re-attach both.
- partial ladder: stop size 2 + partial limit 1 + final limit 1 → covered (sum check).

Reconciler behavior:
- First naked tick takes **no action** (grace); second tick (past `naked_grace_seconds`)
  remediates. Encodes WHY: the fill→bracket race must not trigger emergency orders.
- Stop re-attach **failure → `flatten(instrument)`** called, scoped to that instrument only
  (must not touch sibling symbols — ties to the 2026-06-08 cancel_all incident).
- Target re-attach failure → notify, **flatten NOT called** (missing target ≠ capital risk).
- **Contract-count drift takes precedence:** when counts diverge AND a position is naked,
  the drift path (flatten + lockout) runs and the naked path does not.
- `PaperBroker.exit_coverage` reports covered → backtests never trigger remediation.

Mocking note (Rule 9): tests mock `search_open_orders`; they therefore cannot catch a
real SDK schema change in the `Order` model. Documented in the test module.

---

## Out of scope

- Persisting `open_contracts` / `RiskState` across restarts (rejected approach).
- Re-attaching the *original* stop/target prices or the partial/BE state machine — emergency
  orders are deliberately plain.
- Any change to the contract-count or balance drift logic.
