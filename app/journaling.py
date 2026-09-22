"""CSV ledger + journaler builders.

Extracted from main.py: the trade/rejection/excursion CSV writers and the
on_pre_place / on_signal / on_fill / on_reject callback factories that the
ExecutionEngine and broker subscribe to. Pure record-keeping — these write
trades/*.csv and (via the excursion tracker) observe price; they never place
or mutate orders.

The CSV files are keyed for downstream join by /analyze-trades:
  - trades.csv       one row per fill (ENTRY rows carry signal/grade/slippage)
  - rejections.csv   one row per rejected/missed setup
  - excursions.csv   MFE/MAE over N bars, keyed by broker_order_id / rej-id
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from app.api.journal import Journal
from app.bot_config import BotConfig, load_bot_config
from app.sim.events import Fill
from app.execution.engine import OrderOutcome
from app.notifications import DiscordNotifier, EmailNotifier
from app.strategy.composer import Signal

log = logging.getLogger("topstep_bot")

_CT = ZoneInfo("America/Chicago")

# Bars of post-event price action to track for MFE/MAE (30 = 30 min on 1-min bars).
_MFE_WINDOW_BARS = 30


def _root_instrument(instrument: str) -> str:
    """Strip SDK contract suffix: 'CON.F.US.MNQ.M26' → 'MNQ', 'MNQ' → 'MNQ'."""
    return instrument.split(".")[-2] if "." in instrument else instrument


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
        bar_close = _last_bar_close.get(_root_instrument(signal.instrument))
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
            "grade":             signal.setup_grade.grade if signal.setup_grade else "",
            "grade_reason":      signal.setup_grade.reason if signal.setup_grade else "",
            "score":             str(signal.setup_grade.score) if signal.setup_grade else "",
            "order_bar_close":   str(bar_close) if bar_close is not None else "",
        }

    return pre_place


def _make_signal_journaler(
    journal: Journal,
    notifier: EmailNotifier | None = None,
    config_path: Path | None = None,
    discord: DiscordNotifier | None = None,
    excursion_tracker=None,
):
    """Build the on_signal callback bound to a specific Journal."""

    async def journal_signal(signal: Signal, outcome: OrderOutcome) -> None:
        if outcome.placed:
            _g = signal.setup_grade
            log.info(
                "SIGNAL PLACED  %s  grade=%s  size=%d  oid=%s | %s",
                signal.side.upper(), (_g.grade if _g is not None else "—"),
                outcome.allowed_size,
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
                g = signal.setup_grade
                grade_letter = g.grade if g is not None else "—"
                grade_line = grade_letter + (f" — {g.reason}" if g is not None and g.reason else "")
                subject = (
                    f"ENTRY [{grade_letter}] {signal.side.upper()} {signal.instrument} "
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
                    f"  Grade:  {grade_line}\n"
                    f"  Setup:  {signal.rationale}"
                )
                await notifier.send(subject, body)
        else:
            log.info(
                "SIGNAL DENIED  %s  reason=%s | %s",
                signal.side.upper(), outcome.reason, signal.rationale,
            )
            _g = signal.setup_grade
            _append_rejection_csv(
                ts=datetime.now(timezone.utc).isoformat(),
                instrument=signal.instrument, side=signal.side,
                reason=outcome.reason or "", grade=(_g.grade if _g else ""),
                score=(str(_g.score) if _g else ""),
                entry=str(signal.entry), stop=str(signal.stop),
                target=str(signal.target), killzone=signal.killzone or "",
                rationale=signal.rationale or "", source="engine",
            )
            if excursion_tracker is not None:
                excursion_tracker.open(
                    key=f"rej-{signal.instrument}-{outcome.reason}-{datetime.now(timezone.utc).timestamp():.0f}",
                    kind="rejection", side=signal.side, ref=signal.entry,
                    target=signal.target, stop=signal.stop, window_bars=_MFE_WINDOW_BARS,
                    instrument=signal.instrument,
                )
        if discord is not None and discord.enabled:
            await discord.send_signal(signal, outcome)
        await journal.record_signal(signal, outcome)

    return journal_signal


_TRADES_CSV = Path("trades/trades.csv")  # permanent master ledger
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
    # Grade + execution quality (ENTRY rows)
    "grade", "grade_reason", "score", "slippage", "exec_slippage",
    # Account that owns the fill — lets the session-P&L CSV fallback scope to
    # the live account across shadow/combine switches. Appended last so older
    # ledgers (whose header lacks it) stay column-aligned on the prefix.
    "account",
]

# Keyed by instrument (written in on_pre_place, before HTTP round-trip) then
# re-keyed to broker_order_id in journal_signal once the order ID is known.
# Falls back to instrument key in _append_fill_csv for market orders that fill
# during the HTTP await before journal_signal can re-key.
_pending_signal_meta: dict[str, dict] = {}

# Tracks the close of the last bar seen per root instrument.  Written by the
# on_bar handler registered by _make_bar_close_watcher(); read by pre_place to
# capture the "decision-bar close" so exec_slippage can be computed at fill time.
_last_bar_close: dict[str, Decimal] = {}


def _daily_csv_path(offset_days: int = 0) -> Path:
    """Today's trading-day CSV path (CT date, matches Topstep session boundary)."""
    ct_date = (datetime.now(_CT) + timedelta(days=offset_days)).strftime("%Y-%m-%d")
    return Path("trades") / f"trades_{ct_date}.csv"


