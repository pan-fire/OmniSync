"""Email notification channel: send each notification as a mail through SMTP.

Uses the standard library's smtplib in a worker thread (no extra
dependency). Security: STARTTLS (default, port 587), implicit TLS (port
465) or none; TLS certificates are verified against the host name. The
SMTP host is resolved and checked like a webhook URL (no link-local or
metadata address), and a login without encryption is only sent to a
loopback or private address.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
import socket
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from backend.services.notification_channels.base import NotificationChannelBase
from backend.services.notification_channels.net import resolve_checked
from backend.services.notification_channels.settings import EmailSettings
from backend.services.notification_events import NotificationEvent
from backend.version import get_version

logger = logging.getLogger(__name__)

# Seconds each SMTP network operation may take.
SMTP_TIMEOUT = 10.0


class _Pinned:
    """Connect to the checked address while TLS still verifies the host name.

    smtplib takes the TLS server name (SMTP_SSL's wrap and starttls()) from
    ``_host``, which only its constructor sets; connect() records the name
    here first. The socket goes to ``_pinned_ip`` instead of a second DNS
    lookup.
    """
    _pinned_ip: str
    _host: str

    def connect(self, host: str = "localhost", port: int = 0,  # type: ignore[override]
                source_address: tuple[str, int] | None = None) -> tuple[int, bytes]:
        self._host = host
        return super().connect(host, port, source_address)  # type: ignore[misc]

    def _get_socket(self, host, port, timeout):  # type: ignore[no-untyped-def]
        return super()._get_socket(self._pinned_ip, port, timeout)  # type: ignore[misc]


class _PinnedSMTP(_Pinned, smtplib.SMTP):  # pyright: ignore[reportIncompatibleMethodOverride]
    pass


class _PinnedSMTPSSL(_Pinned, smtplib.SMTP_SSL):  # pyright: ignore[reportIncompatibleMethodOverride]
    pass


def _one_line(text: str) -> str:
    return " ".join(text.split())


def build_message(event: NotificationEvent, settings: EmailSettings) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = _one_line(f"[OmniSync] {event.title}")[:250]
    msg["From"] = settings.from_addr
    msg["To"] = ", ".join(settings.to)
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=settings.from_addr.rsplit("@", 1)[-1] or None)
    msg["X-OmniSync-Event"] = event.event_type.value
    msg["X-OmniSync-Severity"] = event.severity.value
    lines = [
        event.body,
        "",
        f"Severity: {event.severity.value}",
        f"Event: {event.event_type.value}",
    ]
    if event.profile_name or event.profile_slug:
        lines.append(f"Profile: {event.profile_name or event.profile_slug}")
    lines += [
        f"Time: {event.timestamp.isoformat()}",
        "",
        f"-- OmniSync {get_version()}",
    ]
    msg.set_content("\n".join(lines))
    return msg


def _send_sync(settings: EmailSettings, ip: str, msg: EmailMessage, timeout: float) -> None:
    context = ssl.create_default_context()
    local_name = socket.gethostname() or "localhost"
    smtp: smtplib.SMTP
    if settings.security == "tls":
        smtp = _PinnedSMTPSSL(local_hostname=local_name, timeout=timeout, context=context)
    else:
        smtp = _PinnedSMTP(local_hostname=local_name, timeout=timeout)
    smtp._pinned_ip = ip  # type: ignore[attr-defined]
    try:
        smtp.connect(settings.host, settings.port)
        smtp.ehlo()
        if settings.security == "starttls":
            smtp.starttls(context=context)
            smtp.ehlo()
        if settings.username:
            smtp.login(settings.username, settings.password)
        smtp.send_message(msg)
    finally:
        try:
            smtp.quit()
        except (smtplib.SMTPException, OSError):
            smtp.close()


class EmailChannel(NotificationChannelBase):
    """Delivers notifications as mail through an SMTP server."""

    def __init__(self, settings: Callable[[], EmailSettings], timeout: float = SMTP_TIMEOUT) -> None:
        self._settings = settings
        self._timeout = timeout

    @property
    def channel_name(self) -> str:
        return "email"

    def missing_dependencies(self) -> list[str]:
        settings = self._settings()
        missing = []
        if not settings.host:
            missing.append("smtp_host")
        if not settings.from_addr:
            missing.append("mail_from")
        if not settings.to:
            missing.append("mail_to")
        return missing

    async def is_available(self) -> bool:
        return not self.missing_dependencies()

    async def describe(self) -> dict[str, object]:
        missing = self.missing_dependencies()
        return {"available": not missing, "missing_dependencies": missing}

    async def send(self, event: NotificationEvent) -> None:
        settings = self._settings()
        if self.missing_dependencies():
            raise RuntimeError("SMTP host, sender or recipients not configured")
        addresses = await resolve_checked(
            settings.host, settings.port,
            # A password without encryption only goes to the local network.
            require_local=settings.security == "none" and bool(settings.username),
        )
        msg = build_message(event, settings)
        await asyncio.to_thread(_send_sync, settings, str(addresses[0]), msg, self._timeout)
        logger.debug("Mail notification sent through %s:%d to %d recipient(s)",
                     settings.host, settings.port, len(settings.to))
