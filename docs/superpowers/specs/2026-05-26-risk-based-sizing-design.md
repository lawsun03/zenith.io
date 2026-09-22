# Risk-Based Position Sizing — Design Spec

**Date:** 2026-05-26
**Status:** Approved design, pending implementation
**Author:** brainstormed with Claude

## Problem

Position size is fixed at `contracts: 4`, but stop distance is variable (placed beyond
swept structure). So per-trade dollar risk is uncontrolled: on 2026-05-26 it ranged from
$84 (2.1pt stop) to $432 (10.8pt stop) on the "same" 4 lots — a 5× spread. The −$432
trade was correctly stopped (not a bug); the flaw is that risk per trade is not normalized.
See [[trade-review-priorities-2026-05-26]] (priority #1).

## Goal

Size each entry so dollar risk is a fixed percent of account equity, regardless of stop
distance. Cap the tail the −$432 trade exposed without skipping trades.

Non-goals: changing stop placement, partial profits/BE (separate work), per-instrument
risk overrides, dynamic risk by setup quality.

## Decisions (from brainstorming)

- **Risk basis:** percent of account equity (`RiskState.current_equity`).
- **Default:** `0.25` (= 0.25%). On $50k ≈ $125/trade.
- **Percent representation:** the field value is a percent; budget = `equity * (pct / 100)`.
- **Wide-stop edge:** if even 1 contract exceeds the budget, **take 1 contract anyway**
  (floor to a minimum of 1).
- **Upper cap:** account `max_contracts` (30 for the 50k combine). Risk sizing may scale
  up to this cap on tight-stop setups.
- **Disabled sentinel:** `risk_per_trade_pct <= 0` → fall back to the fixed `contracts`
  field (today's behavior), matching the project's "0 = disabled" convention.
- **Point value:** reuse the canonical `app.sim.topstepx._point_value()` — the existing
  source of truth for live P&L. No second/driftable copy.

## Architecture

Compute size in the **engine** (`_act_on_signal`), which already builds `ProposedOrder`
and already depends on the broker. The arithmetic is a **pure, tested function** in a new
`app/risk/sizing.py`. The pretrade gate (`check`) is **unchanged** — it continues to clamp
the requested size to `max_contracts` headroom, which IS the chosen upper cap, so the cap
falls out for free. Keeping the hard gates untouched lowers risk.

### Pure sizing function (`app/risk/sizing.py`)

```python
def risk_based_size(
    equity: Decimal,
    risk_pct: Decimal,        # percent units, e.g. Decimal("0.25")
    stop_distance: Decimal,   # price points, > 0
    point_value: Decimal,     # $ per point per contract, > 0
    max_size: int,            # account max_contracts
) -> int:
    budget = equity * (risk_pct / Decimal("100"))
    risk_per_contract = stop_distance * point_value
    raw = int(budget // risk_per_contract)   # floor
    return max(1, min(raw, max_size))        # floor-to-1, cap at max_size
```

Preconditions (caller guarantees): `risk_pct > 0`, `stop_distance > 0`, `point_value > 0`,
`equity > 0`. The function is total given those; no I/O, no exceptions on valid input.

### Engine wiring (`_act_on_signal`)

```python
if self.risk_per_trade_pct and self.risk_per_trade_pct > 0:
    equity = self.risk_state.current_equity
    if equity <= 0:                              # pre-first-tick fallback
        equity = self.risk_state.realized_balance
    stop_distance = abs(signal.entry - signal.stop)
    pv = _point_value(signal.instrument)
    size = risk_based_size(
        equity, self.risk_per_trade_pct, stop_distance, pv,
        max_size=self.risk_state.config.max_contracts,
    )
else:
    size = self.contracts                        # unchanged default
```

Then `ProposedOrder(size=size, ...)` exactly as today; `check()` clamps to headroom.

**Equity fallback rationale:** `RiskState.__post_init__` sets `realized_balance` and
`equity_high_water` to `starting_balance` but leaves `_current_equity` at 0 until the
first `mark_equity()` tick. Without the fallback, the first signal of a session would
compute budget 0 → floor to 1 lot. The fallback uses `realized_balance` (always set).

### Logging (Rule 12)

One INFO line per risk-sized entry, before placement:
```
Risk-sized: equity=$50000 budget=$125.00 stop=10.8pt $/ct=$108.00 -> size=1 (OVER-BUDGET floored to 1)
```
The `OVER-BUDGET floored to 1` suffix appears only when `raw < 1` (wide-stop case), so
post-mortems can see when a trade exceeded the intended budget.

## Config wiring (Rule 10 checklist)

| Where | Change |
|-------|--------|
| `app/bot_config.py` | add `risk_per_trade_pct: Decimal = Decimal("0.25")` to `BotConfig` |
| `app/main.py` | pass `risk_per_trade_pct` into `ExecutionEngine`; persist via existing `save_bot_config` |
| `app/execution/engine.py` | ctor param `risk_per_trade_pct`; store `self.risk_per_trade_pct`; use in `_act_on_signal`; import `_point_value` and `risk_based_size` |
| `app/api/server.py` | return field in `GET /api/config`; hot-apply in `PATCH /api/config` (`_engine.risk_per_trade_pct = ...`) |
| `frontend/src/types.ts` | add `risk_per_trade_pct: number` |
| `frontend/src/components/ConfigPanel.tsx` | add form field (label "Risk % per trade", `text-dim`) |

## Files touched

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `app/risk/sizing.py` | pure `risk_based_size()` |
| Create | `tests/test_sizing.py` | unit tests for the pure function |
| Modify | `app/execution/engine.py` | compute size in `_act_on_signal`; log decision |
| Modify | `app/bot_config.py` | `risk_per_trade_pct` field |
| Modify | `app/main.py` | wire field into engine |
| Modify | `app/api/server.py` | GET + PATCH hot-apply |
| Modify | `frontend/src/types.ts` | type field |
| Modify | `frontend/src/components/ConfigPanel.tsx` | form field |
| Modify | `tests/test_engine.py` | engine-level sizing test |

## Success criteria

- `risk_based_size` unit tests pass: normal down-size, wide-stop floor-to-1-over-budget,
  tight-stop clamp-to-max_contracts, equity fallback path.
- Engine test: a 10.8pt MGC stop at $50k / 0.25% yields size 1 (≈$108 risk), not 4 (≈$432).
- With `risk_per_trade_pct=0`, size equals `contracts` (today's behavior) — regression-safe.
- `GET /api/config` shows the field; `PATCH` changes it on the running engine with no restart.
- Frontend renders the field, `npm run build` clean.

## Out of scope (Rule 2)

- No per-instrument risk %, no setup-quality scaling, no stop-placement changes.
- No extraction of a shared instruments module — reuse `topstepx._point_value` as-is.
  (Map duplication between `topstepx.py` and `paper.py` is noted as future cleanup, not
  fixed here — Rule 3 surgical.)
