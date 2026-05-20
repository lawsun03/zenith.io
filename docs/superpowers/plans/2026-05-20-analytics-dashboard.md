# Analytics Dashboard + Claude Advisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/analytics` page showing historical trade performance metrics and an on-demand Claude advisor that uses agentic tool-calling to investigate the data and produce specific parameter recommendations.

**Architecture:** New `app/analytics/` Python module (loader → tools → advisor) plus five new React components assembled into a `Analytics.tsx` page. Two new FastAPI routes added inside `build_app()`. The analytics module is read-only — it never touches the trading path.

**Tech Stack:** Python `anthropic` SDK (AsyncAnthropic), FastAPI StreamingResponse, React + TypeScript + Tailwind, `fetch` + `ReadableStream` for SSE consumption.

---

## File Map

**Create:**
- `app/analytics/__init__.py` — empty, marks package
- `app/analytics/loader.py` — reads trade CSVs, backtest summaries, logs, config
- `app/analytics/tools.py` — six tool functions Claude can call
- `app/analytics/advisor.py` — agentic loop, SSE streaming
- `tests/test_analytics_loader.py` — loader unit tests
- `tests/test_analytics_tools.py` — tool function unit tests
- `frontend/src/pages/Analytics.tsx` — top-level page, fetches stats, stacked layout
- `frontend/src/components/StatsRow.tsx` — five stat cards
- `frontend/src/components/KillzoneTable.tsx` — per-killzone grid
- `frontend/src/components/TradesTable.tsx` — recent trades list
- `frontend/src/components/ClaudeAdvisor.tsx` — Claude panel with SSE streaming

**Modify:**
- `app/api/server.py` — add `AskClaudeRequest` model + two new routes inside `build_app()`
- `frontend/src/App.tsx` — add `/analytics` path guard (same pattern as `/backtests`)
- `frontend/src/components/Header.tsx` — add Analytics nav link

---

## Task 1: Install `anthropic` and scaffold the package

**Files:**
- Create: `app/analytics/__init__.py`

- [ ] **Step 1: Install the anthropic SDK into the project venv**

```bash
cd C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot
.venv/Scripts/pip install anthropic
```

Expected: `Successfully installed anthropic-...`

- [ ] **Step 2: Verify install**

```bash
.venv/Scripts/python -c "import anthropic; print(anthropic.__version__)"
```

Expected: a version string like `0.40.0`

- [ ] **Step 3: Create the empty package marker**

Create `app/analytics/__init__.py` with empty contents (just the file, no code needed).

- [ ] **Step 4: Commit**

```bash
git add app/analytics/__init__.py
git commit -m "feat: scaffold app/analytics package, install anthropic SDK"
```

---

## Task 2: Data loader

**Files:**
- Create: `app/analytics/loader.py`
- Create: `tests/test_analytics_loader.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analytics_loader.py`:

```python
import re
from pathlib import Path

import pytest

from app.analytics.loader import (
    _parse_log_line,
    _parse_summary_file,
    load_all_trades,
    load_backtest_summaries,
    load_config,
    load_log_events,
)


def test_load_all_trades_returns_list():
    result = load_all_trades()
    assert isinstance(result, list)


def test_trade_has_required_fields():
    trades = load_all_trades()
    if not trades:
        pytest.skip("no trade files on disk")
    for field in ("ts", "instrument", "side", "type", "fill_price", "size", "realized_pnl"):
        assert field in trades[0]


def test_old_trade_file_missing_cols_handled():
    """trades.csv has no killzone/stop/target — must not raise, must include key."""
    for t in load_all_trades():
        assert "killzone" in t  # key present even if value is None


def test_parse_summary_file(tmp_path: Path):
    text = """\
============================================================
BACKTEST SUMMARY: body_atr_multiple=1.0  swing_lookback=2  r_multiple=1.5
============================================================
Verdict:           PROFITABLE

Net P&L:           +$1,706.00
  Gross profit:    +$3,939.00
  Gross loss:      -$2,233.00

Total trades:      103
  Winners:         57  (55.3%)
  Losers:          46

Profit factor:   1.76
Expectancy:      +$16.56 per trade
Max drawdown:    -$321.00

By killzone:
  London      trades= 47  win_rate= 63.8%  net=    +$1,000.00
  NY AM       trades= 35  win_rate= 48.6%  net=      +$544.00

============================================================
"""
    p = tmp_path / "summary_run00.txt"
    p.write_text(text, encoding="utf-8")
    result = _parse_summary_file(p)
    assert result is not None
    assert result["net_pnl"] == pytest.approx(1706.0)
    assert result["total_trades"] == 103
    assert result["win_rate"] == pytest.approx(0.553, abs=0.001)
    assert result["params"]["r_multiple"] == "1.5"
    assert "london" in result["killzones"]
    assert result["killzones"]["london"]["trades"] == 47
    assert result["killzones"]["london"]["net_pnl"] == pytest.approx(1000.0)


def test_parse_log_line_valid():
    line = "08:25:01 INFO    app.execution.engine | Signal fired: short"
    r = _parse_log_line(line)
    assert r is not None
    assert r["level"] == "INFO"
    assert r["logger"] == "app.execution.engine"
    assert "Signal fired" in r["message"]


def test_parse_log_line_invalid():
    assert _parse_log_line("garbage with no structure") is None


def test_load_config_returns_dict():
    assert isinstance(load_config(), dict)


def test_load_backtest_summaries_returns_list():
    summaries = load_backtest_summaries()
    assert isinstance(summaries, list)
    if summaries:
        assert "net_pnl" in summaries[0]
        assert "params" in summaries[0]
```

- [ ] **Step 2: Run — verify they fail**

