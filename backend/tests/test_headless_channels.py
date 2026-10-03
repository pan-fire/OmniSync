"""Webhook, ntfy and email channels: delivery, SSRF guards, settings, dispatch.

Delivery runs against tiny local servers (backend/tests/notify_servers.py):
nothing leaves the machine.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import smtplib
import socket
import stat
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import pytest_asyncio
import toml
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes import notifications
from backend.db.models import Base, NotificationLog
from backend.main import app
from backend.services.config import ConfigService
from backend.services.history import prune_history
from backend.services.notification_channels import net
from backend.services.notification_channels.net import (
    UnsafeAddressError,
    check_http_url,
    post_json,
    resolve_checked,
)
from backend.services.notification_channels.ntfy import NtfyChannel
from backend.services.notification_channels.settings import EmailSettings, NtfySettings, WebhookSettings
from backend.services.notification_channels.smtp import EmailChannel
from backend.services.notification_channels.webhook import WebhookChannel
from backend.services.notification_dispatcher import NotificationDispatcher
from backend.services.notification_events import (
    NotificationEvent,
    NotificationEventType,
    NotificationSeverity,
    sync_failed_event,
)
from backend.tests.auth import AUTH_HEADERS
from backend.tests.notify_servers import HttpServer, SmtpServer
from backend.version import get_version

SECRET = "s3cr3t-T0KEN"


def _event(severity: NotificationSeverity = NotificationSeverity.ERROR) -> NotificationEvent:
    return NotificationEvent(
        severity=severity, event_type=NotificationEventType.SYNC_FAILED,
        title="Sync push failed — Fotos Müller", body="Tür zu: connection refused ✓",
        timestamp=datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc),
        profile_slug="fotos", profile_name="Fotos Müller",
    )


@pytest_asyncio.fixture
async def http_server():
    server = await HttpServer().start()
    yield server
    await server.stop()


@pytest_asyncio.fixture
async def smtp_server():
    server = await SmtpServer().start()
    yield server
    await server.stop()


def _resolve_to(monkeypatch, mapping: dict[str, str]) -> None:
    """Make getaddrinfo answer ``mapping`` (host -> address) for the given names."""
    loop = asyncio.get_running_loop()
    real = loop.getaddrinfo

    async def fake(host, port, *args, **kwargs):
        if host in mapping:
            family = socket.AF_INET6 if ":" in mapping[host] else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (mapping[host], port))]
        return await real(host, port, *args, **kwargs)

    monkeypatch.setattr(loop, "getaddrinfo", fake)


# --- SSRF guard ---


class TestUrlChecks:

    @pytest.mark.parametrize("url", [
        "https://hooks.example.com/x",
        "https://10.0.0.5:8123/api/webhook/abc",
        "http://192.168.1.10:5678/webhook",
        "http://[fd12::1]/x",
        "http://100.101.102.103/x",  # Tailscale
        "http://homeassistant.local:8123/api/webhook/x",  # resolved before sending
    ])
    def test_allowed(self, url) -> None:
        assert check_http_url(url, allow_http=True) == url

    @pytest.mark.parametrize("url", [
        "ftp://example.com/x", "file:///etc/passwd", "gopher://x", "javascript:alert(1)",
        "https://", "https://user:pw@example.com/x",
        "https://169.254.169.254/latest/meta-data", "http://169.254.169.254/",
        "http://[fd00:ec2::254]/", "https://[fe80::1]/", "http://0.0.0.0/",
        "http://[::ffff:169.254.169.254]/", "https://100.100.100.200/",
        "http://8.8.8.8/x",  # plain http to a public address
    ])
    def test_refused(self, url) -> None:
        with pytest.raises(UnsafeAddressError):
            check_http_url(url, allow_http=True)

    def test_http_needs_the_opt_in(self) -> None:
        with pytest.raises(UnsafeAddressError, match="https"):
            check_http_url("http://192.168.1.10/x", allow_http=False)

    async def test_host_resolving_to_metadata_is_refused(self, monkeypatch) -> None:
        _resolve_to(monkeypatch, {"evil.example": "169.254.169.254"})
        with pytest.raises(UnsafeAddressError, match="link-local"):
            await resolve_checked("evil.example", 443, require_local=False)

    async def test_http_host_resolving_to_public_is_refused(self, monkeypatch) -> None:
        _resolve_to(monkeypatch, {"public.example": "93.184.216.34"})
        with pytest.raises(UnsafeAddressError, match="public"):
            await resolve_checked("public.example", 80, require_local=True)
        assert await resolve_checked("public.example", 443, require_local=False) == [
            ipaddress.ip_address("93.184.216.34"),
        ]

    async def test_request_goes_to_the_checked_address(self, monkeypatch, http_server) -> None:
        """DNS is asked once; the connection uses that answer, the Host header the name."""
        _resolve_to(monkeypatch, {"ha.lan": "127.0.0.1"})
        await post_json(f"http://ha.lan:{http_server.port}/hook?k=1", {"a": 1}, allow_http=True)
        (request,) = http_server.requests
        assert request.headers["host"] == f"ha.lan:{http_server.port}"
        assert request.path == "/hook?k=1"

    async def test_redirect_is_not_followed(self, http_server) -> None:
        http_server.status = 302
        http_server.response_headers = {"Location": "http://169.254.169.254/"}
        with pytest.raises(ConnectionError, match="redirect"):
            await post_json(http_server.url + "/x", {}, allow_http=True)
        assert len(http_server.requests) == 1

    async def test_error_status_raises(self, http_server) -> None:
        http_server.status = 500
        with pytest.raises(ConnectionError, match="HTTP 500"):
            await post_json(http_server.url + "/x", {}, allow_http=True)

    async def test_timeout(self, http_server) -> None:
        http_server.delay = 2
        with pytest.raises(httpx.TimeoutException):
            await post_json(http_server.url + "/x", {}, allow_http=True, timeout=0.2)


    @pytest.mark.parametrize("addr", [
        "64:ff9b::a9fe:a9fe",          # NAT64 of 169.254.169.254
        "64:ff9b::6464:64c8",          # NAT64 of 100.100.100.200 (Alibaba metadata)
        "64:ff9b::0.0.0.0",
        "64:ff9b::e000:1",             # NAT64 of 224.0.0.1 (multicast)
        "2002:a9fe:a9fe::1",           # 6to4 of 169.254.169.254
        "2002:6464:64c8:1::1",
        "::ffff:169.254.169.254",      # IPv4-mapped
        "::169.254.169.254",           # IPv4-compatible
        "2001:0:4136:e378:8000:63bf:5601:5601",  # Teredo, client 169.254.169.254
    ])
    def test_embedded_blocked_ipv4_is_blocked(self, addr) -> None:
        assert net.is_blocked(ipaddress.ip_address(addr))
        with pytest.raises(UnsafeAddressError):
            check_http_url(f"https://[{addr}]/x", allow_http=False)

    @pytest.mark.parametrize("addr", ["64:ff9b::808:808", "2002:808:808::1", "::ffff:8.8.8.8", "2606:4700::1111"])
    def test_embedded_public_ipv4_is_allowed_over_https(self, addr) -> None:
        assert not net.is_blocked(ipaddress.ip_address(addr))
        assert not net.is_local(ipaddress.ip_address(addr))

    @pytest.mark.parametrize("addr", ["2002:808:808::1", "64:ff9b::808:808"])
    def test_embedded_public_ipv4_is_no_local_address_for_http(self, addr) -> None:
        """ipaddress calls all of 2002::/16 private; 6to4 to a public address is not."""
        with pytest.raises(UnsafeAddressError, match="loopback or private"):
            check_http_url(f"http://[{addr}]/x", allow_http=True)

    @pytest.mark.parametrize("addr", ["2002:c0a8:10a::1", "64:ff9b::a00:5", "::ffff:192.168.1.10"])
    def test_embedded_private_ipv4_counts_as_local(self, addr) -> None:
        assert net.is_local(ipaddress.ip_address(addr))

    async def test_host_resolving_to_nat64_metadata_is_refused(self, monkeypatch) -> None:
        _resolve_to(monkeypatch, {"evil6.example": "64:ff9b::a9fe:a9fe"})
        with pytest.raises(UnsafeAddressError, match="link-local"):
            await resolve_checked("evil6.example", 443, require_local=False)

    async def test_https_goes_to_the_checked_address_with_the_host_name_for_tls(self, monkeypatch, tls) -> None:
        """https: the connection goes to the resolved address, while SNI, the
        certificate check and the Host header use the name."""
        server_ctx, client_ctx, seen_sni = tls
        server = await HttpServer(ssl_context=server_ctx).start()
        try:
            _resolve_to(monkeypatch, {"hooks.test": "127.0.0.1"})
            _trust(monkeypatch, client_ctx)
            await post_json(f"https://hooks.test:{server.port}/hook?k=1", {"a": 1}, allow_http=False)
        finally:
            await server.stop()
        (request,) = server.requests
        assert request.headers["host"] == f"hooks.test:{server.port}"
        assert request.path == "/hook?k=1"
        assert seen_sni == ["hooks.test"]

    async def test_https_certificate_must_match_the_name(self, monkeypatch, tls) -> None:
        """The certificate is checked against the name, not the pinned address."""
        server_ctx, client_ctx, _ = tls
        server = await HttpServer(ssl_context=server_ctx).start()
        try:
            _resolve_to(monkeypatch, {"other.test": "127.0.0.1"})
            _trust(monkeypatch, client_ctx)
            with pytest.raises(httpx.ConnectError):
                await post_json(f"https://other.test:{server.port}/x", {}, allow_http=False)
        finally:
            await server.stop()
        assert server.requests == []


@pytest.fixture(scope="module")
def _tls_material(tmp_path_factory):
    """A CA-less self-signed certificate for hooks.test (only), as PEM files."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "hooks.test")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("hooks.test")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    folder = tmp_path_factory.mktemp("tls")
    cert_file, key_file = folder / "cert.pem", folder / "key.pem"
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    return cert_file, key_file


