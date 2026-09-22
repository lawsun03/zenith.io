# Feed-Dead Watchdog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Detect when the live bar feed goes silent during expected-bar hours and alert (Discord + email) once on death and once on recovery, with session-aware suppression and a dashboard status indicator.

**Architecture:** A wall-clock `_watchdog_clock` task in `ExecutionEngine` (mirrors `_flatten_clock`) checks the gap since the last bar each 30s. `_handle_bar` stamps `_last_bar_at`. A pure `bars_expected(ts)` gate (in `app/risk/flatten.py`) suppresses the daily break/weekend/early-close. A per-tick `on_feed_status(payload)` callback — fired EVERY tick with `transition ∈ {None,"dead","recovered"}` — is wired in `main.py` to (a) `journal.publish_feed_watchdog(payload)` for the UI every tick, and (b) Discord+email only on a transition. The dashboard WebSocket carries a `{kind:"feed_watchdog"}` message rendered in `StrategyDebug.tsx`.

**Spec:** `docs/superpowers/specs/2026-06-16-feed-dead-watchdog-design.md`

**Architecture reconciliations vs spec** (the spec idealized "engine pushes SSE"): the live stream is a **WebSocket** via the `Journal` (`app/api/journal.py`), not raw SSE; the engine has no journal reference, so the UI push is routed through the same `on_feed_status` callback (fired every tick, not only on transitions) that main.py owns. Functionally identical to the spec; cleaner wiring.

**Run all Python via** `.venv/Scripts/python.exe`.

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `app/risk/flatten.py` | `bars_expected(ts) -> bool` session gate (reuses `_EARLY_CLOSE_DATES`, `_SESSION_OPEN`, `trading_day_ct`, `CT`) |
| Modify | `app/execution/engine.py` | `_last_bar_at` stamp; `feed_watchdog_enabled` + `on_feed_status` params; `_watchdog_step(now)` state machine; `_watchdog_clock` task lifecycle |
| Modify | `app/notifications/discord.py` | `send_alert(title, message)` generic webhook post |
| Modify | `app/api/journal.py` | `publish_feed_watchdog(payload: dict)` broadcasting `{kind:"feed_watchdog"}` |
| Modify | `app/main.py` | construct the `on_feed_status` handler (journal publish + notifiers); pass it + `feed_watchdog_enabled` to `ExecutionEngine` |
| Modify | `app/bot_config.py` | `feed_watchdog_enabled: bool = True` + serialization |
| Modify | `app/api/server.py` | return + hot-apply `feed_watchdog_enabled` in GET/PATCH `/api/config` |
| Modify | `frontend/src/hooks/useStream.ts` | handle `kind:"feed_watchdog"` |
| Modify | `frontend/src/components/StrategyDebug.tsx` | render feed-watchdog status |
| Modify | `frontend/src/types.ts` (+ ConfigPanel) | `feed_watchdog_enabled` field + toggle |
| Create | `tests/test_feed_watchdog.py` | `bars_expected`, threshold, state machine, callback decoupling, engine integration |

---

## Task 1: `bars_expected` session gate (pure, TDD)

**Files:** Modify `app/risk/flatten.py`; Test `tests/test_feed_watchdog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_feed_watchdog.py
from __future__ import annotations
from datetime import datetime, timezone
from app.risk.flatten import bars_expected


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class TestBarsExpected:
    # 2026-01-15 is a Thursday, CST (UTC-6): 10:00 CT == 16:00 UTC (active)
    def test_active_midsession(self):
        assert bars_expected(_utc(2026, 1, 15, 16, 0))      # 10:00 CT Thu

    def test_daily_maintenance_break(self):
        # 16:30 CT == 22:30 UTC — inside 16:00-17:00 CT break
        assert not bars_expected(_utc(2026, 1, 15, 22, 30))

    def test_reopen_after_break(self):
        # 17:00 CT == 23:00 UTC — session reopens
        assert bars_expected(_utc(2026, 1, 15, 23, 0))

    def test_saturday_closed(self):
        # 2026-01-17 is Saturday
        assert not bars_expected(_utc(2026, 1, 17, 18, 0))

    def test_friday_evening_closed(self):
        # Fri 2026-01-16 16:30 CT == 22:30 UTC (after Fri 16:00 close)
        assert not bars_expected(_utc(2026, 1, 16, 22, 30))

    def test_sunday_reopen(self):
        # Sun 2026-01-18 17:30 CT == 23:30 UTC (after Sun 17:00 open)
        assert bars_expected(_utc(2026, 1, 18, 23, 30))

    def test_early_close_day_afternoon_closed(self):
        # 2026-07-03 early close (noon CT). 13:00 CT CDT == 18:00 UTC — closed
        assert not bars_expected(_utc(2026, 7, 3, 18, 0))

    def test_early_close_day_morning_open(self):
        # 2026-07-03 10:00 CT CDT == 15:00 UTC — still open before noon
        assert bars_expected(_utc(2026, 7, 3, 15, 0))
```

