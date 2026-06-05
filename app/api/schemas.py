"""Pydantic request models for the dashboard API.

Extracted from server.py to keep the route module focused on handlers. These
are the request bodies for the POST endpoints (backtest, databento fetch,
random search, notes, force-signal, ask-claude)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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
    partial_profit_r: str | None = None
    enforce_risk_limits: bool = True  # False = disable MLL/DLL/DPL for exploration


class DatabentoBarsRequest(BaseModel):
    start: str       # "YYYY-MM-DD"
    end: str         # "YYYY-MM-DD"
    symbol: str      # "MGC" — mapped to GC.c.0 for Databento
    dry_run: bool = False


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