def _entry_slippage(fill: Fill, meta: dict) -> str:
    """fill_price - signal_entry for ENTRY rows; "" otherwise or if entry unknown.

    Raw signed difference (interpret adversity by side downstream: a SHORT
    filling below its planned entry, or a LONG above, is adverse)."""
    if not fill.is_entry:
        return ""
    entry = meta.get("signal_entry")
    if not entry:
        return ""
    try:
        return str(Decimal(str(fill.fill_price)) - Decimal(str(entry)))
    except Exception:
        return ""


def _exec_slippage(fill: Fill, meta: dict) -> str:
    """fill_price - order_bar_close for ENTRY rows; '' otherwise or if unknown.

    Measures real execution quality (fill vs market at signal time) as opposed
    to `slippage` / _entry_slippage which measures fill vs FVG proximal edge
    (plan-deviation). On market entries this is typically 0–2 ticks; the large
    values in the `slippage` column come from the FVG-edge reference, not from
    poor execution.
    """
    if not fill.is_entry:
        return ""
    bar_close = meta.get("order_bar_close")
    if not bar_close:
        return ""
    try:
        return str(Decimal(str(fill.fill_price)) - Decimal(str(bar_close)))
    except Exception:
        return ""


def _make_bar_close_watcher():
    """Return an async on_bar handler that keeps _last_bar_close up to date."""

    async def on_bar(bar) -> None:
        _last_bar_close[_root_instrument(bar.instrument)] = bar.close

    return on_bar


def _append_fill_csv(fill: Fill, account_id: "str | int | None" = None) -> None:
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
        # Grade + slippage
        meta.get("grade", ""),
        meta.get("grade_reason", ""),
        meta.get("score", ""),
        _entry_slippage(fill, meta),
        _exec_slippage(fill, meta),
        str(account_id) if account_id is not None else "",
    ]
    for path in (_TRADES_CSV, _daily_csv_path()):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
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


_REJECTIONS_CSV = Path("trades/rejections.csv")
_REJECTIONS_HEADERS = [
    "ts", "instrument", "side", "reason", "grade", "score",
    "entry", "stop", "target", "killzone", "rationale", "source",
]


def _daily_rejections_path() -> Path:
    """Today's rejections CSV (CT date, matches Topstep session boundary)."""
    ct_date = datetime.now(_CT).strftime("%Y-%m-%d")
    return Path("trades") / f"rejections_{ct_date}.csv"


def _append_rejection_csv(
    *, ts: str, instrument: str, side: str, reason: str, grade: str = "",
    score: str = "", entry: str = "", stop: str = "", target: str = "",
    killzone: str = "", rationale: str = "", source: str = "",
) -> None:
    """Append one rejected/missed setup. source = 'engine' (vp/bias deny) or 'runner'."""
    row = [ts, instrument, side, reason, grade, score, entry, stop, target,
           killzone, rationale, source]
    for path in (_REJECTIONS_CSV, _daily_rejections_path()):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if write_header:
                    w.writerow(_REJECTIONS_HEADERS)
                w.writerow(row)
        except Exception:
            log.exception("_append_rejection_csv failed for %s", path)


