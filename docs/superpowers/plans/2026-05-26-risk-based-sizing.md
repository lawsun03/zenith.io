# Risk-Based Position Sizing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Size each entry so dollar risk is a fixed percent of account equity (default 0.25%), capping the variable-stop tail risk that produced the −$432 trade, while falling back to the fixed `contracts` count when disabled.

**Architecture:** A pure, tested `risk_based_size()` lives in `app/risk/sizing.py`. The engine computes entry size in a new `_entry_size()` method (using `RiskState.current_equity` and the canonical `topstepx._point_value`), then builds `ProposedOrder` as today; the pretrade gate is unchanged and still clamps to `max_contracts` headroom. A new `risk_per_trade_pct` BotConfig field (0 = disabled) is wired through main → engine → API → frontend.

**Tech Stack:** Python, Decimal arithmetic, Pydantic (`BotConfig`), FastAPI, React/TypeScript, pytest.

**Spec:** `docs/superpowers/specs/2026-05-26-risk-based-sizing-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `app/risk/sizing.py` | pure `risk_based_size()` — equity × pct ÷ (stop × point_value), floor-to-1, cap at max_size |
| Create | `tests/test_sizing.py` | unit tests for the pure function |
| Modify | `app/execution/engine.py` | ctor param `risk_per_trade_pct`; `_entry_size()`; use it in `_act_on_signal`; imports |
| Modify | `tests/test_engine.py` | engine-level sizing test (wide stop → 1, disabled → contracts) |
| Modify | `app/bot_config.py` | `risk_per_trade_pct` field + `save_bot_config` serialization |
| Modify | `app/main.py` | pass `risk_per_trade_pct` into `ExecutionEngine` |
| Modify | `app/api/server.py` | return field in `GET /api/config`; hot-apply in `PATCH /api/config` |
| Modify | `frontend/src/types.ts` | `risk_per_trade_pct: number` on the config interface |
| Modify | `frontend/src/components/ConfigPanel.tsx` | form field (init, submit, render) |

---

## Task 1: Pure sizing function

**Files:**
- Create: `app/risk/sizing.py`
- Create: `tests/test_sizing.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sizing.py`:

```python
"""Tests for risk-based position sizing (pure function)."""
from decimal import Decimal

from app.risk.sizing import risk_based_size


# MGC point value = $10/pt. $50k equity, 0.25% = $125 budget.
EQUITY = Decimal("50000")
PCT = Decimal("0.25")
PV = Decimal("10")
MAXSZ = 30


def test_normal_stop_sizes_down_from_budget():
    """3.0pt stop -> $30/contract; $125 budget // $30 = 4 contracts."""
    assert risk_based_size(EQUITY, PCT, Decimal("3.0"), PV, MAXSZ) == 4


def test_wide_stop_caps_risk():
    """10.8pt stop -> $108/contract; floor(125/108)=1. The -$432 trade case."""
    assert risk_based_size(EQUITY, PCT, Decimal("10.8"), PV, MAXSZ) == 1


def test_very_wide_stop_floors_to_one_over_budget():
    """20pt stop -> $200/contract > $125 budget; floor=0 -> floored up to 1."""
    assert risk_based_size(EQUITY, PCT, Decimal("20"), PV, MAXSZ) == 1


def test_tight_stop_clamps_to_max_size():
    """0.1pt stop -> $1/contract; raw=125 but capped at max_size=30."""
    assert risk_based_size(EQUITY, PCT, Decimal("0.1"), PV, MAXSZ) == 30


def test_half_percent_doubles_budget():
    """0.5% = $250 budget; 3.0pt stop -> floor(250/30)=8."""
    assert risk_based_size(EQUITY, Decimal("0.5"), Decimal("3.0"), PV, MAXSZ) == 8
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_sizing.py -v`
Expected: `ModuleNotFoundError: No module named 'app.risk.sizing'`

- [ ] **Step 3: Write the implementation**

Create `app/risk/sizing.py`:

```python
"""
Risk-based position sizing — pure, deterministic.

size = floor( (equity × risk_pct%) ÷ (stop_distance × point_value) )
clamped to [1, max_size]. Floor-to-1 means a stop so wide that one contract
already exceeds the budget still takes one contract (per design decision).

Pure function: no I/O, no state. Price/risk arithmetic is deterministic Python
(never delegated to a model) — see CLAUDE.md Rule 5.
"""
from __future__ import annotations