@pytest.fixture
def tls(_tls_material):
    """(server context, client context trusting only that certificate, SNI names seen)."""
    import ssl

    cert_file, key_file = _tls_material
    seen: list[str | None] = []
    server_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_ctx.load_cert_chain(cert_file, key_file)
    server_ctx.sni_callback = lambda _sock, server_name, _ctx: seen.append(server_name)
    client_ctx = ssl.create_default_context(cafile=str(cert_file))
    return server_ctx, client_ctx, seen


def _trust(monkeypatch, client_ctx) -> None:
    """Make post_json's client verify against ``client_ctx`` (the test certificate)."""
    real = httpx.AsyncClient

    class TrustingClient(real):
        def __init__(self, **kwargs):
            super().__init__(verify=client_ctx, **kwargs)

    monkeypatch.setattr(net.httpx, "AsyncClient", TrustingClient)


# --- Webhook ---


class TestWebhookChannel:

    async def test_posts_the_event_as_json(self, http_server) -> None:
        settings = WebhookSettings(url=http_server.url + "/hook", allow_http=True,
                                   headers={"Authorization": f"Bearer {SECRET}"})
        channel = WebhookChannel(lambda: settings)
        assert await channel.is_available()
        await channel.send(_event())
        (request,) = http_server.requests
        assert request.method == "POST" and request.path == "/hook"
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["user-agent"] == f"OmniSync/{get_version()}"
        assert request.json() == {
            "source": "omnisync", "version": get_version(),
            "event_type": "sync_failed", "severity": "error",
            "title": "Sync push failed — Fotos Müller", "body": "Tür zu: connection refused ✓",
            "profile_slug": "fotos", "profile_name": "Fotos Müller",
            "timestamp": "2026-10-02T12:00:00+00:00",
        }

    async def test_http_without_opt_in_is_refused(self, http_server) -> None:
        channel = WebhookChannel(lambda: WebhookSettings(url=http_server.url + "/hook"))
        with pytest.raises(UnsafeAddressError):
            await channel.send(_event())
        assert http_server.requests == []

    async def test_unconfigured_is_unavailable(self) -> None:
        channel = WebhookChannel(lambda: WebhookSettings())
        assert await channel.describe() == {"available": False, "missing_dependencies": ["webhook_url"]}