- [ ] **Step 2: Run — verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_watchdog.py::TestBarsExpected -q`
Expected: FAIL — `ImportError: cannot import name 'bars_expected'`.

- [ ] **Step 3: Implement**

Add to `app/risk/flatten.py` (after `is_early_close_day`):

```python
from datetime import time as _time  # if not already imported; `time` already imported at top

_DAILY_BREAK_START = time(16, 0)   # 16:00 CT maintenance break begins
# _SESSION_OPEN (17:00 CT) already defined = break end / new session
_EARLY_CLOSE_TIME = time(12, 0)    # noon CT close on early-close days


def bars_expected(ts: datetime) -> bool:
    """True when live bars should be arriving for CME equity/metal futures.

    False during: the 16:00-17:00 CT daily maintenance break; the weekend gap
    (Fri 16:00 CT -> Sun 17:00 CT); and after the noon close on early-close days.
    DST-correct via CT. Used by the feed-dead watchdog to suppress false alarms.
    """
    ct = ts.astimezone(CT)
    t = ct.time()
    wd = ct.weekday()  # Mon=0 .. Sun=6

    # Daily maintenance break 16:00-17:00 CT (every trading day)
    if _DAILY_BREAK_START <= t < _SESSION_OPEN:
        return False

    # Weekend gap: Fri 16:00 CT -> Sun 17:00 CT
    if wd == 5:                                  # Saturday: closed all day
        return False
    if wd == 4 and t >= _DAILY_BREAK_START:      # Friday after 16:00 CT
        return False
    if wd == 6 and t < _SESSION_OPEN:            # Sunday before 17:00 CT
        return False

    # Early-close days: no bars from noon CT close to the 17:00 reopen
    if is_early_close_day(ts) and _EARLY_CLOSE_TIME <= t < _SESSION_OPEN:
        return False

    return True
