"""Email over implicit TLS and STARTTLS against a real local TLS server.

The server is a small threaded SMTP stub with a certificate from a test CA;
the client trusts that CA through SSL_CERT_FILE, so the channel's ordinary
default SSL context verifies the chain and the host name. The host names
used here (``smtp.test``) do not exist in DNS: only the channel's checked
lookup knows them, so a delivered mail also shows the socket went to the
pinned address.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import socket
import ssl
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from backend.services.notification_channels.settings import EmailSettings
from backend.services.notification_channels.smtp import EmailChannel
from backend.services.notification_events import (
    NotificationEvent,
    NotificationEventType,
    NotificationSeverity,
)

CERT_NAME = "smtp.test"


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _make_pki(directory: Path) -> tuple[Path, Path, Path]:
    """A CA and a server certificate for CERT_NAME; returns (ca, cert, key) paths."""
    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("OmniSync test CA")).issuer_name(_name("OmniSync test CA"))
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
            encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(CERT_NAME)).issuer_name(ca_cert.subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5)).not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(CERT_NAME)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path, cert_path, key_path = directory / "ca.pem", directory / "cert.pem", directory / "key.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return ca_path, cert_path, key_path


@dataclass
class TlsSmtpServer:
    """Threaded SMTP stub on 127.0.0.1 speaking implicit TLS or STARTTLS."""
    mode: str  # "tls" or "starttls"
    context: ssl.SSLContext
    port: int = 0
    messages: list[bytes] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    _listener: socket.socket | None = None
    _thread: threading.Thread | None = None
    _stop: threading.Event = field(default_factory=threading.Event)

    def start(self) -> TlsSmtpServer:
        self._listener = socket.create_server(("127.0.0.1", 0))
        self._listener.settimeout(0.1)
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
        if self._listener is not None:
            self._listener.close()

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn, _ = self._listener.accept()
            except TimeoutError:
                continue
            with conn:
                conn.settimeout(5)
                try:
                    if self.mode == "tls":
                        self._secure(conn)
                    else:
                        self._plain(conn)
                except OSError as exc:  # ssl.SSLError included
                    self.errors.append(type(exc).__name__)

    def _secure(self, conn: socket.socket, greet: bool = True) -> None:
        """Talk SMTP over TLS on ``conn`` (implicit TLS, or after STARTTLS)."""
        with self.context.wrap_socket(conn, server_side=True) as tls, tls.makefile("rb") as reader:
            if greet:
                tls.sendall(b"220 stub ESMTP\r\n")
            self._dialogue(tls, reader, offer_starttls=False)

    def _plain(self, conn: socket.socket) -> None:
        conn.sendall(b"220 stub ESMTP\r\n")
        with conn.makefile("rb") as reader:
            upgrade = self._dialogue(conn, reader, offer_starttls=True)
        if upgrade:
            # RFC 3207: no new greeting; the client sends EHLO again.
            self._secure(conn, greet=False)

    def _dialogue(self, sock: socket.socket, reader: BinaryIO, offer_starttls: bool) -> bool:
        """Answer commands until QUIT or EOF; True when the client asked for STARTTLS."""
        def send(line: str) -> None:
            sock.sendall(line.encode() + b"\r\n")

        while raw := reader.readline():
            verb = raw.decode().strip().split(" ", 1)[0].upper()
            if verb == "EHLO":
                if offer_starttls:
                    send("250-stub")
                    send("250 STARTTLS")
                else:
                    send("250 stub")
            elif verb == "STARTTLS" and offer_starttls:
                send("220 go ahead")
                return True
            elif verb in ("MAIL", "RCPT", "HELO", "RSET", "NOOP"):
                send("250 ok")
            elif verb == "DATA":
                send("354 go")
                data = b""
                while (chunk := reader.readline()) not in (b".\r\n", b""):
                    data += chunk
                self.messages.append(data)
                send("250 queued")
            elif verb == "QUIT":
                send("221 bye")
                break
            else:
                send("502 not implemented")
        return False


def _event() -> NotificationEvent:
    return NotificationEvent(
        severity=NotificationSeverity.ERROR, event_type=NotificationEventType.SYNC_FAILED,
        title="Sync push failed", body="connection refused",
        timestamp=dt.datetime(2026, 10, 2, 12, 0, tzinfo=dt.timezone.utc),
        profile_slug="fotos", profile_name="Fotos",
    )


def _resolve_to(monkeypatch, mapping: dict[str, str]) -> None:
    """Make the event loop's getaddrinfo answer ``mapping`` (host -> IPv4)."""
    loop = asyncio.get_running_loop()
    real = loop.getaddrinfo

    async def fake(host, port, *args, **kwargs):
        if host in mapping:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping[host], port))]
        return await real(host, port, *args, **kwargs)

    monkeypatch.setattr(loop, "getaddrinfo", fake)


@pytest.fixture
def pki(tmp_path, monkeypatch) -> tuple[Path, Path]:
    ca, cert, key = _make_pki(tmp_path)
    # ssl.create_default_context() loads the default verify paths, which
    # honour SSL_CERT_FILE: the channel then trusts the test CA.
    monkeypatch.setenv("SSL_CERT_FILE", str(ca))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    return cert, key


def _server(mode: str, pki: tuple[Path, Path]) -> TlsSmtpServer:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*pki)
    return TlsSmtpServer(mode=mode, context=ctx).start()


def _settings(server: TlsSmtpServer, host: str, mode: str) -> EmailSettings:
    return EmailSettings(host=host, port=server.port, security=mode,  # type: ignore[arg-type]
                         from_addr="omnisync@nas.lan", to=["a@example.com"])


@pytest.mark.parametrize("mode", ["tls", "starttls"])
async def test_sends_over_verified_tls(monkeypatch, pki, mode) -> None:
    _resolve_to(monkeypatch, {CERT_NAME: "127.0.0.1"})
    server = _server(mode, pki)
    try:
        await EmailChannel(lambda: _settings(server, CERT_NAME, mode)).send(_event())
    finally:
        server.stop()
    assert server.errors == []
    assert len(server.messages) == 1
    assert b"Subject: [OmniSync] Sync push failed" in server.messages[0]


@pytest.mark.parametrize("mode", ["tls", "starttls"])
async def test_host_name_mismatch_is_refused(monkeypatch, pki, mode) -> None:
    # The address is the right server, but the configured name is not on its certificate.
    _resolve_to(monkeypatch, {"other.test": "127.0.0.1"})
    server = _server(mode, pki)
    try:
        with pytest.raises(ssl.SSLCertVerificationError, match="other.test"):
            await EmailChannel(lambda: _settings(server, "other.test", mode)).send(_event())
    finally:
        server.stop()
    assert server.messages == []