```bash
cd C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot
.venv/Scripts/pytest tests/test_analytics_loader.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'app.analytics.loader'`

- [ ] **Step 3: Implement `app/analytics/loader.py`**

```python
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent  # project root


def load_all_trades() -> list[dict]:
    """Merge all trades*.csv files from project root."""
    rows: list[dict] = []
    for path in sorted(_ROOT.glob("trades*.csv")):
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({
                    "ts": row.get("ts", ""),
                    "instrument": row.get("instrument", ""),
                    "side": row.get("side", ""),
                    "type": row.get("type", ""),
                    "fill_price": _float(row.get("fill_price")),
                    "size": _int(row.get("size")),
                    "realized_pnl": _float(row.get("realized_pnl")),
                    "broker_order_id": row.get("broker_order_id", ""),
                    "stop": _float(row.get("stop")),
                    "target": _float(row.get("target")),
                    "killzone": row.get("killzone") or None,
                    "rationale": row.get("rationale") or None,
                })
    return rows


def load_backtest_summaries() -> list[dict]:
    """Parse all summary_run*.txt from backtest_results/."""
    results = []
    for path in sorted((_ROOT / "backtest_results").glob("summary_run*.txt")):
        parsed = _parse_summary_file(path)
        if parsed:
            results.append(parsed)
    return results


def load_log_events(days: int = 2) -> list[dict]:
    """Read last N daily log files. Files are UTF-16 (Windows PowerShell redirect)."""
    events: list[dict] = []
    logs_dir = _ROOT / "logs"
    if not logs_dir.exists():
        return events
    for path in sorted(logs_dir.glob("*.log"))[-days:]:
        try:
            text = path.read_text(encoding="utf-16", errors="replace")
        except Exception:
            continue
        for line in text.splitlines():
            parsed = _parse_log_line(line.strip())
            if parsed:
                events.append(parsed)
    return events


def load_config() -> dict:
    p = _ROOT / "bot_config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _float(v: str | None) -> float | None:
    if not v:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _int(v: str | None) -> int | None:
    if not v:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _parse_summary_file(path: Path) -> dict | None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Header: "BACKTEST SUMMARY: key=val  key=val ..."
    params: dict[str, str] = {}
    for line in lines[:3]:
        for m in re.finditer(r'(\w+)=([\d.]+)', line):
            params[m.group(1)] = m.group(2)

    def extract(label: str) -> str | None:
        for line in lines:
            if label in line and ":" in line:
                return line.split(":")[-1].strip()
        return None

    def extract_float(label: str) -> float | None:
        v = extract(label)
        if not v:
            return None
        v = re.sub(r'[+$,]', '', v).split()[0]
        try:
            return float(v)
        except ValueError:
            return None

    total_trades: int | None = None
    win_rate: float | None = None
    for line in lines:
        if "Total trades:" in line:
            m = re.search(r'(\d+)', line.split(":")[-1])
            if m:
                total_trades = int(m.group(1))
        if "Winners:" in line:
            m = re.search(r'\(([\d.]+)%\)', line)
            if m:
                win_rate = float(m.group(1)) / 100

    # Killzone section
    killzones: dict[str, dict] = {}
    in_kz = False
    for line in lines:
        if "By killzone:" in line:
            in_kz = True
            continue
        if in_kz:
            if "===" in line:
                break
            m = re.match(
                r'\s*([\w/ ]+?)\s+trades=\s*(\d+)\s+win_rate=\s*([\d.]+)%\s+net=\s*([+\-$\d,. ]+)',
                line,
            )
            if m:
                kz = m.group(1).strip().lower().replace(" ", "_").replace("/", "_")
                net = re.sub(r'[+$,\s]', '', m.group(4))
                killzones[kz] = {
                    "trades": int(m.group(2)),
                    "win_rate": float(m.group(3)) / 100,
                    "net_pnl": float(net) if net else 0.0,
                }

    net_pnl = extract_float("Net P&L")
    if net_pnl is None:
        return None

    return {
        "params": params,
        "verdict": extract("Verdict"),
        "net_pnl": net_pnl,
        "gross_profit": extract_float("Gross profit"),
        "gross_loss": extract_float("Gross loss"),
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": extract_float("Profit factor"),
        "expectancy": extract_float("Expectancy"),
        "max_drawdown": extract_float("Max drawdown"),
        "killzones": killzones,
    }


def _parse_log_line(line: str) -> dict | None:
    # Format: "HH:MM:SS LEVEL   logger | message"
    m = re.match(
        r'(\d{2}:\d{2}:\d{2})\s+(INFO|WARNING|ERROR|DEBUG|CRITICAL)\s+(\S+)\s+\|\s+(.*)',
        line,
    )
    if not m:
        return None
    return {
        "time": m.group(1),
        "level": m.group(2),
        "logger": m.group(3),
        "message": m.group(4).strip(),
    }
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
.venv/Scripts/pytest tests/test_analytics_loader.py -v
```

Expected: all tests pass (some may skip if no trade files exist)

- [ ] **Step 5: Commit**

```bash
git add app/analytics/loader.py tests/test_analytics_loader.py
git commit -m "feat: analytics data loader (trades, backtests, logs, config)"
```

---

## Task 3: Tool functions

**Files:**
- Create: `app/analytics/tools.py`
- Create: `tests/test_analytics_tools.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_analytics_tools.py`:

