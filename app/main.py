"""
Entry point. Construct the full graph, run forever (or until the bar
stream ends in paper mode).

Run:
    TOPSTEP_BOT_MODE=paper \
    TOPSTEP_BOT_INSTRUMENT=MGC \
    TOPSTEP_BOT_PAPER_BARS=./bars.csv \
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
from app.execution.reconciler import Reconciler, ReconcilerConfig
from app.notifications import DiscordNotifier, EmailNotifier, EndOfDayScheduler, HourlyHealthScheduler, TailHandler
from project_x_py.exceptions import ProjectXConnectionError
from app.replay import load_bars_csv
from app.risk.config import config_for_account, fifty_k_combine
from app.risk.state import RiskState
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.killzone import killzones_from_names
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
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
        vp=VolumeProfileTracker(),
    )


_CT = ZoneInfo("America/Chicago")


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


def _make_pre_place(config_path: Path | None = None):
    """
    Build the on_pre_place callback.

    Called BEFORE await broker.place_bracket() so signal meta is written
    before a market-order fill can race in via WebSocket during the HTTP
    round-trip. Keyed by instrument (safe: pretrade gate ensures at most
    one open position per instrument at a time).
    """

    async def pre_place(signal: Signal, size: int) -> None:
        cfg_snap = load_bot_config(config_path) if config_path else BotConfig()
        _pending_signal_meta[signal.instrument] = {
            "signal_entry":      str(signal.entry),
            "stop":              str(signal.stop),
            "target":            str(signal.target),
            "killzone":          signal.killzone,
            "sweep_pattern":     signal.sweep_pattern,
            "sweep_extreme":     str(signal.sweep_extreme),
            "fvg_low":           str(signal.fvg_low) if signal.fvg_low else "",
            "fvg_high":          str(signal.fvg_high) if signal.fvg_high else "",
            "rationale":         signal.rationale,
            "contracts":         str(size),
            "entry_mode":        cfg_snap.entry_mode,
            "r_multiple":        str(cfg_snap.strategy.r_multiple),
            "stop_buffer":       str(cfg_snap.strategy.stop_buffer),
            "body_atr_multiple": str(cfg_snap.strategy.body_atr_multiple),
            "vp_enabled":        str(cfg_snap.strategy.vp_enabled),
        }

    return pre_place


def _make_signal_journaler(
    journal: Journal,
    notifier: EmailNotifier | None = None,
    config_path: Path | None = None,
    discord: DiscordNotifier | None = None,
):
    """Build the on_signal callback bound to a specific Journal."""

    async def journal_signal(signal: Signal, outcome: OrderOutcome) -> None:
        if outcome.placed:
            log.info(
                "SIGNAL PLACED  %s  size=%d  oid=%s | %s",
                signal.side.upper(), outcome.allowed_size,
                outcome.broker_order_id, signal.rationale,
            )
            if outcome.broker_order_id:
                # on_pre_place already wrote meta keyed by instrument.
                # Re-key to broker_order_id so the fill lookup hits reliably.
                # If a racing fill already consumed the instrument key,
                # pop returns None and we leave the meta where it was used.
                existing = _pending_signal_meta.pop(signal.instrument, None)
                if existing is not None:
                    _pending_signal_meta[outcome.broker_order_id] = existing
            if notifier is not None and notifier.enabled:
                subject = (
                    f"ENTRY {signal.side.upper()} {signal.instrument} "
                    f"x{outcome.allowed_size} @ {signal.entry}"
                )
                body = (
                    f"{signal.side.upper()} {signal.instrument} "
                    f"x{outcome.allowed_size}\n"
                    f"  Entry:  {signal.entry}\n"
                    f"  Stop:   {signal.stop}\n"
                    f"  Target: {signal.target}\n"
                    f"  Order:  {outcome.broker_order_id}\n"
                    f"  Killzone: {signal.killzone}\n"
                    f"  Setup:  {signal.rationale}"
                )
                await notifier.send(subject, body)
        else:
            log.info(
                "SIGNAL DENIED  %s  reason=%s | %s",
                signal.side.upper(), outcome.reason, signal.rationale,
            )
        if discord is not None and discord.enabled:
            await discord.send_signal(signal, outcome)
        await journal.record_signal(signal, outcome)

    return journal_signal


_TRADES_CSV = Path("trades.csv")  # permanent master ledger
_TRADES_HEADERS = [
    # Fill fields
    "ts", "instrument", "side", "type", "fill_price", "size", "realized_pnl",
    "broker_order_id",
    # Signal prices (ENTRY rows only)
    "signal_entry", "stop", "target",
    # Setup context
    "killzone", "sweep_pattern", "sweep_extreme", "fvg_low", "fvg_high",
    "rationale",
    # Config snapshot at signal time (ENTRY rows only)
    "contracts", "entry_mode", "r_multiple", "stop_buffer",
    "body_atr_multiple", "vp_enabled",
]

# Keyed by instrument (written in on_pre_place, before HTTP round-trip) then
# re-keyed to broker_order_id in journal_signal once the order ID is known.
# Falls back to instrument key in _append_fill_csv for market orders that fill
# during the HTTP await before journal_signal can re-key.
_pending_signal_meta: dict[str, dict] = {}


def _daily_csv_path() -> Path:
    """Today's trading-day CSV path (CT date, matches Topstep session boundary)."""
    ct_date = datetime.now(_CT).strftime("%Y-%m-%d")
    return Path(f"trades_{ct_date}.csv")


