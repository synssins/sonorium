"""
Who is calling, and may they? One ASGI middleware in front of every route.

The caller (auth/principal.py), first match wins:

    ingress    add-on only: the ingress port (8099), from the Supervisor's
               address. HA's sidebar: HA has signed the user in; full access.
               A sign-in plugin may attach the HA user's account.
    device     "Authorization: Bearer sd_..." a phone linked with Link App:
               full access less managing users, devices and keys; with the
               Users plugin, what its user may do.
    plugins    their authenticate() hook: the Users plugin's browser
               sessions, the Integration plugin's keys.
    bypass     BYPASS_LOGIN / bypass_login is on: the web UI needs no sign-in.
    fallback   a plugin's fallback() (the Users plugin: open access while
               Require login is off, else nobody), or with no sign-in plugin
               "local": full access, Sonorium's behaviour without accounts.

Then the route's permission (auth/permissions.py) decides. Routes that by
design need a token (plugin triggers, the event stream) check it themselves.

With web_ui_only_through_ha on (add-on), the direct ports recognise only
phones and keys, and serve no web pages.

Changes (POST/PUT/DELETE) from a browser must carry the caller's CSRF token
(X-CSRF-Token, from GET /api/auth/me) when it has one, and must not come from
another site (Origin / Sec-Fetch-Site) when it hasn't.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import parse_qs, urlsplit

from starlette.responses import JSONResponse, PlainTextResponse

from sonorium.auth import config, permissions as P
from sonorium.auth.principal import CALLER, Principal, Reject, RequestContext
from sonorium.auth.service import DEVICE_PREFIX, AuthService
from sonorium.obs import logger

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CHANNEL_ABILITIES = frozenset({"ch.view", "ch.play", "ch.change", "ch.speakers", "ch.add"})
PREFIXED = ("sd_", "sk_", "ss_")


def _headers(scope) -> dict:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}


def bearer(headers: dict, query_string: bytes) -> Optional[str]:
    """Authorization: Bearer, X-Sonorium-Token, or ?token= (devices that can only call a URL)."""
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    if headers.get("x-sonorium-token"):
        return headers["x-sonorium-token"].strip() or None
    values = parse_qs(query_string.decode("latin-1")).get("token")
    return values[0] if values else None


def listener_of(scope) -> str:
    server = scope.get("server") or (None, None)
    ingress = config.ingress_port()
    if ingress and server and server[1] == ingress:
        return "ingress"
    return "https" if scope.get("scheme") == "https" else "http"


def cross_site(headers: dict) -> bool:
    """A browser request from another site (or another port: other web apps on the same host)."""
    origin = headers.get("origin")
    if origin == "null":
        return True
    if origin:
        netloc = urlsplit(origin).netloc.lower()
        # Behind a reverse proxy the Host header can be the proxy's target; it sends X-Forwarded-Host
        hosts = {(headers.get("host") or "").lower(), (headers.get("x-forwarded-host") or "").lower()}
        return netloc not in hosts
    return headers.get("sec-fetch-site") in ("cross-site", "same-site")


def resolve(service: AuthService, scope, headers: dict) -> tuple[Principal, Optional[Reject]]:
    """The caller, and the refusal to give if their token isn't valid (for non-public routes)."""
    listener = listener_of(scope)
    client = (scope.get("client") or ("", 0))[0]
    token = bearer(headers, scope.get("query_string") or b"")
    web_ok = listener == "ingress" or not config.web_ui_only_through_ha()
    ctx = RequestContext(scope, headers, listener, client, token, web_ok, service)

    if listener == "ingress":
        ha = {"id": headers.get("x-remote-user-id") or "", "name": headers.get("x-remote-user-name") or "",
              "display_name": headers.get("x-remote-user-display-name") or ""}
        principal = Principal("ingress", P.ALL_PERMS, "ingress", role="admin",
                              csrf=service.sign("csrf:ingress:" + ha["id"]), ha_user=ha)
        for _, hook in service.provider("ingress"):
            principal = hook(ctx, principal) or principal
        return principal, None

    if token and token.startswith(DEVICE_PREFIX):
        found = service.tokens.verify(token, "device")
        if not found:
            return Principal("anonymous", listener=listener, token_given=True), Reject(
                401, {"detail": "This phone is not linked to Sonorium. Link it again.", "pair": True})
        device_id, record = found
        principal = Principal("device", P.device_perms(P.ALL_PERMS), listener, user_id=record.get("user_id"),
                              device_id=device_id, token_given=True)
        for _, hook in service.provider("device"):
            result = hook(ctx, principal, record)
            if isinstance(result, Reject):
                return Principal("anonymous", listener=listener, token_given=True), result
            principal = result or principal
        return principal, None

    for _, hook in service.provider("authenticate"):
        result = hook(ctx)
        if isinstance(result, Reject):
            return Principal("anonymous", listener=listener, token_given=bool(token)), result
        if result is not None and (web_ok or result.csrf is None):
            result.listener, result.token_given = listener, bool(token)
            return result, None

    if token and token.startswith(PREFIXED):
        return Principal("anonymous", listener=listener, token_given=True), Reject(
            401, {"detail": "This key is not valid. It may have expired or been revoked.", "pair": True})

    if service.closed:
        return Principal("anonymous", listener=listener, token_given=bool(token)), None
    if web_ok and config.bypass_login():
        return Principal("bypass", P.ALL_PERMS - {P.SELF}, listener, role="admin", token_given=bool(token)), None
    if not web_ok:
        return Principal("anonymous", listener=listener, token_given=bool(token)), None
    for _, hook in service.provider("fallback"):
        result = hook(ctx)
        if result is not None:
            result.listener, result.token_given = listener, bool(token)
            return result, None
    return Principal("local", P.ALL_PERMS, listener, token_given=bool(token)), None


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
        listener = listener_of(scope)
        client = (scope.get("client") or ("", 0))[0]

        async def answer(status, body):
            response = JSONResponse(body, status_code=status) if isinstance(body, dict) else PlainTextResponse(body, status_code=status)
            return await response(scope, receive, send)

        if listener == "ingress" and client != config.SUPERVISOR_IP:
            logger.warning(f"Refused a request to the ingress port from {client}: only Home Assistant's Supervisor may use it")
            return await answer(403, "Forbidden")

        # Speakers and browsers fetch these without signing in
        if path.startswith("/stream/") or path.startswith("/static/"):
            return await self.app(scope, receive, send)

        headers = _headers(scope)
        for plugin_id, prepare in service.provider("prepare"):
            try:
                await prepare(listener, headers)
            except Exception as e:
                logger.warning(f"Sign-in: plugin {plugin_id} could not get ready for a request: {e}")
        principal, reject = resolve(service, scope, headers)
        scope.setdefault("state", {})["auth"] = principal
        caller = CALLER.set(principal)  # current_caller() for the route and the tasks it starts
        try:
            return await self._checked(scope, receive, send, principal, reject, headers, method, path, listener, answer)
        finally:
            CALLER.reset(caller)

    async def _checked(self, scope, receive, send, principal, reject, headers, method, path, listener, answer):
        service = self.service

        matches = service.registry.match(method, path)
        if not matches:
            if service.registry.match(method, path, inactive=True):
                return await answer(404, {"detail": "Not Found"})  # a plugin that is switched off
            if path.startswith("/api/") or path == "/api":
                # fail closed: an API route without a permission (tests/test_auth_routes_coverage.py)
                return await answer(403, {"detail": "Not allowed"})
            return await self.app(scope, receive, send)  # 404 as usual

        if any(m[2].page for m in matches):
            if listener != "ingress" and config.web_ui_only_through_ha():
                return await answer(404, "Sonorium's web UI is only available from Home Assistant's sidebar.")
            return await self.app(scope, receive, send)
        if all(m[2].public for m in matches):
            if method not in SAFE_METHODS and not principal.token_given and cross_site(headers):
                return await answer(403, {"detail": "Not allowed from another site"})
            return await self.app(scope, receive, send)

        guarded = [m for m in matches if not m[2].public]
        perm, params = guarded[0][2], guarded[0][1]

        if reject is not None and not perm.legacy:
            return await answer(reject.status, reject.body)

        if not all(m[2].allows(principal.perms) for m in guarded):
            if perm.legacy:  # the route checks the plugin framework's own token
                return await self.app(scope, receive, send)
            return await answer(*self._refusal(principal))

        if principal.channels is not None and perm.any_of & CHANNEL_ABILITIES:
            if "session_id" in params:
                if not principal.can_channel(params["session_id"]):
                    return await answer(403, {"detail": "Not allowed for this channel"})
            elif "channel_id" in params or (method != "GET" and path.startswith("/api/sessions")):
                return await answer(403, {"detail": "Only allowed for some channels"})

        if method not in SAFE_METHODS:
            if principal.csrf is not None:
                if headers.get("x-csrf-token") != principal.csrf:
                    return await answer(403, {"detail": "Reload the page and try again", "csrf": True})
            elif not principal.token_given and cross_site(headers):
                return await answer(403, {"detail": "Not allowed from another site"})
            if principal.kind == "ingress":
                logger.debug(f"{method} {path} by Home Assistant user "
                             f"{principal.ha_user.get('name') or principal.ha_user.get('id') or '?'}")
        return await self.app(scope, receive, send)

    def _refusal(self, principal: Principal) -> tuple[int, dict]:
        service = self.service
        if principal.kind == "anonymous":
            if principal.token_given:
                return 401, {"detail": "A valid key or linked phone is needed", "pair": True}
            if not service.ready:
                return 503, {"detail": "Sonorium is starting", "starting": True}
            if service.closed:
                return 503, {"detail": "The sign-in plugin didn't start. See Logs.", "closed": True}
            if principal.listener != "ingress" and config.web_ui_only_through_ha():
                return 401, {"detail": "A key or a linked phone is needed"}
            body = {"detail": "Sign in first", "login": True}
            body.update(principal.extra.get("refusal") or {})
            return 401, body
        if principal.kind == "open":  # open access, but this needs someone signed in
            return 401, {"detail": "Sign in first", "login": True}
        if principal.reset:
            return 403, {"detail": "Choose a new password first", "reset": True}
        return 403, {"detail": "Only an admin can do this" if principal.role == "user" else "Not allowed"}


def principal_of(request) -> Principal:
    """The caller of a request (set by the middleware)."""
    p = getattr(getattr(request, "state", None), "auth", None) if request is not None else None
    return p if isinstance(p, Principal) else Principal("anonymous")
