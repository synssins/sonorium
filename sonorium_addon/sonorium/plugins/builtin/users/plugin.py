"""
Users: accounts and roles, browser sign-in, Require login, one-time sign-in
codes from a linked phone, Users & devices, and Home Assistant sidebar users.

A built-in plugin on top of the core's sign-in (sonorium/auth). Switched on
by default; switching it off or removing it makes Sonorium open to anyone on
the network again (the web UI asks first). Its data (users.json) stays, so
switching it back on brings the accounts back.

Roles: Admin (everything) and User (channels, playlists and sequences, view
themes and speakers, their own phones). A linked phone acts as its user.

Add-on: Home Assistant's users are Sonorium's users, with their role from HA
(ha.py), and sign in with their HA username and password.
Later in this plugin: zones (ownership, Kid mode, speaker homes).
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from sonorium.auth import config, permissions as P
from sonorium.auth.principal import Principal, Reject
from sonorium.auth.ratelimit import RateLimiter
from sonorium.auth.routes import client_of, json_body, limited
from sonorium.obs import logger
from sonorium.plugins.base import BasePlugin

HERE = Path(__file__).resolve().parent


def _sibling(name: str):
    """accounts.py / passwords.py next to this file (a plugin is loaded from its folder, not as a package)."""
    module_name = f"sonorium_plugin_users_{name}"
    spec = importlib.util.spec_from_file_location(module_name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


passwords = _sibling("passwords")
ha_mod = _sibling("ha")
accounts_mod = _sibling("accounts")
Accounts, AuthError = accounts_mod.Accounts, accounts_mod.AuthError

PLUGIN_ID = "users"
SESSION_COOKIE = "sonorium_session"
LOGIN_RATE, LOGIN_BURST = 5 / 60, 5   # 5 failed sign-ins a minute per address
CODE_RATE, CODE_BURST = 1 / 60, 3     # sign-in codes a phone may ask for

PERMISSIONS = """
POST    /api/auth/setup                 public
POST    /api/auth/login                 public
POST    /api/auth/logout                public
POST    /api/auth/code-login            public
POST    /api/auth/new-password          public
PUT     /api/auth/require-login         admin.users
POST    /api/pair/login                 public
POST    /api/devices/recovery-code      device
GET     /api/users                      admin.users
POST    /api/users                      admin.users
PUT     /api/users/{user_id}            admin.users
POST    /api/users/{user_id}/password   admin.users
POST    /api/users/{user_id}/sign-out   admin.users
DELETE  /api/users/{user_id}/sessions/{session_id}  admin.users
DELETE  /api/users/{user_id}            admin.users
"""


def browser_name(user_agent: str) -> str:
    """"Chrome on Windows" from a User-Agent."""
    ua = user_agent or ""
    browser = ("Edge" if "Edg/" in ua else "Opera" if "OPR/" in ua else "Firefox" if "Firefox/" in ua
               else "Chrome" if ("Chrome/" in ua or "CriOS/" in ua) else "Safari" if "Safari/" in ua else "Browser")
    system = ("iPhone" if "iPhone" in ua else "iPad" if "iPad" in ua else "Android" if "Android" in ua
              else "Windows" if "Windows" in ua else "Mac" if "Macintosh" in ua else "Linux" if "Linux" in ua else "")
    return f"{browser} on {system}" if system else browser


class UsersAuth:
    """The core's authenticator hooks (sonorium/auth/principal.py)."""

    def __init__(self, accounts: Accounts):
        self.accounts = accounts

    def login_required(self) -> bool:
        return self.accounts.require_login

    def user(self, user_id):
        return self.accounts.brief(user_id)

    def blocked(self, user_id) -> bool:
        return self.accounts.disabled(user_id)

    async def prepare(self, listener: str, headers: dict) -> None:
        """Add-on: HA's list of users when it's a minute old, or at once for an HA user Sonorium hasn't seen."""
        directory = self.accounts.ha
        if directory is None:
            return
        ha_user_id = headers.get("x-remote-user-id") if listener == "ingress" else None
        unknown = bool(ha_user_id) and not any(u.get("ha_user_id") == ha_user_id
                                               for u in self.accounts.users.values())
        await directory.refresh(force=unknown)

    def authenticate(self, ctx) -> Optional[Principal]:
        if not ctx.web_ok:
            return None
        found = self.accounts.session(ctx.cookie(SESSION_COOKIE))
        if not found:
            return None
        session_id, record = found
        user = self.accounts.user(record["user_id"])
        reset = bool((record.get("data") or {}).get("reset"))
        return Principal("session", frozenset() if reset else accounts_mod.role_perms(user["role"]),
                         user_id=record["user_id"], role=user["role"], session_id=session_id,
                         csrf=self.accounts.csrf(session_id), reset=reset)

    def fallback(self, ctx) -> Principal:
        if not self.accounts.require_login:
            return Principal("open", accounts_mod.OPEN_PERMS)
        refusal = {"setup": True} if not self.accounts.has_users() and not config.is_addon() else {}
        return Principal("anonymous", extra={"refusal": refusal})

    def ingress(self, ctx, principal: Principal) -> Principal:
        ha = principal.ha_user
        if ha.get("id"):
            principal.user_id = self.accounts.ha_user(ha["id"], ha.get("name") or ha.get("display_name") or "")
            self.accounts.seen(principal.user_id)
            user = self.accounts.user(principal.user_id)
            principal.role = user["role"]
            # The role HA gives the user; nothing while HA has the user switched off
            principal.perms = frozenset() if user.get("disabled") else accounts_mod.role_perms(user["role"])
        return principal

    def device(self, ctx, principal: Principal, record: dict):
        user_id = record.get("user_id")
        if user_id is None:
            if self.accounts.require_login:
                return Reject(401, {"detail": "Link this phone again to sign it in.", "pair": True})
            principal.perms = P.device_perms(accounts_mod.OPEN_PERMS)
            return principal
        user = self.accounts.user(user_id)
        if user is None or user.get("disabled"):
            return Reject(401, {"detail": "This phone is not linked to Sonorium. Link it again.", "pair": True})
        self.accounts.seen(user_id)
        principal.perms = P.device_perms(accounts_mod.role_perms(user["role"]))
        principal.role = user["role"]
        return principal

    def me(self, ctx, principal: Principal) -> dict:
        a = self.accounts
        return {"require_login": a.require_login, "has_users": a.has_users(), "ha_users": a.ha is not None,
                "needs_setup": (principal.kind == "anonymous" and not a.has_users() and a.require_login
                                and principal.listener != "ingress" and not config.is_addon())}


