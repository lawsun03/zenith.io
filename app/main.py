"""
Entry point. Construct the full graph, run forever (or until the bar
stream ends in paper mode).

Run:
    TOPSTEP_BOT_MODE=paper \
    TOPSTEP_BOT_INSTRUMENT=MGC \
    TOPSTEP_BOT_PAPER_BARS=bars/bars_MGC.csv \
    python -m app.main

Or for live (after demo-account testing!):
    TOPSTEP_BOT_MODE=live \
    TOPSTEP_BOT_INSTRUMENT=MGC \
    PROJECT_X_USERNAME=... \
    PROJECT_X_API_KEY=... \
    python -m app.main

What this does in order:
  1. Load and validate config from env.
  2. Configure logging.
  3. Construct broker (paper or TopstepXBroker).
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

from __future__ import annotations

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
from app.bot_config import BotConfig, StrategyParams, load_bot_config
from app.broker.events import Fill
from app.broker.paper import PaperBroker
from app.broker.protocol import Broker
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
    _make_fill_journaler,
    _make_pre_place,
    _make_reject_journaler,
    _make_signal_journaler,
)
from app.notifications import DiscordNotifier, EmailNotifier, EndOfDayScheduler, HourlyHealthScheduler, TailHandler
from project_x_py.exceptions import ProjectXConnectionError
from app.replay import load_bars_csv
from app.risk.config import config_for_account, fifty_k_combine
from app.risk.state import RiskState
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.killzone import killzones_from_names
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
from app.strategy.htf import HTFBiasTracker, HTFLevelFinder
from app.strategy.volume_profile import VolumeProfileTracker
from app.sync.outbox import Outbox
from app.sync.sender import Sender, SenderConfig

log = logging.getLogger("topstep_bot")


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
        )),
        composer=SweepDisplacementComposer(ComposerConfig(
            instrument=instrument,
            displacement_window_bars=s.displacement_window_bars,
            stop_buffer=s.stop_buffer,
            r_multiple=s.r_multiple,
            killzones=zones,  # None falls back to default in the composer
            trend_ema_period=s.trend_ema_period,
            cooldown_bars_after_stop=s.cooldown_bars_after_stop,
            min_atr_filter=s.min_atr_filter,
            max_atr_filter=s.max_atr_filter,
        )),
        grader=SetupGrader(target_clarity_mode=s.target_clarity_mode),
        strategy_cfg=s,
        vp=VolumeProfileTracker(),
        signal_instrument=signal_instrument or "",
    )


_CT = ZoneInfo("America/Chicago")
HTF_REFRESH_SECONDS = 60  # 4h/30min structure barely moves intraday; 60s is ample


async def _fetch_live_state(account_name: str | None) -> tuple[Decimal, str, Decimal]:
    """
    Authenticate and return (balance, resolved_account_name, session_daily_pnl).

    session_daily_pnl is the sum of realized P&L from all closed trades since
    the start of the current Topstep trading session (5 PM CT). This lets the
    bot pick up the correct daily P&L if it restarts mid-session rather than
    resetting to $0 and making the DLL gate too lenient.

    If the trade-search call fails (endpoint unavailable, auth issue, etc.)
    we fall back to $0 and log a warning — same as the old behavior.
    """
    from project_x_py import ProjectX  # type: ignore

    now_utc = datetime.now(timezone.utc)
    now_ct  = now_utc.astimezone(_CT)

    # Trading session starts at 5 PM CT. Before 5 PM → session started yesterday.
    if now_ct.hour < 17:
        session_start = (now_ct - timedelta(days=1)).replace(
            hour=17, minute=0, second=0, microsecond=0
        )
    else:
        session_start = now_ct.replace(hour=17, minute=0, second=0, microsecond=0)

    async with ProjectX.from_env() as client:
        await client.authenticate()
        accounts = await client.list_accounts()

        if account_name:
            account = next((a for a in accounts if a.name == account_name), None)
            if account is None:
                tradeable = [a for a in accounts if a.canTrade]
                available_names = [a.name for a in tradeable]
                if tradeable:
                    account = tradeable[0]
                    log.warning(
                        "Configured account %r not found; falling back to %r. "
                        "Update account_name in bot_config.json to silence this. "
                        "Available accounts: %s",
                        account_name, account.name, available_names,
                    )
                else:
                    log.error(
                        "Configured account %r not found and no tradeable accounts available. "
                        "Available accounts: %s",
                        account_name, [a.name for a in accounts],
                    )
                    return Decimal("50000"), "", Decimal("0")
        else:
            account = next((a for a in accounts if a.canTrade), None)

        if account is None:
            return Decimal("50000"), "", Decimal("0")

        balance = Decimal(str(account.balance))
        name    = account.name

        # Bootstrap daily P&L from today's session trades.
        daily_pnl = Decimal("0")
        try:
            trades = await client.search_trades(
                start_date=session_start,
                end_date=now_utc,
                account_id=account.id,
                limit=500,
            )
            raw_pnl = sum(
                t.profitAndLoss for t in trades
                if t.profitAndLoss is not None and not t.voided
            )
            daily_pnl = Decimal(str(raw_pnl))
            log.info(
                "Session daily P&L bootstrapped from %d trades since %s: $%s",
                len(trades), session_start.strftime("%H:%M CT"), daily_pnl,
            )
        except Exception:
            log.warning(
                "Could not fetch session trades for daily P&L bootstrap — starting at $0. "
                "DLL gate will be correct only after the first fill this session."
            )

    return balance, name, daily_pnl


async def _build_broker(cfg: AppConfig) -> Broker:
    """
    Build the right broker for the mode. Live import of TopstepXBroker
    is lazy so paper mode doesn't require project_x_py installed.
    """
    if cfg.mode == "paper":
        return PaperBroker(starting_balance=Decimal("50000"))

    from app.broker.topstepx import TopstepXBroker
    bot_cfg = load_bot_config(Path(os.environ.get("BOT_CONFIG_PATH", "bot_config.json")))
    return TopstepXBroker(account_name=bot_cfg.account_name, entry_mode=bot_cfg.entry_mode, partial_profit_r=bot_cfg.partial_profit_r)


def _make_bar_journaler(journal: Journal, execution_instrument: str = ""):
    """Build the on_bar subscriber that streams bars to the chart."""
    from app.broker.events import Bar as BarEvent

    async def on_bar(bar: BarEvent) -> None:
        # Re-label signal instrument bars (e.g. GC) as the execution instrument (MGC)
        # for the chart — prices are identical, only the label differs.
        journal.publish_bar(bar, display_instrument=execution_instrument or None)

    return on_bar


def _make_strategy_state_publisher(journal: Journal, engine: Any, execution_instrument: str = ""):
    """Build the on_bar subscriber that emits strategy_state for the StrategyDebug panel.

    Reads pre-computed grader state — no heavy computation on the hot path.
    Pure observability: never affects trade decisions.
    """
    from app.broker.events import Bar as BarEvent
    from app.strategy.killzone import in_session_window, in_macro_window, in_news_blackout

    async def on_bar(bar: BarEvent) -> None:
        runner = engine.runners.get(execution_instrument) if execution_instrument else None
        if runner is None:
            # Try the first runner if instrument key doesn't match
            if engine.runners:
                runner = next(iter(engine.runners.values()))
        if runner is None:
            return

        grade = runner.grader.last_grade
        active_fvgs_count = len(runner.displacement.active_fvgs)

        # Read session range for the current bar's killzone (already maintained by runner)
        cfg = engine.strategy_cfg
        kz = None
        if cfg is not None:
            from app.strategy.killzone import in_killzone
            kz = in_killzone(bar.ts, runner.composer._zones)
        sr = runner.grader.session_range(kz.name if kz else "") if kz else None

        in_session = True
        in_macro = False
        news_block = False
        if cfg is not None:
            in_session = in_session_window(bar.ts, cfg.ifvg_session_windows)
            in_macro = in_macro_window(bar.ts, cfg.ifvg_macro_windows)
            news_block = in_news_blackout(bar.ts, cfg.ifvg_news_blackout)

        journal.publish_strategy_state(
            instrument=runner.instrument,
            grade=grade,
            active_fvgs_count=active_fvgs_count,
            session_high=sr[0] if sr else None,
            session_low=sr[1] if sr else None,
            in_session=in_session,
            in_macro=in_macro,
            news_blackout=news_block,
        )

    return on_bar


async def _run_paper(
    broker: PaperBroker,
    cfg: AppConfig,
    shutdown: asyncio.Event,
    restart_event: asyncio.Event | None = None,
    journal: Optional[Journal] = None,
    risk_state: Optional[RiskState] = None,
    engine: Optional[Any] = None,
) -> None:
    """
    Paper mode: stream bars from a CSV through the broker.

    Loops on restart_event — each restart reloads config, resets journal/
    risk/broker state, and replays from bar 0 with the new parameters.
    """
    if not cfg.paper_bars_path:
        log.info("Paper mode idle (no TOPSTEP_BOT_PAPER_BARS set).")
        await shutdown.wait()
        return

    first_run = True
    while True:
        bot_cfg = load_bot_config(cfg.bot_config_path)
        delay_s = bot_cfg.replay_delay_ms / 1000.0

        if first_run and bot_cfg.replay_start_delay_s > 0:
            log.info(
                "Replay starting in %ds — open the dashboard now.",
                bot_cfg.replay_start_delay_s,
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(shutdown.wait()),
                    timeout=bot_cfg.replay_start_delay_s,
                )
                return  # shutdown during countdown
            except asyncio.TimeoutError:
                pass

        effective_tf = (bot_cfg.timeframes[0] if bot_cfg.timeframes else cfg.timeframes[0])
        log.info(
            "Replaying bars from %s (delay=%.0fms/bar, tf=%s)",
            cfg.paper_bars_path, bot_cfg.replay_delay_ms, effective_tf,
        )
        count = 0
        restarting = False
        for bar in load_bars_csv(
            cfg.paper_bars_path,
            instrument=cfg.instrument,
            timeframe=effective_tf,
        ):
            if shutdown.is_set():
                log.info("Shutdown signaled mid-replay; stopping.")
                return
            if restart_event is not None and restart_event.is_set():
                log.info("Restart signaled mid-replay; aborting current run.")
                restarting = True
                break
            await broker.inject_bar(bar)
            count += 1
            await asyncio.sleep(delay_s)

        if not restarting:
            log.info("Replay complete: %d bars processed.", count)

        if restart_event is None:
            await shutdown.wait()
            return

        if not restarting:
            # Replay finished naturally — wait for restart or shutdown.
            shutdown_task  = asyncio.create_task(shutdown.wait())
            restart_task   = asyncio.create_task(restart_event.wait())
            done, pending  = await asyncio.wait(
                [shutdown_task, restart_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            if shutdown.is_set():
                return

        # ── Restart: reload config, reset all state ──────────────────────
        log.info("Restarting replay with fresh state...")
        restart_event.clear()
        if journal is not None:
            journal.reset()
        if risk_state is not None:
            risk_state.reset()
        if engine is not None:
            new_cfg = load_bot_config(cfg.bot_config_path)
            new_runner = _build_runner(
                cfg.instrument, new_cfg.strategy, new_cfg.enabled_killzones,
                timeframe=new_cfg.timeframes[0] if new_cfg.timeframes else "1min",
                signal_instrument=new_cfg.signal_instrument,
            )
            engine.runners = {cfg.instrument: new_runner}
            engine._bar_router = {
                new_runner.signal_instrument: new_runner.instrument
            } if new_runner.signal_instrument and new_runner.signal_instrument != new_runner.instrument else {}
            engine.strategy_cfg = new_cfg.strategy  # keep VP cfg in sync on restart
        broker.reset()
        first_run = False


async def _run_live(
    broker: Broker,
    cfg: AppConfig,
    shutdown: asyncio.Event,
    runner: "StrategyRunner | None" = None,
    bot_cfg: "BotConfig | None" = None,
    engine: "ExecutionEngine | None" = None,
) -> None:
    """Live mode: subscribe, warm up VP + HTF trackers, then block on shutdown."""
    signal_instr = (bot_cfg.signal_instrument if bot_cfg and bot_cfg.signal_instrument else None) or cfg.instrument
    if signal_instr != cfg.instrument:
        log.info("Signal instrument: %s — execution instrument: %s", signal_instr, cfg.instrument)
    await broker.subscribe([signal_instr], cfg.timeframes)
    if runner is not None and bot_cfg is not None and runner.vp is not None:
        await _warm_up_vp(broker, runner, bot_cfg)
    htf_task = None
    if bot_cfg is not None and engine is not None:
        s = bot_cfg.strategy
        # Build now so bias is ready before the first signal (only if enabled).
        if s.htf_bias_enabled or s.htf_target_enabled:
            await _rebuild_engine_htf(broker, engine, s)
            log.info(
                "HTF confluence active: bias=%s target=%s (bias_tf=%s swing_tf=%s)",
                s.htf_bias_enabled, s.htf_target_enabled,
                s.htf_bias_timeframe, s.htf_swing_timeframe,
            )
        # Always run the refresh loop so a later PATCH toggle is picked up and
        # trackers stay fresh. It no-ops cheaply while both trackers are None.
        htf_task = asyncio.create_task(_htf_refresh_loop(broker, engine, shutdown))
    log.info(
        "Live mode running. Instrument=%s timeframes=%s. Ctrl+C to stop.",
        cfg.instrument, cfg.timeframes,
    )
    await shutdown.wait()
    if htf_task is not None:
        htf_task.cancel()
        try:
            await htf_task
        except asyncio.CancelledError:
            pass


def _install_signal_handlers(shutdown: asyncio.Event) -> None:
    """Translate SIGINT/SIGTERM into setting the shutdown event."""
    loop = asyncio.get_running_loop()

    def _handler(sig_name: str) -> None:
        log.info("Received %s — initiating graceful shutdown.", sig_name)
        shutdown.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _handler, sig_name)
        except NotImplementedError:
            # Windows: add_signal_handler isn't supported on the
            # ProactorEventLoop. Fall back to default Ctrl+C → KeyboardInterrupt;
            # main() catches it. SIGTERM has no good Windows equivalent for
            # console apps — Task Scheduler will kill the process.
            pass


_PID_FILE = Path("topstep-bot.pid")


def _acquire_pid_lock() -> bool:
    """
    Write our PID to topstep-bot.pid. Returns True if we are the sole
    owner; False if another live process already holds the lock.

    We always overwrite the file — if the previous holder is dead
    (stale PID), we take over cleanly.
    """
    if _PID_FILE.exists():
        try:
            old_pid = int(_PID_FILE.read_text().strip())
            # Check if that process is still alive.
            try:
                os.kill(old_pid, 0)  # signal 0 = existence check
                return False  # process alive → we don't own the lock
            except OSError:
                pass  # process dead → stale lock, overwrite below
        except (ValueError, OSError):
            pass  # unreadable file → overwrite
    _PID_FILE.write_text(str(os.getpid()))
    return True


def _release_pid_lock() -> None:
    try:
        if _PID_FILE.exists():
            pid = int(_PID_FILE.read_text().strip())
            if pid == os.getpid():
                _PID_FILE.unlink()
    except OSError:
        pass


async def _warm_up_vp(broker: "Broker", runner: "StrategyRunner", bot_cfg: BotConfig) -> None:
    """
    Fetch 2 days of historical bars and feed them into the VP tracker.

    This ensures the prior session profile is ready before the first live
    bar arrives. Called once at startup in live mode, after broker connect.
    If the fetch fails (network, SDK), the tracker starts without a prior
    profile and filters are bypassed (has_prior_profile() returns False).
    """
    from app.broker.topstepx import TopstepXBroker
    if not isinstance(broker, TopstepXBroker):
        return

    timeframe = (bot_cfg.timeframes[0] if bot_cfg.timeframes else "1min")
    try:
        # Need enough bars to cross at least one UTC midnight boundary.
        # 1-min bars: 2000 bars ≈ 33 hours, enough to span yesterday.
        bars = await broker.get_historical_bars(timeframe=timeframe, days=3, limit=2000)
    except Exception as exc:
        log.warning("VP warm-up: historical bar fetch failed — VP filter inactive today: %s", exc)
        return

    if not bars:
        log.warning("VP warm-up: no historical bars returned — VP filter inactive today")
        return

    assert runner.vp is not None
    for bar in bars:
        runner.vp.on_bar(bar, bot_cfg.strategy)

    if runner.vp.has_prior_profile():
        log.info("VP warm-up complete: %d bars fed (prior profile ready)", len(bars))
    else:
        log.warning(
            "VP warm-up: %d bars fed but no session boundary crossed — "
            "VP filter inactive today",
            len(bars),
        )


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
    from app.broker.events import Bar  # local import to avoid module-level cycle

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


async def _build_htf_trackers(
    broker: "Broker", s: "StrategyParams",
) -> tuple[HTFBiasTracker | None, HTFLevelFinder | None]:
    """Construct + warm HTF trackers from REST history. Returns (bias, levels).

    Either may be None if its feature is disabled. On fetch failure the tracker
    is returned empty (bias → neutral, find_target → None): fail-open.
    """
    from app.broker.topstepx import TopstepXBroker
    if not isinstance(broker, TopstepXBroker):
        return None, None

    bias_tracker: HTFBiasTracker | None = None
    level_finder: HTFLevelFinder | None = None
    if s.htf_bias_enabled:
        bias_tracker = HTFBiasTracker(lookback=s.htf_bias_lookback)
    if s.htf_target_enabled:
        level_finder = HTFLevelFinder(swing_lookback=s.htf_bias_lookback)

    if bias_tracker is None and level_finder is None:
        return None, None

    await _refresh_htf_once(broker, s, bias_tracker, level_finder)
    return bias_tracker, level_finder


async def _refresh_htf_once(
    broker: "Broker", s: "StrategyParams",
    bias_tracker: "HTFBiasTracker | None", level_finder: "HTFLevelFinder | None",
    runners: "dict | None" = None,
) -> None:
    """Fetch recent 4h + 30min bars and rebuild whichever trackers exist."""
    if bias_tracker is None and level_finder is None:
        return
    try:
        # Try the native HTF timeframe first — fastest and most accurate when
        # the broker has the data. Many futures contracts return very few bars
        # for the active contract (post-roll), so check whether the native
        # fetch gave us enough structure to confirm swings; if not, aggregate
        # from 1min (which always has plenty of history).
        bias_bars = await broker.get_historical_bars(
            timeframe=s.htf_bias_timeframe, days=90, limit=2000,
        )
        # Threshold: we want enough bars that at least a handful of swings
        # can confirm with the configured lookback. (lookback*2 + 1) is the
        # bare minimum to confirm ONE swing; we require 5× that for usable
        # bias signal.
        min_useful_bars = (s.htf_bias_lookback * 2 + 1) * 5
        if len(bias_bars) < min_useful_bars:
            log.info(
                "HTF: native %s fetch gave only %d bars (< %d needed); "
                "aggregating from 1min instead",
                s.htf_bias_timeframe, len(bias_bars), min_useful_bars,
            )
            one_min = await broker.get_historical_bars(
                timeframe="1min", days=30, limit=50000,
            )
            target_secs = _tf_to_seconds(s.htf_bias_timeframe)
            bias_bars = _aggregate_bars(one_min, target_secs, s.htf_bias_timeframe)
            log.info(
                "HTF: aggregated %d 1min bars -> %d %s bars",
                len(one_min), len(bias_bars), s.htf_bias_timeframe,
            )

        if not bias_bars:
            log.warning(
                "HTF: 0 %s bars after fetch+aggregate — bias tracker will "
                "stay empty (neutral). Check broker connectivity.",
                s.htf_bias_timeframe,
            )
        if bias_tracker is not None:
            bias_tracker.rebuild(bias_bars)

        if level_finder is not None:
            swing_bars = await broker.get_historical_bars(
                timeframe=s.htf_swing_timeframe, days=10, limit=2000,
            )
            min_useful_swing = (s.htf_bias_lookback * 2 + 1) * 5
            if len(swing_bars) < min_useful_swing:
                # Reuse the 1min fetch we just did (if available) or pull fresh
                # if this is a level-finder-only configuration.
                if 'one_min' not in locals():
                    one_min = await broker.get_historical_bars(
                        timeframe="1min", days=15, limit=25000,
                    )
                swing_secs = _tf_to_seconds(s.htf_swing_timeframe)
                swing_bars = _aggregate_bars(one_min, swing_secs, s.htf_swing_timeframe)
                log.info(
                    "HTF: aggregated 1min -> %d %s swing bars",
                    len(swing_bars), s.htf_swing_timeframe,
                )
            if not swing_bars:
                log.warning(
                    "HTF: 0 %s swing bars — target finder fallback will be empty.",
                    s.htf_swing_timeframe,
                )
            level_finder.rebuild(fvg_bars=bias_bars, swing_bars=swing_bars)
            # Feed grader with 30min delivery FVGs and HTF swing levels
            if runners:
                for runner in runners.values():
                    runner.grader.update_delivery_fvgs(swing_bars)
                    runner.grader.update_htf_swings(
                        level_finder.swing_highs,
                        level_finder.swing_lows,
                    )
    except Exception:
        log.exception("HTF refresh failed — retaining last-known state")


async def _htf_refresh_loop(
    broker: "Broker", engine: "ExecutionEngine", shutdown: "asyncio.Event",
) -> None:
    """Background task: periodically rebuild the engine's HTF trackers from REST.

    Reads engine.strategy_cfg each tick so live PATCH toggles / timeframe changes
    are honored without restart. No-ops cheaply when both trackers are None.
    """
    while not shutdown.is_set():
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=HTF_REFRESH_SECONDS)
            break
        except asyncio.TimeoutError:
            pass
        s = engine.strategy_cfg
        if s is not None:
            await _refresh_htf_once(broker, s, engine.htf_bias, engine.htf_levels, engine.runners)


async def _rebuild_engine_htf(
    broker: "Broker", engine: "ExecutionEngine", s: "StrategyParams",
) -> None:
    """(Re)build the engine's HTF trackers to match the current strategy flags.

    Called at startup and on live config changes (PATCH /api/config,
    /api/strategy/reload). _build_htf_trackers returns (None, None) when both
    HTF features are disabled, so this also tears trackers down when toggled off.
    """
    bias_tracker, level_finder = await _build_htf_trackers(broker, s)
    engine.htf_bias = bias_tracker
    engine.htf_levels = level_finder


async def _async_main() -> int:
    cfg = load_config()
    _setup_logging(cfg.log_level)

    if not _acquire_pid_lock():
        try:
            other_pid = int(_PID_FILE.read_text().strip())
        except Exception:
            other_pid = 0
        logging.getLogger("topstep_bot").error(
            "Another bot instance is already running (PID %d). "
            "Stop it first. Exiting.", other_pid,
        )
        return 1

    # Capture log records for hourly health emails.
    _tail_handler = TailHandler(capacity=50)
    _tail_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    ))
    logging.getLogger().addHandler(_tail_handler)

    log.info(
        "Starting topstep-bot. mode=%s instrument=%s tfs=%s",
        cfg.mode, cfg.instrument, cfg.timeframes,
    )

    broker = await _build_broker(cfg)

    bot_cfg = load_bot_config(cfg.bot_config_path)

    if cfg.mode == "live":
        log.info("Fetching live account state...")
        live_balance, live_account, live_daily_pnl = await _fetch_live_state(bot_cfg.account_name)
        # If _fetch_live_state fell back to a different account (stale config),
        # push the resolved name into the broker so subscribe() authenticates correctly.
        if live_account and hasattr(broker, '_account_name') and broker._account_name != live_account:
            log.info("Updating broker account from %r → %r", broker._account_name, live_account)
            broker._account_name = live_account
        risk_cfg = config_for_account(live_account, live_balance, soft_buffer=cfg.soft_buffer)
        log.info(
            "Account: %s  balance=$%s  type=%s  starting=$%s  session_pnl=$%s",
            live_account, live_balance, risk_cfg.account_type, risk_cfg.starting_balance,
            live_daily_pnl,
        )
    else:
        risk_cfg = fifty_k_combine(soft_buffer=cfg.soft_buffer)
        # Paper mode doesn't fetch a real account; mirror the same names so
        # downstream startup notifications can read them uniformly.
        live_balance = risk_cfg.starting_balance
        live_account = ""
        live_daily_pnl = Decimal("0")

    risk_state = RiskState(config=risk_cfg)

    if cfg.mode == "live":
        # Sync realized balance and equity to broker truth at startup.
        risk_state.realized_balance = live_balance
        risk_state.equity_high_water = live_balance
        risk_state._current_equity = live_balance
        # Bootstrap daily P&L so a mid-session restart doesn't reset the DLL gate.
        risk_state.daily_pnl = live_daily_pnl
    runner = _build_runner(
        instrument=cfg.instrument,
        s=bot_cfg.strategy,
        enabled_killzones=bot_cfg.enabled_killzones,
        timeframe=bot_cfg.timeframes[0] if bot_cfg.timeframes else "1min",
        signal_instrument=bot_cfg.signal_instrument,
    )

    # Sync: enable only if both endpoint and secret are set. Outbox is
    # always created (it's a local file, harmless when unused) — but
    # we only pass it to the Journal if sync is on, so events stop
    # accumulating when there's nothing to ship them to.
    sync_enabled = bool(cfg.sync_endpoint_url and cfg.sync_hmac_secret)
    outbox: Outbox | None = None
    sender: Sender | None = None
    if sync_enabled:
        outbox = Outbox(cfg.sync_outbox_path)
        log.info("Outbox at %s", cfg.sync_outbox_path)
        sender = Sender(outbox, SenderConfig(
            endpoint_url=cfg.sync_endpoint_url,  # type: ignore[arg-type]
            hmac_secret=cfg.sync_hmac_secret,    # type: ignore[arg-type]
            bot_id=cfg.sync_bot_id,
        ))

    journal = Journal(outbox=outbox)
    # Load any fills already written to today's CSV so a mid-session restart
    # doesn't blank out the EOD summary.
    journal.bootstrap_fills_from_csv(_daily_csv_path())

    # Email notifier — no-ops if SMTP env vars are missing.
    notifier = EmailNotifier()
    if notifier.enabled:
        log.info("Email notifications enabled → %s", notifier.recipient)
    else:
        log.info("Email notifications disabled (no SMTP env vars)")

    # Discord notifier — no-ops if webhook URL env var is missing.
    discord = DiscordNotifier()
    if discord.enabled:
        log.info("Discord notifications enabled (webhook posts on signals + fills)")
    else:
        log.info("Discord notifications disabled (TOPSTEP_BOT_DISCORD_WEBHOOK_URL unset)")

    # Reconciler is constructed before the engine so we can pass
    # reconciler.notify_order_placed as the on_order_placed callback.
    # The reconciler does not call back into the engine, so there is
    # no circular dependency.
    reconciler = Reconciler(
        broker=broker,
        risk_state=risk_state,
        config=ReconcilerConfig(
            interval_seconds=cfg.reconcile_interval_seconds,
            balance_tolerance=Decimal("50"),
            grace_first_tick=True,
            grace_period_after_order_seconds=60.0,
        ),
        notifier=notifier,
    )

    # Records MFE/MAE over _MFE_WINDOW_BARS after each trade/rejection — pure
    # observation, writes excursions.csv via emit; never touches orders.
    excursion_tracker = ExcursionTracker(emit=_append_excursion_csv)

    engine = ExecutionEngine(
        broker=broker,
        risk_state=risk_state,
        runners=[runner],
        on_signal=_make_signal_journaler(journal, notifier, config_path=cfg.bot_config_path, discord=discord, excursion_tracker=excursion_tracker),
        on_order_placed=reconciler.notify_order_placed,
        on_pre_place=_make_pre_place(config_path=cfg.bot_config_path),
        on_reject=_make_reject_journaler(excursion_tracker=excursion_tracker),
        contracts=bot_cfg.contracts,
        risk_per_trade_pct=bot_cfg.risk_per_trade_pct,
        strategy_cfg=bot_cfg.strategy,
    )
    # Subscribe the journal to broker fills and bars.
    broker.on_fill(_make_fill_journaler(journal, notifier, discord=discord, excursion_tracker=excursion_tracker))
    broker.on_bar(_make_bar_journaler(journal, execution_instrument=cfg.instrument))
    broker.on_bar(_make_strategy_state_publisher(journal, engine, execution_instrument=cfg.instrument))

    # broker.on_bar handlers are async in this codebase; on_bar() itself is sync.
    async def _excursion_on_bar(b):
        excursion_tracker.on_bar(b)
    broker.on_bar(_excursion_on_bar)

    eod_scheduler = EndOfDayScheduler(
        journal=journal,
        risk_state=risk_state,
        notifier=notifier,
        trades_csv_path=_TRADES_CSV,
        daily_csv_fn=_daily_csv_path,
        discord=discord,
    )
    health_scheduler = HourlyHealthScheduler(
        notifier=notifier,
        risk_state=risk_state,
        tail_handler=_tail_handler,
    )
    # Wrap the reconciler's tick to feed reports into the journal.
    # We patch tick() in place — the original returns the report, our
    # wrapper records it then returns the same value.
    _original_tick = reconciler.tick

    async def journaled_tick():
        report = await _original_tick()
        await journal.record_reconcile(report)
        return report

    reconciler.tick = journaled_tick

    restart_event: asyncio.Event | None = (
        asyncio.Event() if cfg.mode == "paper" else None
    )

    static_dir = Path(__file__).parent / "api" / "static"
    api_app = build_app(
        risk_state=risk_state,
        reconciler=reconciler,
        journal=journal,
        static_dir=static_dir,
        outbox=outbox,
        bot_config_path=cfg.bot_config_path,
        effective_instrument=cfg.instrument,
        effective_timeframes=cfg.timeframes,
        restart_event=restart_event,
        mode=cfg.mode,
        broker=broker,
        engine=engine,
        runner_factory=_build_runner,
        # Re-warm VP after a /api/strategy/reload rebuilds the runner, so the
        # filter/target don't silently drop their prior-session profile.
        vp_warmup=lambda runner, bot_cfg_: _warm_up_vp(broker, runner, bot_cfg_),
        htf_rebuild=lambda body: _rebuild_engine_htf(broker, engine, body.strategy),
    )

    shutdown = asyncio.Event()
    _install_signal_handlers(shutdown)

    # Start the dashboard server. Bind to 127.0.0.1 only — Topstep's
    # local-only rule means there's no reason to expose this beyond
    # the local machine.
    import uvicorn
    api_config = uvicorn.Config(
        api_app,
        host="127.0.0.1",
        port=int(os.environ.get("TOPSTEP_BOT_PORT", "5174")),
        log_level="warning",  # don't double-log every request
        access_log=False,
    )
    api_server = uvicorn.Server(api_config)
    api_task = asyncio.create_task(api_server.serve())

    try:
        await broker.connect()
        await engine.start()
        await reconciler.start()
        if notifier.enabled or discord.enabled:
            await eod_scheduler.start()
        if notifier.enabled:
            await health_scheduler.start()
        if sender is not None:
            await sender.start()
            log.info("Sync enabled: %s", cfg.sync_endpoint_url)
        else:
            log.info("Sync disabled (no TOPSTEP_BOT_SYNC_ENDPOINT)")

        log.info(
            "Dashboard at http://127.0.0.1:%s",
            api_config.port,
        )

        started_at = datetime.now(_CT).strftime("%Y-%m-%d %H:%M:%S CT")
        killzones_str = ", ".join(bot_cfg.enabled_killzones or []) or "all"

        if notifier.enabled and cfg.mode == "live":
            account_line = f"  Account:    {live_account}\n" if live_account else ""
            await notifier.send(
                subject=f"Bot started — {started_at}",
                body=(
                    f"TopstepX bot is connected and ready to trade.\n\n"
                    f"  Started:    {started_at}\n"
                    f"{account_line}"
                    f"  Balance:    ${live_balance}\n"
                    f"  Daily P&L:  ${live_daily_pnl}\n"
                    f"  Instrument: {cfg.instrument}\n"
                    f"  Killzones:  {killzones_str}\n"
                    f"  Dashboard:  http://127.0.0.1:{api_config.port}"
                ),
            )

        if discord.enabled:
            await discord.send_startup(
                mode=cfg.mode,
                instrument=cfg.instrument,
                balance=str(live_balance),
                daily_pnl=str(live_daily_pnl),
                killzones=killzones_str,
                dashboard_url=f"http://127.0.0.1:{api_config.port}",
                started_at=started_at,
                account=live_account or None,
            )

        if cfg.mode == "paper":
            await _run_paper(  # type: ignore[arg-type]
                broker, cfg, shutdown,
                restart_event=restart_event,
                journal=journal,
                risk_state=risk_state,
                engine=engine,
            )
        else:
            await _run_live(broker, cfg, shutdown, runner=runner, bot_cfg=bot_cfg, engine=engine)

        return 0
    except Exception as exc:
        log.exception("Fatal error in main loop")
        if isinstance(exc, ProjectXConnectionError):
            subject = "Connection error — bot stopped"
            body = (
                f"The bot crashed with a ProjectXConnectionError.\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"The process has exited. Restart the bot to reconnect."
            )
        else:
            subject = "Fatal error — bot stopped"
            body = (
                f"The bot crashed with an unhandled exception.\n\n"
                f"{type(exc).__name__}: {exc}\n\n"
                f"The process has exited. Check the logs for the full traceback."
            )
        try:
            await notifier.send(subject, body)
        except Exception:
            pass
        return 1
    finally:
        log.info("Stopping EOD scheduler...")
        try:
            await eod_scheduler.stop()
        except Exception:
            log.exception("EOD scheduler stop failed")
        try:
            await health_scheduler.stop()
        except Exception:
            log.exception("Health scheduler stop failed")
        log.info("Stopping reconciler...")
        try:
            await reconciler.stop()
        except Exception:
            log.exception("Reconciler stop failed")
        log.info("Stopping engine...")
        try:
            await engine.stop()
        except Exception:
            log.exception("Engine stop failed")
        if sender is not None:
            log.info("Stopping sender (draining outbox)...")
            try:
                await sender.stop()
            except Exception:
                log.exception("Sender stop failed")
        if outbox is not None:
            try:
                outbox.close()
            except Exception:
                pass
        log.info("Stopping API server...")
        api_server.should_exit = True
        try:
            await asyncio.wait_for(api_task, timeout=3.0)
        except (asyncio.TimeoutError, Exception):
            api_task.cancel()
            try:
                await api_task
            except Exception:
                pass
        log.info("Disconnecting broker...")
        try:
            await broker.disconnect()
        except Exception:
            log.exception("Broker disconnect failed")
        _release_pid_lock()
        log.info("Shutdown complete.")


def main() -> int:
    try:
        return asyncio.run(_async_main())
    except KeyboardInterrupt:
        # Windows path or any case the signal handler missed.
        return 130


if __name__ == "__main__":
    sys.exit(main())