def _append_fill_csv(fill: Fill) -> None:
    """Append one fill row to master trades.csv and today's daily CSV."""
    if fill.is_entry:
        # Try broker_order_id first (normal path: journal_signal re-keyed it).
        # Fall back to fill.instrument (same as signal.instrument in paper mode).
        # Last resort: pop whatever single key is left — on_pre_place writes under
        # the strategy instrument name ("MGC") but fills arrive with the full
        # contract symbol ("CON.F.US.MGC.M26"), so the instrument fallback misses.
        # MAX_CONTRACTS gate ensures at most one signal is in flight, so if one
        # key remains after both lookups fail it must be ours.
        meta = _pending_signal_meta.pop(fill.broker_order_id, None)
        if meta is None:
            meta = _pending_signal_meta.pop(fill.instrument, None)
        if meta is None and len(_pending_signal_meta) == 1:
            _, meta = _pending_signal_meta.popitem()
        if meta is None:
            meta = {}
    else:
        meta = {}
    row = [
        fill.ts.isoformat(),
        fill.instrument,
        fill.side,
        "ENTRY" if fill.is_entry else "EXIT",
        fill.fill_price,
        fill.size,
        fill.realized_pnl_delta,
        fill.broker_order_id,
        # Signal prices
        meta.get("signal_entry", ""),
        meta.get("stop", ""),
        meta.get("target", ""),
        # Setup context
        meta.get("killzone", ""),
        meta.get("sweep_pattern", ""),
        meta.get("sweep_extreme", ""),
        meta.get("fvg_low", ""),
        meta.get("fvg_high", ""),
        meta.get("rationale", ""),
        # Config snapshot
        meta.get("contracts", ""),
        meta.get("entry_mode", ""),
        meta.get("r_multiple", ""),
        meta.get("stop_buffer", ""),
        meta.get("body_atr_multiple", ""),
        meta.get("vp_enabled", ""),
    ]
    for path in (_TRADES_CSV, _daily_csv_path()):
        try:
            write_header = not path.exists()
            # encoding="utf-8" is load-bearing: signal rationales contain non-cp1252
            # characters (e.g. "≥" U+2265 in "no VP level ≥2.0R"). Without it, Windows
            # defaults to cp1252 and writerow() raises UnicodeEncodeError, silently
            # dropping the ENTRY row. The analytics loader reads these files as utf-8.
            with path.open("a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow(_TRADES_HEADERS)
                w.writerow(row)
        except Exception:
            log.exception("_append_fill_csv failed for %s — fill not logged", path)


def _make_fill_journaler(
    journal: Journal,
    notifier: EmailNotifier | None = None,
    discord: DiscordNotifier | None = None,
):
    """Build the on_fill broker subscriber bound to a specific Journal."""

    async def on_fill(fill: Fill) -> None:
        # Provisional fills are the early-arrival fanout used to keep risk
        # state in sync; a corrected fanout follows. Skip CSV and notifier
        # work — only the corrected version should be logged or shipped.
        if getattr(fill, "is_provisional", False):
            await journal.record_fill(fill)  # journal also skips internally
            return
        _append_fill_csv(fill)
        await journal.record_fill(fill)
        if discord is not None and discord.enabled:
            await discord.send_fill(fill)
        # Notify on EXIT fills only — entry confirmation is covered by the
        # signal-placed email already.
        if (
            notifier is not None
            and notifier.enabled
            and not fill.is_entry
        ):
            pnl = fill.realized_pnl_delta
            sign = "+" if pnl >= 0 else "-"
            subject = f"EXIT {fill.instrument} {sign}${abs(pnl)}"
            body = (
                f"Position closed on {fill.instrument}\n"
                f"  Side:    {fill.side.upper()}\n"
                f"  Price:   {fill.fill_price}\n"
                f"  Size:    {fill.size}\n"
                f"  P&L:     {sign}${abs(pnl)}\n"
                f"  Order:   {fill.broker_order_id}"
            )
            await notifier.send(subject, body)

    return on_fill


def _make_bar_journaler(journal: Journal):
    """Build the on_bar subscriber that streams bars to the chart."""
    from app.broker.events import Bar as BarEvent

    async def on_bar(bar: BarEvent) -> None:
        journal.publish_bar(bar)

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
            )
            engine.runners = {cfg.instrument: new_runner}
            engine.strategy_cfg = new_cfg.strategy  # keep VP cfg in sync on restart
        broker.reset()
        first_run = False


