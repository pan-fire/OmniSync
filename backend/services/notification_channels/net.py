"""Outbound requests of the webhook, ntfy and email channels, with SSRF guards.

The address a channel delivers to is set by the administrator, so private
addresses are allowed: Home Assistant, n8n, a self-hosted ntfy or a mail
relay on the LAN are what many people point them at. What is refused:

* any scheme but http and https;
* plain http, unless the user allowed it (``allow_http``) and every
  address the host resolves to is loopback or private (LAN, Tailscale's
  100.64.0.0/10), so credentials never cross the internet in clear text;
* link-local addresses (169.254.0.0/16, fe80::/10), which include the
  cloud metadata services (169.254.169.254, fd00:ec2::254 on AWS IPv6),
  and addresses that are no destination at all (0.0.0.0, multicast);
* the same IPv4 addresses embedded in IPv6 (IPv4-mapped, NAT64
  ``64:ff9b::/96``, 6to4 ``2002::/16``, Teredo, IPv4-compatible), which a
  dual-stack host or a NAT64 gateway would deliver to the IPv4 address.

The host name is resolved once and the request is sent to the address
that was checked (the Host header and TLS server name stay the host
name), so a DNS answer that changes between the check and the connection
cannot redirect it. Redirects are never followed.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit

import httpx

# Seconds one HTTP request (connect, send, response) may take. The
# dispatcher bounds the whole delivery at CHANNEL_TIMEOUT (20 s) as well.
HTTP_TIMEOUT = 10.0

# Addresses never contacted, besides link-local, multicast and unspecified.
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("fd00:ec2::254/128"),  # AWS metadata over IPv6
    ipaddress.ip_network("100.100.100.200/32"),  # Alibaba Cloud metadata
)
# Counted as private for plain http: shared address space (CGNAT, Tailscale).
_SHARED_NETWORK = ipaddress.ip_network("100.64.0.0/10")

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


class UnsafeAddressError(ValueError):
    """The destination is refused (scheme, plain http, a blocked address)."""


# IPv6 forms that carry an IPv4 address and reach it: NAT64 (RFC 6052's
# well-known prefix), 6to4 and IPv4-compatible. IPv4-mapped (::ffff:0:0/96)
# and Teredo have properties of their own in ipaddress.
_NAT64_NETWORK = ipaddress.ip_network("64:ff9b::/96")
_IPV4_COMPATIBLE = ipaddress.ip_network("::/96")


def embedded_ipv4(ip: IPAddress) -> ipaddress.IPv4Address | None:
    """The IPv4 address an IPv6 address leads to, if it embeds one.

    IPv4-mapped (``::ffff:a.b.c.d``), NAT64 (``64:ff9b::a.b.c.d``), 6to4
    (``2002:aabb:ccdd::/48``), Teredo (the client address) and the old
    IPv4-compatible form (``::a.b.c.d``). A guard that only looked at the
    IPv6 address would let ``64:ff9b::169.254.169.254`` reach the metadata
    service through a NAT64 gateway.
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in _NAT64_NETWORK:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip.teredo is not None:
        return ip.teredo[1]
    if ip in _IPV4_COMPATIBLE and int(ip) > 1:  # not :: or ::1
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return None


def _is_blocked_one(ip: IPAddress) -> bool:
    return (
        ip.is_link_local or ip.is_multicast or ip.is_unspecified
        or any(ip in net for net in _BLOCKED_NETWORKS if net.version == ip.version)
    )


def is_blocked(ip: IPAddress) -> bool:
    """Addresses no channel may send to: link-local/metadata, multicast, unspecified.

    An IPv6 address that embeds an IPv4 one is blocked when either is.
    """
    v4 = embedded_ipv4(ip)
    return _is_blocked_one(ip) or (v4 is not None and _is_blocked_one(v4))