from decimal import Decimal


def risk_based_size(
    equity: Decimal,
    risk_pct: Decimal,        # percent units, e.g. Decimal("0.25") == 0.25%
    stop_distance: Decimal,   # price points between entry and stop, > 0
    point_value: Decimal,     # dollars per 1-point move per contract, > 0
    max_size: int,            # account max_contracts (hard cap)
) -> int:
    """Contracts to trade so dollar risk ≈ equity × risk_pct%, capped at max_size."""
    if stop_distance <= 0 or point_value <= 0:
        raise ValueError("stop_distance and point_value must be positive")
    budget = equity * (risk_pct / Decimal("100"))
    risk_per_contract = stop_distance * point_value
    raw = int(budget // risk_per_contract)   # Decimal floor division, then int
    return max(1, min(raw, max_size))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_sizing.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/risk/sizing.py tests/test_sizing.py
git commit -m "feat: pure risk_based_size() with tests"
```

---

## Task 2: BotConfig field + persistence

**Files:**
- Modify: `app/bot_config.py`

- [ ] **Step 1: Add the field to `BotConfig`**

In `app/bot_config.py`, the `BotConfig` model currently ends:

```python
    contracts: int = 1              # number of contracts per signal
    enabled_killzones: list[str] = Field(
        default_factory=lambda: ["london", "ny_am", "ny_pm"],
    )
    strategy: StrategyParams = Field(default_factory=StrategyParams)
```

Add `risk_per_trade_pct` after `contracts`:

```python
    contracts: int = 1              # number of contracts per signal
    risk_per_trade_pct: Decimal = Decimal("0.25")  # 0 = disabled (use fixed contracts); else % of equity risked per trade
    enabled_killzones: list[str] = Field(
        default_factory=lambda: ["london", "ny_am", "ny_pm"],
    )
    strategy: StrategyParams = Field(default_factory=StrategyParams)
```

- [ ] **Step 2: Serialize it in `save_bot_config`**

In `save_bot_config`, the `data` dict currently has:

```python
        "contracts": config.contracts,
        "enabled_killzones": config.enabled_killzones,
```

Add the field (use `_conv` since it's a Decimal):

```python
        "contracts": config.contracts,
        "risk_per_trade_pct": _conv(config.risk_per_trade_pct),
        "enabled_killzones": config.enabled_killzones,
```

- [ ] **Step 3: Verify round-trip**

Run:
```bash
.venv\Scripts\python.exe -c "from app.bot_config import BotConfig, save_bot_config, load_bot_config; from pathlib import Path; from decimal import Decimal; p=Path('_tmp_cfg.json'); c=BotConfig(risk_per_trade_pct=Decimal('0.5')); save_bot_config(c,p); print('loaded:', load_bot_config(p).risk_per_trade_pct); p.unlink()"
```
Expected: `loaded: 0.5`

- [ ] **Step 4: Commit**

```bash
git add app/bot_config.py
git commit -m "feat: risk_per_trade_pct field on BotConfig"
```

---

## Task 3: Engine sizing

**Files:**
- Modify: `app/execution/engine.py`
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Write the failing engine test**

In `tests/test_engine.py`, add this helper and tests at the end of the file:

```python
def _signal(entry: str, stop: str, target: str, side: str = "long") -> Signal:
    return Signal(
        instrument="MGC", side=side,
        entry=Decimal(entry), stop=Decimal(stop), target=Decimal(target),
        created_at=in_ny_am(0), killzone="NY AM", sweep_pattern="B_one_bar",
        sweep_extreme=Decimal(stop), fvg_low=None, fvg_high=None, rationale="test",
    )


def _engine_with_pct(pct: str) -> tuple[ExecutionEngine, RiskState]:
    rs = RiskState(config=fifty_k_combine())  # starting_balance 50000, max_contracts 30
    eng = ExecutionEngine(
        broker=PaperBroker(),
        risk_state=rs,
        runners=[make_runner()],
        contracts=4,
        risk_per_trade_pct=Decimal(pct),
    )
    return eng, rs


def test_entry_size_wide_stop_caps_at_one():
    """10.8pt stop at $50k / 0.25% ($125 budget, $108/contract) -> 1 contract."""
    eng, rs = _engine_with_pct("0.25")
    rs.mark_equity(Decimal("50000"), in_ny_am(0))
    assert eng._entry_size(_signal("4514.8", "4504.0", "4541.8")) == 1


def test_entry_size_normal_stop():
    """3.0pt stop -> $30/contract; floor(125/30)=4 contracts."""
    eng, rs = _engine_with_pct("0.25")
    rs.mark_equity(Decimal("50000"), in_ny_am(0))
    assert eng._entry_size(_signal("4500.0", "4497.0", "4509.0")) == 4


def test_entry_size_disabled_uses_fixed_contracts():
    """risk_per_trade_pct=0 -> fall back to fixed contracts (4)."""
    eng, rs = _engine_with_pct("0")
    rs.mark_equity(Decimal("50000"), in_ny_am(0))
    assert eng._entry_size(_signal("4514.8", "4504.0", "4541.8")) == 4


def test_entry_size_equity_fallback_before_first_tick():
    """Before any mark_equity tick, current_equity is 0; fall back to realized_balance."""
    eng, rs = _engine_with_pct("0.25")
    # no mark_equity call -> _current_equity == 0, realized_balance == 50000
    assert eng._entry_size(_signal("4500.0", "4497.0", "4509.0")) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine.py -k entry_size -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'risk_per_trade_pct'`

- [ ] **Step 3: Add imports to the engine**

In `app/execution/engine.py`, find the existing imports near the top of the file and add the two the engine needs for sizing. Add these lines alongside the other `app.` imports:

```python
from app.broker.topstepx import _point_value
from app.risk.sizing import risk_based_size
```

- [ ] **Step 4: Add the ctor parameter and store it**

In `ExecutionEngine.__init__`, the signature currently ends:

```python
        replay_mode: bool = False,
        contracts: int = 1,
        strategy_cfg: "StrategyParams | None" = None,
    ) -> None:
