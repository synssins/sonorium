"""
Sign-in, Users & devices, Keys, Link App and pairing (docs/AUTH.md).

    GET  /api/info                       public: server id, version, ports, login required
    GET  /api/auth/me                    public: who you are, CSRF token, first run, bypass
    POST /api/auth/setup                 public, only while there are no accounts
    POST /api/auth/login                 public, 5 failed tries a minute per address
    POST /api/auth/logout
    POST /api/auth/code-login            public: username + one-time code from the app
    POST /api/auth/new-password          after a code sign-in (or your own, signed in)
    PUT  /api/auth/require-login         admin
    POST /api/link                       a Link App QR code for yourself
    POST /api/pair                       public, HTTPS: one-time code -> device token
    POST /api/pair/login                 public, HTTPS: username + password -> device token
    POST /api/devices/recovery-code      a linked phone: one-time sign-in code for its user
    DELETE /api/devices/{id}             a phone or browser of yours (or anyone's, admin)
    GET|POST /api/users, PUT|DELETE /api/users/{id}, POST /api/users/{id}/password|sign-out
    GET|POST /api/keys, PUT|DELETE /api/keys/{id}
"""
from __future__ import annotations

import asyncio
from typing import Callable, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from sonorium import runtime
from sonorium.auth import config, permissions as P
from sonorium.auth.middleware import SESSION_COOKIE, AuthService, principal_of
from sonorium.auth.passwords import hash_password, needs_rehash, password_problem, verify_password
from sonorium.auth.store import SESSION_DAYS, AuthError
from sonorium.obs import logger
from sonorium.plugins.triggers import RateLimiter

API_VERSION = 1
EXPIRY_CHOICES = {"7d": 7, "30d": 30, "90d": 90, "1y": 365, "never": None}

# 5 failed sign-ins a minute per address; pairing: 5 attempts a minute per address
LOGIN_RATE, LOGIN_BURST = 5 / 60, 5
PAIR_RATE, PAIR_BURST = 5 / 60, 5
CODE_RATE, CODE_BURST = 1 / 60, 3  # sign-in codes a phone may ask for


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def browser_name(user_agent: str) -> str:
    """"Chrome on Windows" from a User-Agent."""
    ua = user_agent or ""
    browser = ("Edge" if "Edg/" in ua else "Opera" if "OPR/" in ua else "Firefox" if "Firefox/" in ua
               else "Chrome" if ("Chrome/" in ua or "CriOS/" in ua) else "Safari" if "Safari/" in ua else "Browser")
    system = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android" if "Android" in ua
              else "Windows" if "Windows" in ua else "Mac" if "Macintosh" in ua else "Linux" if "Linux" in ua else "")
    return f"{browser} on {system}" if system else browser


