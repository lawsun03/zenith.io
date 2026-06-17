"""
Discord notifier — webhook poster for signals and fills.

Config via env var (single source — set or unset):

    TOPSTEP_BOT_DISCORD_WEBHOOK_URL  full Discord webhook URL

If unset (or empty), the notifier silently no-ops. A send failure logs a
warning but never raises — losing a webhook post must never crash the
trading path. Webhook posts run via httpx.AsyncClient so they don't block
the event loop.

Discord rate-limits webhooks at ~30 requests/min. We don't enforce
locally; the bot's natural cadence (a few signals + fills per hour) is
well under that. A 429 from Discord is logged and dropped.
"""

from __future__ import annotations

import logging
import os
from decimal import Decimal
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from app.broker.events import Fill
    from app.execution.engine import OrderOutcome
    from app.strategy.composer import Signal

log = logging.getLogger(__name__)

_COLOR_LONG = 0x00FF41   # matrix green
_COLOR_SHORT = 0xFF3333  # red
_COLOR_WIN = 0x00FF41
_COLOR_LOSS = 0xFF3333
_COLOR_FLAT = 0x808080   # zero P&L

# Discord limits any single embed field's `value` to 1024 chars. The
# trade list is rendered as a fenced code block so the monospaced columns
# line up; budget ~990 chars for content + ~30 for the fence markers.
_TRADES_BLOCK_BUDGET = 990


def _format_trades_block(trades: list[dict]) -> str:
    """
    Render the round-trip trade list as a fenced code block.
    If the rendered text would exceed Discord's per-field limit, truncate
    and append a "... N more" line so the reader knows trades were elided.
    """
    header = f"{'TIME':<6} {'SIDE':<5} {'ENTRY':>9} {'EXIT':>9} {'SIZE':>4} {'P&L':>10}"
    sep = "-" * len(header)
    body_lines: list[str] = []
    for t in trades:
        pnl_str = t["pnl"]
        try:
            pnl_dec = Decimal(pnl_str)
            sign = "+" if pnl_dec > 0 else ""
            pnl_str = f"{sign}${pnl_dec}"
        except Exception:
            pass
        body_lines.append(
            f"{t['ts']:<6} {t['side'].upper():<5} "
            f"{t['entry']:>9} {t['exit']:>9} {t['size']:>4} {pnl_str:>10}"
        )

    # Trim from the head (oldest) so the most recent trades stay visible.
    elided = 0
    while body_lines:
        rendered = "\n".join([header, sep, *body_lines])
        suffix = f"\n... {elided} earlier" if elided else ""
        if len(rendered) + len(suffix) <= _TRADES_BLOCK_BUDGET:
            return f"```\n{rendered}{suffix}\n```"
        body_lines.pop(0)
        elided += 1
    return "```\n(no trades fit in field)\n```"


