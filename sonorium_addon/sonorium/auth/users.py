"""
Users: accounts and roles, browser sign-in, Require login, one-time sign-in
codes from a linked phone, Users & devices, and Home Assistant sidebar users.

Part of Sonorium's sign-in (sonorium/auth), installed by the app at startup
(install(), then attach_zones() once channels exist). There is no switch:
sign-in stays optional through Require login (and BYPASS_LOGIN); with no
accounts yet it is the first run (Create the admin account). If the accounts
can't be loaded, sign-in stays closed (AuthService.closed): only public
routes answer, so Sonorium is never left open by a failure.

Roles: Admin (everything) and User (channels, playlists and sequences, view
themes and speakers, their own phones). A linked phone acts as its user.

Add-on: Home Assistant's users are Sonorium's users, with their role from HA
(ha_users.py), and sign in with their HA username and password.

Zones (core/zones.py, Settings > Advanced > Zones, off by default): channels
owned by a user, Child Mode, speaker homes and linked channels.
"""
from __future__ import annotations

import asyncio
import functools
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from sonorium.auth import accounts as accounts_mod, config, ha_users as ha_mod, passwords, permissions as P
from sonorium.auth.accounts import Accounts, AuthError
from sonorium.auth.principal import Principal, Reject
from sonorium.auth.ratelimit import RateLimiter
from sonorium.auth.routes import client_of, json_body, limited
from sonorium.core.channel_policy import ChannelRefused
from sonorium.core import zones as zones_mod
from sonorium.core.zones import Actor, Zones
from sonorium.obs import logger as _logger

# "sonorium.auth": names users and addresses, so only admins see it on the Logs page (logbuffer.py)
logger = _logger.getChild("auth")

PLUGIN_ID = "users"   # its key in the authenticators and AuthService.plugin_state (not a plugin)
ZONES_STATE = "users.zones"   # service.plugin_state key of the Zones object
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
GET     /api/zones                      ch.view
PUT     /api/zones/switch               st.change
PUT     /api/zones/homes/{speaker_id}   st.change
PUT     /api/zones/{channel_id}         st.change
POST    /api/links                      st.change
PUT     /api/links/{link_id}            st.change
DELETE  /api/links/{link_id}            st.change
POST    /api/links/{link_id}/leave      ch.play
POST    /api/links/{link_id}/rejoin     ch.play
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

    def __init__(self, accounts: Accounts, zones=None):
        self.accounts = accounts
        self.zones = zones

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
                return Reject(401, {"detail": "Link this device again to sign it in.", "pair": True})
            principal.perms = P.device_perms(accounts_mod.OPEN_PERMS)
            return principal
        user = self.accounts.user(user_id)
        if user is None or user.get("disabled"):
            return Reject(401, {"detail": "This device is not linked to Sonorium. Link it again.", "pair": True})
        self.accounts.seen(user_id)
        principal.perms = P.device_perms(accounts_mod.role_perms(user["role"]))
        principal.role = user["role"]
        return principal

    def me(self, ctx, principal: Principal) -> dict:
        a = self.accounts
        return {"require_login": a.require_login, "has_users": a.has_users(), "ha_users": a.ha is not None,
                "zones": bool(self.zones is not None and self.zones.enabled),
                "needs_setup": (principal.kind == "anonymous" and not a.has_users() and a.require_login
                                and principal.listener != "ingress" and not config.is_addon())}


