"""
TopstepXBroker — live (and demo) implementation backed by project-x-py.

This is the only file in the bot that imports `project_x_py`. Everything
else talks to the `Broker` Protocol in protocol.py.

Notes / SDK quirks isolated here:

  - SDK uses int sides (0=Buy, 1=Sell). We translate to "long"/"short".
  - `place_bracket_order` returns a response object with `.success` and
    nested order IDs. We unpack into our flat `BracketResult`.
  - `account_info.balance` is a float. We convert to Decimal at the
    boundary; never let floats leak into the risk module.
  - The SDK's EventBus uses string keys ("NEW_BAR", etc.). We register
    once and fan out to our typed handlers.
  - Mark-to-market is not a single SDK event — we synthesize it from
    position updates + last trade price on each bar close. This is good
    enough at 1m/5m granularity; if you need tick-by-tick MTM, hook
    into `data.on_quote` instead.
"""

from __future__ import annotations

import asyncio
import csv
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .events import Bar, BracketResult, BrokerPosition, Fill, MarkToMarket, Side
from .protocol import BarHandler, EquityHandler, FillHandler

log = logging.getLogger(__name__)


# project-x-py side encoding. Centralize so the rest of the file stays clean.
SIDE_BUY = 0
SIDE_SELL = 1

# Dollar P&L per 1-point (1 dollar) price move per contract.
# Used to convert (exit_price - entry_price) → realized dollars.
_POINT_VALUE: dict[str, Decimal] = {
    "MGC":  Decimal("10"),    # Micro Gold: 10 oz
    "GC":   Decimal("100"),   # Gold: 100 oz
    "MNQ":  Decimal("2"),     # Micro Nasdaq-100
    "NQ":   Decimal("20"),    # Nasdaq-100
    "MES":  Decimal("5"),     # Micro E-mini S&P 500
    "ES":   Decimal("50"),    # E-mini S&P 500
    "MCL":  Decimal("100"),   # Micro WTI Crude Oil
    "CL":   Decimal("1000"),  # WTI Crude Oil
    "M2K":  Decimal("5"),     # Micro Russell 2000
    "RTY":  Decimal("50"),    # Russell 2000
}


def _point_value(instrument: str) -> Decimal:
    """Return the dollar value of a 1-point price move for this instrument."""
    # Strip SDK contract suffix: "CON.F.US.MGC.M26" → "MGC"
    sym = instrument.split(".")[-2] if "." in instrument else instrument.upper()
    val = _POINT_VALUE.get(sym)
    if val is None:
        log.warning("Unknown instrument %r — P&L will be in price units, not dollars", sym)
        return Decimal("1")
    return val


@dataclass(frozen=True)
class PartialPlan:
    """How a partial-profit / BE entry is split. Pure data, no SDK."""
    partial_price: Decimal   # the R-multiple level (scale-out price; also the BE trigger for 1-lots)
    partial_size: int        # contracts to scale out (0 when entry size == 1)
    remaining_size: int      # contracts left after the partial
    be_price: Decimal        # break-even = the actual entry fill price


def _partial_plan(
    entry_price: Decimal,
    stop: Decimal,
    size: int,
    partial_r: Decimal,
) -> "PartialPlan | None":
    """Compute the partial/BE plan, or None when partials are disabled.

    partial_price = entry ± R*partial_r (R = |entry-stop|); + for long, - for short.
    Long vs short is inferred from stop position: stop below entry → long.
    partial_size = size // 2 (0 for a 1-lot). be_price = entry_price.
    """
    if partial_r <= 0:
        return None
    r = abs(entry_price - stop)
    is_long = stop < entry_price
    partial_price = entry_price + r * partial_r if is_long else entry_price - r * partial_r
    partial_size = size // 2
    return PartialPlan(
        partial_price=partial_price,
        partial_size=partial_size,
        remaining_size=size - partial_size,
        be_price=entry_price,
    )


def _to_internal_side(sdk_side: int) -> Side:
    return "long" if sdk_side == SIDE_BUY else "short"


def _to_sdk_side(side: Side) -> int:
    return SIDE_BUY if side == "long" else SIDE_SELL


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Intrabar recorder: snapshot the forming bar this often (seconds) and append
# to a single rolling CSV per instrument. Pure observer — off the order path.
_INTRABAR_SAMPLE_SECONDS = 5
_INTRABAR_HEADERS = [
    "sample_ts", "instrument", "bar_minute",
    "open", "high", "low", "close", "volume",
]


def _intrabar_row(bar: Bar, sample_ts: datetime) -> list[str]:
    """Build one intrabar CSV row from a forming-bar snapshot, in header order."""
    return [
        sample_ts.isoformat(),
        bar.instrument,
        bar.ts.isoformat(),
        str(bar.open),
        str(bar.high),
        str(bar.low),
        str(bar.close),
        str(bar.volume),
    ]


def _append_intrabar_csv(
    bar: Bar, sample_ts: datetime, path: Path | None = None
) -> None:
    """Append one snapshot row to intrabar_<instrument>.csv, writing the header
    once if the file does not yet exist (single rolling file, append-forever)."""
    p = path or Path(f"intrabar_{bar.instrument}.csv")
    new_file = not p.exists()
    with p.open("a", newline="") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(_INTRABAR_HEADERS)
        writer.writerow(_intrabar_row(bar, sample_ts))


def _tf_seconds_local(tf: str) -> int:
    """Parse '1min', '5min', '1h' → seconds."""
    t = tf.strip().lower()
    if t.endswith("h"):
        return int(t[:-1]) * 3600
    if t.endswith("min"):
        return int(t[:-3]) * 60
    if t.endswith("d"):
        return int(t[:-1]) * 86400
    return 60


