"""Which addresses a page a request named may reach (PLAN §14, D3): only those on the public internet.

A request's address is taken when it is `http(s)` and its host resolves to public addresses alone: never
this Mac (loopback), a private or carrier-grade NAT network, a link-local address, or one that names no
host at all. The page it opens is held to the same rule for everything it loads afterwards — fetches,
redirects, frames, a navigation in the middle of a game, WebSockets — by a proxy on this Mac that the
browser sends every connection through (`Proxy`). The proxy resolves each connection's host again and
connects to the very address it checked, so a name that resolved to a public address once and to a private
one the next time (DNS rebinding) is refused, and the browser never resolves a name itself.

(Playwright's `route` was the plan; it is not shown a redirect's later requests, and the browser would
resolve a name again after the check. A proxy is shown every connection and makes it itself.)
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

# the shared address space of carrier-grade NAT, which `is_global` already leaves out; named for the reader
CGNAT = ipaddress.ip_network("100.64.0.0/10")
# how long a browser's request to the proxy may take to arrive, and how much of it is read
REQUEST_TIMEOUT_S, REQUEST_MOST = 30.0, 64 * 1024
# the browser's own options for a page held by the proxy: WebRTC sends nothing past the proxy (its UDP could
# reach the local network otherwise)
BROWSER_ARGS = ("--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--webrtc-ip-handling-policy=disable_non_proxied_udp")

class Refused(Exception):
    """An address a page may not be opened at."""


Resolve = Callable[[str, int], Awaitable[list[str]]]
Connect = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


def public(address: str) -> bool:
    """Whether an IP address is one on the public internet."""
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    # an IPv6 address may carry an IPv4 one that is reached in its stead
    if ip.version == 6:
        inner = ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else None)
        if inner is not None and not public(str(inner)):
            return False
    return ip.is_global and not (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast
                                 or ip.is_unspecified or ip.is_reserved or (ip.version == 4 and ip in CGNAT))


async def resolve(host: str, port: int) -> list[str]:
    """Every address the host resolves to (an IP address as itself); none when it does not resolve."""
    try:
        return [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        pass
    try:
        found = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    return list(dict.fromkeys(entry[4][0] for entry in found))


async def reachable(host: str | None, port: int, look: Resolve = resolve) -> list[str]:
    """The host's addresses when every one of them is public; none when any is not."""
    if not host:
        return []
    addresses = await look(host, port)
    return addresses if addresses and all(public(address) for address in addresses) else []


async def allowed(url: str, look: Resolve = resolve) -> bool:
    """Whether a request's address may be opened: `http(s)`, its host resolving to public addresses alone."""
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(await reachable(parts.hostname, port, look))


class Proxy:
    """An HTTP proxy on 127.0.0.1 that connects only to public addresses, checked for each connection.

    `async with Proxy() as proxy:` and launch the browser with `proxy.settings` (and `BROWSER_ARGS`).
    `refused` lists each host:port it would not connect to."""

    def __init__(self, look: Resolve = resolve, connect: Connect | None = None) -> None:
        self.look, self.connect = look, connect or asyncio.open_connection
        self.refused: list[str] = []
        self.server: asyncio.Server | None = None
        self.port = 0
        self.tasks: set[asyncio.Task] = set()

    @property
    def settings(self) -> dict[str, str]:
        # the browser sends this Mac's own addresses past a proxy unless told not to (`<-loopback>`)
        return {"server": f"http://127.0.0.1:{self.port}", "bypass": "<-loopback>"}

    async def __aenter__(self) -> "Proxy":
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.server.close()
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.tasks.add(asyncio.current_task())
        upstream = None
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_S):
                head = await reader.readuntil(b"\r\n\r\n")
            if len(head) > REQUEST_MOST:
                return
            line, *headers = head.decode("latin-1").split("\r\n")
            method, target, version = line.split(" ", 2)
            if method == "CONNECT":
                parts = urlsplit(f"//{target}")
                host, port = parts.hostname, parts.port or 443
            else:
                parts = urlsplit(target)
                host, port = parts.hostname, parts.port or 80
            addresses = await reachable(host, port, self.look) if parts.scheme in ("", "http") else []
            if not addresses:
                self.refused.append(f"{host}:{port}")
                # a tunnel refused; a plain request given no answer at all, which a page sees as a failed load
                # (an answer of the proxy's own would reach the page as the server's)
                if method == "CONNECT":
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                return
            upstream = await self.connect(addresses[0], port)
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                # one request a connection: the next may be for another host, and is checked on its own
                path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
                kept = [header for header in headers if header and header.split(":", 1)[0].strip().lower()
                        not in ("proxy-connection", "connection", "keep-alive", "proxy-authorization")]
                lines = "".join(f"{header}\r\n" for header in kept)
                upstream[1].write(f"{method} {path} {version}\r\n{lines}Connection: close\r\n\r\n".encode("latin-1"))
            await asyncio.gather(_pipe(reader, upstream[1]), _pipe(upstream[0], writer))
        except (OSError, ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
            pass
        finally:
            self.tasks.discard(asyncio.current_task())
            for opened in (writer, *((upstream[1],) if upstream else ())):
                opened.close()


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    try:
        while chunk := await source.read(64 * 1024):
            sink.write(chunk)
            await sink.drain()
    except OSError:
        pass
    finally:
        if sink.can_write_eof():
            try:
                sink.write_eof()
            except OSError:
                pass