def create_router(service) -> APIRouter:
    """The plugin's API. Handlers find the current Accounts on the service, so a reloaded plugin keeps working."""
    router = APIRouter(prefix="/api", tags=["users"])
    login_limiter = RateLimiter(LOGIN_RATE, LOGIN_BURST)
    code_limiter = RateLimiter(CODE_RATE, CODE_BURST)
    # Failed sign-ins for one username from any address (Settings > Advanced; 0: no limit),
    # next to the limit per address: many addresses can't guess one account's password
    from sonorium import limits as limits_mod
    user_limiter = RateLimiter(lambda: limits_mod.value(limits_mod.LOGIN_PER_USER) / 60,
                               lambda: limits_mod.value(limits_mod.LOGIN_PER_USER))

    def user_key(username: str) -> Optional[str]:
        """The per-username bucket ("" and no limit: none)."""
        name = (username or "").strip().casefold()[:64]
        return "user:" + name if name and limits_mod.value(limits_mod.LOGIN_PER_USER) > 0 else None

    def user_limited(username: str) -> None:
        key = user_key(username)
        if key is not None:
            limited(user_limiter, key, spend=False)

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
        key = user_key(username)
        if key is not None:
            user_limiter.allow(key)
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
        user_limited(username)
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

    def setup_allowed(request: Request, data: dict, client: str) -> None:
        """
        The first account is made from this computer or the LAN, or with the
        setup token (SONORIUM_SETUP_TOKEN, in "setup_token" or the
        X-Sonorium-Setup-Token header) when one is set: a server reachable from
        the internet isn't taken over by whoever finds it first.
        """
        import hmac
        import os
        from sonorium.auth.stream_links import on_lan
        expected = (os.environ.get("SONORIUM_SETUP_TOKEN") or "").strip()
        given = str(data.get("setup_token") or request.headers.get("x-sonorium-setup-token") or "").strip()
        if expected and given:
            if hmac.compare_digest(given.encode(), expected.encode()):
                return
            login_limiter.allow(client)
            logger.warning(f"Users: a wrong setup token from {client}")
            raise HTTPException(403, "The setup token is wrong.")
        if on_lan(client):
            return
        logger.warning(f"Users: the first account can't be made from {client} (not on the LAN)")
        raise HTTPException(403, "Make the first account from this computer or the LAN"
                                 + (", or with the setup token." if expected else "."))

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
        setup_allowed(request, data, client)
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
        user_limited(str(data.get("username") or ""))
        user_id = accounts().use_signin_code(str(data.get("username") or ""), str(data.get("code") or ""))
        if user_id is None:
            login_limiter.allow(client)
            key = user_key(str(data.get("username") or ""))
            if key is not None:
                user_limiter.allow(key)
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
        logger.info(f"Link App: {data.get('device_name') or 'a device'} at {client} signed in as {user['username']}")
        return {"device_id": device_id, "token": token, "user": accounts().brief(user_id),
                "server_id": service.identity.server_id, "words": service.identity.words}

    @router.post("/devices/recovery-code")
    async def recovery_code(request: Request):
        """A linked phone: a 6-digit code to sign its user in to the web UI (once, for 5 minutes)."""
        p = p_of(request)
        if p.kind != "device" or not p.user_id:
            raise HTTPException(403, "Only a device linked to a user can show a sign-in code")
        limited(code_limiter, p.device_id, spend=True)
        try:
            code, expires = accounts().new_signin_code(p.user_id)
        except AuthError as e:
            fail(e)
        username = accounts().user(p.user_id)["username"]
        logger.info(f"Users: a one-time sign-in code for {username} was shown on a linked device")
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
                          "can_leave_link": u["role"] == "admin" or bool(u.get("can_leave_link")),
                          "devices": devices, "last_seen": seen, "created": u.get("created")})
        users.sort(key=lambda u: (not u["you"], u["username"].lower()))
        unbound = [d for d in service.devices() if d.get("user_id") not in a.users]  # no (or a removed) user
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
                logger.info(f"Users: {accounts().user(user_id)['username']} is now {accounts().user(user_id)['role']}")
            if "can_leave_link" in data:
                accounts().set_can_leave_link(user_id, bool(data["can_leave_link"]))
                logger.info(f"Users: {accounts().user(user_id)['username']} "
                            f"{'can' if data['can_leave_link'] else 'can not'} leave a link")
        except AuthError as e:
            fail(e)
        brief = accounts().brief(user_id)
        brief["can_leave_link"] = brief["role"] == "admin" or bool(accounts().user(user_id).get("can_leave_link"))
        return {"user": brief}

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
            raise HTTPException(409, "You can't remove your account.")
        user = accounts().user(user_id)
        try:
            accounts().remove(user_id)
        except AuthError as e:
            fail(e)
        zones_state = service.plugin_state.get(ZONES_STATE)
        if zones_state is not None:
            zones_state.forget_user(user_id)
        # Their theme backups go with the account (core/backups.py)
        from sonorium.core.backups import FOLDER, remove_account
        remove_account(f"user:{user_id}", service.folder / FOLDER)
        logger.info(f"Users: {user['username']} removed")
        return {"removed": True}

    # ------------------------------------------------------------ Zones (zones.py)

    def refusals(handler):
        """A refusal from zones (ChannelRefused) answers with its status and message."""
        @functools.wraps(handler)
        async def run(*args, **kwargs):
            try:
                return await handler(*args, **kwargs)
            except ChannelRefused as e:
                raise HTTPException(e.status, e.detail)
        return run

    def zones():
        current = service.plugin_state.get(ZONES_STATE)
        if current is None:
            raise HTTPException(404, "Not Found")
        return current

    def zones_on():
        z = zones()
        if not z.enabled:
            raise HTTPException(409, "Zones are off (Settings > Advanced).")
        if z.sm is None:
            raise HTTPException(503, "Channels aren't ready yet.")
        return z

    @router.get("/zones")
    async def zones_view(request: Request):
        """Zones: on or off, each channel's owner and Child Mode, speaker homes and use, links."""
        return zones().view(Actor.current())

    @router.put("/zones/switch")
    async def zones_switch(request: Request):
        data = await json_body(request)
        zones().set_enabled(bool(data.get("on")))
        return zones().view(Actor.current())

    @router.put("/zones/homes/{speaker_id}")
    @refusals
    async def zones_home(speaker_id: str, request: Request):
        """An admin chooses a speaker's home channel ({"channel_id"})."""
        data = await json_body(request)
        zones_on().set_home(speaker_id, str(data.get("channel_id") or ""))
        return zones().view(Actor.current())

    @router.put("/zones/{channel_id}")
    @refusals
    async def zones_set(channel_id: str, request: Request):
        """An admin sets a channel's owner ({"owner": user id or null}), Child Mode and its hours."""
        data = await json_body(request)
        z = zones_on()
        session = z.sm.get(channel_id)
        if session is None:
            raise HTTPException(404, "Channel not found")
        done = z.set_zone(channel_id, {k: data[k] for k in ("owner", "kid_mode", "schedule") if k in data})
        if done:
            logger.info(f"Zones: {session.name}: {', '.join(f'{k} {v}' for k, v in done.items())}")
            zones_mod.publish("zone.changed", {"channel_id": channel_id, "name": session.name, "changes": done})
        return z.view(Actor.current())

    @router.post("/links")
    @refusals
    async def link_create(request: Request):
        """Link channels: {"name", "channels": [ids], "theme_id", "preset_id"} or {..., "playlist_id"}; they play."""
        return await zones_on().create_link(await json_body(request))

    @router.put("/links/{link_id}")
    @refusals
    async def link_change(link_id: str, request: Request):
        return await zones_on().change_link(link_id, await json_body(request))

    @router.delete("/links/{link_id}")
    @refusals
    async def link_remove(link_id: str, request: Request):
        """Unlink: each channel plays again what it played before."""
        await zones_on().remove_link(link_id)
        return {"removed": True}

    @router.post("/links/{link_id}/leave")
    @refusals
    async def link_leave(link_id: str, request: Request):
        """{"channel_id"}: it plays again what it played before (its owner, if allowed to leave; an admin)."""
        data = await json_body(request)
        return await zones_on().leave(link_id, str(data.get("channel_id") or ""), Actor.current())

    @router.post("/links/{link_id}/rejoin")
    @refusals
    async def link_rejoin(link_id: str, request: Request):
        """{"channel_id"}: back in the link (its owner, if allowed to leave; an admin pulls it back)."""
        data = await json_body(request)
        return await zones_on().rejoin(link_id, str(data.get("channel_id") or ""), Actor.current())

    return router




