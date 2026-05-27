# Live Partial Profits + Break-Even Stop — Design Spec

**Date:** 2026-05-26
**Status:** Approved (pending spec review)
**Goal:** Port the backtest-only partial-profit / break-even logic to live TopstepX trading: take half off at a configurable R-multiple (default 1.5R) and move the stop to break-even on the remainder. For 1-contract entries (common under risk-based sizing), skip the scale-out but still move the stop to break-even at the same R level.

---

## Background

Partial-profit + BE-trail exists today **only** in `PaperBroker` (backtest), driven by `partial_profit_r` (`app/broker/paper.py`): on entry it computes `partial_target = entry ± R×partial_profit_r` and `partial_size = size // 2`; in `inject_bar`, when the partial target is touched it closes `partial_size`, moves the stop to break-even (`bracket.stop = bracket.entry`), and marks `partial_filled`. Live trading has none of this — it places one stop + one target at full size after the entry fills.

This spec ports the behavior to `TopstepXBroker`, which requires real order modification rather than simulated bar resolution.

## Decisions (from brainstorming)

1. **Trigger level / fraction:** half off at **1.5R**, then break-even. Configurable via a single `partial_profit_r` knob (default value 1.5 when enabled).
2. **1-contract entries:** can't split 1 contract — **skip the scale-out but still move the stop to break-even** once price reaches the 1.5R level. This is a *superset* of the backtest, which does nothing for 1-lots. (Noted divergence: the backtest will not match live for 1-lot trades unless its BE-only path is later added — out of scope here.)
3. **Order mechanics (Approach A):** fill-triggered scale-out for size≥2 (resting partial-limit; best fills), quote-price-watch BE for size==1, atomic `modify_order` for stop transitions. SDK exposes `modify_order(order_id, limit_price, stop_price, size)` — confirmed in `project_x_py/order_manager/core.py`.
4. **Default disabled:** `partial_profit_r = 0` ships disabled; enabling is an explicit config change.

## Non-goals

- No change to signal generation, risk gating, or sizing.
- No change to the disabled-path (`partial_profit_r == 0`) live behavior — it must remain byte-for-byte identical to today.
- No partial logic in the engine or risk state — it lives entirely in `TopstepXBroker`.
- No backtest changes (PaperBroker already has its own partial logic). Seeding the backtest's `partial_profit_r` from `BotConfig` is a separate, optional follow-up.

---

## Architecture

All logic lives inside `TopstepXBroker` (mirrors `PaperBroker` owning partials in the backtest). The engine and `RiskState` are untouched; they observe the resulting fills exactly as they do today.

**Isolation / safety invariant:** when `partial_profit_r == 0`, execution follows the existing path verbatim — `_place_bracket_after_fill` → stop + target → `_exit_pairs`. The new code only activates when `partial_profit_r > 0`. This guarantees no regression to the proven non-partial flow.

**Core invariant (drives all failure handling):** the live stop order's size must always equal the current open position size. An oversized stop (full-size stop left after a partial fill) is the dangerous state — if triggered it would over-sell and flip the position. Every failure branch preserves this invariant or flattens.

### State

- Keep `_exit_pairs` **unchanged** for the disabled path.
- Add `_exit_groups: dict[str, ExitGroup]` for the partials path. Each leg's `order_id` maps to the same shared `ExitGroup`:

  ```
  ExitGroup:
    entry_price: Decimal        # actual entry fill price (also the BE price)
    entry_side: "long" | "short"
    instrument: str
    stop_id: str
    partial_id: str | None      # None for size==1
    target_id: str
    be_price: Decimal           # == entry_price
    partial_size: int           # 0 for size==1
    remaining_size: int         # size - partial_size
    partial_filled: bool
  ```

- Add `_be_watches: dict[str, BeWatch]` keyed by instrument, for the size==1 BE-move:

  ```
  BeWatch:
    instrument: str
    side: "long" | "short"
    trigger_price: Decimal      # the 1.5R level
    be_price: Decimal
    stop_id: str
    armed: bool
  ```

### Config carried to fill time

`place_market_bracket` / `place_limit_bracket` already stash `_pending_brackets[entry_id] = {stop_offset, target_offset, close_sdk_side, size, account_id}`. Add `partial_r` (from `self.partial_profit_r`), `partial_size`, and `entry_side` so `_place_bracket_after_fill` knows whether to take the partial path and with what split. `partial_r` is snapshotted at placement time (consistent with how bracket config is read at fill time; a later hot-apply affects the next entry, not this one).

### Pure helper (testable without the SDK)

```
_partial_plan(entry_price, stop, size, partial_r) -> PartialPlan | None
  returns None if partial_r <= 0
  R = abs(entry_price - stop)
  partial_price = entry_price + R*partial_r   (long)   /   entry_price - R*partial_r (short)
  partial_size  = size // 2                   (0 when size == 1)
  remaining     = size - partial_size
  be_price      = entry_price
```

This is the only arithmetic; it mirrors the PaperBroker math and is unit-tested directly.

---

## State machine

### Entry fill, size ≥ 2 (scale-out path)

On entry fill in `_place_bracket_after_fill`, when `partial_r > 0` and `size >= 2`:

