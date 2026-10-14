"""
Behind a reverse proxy: the caller's real address.

Out of the box the caller's address is the connection's (the proxy's, when
there is one): rate limits, logs and "on the LAN" all see the proxy. With
trusted proxies set, a request from one of them is taken at its word:

    X-Forwarded-For     the caller's address (the last one no trusted proxy added)
    X-Forwarded-Proto   http or https, as the caller connected to the proxy
    X-Forwarded-Host    (read where the page's own address is needed)

Trusted proxies: SONORIUM__TRUSTED_PROXIES (Docker, the add-on's
environment), or the connection settings' "trusted_proxies" (standalone;
loaded into that variable at start-up): addresses or networks (CIDR),
separated by commas or spaces. Empty (the default): no proxy is trusted.

ForwardedClient is the app's outermost middleware: everything after it sees
the caller's address in scope["client"]; the connection's own address stays
in scope["sonorium.peer"]. HA's ingress port is never rewritten (sign-in
trusts it by the Supervisor's address).
"""
from __future__ import annotations

import ipaddress
import os
from typing import Optional

ENV = "SONORIUM__TRUSTED_PROXIES"
PEER_KEY = "sonorium.peer"

_cache: tuple[str, tuple] = ("", ())


def parse(text: str) -> tuple:
    """The networks in a list of addresses / CIDRs; ValueError naming the first that isn't one."""
    nets = []
    for part in (text or "").replace(",", " ").split():
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            raise ValueError(f"'{part}' is not an address or a network")
    return tuple(nets)


def trusted() -> tuple:
    """The trusted proxies' networks now (read from the environment, cached while it's unchanged)."""
    global _cache
    text = os.environ.get(ENV) or ""
    if text != _cache[0]:
        try:
            nets = parse(text)
        except ValueError as e:
            from sonorium.obs import logger
            logger.getChild("auth").warning(f"Trusted proxies: {e}; none trusted")
            nets = ()
        _cache = (text, nets)
    return _cache[1]


def _address(text: str):
    try:
        return ipaddress.ip_address((text or "").strip().strip("[]"))
    except ValueError:
        return None


def is_trusted(address: str, nets: Optional[tuple] = None) -> bool:
    nets = trusted() if nets is None else nets
    ip = _address(address)
    return ip is not None and any(ip in net for net in nets)


def forwarded_client(peer: str, forwarded_for: str, nets: Optional[tuple] = None) -> str:
    """
    The caller's address: X-Forwarded-For's last address no trusted proxy
    added, when `peer` is trusted; else the peer.
    """
    nets = trusted() if nets is None else nets
    if not nets or not is_trusted(peer, nets) or not forwarded_for:
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if _address(hop) is None:
            return peer  # a garbled header: the proxy's address stands
        if not is_trusted(hop, nets):
            return str(_address(hop))
    return str(_address(hops[0])) if hops else peer


class ForwardedClient:
    """Outermost middleware: a trusted proxy's request gets the caller's address and scheme."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            scope[PEER_KEY] = client[0] if client else ""
            nets = trusted()
            if nets and client and not _ingress(scope) and is_trusted(client[0], nets):
                headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
                real = forwarded_client(client[0], headers.get("x-forwarded-for", ""), nets)
                if real != client[0]:
                    scope["client"] = (real, 0)
                proto = (headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
                if proto in ("http", "https") and scope["type"] == "http":
                    scope["scheme"] = proto
        return await self.app(scope, receive, send)


def _ingress(scope) -> bool:
    from sonorium.auth import config
    port = config.ingress_port()
    server = scope.get("server") or (None, None)
    return bool(port and server and server[1] == port)


def peer_of(scope) -> str:
    """The connection's own address (the proxy's, behind one)."""
    return scope.get(PEER_KEY) or ((scope.get("client") or ("", 0))[0])