# --- ntfy ---


class TestNtfyChannel:

    async def test_json_publish_with_token(self, http_server) -> None:
        settings = NtfySettings(server=http_server.url, topic="omni_test", allow_http=True, token=SECRET)
        await NtfyChannel(lambda: settings).send(_event())
        (request,) = http_server.requests
        assert request.path == "/"
        assert request.headers["authorization"] == f"Bearer {SECRET}"
        body = request.json()
        assert body == {
            "topic": "omni_test", "title": "Sync push failed — Fotos Müller",
            "message": "Tür zu: connection refused ✓", "priority": 5,
            "tags": ["rotating_light", "omnisync", "sync_failed", "fotos"],
        }
        assert "Müller".encode() in request.body  # UTF-8 JSON, no header encoding

    @pytest.mark.parametrize(("severity", "priority"), [
        (NotificationSeverity.DEBUG, 2), (NotificationSeverity.INFO, 3),
        (NotificationSeverity.WARNING, 4), (NotificationSeverity.ERROR, 5),
    ])
    async def test_priority_follows_severity(self, http_server, severity, priority) -> None:
        settings = NtfySettings(server=http_server.url + "/", topic="t", allow_http=True,
                                username="me", password=SECRET)
        await NtfyChannel(lambda: settings).send(_event(severity))
        (request,) = http_server.requests
        assert request.json()["priority"] == priority
        assert request.headers["authorization"] == httpx.BasicAuth("me", SECRET)._auth_header

    async def test_missing_topic(self) -> None:
        info = await NtfyChannel(lambda: NtfySettings()).describe()
        assert info == {"available": False, "missing_dependencies": ["ntfy_topic"]}

    def test_default_server_is_ntfy_sh(self) -> None:
        assert NtfySettings().server == "https://ntfy.sh"