def create_auth_router(service: AuthService, *, stream_url: Callable[[], str],
                       features: Callable[[], dict], version: str,
                       https_host_port: Callable[[], int]) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["auth"])
    login_limiter = RateLimiter(LOGIN_RATE, LOGIN_BURST)
    pair_limiter = RateLimiter(PAIR_RATE, PAIR_BURST)
    code_limiter = RateLimiter(CODE_RATE, CODE_BURST)

    def store():
        return service.store

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, "Invalid JSON body")
        if not isinstance(data, dict):
            raise HTTPException(400, "Expected a JSON object")
        return data

    def fail(e: AuthError):
        raise HTTPException(e.status, str(e))

    def set_cookie(response, request: Request, value: str, reset: bool = False):
        response.set_cookie(SESSION_COOKIE, value, max_age=600 if reset else SESSION_DAYS * 86400, path="/",
                            httponly=True, samesite="strict", secure=request.url.scheme == "https")

    def clear_cookie(response, request: Request):
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict",
                               secure=request.url.scheme == "https")

    def limited(limiter: RateLimiter, key: str, spend: bool):
        blocked, wait = limiter.blocked(key)
        if not blocked and spend:
            allowed, wait = limiter.allow(key)
            blocked = not allowed
        if blocked:
            raise HTTPException(429, "Too many attempts. Try again in a minute.",
                                headers={"Retry-After": str(int(wait) + 1)})

    def user_brief(user_id: Optional[str]) -> Optional[dict]:
        user = store().user(user_id)
        if user is None:
            return None
        return {"id": user_id, "username": user["username"], "role": user["role"],
                "role_label": P.ROLE_LABELS.get(user["role"], user["role"])}

    def link_address() -> tuple[str, int]:
        url = urlsplit(stream_url() or "")
        return url.hostname or "", url.port or (443 if url.scheme == "https" else 8008)

    def https_ok(request: Request) -> None:
        """Pairing hands out a token: only over HTTPS, unless the HTTPS port is switched off."""
        if request.url.scheme != "https" and https_host_port() and service.identity.tls_ready:
            raise HTTPException(403, "Use the HTTPS port to link a phone.")

    # ------------------------------------------------------------ public info

    @router.get("/info")
    async def info():
        """Public: what this server is, for the app (works before pairing)."""
        host, http = link_address()
        identity = service.identity
        https = https_host_port() if identity.tls_ready else 0
        return {
            "id": identity.server_id, "name": "Sonorium", "version": version, "api": API_VERSION,
            "install": runtime.INSTALL, "features": features(),
            "ports": {"http": http, "https": https or None}, "host": host,
            "login_required": store().require_login and not config.bypass_login(),
            "pair": True, "fingerprint": identity.fingerprint,
        }

    @router.get("/auth/me")
    async def me(request: Request):
        p = principal_of(request)
        s = store()
        ingress = p.listener == "ingress"
        return {
            "kind": p.kind, "user": user_brief(p.user_id), "csrf": p.csrf, "reset": p.reset,
            "perms": sorted(p.perms), "require_login": s.require_login, "has_users": s.has_users(),
            "needs_setup": not ingress and not s.has_users() and s.require_login and p.kind == "anonymous",
            "bypass": config.bypass_login() and not ingress, "ingress": ingress,
            "web_ui": ingress or not config.web_ui_only_through_ha(),
            "words": service.identity.words,
        }

    # ------------------------------------------------------------ sign-in

    @router.post("/auth/setup")
    async def setup(request: Request):
        """The first account (an admin), while there are none."""
        if config.web_ui_only_through_ha() and principal_of(request).listener != "ingress":
            raise HTTPException(404, "Not available")
        data = await body(request)
        client = _client(request)
        limited(login_limiter, client, spend=False)
        if store().has_users():
            raise HTTPException(409, "There is already an admin account. Sign in.")
        problem = password_problem(data.get("password"))
        if problem:
            raise HTTPException(400, problem)
        hashed = await asyncio.to_thread(hash_password, data["password"])
        try:
            user_id = store().add_user(data.get("username", ""), hashed, "admin")
        except AuthError as e:
            fail(e)
        logger.info(f"Sign-in: admin account {store().user(user_id)['username']} made from {client}")
        response = JSONResponse({"user": user_brief(user_id)})
        set_cookie(response, request, store().new_session(user_id, request.headers.get("user-agent", "")))
        return response

    @router.post("/auth/login")
    async def login(request: Request):
        if config.web_ui_only_through_ha() and principal_of(request).listener != "ingress":
            raise HTTPException(404, "Not available")
        data = await body(request)
        client = _client(request)
        limited(login_limiter, client, spend=False)
        username, password = str(data.get("username") or ""), str(data.get("password") or "")
        found = store().find_user(username)
        ok = await asyncio.to_thread(verify_password, found[1].get("password") if found else None, password)
        if not ok:
            login_limiter.allow(client)
            logger.warning(f"Sign-in failed for '{username[:40]}' from {client}")
            raise HTTPException(401, "Wrong username or password.")
        user_id, user = found
        if needs_rehash(user["password"]):
            user["password"] = await asyncio.to_thread(hash_password, password)
            store().save()
        logger.info(f"Sign-in: {user['username']} signed in from {client}")
        response = JSONResponse({"user": user_brief(user_id)})
        set_cookie(response, request, store().new_session(user_id, request.headers.get("user-agent", "")))
        return response

    @router.post("/auth/logout")
    async def logout(request: Request):
        p = principal_of(request)
        if p.kind == "session" and p.session_hash:
            store().end_session(p.session_hash)
        response = JSONResponse({"signed_out": True})
        clear_cookie(response, request)
        return response

    @router.post("/auth/code-login")
    async def code_login(request: Request):
        """Username + the one-time code a linked phone shows: then choose a new password."""
        if config.web_ui_only_through_ha() and principal_of(request).listener != "ingress":
            raise HTTPException(404, "Not available")
        data = await body(request)
        client = _client(request)
        limited(login_limiter, client, spend=False)
        user_id = store().use_signin_code(str(data.get("username") or ""), str(data.get("code") or ""))
        if user_id is None:
            login_limiter.allow(client)
            logger.warning(f"Sign-in with a one-time code failed for '{str(data.get('username'))[:40]}' from {client}")
            raise HTTPException(401, "That code didn't work.")
        logger.info(f"Sign-in: {store().user(user_id)['username']} signed in with a one-time code from {client}")
        response = JSONResponse({"reset": True, "user": user_brief(user_id)})
        set_cookie(response, request, store().new_session(user_id, request.headers.get("user-agent", ""), reset=True),
                   reset=True)
        return response

    @router.post("/auth/new-password")
    async def new_password(request: Request):
        """Your own new password (after a code sign-in, or signed in). Linked phones stay linked."""
        p = principal_of(request)
        if p.kind != "session" or not p.user_id:
            raise HTTPException(401, "Sign in first")
        if request.headers.get("x-csrf-token") != p.csrf:
            raise HTTPException(403, "Reload the page and try again")
        data = await body(request)
        problem = password_problem(data.get("password"))
        if problem:
            raise HTTPException(400, problem)
        hashed = await asyncio.to_thread(hash_password, data["password"])
        s = store()
        s.set_password(p.user_id, hashed)  # signs out every browser of the user, this one too
        logger.info(f"Sign-in: {s.user(p.user_id)['username']} chose a new password")
        response = JSONResponse({"user": user_brief(p.user_id)})
        set_cookie(response, request, s.new_session(p.user_id, request.headers.get("user-agent", "")))
        return response

    @router.put("/auth/require-login")
    async def require_login(request: Request):
        data = await body(request)
        try:
            store().set_require_login(bool(data.get("on")))
        except AuthError as e:
            fail(e)
        logger.info(f"Sign-in: Require login is {'on' if store().require_login else 'off'}")
        return {"require_login": store().require_login}

    # ------------------------------------------------------------ Link App / pairing

    @router.post("/link")
    async def link(request: Request):
        """A Link App QR code: links the app to you. Works once, for 5 minutes."""
        from sonorium.auth.qr import pair_uri, qr_svg
        p = principal_of(request)
        user = store().user(p.user_id)
        if user is None:
            raise HTTPException(409, "Sign in first")
        identity = service.identity
        code, expires = store().new_pair_code(p.user_id)
        host, http = link_address()
        https = https_host_port() if identity.tls_ready else 0
        uri = pair_uri(server_id=identity.server_id, name="Sonorium", hosts=[host] if host else [],
                       http_port=http, https_port=https, fingerprint=identity.fingerprint or "",
                       code=code, username=user["username"])
        logger.info(f"Link App: a code for {user['username']} (works once, for 5 minutes)")
        return {"uri": uri, "qr_svg": qr_svg(uri), "code": code, "username": user["username"],
                "expires_in": int(expires - store().clock()), "words": identity.words,
                "host": host, "http_port": http, "https_port": https or None}

    @router.post("/pair")
    async def pair(request: Request):
        """The app: the QR code's one-time code -> a device token for that user (shown once)."""
        client = _client(request)
        limited(pair_limiter, client, spend=True)
        https_ok(request)
        data = await body(request)
        user_id = store().use_pair_code(str(data.get("code") or ""))
        if user_id is None:
            logger.warning(f"Link App: a phone at {client} tried a wrong or expired code")
            raise HTTPException(401, "This code doesn't work. Show a new code in Sonorium.")
        device_id, token = store().add_device(user_id, str(data.get("device_name") or ""),
                                              str(data.get("platform") or ""), str(data.get("model") or ""))
        user = store().user(user_id)
        logger.info(f"Link App: {data.get('device_name') or 'a phone'} at {client} is linked to {user['username']}")
        return {"device_id": device_id, "token": token, "user": user_brief(user_id),
                "server_id": service.identity.server_id}

    @router.post("/pair/login")
    async def pair_login(request: Request):
        """The app without the QR code: username + password -> a device token (shown once)."""
        client = _client(request)
        limited(pair_limiter, client, spend=True)
        limited(login_limiter, client, spend=False)
        https_ok(request)
        data = await body(request)
        username, password = str(data.get("username") or ""), str(data.get("password") or "")
        found = store().find_user(username)
        ok = await asyncio.to_thread(verify_password, found[1].get("password") if found else None, password)
        if not ok:
            login_limiter.allow(client)
            logger.warning(f"Link App: sign-in failed for '{username[:40]}' from {client}")
            raise HTTPException(401, "Wrong username or password.")
        user_id = found[0]
        device_id, token = store().add_device(user_id, str(data.get("device_name") or ""),
                                              str(data.get("platform") or ""), str(data.get("model") or ""))
        logger.info(f"Link App: {data.get('device_name') or 'a phone'} at {client} signed in as {found[1]['username']}")
        return {"device_id": device_id, "token": token, "user": user_brief(user_id),
                "server_id": service.identity.server_id}

    @router.post("/devices/recovery-code")
    async def recovery_code(request: Request):
        """A linked phone: a 6-digit code to sign its user in to the web UI (once, 5 minutes)."""
        p = principal_of(request)
        if p.kind != "device":
            raise HTTPException(403, "Only a linked phone can ask for a sign-in code")
        limited(code_limiter, p.device_id, spend=True)
        code, expires = store().new_signin_code(p.user_id)
        logger.info(f"Sign-in: a one-time code for {store().user(p.user_id)['username']} was shown on a linked phone")
        return {"code": code, "expires_in": int(expires - store().clock()),
                "username": store().user(p.user_id)["username"]}

    @router.delete("/devices/{device_id}")
    async def remove_device(device_id: str, request: Request):
        """Remove a linked phone or sign out a browser: yours, or anyone's for an admin."""
        p = principal_of(request)
        owner = store().owner_of(device_id)
        if owner is None:
            raise HTTPException(404, "No such device")
        if owner != p.user_id and not p.can("admin.users"):
            raise HTTPException(403, "Not allowed")
        entry = store().remove_device(device_id)
        logger.info(f"Users & devices: {entry.get('name') or 'a browser'} of {store().user(owner)['username']} removed")
        response = JSONResponse({"removed": True, "this_browser": device_id == p.session_hash})
        if device_id == p.session_hash:
            clear_cookie(response, request)
        return response

    # ------------------------------------------------------------ users

    def device_rows(user_id: str, current_session: Optional[str]) -> list[dict]:
        s = store()
        rows = []
        for device_id, d in s.data["devices"].items():
            if d["user_id"] == user_id:
                rows.append({"id": device_id, "kind": "phone", "name": d["name"], "platform": d.get("platform", ""),
                             "model": d.get("model", ""), "created": d.get("created"), "last_seen": d.get("last_seen")})
        for h, sess in s.data["sessions"].items():
            if sess["user_id"] == user_id and not sess.get("reset"):
                rows.append({"id": h, "kind": "browser", "name": browser_name(sess.get("user_agent", "")),
                             "created": sess.get("created"), "last_seen": sess.get("last_seen"),
                             "this": h == current_session})
        rows.sort(key=lambda r: (r["kind"] != "phone", r.get("created") or ""))
        return rows

    @router.get("/users")
    async def list_users(request: Request):
        p = principal_of(request)
        s = store()
        users = []
        for user_id, u in s.users.items():
            devices = device_rows(user_id, p.session_hash)
            seen = max([d.get("last_seen") or "" for d in devices] + [u.get("last_seen") or ""]) or None
            users.append({"id": user_id, "username": u["username"], "role": u["role"],
                          "role_label": P.ROLE_LABELS.get(u["role"], u["role"]), "you": user_id == p.user_id,
                          "has_password": bool(u.get("password")), "ha": bool(u.get("ha_user_id")),
                          "devices": devices, "last_seen": seen, "created": u.get("created")})
        users.sort(key=lambda u: (not u["you"], u["username"].lower()))
        return {"users": users, "require_login": s.require_login, "words": service.identity.words,
                "bypass": config.bypass_login()}

    @router.post("/users")
    async def add_user(request: Request):
        data = await body(request)
        problem = password_problem(data.get("password"))
        if problem:
            raise HTTPException(400, problem)
        role = data.get("role") if data.get("role") in P.ROLES else "user"
        hashed = await asyncio.to_thread(hash_password, data["password"])
        try:
            user_id = store().add_user(str(data.get("username") or ""), hashed, role)
        except AuthError as e:
            fail(e)
        logger.info(f"Users & devices: {store().user(user_id)['username']} added ({role})")
        return {"user": user_brief(user_id)}

    @router.put("/users/{user_id}")
    async def update_user(user_id: str, request: Request):
        data = await body(request)
        try:
            if "role" in data:
                store().set_role(user_id, str(data["role"]))
        except AuthError as e:
            fail(e)
        logger.info(f"Users & devices: {store().user(user_id)['username']} is now {store().user(user_id)['role']}")
        return {"user": user_brief(user_id)}

    @router.post("/users/{user_id}/password")
    async def reset_password(user_id: str, request: Request):
        data = await body(request)
        problem = password_problem(data.get("password"))
        if problem:
            raise HTTPException(400, problem)
        if store().user(user_id) is None:
            raise HTTPException(404, "No such user.")
        hashed = await asyncio.to_thread(hash_password, data["password"])
        p = principal_of(request)
        store().set_password(user_id, hashed, keep_session=p.session_hash if user_id == p.user_id else None)
        logger.info(f"Users & devices: password of {store().user(user_id)['username']} reset")
        return {"user": user_brief(user_id)}

    @router.post("/users/{user_id}/sign-out")
    async def sign_out_everywhere(user_id: str, request: Request):
        if store().user(user_id) is None:
            raise HTTPException(404, "No such user.")
        p = principal_of(request)
        count = store().sign_out_everywhere(user_id)
        logger.info(f"Users & devices: {store().user(user_id)['username']} signed out of every device ({count})")
        response = JSONResponse({"signed_out": count, "this_browser": user_id == p.user_id and p.kind == "session"})
        if user_id == p.user_id and p.kind == "session":
            clear_cookie(response, request)
        return response

    @router.delete("/users/{user_id}")
    async def remove_user(user_id: str, request: Request):
        p = principal_of(request)
        if user_id == p.user_id:
            raise HTTPException(409, "You can't remove your own account.")
        user = store().user(user_id)
        try:
            store().remove_user(user_id)
        except AuthError as e:
            fail(e)
        logger.info(f"Users & devices: {user['username']} removed")
        return {"removed": True}

    # ------------------------------------------------------------ keys

    def expiry(choice) -> Optional[float]:
        if choice not in EXPIRY_CHOICES:
            raise HTTPException(400, "Unknown expiry")
        days = EXPIRY_CHOICES[choice]
        return None if days is None else store().clock() + days * 86400

    def key_row(key_id: str, k: dict) -> dict:
        maker = store().user(k.get("created_by"))
        return {"id": key_id, "name": k["name"], "abilities": k["abilities"], "chips": P.ability_chips(k["abilities"]),
                "channels": k.get("channels"), "expires": k.get("expires"), "created": k.get("created"),
                "created_by": maker["username"] if maker else None, "last_used": k.get("last_used")}

    def channels_value(data: dict):
        channels = data.get("channels")
        if channels is not None and not isinstance(channels, list):
            raise HTTPException(400, "channels: a list of channel ids, or null for all channels")
        return channels

    @router.get("/keys")
    async def list_keys():
        s = store()
        rows = [key_row(i, k) for i, k in s.keys.items()]
        rows.sort(key=lambda r: r.get("created") or "")
        return {"keys": rows, "areas": P.catalogue(), "expiry": list(EXPIRY_CHOICES)}

    @router.post("/keys")
    async def make_key(request: Request):
        """A new key; its value is in this answer only."""
        data = await body(request)
        p = principal_of(request)
        try:
            key_id, token = store().add_key(str(data.get("name") or ""), data.get("abilities") or [],
                                            channels_value(data), expiry(data.get("expires", "90d")), p.user_id)
        except AuthError as e:
            fail(e)
        logger.info(f"Keys: {store().keys[key_id]['name']} made")
        return {"key": token, **key_row(key_id, store().keys[key_id])}

    @router.put("/keys/{key_id}")
    async def edit_key(key_id: str, request: Request):
        """Change what a key may do, its name or expiry; takes effect on its next request."""
        data = await body(request)
        changes = {}
        if "name" in data:
            changes["name"] = str(data["name"])
        if "abilities" in data:
            changes["abilities"] = data["abilities"] or []
        if "channels" in data:
            changes["channels"] = channels_value(data)
        if "expires" in data and data["expires"] != "keep":
            changes["expires"] = expiry(data["expires"])
        try:
            key = store().update_key(key_id, **changes)
        except AuthError as e:
            fail(e)
        logger.info(f"Keys: {key['name']} changed")
        return key_row(key_id, key)

    @router.delete("/keys/{key_id}")
    async def revoke_key(key_id: str):
        key = store().keys.get(key_id)
        if not store().revoke_key(key_id):
            raise HTTPException(404, "No such key.")
        logger.info(f"Keys: {key['name']} revoked")
        return {"revoked": True}

    return router
