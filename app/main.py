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
import logging
import os
import signal
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

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
from app.notifications import EmailNotifier, EndOfDayScheduler
from app.replay import load_bars_csv
from app.risk.config import config_for_account, fifty_k_combine
from app.risk.state import RiskState
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.killzone import killzones_from_names
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
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
) -> StrategyRunner:
    zones = killzones_from_names(enabled_killzones) if enabled_killzones else None
    return StrategyRunner(
        instrument=instrument,
        liquidity=LiquidityTracker(LiquidityConfig(
            swing_lookback=s.swing_lookback,
            min_penetration=s.min_penetration,
            multi_bar_window=s.multi_bar_window,
            max_swings=50,
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
        )),
    )


async def _fetch_live_balance(account_name: str | None) -> tuple[Decimal, str]:
    """
    Authenticate and return (balance, resolved_account_name) for the selected
    account. Used before TradingSuite is created so RiskState gets the right
    starting config.
    """
    from project_x_py import ProjectX  # type: ignore
    async with ProjectX.from_env() as client:
        await client.authenticate()
        accounts = await client.list_accounts()

    if account_name:
        match = next((a for a in accounts if a.name == account_name), None)
        if match:
            return Decimal(str(match.balance)), match.name

    # Fall back to first tradeable account.
    tradeable = [a for a in accounts if a.canTrade]
    if tradeable:
        a = tradeable[0]
        return Decimal(str(a.balance)), a.name

    return Decimal("50000"), ""


async def _build_broker(cfg: AppConfig) -> Broker:
    """
    Build the right broker for the mode. Live import of TopstepXBroker
    is lazy so paper mode doesn't require project_x_py installed.
    """
    if cfg.mode == "paper":
        return PaperBroker(starting_balance=Decimal("50000"))

    from app.broker.topstepx import TopstepXBroker
    bot_cfg = load_bot_config(Path(os.environ.get("BOT_CONFIG_PATH", "bot_config.json")))
    return TopstepXBroker(account_name=bot_cfg.account_name, entry_mode=bot_cfg.entry_mode)


def _make_signal_journaler(journal: Journal, notifier: EmailNotifier | None = None):
    """Build the on_signal callback bound to a specific Journal."""

    async def journal_signal(signal: Signal, outcome: OrderOutcome) -> None:
        if outcome.placed:
            log.info(
                "SIGNAL PLACED  %s  size=%d  oid=%s | %s",
                signal.side.upper(), outcome.allowed_size,
                outcome.broker_order_id, signal.rationale,
            )
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
        await journal.record_signal(signal, outcome)

    return journal_signal


def _make_fill_journaler(journal: Journal, notifier: EmailNotifier | None = None):
    """Build the on_fill broker subscriber bound to a specific Journal."""

    async def on_fill(fill: Fill) -> None:
        await journal.record_fill(fill)
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
            new_runner = _build_runner(cfg.instrument, new_cfg.strategy, new_cfg.enabled_killzones)
            engine.runners = {cfg.instrument: new_runner}
        broker.reset()
        first_run = False


async def _run_live(
    broker: Broker,
    cfg: AppConfig,
    shutdown: asyncio.Event,
) -> None:
    """Live mode: subscribe, then block on shutdown."""
    await broker.subscribe([cfg.instrument], cfg.timeframes)
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


async def _async_main() -> int:
    cfg = load_config()
    _setup_logging(cfg.log_level)
    log.info(
        "Starting topstep-bot. mode=%s instrument=%s tfs=%s",
        cfg.mode, cfg.instrument, cfg.timeframes,
    )

    broker = await _build_broker(cfg)

    bot_cfg = load_bot_config(cfg.bot_config_path)

    if cfg.mode == "live":
        log.info("Fetching live account balance...")
        live_balance, live_account = await _fetch_live_balance(bot_cfg.account_name)
        risk_cfg = config_for_account(live_account, live_balance, soft_buffer=cfg.soft_buffer)
        log.info(
            "Account: %s  balance=$%s  type=%s  starting=$%s",
            live_account, live_balance, risk_cfg.account_type, risk_cfg.starting_balance,
        )
    else:
        risk_cfg = fifty_k_combine(soft_buffer=cfg.soft_buffer)

    risk_state = RiskState(config=risk_cfg)

    if cfg.mode == "live":
        # Sync realized balance and equity to broker truth at startup.
        risk_state.realized_balance = live_balance
        risk_state.equity_high_water = live_balance
        risk_state._current_equity = live_balance
    runner = _build_runner(cfg.instrument, bot_cfg.strategy, bot_cfg.enabled_killzones)

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

    # Email notifier — no-ops if SMTP env vars are missing.
    notifier = EmailNotifier()
    if notifier.enabled:
        log.info("Email notifications enabled → %s", notifier.recipient)
    else:
        log.info("Email notifications disabled (no SMTP env vars)")

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
        ),
    )

    engine = ExecutionEngine(
        broker=broker,
        risk_state=risk_state,
        runners=[runner],
        on_signal=_make_signal_journaler(journal, notifier),
        on_order_placed=reconciler.notify_order_placed,
    )
    # Subscribe the journal to broker fills and bars.
    broker.on_fill(_make_fill_journaler(journal, notifier))
    broker.on_bar(_make_bar_journaler(journal))

    eod_scheduler = EndOfDayScheduler(
        journal=journal,
        risk_state=risk_state,
        notifier=notifier,
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
        if notifier.enabled:
            await eod_scheduler.start()
        if sender is not None:
            await sender.start()
            log.info("Sync enabled: %s", cfg.sync_endpoint_url)
        else:
            log.info("Sync disabled (no TOPSTEP_BOT_SYNC_ENDPOINT)")

        log.info(
            "Dashboard at http://127.0.0.1:%s",
            api_config.port,
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
            await _run_live(broker, cfg, shutdown)

        return 0
    except Exception:
        log.exception("Fatal error in main loop")
        return 1
    finally:
        log.info("Stopping EOD scheduler...")
        try:
            await eod_scheduler.stop()
        except Exception:
            log.exception("EOD scheduler stop failed")
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
        log.info("Shutdown complete.")


def main() -> int:
    try:
        return asyncio.run(_async_main())
    except KeyboardInterrupt:
        # Windows path or any case the signal handler missed.
        return 130


if __name__ == "__main__":
    sys.exit(main())