def create_router(service) -> APIRouter:
    """The plugin's API. Handlers find the current Accounts on the service, so a reloaded plugin keeps working."""
    router = APIRouter(prefix="/api", tags=["users"])
    login_limiter = RateLimiter(LOGIN_RATE, LOGIN_BURST)
    code_limiter = RateLimiter(CODE_RATE, CODE_BURST)

    def accounts() -> Accounts:
        current = service.plugin_state.get(PLUGIN_ID)
        if current is None:
            raise HTTPException(404, "Not Found")
        return current

    def p_of(request: Request):
        from sonorium.auth.middleware import principal_of
        return principal_of(request)

    def fail(e: AuthError):
        raise HTTPException(e.status, str(e))

    def web_only(request: Request):
        if config.web_ui_only_through_ha() and p_of(request).listener != "ingress":
            raise HTTPException(404, "Not available")

    def set_cookie(response, request: Request, value: str, reset: bool = False):
        response.set_cookie(SESSION_COOKIE, value, max_age=600 if reset else accounts_mod.SESSION_DAYS * 86400,
                            path="/", httponly=True, samesite="strict", secure=request.url.scheme == "https")

    def clear_cookie(response, request: Request):
        response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict",
                               secure=request.url.scheme == "https")

    def signed_in(request: Request, user_id: str, *, reset: bool = False, body: Optional[dict] = None):
        response = JSONResponse(body or {"user": accounts().brief(user_id)})
        set_cookie(response, request, accounts().new_session(user_id, request.headers.get("user-agent", ""), reset),
                   reset=reset)
        return response

    def wrong(username: str, client: str, what: str):
        login_limiter.allow(client)
        logger.warning(f"{what} failed for '{username[:40]}' from {client}")
        raise HTTPException(401, "Wrong username or password.")

    async def check_ha_password(entry: dict, username: str, password: str, client: str, what: str):
        """Add-on: an HA user's username and password, checked by Home Assistant (never kept or logged)."""
        a = accounts()
        a.warn_shadowed(username)
        if not entry.get("is_active", True) or not password:
            wrong(username, client, what)
        try:
            ok = await a.ha.check_password(entry["username"], password)
        except ha_mod.HAUnavailable as e:
            logger.warning(f"{what}: Home Assistant didn't answer ({e})")
            raise HTTPException(503, "Home Assistant didn't answer. Try again in a moment.")
        if not ok:
            wrong(username, client, what)
        user_id = a.ha_user(entry["id"], entry.get("username") or "")
        a.apply_ha(user_id)
        if a.disabled(user_id):
            wrong(username, client, what)
        a.save()
        return user_id, a.user(user_id)

    async def check_password(username: str, password: str, client: str, what: str):
        limited(login_limiter, client, spend=False)
        a = accounts()
        if a.ha is not None:  # add-on: Home Assistant's users first
            await a.ha.refresh()
            entry = a.ha.by_username(username)
            if entry is not None:
                return await check_ha_password(entry, username, password, client, what)
        found = a.find(username)
        stored = found[1].get("password") if found and not found[1].get("ha") and not found[1].get("disabled") else None
        ok = await asyncio.to_thread(passwords.verify_password, stored, password)
        if not ok or not stored:
            wrong(username, client, what)
        user_id, user = found
        if passwords.needs_rehash(user["password"]):
            user["password"] = await asyncio.to_thread(passwords.hash_password, password)
            accounts().save()
        return user_id, user

    async def new_hash(data: dict) -> str:
        problem = passwords.password_problem(data.get("password"))
        if problem:
            raise HTTPException(400, problem)
        return await asyncio.to_thread(passwords.hash_password, data["password"])

    # ------------------------------------------------------------ sign-in

    @router.post("/auth/setup")
    async def setup(request: Request):
        """The first account (an admin), while there are none."""
        web_only(request)
        data = await json_body(request)
        client = client_of(request)
        limited(login_limiter, client, spend=False)
        if config.is_addon():
            raise HTTPException(409, "Sign in with your Home Assistant username and password.")
        if accounts().has_users():
            raise HTTPException(409, "There is already an admin account. Sign in.")
        hashed = await new_hash(data)
        try:
            user_id = accounts().add(str(data.get("username") or ""), hashed, "admin")
        except AuthError as e:
            fail(e)
        logger.info(f"Users: admin account {accounts().user(user_id)['username']} made from {client}")
        return signed_in(request, user_id)

    @router.post("/auth/login")
    async def login(request: Request):
        web_only(request)
        data = await json_body(request)
        client = client_of(request)
        user_id, user = await check_password(str(data.get("username") or ""), str(data.get("password") or ""),
                                             client, "Sign-in")
        logger.info(f"Users: {user['username']} signed in from {client}")
        return signed_in(request, user_id)

    @router.post("/auth/logout")
    async def logout(request: Request):
        p = p_of(request)
        if p.kind == "session" and p.session_id:
            accounts().end_session(p.session_id)
        response = JSONResponse({"signed_out": True})
        clear_cookie(response, request)
        return response

    @router.post("/auth/code-login")
    async def code_login(request: Request):
        """Username + the one-time code a linked phone shows: then choose a new password."""
        web_only(request)
        data = await json_body(request)
        client = client_of(request)
        limited(login_limiter, client, spend=False)
        user_id = accounts().use_signin_code(str(data.get("username") or ""), str(data.get("code") or ""))
        if user_id is None:
            login_limiter.allow(client)
            logger.warning(f"Sign-in with a one-time code failed for '{str(data.get('username'))[:40]}' from {client}")
            raise HTTPException(401, "That code didn't work.")
        logger.info(f"Users: {accounts().user(user_id)['username']} signed in with a one-time code from {client}")
        return signed_in(request, user_id, reset=True, body={"reset": True, "user": accounts().brief(user_id)})

    @router.post("/auth/new-password")
    async def new_password(request: Request):
        """Your own new password (after a code sign-in, or signed in). Linked phones stay linked."""
        p = p_of(request)
        if p.kind != "session" or not p.user_id:
            raise HTTPException(401, "Sign in first")
        if request.headers.get("x-csrf-token") != p.csrf:
            raise HTTPException(403, "Reload the page and try again")
        hashed = await new_hash(await json_body(request))
        try:
            accounts().set_password(p.user_id, hashed)  # signs out every browser of the user, this one too
        except AuthError as e:
            fail(e)
        logger.info(f"Users: {accounts().user(p.user_id)['username']} chose a new password")
        return signed_in(request, p.user_id)

    @router.put("/auth/require-login")
    async def require_login(request: Request):
        data = await json_body(request)
        try:
            accounts().set_require_login(bool(data.get("on")))
        except AuthError as e:
            fail(e)
        logger.info(f"Users: Require login is {'on' if accounts().require_login else 'off'}")
        return {"require_login": accounts().require_login}

    # ------------------------------------------------------------ phones

    @router.post("/pair/login")
    async def pair_login(request: Request):
        """The app without the QR code: username + password -> a phone token (shown once)."""
        client = client_of(request)
        service.https_only(request)
        data = await json_body(request)
        user_id, user = await check_password(str(data.get("username") or ""), str(data.get("password") or ""),
                                             client, "Link App sign-in")
        device_id, token = service.add_device(user_id, str(data.get("device_name") or ""),
                                              str(data.get("platform") or ""), str(data.get("model") or ""))
        logger.info(f"Link App: {data.get('device_name') or 'a phone'} at {client} signed in as {user['username']}")
        return {"device_id": device_id, "token": token, "user": accounts().brief(user_id),
                "server_id": service.identity.server_id, "words": service.identity.words}

    @router.post("/devices/recovery-code")
    async def recovery_code(request: Request):
        """A linked phone: a 6-digit code to sign its user in to the web UI (once, for 5 minutes)."""
        p = p_of(request)
        if p.kind != "device" or not p.user_id:
            raise HTTPException(403, "Only a phone linked to a user can show a sign-in code")
        limited(code_limiter, p.device_id, spend=True)
        try:
            code, expires = accounts().new_signin_code(p.user_id)
        except AuthError as e:
            fail(e)
        username = accounts().user(p.user_id)["username"]
        logger.info(f"Users: a one-time sign-in code for {username} was shown on a linked phone")
        return {"code": code, "expires_in": int(expires - service.clock()), "username": username}

    # ------------------------------------------------------------ Users & devices

    def device_rows(user_id: str, current_session: Optional[str]) -> list[dict]:
        rows = []
        for d in service.devices(user_id):
            rows.append({**d, "kind": "phone"})
        for session_id, r in accounts().sessions(user_id):
            rows.append({"id": session_id, "kind": "browser",
                         "name": browser_name((r.get("data") or {}).get("user_agent", "")),
                         "created": r.get("created"), "last_seen": r.get("last_used") or r.get("created"),
                         "this": session_id == current_session})
        return rows

    @router.get("/users")
    async def list_users(request: Request):
        p = p_of(request)
        a = accounts()
        users = []
        for user_id, u in a.users.items():
            devices = device_rows(user_id, p.session_id)
            seen = max([d.get("last_seen") or "" for d in devices] + [u.get("last_seen") or ""]) or None
            users.append({"id": user_id, "username": u["username"], "role": u["role"],
                          "role_label": accounts_mod.ROLE_LABELS.get(u["role"], u["role"]), "you": user_id == p.user_id,
                          "has_password": bool(u.get("password")), "ha": bool(u.get("ha")),
                          "disabled": bool(u.get("disabled")),
                          "devices": devices, "last_seen": seen, "created": u.get("created")})
        users.sort(key=lambda u: (not u["you"], u["username"].lower()))
        unbound = [d for d in service.devices(None)]
        return {"users": users, "unlinked_devices": unbound, "require_login": a.require_login,
                "words": service.identity.words, "bypass": config.bypass_login()}

    @router.post("/users")
    async def add_user(request: Request):
        data = await json_body(request)
        role = data.get("role") if data.get("role") in accounts_mod.ROLES else "user"
        hashed = await new_hash(data)
        try:
            user_id = accounts().add(str(data.get("username") or ""), hashed, role)
        except AuthError as e:
            fail(e)
        logger.info(f"Users: {accounts().user(user_id)['username']} added ({role})")
        return {"user": accounts().brief(user_id)}

    @router.put("/users/{user_id}")
    async def update_user(user_id: str, request: Request):
        data = await json_body(request)
        try:
            if "role" in data:
                accounts().set_role(user_id, str(data["role"]))
        except AuthError as e:
            fail(e)
        logger.info(f"Users: {accounts().user(user_id)['username']} is now {accounts().user(user_id)['role']}")
        return {"user": accounts().brief(user_id)}

    @router.post("/users/{user_id}/password")
    async def reset_password(user_id: str, request: Request):
        data = await json_body(request)
        if accounts().user(user_id) is None:
            raise HTTPException(404, "No such user.")
        if accounts().user(user_id).get("ha"):
            raise HTTPException(409, "Set in Home Assistant")
        hashed = await new_hash(data)
        p = p_of(request)
        try:
            accounts().set_password(user_id, hashed, keep_session=p.session_id if user_id == p.user_id else None)
        except AuthError as e:
            fail(e)
        logger.info(f"Users: password of {accounts().user(user_id)['username']} reset")
        return {"user": accounts().brief(user_id)}

    @router.post("/users/{user_id}/sign-out")
    async def sign_out_everywhere(user_id: str, request: Request):
        if accounts().user(user_id) is None:
            raise HTTPException(404, "No such user.")
        p = p_of(request)
        count = accounts().sign_out_everywhere(user_id)
        logger.info(f"Users: {accounts().user(user_id)['username']} signed out of every device ({count})")
        mine = user_id == p.user_id and p.kind == "session"
        response = JSONResponse({"signed_out": count, "this_browser": mine})
        if mine:
            clear_cookie(response, request)
        return response

    @router.delete("/users/{user_id}/sessions/{session_id}")
    async def end_browser_session(user_id: str, session_id: str, request: Request):
        """Sign one browser of a user out (Users & devices: Remove on a browser)."""
        if accounts().user(user_id) is None:
            raise HTTPException(404, "No such user.")
        if not any(i == session_id for i, _ in accounts().sessions(user_id)):
            raise HTTPException(404, "No such browser.")
        accounts().end_session(session_id)
        logger.info(f"Users: a browser of {accounts().user(user_id)['username']} signed out")
        mine = session_id == p_of(request).session_id
        response = JSONResponse({"signed_out": True, "this_browser": mine})
        if mine:
            clear_cookie(response, request)
        return response

    @router.delete("/users/{user_id}")
    async def remove_user(user_id: str, request: Request):
        if user_id == p_of(request).user_id:
            raise HTTPException(409, "You can't remove your own account.")
        user = accounts().user(user_id)
        try:
            accounts().remove(user_id)
        except AuthError as e:
            fail(e)
        logger.info(f"Users: {user['username']} removed")
        return {"removed": True}

    return router


