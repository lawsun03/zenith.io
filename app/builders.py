"""Shared construction helpers for the research platform.

Extracted from the former app/main.py when the live daemon was removed.
Nothing here places an order; these build runners, aggregate bars and
journal state for backtests and the research dashboard.
"""

from __future__ import annotations

"""
Entry point. Construct the full graph, run forever (or until the bar
stream ends in paper mode).

Run:
    TOPSTEP_BOT_MODE=paper \
    TOPSTEP_BOT_INSTRUMENT=MGC \
    TOPSTEP_BOT_PAPER_BARS=bars/bars_MGC.csv \
    python -m app.builders

Or for live (after demo-account testing!):
    TOPSTEP_BOT_MODE=live \
    TOPSTEP_BOT_INSTRUMENT=MGC \
    PROJECT_X_USERNAME=... \
    PROJECT_X_API_KEY=... \
    python -m app.builders

What this does in order:
  1. Load and validate config from env.
  2. Configure logging.
  3. Construct the runner and supporting trackers.
  4. Construct RiskState with the account config.
  5. Construct StrategyRunner with default detector params.
  6. Construct ExecutionEngine and Reconciler.
  7. Wire up signal handlers (SIGINT/SIGTERM → graceful shutdown).
  8. Connect, subscribe, start engine + reconciler.
  9. Paper: replay the CSV. Live: sleep until shutdown.
 10. On shutdown: stop reconciler, stop engine, disconnect broker.

Detector parameters live here as defaults. Eventually you'll move
these to a config file or pull from your DigitalOcean param store —
but inline defaults are fine for first ship.
"""
import asyncio
import csv
import logging
import os
import signal
import socket
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo
from app.api.journal import Journal
from app.api.server import build_app
from app.bot_config import BotConfig, StrategyParams, load_bot_config, strategy_for
from app.sim.events import Fill
from app.sim.paper import PaperBroker
from app.sim.protocol import Broker
from app.config import AppConfig, load_config
from app.execution.engine import (
    ExecutionEngine,
    OrderOutcome,
    StrategyRunner,
)
from app.strategy.grader import SetupGrader
from app.execution.excursion import ExcursionTracker
from app.execution.reconciler import Reconciler, ReconcilerConfig
from app.journaling import (
    _TRADES_CSV,
    _append_excursion_csv,
    _daily_csv_path,
    _make_bar_close_watcher,
    _make_fill_journaler,
    _make_pre_place,
    _make_reject_journaler,
    _make_signal_journaler,
)
from app.notifications import DiscordNotifier, EmailNotifier, EndOfDayScheduler, HourlyHealthScheduler, TailHandler
from app.replay import load_bars_csv
from app.risk.account_phase import tracker_from_config
from app.risk.config import config_for_account, fifty_k_combine
from app.risk.state import RiskState
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.killzone import default_killzones, killzones_from_names
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
from app.strategy.htf import HTFBiasTracker, HTFLevelFinder
from app.strategy.volume_profile import VolumeProfileTracker
log = logging.getLogger("topstep_bot")
_CT = ZoneInfo("America/Chicago")
HTF_REFRESH_SECONDS = 60  # 4h/30min structure barely moves intraday; 60s is ample
_PID_FILE = Path("topstep-bot.pid")


