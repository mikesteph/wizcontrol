"""Low level WiZ local UDP protocol.

WiZ lights listen on UDP port 38899 and speak small JSON messages, e.g.
    {"method": "setPilot", "params": {"state": true, "dimming": 80}}
Replies come back from the light's port 38899 to whatever port we sent from.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import sys
from collections import defaultdict
from typing import Callable, Iterable

log = logging.getLogger(__name__)

WIZ_PORT = 38899

Listener = Callable[[str, dict], None]


class WizError(Exception):
    """The light answered with an error object."""


class _WizProtocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.waiters: dict[tuple[str, str], list[asyncio.Future]] = defaultdict(list)
        self.listeners: list[Listener] = []

    def connection_made(self, transport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # type: ignore[override]
        try:
            msg = json.loads(data.decode("utf-8", "replace"))
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        ip = addr[0]
        for cb in list(self.listeners):
            try:
                cb(ip, msg)
            except Exception:  # pragma: no cover - listener bugs must not kill the socket
                log.exception("listener failed")
        key = (ip, str(msg.get("method", "")))
        waiters = self.waiters.pop(key, None)
        if waiters:
            for fut in waiters:
                if not fut.done():
                    fut.set_result(msg)

    def error_received(self, exc: Exception) -> None:  # type: ignore[override]
        # ICMP "port unreachable" and friends. Harmless for a UDP fire-and-forget protocol.
        log.debug("udp error: %s", exc)

    def connection_lost(self, exc) -> None:  # type: ignore[override]
        log.debug("udp socket closed: %s", exc)


class WizClient:
    """One shared UDP socket used for every light."""

    def __init__(self) -> None:
        self._proto: _WizProtocol | None = None
        self._transport: asyncio.DatagramTransport | None = None

    async def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if sys.platform == "win32" and hasattr(socket, "SIO_UDP_CONNRESET"):
            # Stop Windows from reporting ICMP port-unreachable as a socket error,
            # which otherwise breaks the receive loop after sweeping a subnet.
            try:
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)  # type: ignore[attr-defined]
            except (OSError, ValueError):
                pass
        sock.bind(("0.0.0.0", 0))
        sock.setblocking(False)
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(_WizProtocol, sock=sock)
        self._transport = transport  # type: ignore[assignment]
        self._proto = proto

    def close(self) -> None:
        if self._transport:
            self._transport.close()

    # ------------------------------------------------------------------ sending
    def send(self, ip: str, method: str, params: dict | None = None) -> None:
        """Fire and forget."""
        if not self._transport:
            return
        payload = json.dumps({"method": method, "params": params or {}}, separators=(",", ":"))
        try:
            self._transport.sendto(payload.encode(), (ip, WIZ_PORT))
        except OSError as exc:
            log.debug("send to %s failed: %s", ip, exc)

    def expect(self, ip: str, method: str) -> asyncio.Future:
        """Future resolved by the next reply from ``ip`` for ``method``."""
        assert self._proto is not None
        fut = asyncio.get_running_loop().create_future()
        self._proto.waiters[(ip, method)].append(fut)
        return fut

    def _discard(self, ip: str, method: str, fut: asyncio.Future) -> None:
        if not self._proto:
            return
        lst = self._proto.waiters.get((ip, method))
        if lst and fut in lst:
            lst.remove(fut)
            if not lst:
                self._proto.waiters.pop((ip, method), None)

    async def request(
        self,
        ip: str,
        method: str,
        params: dict | None = None,
        timeout: float = 1.2,
        attempts: int = 3,
    ) -> dict:
        """Send and wait for the reply, resending a few times (UDP can drop packets)."""
        fut = self.expect(ip, method)
        try:
            per_try = timeout / attempts
            for _ in range(attempts):
                self.send(ip, method, params)
                try:
                    msg = await asyncio.wait_for(asyncio.shield(fut), per_try)
                    break
                except asyncio.TimeoutError:
                    continue
            else:
                raise asyncio.TimeoutError(f"{ip} did not answer {method}")
        finally:
            self._discard(ip, method, fut)
        if "error" in msg:
            raise WizError(str(msg["error"]))
        return msg.get("result", {}) or {}

    def add_listener(self, cb: Listener) -> None:
        assert self._proto is not None
        self._proto.listeners.append(cb)

    def remove_listener(self, cb: Listener) -> None:
        if self._proto and cb in self._proto.listeners:
            self._proto.listeners.remove(cb)

    # ---------------------------------------------------------------- discovery
    async def discover(self, extra_networks: Iterable[str] = (), duration: float = 3.0) -> dict[str, dict]:
        """Find lights by broadcast plus a unicast sweep of local /24 networks.

        Returns {ip: {"mac": ..., "moduleName": ...}}.
        """
        found: dict[str, dict] = {}

        def on_msg(ip: str, msg: dict) -> None:
            if msg.get("method") in ("registration", "getSystemConfig", "getPilot") and isinstance(
                msg.get("result"), dict
            ):
                info = found.setdefault(ip, {})
                res = msg["result"]
                for k in ("mac", "moduleName", "fwVersion"):
                    if res.get(k):
                        info[k] = res[k]

        self.add_listener(on_msg)
        try:
            networks = local_networks()
            for net in extra_networks:
                try:
                    networks.append(ipaddress.ip_network(str(net).strip(), strict=False))
                except ValueError:
                    log.warning("ignoring invalid network %r", net)
            networks = [n for n in dict.fromkeys(networks) if n.version == 4 and n.num_addresses <= 1024]

            reg = {"phoneMac": "AAAAAAAAAAAA", "register": False, "phoneIp": "1.2.3.4", "id": "1"}
            broadcasts = {"255.255.255.255"} | {str(n.broadcast_address) for n in networks}
            log.info("discovery: broadcasting to %s, sweeping %s", sorted(broadcasts), [str(n) for n in networks])
            for _ in range(3):
                for b in broadcasts:
                    self.send(b, "registration", reg)
                await asyncio.sleep(0.15)

            # Unicast sweep - works even where routers/firewalls eat broadcasts.
            for net in networks:
                for i, host in enumerate(net.hosts()):
                    self.send(str(host), "getSystemConfig")
                    if i % 64 == 63:
                        await asyncio.sleep(0.02)
            await asyncio.sleep(duration)
        finally:
            self.remove_listener(on_msg)

        async def fill(ip: str) -> None:
            if found[ip].get("moduleName") and found[ip].get("mac"):
                return
            try:
                res = await self.request(ip, "getSystemConfig", timeout=1.5)
                for k in ("mac", "moduleName", "fwVersion"):
                    if res.get(k):
                        found[ip][k] = res[k]
            except (asyncio.TimeoutError, WizError):
                pass

        await asyncio.gather(*(fill(ip) for ip in list(found)))
        return found


def local_ipv4s() -> list[str]:
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the default route
            ips.add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(str(info[4][0]))
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith(("127.", "169.254.", "0.")))


def local_networks() -> list[ipaddress.IPv4Network]:
    return [ipaddress.ip_network(f"{ip}/24", strict=False) for ip in local_ipv4s()]  # type: ignore[misc]
