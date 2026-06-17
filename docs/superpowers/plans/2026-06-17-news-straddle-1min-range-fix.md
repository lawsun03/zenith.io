# News Straddle 1-Min Range Fix + Skip Alerting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live news-straddle scheduler build its pre-release range from real 1-minute bars (so it actually arms the OCO), and never skip silently — with an early warning ≥5 min before the release, a final alert if it still can't arm, and a confirmation when it does.

**Architecture:** Replace the scheduler's 5-min `broker.on_bar` buffer with an on-demand `get_historical_bars(timeframe="1min", …)` fetch at arm time. Add a pre-flight readiness check (with retry) at `release−preflight_lead`. Route all outcomes through an injected `alert_fn` that fans out to Discord + the dashboard journal, exactly like the existing feed-watchdog path. The validated arm timing (`release−120s`, 15-min range) is unchanged.

**Tech Stack:** Python 3 / asyncio, pytest. Files in `app/notifications/`, `app/strategy/`, `app/bot_config.py`, `app/api/journal.py`, `app/main.py`.

**Spec:** `docs/superpowers/specs/2026-06-17-news-straddle-1min-range-fix-design.md`

**Working dir:** this worktree, `C:\Users\Lawrence\Documents\zenith-fix-straddle`, branch `fix/news-straddle-1min-range`. Run pytest with `.venv\Scripts\python.exe -m pytest` (the venv lives in the main checkout; from this worktree use `C:/Users/Lawrence/Documents/topstep-trader-bot/topstep-bot/.venv/Scripts/python.exe -m pytest`).

---

## File Structure

- **Modify `app/bot_config.py`** — add two `StrategyParams` fields: `news_straddle_preflight_lead_seconds`, `news_straddle_retry_interval_seconds`.
- **Modify `app/api/journal.py`** — add `publish_straddle_event(payload)` (mirrors `publish_feed_watchdog`).
- **Modify `app/notifications/news_straddle_scheduler.py`** — core: `event_type` + `alert_fn` + preflight/retry config in `__init__`; drop `_buffer`/`on_bar`/`_compute_range`; add `_fetch_range`, `_is_ready`, `_preflight`, rewrite `_arm_event` (fetch-based + alerts), rewrite `_run` (preflight→retry→arm), extend `state()`.
- **Modify `app/strategy/news_straddle.py`** — `build_news_straddle_schedulers` threads `alert_fn`, `preflight_lead_seconds`, `retry_interval_seconds`, and `event_type` through.
- **Modify `app/main.py`** — build the straddle `alert_fn` (Discord + journal + email), pass new args to `build_news_straddle_schedulers`, and remove the `broker.on_bar(_sch.on_bar)` registration.
- **Modify `tests/test_news_straddle_live.py`** — update `FakeBroker` + the scheduler tests to the fetch model; add fetch-failure, early-warning, and alert-payload tests.

---

## Notes for the executor