def is_local(ip: IPAddress) -> bool:
    """Loopback or private (LAN, ULA, CGNAT/Tailscale): where plain http may go.

    An IPv6 address that embeds an IPv4 one is judged by that address:
    ipaddress counts all of 2002::/16 (6to4) as private, but 6to4 traffic to
    a public IPv4 address crosses the internet.
    """
    v4 = embedded_ipv4(ip)
    if v4 is not None:
        ip = v4
    if ip.is_loopback or ip.is_private:
        return True
    return isinstance(ip, ipaddress.IPv4Address) and ip in _SHARED_NETWORK


def _literal(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def check_http_url(url: str, allow_http: bool) -> str:
    """The checks of a webhook or ntfy URL that need no DNS; raises UnsafeAddressError.

    Run when the settings are saved. A host name is checked again, by the
    addresses it resolves to, before every request (resolve_checked).
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeAddressError("the URL is not valid") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeAddressError("the URL must start with https:// (or http:// on a local network)")
    host = parts.hostname or ""
    if not host:
        raise UnsafeAddressError("the URL has no host")
    if parts.username is not None or parts.password is not None:
        raise UnsafeAddressError("put credentials in a header, not in the URL")
    if port == 0:
        raise UnsafeAddressError("the URL has no valid port")
    ip = _literal(host)
    if ip is not None and is_blocked(ip):
        raise UnsafeAddressError("link-local and metadata addresses are not allowed")
    if parts.scheme == "http":
        if not allow_http:
            raise UnsafeAddressError("the URL must use https; allow http only for an address on your local network")
        if ip is not None and not is_local(ip):
            raise UnsafeAddressError("http is only allowed for loopback or private addresses; use https")
    return url


async def resolve_checked(host: str, port: int, *, require_local: bool) -> list[IPAddress]:
    """The addresses ``host`` resolves to, all of them allowed; raises UnsafeAddressError.

    ``require_local``: every address must be loopback or private (plain http).
    """
    ip = _literal(host)
    if ip is not None:
        addresses = [ip]
    else:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
            )
        except OSError as exc:
            raise ConnectionError(f"could not resolve {host}: {exc}") from exc
        addresses = []
        for info in infos:
            addr = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
            if addr not in addresses:
                addresses.append(addr)
        if not addresses:
            raise ConnectionError(f"{host} has no address")
    for addr in addresses:
        if is_blocked(addr):
            raise UnsafeAddressError(f"{host} resolves to a link-local or metadata address ({addr})")
        if require_local and not is_local(addr):
            raise UnsafeAddressError(f"{host} resolves to a public address ({addr}); plain http is refused")
    return addresses


def describe_url(url: str) -> str:
    """scheme://host[:port] of ``url``: what logs say (the path may hold a secret token)."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], "", "", ""))


async def post_json(
    url: str,
    payload: object,
    *,
    allow_http: bool,
    headers: dict[str, str] | None = None,
    auth: tuple[str, str] | None = None,
    timeout: float = HTTP_TIMEOUT,
) -> httpx.Response:
    """POST ``payload`` as JSON to ``url`` after the SSRF checks; raise unless 2xx.

    The connection goes to the checked address; a redirect is an error.
    """
    check_http_url(url, allow_http)
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    addresses = await resolve_checked(host, port, require_local=parts.scheme == "http")
    pinned = addresses[0]
    ip_host = f"[{pinned}]" if pinned.version == 6 else str(pinned)
    netloc = f"{ip_host}:{parts.port}" if parts.port else ip_host
    target = urlunsplit((parts.scheme, netloc, parts.path or "/", parts.query, ""))

    request_headers = {**(headers or {}), "Host": parts.netloc}
    extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
    async with httpx.AsyncClient(
        timeout=timeout, follow_redirects=False, trust_env=False, auth=auth,
    ) as client:
        request = client.build_request(
            "POST", target, json=payload, headers=request_headers, extensions=extensions,
        )
        response = await client.send(request)
    if response.is_redirect:
        raise ConnectionError(f"{describe_url(url)} answered with a redirect ({response.status_code}); "
                              "redirects are not followed, use the final URL")
    if not response.is_success:
        raise ConnectionError(f"{describe_url(url)} answered HTTP {response.status_code}")
    return response