```python
import pytest
from app.analytics.tools import (
    get_backtest_runs,
    get_current_config,
    get_killzone_breakdown,
    get_log_summary,
    get_performance_summary,
    get_recent_trades,
)


def test_performance_summary_structure():
    result = get_performance_summary()
    if "error" in result:
        return  # no trades on disk yet — acceptable
    for key in ("total_trades", "win_rate", "net_pnl", "profit_factor", "expectancy"):
        assert key in result
    assert 0.0 <= result["win_rate"] <= 1.0


def test_killzone_breakdown_structure():
    result = get_killzone_breakdown()
    assert isinstance(result, dict)
    for kz, data in result.items():
        assert isinstance(kz, str)
        for key in ("trades", "win_rate", "net_pnl"):
            assert key in data


def test_recent_trades_limit():
    trades = get_recent_trades(limit=5)
    assert len(trades) <= 5
    for t in trades:
        assert t["type"] == "EXIT"


def test_recent_trades_descending_order():
    trades = get_recent_trades(limit=20)
    timestamps = [t["ts"] for t in trades]
    assert timestamps == sorted(timestamps, reverse=True)


def test_backtest_runs_sorted_by_profit_factor():
    runs = get_backtest_runs(sort_by="profit_factor", limit=5)
    pfs = [r["profit_factor"] for r in runs if r.get("profit_factor") is not None]
    assert pfs == sorted(pfs, reverse=True)


def test_backtest_runs_sorted_by_net_pnl():
    runs = get_backtest_runs(sort_by="net_pnl", limit=5)
    vals = [r["net_pnl"] for r in runs]
    assert vals == sorted(vals, reverse=True)


def test_current_config_returns_dict():
    assert isinstance(get_current_config(), dict)


def test_log_summary_structure():
    result = get_log_summary(days=2)
    for key in ("total_log_lines", "vp_rejections", "signal_denials", "errors", "warnings"):
        assert key in result
    assert isinstance(result["vp_examples"], list)
```

- [ ] **Step 2: Run — verify they fail**

```bash
.venv/Scripts/pytest tests/test_analytics_tools.py -v 2>&1 | head -10
```

Expected: `ModuleNotFoundError: No module named 'app.analytics.tools'`

- [ ] **Step 3: Implement `app/analytics/tools.py`**

```python
from __future__ import annotations

from collections import defaultdict

from app.analytics.loader import (
    load_all_trades,
    load_backtest_summaries,
    load_config,
    load_log_events,
)


def get_performance_summary() -> dict:
    trades = load_all_trades()
    exits = [t for t in trades if t["type"] == "EXIT" and t["realized_pnl"] is not None]
    if not exits:
        return {"error": "no closed trades found"}
    winners = [t for t in exits if t["realized_pnl"] > 0]
    losers  = [t for t in exits if t["realized_pnl"] < 0]
    net_pnl      = sum(t["realized_pnl"] for t in exits)
    gross_profit = sum(t["realized_pnl"] for t in winners)
    gross_loss   = sum(t["realized_pnl"] for t in losers)
    win_rate      = len(winners) / len(exits)
    profit_factor = abs(gross_profit / gross_loss) if gross_loss else None
    # Running max drawdown from a $50k starting balance
    equity = 50_000.0
    peak   = equity
    max_dd = 0.0
    for t in sorted(exits, key=lambda x: x["ts"]):
        equity += t["realized_pnl"]
        peak    = max(peak, equity)
        max_dd  = min(max_dd, equity - peak)
    return {
        "total_trades": len(exits),
        "winners":      len(winners),
        "losers":       len(losers),
        "win_rate":     round(win_rate, 4),
        "net_pnl":      round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss":   round(gross_loss, 2),
        "profit_factor": round(profit_factor, 3) if profit_factor else None,
        "expectancy":   round(net_pnl / len(exits), 2),
        "avg_winner":   round(gross_profit / len(winners), 2) if winners else 0.0,
        "avg_loser":    round(gross_loss   / len(losers),  2) if losers  else 0.0,
        "max_drawdown": round(max_dd, 2),
    }


def get_killzone_breakdown() -> dict:
    trades = load_all_trades()
    exits  = [t for t in trades if t["type"] == "EXIT" and t["realized_pnl"] is not None]
    kz_map: dict[str, list[float]] = defaultdict(list)
    for t in exits:
        kz_map[t["killzone"] or "unknown"].append(t["realized_pnl"])
    result = {}
    for kz, pnls in sorted(kz_map.items()):
        winners = [p for p in pnls if p > 0]
        result[kz] = {
            "trades":   len(pnls),
            "win_rate": round(len(winners) / len(pnls), 4),
            "net_pnl":  round(sum(pnls), 2),
            "avg_pnl":  round(sum(pnls) / len(pnls), 2),
        }
    return result


def get_recent_trades(limit: int = 50) -> list[dict]:
    trades = load_all_trades()
    exits  = [t for t in trades if t["type"] == "EXIT"]
    return sorted(exits, key=lambda x: x["ts"], reverse=True)[:limit]


def get_backtest_runs(sort_by: str = "profit_factor", limit: int = 10) -> list[dict]:
    summaries = load_backtest_summaries()
    valid = [s for s in summaries if s.get(sort_by) is not None]
    valid.sort(key=lambda x: x.get(sort_by) or 0, reverse=True)
    return valid[:limit]


def get_current_config() -> dict:
    return load_config()


def get_log_summary(days: int = 2) -> dict:
    events = load_log_events(days=days)
    msg = lambda e: e.get("message", "").lower()
    vp      = [e for e in events if "vp_filter" in msg(e) or "value area" in msg(e)]
    denied  = [e for e in events if "denied" in msg(e) or "max_contracts" in msg(e) or "locked" in msg(e)]
    errors  = [e for e in events if e.get("level") == "ERROR"]
    warnings = [e for e in events if e.get("level") == "WARNING"]
    return {
        "total_log_lines": len(events),
        "vp_rejections":   len(vp),
        "vp_examples":     [e["message"] for e in vp[:3]],
        "signal_denials":  len(denied),
        "denial_examples": [e["message"] for e in denied[:3]],
        "errors":          len(errors),
        "error_examples":  [e["message"] for e in errors[:3]],
        "warnings":        len(warnings),
    }
```

