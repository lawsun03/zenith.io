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
from datetime import date, datetime, timedelta, timezone
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

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from app.bot_config import BotConfig, load_bot_config, save_bot_config, strategy_for
from app.execution.reconciler import Reconciler
from app.risk.state import RiskState
from app.strategy.composer import Signal
from app.strategy.killzone import in_killzone, killzones_from_names

from .journal import Journal, _decimal_to_str
from .schemas import (
    AskClaudeRequest,
    BacktestRequest,
    DatabentoBarsRequest,
    ForceSignalRequest,
    NoteRequest,
    RandomSearchRequest,
)

if TYPE_CHECKING:
    from app.sync.outbox import Outbox


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


def _build_htf_state(engine: Any, cfg: "BotConfig") -> dict:
    """Serialize the engine's HTF state for the setup checklist.

    Returns the flag (whether the user enabled the feature), whether the
    tracker is wired (i.e. the REST warm-up succeeded — relevant because
    the gate fails open while None), and the current bias if known.
    """
    s = cfg.strategy
    bias_tracker = getattr(engine, "htf_bias", None) if engine is not None else None
    levels_tracker = getattr(engine, "htf_levels", None) if engine is not None else None
    return {
        "bias_enabled":   s.htf_bias_enabled,
        "bias_ready":     bias_tracker is not None,
        "bias":           bias_tracker.bias() if bias_tracker is not None else None,
        "target_enabled": s.htf_target_enabled,
        "target_ready":   levels_tracker is not None,
        "bias_timeframe": s.htf_bias_timeframe,
    }


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


# Maps the bot's instrument symbol to the Databento continuous front-month
# symbol on GLBX.MDP3 (stype_in="continuous", schema="ohlcv-1m"), as consumed
# by scripts/fetch_bars_databento.py.
_DATABENTO_SYMBOL_MAP: dict[str, str] = {
    "MGC": "GC.c.0",
    "MES": "ES.c.0",
    "MNQ": "NQ.c.0",
}


def _bars_csv_path(symbol: str) -> str:
    """Return the absolute path to the local bars CSV for the given instrument symbol."""
    return str(Path(__file__).resolve().parent.parent.parent / "bars" / f"bars_{symbol.upper()}.csv")


def _csv_cached_through(csv_path: str) -> str | None:
    """Return the YYYY-MM-DD of the last bar in the CSV, or None if absent/empty."""
    p = Path(csv_path)
    if not p.exists():
        return None
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None
            chunk = min(512, size)
            f.seek(-chunk, 2)
            tail = f.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            ts = line.split(",")[0].strip()
            if len(ts) >= 10:
                return ts[:10]
        return None
    except Exception:
        return None