class DiscordNotifier:
    """Post signal/fill events to a Discord channel via webhook."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self.webhook_url = (
            webhook_url
            if webhook_url is not None
            else os.environ.get("TOPSTEP_BOT_DISCORD_WEBHOOK_URL", "")
        ).strip()

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    async def send_startup(
        self,
        mode: str,
        instrument: str,
        balance: str,
        daily_pnl: str,
        killzones: str,
        dashboard_url: str,
        started_at: str,
        account: str | None = None,
    ) -> None:
        """Post a 'bot connected' embed at startup."""
        if not self.enabled:
            return
        fields = [
            {"name": "Mode",       "value": mode,         "inline": True},
            {"name": "Instrument", "value": instrument,   "inline": True},
            {"name": "Balance",    "value": f"${balance}", "inline": True},
            {"name": "Daily P&L",  "value": f"${daily_pnl}", "inline": True},
            {"name": "Killzones",  "value": killzones or "all", "inline": False},
            {"name": "Dashboard",  "value": dashboard_url, "inline": False},
        ]
        if account:
            fields.insert(2, {"name": "Account", "value": account, "inline": True})
        embed = {
            "title": f"topstep-bot connected — {started_at}",
            "color": _COLOR_LONG,
            "fields": fields,
        }
        await self._post({"embeds": [embed]})

    async def send_signal(self, signal: "Signal", outcome: "OrderOutcome") -> None:
        """Post a placed signal. Denied signals are skipped to keep the channel quiet."""
        if not self.enabled or not outcome.placed:
            return
        side = signal.side.upper()
        color = _COLOR_LONG if signal.side == "long" else _COLOR_SHORT
        g = signal.setup_grade
        if g is None:
            grade_val = "—"
        else:
            # Bold the letter; render the reason's criteria (the comma list after
            # the em-dash) as a bulleted list instead of one long line.
            grade_val = f"**{g.grade}**"
            crit = g.reason.split("—", 1)[1].strip() if g.reason and "—" in g.reason else ""
            if crit:
                bullets = "\n".join(f"• {c.strip()}" for c in crit.split(",") if c.strip())
                grade_val = f"**{g.grade}**\n{bullets}"
        embed = {
            "title": f"SIGNAL {side} {signal.instrument} x{outcome.allowed_size} @ {signal.entry}",
            "color": color,
            "fields": [
                {"name": "Grade",    "value": grade_val, "inline": False},
                {"name": "Entry",    "value": str(signal.entry),  "inline": True},
                {"name": "Stop",     "value": str(signal.stop),   "inline": True},
                {"name": "Target",   "value": str(signal.target), "inline": True},
                {"name": "Killzone", "value": str(signal.killzone or "—"), "inline": True},
                {"name": "Order ID", "value": str(outcome.broker_order_id or "—"), "inline": True},
                {"name": "Setup",    "value": str(signal.rationale or "—"), "inline": False},
            ],
            "timestamp": signal.created_at.isoformat(),
        }
        await self._post({"embeds": [embed]})

    async def send_alert(self, title: str, message: str) -> bool:
        """Generic operational alert (e.g. feed dead/recovered). No-op + False
        when disabled; returns True on a successful webhook post."""
        if not self.enabled:
            return False
        embed = {"title": title, "description": message, "color": _COLOR_SHORT}
        await self._post({"embeds": [embed]})
        return True

    async def send_fill(self, fill: "Fill") -> None:
        """
        Post exit fills only. Entry fills are already covered by send_signal
        (which fires when the order is placed), so posting a second message
        when the entry confirms is redundant noise.
        Provisional fills are skipped — the corrected fanout follows.
        """
        if not self.enabled or getattr(fill, "is_provisional", False) or fill.is_entry:
            return
        pnl = Decimal(fill.realized_pnl_delta)
        sign = "+" if pnl > 0 else ("-" if pnl < 0 else "")
        color = _COLOR_WIN if pnl > 0 else (_COLOR_LOSS if pnl < 0 else _COLOR_FLAT)
        embed = {
            "title": f"EXIT {fill.instrument} {sign}${abs(pnl)}",
            "color": color,
            "fields": [
                {"name": "Price",    "value": str(fill.fill_price), "inline": True},
                {"name": "Size",     "value": str(fill.size),       "inline": True},
                {"name": "P&L",      "value": f"{sign}${abs(pnl)}", "inline": True},
                {"name": "Order ID", "value": str(fill.broker_order_id or "—"), "inline": True},
            ],
            "timestamp": fill.ts.isoformat(),
        }
        await self._post({"embeds": [embed]})

    async def send_eod_summary(
        self,
        stats: dict,
        trades: list[dict],
        equity: str,
        high_water: str,
        locked_out=None,
    ) -> None:
        """
        Post the end-of-day summary. One embed with the stats fields, plus
        a per-trade list (capped to stay within Discord's 1024-char field
        limit). If the list overflows, the tail is replaced by an
        "... N more" line.
        """
        if not self.enabled:
            return

        net_pnl = Decimal(str(stats.get("net_pnl", "0")))
        color = _COLOR_WIN if net_pnl > 0 else (_COLOR_LOSS if net_pnl < 0 else _COLOR_FLAT)

        fields = [
            {"name": "Net P&L",       "value": f"${stats['net_pnl']}", "inline": True},
            {"name": "Equity",        "value": f"${equity}",          "inline": True},
            {"name": "High-water",    "value": f"${high_water}",      "inline": True},
            {"name": "Trades",        "value": str(stats["trades"]),  "inline": True},
            {"name": "Wins / Losses", "value": f"{stats['wins']} / {stats['losses']}", "inline": True},
            {"name": "Win rate",      "value": f"{stats['win_rate']}%", "inline": True},
            {"name": "Avg win",       "value": f"${stats['avg_win']}",  "inline": True},
            {"name": "Avg loss",      "value": f"${stats['avg_loss']}", "inline": True},
            {"name": "Profit factor", "value": stats["profit_factor"],  "inline": True},
            {"name": "Signals",
             "value": (f"{stats['signals_placed']} placed / "
                       f"{stats['signals_denied']} denied / "
                       f"{stats.get('sweeps_armed', '—')} sweeps armed"),
             "inline": False},
        ]

        if trades:
            fields.append({
                "name": f"Trades ({len(trades)})",
                "value": _format_trades_block(trades),
                "inline": False,
            })
        else:
            fields.append({"name": "Trades", "value": "_none_", "inline": False})

        if locked_out is not None:
            fields.append({
                "name": "LOCKOUT",
                "value": f"{getattr(locked_out, 'code', '?')} — {getattr(locked_out, 'message', '')}",
                "inline": False,
            })

        embed = {
            "title": f"EOD summary — {stats['date']}  (net ${stats['net_pnl']})",
            "color": color,
            "fields": fields,
        }
        await self._post({"embeds": [embed]})

    async def _post(self, payload: dict) -> None:
        """POST to the webhook. Swallow errors — never crash trading."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(self.webhook_url, json=payload)
            if resp.status_code >= 400:
                log.warning(
                    "Discord webhook returned %s: %s",
                    resp.status_code, resp.text[:200],
                )
        except Exception as e:
            log.warning("Discord webhook post failed: %s", e)