```

(`time` is already imported in flatten.py; `CT`, `_SESSION_OPEN`, `is_early_close_day`, `_EARLY_CLOSE_DATES` already exist. Verify the `time` import and reuse it — do not re-import under an alias if already present.)

- [ ] **Step 4: Run — verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_watchdog.py::TestBarsExpected -q`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add app/risk/flatten.py tests/test_feed_watchdog.py
git commit -m "feat(watchdog): bars_expected session gate (break/weekend/early-close)"
```

---

## Task 2: Engine watchdog state machine (`_watchdog_step`, TDD)

**Files:** Modify `app/execution/engine.py`; Test `tests/test_feed_watchdog.py`

- [ ] **Step 1: Write the failing test**

```python
class TestWatchdogStep:
    def _engine(self, tf="5min"):
        from types import SimpleNamespace
        from decimal import Decimal
        from app.sim.paper import PaperBroker
        from app.execution.engine import ExecutionEngine
        from app.risk.config import fifty_k_combine
        from app.risk.state import RiskState
        runner = SimpleNamespace(instrument="MNQ", timeframe=tf)
        eng = ExecutionEngine(
            broker=PaperBroker(), risk_state=RiskState(config=fifty_k_combine()),
            runners=[runner], replay_mode=True, feed_watchdog_enabled=True)
        return eng

    def test_threshold_5min(self):
        eng = self._engine("5min")
        assert eng._watchdog_threshold_s() == 900   # max(3*300, 600)

    def test_threshold_1min_floor(self):
        eng = self._engine("1min")
        assert eng._watchdog_threshold_s() == 600   # max(3*60, 600)

    def test_live_to_dead_when_expected(self):
        eng = self._engine()
        eng._last_bar_at = _utc(2026, 1, 15, 16, 0)          # 10:00 CT
        out = eng._watchdog_step(_utc(2026, 1, 15, 16, 20))  # 20 min later, expected
        assert out["status"] == "dead" and out["transition"] == "dead"
        assert eng._feed_status == "DEAD"

    def test_dead_does_not_realert(self):
        eng = self._engine()
        eng._last_bar_at = _utc(2026, 1, 15, 16, 0)
        eng._watchdog_step(_utc(2026, 1, 15, 16, 20))        # -> dead (transition)
        out = eng._watchdog_step(_utc(2026, 1, 15, 16, 25))  # still dead
        assert out["transition"] is None and eng._feed_status == "DEAD"

    def test_recovery_on_bar(self):
        eng = self._engine()
        eng._last_bar_at = _utc(2026, 1, 15, 16, 0)
        eng._watchdog_step(_utc(2026, 1, 15, 16, 20))        # dead
        eng._last_bar_at = _utc(2026, 1, 15, 16, 26)         # a bar arrived
        out = eng._watchdog_step(_utc(2026, 1, 15, 16, 26))
        assert out["transition"] == "recovered" and eng._feed_status == "LIVE"

    def test_quiet_suppressed_in_break(self):
        eng = self._engine()
        eng._last_bar_at = _utc(2026, 1, 15, 22, 0)          # 16:00 CT (break start)
        out = eng._watchdog_step(_utc(2026, 1, 15, 22, 40))  # 16:40 CT, big gap but break
        assert out["status"] == "quiet" and out["transition"] is None
        assert eng._feed_status == "QUIET_EXPECTED"

    def test_not_armed_never_alerts(self):
        eng = self._engine()
        eng._last_bar_at = None
        out = eng._watchdog_step(_utc(2026, 1, 15, 16, 20))
        assert out["transition"] is None and eng._feed_status == "LIVE"
```

- [ ] **Step 2: Run — verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_watchdog.py::TestWatchdogStep -q`
Expected: FAIL — `TypeError: ... unexpected keyword 'feed_watchdog_enabled'` (or `AttributeError` on `_watchdog_step`).

- [ ] **Step 3: Implement**

In `app/execution/engine.py`:

(a) Add params to `__init__` (after `flatten_wallclock_enabled: bool = True,` ~line 431):
```python
        feed_watchdog_enabled: bool = True,
        on_feed_status: "Callable[[dict], Awaitable[None]] | None" = None,
```
(b) In `__init__` body (near `self.flatten_wallclock_enabled = ...` ~line 458) add:
```python
        self.feed_watchdog_enabled = feed_watchdog_enabled
        self.on_feed_status = on_feed_status
        self._last_bar_at: datetime | None = None
        self._feed_status: str = "LIVE"   # LIVE | QUIET_EXPECTED | DEAD
        self._watchdog_task: asyncio.Task | None = None
```
(c) In `_handle_bar` (line 637), as the FIRST statement of the method body:
```python
        self._last_bar_at = datetime.now(timezone.utc)
```
(d) Add the threshold + step methods (near `_flatten_clock`):
```python
    def _watchdog_threshold_s(self) -> int:
        tfs = [_tf_seconds(r.timeframe) for r in self.runners.values()]
        base = max(tfs) if tfs else 300
        return max(3 * base, 600)

    def _watchdog_step(self, now: datetime) -> dict:
        """Pure-ish state transition for the feed watchdog. Updates
        self._feed_status and returns a payload dict with a one-shot
        `transition` ∈ {None,'dead','recovered'} for the caller to act on."""
        from app.risk.flatten import bars_expected
        threshold = self._watchdog_threshold_s()
        expected = bars_expected(now)
        gap = None if self._last_bar_at is None else (now - self._last_bar_at).total_seconds()
        transition = None
        prev = self._feed_status

        if self._last_bar_at is None:
            self._feed_status = "LIVE"          # not armed yet
        elif prev == "DEAD":
            if gap is not None and gap <= threshold:   # a fresh bar arrived
                self._feed_status = "LIVE"
                transition = "recovered"
            # else stay DEAD (no re-alert)
        else:  # LIVE or QUIET_EXPECTED
            if not expected:
                self._feed_status = "QUIET_EXPECTED"
            elif gap is not None and gap > threshold:
                self._feed_status = "DEAD"
                transition = "dead"
            else:
                self._feed_status = "LIVE"

        status_str = {"LIVE": "live", "QUIET_EXPECTED": "quiet", "DEAD": "dead"}[self._feed_status]
        return {
            "kind": "feed_watchdog",
            "status": status_str,
            "transition": transition,
            "last_bar_at": self._last_bar_at.isoformat() if self._last_bar_at else None,
            "seconds_since": round(gap, 1) if gap is not None else None,
            "threshold_s": threshold,
            "expected": expected,
        }
```