# --- Email ---


class TestEmailChannel:

    def _settings(self, server: SmtpServer, **kw) -> EmailSettings:
        return EmailSettings(**{
            "host": "127.0.0.1", "port": server.port, "security": "none",
            "from_addr": "omnisync@nas.lan", "to": ["a@example.com", "b@example.com"], **kw,
        })

    async def test_sends_a_mail(self, smtp_server) -> None:
        settings = self._settings(smtp_server, username="nas", password=SECRET)
        channel = EmailChannel(lambda: settings)
        assert await channel.is_available()
        await channel.send(_event())
        assert smtp_server.logins == [("nas", SECRET)]
        assert smtp_server.envelopes == [("omnisync@nas.lan", ["a@example.com", "b@example.com"])]
        (msg,) = smtp_server.messages
        assert msg["Subject"] == "[OmniSync] Sync push failed — Fotos Müller"
        assert msg["From"] == "omnisync@nas.lan"
        assert msg["X-OmniSync-Severity"] == "error"
        text = msg.get_content()
        assert "Tür zu: connection refused ✓" in text
        assert "Profile: Fotos Müller" in text
        assert f"OmniSync {get_version()}" in text

    async def test_without_login(self, smtp_server) -> None:
        await EmailChannel(lambda: self._settings(smtp_server)).send(_event())
        assert smtp_server.logins == [] and len(smtp_server.messages) == 1

    async def test_starttls_is_required_by_default(self, smtp_server) -> None:
        """A server without STARTTLS fails the default setting instead of sending in clear text."""
        settings = self._settings(smtp_server, security="starttls", username="nas", password=SECRET)
        with pytest.raises(smtplib.SMTPNotSupportedError):
            await EmailChannel(lambda: settings).send(_event())
        assert smtp_server.logins == [] and smtp_server.messages == []

    async def test_plain_login_only_on_the_local_network(self, monkeypatch, smtp_server) -> None:
        _resolve_to(monkeypatch, {"smtp.example.com": "93.184.216.34"})
        settings = self._settings(smtp_server, host="smtp.example.com", username="nas", password=SECRET)
        with pytest.raises(UnsafeAddressError):
            await EmailChannel(lambda: settings).send(_event())

    async def test_metadata_host_is_refused(self) -> None:
        settings = EmailSettings(host="169.254.169.254", from_addr="a@b.c", to=["d@e.f"])
        with pytest.raises(UnsafeAddressError):
            await EmailChannel(lambda: settings).send(_event())

    async def test_line_breaks_in_titles_do_not_inject_headers(self, smtp_server) -> None:
        event = _event().model_copy(update={"title": "x\r\nBcc: evil@example.com"})
        await EmailChannel(lambda: self._settings(smtp_server)).send(event)
        (msg,) = smtp_server.messages
        assert msg["Bcc"] is None
        assert smtp_server.envelopes[0][1] == ["a@example.com", "b@example.com"]

    async def test_missing_settings(self) -> None:
        info = await EmailChannel(lambda: EmailSettings()).describe()
        assert info["missing_dependencies"] == ["smtp_host", "mail_from", "mail_to"]

    @pytest.mark.parametrize("bad", [
        {"to": ["not-an-address"]}, {"from_addr": "a@b.c\r\nBcc: x@y.z"}, {"port": 0},
        {"security": "ssl"}, {"host": "smtp host"},
    ])
    def test_invalid_settings(self, bad) -> None:
        with pytest.raises(ValueError):
            EmailSettings(**bad)


# --- API: settings, masking, test button ---