async def _run_live(
    broker: Broker,
    cfg: AppConfig,
    shutdown: asyncio.Event,
    runner: "StrategyRunner | None" = None,
    bot_cfg: "BotConfig | None" = None,
) -> None:
    """Live mode: subscribe, warm up VP, then block on shutdown."""
    await broker.subscribe([cfg.instrument], cfg.timeframes)
    if runner is not None and bot_cfg is not None and runner.vp is not None:
        await _warm_up_vp(broker, runner, bot_cfg)
    log.info(
        "Live mode running. Instrument=%s timeframes=%s. Ctrl+C to stop.",
        cfg.instrument, cfg.timeframes,
    )
    await shutdown.wait()


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
        cfg.instrument, bot_cfg.strategy, bot_cfg.enabled_killzones,
        timeframe=bot_cfg.timeframes[0] if bot_cfg.timeframes else "1min",
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

    engine = ExecutionEngine(
        broker=broker,
        risk_state=risk_state,
        runners=[runner],
        on_signal=_make_signal_journaler(journal, notifier, config_path=cfg.bot_config_path, discord=discord),
        on_order_placed=reconciler.notify_order_placed,
        on_pre_place=_make_pre_place(config_path=cfg.bot_config_path),
        contracts=bot_cfg.contracts,
        risk_per_trade_pct=bot_cfg.risk_per_trade_pct,
        strategy_cfg=bot_cfg.strategy,
    )
    # Subscribe the journal to broker fills and bars.
    broker.on_fill(_make_fill_journaler(journal, notifier, discord=discord))
    broker.on_bar(_make_bar_journaler(journal))

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
            await _run_live(broker, cfg, shutdown, runner=runner, bot_cfg=bot_cfg)

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
