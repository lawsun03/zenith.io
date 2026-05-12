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
import logging
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

from .events import Bar, BracketResult, BrokerPosition, Fill, MarkToMarket, Side
from .protocol import BarHandler, EquityHandler, FillHandler

log = logging.getLogger(__name__)


# project-x-py side encoding. Centralize so the rest of the file stays clean.
SIDE_BUY = 0
SIDE_SELL = 1


def _to_internal_side(sdk_side: int) -> Side:
    return "long" if sdk_side == SIDE_BUY else "short"


def _to_sdk_side(side: Side) -> int:
    return SIDE_BUY if side == "long" else SIDE_SELL


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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

    def __init__(self, account_name: str | None = None, entry_mode: str = "market") -> None:
        self._suite = None  # project_x_py.TradingSuite, lazily imported
        self._account_name = account_name
        self.entry_mode = entry_mode  # "market" or "limit"
        self._bar_handlers: list[BarHandler] = []
        self._fill_handlers: list[FillHandler] = []
        self._equity_handlers: list[EquityHandler] = []
        self._instruments: list[str] = []
        self._known_order_ids: set[str] = set()  # for fill dedup
        # Pending limit entries awaiting fill → then place stop + target
        self._pending_brackets: dict[str, dict] = {}

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
        if self._suite is not None:
            try:
                await self._suite.disconnect()
            except Exception as e:
                log.warning("Error during disconnect: %s", e)
            self._suite = None

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
        """Call the positions API directly, tolerating extra fields the SDK rejects."""
        try:
            account_info = self._suite.client.account_info
            account_id = account_info.id if account_info else None
            payload = {"accountId": account_id} if account_id else {}
            resp = await self._suite.client._make_request(
                "POST", "/Position/searchOpen", data=payload
            )
            if resp is None:
                return []
            positions: list[dict] = []
            if isinstance(resp, list):
                positions = resp
            elif isinstance(resp, dict):
                if not resp.get("success", False):
                    return []
                positions = resp.get("positions") or []
            return positions
        except Exception as e:
            log.warning("_raw_positions failed: %s", e)
            return []

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
        return await self.place_market_bracket(instrument, side, size, stop, target)

    async def place_market_bracket(
        self,
        instrument: str,
        side: Side,
        size: int,
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

        # Register bracket data — placed when fill event arrives for this order ID.
        self._pending_brackets[entry_order_id] = {
            "stop": stop,
            "target": target,
            "close_sdk_side": close_sdk_side,
            "size": size,
            "account_id": account_id,
        }
        self._known_order_ids.add(entry_order_id)
        log.info(
            "Market entry placed: order=%s stop=%s target=%s — watching for fill",
            entry_order_id, stop, target,
        )

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

        # Register bracket data so the fill watcher can place stop+target.
        self._pending_brackets[entry_order_id] = {
            "stop": stop,
            "target": target,
            "close_sdk_side": close_sdk_side,
            "size": size,
            "account_id": account_id,
        }
        self._known_order_ids.add(entry_order_id)
        log.info(
            "Limit entry placed: order=%s entry=%s stop=%s target=%s — watching for fill",
            entry_order_id, entry, stop, target,
        )

        return BracketResult(
            success=True,
            entry_order_id=entry_order_id,
            stop_order_id=None,   # placed after fill
            target_order_id=None,
            error=None,
        )

    async def _place_bracket_after_fill(self, bracket: dict) -> None:
        """Invoked via create_task when a pending limit entry fills."""
        stop = bracket["stop"]
        target = bracket["target"]
        close_sdk_side = bracket["close_sdk_side"]
        size = bracket["size"]
        account_id = bracket["account_id"]

        try:
            stop_resp = await self._suite.orders.place_stop_order(
                self._suite.instrument_id,
                close_sdk_side,
                size,
                float(stop),
                account_id,
            )
            if getattr(stop_resp, "success", False):
                log.info("Stop placed after fill: order=%s stop=%s", stop_resp.orderId, stop)
            else:
                log.error("Stop order rejected after fill: stop=%s", stop)
        except Exception:
            log.exception("_place_bracket_after_fill: stop order failed")

        try:
            target_resp = await self._suite.orders.place_limit_order(
                self._suite.instrument_id,
                close_sdk_side,
                size,
                float(target),
                account_id,
            )
            if getattr(target_resp, "success", False):
                log.info("Target placed after fill: order=%s target=%s", target_resp.orderId, target)
            else:
                log.error("Target order rejected after fill: target=%s", target)
        except Exception:
            log.exception("_place_bracket_after_fill: target order failed")

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

        # SDK exposes fill events under different names depending on version.
        for event_name in ("ORDER_FILLED", "FILL", "POSITION_CHANGED"):
            try:
                event_type = getattr(EventType, event_name)
            except AttributeError:
                continue

            async def _on_fill_event(event, _en=event_name):
                fill = self._build_fill(event.data)
                if fill is None:
                    return
                # If this fill is for a pending limit bracket entry, kick off
                # the stop+target placement in the background.
                order_id = fill.broker_order_id
                if order_id and order_id in self._pending_brackets and fill.is_entry:
                    bracket_data = self._pending_brackets.pop(order_id)
                    log.info(
                        "Entry fill confirmed order=%s @ %s — placing stop+target",
                        order_id, fill.fill_price,
                    )
                    asyncio.create_task(self._place_bracket_after_fill(bracket_data))
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

        Field names are version-dependent; this is the common shape
        with defensive `.get()`s. If the payload is missing core fields
        we drop the event rather than guess.
        """
        try:
            order_id = str(data.get("order_id") or data.get("orderId") or "")
            instrument = str(data.get("contract_id") or data.get("symbol") or "")
            sdk_side = int(data.get("side", -1))
            size = int(data.get("size", 0))
            fill_price = Decimal(str(data.get("fill_price") or data.get("price") or 0))
            realized = Decimal(str(data.get("realized_pnl_delta", 0)))
            contracts_delta_raw = data.get("contracts_delta")
            if contracts_delta_raw is None:
                # Reconstruct from side + size if delta isn't on the event.
                # +size for buy (long), -size for sell (short closing).
                # This is best-effort; the reconciler is the authority.
                contracts_delta_raw = size if sdk_side == SIDE_BUY else -size
            contracts_delta = int(contracts_delta_raw)
            is_entry = bool(data.get("is_entry", realized == 0))
        except (KeyError, TypeError, ValueError) as e:
            log.warning("Unparseable fill event: %s (%s)", data, e)
            return None

        if size == 0 or sdk_side < 0 or not instrument:
            return None

        return Fill(
            ts=self._coerce_ts(data.get("timestamp")),
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
