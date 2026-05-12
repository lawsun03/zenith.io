"""
End-of-day scheduler — sends an email summary at Topstep's 3:10 PM CT close.

Computes trade stats from the journal (fills only — that's where realized
P&L lives), formats them into a human-readable email, and fires the
notifier. The bot keeps running after EOD — this is just a daily report.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from app.api.journal import Journal
    from app.risk.state import RiskState
    from .email import EmailNotifier

CT = ZoneInfo("America/Chicago")
log = logging.getLogger(__name__)


class EndOfDayScheduler:
    """Background task that fires once per day at Topstep's close."""

    def __init__(
        self,
        journal: "Journal",
        risk_state: "RiskState",
        notifier: "EmailNotifier",
        close_hour_ct: int = 15,
        close_minute_ct: int = 10,
    ) -> None:
        self.journal = journal
        self.risk_state = risk_state
        self.notifier = notifier
        self.close_hour_ct = close_hour_ct
        self.close_minute_ct = close_minute_ct
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=3.0)
        except asyncio.TimeoutError:
            self._task.cancel()
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            sleep_seconds = self._seconds_until_next_close()
            log.info("EOD scheduler: next summary in %ds", sleep_seconds)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=sleep_seconds
                )
                break  # stop_event fired
            except asyncio.TimeoutError:
                pass
            if self._stop_event.is_set():
                break
            try:
                await self._send_summary()
            except Exception:
                log.exception("EOD summary failed")

    def _seconds_until_next_close(self) -> float:
        """Seconds until the next 3:10 PM CT moment."""
        now_ct = datetime.now(CT)
        close_today = now_ct.replace(
            hour=self.close_hour_ct,
            minute=self.close_minute_ct,
            second=0,
            microsecond=0,
        )
        if now_ct >= close_today:
            close_today = close_today + timedelta(days=1)
        return (close_today - now_ct).total_seconds()

    async def _send_summary(self) -> None:
        """Pull stats from the journal and send the daily email."""
        if not self.notifier.enabled:
            log.info("EOD: email notifier not configured, skipping")
            return

        fills = await self.journal.recent_fills(1000)
        signals = await self.journal.recent_signals(1000)

        # Restrict to today's trading day (last 24h is good enough for a daily).
        cutoff = datetime.now(CT) - timedelta(hours=24)
        today_fills = [f for f in fills if _parse_ts(f["ts"]) >= cutoff]
        today_signals = [s for s in signals if _parse_ts(s["ts"]) >= cutoff]

        stats = compute_stats(today_fills, today_signals)
        body = format_summary(stats, self.risk_state)

        await self.notifier.send(
            subject=f"EOD summary  {stats['date']}  net=${stats['net_pnl']}",
            body=body,
        )


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone(CT)


def compute_stats(fills: list[dict], signals: list[dict]) -> dict:
    """Compute trade stats from a list of journal fill entries."""
    exit_fills = [
        f for f in fills if not f["payload"].get("is_entry", True)
    ]
    pnl_deltas = [
        Decimal(str(f["payload"].get("realized_pnl_delta", 0)))
        for f in exit_fills
    ]
    wins = [p for p in pnl_deltas if p > 0]
    losses = [p for p in pnl_deltas if p < 0]
    net = sum(pnl_deltas, Decimal("0"))
    gross_win = sum(wins, Decimal("0"))
    gross_loss = abs(sum(losses, Decimal("0")))

    placed = [
        s for s in signals
        if s["payload"].get("outcome", {}).get("placed")
    ]
    denied = len(signals) - len(placed)

    win_rate = (len(wins) / len(exit_fills) * 100) if exit_fills else 0.0
    avg_win = (gross_win / len(wins)) if wins else Decimal("0")
    avg_loss = (gross_loss / len(losses)) if losses else Decimal("0")
    profit_factor = (
        float(gross_win / gross_loss) if gross_loss > 0 else float("inf")
    )

    return {
        "date": datetime.now(CT).strftime("%Y-%m-%d"),
        "trades": len(exit_fills),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "net_pnl": str(net.quantize(Decimal("0.01"))),
        "gross_win": str(gross_win.quantize(Decimal("0.01"))),
        "gross_loss": str(gross_loss.quantize(Decimal("0.01"))),
        "avg_win": str(avg_win.quantize(Decimal("0.01"))),
        "avg_loss": str(avg_loss.quantize(Decimal("0.01"))),
        "profit_factor": (
            "inf" if profit_factor == float("inf")
            else f"{profit_factor:.2f}"
        ),
        "signals_total": len(signals),
        "signals_placed": len(placed),
        "signals_denied": denied,
    }


def format_summary(stats: dict, risk_state: "RiskState") -> str:
    """Format stats dict as a plain-text email body."""
    lines = [
        f"End of trading day: {stats['date']}",
        f"(Topstep close: 3:10 PM CT)",
        "",
        f"NET P&L:        ${stats['net_pnl']}",
        f"Account equity: ${risk_state.current_equity}",
        f"High-water:     ${risk_state.equity_high_water}",
        "",
        "TRADES",
        f"  Total:        {stats['trades']}",
        f"  Wins:         {stats['wins']}",
        f"  Losses:       {stats['losses']}",
        f"  Win rate:     {stats['win_rate']}%",
        f"  Avg win:      ${stats['avg_win']}",
        f"  Avg loss:     ${stats['avg_loss']}",
        f"  Profit factor:{stats['profit_factor']}",
        "",
        "SIGNALS",
        f"  Generated:    {stats['signals_total']}",
        f"  Placed:       {stats['signals_placed']}",
        f"  Denied:       {stats['signals_denied']}",
        "",
        "RISK STATE",
        f"  Daily P&L:    ${risk_state.daily_pnl}",
        f"  MLL floor:    ${risk_state.mll_floor}",
        f"  Buffer to MLL:${risk_state.buffer_to_mll}",
    ]
    if risk_state.locked_out:
        lines += [
            "",
            f"LOCKOUT: {risk_state.locked_out.code} - "
            f"{risk_state.locked_out.message}",
        ]
    return "\n".join(lines)