```

Add `risk_per_trade_pct`:

```python
        replay_mode: bool = False,
        contracts: int = 1,
        risk_per_trade_pct: Decimal = Decimal("0"),
        strategy_cfg: "StrategyParams | None" = None,
    ) -> None:
```

And where `self.contracts = contracts` is set, add the new attribute directly after it:

```python
        self.contracts = contracts  # contracts per signal; hot-applied via PATCH /api/config
        self.risk_per_trade_pct = risk_per_trade_pct  # 0 = use fixed contracts; else % equity risked; hot-applied
```

(Confirm `from decimal import Decimal` is already imported at the top of `engine.py`; the file uses Decimal elsewhere. If not present, add it.)

- [ ] **Step 5: Add the `_entry_size` method**

In `app/execution/engine.py`, add this method to `ExecutionEngine`, immediately before `_act_on_signal`:

```python
    def _entry_size(self, signal: Signal) -> int:
        """Contracts for this entry. Risk-based when risk_per_trade_pct > 0,
        else the fixed `contracts` count. Logs the decision (Rule 12)."""
        if not self.risk_per_trade_pct or self.risk_per_trade_pct <= 0:
            return self.contracts

        equity = self.risk_state.current_equity
        if equity <= 0:  # before the first mark-to-market tick of the session
            equity = self.risk_state.realized_balance
        stop_distance = abs(signal.entry - signal.stop)
        if stop_distance <= 0:
            return self.contracts  # degenerate signal; fall back rather than divide by zero

        pv = _point_value(signal.instrument)
        size = risk_based_size(
            equity, self.risk_per_trade_pct, stop_distance, pv,
            max_size=self.risk_state.config.max_contracts,
        )
        budget = equity * (self.risk_per_trade_pct / Decimal("100"))
        risk_per_contract = stop_distance * pv
        over = " (OVER-BUDGET floored to 1)" if risk_per_contract > budget else ""
        log.info(
            "Risk-sized: equity=%s budget=%s stop=%spt $/ct=%s -> size=%d%s",
            equity, budget, stop_distance, risk_per_contract, size, over,
        )
        return size
```

- [ ] **Step 6: Use it in `_act_on_signal`**

In `_act_on_signal`, the `ProposedOrder` is currently built with `size=self.contracts`:

```python
        order = ProposedOrder(
            instrument=signal.instrument,
            side=signal.side,
            size=self.contracts,
            entry=signal.entry,
            stop=signal.stop,
            target=signal.target,
            is_entry=True,
        )
