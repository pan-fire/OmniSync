"""Tiny local HTTP and SMTP servers for the notification channel tests.

They speak just enough of each protocol for httpx and smtplib, record what
they receive, and listen on 127.0.0.1 only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from email.message import EmailMessage


@dataclass
class HttpRequest:
    method: str
    path: str
    headers: dict[str, str]  # lower-case names
    body: bytes

    def json(self) -> object:
        return json.loads(self.body)


@dataclass
class HttpServer:
    port: int = 0
    requests: list[HttpRequest] = field(default_factory=list)
    status: int = 200
    response_headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0  # seconds before answering (timeouts)
    ssl_context: ssl.SSLContext | None = None  # serve https with this
    _server: asyncio.base_events.Server | None = None

    @property
    def url(self) -> str:
        return f"{'https' if self.ssl_context else 'http'}://127.0.0.1:{self.port}"

    async def start(self) -> HttpServer:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, ssl=self.ssl_context)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, path, _ = lines[0].split(" ", 2)
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    name, value = line.split(":", 1)
                    headers[name.strip().lower()] = value.strip()
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            self.requests.append(HttpRequest(method, path, headers, body))
            if self.delay:
                await asyncio.sleep(self.delay)
            extra = "".join(f"{k}: {v}\r\n" for k, v in self.response_headers.items())
            writer.write(
                f"HTTP/1.1 {self.status} X\r\nContent-Length: 2\r\nConnection: close\r\n{extra}\r\nok".encode()
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()


@dataclass
class SmtpServer:
    """Plain SMTP (no TLS) with AUTH PLAIN/LOGIN; records each mail."""
    port: int = 0
    messages: list[EmailMessage] = field(default_factory=list)
    envelopes: list[tuple[str, list[str]]] = field(default_factory=list)
    logins: list[tuple[str, str]] = field(default_factory=list)
    _server: asyncio.base_events.Server | None = None

    async def start(self) -> SmtpServer:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        def send(line: str) -> None:
            writer.write(line.encode() + b"\r\n")

        mail_from, rcpts = "", []
        send("220 stub ESMTP")
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode().rstrip("\r\n")
                verb = line.split(" ", 1)[0].upper()
                if verb == "EHLO":
                    send("250-stub")
                    send("250-AUTH PLAIN LOGIN")
                    send("250 8BITMIME")
                elif verb == "HELO":
                    send("250 stub")
                elif verb == "AUTH":
                    parts = line.split(" ")
                    if parts[1].upper() == "PLAIN":
                        token = parts[2] if len(parts) > 2 else None
                        if token is None:
                            send("334 ")
                            token = (await reader.readline()).decode().strip()
                        _, user, password = base64.b64decode(token).decode().split("\0")
                    else:
                        send("334 VXNlcm5hbWU6")
                        user = base64.b64decode((await reader.readline()).strip()).decode()
                        send("334 UGFzc3dvcmQ6")
                        password = base64.b64decode((await reader.readline()).strip()).decode()
                    self.logins.append((user, password))
                    send("235 ok")
                elif verb == "MAIL":
                    mail_from, rcpts = line.split(":", 1)[1].split()[0].strip("<>"), []
                    send("250 ok")
                elif verb == "RCPT":
                    rcpts.append(line.split(":", 1)[1].strip().strip("<>"))
                    send("250 ok")
                elif verb == "DATA":
                    send("354 go")
                    data = b""
                    while True:
                        chunk = await reader.readline()
                        if chunk in (b".\r\n", b""):
                            break
                        data += chunk[1:] if chunk.startswith(b"..") else chunk
                    self.messages.append(message_from_bytes(data, policy=policy.default))  # type: ignore[arg-type]
                    self.envelopes.append((mail_from, rcpts))
                    send("250 queued")
                elif verb == "QUIT":
                    send("221 bye")
                    await writer.drain()
                    break
                else:
                    send("502 not implemented")
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()