def install(service) -> Optional[Accounts]:
    """
    At startup: the accounts (users.json), Home Assistant's users on the
    add-on, the routes and the sign-in hooks. Sign-in stays closed until this
    has worked ("users" is expected from the start); a failure is logged and
    leaves it closed. Returns the accounts, or None when they couldn't load.
    """
    service.expected_providers.add(PLUGIN_ID)
    try:
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
        zones = service.plugin_state.get(ZONES_STATE)
        if zones is None:
            zones = Zones(accounts)
            service.plugin_state[ZONES_STATE] = zones
        service.add_routes(PLUGIN_ID, create_router(service), PERMISSIONS)
        service.register_authenticator(PLUGIN_ID, UsersAuth(accounts, zones), provider=True)
    except Exception as e:
        logger.error(f"Users: the accounts could not be loaded ({e}). Sign-in stays closed: only streams answer. "
                     "To get in anyway, set BYPASS_LOGIN (Docker) or bypass_login (add-on) to true.")
        return None
    logger.info("Users: sign-in is on" + ("" if accounts.require_login else " (Require login is off)")
                + ("; zones are on" if zones.enabled else ""))
    return accounts


def attach_zones(service, session_manager, on_change=None, timers: bool = True) -> None:
    """Once the channels exist: Zones' policy on them, and Child Mode's hours (timers)."""
    zones = service.plugin_state.get(ZONES_STATE)
    if zones is None:
        return
    zones.attach(session_manager, on_change=on_change)
    if zones.sm is not None and timers:
        zones.start()
