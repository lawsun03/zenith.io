"""
Application config.

Read from environment at startup. Fail loudly if anything required is
missing — better to crash on launch than discover a missing API key
mid-session.

Env vars consumed:
  TOPSTEP_BOT_MODE             "paper" | "live"  (required)
  TOPSTEP_BOT_INSTRUMENT       e.g. "MGC"        (required)
  TOPSTEP_BOT_TIMEFRAMES       comma list, e.g. "1min,5min" (default: "1min")
  TOPSTEP_BOT_SOFT_BUFFER      decimal dollars   (default: 500)
  TOPSTEP_BOT_RECONCILE_SECS   reconciler interval (default: 30)
  TOPSTEP_BOT_LOG_LEVEL        DEBUG/INFO/WARN/ERROR (default: INFO)
  TOPSTEP_BOT_PAPER_BARS       path to a CSV of bars for paper mode
                               (optional; if absent, paper mode runs
                                idle waiting for inject_bar() calls)

Live mode also needs (read by project_x_py from env):
  PROJECT_X_API_KEY
  PROJECT_X_USERNAME
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal

from app.bot_config import BotConfig, load_bot_config

Mode = Literal["paper", "live"]


@dataclass(frozen=True)
class AppConfig:
    mode: Mode
    instrument: str
    timeframes: list[str]
    soft_buffer: Decimal
    reconcile_interval_seconds: float
    log_level: str
    paper_bars_path: str
    bot_config_path: Path

    # DigitalOcean sync — all three required to enable.
    sync_endpoint_url: str | None
    sync_hmac_secret: str | None
    sync_outbox_path: str           # always set; just unused if sync disabled
    sync_bot_id: str


def _required_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Missing required env var: {name}. "
            f"See app/config.py for the full list."
        )
    return val


def load_config() -> AppConfig:
    mode_raw = _required_env("TOPSTEP_BOT_MODE").lower()
    if mode_raw not in ("paper", "live"):
        raise RuntimeError(
            f"TOPSTEP_BOT_MODE must be 'paper' or 'live', got {mode_raw!r}"
        )

    # bot_config.json overrides env vars for instrument and timeframes.
    bot_config_path = Path(os.environ.get("BOT_CONFIG_PATH", "bot_config.json"))
    bot_cfg: BotConfig = load_bot_config(bot_config_path, strict=True)

    env_instrument = _required_env("TOPSTEP_BOT_INSTRUMENT").upper()
    instrument = bot_cfg.instrument.upper() if bot_cfg.instrument else env_instrument

    env_timeframes_raw = os.environ.get("TOPSTEP_BOT_TIMEFRAMES", "1min")
    env_timeframes = [t.strip() for t in env_timeframes_raw.split(",") if t.strip()] or ["1min"]
    timeframes = bot_cfg.timeframes if bot_cfg.timeframes else env_timeframes

    soft_buffer = Decimal(os.environ.get("TOPSTEP_BOT_SOFT_BUFFER", "500"))
    reconcile_secs = float(os.environ.get("TOPSTEP_BOT_RECONCILE_SECS", "30"))
    log_level = os.environ.get("TOPSTEP_BOT_LOG_LEVEL", "INFO").upper()

    # paper_bars_path: env override → auto-derive from instrument
    paper_bars_path = (
        os.environ.get("TOPSTEP_BOT_PAPER_BARS")
        or f"bars/bars_{instrument}.csv"
    )

    # Sync: optional. If endpoint + secret are present, sync is enabled.
    sync_endpoint = os.environ.get("TOPSTEP_BOT_SYNC_ENDPOINT") or None
    sync_secret = os.environ.get("TOPSTEP_BOT_SYNC_HMAC_SECRET") or None
    sync_outbox = os.environ.get("TOPSTEP_BOT_OUTBOX_PATH", "./outbox.db")
    sync_bot_id = os.environ.get("TOPSTEP_BOT_BOT_ID", "default")

    if bool(sync_endpoint) != bool(sync_secret):
        raise RuntimeError(
            "TOPSTEP_BOT_SYNC_ENDPOINT and TOPSTEP_BOT_SYNC_HMAC_SECRET "
            "must be set together (or both unset)."
        )

    if mode_raw == "live":
        for v in ("PROJECT_X_API_KEY", "PROJECT_X_USERNAME"):
            if not os.environ.get(v):
                raise RuntimeError(
                    f"Live mode requires {v} in environment "
                    f"(consumed by project_x_py SDK)."
                )

    return AppConfig(
        mode=mode_raw,  # type: ignore[arg-type]
        instrument=instrument,
        timeframes=timeframes,
        soft_buffer=soft_buffer,
        reconcile_interval_seconds=reconcile_secs,
        log_level=log_level,
        paper_bars_path=paper_bars_path,
        bot_config_path=bot_config_path,
        sync_endpoint_url=sync_endpoint,
        sync_hmac_secret=sync_secret,
        sync_outbox_path=sync_outbox,
        sync_bot_id=sync_bot_id,
    )