```

Change `size=self.contracts` to `size=self._entry_size(signal)`:

```python
        order = ProposedOrder(
            instrument=signal.instrument,
            side=signal.side,
            size=self._entry_size(signal),
            entry=signal.entry,
            stop=signal.stop,
            target=signal.target,
            is_entry=True,
        )
```

- [ ] **Step 7: Run the sizing tests**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine.py -k entry_size -v`
Expected: all 4 `entry_size` tests PASS.

- [ ] **Step 8: Run the full engine + sizing suites**

Run: `.venv\Scripts\python.exe -m pytest tests/test_engine.py tests/test_sizing.py -q`
Expected: the pre-existing `test_signal_denied_when_already_at_max_contracts` failure may remain (it is one of the 5 known baseline failures); NO new failures, all `entry_size` and sizing tests pass.

- [ ] **Step 9: Commit**

```bash
git add app/execution/engine.py tests/test_engine.py
git commit -m "feat: engine computes risk-based entry size via _entry_size()"
```

---

## Task 4: Wire the field into the engine at startup

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Pass `risk_per_trade_pct` into `ExecutionEngine`**

In `app/main.py`, the `ExecutionEngine(...)` construction currently reads:

```python
    engine = ExecutionEngine(
        broker=broker,
        risk_state=risk_state,
        runners=runners,
        on_signal=...,
        on_order_placed=reconciler.notify_order_placed,
        on_pre_place=_make_pre_place(config_path=cfg.bot_config_path),
        contracts=bot_cfg.contracts,
        strategy_cfg=bot_cfg.strategy,
    )
```

Add `risk_per_trade_pct=bot_cfg.risk_per_trade_pct,` directly after the `contracts=` line:

```python
        contracts=bot_cfg.contracts,
        risk_per_trade_pct=bot_cfg.risk_per_trade_pct,
        strategy_cfg=bot_cfg.strategy,
    )
```

(The exact other ctor args may differ slightly in your tree; only add the one new line after `contracts=bot_cfg.contracts,`.)

- [ ] **Step 2: Verify import + startup log line wiring**

Run: `.venv\Scripts\python.exe -c "import app.main"`
Expected: no output, exit code 0.

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat: wire risk_per_trade_pct from BotConfig into engine"
```

---

## Task 5: API — expose and hot-apply

**Files:**
- Modify: `app/api/server.py`

- [ ] **Step 1: Return the field in `GET /api/config`**

In `get_config()`, the returned dict includes `"contracts": cfg.contracts,`. Add the new field right after it. It is a Decimal, so convert to float for JSON:

```python
            "contracts": cfg.contracts,
            "risk_per_trade_pct": float(cfg.risk_per_trade_pct),
            "enabled_killzones": cfg.enabled_killzones,
```

- [ ] **Step 2: Hot-apply in `PATCH /api/config`**

In `patch_config()`, after `save_bot_config(body, _bot_config_path)`, the engine hot-apply block reads:

```python
        if _engine is not None:
            _engine.contracts = body.contracts
            _engine.strategy_cfg = body.strategy
```

Add the risk field:

```python
        if _engine is not None:
            _engine.contracts = body.contracts
            _engine.risk_per_trade_pct = body.risk_per_trade_pct
            _engine.strategy_cfg = body.strategy
```

- [ ] **Step 3: Return the field in the PATCH response**

The `patch_config` response dict also lists `"contracts": body.contracts,`. Add after it:

```python
            "contracts": body.contracts,
            "risk_per_trade_pct": float(body.risk_per_trade_pct),
            "enabled_killzones": body.enabled_killzones,
```

- [ ] **Step 4: Verify**

Run: `.venv\Scripts\python.exe -c "import app.api.server"`
Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add app/api/server.py
git commit -m "feat: GET/PATCH /api/config expose + hot-apply risk_per_trade_pct"
```

---

## Task 6: Frontend — type + form field

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/ConfigPanel.tsx`

- [ ] **Step 1: Add the type**

In `frontend/src/types.ts`, the config interface has `contracts: number`. Add the new field directly after it:

```ts
  contracts: number
  risk_per_trade_pct: number
  mode?: string
