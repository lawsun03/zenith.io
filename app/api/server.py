"""
FastAPI dashboard server.

Mounted alongside the bot in main.py. Read-only by design: the
dashboard observes, it never controls. (A future kill-switch endpoint
would need confirmation via typed-instrument-name to avoid 2am
disasters.)

Endpoints:
  GET  /api/status              Snapshot of risk state + last reconcile
  GET  /api/signals             Recent signals
  GET  /api/fills                Recent fills
  GET  /api/reconciles           Recent reconcile reports
  WS   /api/stream               Live event stream
  GET  /                         Static React app (single file)

CORS is permissive (localhost only matters here — bot runs locally
per Topstep rules). If you ever expose this beyond localhost, lock
the origins down.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, TYPE_CHECKING

import csv
import io
import random
import re
import subprocess
import os
import sys
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.bot_config import BotConfig, load_bot_config, save_bot_config
from app.execution.reconciler import Reconciler
from app.risk.state import RiskState
from app.strategy.composer import Signal
from app.strategy.killzone import in_killzone, killzones_from_names

from .journal import Journal, _decimal_to_str

if TYPE_CHECKING:
    from app.sync.outbox import Outbox


class BacktestRequest(BaseModel):
    bars_path: str | None = None      # default: bars_<INSTRUMENT>.csv (or fetched)
    instrument: str | None = None     # default: current config
    timeframe: str | None = None      # default: current config
    label: str | None = None
    starting_balance: str = "50000"
    start_date: str | None = None     # "YYYY-MM-DD"
    end_date: str | None = None       # "YYYY-MM-DD"
    # Per-run overrides. When provided, we write a temporary config
    # for the backtest subprocess; the live bot's bot_config.json is untouched.
    strategy: dict[str, Any] | None = None
    enabled_killzones: list[str] | None = None


class RandomSearchRequest(BaseModel):
    count_per_timeframe: int = 15
    timeframes: list[str] = ["1min", "5min"]
    start_date: str
    end_date: str
    concurrency: int = 2          # how many subprocesses at once
    label_prefix: str = "rs"
    seed: int | None = None       # set for reproducible searches


class NoteRequest(BaseModel):
    note: str = ""


class ForceSignalRequest(BaseModel):
    side: str = "long"          # "long" or "short"
    entry: str                  # price as string, e.g. "4720.0"
    stop_distance: str = "2.0"  # points from entry
    r_multiple: str = "2.0"


class AskClaudeRequest(BaseModel):
    question: str | None = None


def _random_strategy_params(rng: random.Random | None = None) -> dict[str, Any]:
    """
    Sample a strategy param set from sensible-but-wide ranges. These
    ranges intentionally exclude pathological extremes (e.g. r_multiple=10
    is technically allowed but virtually never wins a real backtest).

    Pass a seeded random.Random instance for reproducible searches.
    """
    r = rng or random
    return {
        "swing_lookback":           r.randint(1, 8),
        "min_penetration":          f"{r.uniform(0.05, 0.50):.2f}",
        "multi_bar_window":         r.randint(1, 8),
        "atr_period":               r.randint(5, 30),
        "body_atr_multiple":        f"{r.uniform(0.5, 2.5):.2f}",
        "min_body_to_range_ratio":  f"{r.uniform(0.30, 0.85):.2f}",
        "min_absolute_body":        f"{r.uniform(0.5, 3.0):.2f}",
        "displacement_window_bars": r.randint(2, 10),
        "stop_buffer":              f"{r.uniform(0.10, 1.00):.2f}",
        "r_multiple":               f"{r.uniform(1.0, 4.0):.2f}",
    }


# In-memory tracking for random searches. Lost on restart — these are
# meant to be tail-anchored to the result-file polling that the frontend
# already does, not a durable job system.
_active_searches: dict[str, dict[str, Any]] = {}

log = logging.getLogger(__name__)


def _build_vp_state(vp: Any, cfg: "BotConfig") -> dict:
    """Serialize VP filter state for the setup checklist."""
    enabled = cfg.strategy.vp_enabled
    if vp is None or not enabled:
        return {"enabled": enabled, "profile_available": False}

    prior = vp._prior
    if prior is None:
        return {
            "enabled": True,
            "profile_available": False,
            "tolerance": str(cfg.strategy.vp_filter_tolerance),
        }

    return {
        "enabled": True,
        "profile_available": True,
        "poc":  str(prior.poc),
        "vah":  str(prior.vah),
        "val":  str(prior.val),
        "hvns": [str(h) for h in prior.hvns],
        "tolerance": str(cfg.strategy.vp_filter_tolerance),
        "min_target_r": str(cfg.strategy.vp_min_target_r),
        "session_date": str(prior.session_date),
    }


def build_app(
    risk_state: RiskState,
    reconciler: Reconciler,
    journal: Journal,
    static_dir: Path | None = None,
    outbox: "Outbox | None" = None,
    bot_config_path: Path | None = None,
    effective_instrument: str = "MGC",
    effective_timeframes: list[str] | None = None,
    restart_event: "asyncio.Event | None" = None,
    mode: str = "paper",
    broker: Any = None,
    engine: Any = None,
    runner_factory: Any = None,
) -> FastAPI:
    """
    Construct the FastAPI app. Dependencies are passed in (not module
    globals) so the test suite can build an isolated app per test.
    """
    app = FastAPI(title="topstep-bot dashboard", version="0.1")

    # Permissive CORS — bot runs on localhost. Doesn't matter in
    # production because the bot can't be on a VPS anyway.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "PATCH", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    _bot_config_path = bot_config_path or Path("bot_config.json")
    _effective_timeframes = effective_timeframes or ["1min"]
    _mode = mode
    _broker = broker
    _engine = engine
    _runner_factory = runner_factory

    # ------------------------------------------------------------------
    # /api/status — the four numbers that matter, plus context
    # ------------------------------------------------------------------

    @app.get("/api/status")
    async def status() -> JSONResponse:
        cfg = risk_state.config
        # buffer_to_dll relies on daily_pnl, which is signed.
        return JSONResponse(_decimal_to_str({
            "now": datetime.now(timezone.utc),
            "account": {
                "type": cfg.account_type,
                "size": cfg.account_size,
                "starting_balance": cfg.starting_balance,
                "daily_loss_limit": cfg.daily_loss_limit,
                "max_contracts": cfg.max_contracts,
                "soft_buffer": cfg.soft_buffer,
            },
            "equity": {
                "current": risk_state.current_equity,
                "high_water": risk_state.equity_high_water,
                "realized_balance": risk_state.realized_balance,
            },
            "limits": {
                "mll_floor": risk_state.mll_floor,
                "buffer_to_mll": risk_state.buffer_to_mll,
                "buffer_to_dll": risk_state.buffer_to_dll,
                "daily_pnl": risk_state.daily_pnl,
                "open_contracts": risk_state.open_contracts,
                "mll_locked_at_starting_balance":
                    risk_state.mll_locked_at_starting_balance,
            },
            "lockout": (
                {
                    "code": risk_state.locked_out.code,
                    "message": risk_state.locked_out.message,
                }
                if risk_state.locked_out is not None
                else None
            ),
            "last_reconcile": (
                _serialize_report(reconciler.last_report)
                if reconciler.last_report is not None
                else None
            ),
            "sync": (
                outbox.stats()
                if outbox is not None
                else None
            ),
        }))

    # ------------------------------------------------------------------
    # Journal endpoints — three rolling buffers
    # ------------------------------------------------------------------

    @app.get("/api/signals")
    async def signals(limit: int = 50) -> JSONResponse:
        return JSONResponse({"items": await journal.recent_signals(limit)})

    @app.get("/api/fills")
    async def fills(limit: int = 50) -> JSONResponse:
        return JSONResponse({"items": await journal.recent_fills(limit)})

    @app.get("/api/reconciles")
    async def reconciles(limit: int = 20) -> JSONResponse:
        return JSONResponse({"items": await journal.recent_reconciles(limit)})

    @app.get("/api/setup_state")
    async def setup_state() -> JSONResponse:
        """
        Live snapshot of signal-formation conditions for the dashboard checklist.
        Shows killzone, pending sweeps, displacement candidate, cooldown, and ATR.
        """
        if _engine is None:
            return JSONResponse({"available": False, "instruments": []})

        cfg = load_bot_config(_bot_config_path)
        kz_list = killzones_from_names(cfg.enabled_killzones)
        now = datetime.now(timezone.utc)
        active_kz = in_killzone(now, kz_list)

        instruments: list[dict] = []
        for instrument, runner in _engine.runners.items():
            awaiting = runner.composer.awaiting  # list[SweepEvent]
            disp_candidate = runner.displacement.peek_displacement()
            atr = runner.displacement.atr
            cooldown = runner.composer._cooldown_remaining

            instruments.append({
                "instrument": instrument,
                "killzone": {
                    "active": active_kz is not None,
                    "name": active_kz.name if active_kz else None,
                },
                "sweeps_pending": [
                    {
                        "side": s.side,
                        "pattern": s.pattern,
                        "swept_price": str(s.swept_swing.price),
                        "sweep_extreme": str(s.sweep_extreme),
                    }
                    for s in awaiting
                ],
                "displacement_candidate": (
                    {
                        "side": disp_candidate[0],
                        "bar_ts": disp_candidate[2].ts.isoformat(),
                    }
                    if disp_candidate is not None else None
                ),
                "cooldown_bars_remaining": cooldown,
                "atr": str(atr) if atr is not None else None,
                "vp": _build_vp_state(runner.vp, cfg),
            })

        return JSONResponse({"available": True, "instruments": instruments})

    @app.get("/api/export/trades.csv")
    async def export_trades() -> StreamingResponse:
        signals = await journal.recent_signals(10_000)
        fills   = await journal.recent_fills(10_000)

        rows: list[dict] = []
        for e in signals:
            p = e["payload"]
            rows.append({
                "timestamp":    e["ts"],
                "kind":         "signal",
                "instrument":   p.get("instrument", ""),
                "side":         p.get("side", ""),
                "entry":        p.get("entry", ""),
                "stop":         p.get("stop", ""),
                "target":       p.get("target", ""),
                "killzone":     p.get("killzone", ""),
                "placed":       p.get("outcome", {}).get("placed", ""),
                "size":         p.get("outcome", {}).get("allowed_size", ""),
                "reason":       p.get("outcome", {}).get("reason", ""),
                "fill_price":   "",
                "is_entry":     "",
                "realized_pnl": "",
                "rationale":    p.get("rationale", ""),
            })
        for e in fills:
            p = e["payload"]
            rows.append({
                "timestamp":    e["ts"],
                "kind":         "fill",
                "instrument":   p.get("instrument", ""),
                "side":         p.get("side", ""),
                "entry":        "",
                "stop":         "",
                "target":       "",
                "killzone":     "",
                "placed":       "",
                "size":         p.get("size", ""),
                "reason":       "",
                "fill_price":   p.get("fill_price", ""),
                "is_entry":     p.get("is_entry", ""),
                "realized_pnl": p.get("realized_pnl_delta", ""),
                "rationale":    "",
            })

        rows.sort(key=lambda r: r["timestamp"])

        buf = io.StringIO()
        fields = ["timestamp","kind","instrument","side","entry","stop","target",
                  "killzone","placed","size","reason","fill_price","is_entry",
                  "realized_pnl","rationale"]
        w = csv.DictWriter(buf, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=trades.csv"},
        )

    # ------------------------------------------------------------------
    # /api/config — read and write mutable bot configuration
    # ------------------------------------------------------------------

    @app.get("/api/config")
    async def get_config() -> JSONResponse:
        cfg = load_bot_config(_bot_config_path)
        return JSONResponse({
            "instrument": cfg.instrument or effective_instrument,
            "timeframes": cfg.timeframes or _effective_timeframes,
            "replay_delay_ms": cfg.replay_delay_ms,
            "replay_start_delay_s": cfg.replay_start_delay_s,
            "account_name": cfg.account_name,
            "entry_mode": cfg.entry_mode,
            "contracts": cfg.contracts,
            "risk_per_trade_pct": float(cfg.risk_per_trade_pct),
            "partial_profit_r": float(cfg.partial_profit_r),
            "enabled_killzones": cfg.enabled_killzones,
            "mode": _mode,
            "strategy": _decimal_to_str(cfg.strategy.model_dump()),
        })

    @app.patch("/api/config")
    async def patch_config(body: BotConfig) -> JSONResponse:
        save_bot_config(body, _bot_config_path)
        # Hot-apply entry_mode to the running broker so the next signal
        # uses the new mode without requiring a restart.
        if _broker is not None and hasattr(_broker, "entry_mode"):
            _broker.entry_mode = body.entry_mode
        if _broker is not None and hasattr(_broker, "partial_profit_r"):
            _broker.partial_profit_r = body.partial_profit_r
        if _engine is not None:
            _engine.contracts = body.contracts
            _engine.risk_per_trade_pct = body.risk_per_trade_pct
            _engine.strategy_cfg = body.strategy
        return JSONResponse({
            "instrument": body.instrument,
            "timeframes": body.timeframes,
            "replay_delay_ms": body.replay_delay_ms,
            "replay_start_delay_s": body.replay_start_delay_s,
            "account_name": body.account_name,
            "entry_mode": body.entry_mode,
            "contracts": body.contracts,
            "risk_per_trade_pct": float(body.risk_per_trade_pct),
            "partial_profit_r": float(body.partial_profit_r),
            "enabled_killzones": body.enabled_killzones,
            "mode": _mode,
            "strategy": _decimal_to_str(body.strategy.model_dump()),
        })

    @app.get("/api/accounts")
    async def list_accounts() -> JSONResponse:
        """List TopstepX accounts. Only useful in live mode."""
        if _mode != "live":
            return JSONResponse({"accounts": []})
        try:
            from project_x_py import ProjectX  # type: ignore
            async with ProjectX.from_env() as client:
                await client.authenticate()
                accounts = await client.list_accounts()
            return JSONResponse({
                "accounts": [
                    {
                        "name": a.name,
                        "balance": a.balance,
                        "can_trade": a.canTrade,
                        "simulated": a.simulated,
                    }
                    for a in accounts
                ]
            })
        except Exception as e:
            log.warning("Could not list accounts: %s", e)
            return JSONResponse({"accounts": [], "error": str(e)})

    @app.get("/api/bars/availability")
    async def bars_availability(timeframe: str = "5min") -> JSONResponse:
        """Earliest and latest bar timestamps the broker can serve."""
        if _mode != "live" or _broker is None:
            return JSONResponse({
                "earliest": None, "latest": None, "bars": 0,
                "available": False,
                "reason": "Live mode required for availability probe",
            })
        try:
            avail = await _broker.get_data_availability(timeframe=timeframe)
            return JSONResponse({**avail, "available": True, "timeframe": timeframe})
        except Exception as e:
            log.exception("bars/availability failed")
            return JSONResponse({
                "earliest": None, "latest": None, "bars": 0,
                "available": False, "reason": str(e),
            })

    @app.get("/api/bars")
    async def get_bars(limit: int = 500) -> JSONResponse:
        """
        Recent historical bars for chart pre-population. Live mode only.
        Returns up to `limit` bars at the currently-configured timeframe.
        """
        if _mode != "live" or _broker is None:
            return JSONResponse({"bars": []})
        try:
            cfg = load_bot_config(_bot_config_path)
            tf = (cfg.timeframes or _effective_timeframes)[0]
            bars = await _broker.get_historical_bars(timeframe=tf, limit=limit)
            return JSONResponse({"bars": [
                {
                    "time": int(b.ts.timestamp()),
                    "open": float(b.open),
                    "high": float(b.high),
                    "low": float(b.low),
                    "close": float(b.close),
                }
                for b in bars
            ]})
        except Exception as e:
            log.exception("get_bars failed")
            return JSONResponse({"bars": [], "error": str(e)})

    @app.get("/api/vp/profile")
    async def get_vp_profile() -> JSONResponse:
        """Current prior-session volume profile for chart overlay."""
        if _engine is None:
            return JSONResponse(None)
        for runner in _engine.runners.values():
            if runner.vp is not None and runner.vp._prior is not None:
                p = runner.vp._prior
                return JSONResponse({
                    "session_date": p.session_date.isoformat(),
                    "poc": str(p.poc),
                    "vah": str(p.vah),
                    "val": str(p.val),
                    "hvns": [str(h) for h in p.hvns],
                    "total_volume": p.total_volume,
                    "bins": [[str(k), v] for k, v in sorted(p.bins.items())],
                })
        return JSONResponse(None)

    @app.get("/api/forming/status")
    async def get_forming_status() -> JSONResponse:
        """Forming-bar poll state: what the 5-second poll is currently seeing."""
        if _engine is None:
            return JSONResponse(None)
        result = {}
        for instrument, runner in _engine.runners.items():
            peek = runner.displacement.peek_displacement()
            fired_ts = _engine._forming_signal_fired.get(instrument)
            result[instrument] = {
                "has_displacement_candidate": peek is not None,
                "awaiting_sweeps": len(runner.composer.awaiting),
                "last_fired_b2_ts": fired_ts.isoformat() if fired_ts else None,
            }
        return JSONResponse(result)

    @app.get("/api/forming-bar")
    async def get_forming_bar() -> JSONResponse:
        """Return the current partially-closed bar for chart display."""
        if _broker is None:
            return JSONResponse(None)
        get_fb = getattr(_broker, "get_forming_bar", None)
        if get_fb is None:
            return JSONResponse(None)
        cfg = load_bot_config(_bot_config_path)
        tf = (cfg.timeframes or _effective_timeframes)[0]
        bar = await get_fb(tf)
        if bar is None:
            return JSONResponse(None)
        return JSONResponse({
            "time": int(bar.ts.timestamp()),
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
        })

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

    @app.post("/api/test-trade")
    async def test_trade() -> JSONResponse:
        """
        Place a 1-contract long market order and flatten it 30 seconds later.
        Only works in live mode with a connected broker.
        """
        if _mode != "live":
            return JSONResponse({"ok": False, "reason": "only available in live mode"}, status_code=400)
        if _broker is None:
            return JSONResponse({"ok": False, "reason": "broker not available"}, status_code=400)

        async def _run():
            try:
                ok = await _broker.place_market_order("long", size=1)
                log.info("Test trade placed: %s", ok)
                await asyncio.sleep(30)
                instrument = _broker._instruments[0] if _broker._instruments else "MGC"
                await _broker.flatten(instrument)
                log.info("Test trade closed after 30s")
            except Exception:
                log.exception("Test trade error")

        asyncio.create_task(_run())
        return JSONResponse({"ok": True, "message": "1-contract long placed — will flatten in 30s"})

    @app.post("/api/debug/force-signal")
    async def force_signal(body: ForceSignalRequest) -> JSONResponse:
        """
        Inject a synthetic signal directly into the execution engine.
        Tests the exact same path as a real strategy signal:
          pretrade check → broker.place_bracket() → fill event.
        Returns the full outcome so you can see exactly where it fails.
        """
        if _mode != "live":
            return JSONResponse({"ok": False, "reason": "only available in live mode"}, status_code=400)
        if _engine is None:
            return JSONResponse({"ok": False, "reason": "engine not started"}, status_code=400)

        try:
            entry = Decimal(body.entry)
            stop_dist = Decimal(body.stop_distance)
            r = Decimal(body.r_multiple)
        except Exception:
            return JSONResponse({"ok": False, "reason": "invalid price values"}, status_code=400)

        if body.side == "long":
            stop = entry - stop_dist
            target = entry + stop_dist * r
        else:
            stop = entry + stop_dist
            target = entry - stop_dist * r

        signal = Signal(
            instrument=effective_instrument,
            side=body.side,
            entry=entry,
            stop=stop,
            target=target,
            created_at=datetime.now(timezone.utc),
            killzone="DEBUG",
            sweep_pattern="DEBUG",
            sweep_extreme=stop,
            fvg_low=None,
            fvg_high=None,
            rationale=f"DEBUG force-signal: {body.side} entry={entry} stop={stop} target={target}",
        )

        try:
            outcome = await _engine._act_on_signal(signal)
        except Exception as e:
            log.exception("force-signal failed")
            return JSONResponse({"ok": False, "reason": f"engine error: {e}"}, status_code=500)

        return JSONResponse({
            "ok": outcome.placed,
            "placed": outcome.placed,
            "reason": outcome.reason,
            "allowed_size": outcome.allowed_size,
            "broker_order_id": outcome.broker_order_id,
            "signal": {
                "side": signal.side,
                "entry": str(signal.entry),
                "stop": str(signal.stop),
                "target": str(signal.target),
            },
        })

    @app.post("/api/replay/restart")
    async def replay_restart() -> JSONResponse:
        if restart_event is None:
            return JSONResponse({"ok": False, "reason": "not in paper mode"}, status_code=400)
        restart_event.set()
        return JSONResponse({"ok": True})

    @app.post("/api/risk/clear-lockout")
    async def clear_lockout() -> JSONResponse:
        """Manually clear a RECONCILE_DRIFT lockout after the operator has
        reviewed and resolved the position discrepancy."""
        if risk_state.locked_out is None:
            return JSONResponse({"ok": True, "was_locked": False})
        code = risk_state.locked_out.code
        if code not in {"RECONCILE_DRIFT"}:
            return JSONResponse(
                {"ok": False, "reason": f"Cannot manually clear lockout code={code}"},
                status_code=400,
            )
        risk_state.locked_out = None
        log.info("Lockout cleared manually via API.")
        return JSONResponse({"ok": True, "was_locked": True, "cleared_code": code})

    @app.post("/api/restart")
    async def restart_bot() -> JSONResponse:
        """Trigger a process restart. Exits with code 2; dev.ps1 retry loop
        will respawn the bot. Used to apply config changes (account swap,
        timeframe change) that require re-initializing the broker."""
        log.info("Restart triggered via API.")

        async def _exit_soon() -> None:
            # Brief delay so the HTTP response can flush before we die.
            await asyncio.sleep(1.0)
            log.info("Exiting with code 2 for restart.")
            os._exit(2)

        asyncio.create_task(_exit_soon())
        return JSONResponse({"ok": True, "message": "Bot restarting..."})

    @app.post("/api/risk/flatten")
    async def flatten_all() -> JSONResponse:
        """Emergency flatten — close all open positions immediately."""
        if _broker is None:
            return JSONResponse({"ok": False, "reason": "No broker in this mode"}, status_code=400)
        try:
            positions = await _broker.get_positions()
            if not positions:
                # Nothing open — just clear any drift lockout and return.
                if risk_state.locked_out and risk_state.locked_out.code == "RECONCILE_DRIFT":
                    risk_state.locked_out = None
                return JSONResponse({"ok": True, "note": "no open positions"})
            instruments = {p.instrument for p in positions}
            for instrument in instruments:
                await _broker.flatten(instrument)
            if risk_state.locked_out and risk_state.locked_out.code == "RECONCILE_DRIFT":
                risk_state.locked_out = None
            log.info("Emergency flatten triggered via API.")
            return JSONResponse({"ok": True})
        except Exception as e:
            log.exception("Emergency flatten failed")
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

    @app.post("/api/strategy/reload")
    async def strategy_reload() -> JSONResponse:
        """
        Reload bot_config.json and rebuild the strategy runner in place,
        without restarting the bot process. The runner's internal state
        (recent bars, ATR window, swing levels) is reset — next bar
        rebuilds it fresh. Risk state, open positions, and the broker
        connection are untouched.
        """
        if _engine is None or _runner_factory is None:
            return JSONResponse(
                {"ok": False, "reason": "engine/runner factory not wired up"},
                status_code=400,
            )
        try:
            new_cfg = load_bot_config(_bot_config_path)
            instrument = (new_cfg.instrument or effective_instrument).upper()
            new_runner = _runner_factory(
                instrument, new_cfg.strategy, new_cfg.enabled_killzones,
                new_cfg.timeframes[0] if new_cfg.timeframes else "1min",
            )
            _engine.runners = {instrument: new_runner}
            _engine.strategy_cfg = new_cfg.strategy
            if _broker is not None and hasattr(_broker, "entry_mode"):
                _broker.entry_mode = new_cfg.entry_mode
            log.info(
                "Strategy reloaded: instrument=%s killzones=%s entry_mode=%s params=%s",
                instrument, new_cfg.enabled_killzones, new_cfg.entry_mode,
                new_cfg.strategy.model_dump(),
            )
            return JSONResponse({
                "ok": True,
                "instrument": instrument,
                "enabled_killzones": new_cfg.enabled_killzones,
                "entry_mode": new_cfg.entry_mode,
                "strategy": _decimal_to_str(new_cfg.strategy.model_dump()),
            })
        except Exception as e:
            log.exception("strategy/reload failed")
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

    # ------------------------------------------------------------------
    # Backtests — spawn an isolated subprocess, save results to disk
    # ------------------------------------------------------------------

    backtests_dir = Path("backtests")

    bars_cache_dir = Path("bars_cache")

    async def _fetch_bars_to_csv(
        instrument: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> Path:
        """
        Pull bars from TopstepX for [start, end] (inclusive), chunking by 30
        days to stay under any per-request cap, then write them to a CSV
        whose path is returned. Cached by (instrument, tf, start, end).
        """
        if _broker is None:
            raise RuntimeError("broker not available")
        bars_cache_dir.mkdir(parents=True, exist_ok=True)
        cache_key = (
            f"bars_{instrument}_{timeframe}_"
            f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}.csv"
        )
        out_path = bars_cache_dir / cache_key
        if out_path.exists():
            log.info("Using cached bars: %s", out_path)
            return out_path

        all_bars: list[Any] = []
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(days=30), end)
            log.info(
                "Fetching bars %s %s: %s → %s",
                instrument, timeframe,
                chunk_start.isoformat(), chunk_end.isoformat(),
            )
            chunk = await _broker.get_historical_bars(
                timeframe=timeframe,
                limit=200_000,
                start_time=chunk_start,
                end_time=chunk_end,
            )
            all_bars.extend(chunk)
            chunk_start = chunk_end

        # Dedupe by timestamp (chunk boundaries can overlap).
        seen: set[int] = set()
        unique: list[Any] = []
        for b in sorted(all_bars, key=lambda x: x.ts):
            k = int(b.ts.timestamp())
            if k in seen:
                continue
            seen.add(k)
            unique.append(b)

        with out_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
            for b in unique:
                w.writerow([
                    b.ts.isoformat(), b.open, b.high, b.low, b.close, b.volume,
                ])
        log.info("Wrote %d bars to %s", len(unique), out_path)
        return out_path

    @app.post("/api/backtest/run")
    async def run_backtest(req: BacktestRequest) -> JSONResponse:
        cfg = load_bot_config(_bot_config_path)
        instrument = (req.instrument or cfg.instrument or effective_instrument).upper()
        timeframe = req.timeframe or (cfg.timeframes or _effective_timeframes)[0]

        # Resolve bars source: explicit path > date range fetch > default file.
        if req.start_date and req.end_date:
            if _mode != "live" or _broker is None:
                return JSONResponse(
                    {"ok": False, "reason": "date-range fetch requires live mode (need authenticated broker)"},
                    status_code=400,
                )
            try:
                start_dt = datetime.fromisoformat(req.start_date).replace(tzinfo=timezone.utc)
                end_dt = datetime.fromisoformat(req.end_date).replace(tzinfo=timezone.utc)
            except ValueError as e:
                return JSONResponse(
                    {"ok": False, "reason": f"bad date format: {e}"},
                    status_code=400,
                )
            if end_dt <= start_dt:
                return JSONResponse(
                    {"ok": False, "reason": "end_date must be after start_date"},
                    status_code=400,
                )

            # Clamp to TopstepX availability so we don't waste a fetch loop on
            # date ranges where the broker has no data.
            try:
                avail = await _broker.get_data_availability(timeframe=timeframe)
            except Exception:
                avail = None
            if avail and avail.get("earliest"):
                earliest_str = avail["earliest"]
                try:
                    earliest_dt = datetime.fromisoformat(earliest_str)
                    if earliest_dt.tzinfo is None:
                        earliest_dt = earliest_dt.replace(tzinfo=timezone.utc)
                    if start_dt < earliest_dt:
                        return JSONResponse({
                            "ok": False,
                            "reason": (
                                f"start_date {req.start_date} is before TopstepX's earliest "
                                f"available data for {timeframe} ({earliest_dt.date().isoformat()}). "
                                f"Pick a later start date or import bars from another source."
                            ),
                            "earliest_available": earliest_dt.isoformat(),
                        }, status_code=400)
                except Exception:
                    pass

            try:
                cache_path = await _fetch_bars_to_csv(
                    instrument, timeframe, start_dt, end_dt,
                )
                bars_path = str(cache_path)
            except Exception as e:
                log.exception("bars fetch failed")
                return JSONResponse(
                    {"ok": False, "reason": f"bars fetch failed: {e}"},
                    status_code=500,
                )
        else:
            bars_path = req.bars_path or f"./bars_{instrument}.csv"
            if not Path(bars_path).exists():
                return JSONResponse(
                    {"ok": False, "reason": f"bars file not found: {bars_path}"},
                    status_code=400,
                )

        backtests_dir.mkdir(parents=True, exist_ok=True)

        # If the user overrode strategy params for this run, write a temp
        # config so the subprocess picks them up *without* touching the
        # live bot_config.json. Falls back to the live config otherwise.
        config_path_for_run = str(_bot_config_path)
        if req.strategy or req.enabled_killzones is not None:
            try:
                base_cfg = load_bot_config(_bot_config_path).model_dump()
                if req.strategy:
                    merged_strategy = {**base_cfg.get("strategy", {}), **req.strategy}
                    merged_strategy = {
                        k: v for k, v in merged_strategy.items()
                        if v is not None and v != ""
                    }
                    base_cfg["strategy"] = merged_strategy
                if req.enabled_killzones is not None:
                    base_cfg["enabled_killzones"] = req.enabled_killzones
                # Pydantic refuses Decimals in JSON dump output, so coerce.
                tmp_cfg = backtests_dir / f"_config_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}.json"
                tmp_cfg.write_text(json.dumps(base_cfg, default=str, indent=2))
                config_path_for_run = str(tmp_cfg)
            except Exception:
                log.exception("Failed to write per-run config override; using live config")

        # Spawn a fully detached subprocess so the live bot is untouched.
        cmd = [
            sys.executable, "-m", "app.backtest",
            "--config", config_path_for_run,
            "--bars", bars_path,
            "--instrument", instrument,
            "--timeframe", timeframe,
            "--starting-balance", req.starting_balance,
            "--out-dir", str(backtests_dir),
        ]
        if req.label:
            cmd += ["--label", req.label]
        try:
            proc = subprocess.Popen(cmd, cwd=Path.cwd())
        except Exception as e:
            log.exception("Failed to spawn backtest subprocess")
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

        # If we wrote a temp config, schedule its cleanup once the subprocess
        # finishes. Without this, every override-run leaves a stray file
        # behind that the old list endpoint used to misinterpret as a result.
        if config_path_for_run != str(_bot_config_path):
            tmp = Path(config_path_for_run)

            async def _cleanup_when_done():
                while proc.poll() is None:
                    await asyncio.sleep(2)
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass

            asyncio.create_task(_cleanup_when_done())

        return JSONResponse({"ok": True, "pid": proc.pid, "command": cmd})

    async def _run_one_search_iter(
        search_id: str,
        instrument: str,
        timeframe: str,
        bars_path: str,
        strategy: dict[str, Any],
        label: str,
        starting_balance: str,
        sem: asyncio.Semaphore,
    ) -> None:
        """Run one backtest from the search, respecting the concurrency cap."""
        async with sem:
            # Per-run config file with the random params.
            base_cfg = load_bot_config(_bot_config_path).model_dump()
            base_cfg["strategy"] = {
                **base_cfg.get("strategy", {}),
                **{k: v for k, v in strategy.items() if v is not None and v != ""},
            }
            tmp_cfg = backtests_dir / (
                f"_config_{search_id}_{label}_"
                f"{datetime.now(timezone.utc).strftime('%H%M%S_%f')}.json"
            )
            tmp_cfg.write_text(json.dumps(base_cfg, default=str, indent=2))

            cmd = [
                sys.executable, "-m", "app.backtest",
                "--config", str(tmp_cfg),
                "--bars", bars_path,
                "--instrument", instrument,
                "--timeframe", timeframe,
                "--starting-balance", starting_balance,
                "--out-dir", str(backtests_dir),
                "--label", label,
            ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, cwd=str(Path.cwd()),
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
            except Exception:
                log.exception("Search iter spawn failed: %s", label)
            finally:
                # Tidy up the per-run config file so it doesn't pollute the
                # results dir on the next list call.
                try:
                    tmp_cfg.unlink(missing_ok=True)
                except Exception:
                    pass
                state = _active_searches.get(search_id)
                if state:
                    state["completed"] += 1

    @app.post("/api/backtest/random-search")
    async def random_search(req: RandomSearchRequest) -> JSONResponse:
        """
        Generate N random param sets per timeframe and run them as
        backtests in parallel (throttled by `concurrency`). Returns a
        search_id; poll `/api/backtest/random-search/{id}` for progress.
        """
        if _mode != "live" or _broker is None:
            return JSONResponse(
                {"ok": False, "reason": "random search requires live mode (need broker for bar fetch)"},
                status_code=400,
            )
        cfg = load_bot_config(_bot_config_path)
        instrument = (cfg.instrument or effective_instrument).upper()
        try:
            start_dt = datetime.fromisoformat(req.start_date).replace(tzinfo=timezone.utc)
            end_dt = datetime.fromisoformat(req.end_date).replace(tzinfo=timezone.utc)
        except ValueError as e:
            return JSONResponse({"ok": False, "reason": f"bad date: {e}"}, status_code=400)

        # Pre-fetch bars per timeframe so all the subprocesses for that tf
        # share the same cache file (avoid 30 redundant TopstepX fetches).
        bars_paths: dict[str, str] = {}
        for tf in req.timeframes:
            try:
                p = await _fetch_bars_to_csv(instrument, tf, start_dt, end_dt)
                bars_paths[tf] = str(p)
            except Exception as e:
                log.exception("Random search bars fetch failed for %s", tf)
                return JSONResponse(
                    {"ok": False, "reason": f"bars fetch failed for {tf}: {e}"},
                    status_code=500,
                )

        search_id = uuid.uuid4().hex[:8]
        total = req.count_per_timeframe * len(req.timeframes)
        _active_searches[search_id] = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "completed": 0,
            "timeframes": req.timeframes,
            "count_per_timeframe": req.count_per_timeframe,
            "seed": req.seed,
            "labels": [],
            "task": None,
        }

        sem = asyncio.Semaphore(max(1, req.concurrency))

        async def _runner():
            rng = random.Random(req.seed) if req.seed is not None else None
            coros = []
            for tf in req.timeframes:
                for i in range(req.count_per_timeframe):
                    label = f"{req.label_prefix}-{search_id}-{tf}-{i+1:02d}"
                    _active_searches[search_id]["labels"].append(label)
                    strategy = _random_strategy_params(rng)
                    coros.append(_run_one_search_iter(
                        search_id=search_id,
                        instrument=instrument,
                        timeframe=tf,
                        bars_path=bars_paths[tf],
                        strategy=strategy,
                        label=label,
                        starting_balance="50000",
                        sem=sem,
                    ))
            await asyncio.gather(*coros, return_exceptions=True)
            _active_searches[search_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

        _active_searches[search_id]["task"] = asyncio.create_task(_runner())
        return JSONResponse({
            "ok": True,
            "search_id": search_id,
            "total": total,
            "timeframes": req.timeframes,
            "count_per_timeframe": req.count_per_timeframe,
        })

    @app.get("/api/backtest/random-search/{search_id}")
    async def random_search_status(search_id: str) -> JSONResponse:
        state = _active_searches.get(search_id)
        if not state:
            return JSONResponse({"error": "unknown search_id"}, status_code=404)
        return JSONResponse({
            "search_id": search_id,
            "total": state["total"],
            "completed": state["completed"],
            "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"),
            "labels": state["labels"],
            "running": state["task"] is not None and not state["task"].done(),
        })

    @app.get("/api/backtest/list")
    async def list_backtests() -> JSONResponse:
        if not backtests_dir.exists():
            return JSONResponse({"backtests": []})
        items = []
        for f in sorted(backtests_dir.glob("*.json"), reverse=True):
            # Files starting with "_" are per-run config overrides, not results.
            if f.name.startswith("_"):
                continue
            try:
                data = json.loads(f.read_text())
                # A real result must have a stats block. Skip anything else.
                if not isinstance(data.get("stats"), dict):
                    continue
                items.append({
                    "id": data.get("id", f.stem),
                    "label": data.get("label", f.stem),
                    "completed_at": data.get("completed_at"),
                    "instrument": data.get("instrument"),
                    "timeframe": data.get("timeframe"),
                    "bars_processed": data.get("bars_processed"),
                    "stats": data.get("stats", {}),
                    "ending_balance": data.get("ending_balance"),
                    "bookmarked": bool(data.get("bookmarked", False)),
                })
            except Exception:
                continue
        return JSONResponse({"backtests": items})

    @app.get("/api/backtest/{run_id}")
    async def get_backtest(run_id: str) -> JSONResponse:
        # Reject anything that isn't a plain id, anything starting with `_`
        # (those are temp config files), or anything with traversal chars.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", run_id):
            return JSONResponse({"error": "invalid id"}, status_code=400)
        f = backtests_dir / f"{run_id}.json"
        if not f.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(json.loads(f.read_text()))

    @app.delete("/api/backtest/clear-unbookmarked")
    async def clear_unbookmarked() -> JSONResponse:
        """Delete all backtest JSON files that do not have bookmarked=true."""
        deleted = 0
        for f in backtests_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                if not data.get("bookmarked"):
                    f.unlink()
                    deleted += 1
            except Exception:
                pass
        return JSONResponse({"ok": True, "deleted": deleted})

    @app.delete("/api/backtest/{run_id}")
    async def delete_backtest(run_id: str) -> JSONResponse:
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", run_id):
            return JSONResponse({"error": "invalid id"}, status_code=400)
        f = backtests_dir / f"{run_id}.json"
        if f.exists():
            f.unlink()
        return JSONResponse({"ok": True})

    @app.post("/api/backtest/{run_id}/note")
    async def save_backtest_note(run_id: str, body: NoteRequest) -> JSONResponse:
        """Persist a free-text note on a saved backtest run."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", run_id):
            return JSONResponse({"error": "invalid id"}, status_code=400)
        f = backtests_dir / f"{run_id}.json"
        if not f.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            data = json.loads(f.read_text())
            data["note"] = body.note[:4000]
            f.write_text(json.dumps(data, indent=2))
            return JSONResponse({"ok": True})
        except Exception as e:
            log.exception("note save failed")
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

    @app.post("/api/backtest/{run_id}/bookmark")
    async def toggle_bookmark(run_id: str) -> JSONResponse:
        """Flip the bookmark flag on a saved run. Persisted in its JSON file."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", run_id):
            return JSONResponse({"error": "invalid id"}, status_code=400)
        f = backtests_dir / f"{run_id}.json"
        if not f.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        try:
            data = json.loads(f.read_text())
            data["bookmarked"] = not bool(data.get("bookmarked", False))
            f.write_text(json.dumps(data, indent=2))
            return JSONResponse({"ok": True, "bookmarked": data["bookmarked"]})
        except Exception as e:
            log.exception("bookmark toggle failed")
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

    # ------------------------------------------------------------------
    # WebSocket — live event feed
    # ------------------------------------------------------------------

    @app.websocket("/api/stream")
    async def stream(ws: WebSocket) -> None:
        """
        Push every new journal entry to connected clients.

        Protocol:
          - On connect, send a "snapshot" with the latest status.
          - Then push every new entry as it arrives.
          - On client disconnect, unsubscribe.
        """
        await ws.accept()
        q = journal.subscribe()

        try:
            # Initial snapshot — so a fresh client can render before
            # waiting for the next event.
            snapshot = {
                "kind": "snapshot",
                "ts": datetime.now(timezone.utc).isoformat(),
                "payload": json.loads(
                    (await status()).body.decode("utf-8")
                ),
            }
            await ws.send_text(json.dumps(snapshot))

            while True:
                entry = await q.get()
                await ws.send_text(json.dumps({
                    "kind": entry.kind,
                    "ts": entry.ts.isoformat(),
                    "payload": entry.payload,
                }))
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("WebSocket stream error")
        finally:
            journal.unsubscribe(q)

    # ------------------------------------------------------------------
    # Static frontend — single-page React app served from disk
    # ------------------------------------------------------------------

    if static_dir is not None and static_dir.exists():
        index = static_dir / "index.html"

        @app.get("/")
        async def root() -> FileResponse:
            return FileResponse(index)

        # Catch-all for SPA routing (if we ever add client-side routes).
        # MUST come AFTER the /api/* routes — FastAPI matches in order
        # and a top-level catch-all would shadow them.
        @app.get("/{path:path}")
        async def spa_fallback(path: str) -> FileResponse:
            target = static_dir / path
            if target.is_file():
                return FileResponse(target)
            return FileResponse(index)

    return app


def _serialize_report(report: Any) -> dict:
    """Reconcile report → dict for the status endpoint."""
    return _decimal_to_str({
        "ts": report.ts,
        "broker_open_contracts": report.broker_open_contracts,
        "broker_balance": report.broker_balance,
        "internal_open_contracts": report.internal_open_contracts,
        "internal_balance": report.internal_balance,
        "drift_detected": report.drift_detected,
        "drift_kind": report.drift_kind,
        "flattened": report.flattened,
        "notes": report.notes,
    })