- The scheduler's `_arm_event` and the existing scheduler tests currently rely on a bar **buffer** fed via `on_bar`. This plan **removes** that. The existing tests `test_scheduler_arms_oco_from_pre_range` and `test_scheduler_skips_when_too_few_bars` are **rewritten** (not just added to). Don't leave the old `on_bar`-feeding versions in place — they'll fail once the buffer is gone.
- `get_historical_bars` signature (in `app/broker/topstepx.py`): `async def get_historical_bars(self, timeframe="1min", limit=500, days=5, start_time=None, end_time=None, instrument=None) -> list[Bar]`.
- `Bar` (in `app/broker/events.py`) has `.ts` (UTC-aware datetime), `.high`, `.low` (Decimal), `.instrument`, `.timeframe`.
- `place_oco_stop_entries(instrument, buy_stop, sell_stop, *, stop_r, tp_r, size) -> (buy_id, sell_id)`.
- `alert_fn` is `Callable[[dict], Awaitable[None]] | None`. The scheduler must tolerate `None` (used in tests that don't care about alerts).

---

## Task 1: Config fields for pre-flight + retry

**Files:**
- Modify: `app/bot_config.py` (after line 182, `news_straddle_arm_lead_seconds`)
- Test: `tests/test_news_straddle_live.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_news_straddle_live.py`:
```python
def test_straddle_preflight_config_defaults():
    from app.bot_config import StrategyParams
    s = StrategyParams()
    assert s.news_straddle_preflight_lead_seconds == 300
    assert s.news_straddle_retry_interval_seconds == 60
```

- [ ] **Step 2: Run it, verify it fails**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py::test_straddle_preflight_config_defaults -v`
Expected: FAIL — `AttributeError: ... has no attribute 'news_straddle_preflight_lead_seconds'`.

- [ ] **Step 3: Add the fields**

In `app/bot_config.py`, immediately after the `news_straddle_arm_lead_seconds: int = 120` line, add:
```python
    # Pre-flight readiness check: warn this far before release if the straddle is
    # at risk of not arming (feed/data unavailable), with time to react. Retry the
    # readiness check this often until arm time. Arm timing itself is unchanged.
    news_straddle_preflight_lead_seconds: int = 300
    news_straddle_retry_interval_seconds: int = 60
```

- [ ] **Step 4: Run it, verify it passes**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py::test_straddle_preflight_config_defaults -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py tests/test_news_straddle_live.py
git commit -m "feat(straddle): add preflight_lead + retry_interval config"
```

---

## Task 2: Journal `publish_straddle_event`

**Files:**
- Modify: `app/api/journal.py` (after `publish_feed_watchdog`, ~line 303)
- Test: `tests/test_news_straddle_live.py`

- [ ] **Step 1: Write the failing test**

```python
def test_journal_publish_straddle_event():
    from app.api.journal import Journal
    captured = []
    j = Journal()
    j._publish = lambda entry: captured.append(entry)   # stub the broadcast
    j.publish_straddle_event({"kind": "armed", "event_type": "FOMC"})
    assert len(captured) == 1
    assert captured[0].kind == "straddle_event"
    assert captured[0].payload["kind"] == "armed"
```

- [ ] **Step 2: Run it, verify it fails**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py::test_journal_publish_straddle_event -v`
Expected: FAIL — `AttributeError: 'Journal' object has no attribute 'publish_straddle_event'`.

- [ ] **Step 3: Add the method**

In `app/api/journal.py`, directly after the `publish_feed_watchdog` method, add:
```python
    def publish_straddle_event(self, payload: dict) -> None:
        """Broadcast a news-straddle lifecycle event (early_warning | skipped |
        armed | recovered) to dashboard clients so a skip is never silent."""
        self._publish(JournalEntry(
            ts=datetime.now(timezone.utc), kind="straddle_event", payload=payload,
        ))
```

- [ ] **Step 4: Run it, verify it passes**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py::test_journal_publish_straddle_event -v`
Expected: PASS. (If `Journal()` needs constructor args, check the top of `journal.py` and pass what the existing tests pass; the stubbing of `_publish` keeps it isolated.)

- [ ] **Step 5: Commit**

```bash
git add app/api/journal.py tests/test_news_straddle_live.py
git commit -m "feat(journal): publish_straddle_event for dashboard alerts"
```

---

## Task 3: Scheduler core — fetch-based range + alerts (drop the buffer)

**Files:**
- Modify: `app/notifications/news_straddle_scheduler.py`
- Test: `tests/test_news_straddle_live.py`

- [ ] **Step 1: Rewrite the scheduler tests for the fetch model (failing)**

Replace the `FakeBroker`, `_bar`, `_sched`, and the three scheduler tests (`test_scheduler_arms_oco_from_pre_range`, `test_scheduler_skips_when_too_few_bars`, `test_scheduler_does_not_rearm_fired_event`) with:
```python
from app.notifications.news_straddle_scheduler import NewsStraddleScheduler


def _bar(ts, high, low):
    return Bar(ts=ts, instrument="MNQ", timeframe="1min",
               open=Decimal(str(low)), high=Decimal(str(high)),
               low=Decimal(str(low)), close=Decimal(str(low)), volume=100)


class FakeBroker:
    def __init__(self, bars=None, raise_on_fetch=False):
        self.oco_calls = []
        self._bars = bars or []
        self.raise_on_fetch = raise_on_fetch

    async def get_historical_bars(self, timeframe="1min", limit=500, days=5,
                                  start_time=None, end_time=None, instrument=None):
        if self.raise_on_fetch:
            raise RuntimeError("broker not connected")
        return list(self._bars)

    async def place_oco_stop_entries(self, instrument, buy_stop, sell_stop, *, stop_r, tp_r, size):
        self.oco_calls.append(
            {"instrument": instrument, "buy_stop": buy_stop, "sell_stop": sell_stop,
             "stop_r": stop_r, "tp_r": tp_r, "size": size})
        return ("BUY_OID", "SELL_OID")


def _window_bars(arm_ts, n, highs, lows):
    """n 1-min bars ending strictly before arm_ts."""
    base = arm_ts - timedelta(minutes=n)
    return [_bar(base + timedelta(minutes=i), highs[i], lows[i]) for i in range(n)]


def _sched(broker, event_ts, alerts=None, **kw):
    async def _alert_fn(payload):
        (alerts if alerts is not None else []).append(payload)
    return NewsStraddleScheduler(
        broker, instrument="MNQ", event_type="CPI", event_times=[event_ts],
        offset_ticks=60, tp_r=Decimal("3.0"), tick=Decimal("0.25"), size=2,
        arm_lead_seconds=120, range_minutes=15, min_range_bars=5,
        preflight_lead_seconds=300, retry_interval_seconds=60,
        alert_fn=_alert_fn, **kw,
    )


def test_scheduler_arms_oco_from_1min_fetch():
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    arm_ts = event - timedelta(seconds=120)
    # 15 one-min bars in [arm-15min, arm); high spans up to 110, low down to 90.
    bars = _window_bars(arm_ts, 15, [100 + i % 11 for i in range(15)],
                                    [100 - i % 11 for i in range(15)])
    broker = FakeBroker(bars=bars)
    alerts = []
    sched = _sched(broker, event, alerts=alerts)
    asyncio.run(sched._arm_event(sched._events[0]))
    assert len(broker.oco_calls) == 1
    call = broker.oco_calls[0]
    assert call["buy_stop"] == Decimal("110") + Decimal("15")   # high+offset
    assert call["sell_stop"] == Decimal("90") - Decimal("15")   # low-offset
    assert call["stop_r"] == Decimal("15") and call["tp_r"] == Decimal("3.0") and call["size"] == 2
    assert sched._events[0].status == "armed"
    assert any(a["kind"] == "armed" for a in alerts)


def test_scheduler_skips_and_alerts_on_too_few_bars():
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    arm_ts = event - timedelta(seconds=120)
    bars = _window_bars(arm_ts, 3, [105, 105, 105], [95, 95, 95])  # 3 < 5
    broker = FakeBroker(bars=bars)
    alerts = []
    sched = _sched(broker, event, alerts=alerts)
    asyncio.run(sched._arm_event(sched._events[0]))
    assert broker.oco_calls == []
    assert sched._events[0].status == "skipped"
    assert any(a["kind"] == "skipped" and "insufficient_bars" in a["reason"] for a in alerts)


def test_scheduler_skips_and_alerts_on_fetch_error():
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    broker = FakeBroker(raise_on_fetch=True)
    alerts = []
    sched = _sched(broker, event, alerts=alerts)
    asyncio.run(sched._arm_event(sched._events[0]))
    assert broker.oco_calls == []
    assert sched._events[0].status == "skipped"
    assert any(a["kind"] == "skipped" and a["reason"] == "fetch_error" for a in alerts)


def test_scheduler_does_not_rearm_fired_event():
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    arm_ts = event - timedelta(seconds=120)
    bars = _window_bars(arm_ts, 15, [105] * 15, [95] * 15)
    broker = FakeBroker(bars=bars)
    sched = _sched(broker, event)
    asyncio.run(sched._arm_event(sched._events[0]))
    asyncio.run(sched._arm_event(sched._events[0]))   # no-op second time
    assert len(broker.oco_calls) == 1
```

- [ ] **Step 2: Run them, verify they fail**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py -k scheduler -v`
Expected: FAIL — `NewsStraddleScheduler.__init__` doesn't accept `event_type`/`alert_fn`/`preflight_lead_seconds`/`retry_interval_seconds`, and `_arm_event` still uses the buffer.

- [ ] **Step 3: Rewrite the scheduler**

In `app/notifications/news_straddle_scheduler.py`:

(a) Add a status constant near the others and a `reason` field on `_SchedEvent`:
```python
_RECOVERED = "recovered"   # informational, not terminal
```
```python
@dataclass
class _SchedEvent:
    ts: datetime
    status: str = _PENDING
    buy_id: str | None = None
    sell_id: str | None = None
    rhigh: Decimal | None = None
    rlow: Decimal | None = None
    reason: str | None = None
```

(b) Replace `__init__` (keep the existing body, change the signature + drop `_buffer`, add the new attributes):
```python
    def __init__(
        self,
        broker,
        *,
        instrument: str,
        event_type: str,
        event_times: list[datetime],
        offset_ticks: int,
        tp_r: Decimal,
        tick: Decimal,
        size: int,
        arm_lead_seconds: int = 120,
        range_minutes: int = 15,
        min_range_bars: int = 5,
        preflight_lead_seconds: int = 300,
        retry_interval_seconds: int = 60,
        alert_fn: "Callable[[dict], Awaitable[None]] | None" = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.broker = broker
        self.instrument = instrument
        self.event_type = event_type
        self.offset = Decimal(offset_ticks) * tick
        self.tp_r = tp_r
        self.size = size
        self.arm_lead_seconds = arm_lead_seconds
        self.range_minutes = range_minutes
        self.min_range_bars = min_range_bars
        self.preflight_lead_seconds = preflight_lead_seconds
        self.retry_interval_seconds = retry_interval_seconds
        self._alert_fn = alert_fn
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._events = [_SchedEvent(ts=t) for t in sorted(event_times)]
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
```
Add to the imports at the top: `from typing import Awaitable, Callable`.

(c) **Delete** `on_bar` and `_compute_range` entirely.

(d) Add the fetch + readiness + alert helpers and rewrite `_arm_event`:
```python
    async def _alert(self, kind: str, ev: _SchedEvent, reason: str = "", **extra) -> None:
        ev.reason = reason or None
        if self._alert_fn is None:
            return
        payload = {
            "kind": kind,
            "event_type": self.event_type,
            "instrument": self.instrument,
            "release_ts": ev.ts.isoformat(),
            "reason": reason,
            "range_high": str(ev.rhigh) if ev.rhigh is not None else None,
            "range_low": str(ev.rlow) if ev.rlow is not None else None,
            "size": self.size,
            **extra,
        }
        try:
            await self._alert_fn(payload)
        except Exception:
            log.exception("news_straddle scheduler: alert_fn raised")

    async def _fetch_range(self, arm_ts: datetime) -> "tuple[Decimal | None, Decimal | None, int]":
        start = arm_ts - timedelta(minutes=self.range_minutes)
        bars = await self.broker.get_historical_bars(
            timeframe="1min", start_time=start, end_time=arm_ts, instrument=self.instrument,
        )
        window = [b for b in bars if start <= b.ts < arm_ts]
        if len(window) < self.min_range_bars:
            return None, None, len(window)
        return max(b.high for b in window), min(b.low for b in window), len(window)

    async def _is_ready(self) -> "tuple[bool, int, str]":
        """Pre-flight readiness on data-so-far: enough 1-min bars in the last
        range_minutes. Any fetch error (incl. broker not connected) = not ready."""
        now = self._now()
        start = now - timedelta(minutes=self.range_minutes)
        try:
            bars = await self.broker.get_historical_bars(
                timeframe="1min", start_time=start, end_time=now, instrument=self.instrument,
            )
        except Exception:
            return False, 0, "broker_unavailable"
        n = len([b for b in bars if start <= b.ts < now])
        if n < self.min_range_bars:
            return False, n, f"insufficient_bars:{n}/{self.min_range_bars}"
        return True, n, ""

    async def _arm_event(self, ev: _SchedEvent) -> None:
        if ev.status != _PENDING:
            return  # one straddle per event — never re-arm
        arm_ts = ev.ts - timedelta(seconds=self.arm_lead_seconds)
        try:
            high, low, n = await self._fetch_range(arm_ts)
        except Exception:
            ev.status = _SKIPPED
            log.exception("news_straddle scheduler: range fetch failed for %s", ev.ts)
            await self._alert("skipped", ev, reason="fetch_error")
            return
        if high is None:
            ev.status = _SKIPPED
            log.warning("news_straddle scheduler: %s skipped (%d/%d 1-min bars)",
                        ev.ts, n, self.min_range_bars)
            await self._alert("skipped", ev, reason=f"insufficient_bars:{n}/{self.min_range_bars}")
            return
        ev.rhigh, ev.rlow = high, low
        buy_stop = high + self.offset
        sell_stop = low - self.offset
        log.info("news_straddle scheduler arming %s: range=[%s-%s] buy=%s sell=%s R=%s",
                 ev.ts, low, high, buy_stop, sell_stop, self.offset)
        try:
            buy_id, sell_id = await self.broker.place_oco_stop_entries(
                self.instrument, buy_stop, sell_stop,
                stop_r=self.offset, tp_r=self.tp_r, size=self.size,
            )
        except Exception:
            ev.status = _SKIPPED
            log.exception("news_straddle scheduler: place_oco_stop_entries failed for %s", ev.ts)
            await self._alert("skipped", ev, reason="oco_error")
            return
        if not (buy_id and sell_id):
            ev.status = _SKIPPED
            log.error("news_straddle scheduler: OCO not placed for %s", ev.ts)
            await self._alert("skipped", ev, reason="oco_not_placed")
            return
        ev.buy_id, ev.sell_id, ev.status = buy_id, sell_id, _ARMED
        await self._alert("armed", ev, reason="")
```

- [ ] **Step 4: Run the scheduler tests, verify they pass**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py -k scheduler -v`
Expected: PASS for `test_scheduler_arms_oco_from_1min_fetch`, `test_scheduler_skips_and_alerts_on_too_few_bars`, `test_scheduler_skips_and_alerts_on_fetch_error`, `test_scheduler_does_not_rearm_fired_event`. (`_run` is not exercised yet — that's Task 4.)

- [ ] **Step 5: Commit**

```bash
git add app/notifications/news_straddle_scheduler.py tests/test_news_straddle_live.py
git commit -m "feat(straddle): build range from 1-min fetch + alert on every outcome"
```

---

## Task 4: Pre-flight readiness + retry in `_run`

**Files:**
- Modify: `app/notifications/news_straddle_scheduler.py`
- Test: `tests/test_news_straddle_live.py`

- [ ] **Step 1: Write the failing test (preflight extracted method)**

```python
def test_preflight_alerts_early_when_not_ready():
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    broker = FakeBroker(raise_on_fetch=True)   # feed unavailable
    alerts = []
    sched = _sched(broker, event, alerts=alerts)
    ok = asyncio.run(sched._preflight(sched._events[0]))
    assert ok is False
    assert any(a["kind"] == "early_warning" for a in alerts)


def test_preflight_ok_when_enough_bars():
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    # _is_ready uses [now-15min, now); supply 15 bars ending ~now.
    now = datetime.now(UTC)
    bars = _window_bars(now, 15, [105] * 15, [95] * 15)
    broker = FakeBroker(bars=bars)
    alerts = []
    sched = _sched(broker, event, alerts=alerts)
    ok = asyncio.run(sched._preflight(sched._events[0]))
    assert ok is True
    assert not any(a["kind"] == "early_warning" for a in alerts)
```

- [ ] **Step 2: Run them, verify they fail**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py -k preflight -v`
Expected: FAIL — `_preflight` not defined.

- [ ] **Step 3: Add `_preflight` and rewrite `_run`**

Add `_preflight` (the testable unit) and rewrite `_run` (the timing orchestrator) in `app/notifications/news_straddle_scheduler.py`:
```python
    async def _preflight(self, ev: _SchedEvent) -> bool:
        """Readiness check ~preflight_lead before release. Fires an early-warning
        alert (with time to react) if at risk. Returns True if ready/on-track."""
        ok, n, reason = await self._is_ready()
        if ok:
            log.info("news_straddle scheduler preflight OK %s (%d bars)", ev.ts, n)
            return True
        log.warning("news_straddle scheduler preflight AT-RISK %s: %s", ev.ts, reason)
        await self._alert("early_warning", ev, reason=reason)
        return False

    async def _sleep_until(self, when: datetime) -> bool:
        """Sleep until `when` (interruptible by stop). Returns True if stop was set."""
        secs = max(0.0, (when - self._now()).total_seconds())
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=secs)
            return True
        except asyncio.TimeoutError:
            return False

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            ev = self._next_pending()
            if ev is None:
                if await self._sleep_until(self._now() + timedelta(hours=1)):
                    break
                continue
            preflight_ts = ev.ts - timedelta(seconds=self.preflight_lead_seconds)
            arm_ts = ev.ts - timedelta(seconds=self.arm_lead_seconds)
            if await self._sleep_until(preflight_ts):
                break
            if ev.status != _PENDING:
                continue
            # Pre-flight; on trouble, retry until arm time.
            if not await self._preflight(ev):
                while not self._stop_event.is_set() and self._now() < arm_ts:
                    nxt = min(self._now() + timedelta(seconds=self.retry_interval_seconds), arm_ts)
                    if await self._sleep_until(nxt):
                        break
                    ok, _, _ = await self._is_ready()
                    if ok:
                        log.info("news_straddle scheduler preflight RECOVERED %s", ev.ts)
                        await self._alert("recovered", ev, reason="")
                        break
            if self._stop_event.is_set():
                break
            # Arm at the validated arm time regardless of preflight outcome.
            if await self._sleep_until(arm_ts):
                break
            if ev.status == _PENDING:
                try:
                    await self._arm_event(ev)
                except Exception:
                    log.exception("news_straddle scheduler: arm failed for %s", ev.ts)
```
Note: this replaces the old `_run`. Keep `_next_pending`, `start`, `stop` as they are.

- [ ] **Step 4: Run the preflight tests + full scheduler suite, verify pass**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py -k "preflight or scheduler" -v`
Expected: PASS for all preflight + scheduler tests.

- [ ] **Step 5: Commit**

```bash
git add app/notifications/news_straddle_scheduler.py tests/test_news_straddle_live.py
git commit -m "feat(straddle): preflight readiness check + retry-on-trouble"
```

---

## Task 5: Extend `state()` for observability

**Files:**
- Modify: `app/notifications/news_straddle_scheduler.py` (`state()`)
- Test: `tests/test_news_straddle_live.py`

- [ ] **Step 1: Update the state-shape test (failing)**

Replace `test_scheduler_state_shape` with:
```python
def test_scheduler_state_includes_reason():
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    broker = FakeBroker(raise_on_fetch=True)
    sched = _sched(broker, event)
    asyncio.run(sched._arm_event(sched._events[0]))  # -> skipped, reason set
    st = sched.state()
    assert st["event_type"] == "CPI"
    ev0 = st["events"][0]
    assert ev0["status"] == "skipped"
    assert ev0["reason"] == "fetch_error"
```

- [ ] **Step 2: Run it, verify it fails**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py::test_scheduler_state_includes_reason -v`
Expected: FAIL — `state()` has no `event_type` / per-event `reason`.

- [ ] **Step 3: Update `state()`**

Replace the `state()` method body:
```python
    def state(self) -> dict:
        """Live dashboard view (Rule 13). Pure read."""
        return {
            "instrument": self.instrument,
            "event_type": self.event_type,
            "offset": str(self.offset),
            "tp_r": str(self.tp_r),
            "size": self.size,
            "events": [
                {
                    "ts": ev.ts.isoformat(),
                    "status": ev.status,
                    "reason": ev.reason,
                    "range_high": str(ev.rhigh) if ev.rhigh is not None else None,
                    "range_low": str(ev.rlow) if ev.rlow is not None else None,
                }
                for ev in self._events
            ],
        }
```

- [ ] **Step 4: Run it, verify it passes**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py::test_scheduler_state_includes_reason -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/notifications/news_straddle_scheduler.py tests/test_news_straddle_live.py
git commit -m "feat(straddle): expose event_type + skip reason in state()"
```

---

## Task 6: Thread alert_fn + config through `build_news_straddle_schedulers`

**Files:**
- Modify: `app/strategy/news_straddle.py` (`build_news_straddle_schedulers`, ~line 291)
- Test: `tests/test_news_straddle_live.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_schedulers_threads_event_type_and_alert():
    import asyncio as _aio
    from app.strategy.news_straddle import build_news_straddle_schedulers, ResolvedStraddleSpec
    specs = [ResolvedStraddleSpec(event_type="FOMC", instrument="MGC", offset_ticks=20,
                                  tp_r=Decimal("3.0"), contracts=20, suppress_base=False)]
    seen = []
    async def alert_fn(p): seen.append(p)
    scheds = build_news_straddle_schedulers(
        broker=FakeBroker(), specs=specs, events_path="data/news_events.csv",
        arm_lead_seconds=120, alert_fn=alert_fn,
        preflight_lead_seconds=300, retry_interval_seconds=60,
    )
    assert len(scheds) == 1
    s = scheds[0]
    assert s.event_type == "FOMC" and s.instrument == "MGC" and s.size == 20
    assert s.preflight_lead_seconds == 300 and s.retry_interval_seconds == 60
    assert s._alert_fn is alert_fn
```

- [ ] **Step 2: Run it, verify it fails**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py::test_build_schedulers_threads_event_type_and_alert -v`
Expected: FAIL — `build_news_straddle_schedulers` got an unexpected keyword `alert_fn`.

- [ ] **Step 3: Update `build_news_straddle_schedulers`**

Replace the function in `app/strategy/news_straddle.py`:
```python
def build_news_straddle_schedulers(
    broker, specs, events_path: str, arm_lead_seconds: int,
    alert_fn=None, preflight_lead_seconds: int = 300, retry_interval_seconds: int = 60,
):
    """One NewsStraddleScheduler per resolved spec — each single-instrument with its
    own offset/tp_r/size and only its own event_type's release times."""
    from app.broker.paper import TICK_SIZE
    from app.notifications.news_straddle_scheduler import NewsStraddleScheduler

    schedulers = []
    for s in specs:
        schedulers.append(NewsStraddleScheduler(
            broker,
            instrument=s.instrument,
            event_type=s.event_type,
            event_times=load_event_times(events_path, s.event_type),
            offset_ticks=s.offset_ticks,
            tp_r=s.tp_r,
            tick=TICK_SIZE.get(s.instrument, Decimal("0.25")),
            size=s.contracts,
            arm_lead_seconds=arm_lead_seconds,
            preflight_lead_seconds=preflight_lead_seconds,
            retry_interval_seconds=retry_interval_seconds,
            alert_fn=alert_fn,
        ))
    return schedulers
```

- [ ] **Step 4: Run it, verify it passes**

Run: `…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py::test_build_schedulers_threads_event_type_and_alert -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/strategy/news_straddle.py tests/test_news_straddle_live.py
git commit -m "feat(straddle): thread alert_fn + preflight config through builder"
```

---

## Task 7: Wire the alert fan-out in main.py + remove the bar-buffer registration

**Files:**
- Modify: `app/main.py` (the `news_straddle_schedulers` block, ~lines 1357-1372)

This task has no new unit test (it is integration wiring); verification is the full suite + a manual import check. Mirror the existing `_on_feed_status` fan-out.

- [ ] **Step 1: Add the straddle alert fan-out and update the build call**

In `app/main.py`, replace the block that builds the schedulers (currently lines ~1357-1372) with:
```python
    # news_straddle LIVE resting-OCO schedulers (B92), one per event spec.
    news_straddle_schedulers = []
    _ns = bot_cfg.strategy
    if (_ns.engine == "news_straddle" and _ns.news_straddle_live_enabled) or _ns.cpi_day_router_enabled:
        from app.strategy.news_straddle import build_news_straddle_schedulers

        async def _on_straddle_event(payload: dict) -> None:
            try:
                journal.publish_straddle_event(payload)
            except Exception:
                log.exception("publish_straddle_event failed")
            kind = payload.get("kind")
            inst = payload.get("instrument")
            et = payload.get("event_type")
            rel = payload.get("release_ts")
            if kind == "early_warning":
                title = "🟠 STRADDLE AT RISK"
                body = f"{et}/{inst} {rel}: may not arm — {payload.get('reason')}"
            elif kind == "skipped":
                title = "🔴 STRADDLE SKIPPED"
                body = f"{et}/{inst} {rel}: no OCO placed — {payload.get('reason')}"
            elif kind == "armed":
                title = "🟢 STRADDLE ARMED"
                body = (f"{et}/{inst} {payload.get('size')}x: range "
                        f"[{payload.get('range_low')}–{payload.get('range_high')}]")
            elif kind == "recovered":
                title = "🟢 STRADDLE FEED RECOVERED"
                body = f"{et}/{inst} {rel}: data healthy again, on track."
            else:
                return
            if discord.enabled:
                await discord.send_alert(title, body)
            if notifier.enabled:
                await notifier.send(title, body)

        news_straddle_schedulers = build_news_straddle_schedulers(
            broker, _straddle_specs,
            _ns.news_straddle_events_path, _ns.news_straddle_arm_lead_seconds,
            alert_fn=_on_straddle_event,
            preflight_lead_seconds=_ns.news_straddle_preflight_lead_seconds,
            retry_interval_seconds=_ns.news_straddle_retry_interval_seconds,
        )
        for _sch in news_straddle_schedulers:
            log.warning(
                "news_straddle LIVE: %d events, %d contract(s), arm %ds (preflight %ds) on %s "
                "(offset %s) — resting OCO straddle.",
                len(_sch._events), _sch.size, _sch.arm_lead_seconds,
                _sch.preflight_lead_seconds, _sch.instrument, _sch.offset,
            )
```
The key removal: the old `broker.on_bar(_sch.on_bar)` line is gone (the scheduler no longer buffers bars).

- [ ] **Step 2: Verify the module imports and the full suite passes**

Run:
```bash
…/.venv/Scripts/python.exe -c "import app.main"
…/.venv/Scripts/python.exe -m pytest tests/test_news_straddle_live.py -v
…/.venv/Scripts/python.exe -m pytest tests/ -q
```
Expected: `import app.main` succeeds (no NameError/SyntaxError); straddle tests pass; full suite green (no regressions from removing `on_bar`).

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat(straddle): wire skip/arm alerts to Discord+journal; drop bar buffer"
```

---

## Task 8: Live data verification (today's FOMC window)

**Files:**
- Create: `scripts/_verify_straddle_range.py` (scratch; covered by `scripts/_*.py` gitignore)

- [ ] **Step 1: Write the verification script**

```python
# scratch: prove the fixed scheduler builds a valid 1-min range for today's FOMC.
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

async def main():
    from app.broker.topstepx import TopstepXBroker
    from app.config import load_config
    cfg = load_config()
    broker = TopstepXBroker()
    await broker.subscribe(instruments=["MGC"], timeframes=["5min"])  # connect
    arm_ts = datetime(2026, 6, 17, 18, 0, tzinfo=timezone.utc) - timedelta(seconds=120)
    start = arm_ts - timedelta(minutes=15)
    bars = await broker.get_historical_bars(
        timeframe="1min", start_time=start, end_time=arm_ts, instrument="MGC")
    window = [b for b in bars if start <= b.ts < arm_ts]
    print(f"1-min bars in [{start:%H:%M}-{arm_ts:%H:%M}) UTC: {len(window)}")
    if len(window) >= 5:
        high = max(b.high for b in window); low = min(b.low for b in window)
        offset = Decimal("20") * Decimal("0.1")  # FOMC/MGC: 20t * 0.1
        print(f"range=[{low}-{high}]  buy_stop={high+offset}  sell_stop={low-offset}  R={offset}")
        print("RESULT: would have ARMED")
    else:
        print("RESULT: would have SKIPPED")

asyncio.run(main())
```

- [ ] **Step 2: Run it (requires the broker to connect; run from the main checkout where `.env` lives)**

Run: `cd C:/Users/Lawrence/Documents/topstep-trader-bot/topstep-bot && .venv/Scripts/python.exe <path>/scripts/_verify_straddle_range.py`
Expected: prints `len >= 5` and a concrete `range=[…]` with buy/sell stops → "would have ARMED", confirming the fix produces a valid range against today's real data. (If the broker can't be opened a second time alongside the running bot, instead reuse the running bot: this step is a confirmation, not required for correctness — the unit tests are the gate. Note the outcome and proceed.)

- [ ] **Step 3: No commit** (scratch script is gitignored).

---

## Self-Review (completed at write time)

- **Spec coverage:** Core 1-min fetch → Task 3. Preflight + retry + early warning → Task 4. Validated −120s arm preserved → `_arm_event`/`_run` use `arm_lead_seconds` (Tasks 3-4). Alerting (early/skip/armed/recovered) via watchdog path → Tasks 2,6,7. Config fields → Task 1. Observability/state → Task 5. Backtest detector / offsets / window untouched → no task changes them. Live verification → Task 8. All spec sections mapped.
- **Placeholder scan:** No TBD/TODO. The Task 8 fallback note is a real contingency, not a placeholder. The broker-connected "open item" from the spec is resolved (Task 3 `_is_ready` treats any fetch error as not-ready).
- **Type/name consistency:** `event_type`, `alert_fn`, `preflight_lead_seconds`, `retry_interval_seconds` are introduced in Task 1/3 and used identically in Tasks 4-7. `_fetch_range`/`_is_ready`/`_preflight`/`_arm_event`/`_alert`/`_sleep_until` names are consistent across tasks. `publish_straddle_event` (Task 2) is called in Task 7. `build_news_straddle_schedulers` kwargs (Task 6) match the call site (Task 7). `FakeBroker.get_historical_bars` (Task 3) matches the real signature.