- [ ] **Step 4: Run — verify tests pass**

```bash
.venv/Scripts/pytest tests/test_analytics_tools.py -v
```

Expected: all tests pass

- [ ] **Step 5: Commit**

```bash
git add app/analytics/tools.py tests/test_analytics_tools.py
git commit -m "feat: analytics tool functions for Claude agentic loop"
```

---

## Task 4: Advisor (agentic loop)

**Files:**
- Create: `app/analytics/advisor.py`

No automated test — the advisor requires a live Anthropic API key. Manual verification in Task 5.

- [ ] **Step 1: Implement `app/analytics/advisor.py`**

```python
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator

import anthropic

from app.analytics import tools as _tools

_TOOL_DEFS: list[dict] = [
    {
        "name": "get_performance_summary",
        "description": (
            "Overall live-trading performance across all sessions: total trades, "
            "win rate, net P&L, profit factor, expectancy, avg winner/loser, max drawdown."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_killzone_breakdown",
        "description": (
            "Per-killzone breakdown: trade count, win rate, net P&L, avg P&L. "
            "Killzones: london, ny_am, ny_pm, london_ny, asia."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_recent_trades",
        "description": "Most recent closed trades: timestamp, side, killzone, P&L.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of trades to return. Default 50.",
                    "default": 50,
                }
            },
            "required": [],
        },
    },
    {
        "name": "get_backtest_runs",
        "description": (
            "Top historical backtest runs. Each includes params (r_multiple, swing_lookback, "
            "body_atr_multiple, stop_buffer, etc.) and metrics (net_pnl, profit_factor, "
            "win_rate, expectancy, per-killzone breakdown)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {
                    "type": "string",
                    "enum": ["profit_factor", "net_pnl", "win_rate", "expectancy"],
                    "description": "Metric to rank by. Default profit_factor.",
                    "default": "profit_factor",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results. Default 10.",
                    "default": 10,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_current_config",
        "description": "Current bot_config.json: instrument, strategy params, killzones, contracts, entry mode.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_log_summary",
        "description": (
            "Recent log summary: VP filter rejection count, signal denial count, "
            "error/warning counts, with example messages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Days of logs to include. Default 2.",
                    "default": 2,
                }
            },
            "required": [],
        },
    },
]

_TOOL_FN_MAP = {
    "get_performance_summary": _tools.get_performance_summary,
    "get_killzone_breakdown":  _tools.get_killzone_breakdown,
    "get_recent_trades":       _tools.get_recent_trades,
    "get_backtest_runs":       _tools.get_backtest_runs,
    "get_current_config":      _tools.get_current_config,
    "get_log_summary":         _tools.get_log_summary,
}

_SYSTEM = (
    "You are a trading strategy advisor for a TopstepX Micro Gold (MGC) futures bot. "
    "The strategy uses sweep + displacement + Fair Value Gap (FVG) signals, active only "
    "during configured kill zones (london, ny_am, ny_pm, london_ny, asia). "
    "Use the available tools to investigate live performance and backtest history before "
    "drawing conclusions. "
    "Provide specific, actionable recommendations — reference exact bot_config.json param "
    "names and concrete numbers from the data. "
    "Limit your final response to 5 bullet points or fewer."
)


async def run_advisor(question: str | None = None) -> AsyncIterator[str]:
    """
    Agentic loop. Yields SSE-formatted strings.
    Caps at 10 tool rounds to bound API spend (~$0.10–0.20 per session).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        yield _sse("error", {"message": "ANTHROPIC_API_KEY not set in .env"})
        return

    client = anthropic.AsyncAnthropic(api_key=api_key)
    user_msg = question or (
        "Analyze my bot's live performance and backtest history. "
        "Recommend specific parameter changes to improve profitability."
    )
    messages: list[dict] = [{"role": "user", "content": user_msg}]

    for _ in range(10):
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=_SYSTEM,
            tools=_TOOL_DEFS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    yield _sse("text_delta", {"delta": block.text})
            yield _sse("done", {})
            return

        # stop_reason == "tool_use": execute all tool calls then loop
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            yield _sse("tool_call", {"name": block.name, "input": block.input})
            fn = _TOOL_FN_MAP.get(block.name)
            try:
                kwargs = block.input if isinstance(block.input, dict) else {}
                result = fn(**kwargs) if fn else {"error": f"unknown tool: {block.name}"}
            except Exception as exc:
                result = {"error": str(exc)}
            yield _sse("tool_result", {"name": block.name, "preview": json.dumps(result)[:300]})
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(result),
            })

        if not tool_results:
            yield _sse("done", {})
            return
        messages.append({"role": "user", "content": tool_results})

    yield _sse("error", {"message": "Exceeded 10 tool rounds — partial results shown above."})


def _sse(event_type: str, data: dict) -> str:
    return f"data: {json.dumps({'type': event_type, **data})}\n\n"
```

- [ ] **Step 2: Verify import works**

```bash
.venv/Scripts/python -c "from app.analytics.advisor import run_advisor; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add app/analytics/advisor.py
git commit -m "feat: analytics Claude advisor agentic loop with SSE streaming"
```

---

## Task 5: API routes

**Files:**
- Modify: `app/api/server.py`