def _csv_cached_from(csv_path: str) -> str | None:
    """Return the YYYY-MM-DD of the first data bar in the CSV, or None if absent/empty."""
    p = Path(csv_path)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ts = line.split(",")[0].strip()
                if ts.lower() in ("ts", "timestamp", "datetime"):
                    continue
                if len(ts) >= 10:
                    return ts[:10]
        return None
    except Exception:
        return None


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
    vp_warmup: Any = None,
    htf_rebuild: Any = None,
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
    _vp_warmup = vp_warmup  # async callable(runner, bot_cfg) — re-warms VP after a reload
    _htf_rebuild = htf_rebuild  # async callable(body: BotConfig) — rebuilds engine HTF trackers after a live config change

    # ------------------------------------------------------------------
    # /api/status — the four numbers that matter, plus context
    # ------------------------------------------------------------------

    def _today_trade_stats() -> dict:
        try:
            from app.analytics.loader import load_all_trades
            today = date.today().isoformat()
            exits = [
                t for t in load_all_trades()
                if t["type"] == "EXIT" and t["realized_pnl"] is not None and t["ts"].startswith(today)
            ]
            n = len(exits)
            wins = sum(1 for t in exits if t["realized_pnl"] > 0)
            return {
                "trades": n,
                "wins": wins,
                "losses": n - wins,
                "win_rate": round(wins / n, 4) if n else None,
                "avg_pnl": round(sum(t["realized_pnl"] for t in exits) / n, 2) if n else None,
            }
        except Exception:
            return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None, "avg_pnl": None}

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
            "daily_trades": _today_trade_stats(),
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

    @app.get("/api/positions")
    async def get_open_positions() -> JSONResponse:
        """Open brackets with entry/stop/target for the dashboard positions panel."""
        if _broker is None:
            return JSONResponse({"positions": []})
        try:
            return JSONResponse({"positions": _broker.open_brackets()})
        except Exception:
            log.exception("get_open_positions failed")
            return JSONResponse({"positions": []})

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
                "htf": _build_htf_state(_engine, cfg),
            })

        return JSONResponse({"available": True, "instruments": instruments})

    @app.get("/api/htf_diagnostic")
    async def htf_diagnostic() -> JSONResponse:
        """Detailed HTF tracker state — the exact swings the bias decision saw.

        Use this to verify the bias verdict against the chart. The bias logic
        compares strictly the LAST TWO confirmed swing highs and the LAST TWO
        confirmed swing lows (see HTFBiasTracker.rebuild). If those two pairs
        don't both rise (or both fall), the verdict is neutral — even when
        the broader trend looks decisive by eye.
        """
        cfg = load_bot_config(_bot_config_path)
        s = cfg.strategy
        base = {
            "bias_enabled":   s.htf_bias_enabled,
            "bias_timeframe": s.htf_bias_timeframe,
            "bias_lookback":  s.htf_bias_lookback,
        }
        if _engine is None:
            return JSONResponse({**base, "available": False, "reason": "engine not wired"})
        bias_tracker = getattr(_engine, "htf_bias", None)
        if bias_tracker is None:
            return JSONResponse({
                **base,
                "available": False,
                "reason":
                    "HTF bias tracker not active — either the flag is off in "
                    "the running strategy_cfg, or the startup REST warm-up failed",
            })
        return JSONResponse({**base, "available": True, **bias_tracker.diagnostics()})

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
            "instruments": cfg.instruments or [],
            "timeframes": cfg.timeframes or _effective_timeframes,
            "replay_delay_ms": cfg.replay_delay_ms,
            "replay_start_delay_s": cfg.replay_start_delay_s,
            "account_name": cfg.account_name,
            "entry_mode": cfg.entry_mode,
            "forming_bar_entries": cfg.forming_bar_entries,
            "contracts": cfg.contracts,
            "risk_per_trade_pct": float(cfg.risk_per_trade_pct),
            "partial_profit_r": float(cfg.partial_profit_r),
            "max_entry_slippage_frac": float(cfg.max_entry_slippage_frac),
            "enabled_killzones": cfg.enabled_killzones,
            "signal_instrument": cfg.signal_instrument,
            "mode": _mode,
            "strategy": _decimal_to_str(cfg.strategy.model_dump()),
            "strategy_overrides": cfg.strategy_overrides,
            "emergency_stop_distance": {k: float(v) for k, v in cfg.emergency_stop_distance.items()},
            "emergency_target_r": float(cfg.emergency_target_r),
            "naked_grace_seconds": cfg.naked_grace_seconds,
        })

    async def _hot_apply(body: BotConfig) -> None:
        """Save config to disk and hot-apply all fields to the running bot."""
        save_bot_config(body, _bot_config_path)
        if _broker is not None and hasattr(_broker, "entry_mode"):
            _broker.entry_mode = body.entry_mode
        if _broker is not None and hasattr(_broker, "_account_name"):
            _broker._account_name = body.account_name
        if _broker is not None and hasattr(_broker, "partial_profit_r"):
            _broker.partial_profit_r = body.partial_profit_r
        if _broker is not None and hasattr(_broker, "max_entry_slippage_frac"):
            _broker.max_entry_slippage_frac = body.max_entry_slippage_frac
        if _engine is not None:
            _engine.contracts = body.contracts
            _engine.risk_per_trade_pct = body.risk_per_trade_pct
            _engine.forming_bar_entries = body.forming_bar_entries
            _engine.strategy_cfg = body.strategy
            _engine.commission_per_contract = Decimal(str(body.commission_per_contract))
            _engine.max_contracts_override = body.max_contracts_override
            _engine._htf_warned = False
            _engine.flatten_enabled = body.flatten_enabled
            _engine.flatten_time_ct = body.flatten_time_ct
            _engine.entry_cutoff_time_ct = body.entry_cutoff_time_ct
        if _htf_rebuild is not None:
            try:
                await _htf_rebuild(body)
            except Exception:
                log.exception("_hot_apply: HTF tracker rebuild failed")
        reconciler.config.naked_grace_seconds = body.naked_grace_seconds
        reconciler.config.emergency_stop_distance = dict(body.emergency_stop_distance)
        reconciler.config.emergency_target_r = body.emergency_target_r

    @app.patch("/api/config")
    async def patch_config(request: Request) -> JSONResponse:
        updates = await request.json()
        # Merge onto the current saved config so partial patches (e.g. only
        # changing account_name) don't clobber every other field with defaults.
        current = load_bot_config(_bot_config_path)
        base = current.model_dump()
        if "strategy" in updates and isinstance(updates["strategy"], dict):
            base["strategy"].update(updates["strategy"])
            updates = {k: v for k, v in updates.items() if k != "strategy"}
        base.update(updates)
        body = BotConfig.model_validate(base)
        # Validate overrides eagerly: a typo'd field or bad value must 400 here,
        # not blow up later inside a runner rebuild.
        try:
            for inst in body.strategy_overrides:
                strategy_for(body, inst)
        except Exception as e:
            return JSONResponse(
                {"error": f"invalid strategy_overrides: {e}"}, status_code=400,
            )
        await _hot_apply(body)
        return JSONResponse({
            "instrument": body.instrument,
            "timeframes": body.timeframes,
            "replay_delay_ms": body.replay_delay_ms,
            "replay_start_delay_s": body.replay_start_delay_s,
            "account_name": body.account_name,
            "entry_mode": body.entry_mode,
            "forming_bar_entries": body.forming_bar_entries,
            "contracts": body.contracts,
            "risk_per_trade_pct": float(body.risk_per_trade_pct),
            "partial_profit_r": float(body.partial_profit_r),
            "max_entry_slippage_frac": float(body.max_entry_slippage_frac),
            "enabled_killzones": body.enabled_killzones,
            "signal_instrument": body.signal_instrument,
            "mode": _mode,
            "strategy": _decimal_to_str(body.strategy.model_dump()),
            "strategy_overrides": body.strategy_overrides,
            "emergency_stop_distance": {k: float(v) for k, v in body.emergency_stop_distance.items()},
            "emergency_target_r": float(body.emergency_target_r),
            "naked_grace_seconds": body.naked_grace_seconds,
        })

    # ------------------------------------------------------------------
    # Config presets — named snapshots of bot_config.json
    # ------------------------------------------------------------------

    _PRESETS_FILE = Path(__file__).parent.parent.parent / "config_presets.json"

    def _load_presets() -> list:
        if not _PRESETS_FILE.exists():
            return []
        try:
            return json.loads(_PRESETS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_presets(presets: list) -> None:
        _PRESETS_FILE.write_text(json.dumps(presets, indent=2), encoding="utf-8")

    @app.get("/api/config/presets")
    async def get_presets() -> JSONResponse:
        return JSONResponse([{"name": p["name"], "saved_at": p.get("saved_at", "")} for p in _load_presets()])

    @app.post("/api/config/presets")
    async def save_preset(request: Request) -> JSONResponse:
        body = await request.json()
        name = (body.get("name") or "").strip()
        if not name:
            return JSONResponse({"error": "name required"}, status_code=400)
        cfg = load_bot_config(_bot_config_path)
        presets = [p for p in _load_presets() if p["name"] != name]
        presets.append({
            "name": name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "config": json.loads(cfg.model_dump_json()),
        })
        _save_presets(presets)
        return JSONResponse({"ok": True})

    @app.delete("/api/config/presets/{name}")
    async def delete_preset(name: str) -> JSONResponse:
        presets = [p for p in _load_presets() if p["name"] != name]
        _save_presets(presets)
        return JSONResponse({"ok": True})

    @app.post("/api/config/presets/{name}/apply")
    async def apply_preset(name: str) -> JSONResponse:
        preset = next((p for p in _load_presets() if p["name"] == name), None)
        if not preset:
            return JSONResponse({"error": "preset not found"}, status_code=404)
        body = BotConfig.model_validate(preset["config"])
        await _hot_apply(body)
        return JSONResponse({"ok": True, "name": name})

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
    async def get_bars(limit: int = 500, timeframe: str = "", instrument: str = "") -> JSONResponse:
        """
        Recent historical bars for chart pre-population. Live mode only.
        Returns up to `limit` bars at the requested timeframe, or the
        bot's configured timeframe if not specified.
        """
        if _mode != "live" or _broker is None:
            return JSONResponse({"bars": []})
        try:
            cfg = load_bot_config(_bot_config_path)
            tf = timeframe or (cfg.timeframes or _effective_timeframes)[0]
            exec_instr = instrument or cfg.instrument or effective_instrument
            _days = {"4h": 60, "1d": 90}.get(tf, 5)
            bars = await _broker.get_historical_bars(
                timeframe=tf, limit=limit, days=_days, instrument=exec_instr,
            )
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
    async def get_forming_bar(instrument: str = "") -> JSONResponse:
        """Return the current partially-closed bar for chart display."""
        if _broker is None:
            return JSONResponse(None)
        get_fb = getattr(_broker, "get_forming_bar", None)
        if get_fb is None:
            return JSONResponse(None)
        cfg = load_bot_config(_bot_config_path)
        tf = (cfg.timeframes or _effective_timeframes)[0]
        bar = await get_fb(tf, instrument)
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

    # ── Trade Analysis endpoints ─────────────────────────────────────────────

    def _scan_trade_analysis_dates() -> list[dict]:
        trades_dir = Path("trades")
        analysis_dir = Path("trade_analysis")
        dates: set[str] = set()
        for prefix in ("trades", "excursions", "rejections"):
            for f in trades_dir.glob(f"{prefix}_*.csv"):
                m = re.match(rf"{prefix}_(\d{{4}}-\d{{2}}-\d{{2}})\.csv", f.name)
                if m:
                    dates.add(m.group(1))
        results = []
        for dt in sorted(dates, reverse=True):
            trades_path = trades_dir / f"trades_{dt}.csv"
            exc_path = trades_dir / f"excursions_{dt}.csv"
            rej_path = trades_dir / f"rejections_{dt}.csv"
            report_path = analysis_dir / f"{dt}.md"
            trade_count = wins = losses = 0
            net_pnl = 0.0
            if trades_path.exists():
                with open(trades_path, newline="", encoding="utf-8") as fh:
                    for row in csv.DictReader(fh):
                        if row.get("type") == "EXIT":
                            trade_count += 1
                            pnl = float(row.get("realized_pnl") or 0)
                            net_pnl += pnl
                            if pnl > 0:
                                wins += 1
                            else:
                                losses += 1
            results.append({
                "date": dt,
                "has_trades": trades_path.exists(),
                "has_excursions": exc_path.exists(),
                "has_rejections": rej_path.exists(),
                "has_report": report_path.exists(),
                "trade_count": trade_count,
                "win_count": wins,
                "loss_count": losses,
                "net_pnl": round(net_pnl, 2),
            })
        return results

    @app.get("/api/trade-analysis")
    async def list_trade_analysis() -> JSONResponse:
        return JSONResponse(_scan_trade_analysis_dates())

    @app.get("/api/trade-analysis/{date}/report")
    async def get_trade_analysis_report(date: str) -> JSONResponse:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return JSONResponse({"error": "invalid date"}, status_code=400)
        path = Path("trade_analysis") / f"{date}.md"
        if not path.exists():
            return JSONResponse({"error": "report not found"}, status_code=404)
        return JSONResponse({"date": date, "content": path.read_text(encoding="utf-8")})

    @app.get("/api/trade-analysis/{date}/csv/{csv_type}")
    async def get_trade_analysis_csv(date: str, csv_type: str) -> StreamingResponse:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            from fastapi import HTTPException
            raise HTTPException(400, "invalid date")
        if csv_type not in ("trades", "excursions", "rejections"):
            from fastapi import HTTPException
            raise HTTPException(400, "invalid csv_type")
        path = Path("trades") / f"{csv_type}_{date}.csv"
        if not path.exists():
            from fastapi import HTTPException
            raise HTTPException(404, "file not found")
        return StreamingResponse(
            open(path, "rb"),  # noqa: SIM115
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{csv_type}_{date}.csv"'},
        )

    @app.post("/api/trade-analysis/{date}/run")
    async def run_trade_analysis(date: str) -> StreamingResponse:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            return JSONResponse({"error": "invalid date"}, status_code=400)

        async def _stream() -> AsyncIterator[str]:
            import anthropic as _anthropic

            def _sse_ta(event_type: str, data: dict) -> str:
                return f"data: {json.dumps({'type': event_type, **data})}\n\n"

            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                yield _sse_ta("error", {"message": "ANTHROPIC_API_KEY not set in .env"})
                return

            trades_dir = Path("trades")

            def _read_csv(name: str) -> str:
                p = trades_dir / f"{name}_{date}.csv"
                if not p.exists():
                    return ""
                return p.read_text(encoding="utf-8")

            trades_csv = _read_csv("trades")
            excursions_csv = _read_csv("excursions")
            rejections_csv = _read_csv("rejections")

            if not trades_csv and not excursions_csv:
                yield _sse_ta("error", {"message": f"No trade data found for {date}"})
                return

            prompt = f"""Analyze the bot's trades for {date}. Produce a structured markdown report with:

1. **Headline** — net P&L, trade count, win rate, dominant failure mode
2. **Session Structure** — time windows visible in the data
3. **Per-Trade Breakdown** — table with: ET time, symbol, side, grade/score, slippage, contracts, outcome, P&L
4. **Pattern Summary** — grade distribution, win rate by grade, slippage stats, excursion outcomes
5. **Dominant Failure Modes** — top 2 with mechanistic explanation
6. **Prioritized Actions** — 3-4 concrete, evidence-backed fixes

**Trades CSV (all fills):**
```
{trades_csv[:8000]}
```

**Excursions CSV (MFE/MAE/outcome per trade):**
```
{excursions_csv[:4000]}
```

**Rejections CSV (filtered signals):**
```
{rejections_csv[:3000]}
```

Notes:
- Log timestamps are system-local (CDT = UTC-5); convert to ET for the report
- `slippage` column in trades CSV = fill - signal_entry (positive = adverse for longs, favorable for shorts — check sign per side)
- EXIT rows carry realized_pnl; ENTRY rows have pnl=0
- excursions `outcome`: win/stopped/stopped_then_target/partial_win
- Grade A-/A/A+ = passes filter; B/C/D/F = would previously have been rejected
"""

            client = _anthropic.AsyncAnthropic(api_key=api_key)
            full_text = ""

            yield _sse_ta("status", {"message": f"Generating analysis for {date}…"})

            async with client.messages.stream(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                async for chunk in stream.text_stream:
                    full_text += chunk
                    yield _sse_ta("text_delta", {"delta": chunk})

            # Save report
            analysis_dir = Path("trade_analysis")
            analysis_dir.mkdir(exist_ok=True)
            out_path = analysis_dir / f"{date}.md"
            header = f"# Trade Analysis — {date}\n\n"
            out_path.write_text(header + full_text, encoding="utf-8")
            yield _sse_ta("done", {"saved_to": str(out_path)})

        from collections.abc import AsyncIterator
        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── End Trade Analysis endpoints ──────────────────────────────────────────

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
            instruments = new_cfg.instruments or [
                (new_cfg.instrument or effective_instrument).upper()
            ]
            instrument = instruments[0]
            new_runners = [
                _runner_factory(
                    inst, strategy_for(new_cfg, inst), new_cfg.enabled_killzones,
                    new_cfg.timeframes[0] if new_cfg.timeframes else "1min",
                    signal_instrument=new_cfg.signal_instrument if len(instruments) == 1 else None,
                )
                for inst in instruments
            ]
            new_runner = new_runners[0]
            _engine.runners = {r.instrument: r for r in new_runners}
            _engine._bar_router = {
                new_runner.signal_instrument: new_runner.instrument
            } if len(instruments) == 1 and new_runner.signal_instrument and new_runner.signal_instrument != new_runner.instrument else {}
            _engine.strategy_cfg = new_cfg.strategy
            # Reset HTF fail-loud guard so a fresh rebuild can re-warn if needed.
            _engine._htf_warned = False
            if _broker is not None and hasattr(_broker, "entry_mode"):
                _broker.entry_mode = new_cfg.entry_mode
            # Hot-apply HTF trackers to match the reloaded config flags.
            if _htf_rebuild is not None:
                try:
                    await _htf_rebuild(new_cfg)
                except Exception:
                    log.exception("strategy/reload: HTF tracker rebuild failed")
            # Rebuilding the runner created a fresh, empty VolumeProfileTracker.
            # Re-warm it from historical bars so the VP filter/target stay active —
            # otherwise a reload silently disables VP until the next session boundary.
            vp_warmed = None
            if _vp_warmup is not None and new_cfg.strategy.vp_enabled:
                try:
                    for r in new_runners:
                        await _vp_warmup(r, new_cfg)
                    vp_warmed = all(bool(r.vp and r.vp.has_prior_profile()) for r in new_runners)
                except Exception:
                    log.exception("strategy/reload: VP re-warm failed — filter inactive until session boundary")
                    vp_warmed = False
            log.info(
                "Strategy reloaded: instrument=%s killzones=%s entry_mode=%s vp_warmed=%s params=%s",
                instrument, new_cfg.enabled_killzones, new_cfg.entry_mode,
                vp_warmed, new_cfg.strategy.model_dump(),
            )
            return JSONResponse({
                "ok": True,
                "instrument": instrument,
                "enabled_killzones": new_cfg.enabled_killzones,
                "entry_mode": new_cfg.entry_mode,
                "vp_warmed": vp_warmed,
                "strategy": _decimal_to_str(new_cfg.strategy.model_dump()),
            })
        except Exception as e:
            log.exception("strategy/reload failed")
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

    # ------------------------------------------------------------------
    # Backtests — spawn an isolated subprocess, save results to disk
    # ------------------------------------------------------------------

    backtests_dir = Path("backtests")

    bars_cache_dir = Path("bars")

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

    @app.get("/api/databento/symbols")
    async def databento_symbols() -> JSONResponse:
        """Return the instrument symbols supported for Databento fetch."""
        return JSONResponse({"symbols": sorted(_DATABENTO_SYMBOL_MAP.keys())})

    @app.post("/api/databento/fetch")
    async def databento_fetch(req: DatabentoBarsRequest) -> JSONResponse:
        """
        Fetch Databento bars for the requested date range and symbol.
        Skips download if local CSV already covers req.end (cache hit).
        With dry_run=True, returns cost estimate without downloading.
        """
        api_key = os.environ.get("DATABENTO_API_KEY")
        if not api_key:
            return JSONResponse({"ok": False, "reason": "DATABENTO_API_KEY not set in .env"})

        db_symbol = _DATABENTO_SYMBOL_MAP.get(req.symbol.upper())
        if db_symbol is None:
            return JSONResponse(
                {"ok": False, "reason": f"Unsupported symbol: {req.symbol!r}. Supported: {list(_DATABENTO_SYMBOL_MAP)}"},
                status_code=400,
            )

        csv_path = _bars_csv_path(req.symbol)
        cached_through = _csv_cached_through(csv_path)
        cached_from = _csv_cached_from(csv_path)

        # Cache hit: skip download only if the end is covered AND no historical gap.
        needs_backfill = cached_from is not None and cached_from > req.start
        if not req.dry_run and cached_through is not None and cached_through >= req.end and not needs_backfill:
            return JSONResponse({
                "ok": True,
                "days_fetched": 0,
                "cached_through": cached_through,
                "cost_estimate": 0.0,
            })

        script = Path(__file__).resolve().parent.parent.parent / "scripts" / "fetch_bars_databento.py"
        cmd = [
            sys.executable, str(script),
            "--symbol", db_symbol,
            "--start", req.start,
            "--end", req.end,
            "--out", csv_path,
        ]
        if req.dry_run:
            cmd.append("--estimate-only")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            return JSONResponse({"ok": False, "reason": "Databento fetch timed out (>120s)"}, status_code=500)
        except Exception as e:
            return JSONResponse({"ok": False, "reason": str(e)}, status_code=500)

        if result.returncode != 0:
            return JSONResponse({
                "ok": False,
                "reason": result.stderr.strip() or f"Script exited with code {result.returncode}",
            }, status_code=500)

        cost_match = re.search(r'\$(\d+\.\d+)', result.stdout)
        cost = float(cost_match.group(1)) if cost_match else 0.0

        try:
            days_fetched = (date.fromisoformat(req.end) - date.fromisoformat(req.start)).days + 1
        except ValueError:
            days_fetched = 0

        return JSONResponse({
            "ok": True,
            "days_fetched": 0 if req.dry_run else days_fetched,
            "cached_through": _csv_cached_through(csv_path) or req.end,
            "cost_estimate": cost,
        })

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
            bars_path = req.bars_path or f"bars/bars_{instrument}.csv"
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
        if req.strategy or req.enabled_killzones is not None or req.partial_profit_r is not None:
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
                if req.partial_profit_r is not None:
                    base_cfg["partial_profit_r"] = req.partial_profit_r
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
        if req.start_date:
            cmd += ["--start-date", req.start_date]
        if req.end_date:
            cmd += ["--end-date", req.end_date]
        if not req.enforce_risk_limits:
            cmd += ["--no-risk-limits"]
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
                    "start_date": data.get("start_date"),
                    "end_date": data.get("end_date"),
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
    # A/B TP variants — serve ab_tp_variants.json for the backtests page
    # ------------------------------------------------------------------

    _AB_VARIANTS_FILE = Path(__file__).parent.parent.parent / "ab_tp_variants.json"

    @app.get("/api/ab-variants")
    async def get_ab_variants():
        if not _AB_VARIANTS_FILE.exists():
            return JSONResponse([])
        try:
            data = json.loads(_AB_VARIANTS_FILE.read_text(encoding="utf-8"))
            return JSONResponse(data.get("variants", []))
        except Exception as e:
            log.warning("Failed to read ab_tp_variants.json: %s", e)
            return JSONResponse([], status_code=500)

    # ------------------------------------------------------------------
    # Todos — backlog items from todos/ directory
    # ------------------------------------------------------------------

    _TODOS_DIR = Path(__file__).parent.parent.parent / "todos"

    @app.get("/api/todos")
    async def get_todos():
        items = []
        if _TODOS_DIR.exists():
            for f in sorted(_TODOS_DIR.glob("*.md")):
                try:
                    text = f.read_text(encoding="utf-8")
                    # Split frontmatter (key: value lines) from body (after ---)
                    parts = text.split("---", 1)
                    meta_text = parts[0].strip()
                    body = parts[1].strip() if len(parts) > 1 else ""
                    meta: dict = {}
                    for line in meta_text.splitlines():
                        if ":" in line:
                            k, _, v = line.partition(":")
                            meta[k.strip()] = v.strip()
                    items.append({
                        "id": f.stem,
                        "title": meta.get("title", f.stem),
                        "priority": meta.get("priority", "medium"),
                        "status": meta.get("status", "open"),
                        "category": meta.get("category", ""),
                        "created": meta.get("created", ""),
                        "body": body,
                    })
                except Exception:
                    log.warning("Failed to parse todo file: %s", f.name)
        priority_order = {"high": 0, "medium": 1, "low": 2}
        status_order = {"in-progress": 0, "open": 1, "done": 2}
        items.sort(key=lambda x: (status_order.get(x["status"], 9), priority_order.get(x["priority"], 9)))
        return items

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
        "naked_instruments": getattr(report, "naked_instruments", []),
        "notes": report.notes,
    })