class TopstepXBroker:
    """
    Live broker. Async, single-process, single-account.

    Lifecycle:
        broker = TopstepXBroker()
        broker.on_bar(strategy.on_bar)
        broker.on_fill(engine.on_fill)
        broker.on_equity(risk_state.on_equity)
        await broker.connect()
        await broker.subscribe(["MGC"], ["1min", "5min"])
        # ... runs forever ...
        await broker.disconnect()
    """

    def __init__(self, account_name: str | None = None, entry_mode: str = "market", partial_profit_r: Decimal = Decimal("0")) -> None:
        self._suite = None  # project_x_py.TradingSuite, lazily imported
        self._account_name = account_name
        self.entry_mode = entry_mode  # "market" or "limit"
        self.partial_profit_r = partial_profit_r  # 0 = disabled; >0 = take half at NxR then BE (hot-applied via PATCH /api/config)
        self._bar_handlers: list[BarHandler] = []
        self._fill_handlers: list[FillHandler] = []
        self._equity_handlers: list[EquityHandler] = []
        self._instruments: list[str] = []
        self._known_order_ids: set[str] = set()  # for fill dedup
        # Pending limit entries awaiting fill → then place stop + target
        self._pending_brackets: dict[str, dict] = {}
        # OCO pairs: stop_id ↔ target_id. When either fills, the other is cancelled.
        self._exit_pairs: dict[str, str] = {}
        # Partials path (only used when partial_profit_r > 0). Each leg order_id
        # maps to the same shared group dict. Kept separate from _exit_pairs so the
        # disabled path is byte-for-byte unchanged.
        self._exit_groups: dict[str, dict] = {}
        # Break-even watches for 1-lot entries, keyed by instrument. The quote
        # handler moves the stop to BE once price crosses trigger_price.
        self._be_watches: dict[str, dict] = {}
        # Market orders fill in microseconds — the ORDER_FILLED event can arrive via
        # WebSocket before the HTTP response returns and we store the order_id in
        # _pending_brackets. Buffer those early fills here and replay them once the
        # bracket is registered.
        self._early_fills: dict[str, "Fill"] = {}
        # Dedup: ORDER_FILLED, FILL, and POSITION_CHANGED can all fire for the same
        # order. Track which order IDs we have already processed so duplicates are
        # silently dropped instead of double-counting open_contracts in RiskState.
        self._processed_fill_ids: set[str] = set()
        # Flatten orders placed by flatten() — maps order_id to entry context so
        # the fill handler can compute realized P&L the same way stop/target exits do.
        self._flatten_order_ids: dict[str, dict] = {}
        # Tick-aggregated forming bar: accumulates quote mid-prices within the current
        # minute. Updated by the QUOTE_UPDATE handler registered in subscribe().
        self._forming_bar: Bar | None = None
        self._forming_bar_minute: datetime | None = None
        # Background task that snapshots the forming bar to intrabar_<instr>.csv.
        # Started at the end of subscribe(), cancelled in disconnect().
        self._intrabar_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Authenticate and create the TradingSuite. Idempotent."""
        if self._suite is not None:
            return
        # Import lazily so the test suite doesn't need the SDK installed.
        from project_x_py import TradingSuite  # type: ignore

        # The first instrument is the primary; we'll subscribe more
        # via subscribe() if needed. project-x-py's TradingSuite is
        # instrument-bound, so multi-instrument trading needs one
        # suite per symbol — we'll handle that when we get there.
        # For now, defer suite creation until subscribe() so we know
        # which symbol to bind.
        self._TradingSuite = TradingSuite
        log.info("TopstepXBroker.connect() — SDK loaded, awaiting subscribe()")

    async def disconnect(self) -> None:
        if self._intrabar_task is not None:
            self._intrabar_task.cancel()
            try:
                await self._intrabar_task
            except asyncio.CancelledError:
                pass
            self._intrabar_task = None
        if self._suite is not None:
            try:
                await self._suite.disconnect()
            except Exception as e:
                log.warning("Error during disconnect: %s", e)
            self._suite = None

    async def _intrabar_sampler_loop(self) -> None:
        """Every _INTRABAR_SAMPLE_SECONDS, snapshot the forming bar to CSV.

        Pure observer: reads self._forming_bar only. A disk/serialization error
        is logged at ERROR and the loop continues — it never crashes the broker
        and never dies silently (Rule 12). CancelledError exits cleanly.
        """
        while True:
            try:
                await asyncio.sleep(_INTRABAR_SAMPLE_SECONDS)
                bar = self._forming_bar
                if bar is not None:
                    _append_intrabar_csv(bar, _utcnow())
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("intrabar sampler: failed to record snapshot")

    # ------------------------------------------------------------------
    # Account info
    # ------------------------------------------------------------------

    async def account_balance(self) -> Decimal:
        self._require_connected()
        # SDK returns a float — convert at the boundary.
        return Decimal(str(self._suite.client.account_info.balance))

    async def get_positions(self) -> list[BrokerPosition]:
        self._require_connected()
        # The SDK's Position model doesn't tolerate extra API fields
        # (e.g. contractDisplayName) so we bypass it and call the REST
        # endpoint directly via the authenticated HTTP client.
        raw = await self._raw_positions()
        primary = self._instruments[0] if self._instruments else ""
        positions = []
        for p in raw:
            size = int(p.get("size", 0) or 0)
            if size == 0:
                continue
            # type: 0=UNDEFINED, 1=LONG, 2=SHORT
            ptype = int(p.get("type", 0))
            if ptype == 1:
                side: Side = "long"
            elif ptype == 2:
                side = "short"
            else:
                continue
            positions.append(BrokerPosition(
                instrument=primary,
                side=side,
                size=size,
                average_price=Decimal(str(p.get("averagePrice") or 0)),
                unrealized_pnl=Decimal(str(p.get("unrealizedPnl") or 0)),
            ))
        return positions

    async def _raw_positions(self) -> list[dict]:
        """Call the positions API directly, tolerating extra fields the SDK rejects.

        Raises on any failure so the reconciler's outer try/except can abort
        the tick safely instead of misreading an API failure as "no positions".
        Silently returning [] would make broker_contracts=0 and trigger a false
        drift alarm + emergency flatten even when a position is genuinely open.
        """
        account_info = self._suite.client.account_info
        account_id = account_info.id if account_info else None
        payload = {"accountId": account_id} if account_id else {}
        resp = await self._suite.client._make_request(
            "POST", "/Position/searchOpen", data=payload
        )
        if resp is None:
            raise RuntimeError("_raw_positions: null response from /Position/searchOpen")
        if isinstance(resp, dict):
            if not resp.get("success", False):
                raise RuntimeError(
                    f"_raw_positions: API returned success=false: {resp}"
                )
            return resp.get("positions") or []
        if isinstance(resp, list):
            return resp
        raise RuntimeError(f"_raw_positions: unexpected response type {type(resp)}: {resp}")

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def _get_account_id(self) -> int | None:
        try:
            info = self._suite.client.account_info
            return info.id if info else None
        except Exception:
            return None

    async def place_bracket(
        self,
        instrument: str,
        side: Side,
        size: int,
        entry: Decimal,
        stop: Decimal,
        target: Decimal,
    ) -> BracketResult:
        self._require_connected()
        # Dispatch based on self.entry_mode (set from config), fall back to env var.
        mode = self.entry_mode or os.environ.get("TOPSTEP_BOT_ENTRY_MODE", "market")
        if mode.lower() == "limit":
            return await self.place_limit_bracket(instrument, side, size, entry, stop, target)
        # Default: market fill + stop/target placed after fill confirmed.
        return await self.place_market_bracket(instrument, side, size, entry, stop, target)

    async def place_market_bracket(
        self,
        instrument: str,
        side: Side,
        size: int,
        entry: Decimal,
        stop: Decimal,
        target: Decimal,
    ) -> BracketResult:
        """
        Market fill + bracket after fill. Submits a market entry order for
        immediate execution, then places stop and target as separate orders
        once the SDK fires a fill event for the entry. This avoids the SDK
        bracket wrapper entirely and gives us confirmed fill price before
        placing protective orders.
        """
        self._require_connected()
        sdk_side = _to_sdk_side(side)
        close_sdk_side = SIDE_SELL if sdk_side == SIDE_BUY else SIDE_BUY
        account_id = self._get_account_id()

        try:
            resp = await self._suite.orders.place_market_order(
                self._suite.instrument_id,
                sdk_side,
                size,
                account_id,
            )
        except Exception as e:
            log.exception("place_market_bracket: entry order failed for %s", instrument)
            return BracketResult(
                success=False,
                entry_order_id=None,
                stop_order_id=None,
                target_order_id=None,
                error=str(e),
            )

        if not getattr(resp, "success", False):
            err = getattr(resp, "errorMessage", "market entry rejected")
            return BracketResult(
                success=False,
                entry_order_id=None,
                stop_order_id=None,
                target_order_id=None,
                error=str(err),
            )

        entry_order_id = self._safe_str(getattr(resp, "orderId", None))
        if not entry_order_id:
            return BracketResult(
                success=False,
                entry_order_id=None,
                stop_order_id=None,
                target_order_id=None,
                error="no order ID in market entry response",
            )

        # Store stop/target as offsets from signal entry so they anchor to the
        # actual fill price, not the signal price. Market orders fill at the
        # current market price, which may differ from the signal's entry price.
        # Using offsets keeps stop/target the correct distance from where we filled.
        self._pending_brackets[entry_order_id] = {
            "stop_offset": stop - entry,
            "target_offset": target - entry,
            "close_sdk_side": close_sdk_side,
            "size": size,
            "account_id": account_id,
            "partial_r": self.partial_profit_r,
            "entry_side": side,
            "instrument": instrument,
        }
        self._known_order_ids.add(entry_order_id)
        log.info(
            "Market entry placed: order=%s stop_offset=%s target_offset=%s — watching for fill",
            entry_order_id, stop - entry, target - entry,
        )

        # Market orders can fill before the HTTP response returns. If the fill event
        # already arrived and was buffered, replay it now that we're registered.
        early = self._early_fills.pop(entry_order_id, None)
        if early is not None:
            bracket_data = self._pending_brackets.pop(entry_order_id)
            bracket_data["fill_price"] = early.fill_price
            log.info(
                "Replaying early entry fill order=%s @ %s — placing stop+target",
                entry_order_id, early.fill_price,
            )
            if bracket_data.get("partial_r", Decimal("0")) > 0:
                asyncio.create_task(self._place_partial_bracket_after_fill(bracket_data))
            else:
                asyncio.create_task(self._place_bracket_after_fill(bracket_data))
            # Re-fan a non-provisional copy so the journal/UI record the entry.
            # The provisional fanout in _on_fill_event already updated risk
            # state's open_contracts; this copy uses contracts_delta=0 to avoid
            # double-counting while still letting the journal see is_provisional=False.
            confirmed = Fill(
                ts=early.ts,
                instrument=early.instrument,
                side=early.side,
                fill_price=early.fill_price,
                size=early.size,
                is_entry=True,
                realized_pnl_delta=early.realized_pnl_delta,
                contracts_delta=0,
                broker_order_id=early.broker_order_id,
                is_stop=early.is_stop,
                is_provisional=False,
            )
            asyncio.create_task(self._fanout(self._fill_handlers, confirmed))

        return BracketResult(
            success=True,
            entry_order_id=entry_order_id,
            stop_order_id=None,   # placed after fill
            target_order_id=None,
            error=None,
        )

    async def place_limit_bracket(
        self,
        instrument: str,
        side: Side,
        size: int,
        entry: Decimal,
        stop: Decimal,
        target: Decimal,
    ) -> BracketResult:
        """
        Place a standalone limit entry at the FVG level. When the SDK fires a
        fill event for that order, _place_bracket_after_fill() is called to
        submit the stop and target. This bypasses the SDK's 60-second fill
        timeout entirely — the limit order stays working until cancelled or filled.
        """
        self._require_connected()
        sdk_side = _to_sdk_side(side)
        close_sdk_side = SIDE_SELL if sdk_side == SIDE_BUY else SIDE_BUY
        account_id = self._get_account_id()

        try:
            resp = await self._suite.orders.place_limit_order(
                self._suite.instrument_id,
                sdk_side,
                size,
                float(entry),
                account_id,
            )
        except Exception as e:
            log.exception("place_limit_bracket: entry order failed for %s", instrument)
            return BracketResult(
                success=False,
                entry_order_id=None,
                stop_order_id=None,
                target_order_id=None,
                error=str(e),
            )

        if not getattr(resp, "success", False):
            err = getattr(resp, "errorMessage", "limit entry rejected")
            return BracketResult(
                success=False,
                entry_order_id=None,
                stop_order_id=None,
                target_order_id=None,
                error=str(err),
            )

        entry_order_id = self._safe_str(getattr(resp, "orderId", None))
        if not entry_order_id:
            return BracketResult(
                success=False,
                entry_order_id=None,
                stop_order_id=None,
                target_order_id=None,
                error="no order ID in limit entry response",
            )

        # Store offsets so the fill handler can anchor stop/target to actual fill price.
        self._pending_brackets[entry_order_id] = {
            "stop_offset": stop - entry,
            "target_offset": target - entry,
            "close_sdk_side": close_sdk_side,
            "size": size,
            "account_id": account_id,
            "partial_r": self.partial_profit_r,
            "entry_side": side,
            "instrument": instrument,
        }
        self._known_order_ids.add(entry_order_id)
        log.info(
            "Limit entry placed: order=%s entry=%s stop_offset=%s target_offset=%s — watching for fill",
            entry_order_id, entry, stop - entry, target - entry,
        )

        return BracketResult(
            success=True,
            entry_order_id=entry_order_id,
            stop_order_id=None,   # placed after fill
            target_order_id=None,
            error=None,
        )

    async def _place_bracket_after_fill(self, bracket: dict) -> None:
        """Invoked via create_task when a pending entry fills."""
        fill_price = bracket["fill_price"]

        if not fill_price or fill_price == Decimal("0"):
            # filledPrice wasn't in the ORDER_FILLED event (common for market orders).
            # Fall back to the open position's averagePrice — reliable here because
            # this task is created after the fill event fires, so the position exists.
            log.warning(
                "_place_bracket_after_fill: fill_price is zero — querying position for averagePrice"
            )
            try:
                positions = await self.get_positions()
                if positions:
                    fill_price = positions[0].average_price
                    log.info(
                        "_place_bracket_after_fill: using position averagePrice=%s as fill_price",
                        fill_price,
                    )
            except Exception:
                log.exception("_place_bracket_after_fill: could not get position averagePrice")

            if not fill_price or fill_price == Decimal("0"):
                log.error(
                    "_place_bracket_after_fill: fill_price still zero after position query — "
                    "cannot place bracket, position is unprotected"
                )
                return

        stop = fill_price + bracket["stop_offset"]
        target = fill_price + bracket["target_offset"]
        close_sdk_side = bracket["close_sdk_side"]
        size = bracket["size"]
        account_id = bracket["account_id"]
        log.info(
            "_place_bracket_after_fill: fill=%s stop=%s target=%s",
            fill_price, stop, target,
        )

        async def _place_stop() -> str | None:
            try:
                resp = await self._suite.orders.place_stop_order(
                    self._suite.instrument_id,
                    close_sdk_side,
                    size,
                    float(stop),
                    account_id,
                )
                if getattr(resp, "success", False):
                    oid = str(resp.orderId)
                    log.info("Stop placed: order=%s @ %s", oid, stop)
                    return oid
                log.error("Stop order rejected: stop=%s resp=%s", stop, resp)
            except Exception:
                log.exception("_place_bracket_after_fill: stop order failed")
            return None

        async def _place_target() -> str | None:
            try:
                resp = await self._suite.orders.place_limit_order(
                    self._suite.instrument_id,
                    close_sdk_side,
                    size,
                    float(target),
                    account_id,
                )
                if getattr(resp, "success", False):
                    oid = str(resp.orderId)
                    log.info("Target placed: order=%s @ %s", oid, target)
                    return oid
                log.error("Target order rejected: target=%s resp=%s", target, resp)
            except Exception:
                log.exception("_place_bracket_after_fill: target order failed")
            return None

        # Place stop and target simultaneously — no window where one exists without the other.
        stop_id, target_id = await asyncio.gather(_place_stop(), _place_target())

        # Register as OCO pair. Store entry context so whichever leg fills
        # can compute realized P&L from the price difference.
        if stop_id and target_id:
            entry_side = "long" if close_sdk_side == SIDE_SELL else "short"
            ctx = {
                "entry_price": fill_price,
                "entry_side": entry_side,
                "size": size,
            }
            self._exit_pairs[stop_id]   = {**ctx, "paired_id": target_id}
            self._exit_pairs[target_id] = {**ctx, "paired_id": stop_id}
            log.info("OCO pair registered: stop=%s target=%s entry=%s side=%s",
                     stop_id, target_id, fill_price, entry_side)

            # Stop or target may have filled before this registration completed
            # (SDK fires ORDER_FILLED while we were awaiting asyncio.gather above).
            # Those fills were buffered in _early_fills as apparent ENTRY fills.
            # Re-process them now with the correct EXIT classification and P&L.
            for oid in (stop_id, target_id):
                early_exit = self._early_fills.pop(oid, None)
                if early_exit is not None:
                    log.info(
                        "Replaying early exit fill: order=%s @ %s (arrived before OCO registered)",
                        oid, early_exit.fill_price,
                    )
                    asyncio.create_task(self._reprocess_early_exit(early_exit))

    async def _place_partial_bracket_after_fill(self, bracket: dict) -> None:
        """Partials path: place stop + partial-target + final-target (size>=2),
        or stop + target + arm a BE-watch (size==1). Registers an exit group so
        _handle_group_fill can drive the partial->BE transition.

        Stop is placed FIRST so the whole position is protected before anything
        else. Mirrors _place_bracket_after_fill's fill-price fallback.
        """
        fill_price = bracket["fill_price"]
        if not fill_price or fill_price == Decimal("0"):
            try:
                positions = await self.get_positions()
                if positions:
                    fill_price = positions[0].average_price
            except Exception:
                log.exception("_place_partial_bracket_after_fill: could not get averagePrice")
            if not fill_price or fill_price == Decimal("0"):
                log.error("_place_partial_bracket_after_fill: fill_price still zero — falling back to plain bracket")
                await self._place_bracket_after_fill(bracket)
                return

        stop = fill_price + bracket["stop_offset"]
        target = fill_price + bracket["target_offset"]
        close_sdk_side = bracket["close_sdk_side"]
        size = bracket["size"]
        account_id = bracket["account_id"]
        instrument = bracket["instrument"]
        entry_side = bracket["entry_side"]

        plan = _partial_plan(fill_price, stop, size, bracket["partial_r"])
        if plan is None:
            await self._place_bracket_after_fill(bracket)
            return

        # 1) Stop (full size) FIRST.
        stop_id = await self._place_stop(close_sdk_side, size, stop, account_id)
        if stop_id is None:
            log.error("_place_partial_bracket_after_fill: stop placement failed — position UNPROTECTED")
            return

        # 2) Final target at remaining size.
        target_id = await self._place_limit(close_sdk_side, plan.remaining_size, target, account_id)

        # 3) Partial-target leg (size>=2 only).
        partial_id = None
        if plan.partial_size > 0:
            partial_id = await self._place_limit(close_sdk_side, plan.partial_size, plan.partial_price, account_id)
            if partial_id is None:
                # Degrade to a full-size 2-leg bracket: bump target back to full size.
                log.error("partial-target placement failed — degrading to plain bracket")
                if target_id is not None:
                    await self._cancel_order(target_id)
                target_id = await self._place_limit(close_sdk_side, size, target, account_id)
                plan = None  # signal: no partial this trade

        if target_id is None:
            log.error("_place_partial_bracket_after_fill: target placement failed")
            return

        group = {
            "instrument": instrument,
            "entry_price": fill_price,
            "entry_side": entry_side,
            "stop_id": stop_id,
            "partial_id": partial_id,
            "target_id": target_id,
            "be_price": fill_price,
            "partial_size": plan.partial_size if plan else 0,
            "remaining_size": plan.remaining_size if plan else size,
            "partial_filled": False,
            "close_sdk_side": close_sdk_side,
            "account_id": account_id,
        }
        for oid in (stop_id, partial_id, target_id):
            if oid is not None:
                self._exit_groups[oid] = group
        log.info(
            "Partial group registered: stop=%s partial=%s target=%s entry=%s side=%s",
            stop_id, partial_id, target_id, fill_price, entry_side,
        )

        # size==1: no partial leg — arm a BE-watch on the quote stream.
        if plan and plan.partial_size == 0:
            self._be_watches[instrument] = {
                "instrument": instrument,
                "side": entry_side,
                "trigger_price": plan.partial_price,
                "be_price": plan.be_price,
                "stop_id": stop_id,
                "armed": True,
            }

        # Re-process any leg fill that arrived before the group was registered.
        for oid in (stop_id, partial_id, target_id):
            if oid is None:
                continue
            early = self._early_fills.pop(oid, None)
            if early is not None:
                log.info("Replaying early group-leg fill: order=%s", oid)
                asyncio.create_task(self._handle_group_fill(early))

    async def _place_stop(self, close_sdk_side, size, price, account_id) -> "str | None":
        try:
            resp = await self._suite.orders.place_stop_order(
                self._suite.instrument_id, close_sdk_side, size, float(price), account_id)
            if getattr(resp, "success", False):
                oid = str(resp.orderId)
                log.info("Stop placed: order=%s @ %s size=%d", oid, price, size)
                return oid
            log.error("Stop order rejected: price=%s resp=%s", price, resp)
        except Exception:
            log.exception("_place_stop failed")
        return None

    async def _place_limit(self, close_sdk_side, size, price, account_id) -> "str | None":
        try:
            resp = await self._suite.orders.place_limit_order(
                self._suite.instrument_id, close_sdk_side, size, float(price), account_id)
            if getattr(resp, "success", False):
                oid = str(resp.orderId)
                log.info("Limit (exit) placed: order=%s @ %s size=%d", oid, price, size)
                return oid
            log.error("Limit (exit) rejected: price=%s resp=%s", price, resp)
        except Exception:
            log.exception("_place_limit failed")
        return None

    async def _handle_group_fill(self, fill: Fill) -> None:
        """Drive the partial/BE state machine when an exit-group leg fills.

        Emits a corrected EXIT Fill (is_entry=False, real P&L) to all handlers so
        the engine/journal/CSV record it, then performs the OCO/modify actions.
        """
        order_id = fill.broker_order_id
        group = self._exit_groups.get(order_id)
        if group is None:
            log.warning("_handle_group_fill: %s not in any group — ignoring", order_id)
            return

        pv = _point_value(fill.instrument)
        entry_price = group["entry_price"]
        entry_side = group["entry_side"]

        def _pnl(exit_price: Decimal, qty: int) -> Decimal:
            if entry_side == "long":
                return (exit_price - entry_price) * qty * pv
            return (entry_price - exit_price) * qty * pv

        is_partial = (order_id == group["partial_id"])
        is_stop = (order_id == group["stop_id"])
        is_target = (order_id == group["target_id"])

        if is_partial:
            # Scale-out filled → move stop to BE + resize to remaining.
            qty = group["partial_size"]
            await self._emit_group_exit(fill, _pnl(fill.fill_price, qty), qty, is_stop=False)
            group["partial_filled"] = True
            del self._exit_groups[order_id]   # partial leg is one-shot
            group["partial_id"] = None
            await self._modify_stop_to_be(group)
            return

        if is_stop:
            qty = group["remaining_size"] if group["partial_filled"] else (
                group["partial_size"] + group["remaining_size"])
            await self._emit_group_exit(fill, _pnl(fill.fill_price, qty), qty, is_stop=True)
            await self._cancel_group_siblings(group, filled_id=order_id)
            self._clear_group(group)
            return

        if is_target:
            qty = group["remaining_size"]
            await self._emit_group_exit(fill, _pnl(fill.fill_price, qty), qty, is_stop=False)
            await self._cancel_group_siblings(group, filled_id=order_id)
            self._clear_group(group)
            return

    async def _emit_group_exit(self, fill: Fill, pnl: Decimal, qty: int, is_stop: bool) -> None:
        """Fan out a corrected EXIT fill for a group leg."""
        corrected = Fill(
            ts=fill.ts, instrument=fill.instrument, side=fill.side,
            fill_price=fill.fill_price, size=qty, is_entry=False,
            realized_pnl_delta=pnl,
            contracts_delta=(-qty if fill.side == "short" else qty),
            broker_order_id=fill.broker_order_id, is_stop=is_stop,
        )
        log.info("Group exit: order=%s pnl=%s qty=%d is_stop=%s",
                 fill.broker_order_id, pnl, qty, is_stop)
        await self._fanout(self._fill_handlers, corrected)
        await self._emit_equity_snapshot(corrected.ts)

    async def _cancel_group_siblings(self, group: dict, filled_id: str) -> None:
        """Cancel every still-live leg in the group except the one that filled."""
        for key in ("stop_id", "partial_id", "target_id"):
            oid = group.get(key)
            if oid is not None and oid != filled_id and oid in self._exit_groups:
                asyncio.create_task(self._cancel_order(oid))

    def _clear_group(self, group: dict) -> None:
        for key in ("stop_id", "partial_id", "target_id"):
            oid = group.get(key)
            if oid is not None:
                self._exit_groups.pop(oid, None)
        self._be_watches.pop(group["instrument"], None)

    async def _modify_stop_to_be(self, group: dict) -> None:
        """Move the stop to break-even and resize to remaining. The invariant
        (stop size == position size) must hold or we flatten. Ladder:
        modify -> retry once -> cancel+replace -> flatten the remainder.

        Note: the SDK's modify_order raises ProjectXOrderError on failure (it only
        returns True/no-op-True), so the try/except handles production failures;
        the explicit `if ok` handles the test-stub falsy-return path. Both fall
        through to cancel+replace."""
        stop_id = group["stop_id"]
        be = float(group["be_price"])
        remaining = group["remaining_size"]

        for attempt in (1, 2):
            try:
                ok = await self._suite.orders.modify_order(
                    order_id=stop_id, stop_price=be, size=remaining)
                if ok:
                    log.info("Stop moved to BE: order=%s be=%s size=%d", stop_id, be, remaining)
                    return
                log.error("modify_order returned falsy (attempt %d) for stop=%s", attempt, stop_id)
            except Exception:
                log.exception("modify_order raised (attempt %d) for stop=%s", attempt, stop_id)

        # Cancel + replace: the old stop is oversized for the now-smaller position.
        log.error("BE modify failed twice — cancel+replace stop=%s", stop_id)
        try:
            await self._cancel_order(stop_id)
        except Exception:
            log.exception("cancel of oversized stop failed: %s", stop_id)

        new_id = await self._place_stop(
            group["close_sdk_side"], remaining, group["be_price"], group["account_id"])
        if new_id is not None:
            # Re-register: drop old stop id, add the new one to the group.
            self._exit_groups.pop(stop_id, None)
            group["stop_id"] = new_id
            self._exit_groups[new_id] = group
            log.info("Replacement BE stop placed: order=%s size=%d", new_id, remaining)
            return

        # Last resort: flatten the remaining position so it is never naked/oversized.
        log.error("Replacement stop failed — flattening remainder of %s", group["instrument"])
        try:
            await self.flatten(group["instrument"])
        except Exception:
            log.exception("emergency flatten of remainder failed for %s", group["instrument"])
        self._clear_group(group)

    async def _maybe_move_stop_to_be(self, instrument: str, price: Decimal) -> None:
        """1-lot BE move: when price crosses the trigger, modify the stop to BE.
        Called from the quote handler. Cheap no-op when no armed watch exists."""
        watch = self._be_watches.get(instrument)
        if watch is None or not watch["armed"]:
            return
        crossed = (
            (watch["side"] == "long" and price >= watch["trigger_price"])
            or (watch["side"] == "short" and price <= watch["trigger_price"])
        )
        if not crossed:
            return
        watch["armed"] = False
        try:
            ok = await self._suite.orders.modify_order(
                order_id=watch["stop_id"], stop_price=float(watch["be_price"]))
            if ok:
                log.info("BE move (1-lot): stop=%s -> %s", watch["stop_id"], watch["be_price"])
            else:
                log.error("BE move (1-lot) modify returned falsy for stop=%s — original stop still active", watch["stop_id"])
        except Exception:
            log.exception("BE move (1-lot) failed for %s — original stop still active", instrument)

    async def _reprocess_early_exit(self, fill: Fill) -> None:
        """
        Re-process a stop/target fill that arrived before its OCO pair was
        registered. Computes realized P&L from the entry context and fans
        out a corrected EXIT fill to all handlers.
        """
        order_id = fill.broker_order_id
        pair_info = self._exit_pairs.pop(order_id, None)
        if pair_info is None:
            # Already consumed (e.g. duplicate event). The initial fanout already
            # updated risk state, so don't re-fan — that would double-count P&L.
            log.warning("_reprocess_early_exit: %s not in _exit_pairs — already processed", order_id)
            return

        paired_id = pair_info["paired_id"]
        self._exit_pairs.pop(paired_id, None)
        asyncio.create_task(self._cancel_order(paired_id))

        pv = _point_value(fill.instrument)
        if pair_info["entry_side"] == "long":
            pnl = (fill.fill_price - pair_info["entry_price"]) * pair_info["size"] * pv
        else:
            pnl = (pair_info["entry_price"] - fill.fill_price) * pair_info["size"] * pv

        corrected = Fill(
            ts=fill.ts,
            instrument=fill.instrument,
            side=fill.side,
            fill_price=fill.fill_price,
            size=fill.size,
            is_entry=False,
            realized_pnl_delta=pnl,
            # contracts_delta=0: the initial fanout (when we buffered the early fill)
            # already updated risk_state.open_contracts via _handle_fill. Setting 0
            # here prevents the reconciler and risk state from double-counting.
            contracts_delta=0,
            broker_order_id=order_id,
        )
        log.info(
            "Early exit P&L corrected: order=%s pnl=%s (entry=%s exit=%s %s x%d)",
            order_id, pnl, pair_info["entry_price"], fill.fill_price,
            pair_info["entry_side"], pair_info["size"],
        )
        await self._fanout(self._fill_handlers, corrected)
        await self._emit_equity_snapshot(corrected.ts)

    async def _cancel_order(self, order_id: str) -> None:
        """Cancel a single order by ID. Used for OCO cancellation."""
        try:
            resp = await self._suite.orders.cancel_order(int(order_id))
            if getattr(resp, "success", False):
                log.info("OCO cancel confirmed: order=%s", order_id)
            else:
                log.error("OCO cancel rejected: order=%s resp=%s", order_id, resp)
        except Exception:
            log.exception("_cancel_order: failed to cancel order=%s", order_id)

    async def get_data_availability(self, timeframe: str = "5min") -> dict:
        """
        Probe the broker for the earliest/latest bar timestamps available.
        Cached for ~1 hour per (timeframe) since the answer barely moves.
        """
        self._require_connected()
        cache_key = f"avail_{timeframe}"
        cache_max_age = 3600
        now = datetime.now(timezone.utc).timestamp()
        cached = getattr(self, "_avail_cache", {}).get(cache_key)
        if cached and now - cached["fetched_at"] < cache_max_age:
            return cached["payload"]

        interval, unit = self._parse_timeframe(timeframe)
        primary = self._instruments[0] if self._instruments else ""
        # Ask for a huge window — we only need the bookend timestamps.
        from datetime import timedelta
        far_past = datetime.now(timezone.utc) - timedelta(days=400)
        try:
            df = await self._suite.client.get_bars(
                symbol=primary, interval=interval, unit=unit,
                limit=500_000,
                start_time=far_past,
                end_time=datetime.now(timezone.utc),
            )
        except Exception:
            log.exception("availability probe failed")
            return {"earliest": None, "latest": None, "bars": 0}

        if len(df) == 0:
            payload = {"earliest": None, "latest": None, "bars": 0}
        else:
            first = df["timestamp"][0]
            last = df["timestamp"][-1]
            payload = {
                "earliest": str(first),
                "latest": str(last),
                "bars": len(df),
            }

        if not hasattr(self, "_avail_cache"):
            self._avail_cache: dict = {}
        self._avail_cache[cache_key] = {
            "fetched_at": now,
            "payload": payload,
        }
        return payload

    @staticmethod
    def _parse_timeframe(tf: str) -> tuple[int, int]:
        """Parse 'Nmin'/'Nh'/'Nd' → (interval, unit). Unit: 2=min, 3=hour, 4=day."""
        tf = tf.strip().lower()
        if tf.endswith("h"):
            return int(tf[:-1]), 3
        if tf.endswith("min"):
            return int(tf[:-3]), 2
        if tf.endswith("d"):
            return int(tf[:-1]), 4
        return 1, 2

    async def get_historical_bars(
        self,
        timeframe: str = "1min",
        limit: int = 500,
        days: int = 5,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[Bar]:
        """
        Fetch recent historical bars for the subscribed instrument.

        Either pass `days` (look back N days from now), or pass an explicit
        `start_time`/`end_time` window. Used both for chart pre-population
        and for backtest data harvesting.
        """
        self._require_connected()
        interval, unit = self._parse_timeframe(timeframe)
        primary = self._instruments[0] if self._instruments else ""
        try:
            kwargs: dict = {
                "symbol": primary,
                "interval": interval,
                "unit": unit,
                "limit": limit,
            }
            if start_time is not None and end_time is not None:
                kwargs["start_time"] = start_time
                kwargs["end_time"] = end_time
            else:
                kwargs["days"] = days
            df = await self._suite.client.get_bars(**kwargs)
        except Exception:
            log.exception("get_historical_bars failed")
            return []

        bars: list[Bar] = []
        for row in df.iter_rows(named=True):
            ts = row.get("t") or row.get("timestamp")
            o = row.get("o") if "o" in row else row.get("open")
            h = row.get("h") if "h" in row else row.get("high")
            lo = row.get("l") if "l" in row else row.get("low")
            c = row.get("c") if "c" in row else row.get("close")
            v = row.get("v") if "v" in row else row.get("volume") or 0
            if None in (ts, o, h, lo, c):
                continue
            bars.append(Bar(
                instrument=primary,
                timeframe=timeframe,
                ts=self._coerce_ts(ts),
                open=Decimal(str(o)),
                high=Decimal(str(h)),
                low=Decimal(str(lo)),
                close=Decimal(str(c)),
                volume=int(v or 0),
            ))
        return bars

    async def get_forming_bar(self, timeframe: str = "1min") -> Bar | None:
        """
        Return the current forming bar, built by aggregating live quote mid-prices
        tick-by-tick within the current minute. Returns None until the first quote
        arrives after subscribe() completes.
        """
        return self._forming_bar

    async def place_market_order(self, side: Side, size: int = 1) -> bool:
        """Place a market order. Used for test trades and emergency entries."""
        self._require_connected()
        try:
            response = await self._suite.orders.place_market_order(
                contract_id=self._suite.instrument_id,
                side=_to_sdk_side(side),
                size=size,
            )
            return bool(getattr(response, "success", False))
        except Exception as e:
            log.exception("place_market_order failed: %s", e)
            return False

    async def flatten(self, instrument: str) -> bool:
        """
        Close all open positions at market.

        Reads positions directly from the SDK (which uses contract IDs, not
        symbol names) so the instrument string is only used for logging —
        no name-matching that can silently miss positions.
        """
        self._require_connected()
        raw = await self._raw_positions()
        open_positions = [p for p in raw if int(p.get("size", 0) or 0) > 0]
        if not open_positions:
            return True

        # Snapshot entry context from OCO pairs before they go stale.
        # _exit_pairs is still in memory after cancel_all() (cancel doesn't emit fills).
        # Clear it now so stale entries don't contaminate the next position's context.
        entry_ctx: dict | None = None
        if self._exit_pairs:
            sample = next(iter(self._exit_pairs.values()))
            entry_ctx = {
                "entry_price": sample["entry_price"],
                "entry_side": sample["entry_side"],
                "size": sample["size"],
            }
            self._exit_pairs.clear()

        success = True
        for pos in open_positions:
            ptype = int(pos.get("type", 0))
            size = int(pos.get("size", 0))
            if ptype not in (1, 2) or size == 0:
                continue
            # type=1 LONG → close with SELL; type=2 SHORT → close with BUY
            close_side = SIDE_SELL if ptype == 1 else SIDE_BUY
            try:
                response = await self._suite.orders.place_market_order(
                    contract_id=self._suite.instrument_id,
                    side=close_side,
                    size=size,
                )
                if not bool(getattr(response, "success", False)):
                    log.error("flatten market order rejected for %s", instrument)
                    success = False
                    continue
                # Register the flatten order so its fill handler can compute P&L.
                flatten_id = self._safe_str(getattr(response, "orderId", None))
                if flatten_id and entry_ctx:
                    self._flatten_order_ids[flatten_id] = entry_ctx
            except Exception:
                log.exception("flatten market order failed for %s", instrument)
                success = False

        return success

    async def cancel_all(self, instrument: str | None = None) -> int:
        self._require_connected()
        try:
            # SDK signature varies; this is the common shape.
            result = await self._suite.orders.cancel_all_orders()
            return int(getattr(result, "cancelled_count", 0))
        except Exception as e:
            log.exception("cancel_all failed: %s", e)
            return 0

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------

    def on_bar(self, handler: BarHandler) -> None:
        self._bar_handlers.append(handler)

    def on_fill(self, handler: FillHandler) -> None:
        self._fill_handlers.append(handler)

    def on_equity(self, handler: EquityHandler) -> None:
        self._equity_handlers.append(handler)

    async def subscribe(
        self,
        instruments: Iterable[str],
        timeframes: Iterable[str],
    ) -> None:
        """Create the suite and wire up the SDK event handlers."""
        instr_list = list(instruments)
        if not instr_list:
            raise ValueError("Must subscribe to at least one instrument")
        if len(instr_list) > 1:
            # Multi-instrument requires one suite per symbol or the SDK's
            # multi-instrument suite (depends on version). Out of scope
            # for first ship; flag explicitly so it doesn't silently break.
            raise NotImplementedError(
                "Multi-instrument subscription not yet implemented. "
                "Use one TopstepXBroker per instrument for now."
            )

        primary = instr_list[0]
        tf_list = list(timeframes)

        if self._account_name:
            os.environ["PROJECT_X_ACCOUNT_NAME"] = self._account_name
            log.info("TopstepXBroker: selecting account %r", self._account_name)
        elif "PROJECT_X_ACCOUNT_NAME" in os.environ:
            del os.environ["PROJECT_X_ACCOUNT_NAME"]

        self._suite = await self._TradingSuite.create(
            instrument=primary,
            timeframes=tf_list,
        )
        self._instruments = instr_list

        # Wire SDK events → our handlers.
        # events.on(event_type, handler) — NOT a decorator factory.
        from project_x_py import EventType  # type: ignore

        # Track last bar sent so we never feed the strategy a duplicate.
        _last_bar_ts: datetime | None = None

        async def _on_new_bar(event):
            nonlocal _last_bar_ts
            data = event.data
            inner = data.get("data") or data
            ts_raw = (
                data.get("bar_time")
                or inner.get("timestamp")
                or data.get("t")
                or data.get("timestamp")
            )
            ts = self._coerce_ts(ts_raw)

            # NEW_BAR fires at bar OPEN. The just-completed bar opens one
            # timeframe earlier. Compute its expected timestamp and fetch
            # just that bar via REST so the chart and strategy see real
            # OHLCV instead of the doji-shaped opening tick.
            tf_secs = _tf_seconds_local(tf_list[0])
            completed_ts = ts - timedelta(seconds=tf_secs)

            try:
                # Use an explicit time window so each call has unique
                # parameters and can't be SDK-cached. Window covers the
                # last hour (60 bars on 1min) — plenty to catch up.
                window_start = ts - timedelta(minutes=60)
                recent = await self.get_historical_bars(
                    timeframe=tf_list[0],
                    start_time=window_start,
                    end_time=ts,
                )
                # Keep only completed bars (strictly before the current
                # forming bar) newer than what we've already sent.
                to_send: list[Bar] = []
                for bar in recent:
                    if bar.ts >= ts:
                        continue
                    if _last_bar_ts is not None and bar.ts <= _last_bar_ts:
                        continue
                    to_send.append(bar)
                to_send.sort(key=lambda b: b.ts)

                for bar in to_send:
                    await self._fanout(self._bar_handlers, bar)
                    _last_bar_ts = bar.ts

                if to_send:
                    log.info(
                        "_on_new_bar: sent %d bars (range %s .. %s)",
                        len(to_send),
                        to_send[0].ts.isoformat(),
                        to_send[-1].ts.isoformat(),
                    )
                else:
                    log.info(
                        "_on_new_bar: no new bars (event=%s expected_completed=%s last_sent=%s)",
                        ts.isoformat(),
                        completed_ts.isoformat(),
                        _last_bar_ts.isoformat() if _last_bar_ts else "None",
                    )
            except Exception:
                log.exception("_on_new_bar failed")

            await self._emit_equity_snapshot(ts)

        await self._suite.events.on(EventType.NEW_BAR, _on_new_bar)

        async def _on_quote_update(event):
            data = event.data
            bid = data.get("bid")
            ask = data.get("ask")
            if bid is None or ask is None:
                return
            try:
                price = Decimal(str((float(bid) + float(ask)) / 2))
            except (ValueError, TypeError):
                return
            # 1-lot break-even move: arm-and-fire when price crosses the trigger.
            await self._maybe_move_stop_to_be(primary, price)
            now = _utcnow()
            minute_start = now.replace(second=0, microsecond=0)
            if self._forming_bar_minute != minute_start:
                self._forming_bar = Bar(
                    instrument=primary,
                    timeframe=tf_list[0],
                    ts=minute_start,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=1,
                )
                self._forming_bar_minute = minute_start
            elif self._forming_bar is not None:
                fb = self._forming_bar
                self._forming_bar = Bar(
                    instrument=fb.instrument,
                    timeframe=fb.timeframe,
                    ts=fb.ts,
                    open=fb.open,
                    high=max(fb.high, price),
                    low=min(fb.low, price),
                    close=price,
                    volume=fb.volume + 1,
                )

        await self._suite.events.on(EventType.QUOTE_UPDATE, _on_quote_update)

        # Start the intrabar recorder now that _forming_bar can populate.
        self._intrabar_task = asyncio.create_task(self._intrabar_sampler_loop())
        log.info(
            "Intrabar recorder started: %ds interval -> intrabar_%s.csv",
            _INTRABAR_SAMPLE_SECONDS, primary,
        )

        # SDK exposes fill events under different names depending on version.
        for event_name in ("ORDER_FILLED", "FILL", "POSITION_CHANGED"):
            try:
                event_type = getattr(EventType, event_name)
            except AttributeError:
                continue

            async def _on_fill_event(event, _en=event_name):
                log.debug("_on_fill_event [%s]: raw data keys=%s", _en,
                          list(event.data.keys()) if isinstance(event.data, dict) else type(event.data).__name__)
                fill = self._build_fill(event.data)
                if fill is None:
                    log.warning("_on_fill_event [%s]: _build_fill returned None — open_contracts NOT updated. Raw: %s",
                                _en, event.data)
                    return
                order_id = fill.broker_order_id

                # Dedup: ORDER_FILLED, FILL, and POSITION_CHANGED can all fire for
                # the same order. Only process each order_id once — duplicates would
                # double-count contracts_delta and trigger a false reconciler drift.
                if order_id:
                    if order_id in self._processed_fill_ids:
                        log.debug(
                            "_on_fill_event [%s]: duplicate fill order=%s — skipping",
                            _en, order_id,
                        )
                        return
                    self._processed_fill_ids.add(order_id)

                # Route by dict lookup first — these are definitive.
                # is_entry is unreliable because the SDK ORDER_FILLED event
                # never carries realized P&L, so realized==0 always, making
                # is_entry True for both entry and exit fills.
                if order_id and order_id in self._pending_brackets:
                    # Definitive entry fill — bracket registered, place stop+target.
                    bracket_data = self._pending_brackets.pop(order_id)
                    bracket_data["fill_price"] = fill.fill_price
                    log.info(
                        "Entry fill confirmed order=%s @ %s — placing stop+target",
                        order_id, fill.fill_price,
                    )
                    if bracket_data.get("partial_r", Decimal("0")) > 0:
                        asyncio.create_task(self._place_partial_bracket_after_fill(bracket_data))
                    else:
                        asyncio.create_task(self._place_bracket_after_fill(bracket_data))

                elif order_id and order_id in self._exit_pairs:
                    # Definitive exit fill — stop or target hit, cancel the other.
                    pair_info = self._exit_pairs.pop(order_id)
                    paired_id = pair_info["paired_id"]
                    self._exit_pairs.pop(paired_id, None)
                    log.info(
                        "Exit order %s filled — cancelling paired order %s (OCO)",
                        order_id, paired_id,
                    )
                    asyncio.create_task(self._cancel_order(paired_id))

                    # Compute realized P&L from price delta × contract multiplier.
                    entry_price = pair_info["entry_price"]
                    entry_side  = pair_info["entry_side"]
                    ex_size     = pair_info["size"]
                    pv = _point_value(fill.instrument)
                    if entry_side == "long":
                        pnl = (fill.fill_price - entry_price) * ex_size * pv
                    else:
                        pnl = (entry_price - fill.fill_price) * ex_size * pv
                    fill = Fill(
                        ts=fill.ts,
                        instrument=fill.instrument,
                        side=fill.side,
                        fill_price=fill.fill_price,
                        size=fill.size,
                        is_entry=False,
                        realized_pnl_delta=pnl,
                        contracts_delta=fill.contracts_delta,
                        broker_order_id=fill.broker_order_id,
                    )
                    log.info("Exit P&L: order=%s pnl=%s (entry=%s exit=%s %s x%d)",
                             order_id, pnl, entry_price, fill.fill_price, entry_side, ex_size)

                elif order_id and order_id in self._exit_groups:
                    # Partials path: a stop / partial-target / final-target leg filled.
                    await self._handle_group_fill(fill)
                    return

                elif order_id and order_id in self._flatten_order_ids:
                    # Manual flatten fill (reversal or lockout) — compute P&L from
                    # the entry context captured at flatten() time.
                    ctx = self._flatten_order_ids.pop(order_id)
                    pv = _point_value(fill.instrument)
                    if ctx["entry_side"] == "long":
                        pnl = (fill.fill_price - ctx["entry_price"]) * ctx["size"] * pv
                    else:
                        pnl = (ctx["entry_price"] - fill.fill_price) * ctx["size"] * pv
                    fill = Fill(
                        ts=fill.ts,
                        instrument=fill.instrument,
                        side=fill.side,
                        fill_price=fill.fill_price,
                        size=fill.size,
                        is_entry=False,
                        realized_pnl_delta=pnl,
                        contracts_delta=fill.contracts_delta,
                        broker_order_id=fill.broker_order_id,
                    )
                    log.info(
                        "Flatten P&L: order=%s pnl=%s (entry=%s exit=%s %s x%d)",
                        order_id, pnl, ctx["entry_price"], fill.fill_price,
                        ctx["entry_side"], ctx["size"],
                    )

                elif order_id and fill.is_entry:
                    # Not in any known dict yet. Two cases:
                    #   (a) Market entry fill before place_market_bracket registered
                    #       the order_id — replayed by place_market_bracket.
                    #   (b) Stop/target fill before _place_bracket_after_fill registered
                    #       the OCO pair — replayed by _reprocess_early_exit.
                    # Fan out NOW so the engine's _handle_fill updates risk state
                    # immediately (contracts_delta is correct regardless of is_entry).
                    # Skipping fanout here leaves open_contracts stale and causes the
                    # reconciler to fire false drift alerts and lockout emails.
                    # _reprocess_early_exit will re-fan with correct EXIT classification
                    # and P&L, using contracts_delta=0 to avoid double-counting.
                    self._early_fills[order_id] = fill
                    log.info(
                        "Early fill buffered order=%s @ %s (risk state updated, routing pending)",
                        order_id, fill.fill_price,
                    )
                    # Mark provisional so the journal/UI skip this row. The
                    # corrected fanout will follow (from entry-replay or
                    # _reprocess_early_exit) and is the one that gets logged.
                    fill = Fill(
                        ts=fill.ts,
                        instrument=fill.instrument,
                        side=fill.side,
                        fill_price=fill.fill_price,
                        size=fill.size,
                        is_entry=fill.is_entry,
                        realized_pnl_delta=fill.realized_pnl_delta,
                        contracts_delta=fill.contracts_delta,
                        broker_order_id=fill.broker_order_id,
                        is_stop=fill.is_stop,
                        is_provisional=True,
                    )

                await self._fanout(self._fill_handlers, fill)
                await self._emit_equity_snapshot(fill.ts)

            await self._suite.events.on(event_type, _on_fill_event)

        log.info("Subscribed: instruments=%s timeframes=%s", instr_list, tf_list)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if self._suite is None:
            raise RuntimeError(
                "Broker not subscribed. Call subscribe() before use."
            )

    @staticmethod
    def _safe_str(v) -> str | None:
        return str(v) if v is not None else None

    @staticmethod
    def _coerce_ts(raw) -> datetime:
        if isinstance(raw, datetime):
            return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        if raw is None:
            return _utcnow()
        # SDK sometimes passes ISO strings.
        try:
            ts = datetime.fromisoformat(str(raw))
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        except ValueError:
            return _utcnow()

    @staticmethod
    async def _fanout(handlers, event) -> None:
        """
        Run all handlers concurrently; one failing handler must not
        block the others. Strategy and risk should not depend on each
        other's success.
        """
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in handlers),
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                log.exception("Handler raised: %s", r)

    def _build_fill(self, data: dict) -> Fill | None:
        """
        Translate an SDK fill payload into our Fill event.

        The SDK ORDER_FILLED event carries a nested structure:
            {"order": Order(...), "order_id": int, "old_status": int, "new_status": int}
        All trade fields (contractId, side, size, filledPrice) live on the Order
        dataclass, not at the top level. The flat-dict path is kept for any
        legacy event shapes that may arrive.
        """
        try:
            order_obj = data.get("order") if isinstance(data, dict) else None
            if order_obj is not None and hasattr(order_obj, "contractId"):
                # SDK ORDER_FILLED: extract all fields from the nested Order object.
                order_id = str(data.get("order_id") or getattr(order_obj, "id", None) or "")
                instrument = str(getattr(order_obj, "contractId", None) or "")
                sdk_side = int(getattr(order_obj, "side", -1))
                size = int(getattr(order_obj, "size", 0) or 0)
                # filledPrice is set by the exchange when the order is filled.
                # For market orders limitPrice is None; filledPrice is the actual fill.
                fill_price = Decimal(str(
                    getattr(order_obj, "filledPrice", None)
                    or getattr(order_obj, "limitPrice", None)
                    or 0
                ))
                ts = self._coerce_ts(getattr(order_obj, "updateTimestamp", None))
                realized = Decimal("0")
            else:
                # Flat payload (legacy / other event types).
                order_id = str(data.get("order_id") or data.get("orderId") or "")
                instrument = str(data.get("contract_id") or data.get("symbol") or "")
                sdk_side = int(data.get("side", -1))
                size = int(data.get("size", 0))
                fill_price = Decimal(str(data.get("fill_price") or data.get("price") or 0))
                ts = self._coerce_ts(data.get("timestamp"))
                realized = Decimal(str(data.get("realized_pnl_delta", 0)))

            contracts_delta_raw = data.get("contracts_delta") if isinstance(data, dict) else None
            if contracts_delta_raw is None:
                # +size for buy (entry long / exit short), -size for sell.
                contracts_delta_raw = size if sdk_side == SIDE_BUY else -size
            contracts_delta = int(contracts_delta_raw)
            is_entry = bool(data.get("is_entry", realized == 0) if isinstance(data, dict) else realized == 0)
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            log.warning("Unparseable fill event: %s (%s)", data, e)
            return None

        if size == 0 or sdk_side < 0 or not instrument:
            log.debug(
                "_build_fill: dropping fill — size=%s sdk_side=%s instrument=%r",
                size, sdk_side, instrument,
            )
            return None

        return Fill(
            ts=ts,
            instrument=instrument,
            side=_to_internal_side(sdk_side),
            fill_price=fill_price,
            size=size,
            is_entry=is_entry,
            realized_pnl_delta=realized,
            contracts_delta=contracts_delta,
            broker_order_id=order_id,
        )

    async def _emit_equity_snapshot(self, ts: datetime) -> None:
        """Compute equity = realized balance + sum(unrealized P&L) and emit."""
        try:
            realized = await self.account_balance()
            positions = await self.get_positions()
            unrealized = sum((p.unrealized_pnl for p in positions), Decimal("0"))
            equity = realized + unrealized
        except Exception as e:
            log.warning("Could not compute equity snapshot: %s", e)
            return
        mtm = MarkToMarket(ts=ts, equity=equity)
        await self._fanout(self._equity_handlers, mtm)