```

- [ ] **Step 2: Initialize the form value**

In `frontend/src/components/ConfigPanel.tsx`, the form-init object has `contracts: String(config.contracts ?? 1),`. Add after it:

```tsx
      contracts:            String(config.contracts ?? 1),
      risk_per_trade_pct:   String(config.risk_per_trade_pct ?? 0.25),
```

- [ ] **Step 3: Include it in the submit payload**

In the submit/build object (where `contracts: parseInt(form.contracts) || 1,` appears), add after it:

```tsx
      contracts:            parseInt(form.contracts) || 1,
      risk_per_trade_pct:   parseFloat(form.risk_per_trade_pct) || 0,
```

- [ ] **Step 4: Render the field**

In `ConfigPanel.tsx`, immediately after the closing `</div>` of the "Contracts Per Signal" block (the `<div>` that ends just before `</>` near line 435), add a sibling field:

```tsx
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        Risk % Per Trade (0 = off)
                      </label>
                      <input
                        type="number"
                        min={0}
                        max={5}
                        step={0.05}
                        value={form.risk_per_trade_pct ?? '0.25'}
                        onChange={e => set('risk_per_trade_pct', e.target.value)}
                        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Percent of account equity risked per trade. Size = budget ÷ stop distance, capped at max contracts. 0 disables (uses fixed contracts). Hot-applied — no restart.
                      </p>
                    </div>
```

- [ ] **Step 5: Build the frontend**

Run: `cd frontend; npm run build`
Expected: build succeeds, no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/ConfigPanel.tsx
git commit -m "feat: risk_per_trade_pct field in config panel"
```

---

## Manual Verification (after merge, requires bot restart)

The bot is running (PID 15824); the engine picks up the field on next launch (or hot-applies via PATCH without restart).

- [ ] `curl localhost:8000/api/config` shows `risk_per_trade_pct`.
- [ ] PATCH it to `0.25` and confirm the running engine reflects it (next risk-sized entry logs `Risk-sized: ...`).
- [ ] At `localhost:5173`, the "Risk % Per Trade" field renders, no console/TS errors.
- [ ] In a paper replay, confirm a wide-stop signal logs `size=1 (OVER-BUDGET floored to 1)` and a tight-stop signal sizes up toward max_contracts.

---

## Self-Review

**Spec coverage:**
- [x] % of equity basis, default 0.25, percent units → Task 1 (`risk_pct/100`), Task 2 (default `0.25`)
- [x] Wide-stop floor-to-1 → Task 1 `max(1, ...)`, tested `test_very_wide_stop_floors_to_one_over_budget`
- [x] Cap at max_contracts → Task 1 `min(raw, max_size)`, engine passes `config.max_contracts` (Task 3 Step 5)
- [x] Disabled sentinel (0 → fixed contracts) → Task 3 `_entry_size` guard, tested
- [x] Reuse `topstepx._point_value` → Task 3 Step 3 import
- [x] Equity fallback before first tick → Task 3 `_entry_size`, tested `test_entry_size_equity_fallback_before_first_tick`
- [x] Logging (Rule 12) with OVER-BUDGET suffix → Task 3 Step 5
- [x] Config wiring: BotConfig (T2), main (T4), engine ctor (T3), GET+PATCH (T5), types+ConfigPanel (T6)
- [x] Tests (Rule 9): pure fn (T1, 5 tests), engine-level wide/normal/disabled/fallback (T3, 4 tests)

**Placeholder scan:** none — every code step shows complete code and exact commands.

**Type consistency:**
- `risk_based_size(equity, risk_pct, stop_distance, point_value, max_size)` — identical signature in `app/risk/sizing.py` (T1), `tests/test_sizing.py` (T1), and the engine call (T3 Step 5).
- `risk_per_trade_pct` is `Decimal` everywhere in Python (BotConfig, engine ctor, `_entry_size`), serialized via `_conv`/`float()` at the JSON boundary (T2, T5), and `number` in TS (T6) — boundary conversions are explicit.
- `_entry_size(self, signal: Signal) -> int` defined in T3 Step 5, called in T3 Step 6.
- `_point_value(instrument)` is the existing function in `app/broker/topstepx.py` (returns `Decimal`); engine imports it (T3 Step 3).
```