- [ ] **Step 4: Run — verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_watchdog.py::TestWatchdogStep -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/execution/engine.py tests/test_feed_watchdog.py
git commit -m "feat(watchdog): engine feed-watchdog state machine + threshold"
```

---

## Task 3: Watchdog clock task + lifecycle

**Files:** Modify `app/execution/engine.py`; Test `tests/test_feed_watchdog.py`

- [ ] **Step 1: Write the failing test**

```python
def test_watchdog_disabled_no_task():
    import asyncio
    from decimal import Decimal
    from app.sim.paper import PaperBroker
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState
    broker = PaperBroker()
    eng = ExecutionEngine(broker=broker, risk_state=RiskState(config=fifty_k_combine()),
                          runners=[], replay_mode=False, feed_watchdog_enabled=False)
    async def go():
        await broker.connect(); await eng.start()
        assert eng._watchdog_task is None
        await eng.stop()
    asyncio.run(go())


def test_watchdog_clock_invokes_callback(monkeypatch):
    """The clock tick must call on_feed_status with the step payload."""
    import asyncio
    from app.sim.paper import PaperBroker
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState
    seen = []
    async def cb(payload): seen.append(payload)
    broker = PaperBroker()
    eng = ExecutionEngine(broker=broker, risk_state=RiskState(config=fifty_k_combine()),
                          runners=[], replay_mode=True, feed_watchdog_enabled=True,
                          on_feed_status=cb)
    async def go():
        await broker.connect(); await eng.start()
        await eng._watchdog_tick()      # one tick directly, no sleep
        await eng.stop()
    asyncio.run(go())
    assert seen and seen[0]["kind"] == "feed_watchdog"
```

- [ ] **Step 2: Run — verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_watchdog.py -k "watchdog_disabled or clock_invokes" -q`
Expected: FAIL (`_watchdog_task`/`_watchdog_tick` missing or task started when disabled).

- [ ] **Step 3: Implement**

In `app/execution/engine.py`:

(a) Extract the tick body so tests can call it without sleeping:
```python
    async def _watchdog_tick(self) -> None:
        payload = self._watchdog_step(datetime.now(timezone.utc))
        if payload["transition"] == "dead":
            log.warning("FEED DEAD: no bar for %ss (threshold %ss), last bar %s",
                        payload["seconds_since"], payload["threshold_s"], payload["last_bar_at"])
        elif payload["transition"] == "recovered":
            log.info("FEED RECOVERED: bar arrived after going dark")
        if self.on_feed_status is not None:
            try:
                await self.on_feed_status(payload)
            except Exception:
                log.exception("on_feed_status callback raised")

    async def _watchdog_clock(self) -> None:
        """Live feed-dead detector: check every 30s of wall time."""
        while True:
            await asyncio.sleep(30)
            try:
                await self._watchdog_tick()
            except Exception:
                log.exception("watchdog clock check failed")
```
(b) In `start()` (next to the `_flatten_task` creation ~line 543):
```python
            if self._watchdog_task is None and self.feed_watchdog_enabled:
                self._watchdog_task = asyncio.create_task(self._watchdog_clock())
            elif not self.feed_watchdog_enabled:
                log.info("Feed-dead watchdog disabled (feed_watchdog_enabled=False)")
```
(c) In `stop()` (next to `_flatten_task` cancellation ~line 561), mirror the cancel/await/None block for `self._watchdog_task`.

