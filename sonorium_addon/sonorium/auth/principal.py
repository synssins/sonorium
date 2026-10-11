"""
Who is calling (a Principal), and the hooks sign-in plugins provide.

An authenticator is any object a plugin registers with
AuthService.register_authenticator(plugin_id, authenticator). Every method is
optional; the core calls them in this order for each request:

    ingress(ctx, principal)           HA's sidebar (add-on): may return a Principal
                                      with the HA user's account (keep its perms)
    device(ctx, principal, record)    a linked phone: return the Principal it acts as,
                                      or a Reject (e.g. its user is gone)
    authenticate(ctx)                 a bearer token or a cookie the plugin knows:
                                      a Principal, a Reject, or None
    fallback(ctx)                     nobody recognised: a Principal (e.g. open access
                                      while Require login is off) or None
    me(ctx, principal) -> dict        extra fields for GET /api/auth/me
    user(user_id) -> dict | None      {"username", "role", "role_label"} of a user id

With no authenticator registered, a request nobody recognised is "local":
full access, which is Sonorium's behaviour without accounts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Principal:
    kind: str                      # local | ingress | device | key | session | bypass | open | anonymous | <plugin's>
    perms: frozenset = frozenset()
    listener: str = "http"         # http | https | ingress
    user_id: Optional[str] = None
    role: Optional[str] = None
    session_id: Optional[str] = None
    device_id: Optional[str] = None
    key_id: Optional[str] = None
    channels: Optional[list] = None  # only these channels (None = all)
    csrf: Optional[str] = None     # set for browsers: changes must send it as X-CSRF-Token
    reset: bool = False            # the Users plugin: signed in with a one-time code
    token_given: bool = False      # a bearer token came with the request
    ha_user: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def can(self, perm: str) -> bool:
        return perm in self.perms

    def can_channel(self, session_id) -> bool:
        return self.channels is None or str(session_id) in self.channels


@dataclass
class Reject:
    """A refusal with its answer (e.g. a revoked key: 401)."""
    status: int
    body: dict


@dataclass
class RequestContext:
    scope: dict
    headers: dict
    listener: str
    client: str
    token: Optional[str]
    web_ok: bool                   # browsers (cookies) are recognised on this port
    service: object = None

    def cookie(self, name: str) -> Optional[str]:
        for part in (self.headers.get("cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v.strip() or None
        return None
