"""
Home Assistant's users (add-on only).

    ws://supervisor/core/websocket     Home Assistant's WebSocket API through the
      config/auth/list                 Supervisor (homeassistant_api: true): HA's
                                       users with id, username, name, is_owner,
                                       is_active, system_generated, group_ids.
                                       (The Supervisor's GET /auth/list needs an
                                       admin hassio_role, which Sonorium doesn't ask for.)
    POST http://supervisor/auth        {"username", "password"}: 200 when HA
                                       accepts them, 401 when it doesn't
                                       (auth_api: true)

The list is kept for a minute (REFRESH_SECONDS) and asked again sooner when a
Home Assistant user Sonorium hasn't seen opens it from HA's sidebar. Passwords
only pass through to the Supervisor: they're never kept or logged.

Role: HA's owner and the Administrators group (system-admin) are Admins;
everyone else (Users, Read-only) is a User.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Optional

from sonorium.obs import logger as _logger

# "sonorium.auth": names users and addresses, so only admins see it on the Logs page (logbuffer.py)
logger = _logger.getChild("auth")

SUPERVISOR = "http://supervisor"
WEBSOCKET = "ws://supervisor/core/websocket"
REFRESH_SECONDS = 60     # the list is asked again at most this often ...
FORCED_SECONDS = 5       # ... or this often when an unknown HA user turns up
TIMEOUT = 5.0
ADMIN_GROUP = "system-admin"


class HAUnavailable(Exception):
    """The Supervisor didn't answer (or refused Sonorium)."""


def role_of(entry: dict) -> str:
    return "admin" if entry.get("is_owner") or ADMIN_GROUP in (entry.get("group_ids") or []) else "user"


def norm(username: Optional[str]) -> str:
    return (username or "").strip().lower()


class Supervisor:
    """HA's list of users, and HA's check of a username and password."""

    def __init__(self, token: str, transport=None, ws_connect=None):
        self.token = token
        self.transport = transport     # tests: an httpx transport
        self.ws_connect = ws_connect   # tests: stands in for websockets.connect

    def _client(self):
        import httpx
        return httpx.AsyncClient(base_url=SUPERVISOR, timeout=TIMEOUT, transport=self.transport,
                                 headers={"Authorization": f"Bearer {self.token}"})

    async def list_users(self) -> list[dict]:
        """HA's people (no system users): config/auth/list over HA's WebSocket API."""
        connect = self.ws_connect
        if connect is None:
            try:
                import websockets
            except ImportError:
                raise HAUnavailable("the websockets library is missing") from None
            connect = websockets.connect
        try:
            async with connect(WEBSOCKET, max_size=16 * 1024 * 1024) as ws:
                hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
                if hello.get("type") != "auth_required":
                    raise HAUnavailable(f"unexpected {hello.get('type')}")
                await ws.send(json.dumps({"type": "auth", "access_token": self.token}))
                auth = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
                if auth.get("type") != "auth_ok":
                    raise HAUnavailable("Home Assistant refused the add-on's token")
                await ws.send(json.dumps({"id": 1, "type": "config/auth/list"}))
                while True:
                    reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=TIMEOUT))
                    if reply.get("id") == 1 and reply.get("type") == "result":
                        break
        except HAUnavailable:
            raise
        except Exception as e:
            raise HAUnavailable(f"no answer ({type(e).__name__})") from None
        if not reply.get("success"):
            raise HAUnavailable((reply.get("error") or {}).get("message") or "config/auth/list failed")
        users = reply.get("result")
        if not isinstance(users, list):
            raise HAUnavailable("an answer without users")
        return [u for u in users if isinstance(u, dict) and u.get("id") and not u.get("system_generated")]

    async def check_password(self, username: str, password: str) -> bool:
        try:
            async with self._client() as client:
                response = await client.post("/auth", json={"username": username, "password": password})
        except Exception as e:
            raise HAUnavailable(f"no answer ({type(e).__name__})") from None
        if response.status_code == 200:
            return True
        if response.status_code in (400, 401):
            return False
        raise HAUnavailable(f"HTTP {response.status_code}")


class HADirectory:
    """Home Assistant's users, kept for a minute. on_change() runs after each list that came back."""

    def __init__(self, supervisor: Supervisor, clock):
        self.supervisor = supervisor
        self.clock = clock
        self.users: Optional[list[dict]] = None   # None: never had an answer
        self.loaded_at = float("-inf")
        self.forced_at = float("-inf")
        self.on_change = lambda: None
        self._locks: dict = {}
        self._failing = False

    @property
    def loaded(self) -> bool:
        return self.users is not None

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(id(loop))
        if lock is None:
            self._locks = {id(loop): (lock := asyncio.Lock())}
        return lock

    def stale(self) -> bool:
        return self.clock() - self.loaded_at >= REFRESH_SECONDS

    async def refresh(self, force: bool = False) -> bool:
        """Ask the Supervisor again when the list is a minute old (force: unless asked in the last 5 s)."""
        if not (self.stale() or (force and self.clock() - self.forced_at >= FORCED_SECONDS)):
            return False
        async with self._lock():
            now = self.clock()
            if not (now - self.loaded_at >= REFRESH_SECONDS or (force and now - self.forced_at >= FORCED_SECONDS)):
                return False  # another request just asked
            self.loaded_at = now
            if force:
                self.forced_at = now
            try:
                users = await self.supervisor.list_users()
                if not users:
                    raise HAUnavailable("an empty list of users")
            except HAUnavailable as e:
                if not self._failing:
                    logger.warning(f"Users: Home Assistant's list of users isn't available ({e}); "
                                   "Sonorium keeps the roles it knows and tries again in a minute")
                self._failing = True
                return False
            if self._failing:
                logger.info("Users: Home Assistant's list of users is available again")
            self._failing = False
            self.users = users
        self.on_change()
        return True

    def by_username(self, username: Optional[str]) -> Optional[dict]:
        name = norm(username)
        if not name or not self.users:
            return None
        return next((u for u in self.users if norm(u.get("username")) == name), None)

    def by_id(self, ha_user_id: Optional[str]) -> Optional[dict]:
        if not ha_user_id or not self.users:
            return None
        return next((u for u in self.users if u.get("id") == ha_user_id), None)

    async def check_password(self, username: str, password: str) -> bool:
        return await self.supervisor.check_password(username, password)


def directory_for(service) -> Optional[HADirectory]:
    """The add-on with a Supervisor token: Home Assistant's users. Elsewhere: None."""
    from sonorium.auth import config
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not config.is_addon() or not token:
        return None
    return HADirectory(Supervisor(token, getattr(service, "supervisor_transport", None),
                                  getattr(service, "supervisor_ws_connect", None)), service.clock)
