"""
Email notifier — SMTP sender for trade alerts and end-of-day summaries.

Config via env vars (all must be set or the notifier silently no-ops):

    TOPSTEP_BOT_SMTP_HOST     e.g. smtp.gmail.com
    TOPSTEP_BOT_SMTP_PORT     587 (STARTTLS) or 465 (SSL)
    TOPSTEP_BOT_SMTP_USER     login username
    TOPSTEP_BOT_SMTP_PASS     password or app password
    TOPSTEP_BOT_NOTIFY_EMAIL  recipient address (where alerts go)

Gmail note: regular passwords don't work; create an App Password at
https://myaccount.google.com/apppasswords and use that as SMTP_PASS.

The notifier runs SMTP I/O in a thread so it never blocks the event loop.
A send failure logs a warning but never raises — losing an email must
never crash the trading path.
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


class EmailNotifier:
    """SMTP-based email sender. No-ops cleanly if credentials are missing."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        user: str | None = None,
        password: str | None = None,
        recipient: str | None = None,
    ) -> None:
        self.host = host or os.environ.get("TOPSTEP_BOT_SMTP_HOST") or ""
        self.port = port or int(os.environ.get("TOPSTEP_BOT_SMTP_PORT", "587"))
        self.user = user or os.environ.get("TOPSTEP_BOT_SMTP_USER") or ""
        self.password = password or os.environ.get("TOPSTEP_BOT_SMTP_PASS") or ""
        self.recipient = recipient or os.environ.get("TOPSTEP_BOT_NOTIFY_EMAIL") or ""

    @property
    def enabled(self) -> bool:
        return all([self.host, self.user, self.password, self.recipient])

    async def send(
        self,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> bool:
        """Send an email. Returns True on success, False otherwise.

        attachments: list of (filename, raw_bytes) pairs.
        """
        if not self.enabled:
            return False
        try:
            await asyncio.to_thread(self._send_sync, subject, body, attachments)
            log.info("Email sent: %s", subject)
            return True
        except Exception as e:
            log.warning("Email send failed (%s): %s", subject, e)
            return False

    def _send_sync(
        self,
        subject: str,
        body: str,
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> None:
        msg = EmailMessage()
        msg["From"] = self.user
        msg["To"] = self.recipient
        msg["Subject"] = f"[topstep-bot] {subject}"
        msg.set_content(body)

        for filename, data in (attachments or []):
            msg.add_attachment(
                data,
                maintype="text",
                subtype="csv",
                filename=filename,
            )

        if self.port == 465:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=10) as s:
                s.login(self.user, self.password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(self.host, self.port, timeout=10) as s:
                s.starttls()
                s.login(self.user, self.password)
                s.send_message(msg)