The two new routes go inside `build_app()`, following the exact same pattern as the existing routes there. Add `AskClaudeRequest` alongside the other Pydantic models near the top of the file (after `ForceSignalRequest`). Also add `StreamingResponse` to the fastapi imports if not present (check: it's already imported from `fastapi.responses`).

- [ ] **Step 1: Add `AskClaudeRequest` Pydantic model**

In `app/api/server.py`, find the `ForceSignalRequest` class (around line 86) and add after it:

```python
class AskClaudeRequest(BaseModel):
    question: str | None = None
```

- [ ] **Step 2: Add two routes inside `build_app()`**

Find the line `@app.get("/api/forming/status")` inside `build_app()`. Add the two new routes immediately after the `get_forming_status` function (before `@app.post("/api/test-trade")`):

```python
    @app.get("/api/analytics/stats")
    async def get_analytics_stats() -> JSONResponse:
        """Pre-computed stats for the analytics dashboard. No Claude involved."""
        from app.analytics.tools import (
            get_killzone_breakdown,
            get_performance_summary,
            get_recent_trades,
        )
        return JSONResponse({
            "performance": get_performance_summary(),
            "killzones": get_killzone_breakdown(),
            "recent_trades": get_recent_trades(limit=50),
        })

    @app.post("/api/analytics/ask-claude")
    async def ask_claude(req: AskClaudeRequest) -> StreamingResponse:
        """SSE stream of the agentic Claude advisor session."""
        from app.analytics.advisor import run_advisor

        async def stream():
            async for chunk in run_advisor(req.question):
                yield chunk

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
```

- [ ] **Step 3: Verify the routes are importable**

```bash
.venv/Scripts/python -c "from app.api.server import build_app; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Manually test `GET /api/analytics/stats`**

Start the bot (`python -m app.main` or via dev.ps1), then:

```bash
curl http://localhost:5174/api/analytics/stats
```

Expected: JSON with `performance`, `killzones`, `recent_trades` keys.

- [ ] **Step 5: Commit**

```bash
git add app/api/server.py
git commit -m "feat: add /api/analytics/stats and /api/analytics/ask-claude routes"
```

---

## Task 6: Frontend routing and nav link

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Header.tsx`

The app uses a simple `window.location.pathname` guard — the same pattern as `/backtests`. The Analytics link goes in `Header.tsx` as a plain `<a>` tag, matching the Backtests link style.

- [ ] **Step 1: Add `/analytics` path guard to `App.tsx`**

In `frontend/src/App.tsx`, find:

```typescript
  if (path.startsWith('/backtests')) return <BacktestsPage />
```

Add immediately after it:

```typescript
  if (path.startsWith('/analytics')) {
    const { AnalyticsPage } = require('./pages/Analytics')
    return <AnalyticsPage />
  }
```

Note: use a dynamic `require` here to match the lazy import style. Alternatively use a static import at the top of the file:

```typescript
import { AnalyticsPage } from './pages/Analytics'
```

Add that import at the top alongside the other imports, and replace the inline require with:

```typescript
  if (path.startsWith('/analytics')) return <AnalyticsPage />
```

- [ ] **Step 2: Add Analytics link to `Header.tsx`**

In `frontend/src/components/Header.tsx`, find the Backtests link:

```typescript
        <a
          href="/backtests"
          target="_blank"
          rel="noreferrer"
          className="text-dim hover:text-ink text-xs tracking-widest uppercase ml-2"
          title="Open backtests page in a new window"
        >
          Backtests &#x29C9;
        </a>
```

Add immediately before it:

```typescript
        <a
          href="/analytics"
          target="_blank"
          rel="noreferrer"
          className="text-dim hover:text-ink text-xs tracking-widest uppercase ml-2"
          title="Open analytics and Claude advisor"
        >
          Analytics &#x29C9;
        </a>
```

- [ ] **Step 3: Create the stub `Analytics.tsx` page so the import resolves**

Create `frontend/src/pages/Analytics.tsx` with just enough to compile:

```typescript
export function AnalyticsPage() {
  return (
    <div className="min-h-screen bg-bg p-6">
      <p className="text-ink font-mono">Analytics loading...</p>
    </div>
  )
}
```

- [ ] **Step 4: Build and verify no TypeScript errors**

```bash
cd frontend
npm run build 2>&1 | tail -5
```

Expected: `✓ built in ...` with no errors.

- [ ] **Step 5: Open `/analytics` in browser, verify it renders**

Navigate to `http://localhost:5173/analytics` (or open from the header link). Expected: "Analytics loading..." text on a black background.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/components/Header.tsx frontend/src/pages/Analytics.tsx
git commit -m "feat: add /analytics route and nav link"
```

---

## Task 7: StatsRow component

**Files:**
- Create: `frontend/src/components/StatsRow.tsx`

- [ ] **Step 1: Create `frontend/src/components/StatsRow.tsx`**

```typescript
interface PerformanceStats {
  total_trades: number
  winners?: number
  losers?: number
  win_rate: number
  net_pnl: number
  profit_factor: number | null
  expectancy: number
  max_drawdown: number
  error?: string
}

interface Props {
  perf: PerformanceStats | null
}

function StatCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-panel border border-border px-4 py-3">
      <div className="text-[9px] tracking-[0.25em] text-dim uppercase mb-1">{label}</div>
      <div className="text-xl font-mono text-ink tabular-nums">{value}</div>
      {sub && <div className="text-[9px] text-dim mt-0.5">{sub}</div>}
    </div>
  )
}