- [ ] **Step 4: Run — verify pass + engine suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_watchdog.py tests/test_engine.py -q`
Expected: new tests PASS; pre-existing engine baseline unchanged (the known `test_signal_denied_when_already_at_max_contracts` baseline may remain — no NEW failures).

- [ ] **Step 5: Commit**

```bash
git add app/execution/engine.py tests/test_feed_watchdog.py
git commit -m "feat(watchdog): 30s clock task + lifecycle (mirrors flatten clock)"
```

---

## Task 4: Discord `send_alert` + Journal `publish_feed_watchdog`

**Files:** Modify `app/notifications/discord.py`, `app/api/journal.py`; Test `tests/test_feed_watchdog.py`

- [ ] **Step 1: Write the failing test**

```python
def test_discord_send_alert_noop_when_disabled():
    import asyncio
    from app.notifications.discord import DiscordNotifier
    d = DiscordNotifier(webhook_url="")   # disabled
    assert d.enabled is False
    assert asyncio.run(d.send_alert("Feed dead", "no bars 20m")) is False
```

- [ ] **Step 2: Run — verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_watchdog.py::test_discord_send_alert_noop_when_disabled -q`
Expected: FAIL — `AttributeError: 'DiscordNotifier' object has no attribute 'send_alert'`. (If the `DiscordNotifier()` ctor signature differs, inspect `app/notifications/discord.py:80` and adapt the disabled-construction in the test — do not invent kwargs.)

- [ ] **Step 3: Implement**

(a) In `app/notifications/discord.py`, add a generic alert mirroring `send_startup`'s webhook POST (reuse the same internal `_post`/embed helper the class already uses — inspect `send_startup` body and copy its POST mechanism):
```python
    async def send_alert(self, title: str, message: str) -> bool:
        """Generic operational alert (e.g. feed dead/recovered). No-op + False
        when disabled. Returns True on a successful post."""
        if not self.enabled:
            return False
        return await self._post_embed(title=title, description=message)  # use the class's existing post helper
```
NOTE: use whatever the class's existing low-level post is (it has one for `send_startup`/`send_signal`). If there is no reusable helper, factor the POST out of `send_startup` into `_post_embed(title, description)` and have both call it — behavior-preserving.

(b) In `app/api/journal.py`, add next to `publish_strategy_state` (line 297), mirroring `publish_bar`'s broadcast (inspect `publish_bar` at :281 for the exact client-broadcast call):
```python
    def publish_feed_watchdog(self, payload: dict) -> None:
        """Broadcast feed-watchdog status to dashboard WS clients."""
        self._broadcast({"kind": "feed_watchdog", "payload": payload})  # match publish_bar's broadcast call
```

- [ ] **Step 4: Run — verify pass + notifier/journal suites**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_watchdog.py -q && .venv/Scripts/python.exe -m pytest tests/ -k "discord or journal" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/notifications/discord.py app/api/journal.py tests/test_feed_watchdog.py
git commit -m "feat(watchdog): Discord send_alert + Journal publish_feed_watchdog"
```

---

## Task 5: main.py wiring — `on_feed_status` handler

**Files:** Modify `app/main.py`

- [ ] **Step 1: Build the handler and pass it to the engine**

In `app/main.py`, before the `engine = ExecutionEngine(...)` construction (~line 1308), add (capturing `journal`, `notifier`, `discord` already in scope):
```python
    async def _on_feed_status(payload: dict) -> None:
        # UI: push every tick (independent of bars) so the panel reflects DEAD.
        try:
            journal.publish_feed_watchdog(payload)
        except Exception:
            log.exception("publish_feed_watchdog failed")
        # Alerts: only on a transition edge.
        t = payload.get("transition")
        if t == "dead":
            title = "🔴 FEED DEAD"
            msg = (f"No bars for {payload['seconds_since']}s "
                   f"(threshold {payload['threshold_s']}s). Last bar {payload['last_bar_at']}.")
        elif t == "recovered":
            title = "🟢 FEED RECOVERED"
            msg = f"Bars resumed. Last bar {payload['last_bar_at']}."
        else:
            return
        if discord.enabled:
            await discord.send_alert(title, msg)
        if notifier.enabled:
            await notifier.send(title, msg)
