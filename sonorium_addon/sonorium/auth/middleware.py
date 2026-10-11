"""
Who is calling, and may they?  One ASGI middleware in front of every route.

Callers, in the order they're recognised:

    ingress    add-on only: the ingress port (8099), from the Supervisor's
               address. HA's sidebar: HA has signed the user in. Acts as an
               admin; X-Remote-User-Id/-Name pick (or make) the Sonorium account.
    device     "Authorization: Bearer sd_..." a linked phone: its user's role,
               never managing users, devices or keys.
    key        "Bearer sk_..." (or X-Sonorium-Token, or ?token=): only the
               abilities checked on the key (and only its channels).
    session    the sonorium_session cookie of a signed-in browser: the user's role.
    bypass     BYPASS_LOGIN / bypass_login is on: the web UI acts as an admin.
    open       Require login is off: today's open access (not users or keys).
    anonymous  nothing above: public routes only.

On the direct ports (8008/8080 and 8808) with web_ui_only_through_ha on, only
devices and keys are recognised, and web pages aren't served.

Changes (POST/PUT/DELETE) from a browser need the CSRF token (X-CSRF-Token,
from /api/auth/me) when signed in, or must not come from another site
(Origin / Sec-Fetch-Site) when no one is signed in.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, urlsplit

from starlette.responses import JSONResponse, PlainTextResponse

from sonorium.auth import config, permissions as P
from sonorium.auth.store import AuthStore, DEVICE_PREFIX, KEY_PREFIX
from sonorium.obs import logger

SESSION_COOKIE = "sonorium_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CHANNEL_ABILITIES = frozenset({"ch.view", "ch.play", "ch.change", "ch.speakers", "ch.add"})


@dataclass
class Principal:
    kind: str                      # ingress | device | key | session | bypass | open | anonymous
    perms: frozenset = frozenset()
    listener: str = "http"         # http | https | ingress
    user_id: Optional[str] = None
    role: Optional[str] = None
    session_hash: Optional[str] = None
    device_id: Optional[str] = None
    key_id: Optional[str] = None
    channels: Optional[list] = None  # a key's "Only these" channels (None = all)
    csrf: Optional[str] = None
    reset: bool = False            # signed in with a one-time code: only "Choose a new password"
    token_given: bool = False      # a bearer token came with the request
    ha_user: dict = field(default_factory=dict)

    def can(self, perm: str) -> bool:
        return perm in self.perms

    @property
    def signed_in(self) -> bool:
        return self.user_id is not None and self.kind in ("ingress", "session")

    def can_channel(self, session_id) -> bool:
        return self.channels is None or str(session_id) in self.channels


def _headers(scope) -> dict:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}


def _cookie(headers: dict, name: str) -> Optional[str]:
    for part in (headers.get("cookie") or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v.strip() or None
    return None


def bearer(headers: dict, query_string: bytes) -> Optional[str]:
    """Authorization: Bearer, X-Sonorium-Token, or ?token= (devices that can only call a URL)."""
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    if headers.get("x-sonorium-token"):
        return headers["x-sonorium-token"].strip() or None
    values = parse_qs(query_string.decode("latin-1")).get("token")
    return values[0] if values else None


class AuthService:
    """The auth store and identity, made on first use (so importing the app writes nothing)."""

    def __init__(self, folder=None):
        self._folder = folder
        self._store: Optional[AuthStore] = None
        self._identity = None
        self._ha_seen: set[str] = set()
        self.started = time.time()

    @property
    def folder(self):
        return self._folder or config.config_dir()

    @property
    def store(self) -> AuthStore:
        if self._store is None:
            self._store = AuthStore(self.folder)
        return self._store

    @property
    def identity(self):
        if self._identity is None:
            from sonorium.auth.identity import ServerIdentity
            self._identity = ServerIdentity(self.folder)
        return self._identity

    # ------------------------------------------------------------ listener

    @staticmethod
    def listener(scope) -> str:
        server = scope.get("server") or (None, None)
        ingress = config.ingress_port()
        if ingress and server and server[1] == ingress:
            return "ingress"
        return "https" if scope.get("scheme") == "https" else "http"

    # ------------------------------------------------------------- caller

    def resolve(self, scope, headers: dict) -> tuple[Principal, Optional[JSONResponse]]:
        """The caller, and the answer to give if their token is not valid (for non-public routes)."""
        store = self.store
        listener = self.listener(scope)
        if listener == "ingress":
            return self._ingress(headers), None

        token = bearer(headers, scope.get("query_string") or b"")
        if token and token.startswith(DEVICE_PREFIX):
            found = store.device(token)
            if found:
                device_id, device = found
                user = store.user(device["user_id"])
                return Principal("device", P.device_perms(user["role"]), listener, user_id=device["user_id"],
                                 role=user["role"], device_id=device_id, token_given=True), None
            return Principal("anonymous", listener=listener, token_given=True), JSONResponse(
                {"detail": "This phone is not linked to Sonorium. Link it again.", "pair": True}, status_code=401)
        if token and token.startswith(KEY_PREFIX):
            found = store.key(token)
            if found:
                key_id, key = found
                return Principal("key", frozenset(key["abilities"]), listener, key_id=key_id,
                                 channels=key.get("channels"), token_given=True), None
            return Principal("anonymous", listener=listener, token_given=True), JSONResponse(
                {"detail": "This key is not valid. It may have expired or been revoked.", "pair": True}, status_code=401)

        web_ok = not config.web_ui_only_through_ha()
        if web_ok:
            found = store.session(_cookie(headers, SESSION_COOKIE))
            if found:
                session_hash, session = found
                user = store.user(session["user_id"])
                reset = bool(session.get("reset"))
                return Principal("session", frozenset() if reset else P.role_perms(user["role"]), listener,
                                 user_id=session["user_id"], role=user["role"], session_hash=session_hash,
                                 csrf=store.csrf(session_hash), reset=reset, token_given=bool(token)), None
            if config.bypass_login():
                return Principal("bypass", P.ADMIN_PERMS - {P.SELF}, listener, role="admin", token_given=bool(token)), None
            if not store.require_login:
                return Principal("open", P.ADMIN_PERMS - P.OPEN_EXCLUDED, listener, token_given=bool(token)), None
        return Principal("anonymous", listener=listener, token_given=bool(token)), None

    def _ingress(self, headers: dict) -> Principal:
        ha = {"id": headers.get("x-remote-user-id") or "", "name": headers.get("x-remote-user-name") or "",
              "display_name": headers.get("x-remote-user-display-name") or ""}
        user_id = self.store.ha_user(ha["id"], ha["name"] or ha["display_name"]) if ha["id"] else None
        if ha["id"] and ha["id"] not in self._ha_seen:
            self._ha_seen.add(ha["id"])
            logger.info(f"Home Assistant user {ha['display_name'] or ha['name'] or ha['id']} opened Sonorium from HA's sidebar")
        if user_id:
            self.store.seen(user_id)
        return Principal("ingress", P.ADMIN_PERMS if user_id else P.ADMIN_PERMS - {P.SELF}, "ingress",
                         user_id=user_id, role="admin", csrf=self.store.sign("csrf:ingress:" + ha["id"]), ha_user=ha)


def _cross_site(headers: dict) -> bool:
    """A browser request from another site (or another port: other web apps on the same host)."""
    origin = headers.get("origin")
    if origin and origin != "null":
        netloc = urlsplit(origin).netloc.lower()
        # Behind a reverse proxy the Host header can be the proxy's target; it sends X-Forwarded-Host
        hosts = {(headers.get("host") or "").lower(), (headers.get("x-forwarded-host") or "").lower()}
        if netloc not in hosts:
            return True
    elif origin == "null":
        return True
    return headers.get("sec-fetch-site") in ("cross-site", "same-site")


class AuthMiddleware:
    def __init__(self, app, service: AuthService):
        self.app = app
        self.service = service

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path") or ""
        method = scope.get("method", "GET")
        service = self.service
        listener = service.listener(scope)
        client = (scope.get("client") or ("", 0))[0]

        if listener == "ingress" and client != config.SUPERVISOR_IP:
            logger.warning(f"Refused a request to the ingress port from {client}: only Home Assistant's Supervisor may use it")
            return await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)

        # Speakers and the browser fetch these without signing in
        if path.startswith("/stream/") or path.startswith("/static/"):
            return await self.app(scope, receive, send)

        headers = _headers(scope)
        principal, token_error = service.resolve(scope, headers)
        scope.setdefault("state", {})["auth"] = principal

        matches = P.match(method, path)
        if not matches:
            if path.startswith("/api/") or path == "/api":
                # fail closed: an API route nobody gave a permission (tests/test_auth_routes_coverage.py)
                return await JSONResponse({"detail": "Not allowed"}, status_code=403)(scope, receive, send)
            return await self.app(scope, receive, send)  # 404 as usual
        perm = matches[0][2]
        if any(m[2].page for m in matches):
            if listener != "ingress" and config.web_ui_only_through_ha():
                return await PlainTextResponse(
                    "Sonorium's web UI is only available from Home Assistant's sidebar.", status_code=404)(scope, receive, send)
            return await self.app(scope, receive, send)
        if all(m[2].public for m in matches):
            if method not in SAFE_METHODS and not principal.token_given and _cross_site(headers):
                return await JSONResponse({"detail": "Not allowed from another site"}, status_code=403)(scope, receive, send)
            return await self.app(scope, receive, send)
        guarded = [m for m in matches if not m[2].public]
        perm = guarded[0][2]
        params = guarded[0][1]

        if token_error is not None and not perm.legacy:
            return await token_error(scope, receive, send)

        if not all(m[2].allows(principal.perms) for m in guarded):
            if perm.legacy:  # the route checks the plugin framework's own token
                return await self.app(scope, receive, send)
            return await self._refuse(principal, scope, receive, send)

        if principal.channels is not None and perm.any_of & CHANNEL_ABILITIES:
            if "session_id" in params:
                if not principal.can_channel(params["session_id"]):
                    return await JSONResponse({"detail": "This key can't use this channel"}, status_code=403)(scope, receive, send)
            elif "channel_id" in params or (method != "GET" and path.startswith("/api/sessions")):
                return await JSONResponse({"detail": "This key can only use its own channels"}, status_code=403)(scope, receive, send)

        if method not in SAFE_METHODS:
            if principal.kind in ("session", "ingress"):
                if headers.get("x-csrf-token") != principal.csrf:
                    return await JSONResponse({"detail": "Reload the page and try again", "csrf": True},
                                              status_code=403)(scope, receive, send)
            elif principal.kind in ("open", "bypass") and _cross_site(headers):
                return await JSONResponse({"detail": "Not allowed from another site"}, status_code=403)(scope, receive, send)
            if principal.kind == "ingress":
                logger.debug(f"{method} {path} by Home Assistant user {principal.ha_user.get('name') or principal.ha_user.get('id')}")
        return await self.app(scope, receive, send)

    async def _refuse(self, principal: Principal, scope, receive, send):
        if principal.kind == "anonymous":
            store = self.service.store
            if principal.token_given:
                body = {"detail": "A valid key or linked phone is needed", "pair": True}
            elif config.web_ui_only_through_ha():
                body = {"detail": "A key or a linked phone is needed"}
            elif store.require_login and not store.has_users():
                body = {"detail": "Create the admin account first", "setup": True}
            else:
                body = {"detail": "Sign in first", "login": True}
            return await JSONResponse(body, status_code=401)(scope, receive, send)
        if principal.reset:
            return await JSONResponse({"detail": "Choose a new password first", "reset": True}, status_code=403)(scope, receive, send)
        return await JSONResponse({"detail": "Only an admin can do this" if principal.role == "user" else "Not allowed"},
                                  status_code=403)(scope, receive, send)


def principal_of(request) -> Principal:
    """The caller of a request (set by the middleware)."""
    p = getattr(request.state, "auth", None)
    return p if isinstance(p, Principal) else Principal("anonymous")