@pytest_asyncio.fixture
async def api(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    config_path = tmp_path / "config.toml"
    dispatcher = NotificationDispatcher(ConfigService(config_path=config_path), factory)
    dispatcher.register_channel(WebhookChannel(lambda: dispatcher.channel_settings("webhook")))
    dispatcher.register_channel(NtfyChannel(lambda: dispatcher.channel_settings("ntfy")))
    dispatcher.register_channel(EmailChannel(lambda: dispatcher.channel_settings("email")))
    notifications.set_dispatcher(dispatcher)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=AUTH_HEADERS) as c:
        yield c, dispatcher, config_path, factory
    notifications.set_dispatcher(None)
    await engine.dispose()


async def _put(client, channels: dict) -> httpx.Response:
    return await client.put("/notifications/config", json={"channels": channels})


class TestSettingsApi:

    async def test_defaults(self, api) -> None:
        client, _, _, _ = api
        data = (await client.get("/notifications/config")).json()["channels"]
        assert data["webhook"]["enabled"] is False
        assert data["webhook"]["webhook"] == {"url": "", "allow_http": False, "headers": []}
        assert data["ntfy"]["ntfy"]["server"] == "https://ntfy.sh"
        assert data["email"]["email"]["security"] == "starttls" and data["email"]["email"]["port"] == 587
        assert "webhook" not in data["webpush"]

    async def test_ntfy_token_is_masked_kept_and_cleared(self, api) -> None:
        client, _, config_path, _ = api
        resp = await _put(client, {"ntfy": {"enabled": True, "ntfy": {"topic": "nas", "token": SECRET}}})
        assert resp.status_code == 200, resp.text
        assert SECRET not in resp.text
        assert resp.json()["channels"]["ntfy"]["ntfy"]["token_set"] is True
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
        assert toml.loads(config_path.read_text())["notifications"]["channels"]["ntfy"]["token"] == SECRET

        # An update without (or with an empty) token keeps it.
        resp = await _put(client, {"ntfy": {"ntfy": {"topic": "nas2", "token": ""}}})
        assert resp.json()["channels"]["ntfy"]["ntfy"] == {
            "server": "https://ntfy.sh", "topic": "nas2", "allow_http": False, "username": "",
            "token_set": True, "password_set": False,
        }
        get = await client.get("/notifications/config")
        assert SECRET not in get.text

        resp = await _put(client, {"ntfy": {"ntfy": {"clear": ["token"]}}})
        assert resp.json()["channels"]["ntfy"]["ntfy"]["token_set"] is False
        assert SECRET not in config_path.read_text()

    async def test_webhook_headers_keep_if_empty(self, api) -> None:
        client, dispatcher, _, _ = api
        resp = await _put(client, {"webhook": {"webhook": {
            "url": "https://hooks.example.com/x",
            "headers": [{"name": "Authorization", "value": f"Bearer {SECRET}"}, {"name": "X-A", "value": "1"}],
        }}})
        assert resp.status_code == 200, resp.text
        assert SECRET not in resp.text
        assert resp.json()["channels"]["webhook"]["webhook"]["headers"] == [
            {"name": "Authorization", "value_set": True}, {"name": "X-A", "value_set": True},
        ]
        # Resent with an empty value: kept. Left out: removed.
        resp = await _put(client, {"webhook": {"webhook": {"headers": [{"name": "authorization", "value": ""}]}}})
        assert resp.status_code == 200, resp.text
        assert dispatcher.channel_settings("webhook").headers == {"authorization": f"Bearer {SECRET}"}

    async def test_email_password_is_masked(self, api) -> None:
        client, dispatcher, _, _ = api
        resp = await _put(client, {"email": {"email": {
            "host": "smtp.example.com", "username": "me", "password": SECRET,
            "from_addr": "nas@example.com", "to": ["me@example.com"],
        }}})
        assert resp.status_code == 200, resp.text
        assert SECRET not in resp.text
        assert resp.json()["channels"]["email"]["email"]["password_set"] is True
        await _put(client, {"email": {"email": {"port": 465, "security": "tls"}}})
        assert dispatcher.channel_settings("email").password == SECRET

    @pytest.mark.parametrize(("channels", "fragment"), [
        ({"webhook": {"webhook": {"url": "http://192.168.1.2/x"}}}, "https"),
        ({"webhook": {"webhook": {"url": "https://169.254.169.254/x"}}}, "link-local"),
        ({"webhook": {"webhook": {"url": "file:///etc/passwd"}}}, "https"),
        ({"webhook": {"webhook": {"url": "https://a.b", "headers": [{"name": "Host", "value": "x"}]}}}, "Host"),
        ({"webhook": {"webhook": {"url": "https://a.b", "headers": [{"name": "X-A"}]}}}, "needs a value"),
        ({"ntfy": {"ntfy": {"server": "http://8.8.8.8", "allow_http": True}}}, "loopback or private"),
        ({"ntfy": {"ntfy": {"topic": "a/b"}}}, "topic"),
        ({"email": {"email": {"to": ["x"]}}}, "mail address"),
        ({"webpush": {"email": {"host": "x"}}}, "cannot be stored"),
    ])
    async def test_invalid_settings_400(self, api, channels, fragment) -> None:
        client, _, config_path, _ = api
        resp = await _put(client, channels)
        assert resp.status_code == 400, resp.text
        assert fragment in resp.json()["detail"]
        assert not config_path.exists()

    async def test_test_button_and_dispatch(self, api, http_server) -> None:
        """The per-channel test reaches the webhook even while it is off; dispatch honours min_severity."""
        client, dispatcher, _, factory = api
        resp = await _put(client, {"webhook": {"min_severity": "error", "webhook": {
            "url": http_server.url + "/hook", "allow_http": True,
        }}})
        assert resp.status_code == 200, resp.text
        data = (await client.post("/notifications/test", json={"channel": "webhook"})).json()
        assert data == {"success": True, "channels_delivered": ["webhook"], "errors": {}}
        assert http_server.requests[-1].json()["event_type"] == "test"

        await _put(client, {"webhook": {"enabled": True}})
        delivered, errors = await dispatcher.dispatch(sync_failed_event("push", "boom", profile_slug="p"))
        assert "webhook" in delivered and "webhook" not in errors
        assert len(http_server.requests) == 2
        delivered, _ = await dispatcher.dispatch(_event(NotificationSeverity.WARNING))
        assert "webhook" not in delivered and len(http_server.requests) == 2
        # ntfy and email are off and unconfigured: not tried.
        assert "ntfy" not in errors and "email" not in errors

    async def test_failing_channel_does_not_stop_the_others(self, api, http_server, smtp_server) -> None:
        client, dispatcher, _, _ = api
        http_server.status = 500
        await _put(client, {
            "webhook": {"enabled": True, "webhook": {"url": http_server.url, "allow_http": True}},
            "email": {"enabled": True, "email": {
                "host": "127.0.0.1", "port": smtp_server.port, "security": "none",
                "from_addr": "a@b.c", "to": ["d@e.f"],
            }},
        })
        delivered, errors = await dispatcher.dispatch(_event())
        assert delivered == ["email"]
        assert errors == {"webhook": "failed"}
        assert len(smtp_server.messages) == 1

    async def test_status_reports_missing_settings(self, api) -> None:
        client, _, _, _ = api
        data = (await client.get("/notifications/channels/status")).json()["channels"]
        assert data["webhook"]["missing_dependencies"] == ["webhook_url"]
        assert data["ntfy"]["missing_dependencies"] == ["ntfy_topic"]
        assert data["email"]["missing_dependencies"] == ["smtp_host", "mail_from", "mail_to"]

    async def test_invalid_stored_settings_are_reported_without_values(self, api, caplog) -> None:
        _, dispatcher, config_path, _ = api
        config_path.write_text(toml.dumps({"notifications": {"channels": {
            "email": {"port": "not-a-port", "password": SECRET},
        }}}))
        config_path.chmod(0o644)
        with caplog.at_level(logging.INFO):
            dispatcher.reload()
        assert dispatcher.config_error is True
        assert dispatcher.channel_settings("email").port == 587
        assert SECRET not in caplog.text
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600  # tightened: it holds a secret


# --- notification_log pruning ---


async def test_prune_history_removes_old_notifications(tmp_path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    async with factory() as session:
        for days in (200, 100, 5):
            session.add(NotificationLog(
                event_type="test", severity="info", title=f"{days}", body="",
                timestamp=now - timedelta(days=days), channels_delivered="[]",
            ))
        await session.commit()
    result = await prune_history(factory, 90)
    assert result.notifications == 2
    async with factory() as session:
        titles = (await session.execute(select(NotificationLog.title))).scalars().all()
    assert titles == ["5"]
    assert (await prune_history(factory, 0)).notifications == 0
    await engine.dispose()


def test_guard_constants() -> None:
    assert net.is_blocked(ipaddress.ip_address("fd00:ec2::254"))
    assert net.is_local(ipaddress.ip_address("100.64.0.1"))
    assert not net.is_local(ipaddress.ip_address("8.8.8.8"))