```
Then add to the `ExecutionEngine(...)` kwargs:
```python
        feed_watchdog_enabled=bot_cfg.feed_watchdog_enabled,
        on_feed_status=_on_feed_status,
```

- [ ] **Step 2: Verify import + construction**

Run: `.venv/Scripts/python.exe -c "import app.main; print('main imports OK')"`
Expected: `main imports OK` (this also requires Task 6's config field to exist — do Task 6 first if `bot_cfg.feed_watchdog_enabled` errors, or temporarily use `getattr(bot_cfg,'feed_watchdog_enabled',True)`; switch to the attribute after Task 6).

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat(watchdog): wire on_feed_status to journal + Discord/email"
```

---

## Task 6: Config plumbing (`feed_watchdog_enabled`)

**Files:** Modify `app/bot_config.py`, `app/api/server.py`; Test `tests/test_feed_watchdog.py`

- [ ] **Step 1: Write the failing test**

```python
def test_config_default_and_roundtrip():
    from app.bot_config import BotConfig
    c = BotConfig()
    assert c.feed_watchdog_enabled is True
```

- [ ] **Step 2: Run — verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_watchdog.py::test_config_default_and_roundtrip -q`
Expected: FAIL — `AttributeError`.

- [ ] **Step 3: Implement**

(a) `app/bot_config.py`: add field near `flatten_wallclock_enabled` (search it):
```python
    feed_watchdog_enabled: bool = True
```
and add `"feed_watchdog_enabled": config.feed_watchdog_enabled,` to the serialization dict (the same dict that has `flatten_time_ct` ~line 446 / `forming_bar_entries` ~line 452).

(b) `app/api/server.py`: in the GET `/api/config` response dict (the one with `"forming_bar_entries": cfg.forming_bar_entries,` ~line 507) add `"feed_watchdog_enabled": cfg.feed_watchdog_enabled,`. In the PATCH body-apply block (where `_engine.forming_bar_entries = body.forming_bar_entries` ~line 539) add a hot-apply that starts/stops the task:
```python
            if body.feed_watchdog_enabled is not None:
                _engine.feed_watchdog_enabled = body.feed_watchdog_enabled
                # hot-apply: start/stop the task to match (mirror flatten toggle pattern)
                if body.feed_watchdog_enabled and _engine._watchdog_task is None:
                    _engine._watchdog_task = asyncio.create_task(_engine._watchdog_clock())
                elif not body.feed_watchdog_enabled and _engine._watchdog_task is not None:
                    _engine._watchdog_task.cancel()
                    _engine._watchdog_task = None
```
Also add `feed_watchdog_enabled` to the PATCH request model + the persisted-config write block alongside `forming_bar_entries` (inspect the `ConfigPatch`/body schema and the save block in the same handler).

- [ ] **Step 4: Run — verify pass + config/api suites**

Run: `.venv/Scripts/python.exe -m pytest tests/test_feed_watchdog.py tests/ -k "config or api" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py app/api/server.py tests/test_feed_watchdog.py
git commit -m "feat(watchdog): feed_watchdog_enabled config + hot-apply"
```

---

## Task 7: Frontend — render status + config toggle

**Files:** Modify `frontend/src/hooks/useStream.ts`, `frontend/src/components/StrategyDebug.tsx`, `frontend/src/types.ts` (+ ConfigPanel)

- [ ] **Step 1: useStream — handle the new message kind**

In `frontend/src/hooks/useStream.ts` `ws.onmessage` (the `if (msg.kind === ...)` chain ~line 60), add:
```ts
        if (msg.kind === 'feed_watchdog') {
          setFeedWatchdog(msg.payload)
          return
        }
