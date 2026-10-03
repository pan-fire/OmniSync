"""Stored settings of the configurable notification channels.

Webhook, ntfy and email need more than ``enabled`` and ``min_severity``:
an address to deliver to, and often a credential. Those settings live in
config.toml next to the other notification settings
(``[notifications.channels.<name>]``); the models here describe them and
which fields are secrets. Secrets never leave the backend: the API reports
only whether one is set (see backend.api.routes.notifications), and an
update that leaves a secret empty keeps the stored one.

The checks that need no network (scheme, plain http, literal addresses)
run when settings are saved, through check_http_url; the address a host
name resolves to is checked again when a notification is sent
(notification_channels.net).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# A header name as RFC 9110 defines a token.
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")
# Headers a webhook may not set: they describe the request itself.
RESERVED_HEADERS = frozenset({
    "host", "content-length", "content-type", "transfer-encoding", "connection", "upgrade", "te",
})
# An ntfy topic: what ntfy itself accepts.
NTFY_TOPIC = re.compile(r"^[-_A-Za-z0-9]{1,64}$")
# One mail address: local@domain, no spaces, commas, angle brackets or line breaks.
MAIL_ADDRESS = re.compile(r"^[^\s@,<>\"]{1,64}@[^\s@,<>\"]{1,255}$")
# A host name or IP literal for SMTP.
SMTP_HOST = re.compile(r"^[A-Za-z0-9.:\-\[\]]{1,253}$")

MAX_HEADERS = 10
MAX_RECIPIENTS = 20
DEFAULT_NTFY_SERVER = "https://ntfy.sh"


def _no_line_breaks(value: str) -> str:
    if any(c in value for c in "\r\n\x00"):
        raise ValueError("may not contain line breaks or NUL characters")
    return value


class WebhookSettings(BaseModel):
    """[notifications.channels.webhook]: POST a JSON document to ``url``."""
    url: str = Field(default="", max_length=2048)
    allow_http: bool = False
    # Header name -> value; the values are secrets (e.g. Authorization).
    headers: dict[str, str] = Field(default_factory=dict, max_length=MAX_HEADERS)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        return _no_line_breaks(v.strip())

    @field_validator("headers")
    @classmethod
    def _headers(cls, v: dict[str, str]) -> dict[str, str]:
        for name, value in v.items():
            check_header_name(name)
            if len(value) > 4096:
                raise ValueError("a header value may be at most 4096 characters")
            _no_line_breaks(value)
        return v


class NtfySettings(BaseModel):
    """[notifications.channels.ntfy]: publish to ``topic`` on ``server``."""
    server: str = Field(default=DEFAULT_NTFY_SERVER, max_length=2048)
    topic: str = Field(default="", max_length=64)
    allow_http: bool = False
    token: str = Field(default="", max_length=512)  # secret
    username: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=512)  # secret

    @field_validator("server", "token", "username", "password")
    @classmethod
    def _single_line(cls, v: str) -> str:
        return _no_line_breaks(v.strip())

    @field_validator("topic")
    @classmethod
    def _topic(cls, v: str) -> str:
        v = v.strip()
        if v and not NTFY_TOPIC.match(v):
            raise ValueError("topic may contain only letters, digits, '-' and '_' (at most 64)")
        return v


class EmailSettings(BaseModel):
    """[notifications.channels.email]: send a mail through an SMTP server."""
    host: str = Field(default="", max_length=253)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "tls", "none"] = "starttls"
    username: str = Field(default="", max_length=256)
    password: str = Field(default="", max_length=512)  # secret
    from_addr: str = Field(default="", max_length=320)
    to: list[str] = Field(default_factory=list, max_length=MAX_RECIPIENTS)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip()
        if v and not SMTP_HOST.match(v):
            raise ValueError("host must be a host name or an IP address")
        return v

    @field_validator("username", "password")
    @classmethod
    def _single_line(cls, v: str) -> str:
        return _no_line_breaks(v)

    @field_validator("from_addr")
    @classmethod
    def _from(cls, v: str) -> str:
        v = v.strip()
        if v and not MAIL_ADDRESS.match(v):
            raise ValueError("from_addr must be one mail address, e.g. omnisync@example.com")
        return v

    @field_validator("to")
    @classmethod
    def _to(cls, v: list[str]) -> list[str]:
        out = [a.strip() for a in v if a.strip()]
        for address in out:
            if not MAIL_ADDRESS.match(address):
                raise ValueError(f"'{address[:80]}' is not a mail address")
        return out


def check_header_name(name: str) -> str:
    if not HEADER_NAME.match(name):
        raise ValueError("a header name may contain only letters, digits and !#$%&'*+.^_`|~-")
    if name.lower() in RESERVED_HEADERS:
        raise ValueError(f"the header '{name}' is set by OmniSync and cannot be changed")
    return name


# Channel name -> the model of its stored settings.
SETTINGS_MODELS: dict[str, type[BaseModel]] = {
    "webhook": WebhookSettings,
    "ntfy": NtfySettings,
    "email": EmailSettings,
}

# Secret fields of each channel (the webhook's header values are secrets too).
SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "webhook": (),
    "ntfy": ("token", "password"),
    "email": ("password",),
}


def has_secrets(channels: dict[str, object]) -> bool:
    """Whether a raw [notifications.channels] table holds any credential."""
    for name, fields in SECRET_FIELDS.items():
        section = channels.get(name)
        if not isinstance(section, dict):
            continue
        if any(section.get(f) for f in fields):
            return True
        headers = section.get("headers") if name == "webhook" else None
        if isinstance(headers, dict) and any(headers.values()):
            return True
    return False