def _setup_logging(level: str) -> None:
    """
    Plain, dense, single-line records. Easy to grep, easy to ship to
    a log file you can tail mid-session.
    """
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quieten chatty libs once they exist.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _build_runner(
    instrument: str,
    s: StrategyParams,
    enabled_killzones: list[str] | None = None,
    timeframe: str = "1min",
    signal_instrument: str | None = None,
) -> StrategyRunner:
    zones = killzones_from_names(enabled_killzones) if enabled_killzones else None
    if s.engine == "combined":
        from app.strategy.combined import CombinedRunner
        primary = _build_runner(
            instrument, s.model_copy(update={"engine": "ifvg"}),
            enabled_killzones, timeframe, signal_instrument)
        secondary = _build_runner(
            instrument, s.model_copy(update={"engine": "orb"}),
            enabled_killzones, timeframe, signal_instrument)
        return CombinedRunner(primary=primary, secondary=secondary,
                              confluence_gate=s.ifvg_orb_confluence_gate,
                              alignment_gate=s.orb_ifvg_alignment_required)
    if s.engine == "regime_switch":
        from app.strategy.regime_switch import RegimeSwitchRunner
        active = _build_runner(
            instrument, s.model_copy(update={"engine": "ifvg"}),
            enabled_killzones, timeframe, signal_instrument)
        quiet = _build_runner(
            instrument, s.model_copy(update={"engine": "orb"}),
            enabled_killzones, timeframe, signal_instrument)
        return RegimeSwitchRunner(quiet=quiet, active=active)
    if s.engine == "chop_breakout":
        from app.strategy.chop_breakout import (ChopBreakoutConfig,
                                                ChopBreakoutDetector,
                                                ChopBreakoutRunner)
        return ChopBreakoutRunner(
            instrument=instrument,
            timeframe=timeframe,
            detector=ChopBreakoutDetector(ChopBreakoutConfig(
                instrument=instrument,
                regime_metric=s.cb_regime_metric,
                compression_lookback=s.cb_compression_lookback,
                compression_percentile=s.cb_compression_percentile,
                history_window=s.cb_history_window,
                min_chop_bars=s.cb_min_chop_bars,
                vwap_cross_min=s.cb_vwap_cross_min,
                entry_mode=s.cb_entry_mode,
                target_floor_r=s.cb_target_floor_r,
                failed_breakout_bars=s.cb_failed_breakout_bars,
                vwap_invalidation=s.cb_vwap_invalidation,
                sma21_trail=s.cb_sma21_trail,
                atr_period=s.atr_period,
                body_atr_multiple=s.body_atr_multiple,
                min_body_to_range_ratio=s.min_body_to_range_ratio,
                min_absolute_body=s.min_absolute_body,
                swing_lookback=s.swing_lookback,
            )),
            strategy_cfg=s,
            signal_instrument=signal_instrument or "",
        )
    if s.engine == "sweep_bos":
        from app.strategy.sweep_bos import (SweepBOSConfig, SweepBOSDetector,
                                            SweepBOSRunner)
        return SweepBOSRunner(
            instrument=instrument,
            timeframe=timeframe,
            detector=SweepBOSDetector(SweepBOSConfig(
                instrument=instrument,
                swing_lookback=s.swing_lookback,
                min_penetration=s.min_penetration,
                multi_bar_window=s.multi_bar_window,
                stop_buffer=s.stop_buffer,
                r_multiple=s.r_multiple,
                bos_window_bars=s.ifvg_sweep_window_bars,
                killzones=zones,
            )),
            strategy_cfg=s,
            signal_instrument=signal_instrument or "",
        )
    if s.engine == "sweep_reentry":
        from app.strategy.orb import ORBConfig, ORBDetector  # noqa: F811
        from app.strategy.sweep_reentry import (
            SweepReentryConfig, SweepReentryDetector, SweepReentryRunner)
        _orb_det = ORBDetector(ORBConfig(
            instrument=instrument,
            open_et=s.orb_open_et,
            range_minutes=s.orb_range_minutes,
            r_multiple=s.orb_r_multiple,
            max_trades_per_day=s.orb_max_trades_per_day,
            reentry_after_stop=s.orb_reentry_after_stop,
            long_only=s.orb_long_only,
            skip_trading_days=s.skip_trading_days,
            signal_window_mins=s.orb_signal_window_mins,
            require_pm_break=s.orb_require_pm_break,
            fib_target_ext=s.orb_fib_target_ext,
        ))
        _sr_det = SweepReentryDetector(SweepReentryConfig(
            instrument=instrument,
            sweep_depth_atr=s.sweep_reentry_depth_atr,
            atr_period=s.atr_period,
            min_absolute_body=s.min_absolute_body,
            body_atr_multiple=s.body_atr_multiple,
            min_body_to_range_ratio=s.min_body_to_range_ratio,
            r_multiple=s.r_multiple,
            stop_buffer=s.stop_buffer,
        ))
        return SweepReentryRunner(
            instrument=instrument,
            timeframe=timeframe,
            orb_detector=_orb_det,
            sr_detector=_sr_det,
            strategy_cfg=s,
            signal_instrument=signal_instrument or "",
        )
    if s.engine == "vwap":
        from app.strategy.vwap import VWAPConfig, VWAPDetector, VWAPRunner
        return VWAPRunner(
            instrument=instrument,
            timeframe=timeframe,
            detector=VWAPDetector(VWAPConfig(
                instrument=instrument,
                anchor_et=s.vwap_anchor_et,
                band_sigma=s.vwap_band_sigma,
                stop_sigma=s.vwap_stop_sigma,
            )),
            strategy_cfg=s,
            signal_instrument=signal_instrument or "",
        )
    if s.engine == "orb":
        from app.strategy.orb import ORBComposer, ORBConfig, ORBDetector, ORBRunner
        _det = ORBDetector(ORBConfig(
            instrument=instrument,
            open_et=s.orb_open_et,
            range_minutes=s.orb_range_minutes,
            r_multiple=s.orb_r_multiple,
            max_trades_per_day=s.orb_max_trades_per_day,
            pdr_enabled=s.orb_pdr_enabled,
            reentry_after_stop=s.orb_reentry_after_stop,
            long_only=s.orb_long_only,
            skip_trading_days=s.skip_trading_days,
            signal_window_mins=s.orb_signal_window_mins,
            require_pm_break=s.orb_require_pm_break,
            fib_target_ext=s.orb_fib_target_ext,
        ))
        return ORBRunner(
            instrument=instrument,
            timeframe=timeframe,
            detector=_det,
            strategy_cfg=s,
            composer=ORBComposer(
                detector=_det,
                reentry_after_stop=s.orb_reentry_after_stop,
            ),
            signal_instrument=signal_instrument or "",
        )
    if s.engine == "kz_levels":
        from app.strategy.kz_levels import KillzoneLevelTracker, KZLevelsRunner
        # kz_levels requires named session zones (London, NY AM, NY PM).
        # enabled_killzones="all" maps to all_day() which never closes.
        kz_zones = default_killzones()
        return KZLevelsRunner(
            instrument=instrument,
            timeframe=timeframe,
            kz_tracker=KillzoneLevelTracker(),
            displacement=DisplacementDetector(DisplacementConfig(
                atr_period=s.atr_period,
                body_atr_multiple=s.body_atr_multiple,
                min_body_to_range_ratio=s.min_body_to_range_ratio,
                min_absolute_body=s.min_absolute_body,
                atr_ref_lag_bars=s.atr_ref_lag_bars,
                min_absolute_body_pct=s.min_absolute_body_pct,
            )),
            composer=SweepDisplacementComposer(ComposerConfig(
                instrument=instrument,
                displacement_window_bars=s.displacement_window_bars,
                stop_buffer=s.stop_buffer,
                stop_buffer_pct=s.stop_buffer_pct,
                r_multiple=s.r_multiple,
                killzones=kz_zones,
                trend_ema_period=s.trend_ema_period,
                cooldown_bars_after_stop=s.cooldown_bars_after_stop,
                min_atr_filter=s.min_atr_filter,
                max_atr_filter=s.max_atr_filter,
                swing_stop_lookback=s.swing_stop_lookback,
                allowed_sides=s.allowed_sides,
                confirmation=s.confirmation,
                max_stop_atr=s.max_stop_atr,
                inversion_min_body_r=s.inversion_min_body_r,
                daily_signal_cap=s.ifvg_daily_signal_cap,
                skip_trading_days=s.skip_trading_days,
                daily_bias_gate_enabled=s.daily_bias_gate_enabled,
                stop_mode=s.stop_mode,
                block_hours=s.ifvg_block_hours,
                max_short_rank=s.ifvg_max_short_rank,
                silver_bullet_only=s.silver_bullet_only,
                suppress_same_direction_repeat=s.ifvg_suppress_same_direction_repeat,
                fib_target_ext=s.ifvg_fib_target_ext,
                sweep_levels_tag_enabled=s.sweep_levels_tag_enabled,
                sweep_levels_tag_tolerance_ticks=s.sweep_levels_tag_tolerance_ticks,
            )),
            grader=SetupGrader(target_clarity_mode=s.target_clarity_mode),
            strategy_cfg=s,
            zones=kz_zones,
            signal_instrument=signal_instrument or "",
        )
    if s.engine == "forbes":
        from app.strategy.forbes import ForbesConfig, ForbesDetector, ForbesRunner
        return ForbesRunner(
            instrument=instrument,
            timeframe=timeframe,
            detector=ForbesDetector(ForbesConfig.from_params(instrument, s)),
            strategy_cfg=s,
            signal_instrument=signal_instrument or "",
        )
    if s.engine != "ifvg":
        raise ValueError(
            f"_build_runner: unknown engine {s.engine!r} — no dispatch branch matched. "
            "Refusing to silently fall back to iFVG (Rule 12)."
        )
    return StrategyRunner(
        instrument=instrument,
        timeframe=timeframe,
        liquidity=LiquidityTracker(LiquidityConfig(
            swing_lookback=s.swing_lookback,
            min_penetration=s.min_penetration,
            multi_bar_window=s.multi_bar_window,
            max_swings=50,
            min_penetration_atr_factor=s.min_penetration_atr_factor if s.min_penetration_atr_factor > 0 else None,
        )),
        displacement=DisplacementDetector(DisplacementConfig(
            atr_period=s.atr_period,
            body_atr_multiple=s.body_atr_multiple,
            min_body_to_range_ratio=s.min_body_to_range_ratio,
            min_absolute_body=s.min_absolute_body,
            atr_ref_lag_bars=s.atr_ref_lag_bars,
            min_absolute_body_pct=s.min_absolute_body_pct,
        )),
        composer=SweepDisplacementComposer(ComposerConfig(
            instrument=instrument,
            displacement_window_bars=s.displacement_window_bars,
            stop_buffer=s.stop_buffer,
            stop_buffer_pct=s.stop_buffer_pct,
            r_multiple=s.r_multiple,
            killzones=zones,  # None falls back to default in the composer
            trend_ema_period=s.trend_ema_period,
            cooldown_bars_after_stop=s.cooldown_bars_after_stop,
            min_atr_filter=s.min_atr_filter,
            max_atr_filter=s.max_atr_filter,
            swing_stop_lookback=s.swing_stop_lookback,
            allowed_sides=s.allowed_sides,
            confirmation=s.confirmation,
            max_stop_atr=s.max_stop_atr,
            inversion_min_body_r=s.inversion_min_body_r,
            daily_signal_cap=s.ifvg_daily_signal_cap,
            skip_trading_days=s.skip_trading_days,
            daily_bias_gate_enabled=s.daily_bias_gate_enabled,
            stop_mode=s.stop_mode,
            max_short_rank=s.ifvg_max_short_rank,
            silver_bullet_only=s.silver_bullet_only,
            suppress_same_direction_repeat=s.ifvg_suppress_same_direction_repeat,
            fib_target_ext=s.ifvg_fib_target_ext,
            sweep_levels_tag_enabled=s.sweep_levels_tag_enabled,
            sweep_levels_tag_tolerance_ticks=s.sweep_levels_tag_tolerance_ticks,
        )),
        grader=SetupGrader(target_clarity_mode=s.target_clarity_mode),
        strategy_cfg=s,
        vp=VolumeProfileTracker(),
        signal_instrument=signal_instrument or "",
    )