```
Add `feedWatchdog` to the hook's `useState` + return value (type: `{status:string; seconds_since:number|null; threshold_s:number; last_bar_at:string|null; expected:boolean} | null`).

- [ ] **Step 2: StrategyDebug — render it**

In `frontend/src/components/StrategyDebug.tsx`, add a row consuming `feedWatchdog`:
```tsx
{feedWatchdog && (
  <div className="flex justify-between">
    <span className="text-dim">feed</span>
    <span className={feedWatchdog.status === 'dead' ? 'text-red-500'
        : feedWatchdog.status === 'quiet' ? 'text-dim' : 'text-ink'}>
      {feedWatchdog.status}
      {feedWatchdog.seconds_since != null ? ` · ${Math.round(feedWatchdog.seconds_since)}s` : ''}
    </span>
  </div>
)}
```
Pass `feedWatchdog` from the parent (App.tsx) where `useStream` is consumed, matching how `strategyState` is threaded into StrategyDebug. (Use only Tailwind classes already in the codebase; `text-dim`/`text-ink` are the project tokens. `text-red-500` is acceptable for the alert state — confirm it renders, else use an existing red token.)

- [ ] **Step 3: types.ts + ConfigPanel toggle**

Add `feed_watchdog_enabled: boolean` to the config type in `frontend/src/types.ts`, and a checkbox/toggle in `ConfigPanel` next to the `forming_bar_entries` ("Entry Confirmation") control, labeled "Feed-dead watchdog".

- [ ] **Step 4: Build clean (no TS errors)**

Run (from `frontend/`): `npm run build`
Expected: clean build, no TypeScript errors. (Per memory: a build is required for the bot's served static files to update.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/
git commit -m "feat(watchdog): dashboard feed status + config toggle"
```

---

## Task 8: Full regression + acceptance

**Files:** none (verification)

- [ ] **Step 1: Full Python suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass (baseline ~936 + new watchdog tests; no NEW failures). This is the load-bearing gate — Tasks 2-3, 5-6 touch the live `ExecutionEngine` and config hot-apply.

- [ ] **Step 2: Acceptance (paper/live run, Rule 4)**

Start the bot. Confirm: startup log shows the watchdog armed (or disabled line). With the dashboard open, observe the StrategyDebug `feed` row shows `live` during the session. To exercise death without waiting for a real outage, temporarily stop the broker bar stream (or run in a window where you can simulate a gap) and confirm: the panel flips to `dead` after the threshold, a Discord + email alert arrives once, and on resume it flips to `live` with one recovery message. During a real maintenance break, confirm it shows `quiet` and sends nothing.

- [ ] **Step 3: Final commit**

```bash
git add -A && git commit -m "test(watchdog): full regression green + acceptance notes"
```

---

## Self-Review

**Spec coverage:** session gate (Task 1) → spec §"Session gate"; state machine + threshold (Task 2) → §"State machine"; clock/lifecycle (Task 3) → §"Architecture.2"; alert channels + decoupled callback (Tasks 4-5) → §"Architecture.3" + decision 3; observability via journal/WS + panel (Tasks 4,7) → §"Observability" (reconciled: WS not SSE, push via callback→journal); config + hot-apply (Task 6) → §"Config"; error handling (try/except in tick + callback) → §"Error handling"; tests (Tasks 1-3,6) → §"Testing".

**Reconciliations flagged:** (1) live transport is WebSocket via `Journal`, not raw SSE; (2) the engine pushes UI state through the `on_feed_status` callback every tick (engine has no journal ref), with `transition` distinguishing alert edges — functionally equivalent to the spec's "push every tick + alert on transition."

**Verify-points (not placeholders):** `DiscordNotifier` ctor/disabled-construction (Task 4), the class's existing webhook POST helper to reuse (Task 4), `journal._broadcast` exact call shape from `publish_bar` (Task 4), the PATCH `ConfigPatch` schema + save block (Task 6), how `strategyState` is threaded into StrategyDebug from App.tsx (Task 7). Each names the file/anchor to inspect.

**Type consistency:** payload dict keys (`kind/status/transition/last_bar_at/seconds_since/threshold_s/expected`) are identical in `_watchdog_step` (Task 2), the handler (Task 5), and the frontend type (Task 7). `_feed_status` ∈ {LIVE,QUIET_EXPECTED,DEAD} internal; `status` ∈ {live,quiet,dead} on the wire.

**Caution:** Tasks 2,3,5,6 modify the live `ExecutionEngine` + config hot-apply (real-money process). The watchdog is pure observation/notification — it must never raise into the trading loop (try/except in tick + callback) and never gate trades. Task 8 Step 1 full-suite green is the merge gate.