class UsersPlugin(BasePlugin):
    id = PLUGIN_ID
    name = "Users"
    version = "1.0.3"
    description = "Accounts, sign-in and roles for Sonorium"
    author = "Sonorium"
    builtin = True

    def _auth(self):
        host = self._host
        return getattr(host, "auth", None) if host is not None else None

    async def on_enable(self) -> None:
        service = self._auth()
        if service is None:
            logger.warning("Users: no sign-in service (running without the web app); nothing to do")
            return
        accounts = service.plugin_state.get(PLUGIN_ID)
        if accounts is None:
            accounts = Accounts(service)
            service.plugin_state[PLUGIN_ID] = accounts
        if accounts.ha is None:
            accounts.ha = ha_mod.directory_for(service)
            if accounts.ha is not None:
                accounts.ha.on_change = accounts.sync_ha
                accounts.migrate_ha()
                logger.info("Users: Home Assistant's users are Sonorium's users, with their role from Home Assistant")
        service.add_routes(PLUGIN_ID, create_router(service), PERMISSIONS)
        service.register_ui(PLUGIN_ID, self.plugin_dir / "web", js="users.js", css="users.css", version=self.version)
        service.register_authenticator(PLUGIN_ID, UsersAuth(accounts), provider=True)
        logger.info("Users: sign-in is on" + ("" if accounts.require_login else " (Require login is off)"))

    async def on_disable(self) -> None:
        service = self._auth()
        if service is not None:
            service.unregister(PLUGIN_ID)
            logger.warning("Users: switched off. Sonorium is open to anyone on your network.")