_EXCURSIONS_CSV = Path("trades/excursions.csv")
_EXCURSIONS_HEADERS = [
    "key", "kind", "side", "ref", "stop", "target", "mfe", "mae",
    "reached_target", "stop_hit", "outcome",
]


def _daily_excursions_path() -> Path:
    """Today's excursions CSV (CT date, matches Topstep session boundary)."""
    ct_date = datetime.now(_CT).strftime("%Y-%m-%d")
    return Path("trades") / f"excursions_{ct_date}.csv"


def _append_excursion_csv(w) -> None:
    """Append one completed excursion window (MFE/MAE over N bars)."""
    row = [w.key, w.kind, w.side, str(w.ref),
           str(w.stop) if w.stop is not None else "",
           str(w.target) if w.target is not None else "",
           str(w.mfe), str(w.mae), str(w.reached_target),
           str(w.stop_hit), w.outcome]
    for path in (_EXCURSIONS_CSV, _daily_excursions_path()):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists()
            with path.open("a", newline="", encoding="utf-8") as f:
                wr = csv.writer(f)
                if write_header:
                    wr.writerow(_EXCURSIONS_HEADERS)
                wr.writerow(row)
        except Exception:
            log.exception("_append_excursion_csv failed for %s", path)


def _make_reject_journaler(excursion_tracker=None):
    """Build the on_reject callback: write runner-internal rejections to rejections.csv."""

    async def on_reject(info, instrument: str) -> None:
        _append_rejection_csv(
            ts=datetime.now(timezone.utc).isoformat(), instrument=instrument,
            side=info.side, reason=info.reason, grade=info.grade,
            score=str(info.score),
            entry=str(info.entry) if info.entry is not None else "",
            stop=str(info.stop) if info.stop is not None else "",
            target=str(info.target) if info.target is not None else "",
            killzone=info.killzone, rationale=info.rationale, source="runner",
        )
        if excursion_tracker is not None and info.entry is not None:
            excursion_tracker.open(
                key=f"rej-{instrument}-{info.reason}-{datetime.now(timezone.utc).timestamp():.0f}",
                kind="rejection", side=info.side, ref=info.entry,
                target=info.target, stop=info.stop, window_bars=_MFE_WINDOW_BARS,
                instrument=instrument,
            )

    return on_reject


def _make_fill_journaler(
    journal: Journal,
    notifier: EmailNotifier | None = None,
    discord: DiscordNotifier | None = None,
    excursion_tracker=None,
    account_id_getter=None,
):
    """Build the on_fill broker subscriber bound to a specific Journal.

    account_id_getter, if given, is a zero-arg callable returning the current
    account id; it is read per-fill so account switches are reflected in the
    `account` column without rebuilding the journaler.
    """

    async def on_fill(fill: Fill) -> None:
        # Provisional fills are the early-arrival fanout used to keep risk
        # state in sync; a corrected fanout follows. Skip CSV and notifier
        # work — only the corrected version should be logged or shipped.
        if getattr(fill, "is_provisional", False):
            await journal.record_fill(fill)  # journal also skips internally
            return
        # Open an MFE/MAE window BEFORE _append_fill_csv (which pops the meta).
        if fill.is_entry and excursion_tracker is not None:
            m = _pending_signal_meta.get(fill.broker_order_id, {})
            tgt = m.get("target")
            stp = m.get("stop")
            excursion_tracker.open(
                key=fill.broker_order_id or fill.instrument, kind="trade",
                side=fill.side, ref=Decimal(str(fill.fill_price)),
                target=Decimal(tgt) if tgt else None,
                stop=Decimal(stp) if stp else None, window_bars=_MFE_WINDOW_BARS,
                instrument=_root_instrument(fill.instrument),
            )
        acct = None
        if account_id_getter is not None:
            try:
                acct = account_id_getter()
            except Exception:
                acct = None
        _append_fill_csv(fill, account_id=acct)
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