1. Place **stop (full size)** at the stop price — first, so the whole position is protected before anything else.
2. Place **partial-target limit** (`partial_size`) at `partial_price`.
3. Place **final-target limit** (`remaining_size`) at the final target.
4. Register the `ExitGroup` under all three leg ids. Re-check `_early_fills` for any leg that filled before registration (same pattern as today's stop/target early-fill replay).

Transitions:

- **partial limit fills** → `modify_order(stop_id, stop_price=be_price, size=remaining_size)`; emit a partial EXIT `Fill` (P&L on `partial_size`); set `partial_filled = True`; drop `partial_id` from the group. Group is now a BE-stop ↔ final-target OCO at `remaining_size`.
- **final target fills** → cancel stop; emit remainder EXIT; clear group.
- **stop fills before partial** → cancel partial + final; emit full-size EXIT (loss at original stop); clear group.
- **stop fills after partial (BE, remaining)** → cancel final; emit remainder EXIT (break-even); clear group.

### Entry fill, size == 1 (BE-only path)

When `partial_r > 0` and `size == 1`:

1. Place stop (1) + target (1) — the normal 2-leg bracket.
2. Register an `ExitGroup` (no partial leg) for OCO **and** a `BeWatch` keyed by instrument with `trigger_price = partial_price`.
3. `_on_quote_update` gains a cheap check `_maybe_move_stop_to_be(instrument, mid)`: when an armed watch's `trigger_price` is crossed (mid ≥ trigger for long, mid ≤ trigger for short), call `modify_order(stop_id, stop_price=be_price)`, disarm the watch.

Transitions: standard stop ↔ target OCO (the existing `_exit_groups` cancel logic with `partial_id = None`). The only addition is the BE upgrade via the watch.

---

## Failure handling (Rule 12)

- **Partial/final leg placement fails (size≥2):** stop (full) is already live, so the position is protected. Cancel any target leg that did place; place a single full-size target at the final level (degrade to today's 2-leg bracket). Log ERROR + notify. No scale-out this trade.
- **`modify_order` (stop → BE + resize) fails after partial filled — critical:** retry once. If still failing → cancel the oversized stop, place a fresh stop at `be_price` + `remaining_size`. If the replace also fails → log ERROR, notify, and **market-flatten the remainder**. Never leave an oversized or naked stop.
- **size==1 BE modify fails:** retry once → cancel-replace at BE + size 1 → if that fails, leave the original (correctly-sized) stop in place and alert. Safe: stop size still matches the 1-lot position; only the BE upgrade is lost.
- **OCO sibling-cancel fails:** best-effort cancel with logging (as today); the reconciler is the backstop for any lingering order / position drift.
- **Early / duplicate fills:** reuse `_early_fills` buffering and `_processed_fill_ids` dedup. After registering an `ExitGroup`, re-check `_early_fills` for each leg, exactly as `_place_bracket_after_fill` does today.

---

## Config wiring (mirrors `risk_per_trade_pct` end-to-end)

| Layer | Change |
|---|---|
| `app/bot_config.py` | `BotConfig.partial_profit_r: Decimal = Decimal("0")` (top-level, next to `risk_per_trade_pct`); serialize in `save_bot_config` via the Decimal→str helper. |
| `app/main.py` | Pass `partial_profit_r=bot_cfg.partial_profit_r` into `TopstepXBroker(...)`. |
| `app/broker/topstepx.py` | `__init__` accepts `partial_profit_r: Decimal = Decimal("0")`, stores `self.partial_profit_r`. |
| `app/api/server.py` | `GET /api/config` returns `float(cfg.partial_profit_r)`; `PATCH` hot-applies `_broker.partial_profit_r = body.partial_profit_r` and returns it. (Affects the next entry; open positions keep their legs.) |
| `frontend/src/types.ts` | `partial_profit_r: number`. |
| `frontend/src/components/ConfigPanel.tsx` | "Partial profit (R, 0=off)" number field — init/submit/render — help text noting BE-move also applies to 1-lot entries. |

Default `0` (disabled). Recommended live value: `1.5`.

---

## Testing & verification

**Unit (the SDK cannot be unit-tested, so isolate the logic):**
- `_partial_plan(...)` — partial price (long/short), `partial_size = size//2`, `remaining`, `be_price == entry`; `size==1 → partial_size 0`; `partial_r==0 → None`.
- Group transitions with a **stub orders-client** recording `place_stop_order` / `place_limit_order` / `modify_order` / `cancel_order` calls. Assert:
  - partial fill → `modify_order(stop_id, stop_price=BE, size=remaining)` called.
  - stop-after-partial → final target cancelled.
  - stop-before-partial → partial + final cancelled.
  - the stop-size == position-size invariant holds after every transition.
  - `modify_order` failure → cancel-replace, then flatten path fires.
- `BeWatch`: quote crossing `trigger_price` → `modify_order(stop_id, stop_price=BE)` once; not re-fired after disarm.
- Disabled path (`partial_profit_r == 0`) → no `_exit_groups` / `_be_watches` created; identical to current `_exit_pairs` flow.

**Success criteria (Rule 4) — demo/eval account, hard prerequisite before live:**
- size≥2 entry with `partial_profit_r=1.5`: partial fills at the 1.5R level; stop moves to BE at `remaining_size`; confirmed via `scripts/sdk_diagnostic.py` / exchange UI.
- size==1 entry: stop moves to BE after price crosses 1.5R.
- Stop size equals position size at every point (no oversize) throughout both flows.

---

## Risks

- **Oversized stop after partial** — mitigated by the core invariant + the modify-failure → cancel-replace → flatten ladder.
- **`modify_order` semantics differ from assumption** (e.g. it cancels+replaces internally, changing the order id) — must be confirmed against SDK source during implementation before relying on in-place modify; the cancel-replace fallback covers the case where modify is unreliable.
- **Quote-driven BE watch noise** — the watch is a single armed boolean per instrument with one comparison; negligible cost, disarms after firing.
- **Real money** — demo-account validation is a hard gate (Rule 4); ship disabled by default.
