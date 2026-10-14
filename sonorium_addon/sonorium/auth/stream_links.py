"""
Who may play an audio stream (/stream/...) while Require login is on.

Speakers can't sign in, so the stream addresses Sonorium hands out (to
speakers, Cast devices, the app) carry a stream link: ?t=<expiry>.<signature>,
signed with the server's secret for that path, working for
stream_link_hours (Settings > Advanced; 0: no expiry). A stream is played
when any of these holds:

    - Require login is off (as before);
    - the address it carries has a valid stream link for its path;
    - the caller is signed in (a browser session, a linked phone, a key);
    - the caller is on the LAN (a private or loopback address) and
      lan_streams_open is on (the default): speakers that were given a plain
      address keep playing. Behind a reverse proxy, set the trusted proxies
      (auth/proxies.py), or every caller looks like the proxy.

/stream/virtual/<secret> (a virtual speaker's stream) is its own secret and
isn't checked here.

Open streams are counted per address and in all (streams_per_address,
streams_limit): past either, 429.
"""
from __future__ import annotations

import hmac
import ipaddress
import time
from typing import Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sonorium import limits

PARAM = "t"
OWN_SECRET_PREFIXES = ("/stream/virtual/",)
_service = None
_clock: Callable[[], float] = time.time

STREAMS = limits.Slots()


def bind(service, clock: Callable[[], float] = time.time) -> None:
    """The sign-in service whose secret signs stream links (None: no links)."""
    global _service, _clock
    _service, _clock = service, clock


def required() -> bool:
    """Streams need a link (or a signed-in caller, or the LAN): Require login is on."""
    try:
        return _service is not None and bool(_service.login_required())
    except Exception:
        return False


def _signature(path: str, expires: int) -> str:
    return _service.sign(f"stream:{path}:{expires}")[:32]


def token(path: str) -> str:
    """A stream link for `path` (no query): "<expiry, hex>.<signature>"; expiry 0 never ends."""
    hours = limits.value(limits.STREAM_LINK_HOURS)
    expires = int(_clock() + hours * 3600) if hours > 0 else 0
    return f"{expires:x}.{_signature(path, expires)}"


def valid(path: str, value: Optional[str]) -> bool:
    if _service is None or not value or "." not in value:
        return False
    stamp, _, signature = value.partition(".")
    try:
        expires = int(stamp, 16)
    except ValueError:
        return False
    if expires and expires < _clock():
        return False
    return hmac.compare_digest(signature, _signature(path, expires))


def signed(url: str) -> str:
    """
    A stream address (a full URL or a path, any query kept) with its stream
    link added while links are needed; unchanged otherwise. An address on
    the LAN gets none while lan_streams_open is on (it plays without one,
    and some speakers take an address only as it is, without a query).
    """
    if not url or not required():
        return url
    parts = urlsplit(url)
    if not parts.path.startswith("/stream/") or parts.path.startswith(OWN_SECRET_PREFIXES):
        return url
    if parts.hostname and on_lan(parts.hostname) and limits.value(limits.LAN_STREAMS_OPEN) > 0:
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != PARAM]
    query.append((PARAM, token(parts.path)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def on_lan(address: str) -> bool:
    try:
        ip = ipaddress.ip_address((address or "").strip("[]"))
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback or ip.is_link_local


def allowed_without_sign_in(path: str, query_string: bytes, client: str) -> bool:
    """A stream link for this path, or a LAN caller while lan_streams_open is on."""
    values = [v for k, v in parse_qsl(query_string.decode("latin-1"), keep_blank_values=True) if k == PARAM]
    if values and valid(path, values[-1]):
        return True
    return limits.value(limits.LAN_STREAMS_OPEN) > 0 and on_lan(client)