export function StatsRow({ perf }: Props) {
  if (!perf || perf.error) {
    return (
      <div className="grid grid-cols-5 gap-px bg-border border border-border">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="bg-panel px-4 py-3 h-16 animate-pulse" />
        ))}
      </div>
    )
  }

  const pnlColor = perf.net_pnl >= 0 ? 'text-accent' : 'text-danger'
  const ddColor  = perf.max_drawdown < -500 ? 'text-danger' : 'text-ink'

  return (
    <div className="grid grid-cols-5 gap-px bg-border border border-border">
      <StatCard
        label="Net P&L"
        value={`${perf.net_pnl >= 0 ? '+' : ''}$${perf.net_pnl.toFixed(0)}`}
        sub="all sessions"
      />
      <StatCard
        label="Win Rate"
        value={`${(perf.win_rate * 100).toFixed(1)}%`}
        sub={`${perf.winners ?? '?'} / ${perf.total_trades} trades`}
      />
      <StatCard
        label="Profit Factor"
        value={perf.profit_factor != null ? perf.profit_factor.toFixed(2) : '—'}
        sub="gross win / loss"
      />
      <StatCard
        label="Expectancy"
        value={`${perf.expectancy >= 0 ? '+' : ''}$${perf.expectancy.toFixed(1)}`}
        sub="per trade"
      />
      <StatCard
        label="Max Drawdown"
        value={`$${perf.max_drawdown.toFixed(0)}`}
        sub="from peak"
      />
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/StatsRow.tsx
git commit -m "feat: StatsRow analytics component"
```

---

## Task 8: KillzoneTable component

**Files:**
- Create: `frontend/src/components/KillzoneTable.tsx`

- [ ] **Step 1: Create `frontend/src/components/KillzoneTable.tsx`**

```typescript
interface KzStats {
  trades: number
  win_rate: number
  net_pnl: number
  avg_pnl: number
}

interface Props {
  killzones: Record<string, KzStats> | null
}

const KZ_LABEL: Record<string, string> = {
  london:    'London',
  ny_am:     'NY AM',
  ny_pm:     'NY PM',
  london_ny: 'London/NY',
  asia:      'Asia',
  unknown:   'Unknown',
}

export function KillzoneTable({ killzones }: Props) {
  if (!killzones) {
    return (
      <div className="bg-panel border border-border px-4 py-3 h-24 animate-pulse" />
    )
  }

  const entries = Object.entries(killzones).filter(([, v]) => v.trades > 0)

  if (entries.length === 0) {
    return (
      <div className="bg-panel border border-border px-4 py-3">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">By Killzone</span>
        <p className="text-dim text-xs mt-2">No trade data yet.</p>
      </div>
    )
  }

  return (
    <div className="bg-panel border border-border">
      <div className="px-4 py-2 border-b border-border">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">By Killzone</span>
      </div>
      <div className="grid gap-px bg-border" style={{ gridTemplateColumns: `repeat(${entries.length}, 1fr)` }}>
        {entries.map(([kz, stats]) => {
          const winPct = (stats.win_rate * 100).toFixed(1)
          const pnlColor = stats.net_pnl >= 0 ? 'text-accent' : 'text-danger'
          const rateColor = stats.win_rate >= 0.55 ? 'text-accent' : stats.win_rate >= 0.45 ? 'text-ink' : 'text-warn'
          return (
            <div key={kz} className="bg-panel px-4 py-3 text-center">
              <div className="text-[9px] tracking-[0.2em] text-dim uppercase mb-1">
                {KZ_LABEL[kz] ?? kz}
              </div>
              <div className={`text-lg font-mono tabular-nums ${rateColor}`}>{winPct}%</div>
              <div className={`text-xs font-mono tabular-nums ${pnlColor} mt-0.5`}>
                {stats.net_pnl >= 0 ? '+' : ''}${stats.net_pnl.toFixed(0)}
              </div>
              <div className="text-[9px] text-dim mt-0.5">{stats.trades} trades</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/KillzoneTable.tsx
git commit -m "feat: KillzoneTable analytics component"
```

---

## Task 9: TradesTable component

**Files:**
- Create: `frontend/src/components/TradesTable.tsx`

- [ ] **Step 1: Create `frontend/src/components/TradesTable.tsx`**

```typescript
interface Trade {
  ts: string
  side: string
  killzone: string | null
  fill_price: number | null
  size: number | null
  realized_pnl: number | null
}

interface Props {
  trades: Trade[]
}

const PT = 'America/Los_Angeles'

function fmtTime(ts: string): string {
  try {
    return new Date(ts).toLocaleTimeString('en-US', {
      timeZone: PT,
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    })
  } catch {
    return ts.slice(11, 16)
  }
}

function fmtDate(ts: string): string {
  try {
    return new Date(ts).toLocaleDateString('en-US', {
      timeZone: PT,
      month: '2-digit',
      day: '2-digit',
    })
  } catch {
    return ts.slice(0, 10)
  }
}

export function TradesTable({ trades }: Props) {
  if (trades.length === 0) {
    return (
      <div className="bg-panel border border-border px-4 py-3">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Recent Trades</span>
        <p className="text-dim text-xs mt-2">No closed trades yet.</p>
      </div>
    )
  }

  return (
    <div className="bg-panel border border-border">
      <div className="px-4 py-2 border-b border-border">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Recent Trades</span>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="border-b border-border">
              <th className="px-4 py-2 text-left text-[9px] tracking-[0.2em] text-dim uppercase">Date</th>
              <th className="px-4 py-2 text-left text-[9px] tracking-[0.2em] text-dim uppercase">Time (PT)</th>
              <th className="px-4 py-2 text-left text-[9px] tracking-[0.2em] text-dim uppercase">Side</th>
              <th className="px-4 py-2 text-left text-[9px] tracking-[0.2em] text-dim uppercase">Zone</th>
              <th className="px-4 py-2 text-right text-[9px] tracking-[0.2em] text-dim uppercase">Size</th>
              <th className="px-4 py-2 text-right text-[9px] tracking-[0.2em] text-dim uppercase">P&L</th>
            </tr>
          </thead>
          <tbody>
            {trades.slice(0, 50).map((t, i) => {
              const pnl = t.realized_pnl ?? 0
              const pnlColor = pnl >= 0 ? 'text-accent' : 'text-danger'
              const sideColor = t.side === 'long' ? 'text-accent' : 'text-danger'
              return (
                <tr key={i} className="border-b border-border/40 hover:bg-border/20">
                  <td className="px-4 py-1.5 text-dim">{fmtDate(t.ts)}</td>
                  <td className="px-4 py-1.5 text-dim tabular-nums">{fmtTime(t.ts)}</td>
                  <td className={`px-4 py-1.5 uppercase ${sideColor}`}>{t.side}</td>
                  <td className="px-4 py-1.5 text-dim uppercase">{t.killzone ?? '—'}</td>
                  <td className="px-4 py-1.5 text-right text-ink tabular-nums">{t.size ?? '—'}</td>
                  <td className={`px-4 py-1.5 text-right tabular-nums ${pnlColor}`}>
                    {pnl >= 0 ? '+' : ''}${pnl.toFixed(0)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/TradesTable.tsx
git commit -m "feat: TradesTable analytics component"
```

---

## Task 10: ClaudeAdvisor component

**Files:**
- Create: `frontend/src/components/ClaudeAdvisor.tsx`

This is the most complex component. It manages four states (idle → investigating → responding → done/error) and consumes a POST SSE stream via `fetch` + `ReadableStream` (not `EventSource`, which only supports GET).

- [ ] **Step 1: Create `frontend/src/components/ClaudeAdvisor.tsx`**

```typescript
import { useState, useRef } from 'react'

type Phase = 'idle' | 'investigating' | 'responding' | 'done' | 'error'

interface ToolEvent {
  name: string
  done: boolean
}

export function ClaudeAdvisor() {
  const [phase, setPhase]         = useState<Phase>('idle')
  const [question, setQuestion]   = useState('')
  const [toolLog, setToolLog]     = useState<ToolEvent[]>([])
  const [response, setResponse]   = useState('')
  const [errorMsg, setErrorMsg]   = useState('')
  const abortRef = useRef<AbortController | null>(null)

  async function handleAsk() {
    setPhase('investigating')
    setToolLog([])
    setResponse('')
    setErrorMsg('')

    const abort = new AbortController()
    abortRef.current = abort

    try {
      const res = await fetch('/api/analytics/ask-claude', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: question.trim() || null }),
        signal: abort.signal,
      })

      if (!res.body) {
        setPhase('error')
        setErrorMsg('No response body from server.')
        return
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // SSE events are delimited by double newlines
        const parts = buffer.split('\n\n')
        buffer = parts.pop() ?? ''

        for (const part of parts) {
          for (const line of part.split('\n')) {
            if (!line.startsWith('data: ')) continue
            let event: Record<string, unknown>
            try {
              event = JSON.parse(line.slice(6))
            } catch {
              continue
            }

            if (event.type === 'tool_call') {
              setPhase('investigating')
              setToolLog(prev => [...prev, { name: event.name as string, done: false }])
            } else if (event.type === 'tool_result') {
              setToolLog(prev =>
                prev.map(t => t.name === event.name ? { ...t, done: true } : t)
              )
            } else if (event.type === 'text_delta') {
              setPhase('responding')
              setResponse(prev => prev + (event.delta as string))
            } else if (event.type === 'done') {
              setPhase('done')
            } else if (event.type === 'error') {
              setPhase('error')
              setErrorMsg(event.message as string)
            }
          }
        }
      }
      // Only set done if we didn't already transition to error via an event
      setPhase(prev => (prev === 'investigating' || prev === 'responding') ? 'done' : prev)
    } catch (err: unknown) {
      if ((err as Error).name === 'AbortError') return
      setPhase('error')
      setErrorMsg(String(err))
    }
  }

  function handleReset() {
    abortRef.current?.abort()
    setPhase('idle')
    setToolLog([])
    setResponse('')
    setErrorMsg('')
  }

  return (
    <div className="bg-panel border border-border">
      {/* Header */}
      <div className="px-4 py-2 border-b border-border flex items-center justify-between">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">◈ Claude Advisor</span>
        {phase !== 'idle' && (
          <button
            onClick={handleReset}
            className="text-[9px] tracking-widest uppercase text-dim hover:text-ink border border-border px-2 py-0.5"
          >
            Reset
          </button>
        )}
      </div>

      <div className="p-4 flex flex-col gap-3">
        {/* Input row */}
        {phase === 'idle' && (
          <div className="flex gap-2">
            <input
              type="text"
              value={question}
              onChange={e => setQuestion(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleAsk()}
              placeholder="e.g. Why is NY AM underperforming? (leave blank for full analysis)"
              className="flex-1 bg-bg border border-border text-ink font-mono text-xs px-3 py-2 placeholder:text-dim focus:outline-none focus:border-accent/50"
            />
            <button
              onClick={handleAsk}
              className="text-[10px] tracking-widest uppercase border border-accent/60 text-accent px-4 py-2 hover:bg-accent/10"
            >
              Ask Claude
            </button>
          </div>
        )}

        {/* Investigation log */}
        {(phase === 'investigating' || phase === 'responding' || phase === 'done') && toolLog.length > 0 && (
          <div className="border border-border/50 bg-bg p-3">
            <div className="text-[9px] tracking-[0.25em] text-dim uppercase mb-2">
              {phase === 'investigating' ? 'Investigating...' : 'Investigation complete'}
            </div>
            <div className="flex flex-col gap-1">
              {toolLog.map((t, i) => (
                <div key={i} className="flex items-center gap-2 text-[10px] font-mono">
                  <span className={t.done ? 'text-accent' : 'text-warn animate-pulse'}>▶</span>
                  <span className="text-ink">{t.name}()</span>
                  <span className="text-dim">{t.done ? '· done' : '· running...'}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Streaming response */}
        {(phase === 'responding' || phase === 'done') && response && (
          <div className="border border-border/50 bg-bg p-3">
            <div className="text-[9px] tracking-[0.25em] text-dim uppercase mb-2">Recommendation</div>
            <pre className="text-ink text-xs font-mono whitespace-pre-wrap leading-relaxed">
              {response}
              {phase === 'responding' && <span className="animate-pulse">▌</span>}
            </pre>
          </div>
        )}

        {/* Done: ask again */}
        {phase === 'done' && (
          <button
            onClick={handleReset}
            className="self-start text-[9px] tracking-widest uppercase border border-border text-dim px-3 py-1 hover:text-ink hover:border-ink"
          >
            Ask Again
          </button>
        )}

        {/* Error */}
        {phase === 'error' && (
          <div className="border border-danger/40 bg-bg p-3">
            <div className="text-[9px] tracking-[0.25em] text-danger uppercase mb-1">Error</div>
            <p className="text-danger text-xs font-mono">{errorMsg}</p>
            <button
              onClick={handleReset}
              className="mt-2 text-[9px] tracking-widest uppercase border border-danger/40 text-danger/70 px-3 py-1 hover:bg-danger/10"
            >
              Retry
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Build — verify no TypeScript errors**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

Expected: `✓ built in ...`

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/ClaudeAdvisor.tsx
git commit -m "feat: ClaudeAdvisor component with SSE streaming and tool call log"
```

---

## Task 11: Analytics page (assemble everything)

**Files:**
- Modify: `frontend/src/pages/Analytics.tsx`

Replace the stub from Task 6 with the full implementation that fetches stats and renders all components.

- [ ] **Step 1: Replace `frontend/src/pages/Analytics.tsx`**

```typescript
import { useEffect, useState } from 'react'
import { StatsRow } from '../components/StatsRow'
import { KillzoneTable } from '../components/KillzoneTable'
import { TradesTable } from '../components/TradesTable'
import { ClaudeAdvisor } from '../components/ClaudeAdvisor'

interface KzStats {
  trades: number
  win_rate: number
  net_pnl: number
  avg_pnl: number
}

interface AnalyticsStats {
  performance: {
    total_trades: number
    winners?: number
    losers?: number
    win_rate: number
    net_pnl: number
    profit_factor: number | null
    expectancy: number
    max_drawdown: number
    error?: string
  }
  killzones: Record<string, KzStats>
  recent_trades: Array<{
    ts: string
    side: string
    killzone: string | null
    fill_price: number | null
    size: number | null
    realized_pnl: number | null
  }>
}

export function AnalyticsPage() {
  const [stats, setStats] = useState<AnalyticsStats | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/analytics/stats')
      .then(r => r.json())
      .then(setStats)
      .catch(e => setLoadErr(String(e)))
  }, [])

  return (
    <div className="min-h-screen bg-bg">
      {/* Nav bar */}
      <header className="border-b border-border px-6 py-4 flex items-center justify-between">
        <span className="text-xs tracking-[0.4em] text-dim">TOPSTEP-BOT · ANALYTICS</span>
        <div className="flex items-center gap-4">
          <a
            href="/"
            className="text-dim hover:text-ink text-xs tracking-widest uppercase"
          >
            ← Live
          </a>
        </div>
      </header>

      <main className="p-6 flex flex-col gap-6 max-w-[1400px] mx-auto">
        {loadErr && (
          <div className="border border-danger/40 bg-panel px-4 py-3 text-danger text-xs font-mono">
            Failed to load stats: {loadErr}
          </div>
        )}

        <StatsRow perf={stats?.performance ?? null} />
        <KillzoneTable killzones={stats?.killzones ?? null} />
        <TradesTable trades={stats?.recent_trades ?? []} />
        <ClaudeAdvisor />
      </main>
    </div>
  )
}
```

- [ ] **Step 2: Build — verify no TypeScript errors**

```bash
cd frontend && npm run build 2>&1 | tail -5
```

Expected: `✓ built in ...`

- [ ] **Step 3: Verify the analytics page in browser**

Start the bot. Open `http://localhost:5173/analytics` (or click Analytics in the header).

Verify:
- Stat cards populate with real numbers from trade CSVs
- Killzone grid shows London/NY AM data
- Trades table shows recent exits
- "Ask Claude" button is visible

- [ ] **Step 4: Add `ANTHROPIC_API_KEY` to `.env` and test Claude advisor**

In `.env`, add:
```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

(Get key from console.anthropic.com → API Keys)

Restart the bot to pick up the new env var. Click "Ask Claude". Verify:
- Tool call log appears (get_performance_summary, get_killzone_breakdown, etc.)
- Each line shows `· done` after it completes
- Claude's recommendation streams in as text

- [ ] **Step 5: Run full test suite — confirm nothing broken**

```bash
cd C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot
.venv/Scripts/pytest tests/ -v 2>&1 | tail -20
```

Expected: all existing tests pass + new analytics tests pass

- [ ] **Step 6: Final commit**

```bash
git add frontend/src/pages/Analytics.tsx
git commit -m "feat: analytics page — assembles stats, killzone, trades, Claude advisor"
```