def _session_pnl_from_trades(trades: "list[dict]") -> Decimal:
    """Sum realized P&L over settled trades from a /Trade/search response.

    Excludes voided trades and half-turn trades (profitAndLoss is None) —
    counting either would seed the daily-loss gate with phantom P&L.
    """
    total = Decimal("0")
    for t in trades:
        pnl = t.get("profitAndLoss")
        if pnl is None or t.get("voided"):
            continue
        total += Decimal(str(pnl))
    return total


def _daily_pnl_from_csv(
    session_start: "datetime",
    account_id: "str | int | None" = None,
    csv_path: "Path | None" = None,
) -> Decimal:
    """
    Fallback: derive session P&L from the local trades CSV when the broker
    API call fails. Reads EXIT rows whose timestamp falls within the session window.

    When account_id is given, rows are scoped to that account: a row counts only
    if its `account` column matches, OR the row predates the column (empty/absent
    `account`) — the latter keeps legacy ledgers from silently zeroing out.
    """
    import csv as _csv
    from pathlib import Path as _Path
    total = Decimal("0")
    path = _Path(csv_path) if csv_path is not None else _Path("trades/trades.csv")
    if not path.exists():
        return total
    acct = str(account_id) if account_id is not None else None
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                if row.get("type") != "EXIT":
                    continue
                if acct is not None:
                    row_acct = (row.get("account") or "").strip()
                    if row_acct and row_acct != acct:
                        continue
                try:
                    ts = datetime.fromisoformat(row["ts"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= session_start:
                        total += Decimal(str(row.get("realized_pnl") or "0"))
                except Exception:
                    continue
        log.info("Daily P&L bootstrapped from CSV: $%s", total)
    except Exception as e:
        log.warning("CSV daily P&L fallback failed: %s", e)
    return total


def _make_bar_journaler(journal: Journal, execution_instrument: str = ""):
    """Build the on_bar subscriber that streams bars to the chart."""
    from app.sim.events import Bar as BarEvent

    async def on_bar(bar: BarEvent) -> None:
        # Re-label signal instrument bars (e.g. GC) as the execution instrument (MGC)
        # for the chart — prices are identical, only the label differs.
        journal.publish_bar(bar, display_instrument=execution_instrument or None)

    return on_bar


def _make_strategy_state_publisher(journal: Journal, engine: Any, execution_instrument: str = "", broker: Any = None, cpi_dates=None, base_suppress: bool = True):
    """Build the on_bar subscriber that emits strategy_state for the StrategyDebug panel.

    Reads pre-computed grader state — no heavy computation on the hot path.
    Pure observability: never affects trade decisions. iFVG-specific reads are
    guarded so the news_straddle runner (no displacement/composer zones) is
    tolerated — its live state comes from the scheduler instead (B92, Rule 13).
    """
    from app.sim.events import Bar as BarEvent
    from app.strategy.killzone import in_macro_window, in_news_blackout

    async def on_bar(bar: BarEvent) -> None:
        runner = engine.runners.get(bar.instrument) or (
            engine.runners.get(execution_instrument) if execution_instrument else None
        )
        if runner is None and engine.runners:
            runner = next(iter(engine.runners.values()))
        if runner is None:
            return

        grade = getattr(runner.grader, "last_grade", None)
        _disp = getattr(runner, "displacement", None)
        active_fvgs_count = len(_disp.active_fvgs) if _disp is not None else 0

        # Read session range for the current bar's killzone (already maintained by runner)
        cfg = engine.strategy_cfg
        kz = None
        _zones = getattr(getattr(runner, "composer", None), "_zones", None)
        if cfg is not None and _zones is not None:
            from app.strategy.killzone import in_killzone
            kz = in_killzone(bar.ts, _zones)
        sr = runner.grader.session_range(kz.name if kz else "") if kz else None

        in_macro = False
        news_block = False
        if cfg is not None:
            in_macro = in_macro_window(bar.ts, cfg.ifvg_macro_windows)
            news_block = in_news_blackout(bar.ts, cfg.ifvg_news_blackout)

        phase_data: dict | None = None
        if engine.phase is not None:
            p = engine.phase
            phase_data = {
                "name": p.phase,
                "balance": str(p.balance),
                "mll": str(p.mll),
                "cushion": str(p.cushion),
                "today_pnl": str(p.today_pnl),
                "best_day": str(p.best_day_live),
                "winning_days": p.winning_days,
                "target_reached": p.target_reached(),
            }

        # Extract ORB state from CombinedRunner.secondary or a standalone ORBRunner.
        orb_state: dict | None = None
        orb_runner = getattr(runner, "secondary", None)
        if orb_runner is None and hasattr(runner, "detector"):
            orb_runner = runner
        if orb_runner is not None and hasattr(orb_runner, "detector"):
            orb_state = orb_runner.detector.state()

        # Live MFE/MAE excursion for the primary instrument (Rule 13: strategy state observable)
        pos_excursion: dict | None = None
        if broker is not None and hasattr(broker, "live_excursion"):
            pos_excursion = broker.live_excursion(runner.instrument)

        from app.strategy.cpi_day import router_state as _cpi_router_state
        cpi_router = _cpi_router_state(bar.ts, cpi_dates, base_suppress) if cpi_dates else None

        journal.publish_strategy_state(
            instrument=runner.instrument,
            grade=grade,
            active_fvgs_count=active_fvgs_count,
            session_high=sr[0] if sr else None,
            session_low=sr[1] if sr else None,
            in_macro=in_macro,
            news_blackout=news_block,
            phase=phase_data,
            orb_state=orb_state,
            pos_excursion=pos_excursion,
            cpi_day_router=cpi_router,
        )

    return on_bar




def _tf_to_seconds(tf: str) -> int:
    """Parse '4h' → 14400, '30min' → 1800, '1d' → 86400, '1min' → 60."""
    tf = tf.strip().lower()
    if tf.endswith("min"):
        return int(tf[:-3]) * 60
    if tf.endswith("h"):
        return int(tf[:-1]) * 3600
    if tf.endswith("d"):
        return int(tf[:-1]) * 86400
    return 60


def _aggregate_bars(
    bars: list,  # list[Bar] — import-loop avoidance
    target_seconds: int,
    target_label: str,
) -> list:
    """Group bars into time buckets and compute OHLCV per bucket.

    Buckets align to UTC-epoch multiples of target_seconds. Used to fold
    1min data into 4h / 30min / 1d HTF bars when the broker doesn't return
    enough native HTF bars for the current contract (e.g. after a roll).
    """
    if not bars:
        return []
    from datetime import datetime, timezone
    from app.sim.events import Bar  # local import to avoid module-level cycle

    buckets: dict[int, list] = {}
    for b in bars:
        epoch = int(b.ts.timestamp())
        bucket_epoch = (epoch // target_seconds) * target_seconds
        buckets.setdefault(bucket_epoch, []).append(b)

    out: list = []
    for bucket_epoch in sorted(buckets):
        group = sorted(buckets[bucket_epoch], key=lambda b: b.ts)
        out.append(Bar(
            instrument=group[0].instrument,
            timeframe=target_label,
            ts=datetime.fromtimestamp(bucket_epoch, tz=timezone.utc),
            open=group[0].open,
            high=max(b.high for b in group),
            low=min(b.low for b in group),
            close=group[-1].close,
            volume=sum(b.volume for b in group),
        ))
    return out








