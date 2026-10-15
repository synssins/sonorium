"""
Scopes: what an account or key may see, on top of what its abilities allow
(sonorium/auth/permissions.py: SCOPES, and ROLES for which roles may carry
which). A caller without a scope sees everything; with one, only what the
scope lists. Something outside a caller's scope is hidden, not refused: it is
missing from every listing, and a request for it answers 404.

    channels    only these channels (a key's "Only these", a Listener's channels)

Enforced in two places only:
    - the middleware (auth/middleware.py), for every route with a path
      parameter a scope checks (permissions.SCOPE_PARAMS);
    - listings and event streams, through the helpers here.
"""
from __future__ import annotations

from typing import Callable, Iterable, Optional

from sonorium.auth.principal import current_caller


def scopes_of(principal) -> dict:
    """The scopes a caller carries: {"channels": [ids]}; empty: no limits."""
    if principal is None:
        return {}
    out = {}
    if getattr(principal, "channels", None) is not None:
        out["channels"] = principal.channels
    return out


def allows(principal, scope: str, value) -> bool:
    """The value is inside the caller's scope (always, for a scope the caller doesn't carry)."""
    limit = scopes_of(principal).get(scope)
    return limit is None or str(value) in {str(v) for v in limit}


def channel_visible(channel_id, principal=None) -> bool:
    """The caller (by default the current request's) sees this channel."""
    return allows(principal if principal is not None else current_caller(), "channels", channel_id)


def visible_channels(items: Iterable, key: Callable = lambda s: s.id, principal=None) -> list:
    """Only the channels the caller sees, in their order."""
    p = principal if principal is not None else current_caller()
    return [item for item in items if allows(p, "channels", key(item))]


def channels_limited(principal=None) -> bool:
    p = principal if principal is not None else current_caller()
    return "channels" in scopes_of(p)


# The session manager, to tell which channel an audio stream plays (bound at startup)
_stream_session: Optional[Callable[[str], Optional[str]]] = None


def bind_streams(resolver: Optional[Callable[[str], Optional[str]]]) -> None:
    """resolver(stream path) -> the channel (session id) it plays, or None."""
    global _stream_session
    _stream_session = resolver


def stream_channel(path: str) -> Optional[str]:
    try:
        return _stream_session(path) if _stream_session is not None else None
    except Exception:
        return None
