"""
The core of sign-in: one AuthService per Sonorium, shared by the middleware,
the core routes, Users (users.py), Keys (keys.py) and the plugin host
(PluginHost.auth).

    security.json   owner-only: the signing secret (CSRF), and every token
                    (linked phones, browser sessions, keys) as hashes
    server.json     the server ID;  tls/  the HTTPS certificate (identity.py)

Users and Keys plug in with:

    register_authenticator(owner, authenticator)       who is calling (principal.py)
    add_routes(owner, router, permissions)             their API, with a permission per route
    unregister(owner)                                  all of it (a switch turned off)

A gatekeeper (Users: expected from the moment the app is made) keeps sign-in
closed until it has registered: while Sonorium starts, or for good when the
accounts couldn't be loaded, nothing but public routes answers, so a failure
never leaves Sonorium open.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from pathlib import Path
from typing import Optional

from sonorium.auth import config, permissions as P
from sonorium.auth.secrets import JsonFile
from sonorium.auth.tokens import TokenService
from sonorium.obs import logger

SECURITY_FILE = "security.json"
DEVICE_PREFIX = "sd_"
PAIR_CODE_SECONDS = 300
PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
# keys.py: part of Sonorium, so not listed as a plugin. users.py is part of Sonorium too, but "users"
# stays in the "plugins" list of /api/info and /api/auth/me: released Sonorium apps read it to offer
# signing in with a username and password (new apps read "accounts").
CORE_AUTHENTICATORS = frozenset({"keys"})


class AuthService:
    """Everything is made on first use, so importing or building the app writes nothing."""

    def __init__(self, folder=None, clock=time.time):
        self._folder = folder
        self.clock = clock
        self._file: Optional[JsonFile] = None
        self._tokens: Optional[TokenService] = None
        self._identity = None
        self.registry = P.PermissionRegistry()
        self.authenticators: dict[str, object] = {}
        self.ui_bundles: dict[str, dict] = {}
        self.app = None                       # the FastAPI app (set by api.WebApp)
        self._routers: set[str] = set()       # plugins whose routers are in the app
        self.ready = False                    # plugins loaded (or failed): see expected_providers
        self.expected_providers: set[str] = set()
        self.fresh_install: Optional[bool] = None
        self.pair_codes: dict[str, dict] = {}
        self.plugin_state: dict[str, object] = {}  # a plugin's live objects, kept across reloads (e.g. Accounts)
        self.https_only = lambda request: None     # set by the core routes
        self.supervisor_transport = None           # tests: an httpx transport standing in for HA's Supervisor
        self.supervisor_ws_connect = None          # tests: stands in for websockets.connect (HA's WebSocket API)

    # ------------------------------------------------------------ storage

    @property
    def folder(self) -> Path:
        return Path(self._folder or config.config_dir())

    @property
    def file(self) -> JsonFile:
        if self._file is None:
            self._file = JsonFile(self.folder / SECURITY_FILE, private=True)
            if not self._file.data.get("secret"):
                self._file.data["secret"] = secrets.token_hex(32)
                self._file.save()
        return self._file

    @property
    def tokens(self) -> TokenService:
        if self._tokens is None:
            self._tokens = TokenService(self.file, clock=self.clock)
        return self._tokens

    @property
    def identity(self):
        if self._identity is None:
            from sonorium.auth.identity import ServerIdentity
            self._identity = ServerIdentity(self.folder)
        return self._identity

    def sign(self, purpose: str) -> str:
        """An HMAC of `purpose` with the server's secret (CSRF tokens and the like)."""
        secret = bytes.fromhex(self.file.data["secret"])
        return hmac.new(secret, purpose.encode("utf-8"), hashlib.sha256).hexdigest()[:40]

    def flush(self) -> None:
        if self._tokens is not None:
            self._tokens.flush()

    # ------------------------------------------------------------ startup

    def note_install(self) -> None:
        """Before state.json is written: is this a new install? (Users: Require login starts on.)"""
        if self.fresh_install is None:
            self.fresh_install = not (self.folder / "state.json").exists()

    def mark_ready(self) -> None:
        self.ready = True
        missing = self.expected_providers - set(self.authenticators)
        if missing:
            logger.error(f"Sign-in ({', '.join(sorted(missing))}) didn't start: only streams answer. See the "
                         "messages above. To get in anyway, set BYPASS_LOGIN (Docker) or bypass_login (add-on) to true.")

    @property
    def closed(self) -> bool:
        """Starting, or a gatekeeper (Users) didn't start: only public routes answer."""
        return not self.ready or bool(self.expected_providers - set(self.authenticators))

    # ------------------------------------------------------------ plugins

    def register_authenticator(self, plugin_id: str, authenticator, provider: bool = False) -> None:
        self.authenticators[plugin_id] = authenticator
        if provider:
            self.expected_providers.add(plugin_id)
        self.registry.set_active(plugin_id, True)
        if plugin_id in self.ui_bundles:
            self.ui_bundles[plugin_id]["active"] = True

    def plugin_ids(self) -> list[str]:
        """The "plugins" list of /api/info and /api/auth/me: "users" while accounts are loaded (see CORE_AUTHENTICATORS)."""
        return sorted(a for a in self.authenticators if a not in CORE_AUTHENTICATORS)

    @property
    def accounts_loaded(self) -> bool:
        """Users (users.py) are up: accounts, sign-in with a username and password."""
        return "users" in self.authenticators

    def add_routes(self, plugin_id: str, router, permissions) -> None:
        """A plugin's API: every route of the router must be in `permissions` ("METHOD path perm" lines)."""
        table = P.parse(permissions) if isinstance(permissions, str) else permissions
        paths = {(m, r.path) for r in getattr(router, "routes", []) for m in (getattr(r, "methods", None) or [])
                 if m != "HEAD"}
        missing = sorted(paths - set(table))
        if missing:
            raise ValueError(f"Plugin {plugin_id}: no permission for {missing}")
        self.registry.register(plugin_id, table)
        if plugin_id not in self._routers and self.app is not None:
            self.app.include_router(router)
            self._routers.add(plugin_id)

    def register_ui(self, plugin_id: str, folder: Path, js: Optional[str] = None, css: Optional[str] = None,
                    version: str = "") -> None:
        self.ui_bundles[plugin_id] = {"folder": Path(folder), "js": js, "css": css, "version": version, "active": True}

    def unregister(self, plugin_id: str) -> None:
        """The plugin was switched off: its sign-in, routes (404 from now) and UI go."""
        self.authenticators.pop(plugin_id, None)
        self.expected_providers.discard(plugin_id)
        self.registry.set_active(plugin_id, False)
        if plugin_id in self.ui_bundles:
            self.ui_bundles[plugin_id]["active"] = False

    def login_required(self) -> bool:
        """Require login is on (users.py), and the bypass is off."""
        if config.bypass_login():
            return False
        return any(bool(getattr(a, "login_required", lambda: False)()) for a in list(self.authenticators.values()))

    def provider(self, method: str):
        """The registered authenticators that have `method`, in registration order."""
        return [(pid, getattr(a, method)) for pid, a in list(self.authenticators.items()) if hasattr(a, method)]

    def describe_user(self, user_id: Optional[str]) -> Optional[dict]:
        if not user_id:
            return None
        for _, user in self.provider("user"):
            try:
                found = user(user_id)
            except Exception:
                found = None
            if found:
                return found
        return None

    def user_blocked(self, user_id: Optional[str]) -> bool:
        """Whether the user is switched off (e.g. deactivated in Home Assistant; users.py)."""
        if not user_id:
            return False
        for _, blocked in self.provider("blocked"):
            try:
                if blocked(user_id):
                    return True
            except Exception:
                return True
        return False

    # ------------------------------------------------------------ linked phones (core)

    def new_pair_code(self, user_id: Optional[str]) -> tuple[str, float]:
        """A Link App code: works once, for 5 minutes. The previous code of the same user stops working."""
        self._expire_codes()
        for code in [c for c, v in self.pair_codes.items() if v["user_id"] == user_id]:
            self.pair_codes.pop(code, None)
        code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(8))
        expires = self.clock() + PAIR_CODE_SECONDS
        self.pair_codes[code] = {"user_id": user_id, "expires": expires}
        return code, expires

    def _expire_codes(self) -> None:
        now = self.clock()
        for code in [c for c, v in self.pair_codes.items() if v["expires"] <= now]:
            self.pair_codes.pop(code, None)

    def use_pair_code(self, code: str) -> tuple[bool, Optional[str]]:
        """(valid, user id) for a code, which is then used up."""
        self._expire_codes()
        code = (code or "").strip().upper().replace("-", "").replace(" ", "")
        match = None
        for known in list(self.pair_codes):
            if hmac.compare_digest(known, code):
                match = known
        if match is None:
            return False, None
        return True, self.pair_codes.pop(match)["user_id"]

    def add_device(self, user_id: Optional[str], name: str, platform: str = "", model: str = "") -> tuple[str, str]:
        """A linked phone. Returns (device id, token); the token is never shown again."""
        name = (name or "Phone").strip()[:60] or "Phone"
        return self.tokens.issue("device", prefix=DEVICE_PREFIX, user_id=user_id, name=name,
                                 data={"platform": (platform or "").strip()[:30], "model": (model or "").strip()[:60]})

    @staticmethod
    def _device_row(device_id: str, r: dict) -> dict:
        return {"id": device_id, "name": r.get("name") or "Phone", "user_id": r.get("user_id"),
                "platform": (r.get("data") or {}).get("platform", ""),
                "model": (r.get("data") or {}).get("model", ""),
                "created": r.get("created"), "last_seen": r.get("last_used") or r.get("created")}

    def devices(self, user_id=...) -> list[dict]:
        rows = [self._device_row(device_id, r)
                for device_id, r in self.tokens.list("device", None if user_id is ... else user_id)]
        rows.sort(key=lambda d: d.get("created") or "")
        return rows

    def device(self, device_id: Optional[str]) -> Optional[dict]:
        """One linked phone's row (as in devices()), or None."""
        record = self.tokens.get(device_id) if device_id else None
        if record is None or record.get("kind") != "device":
            return None
        return self._device_row(device_id, record)

    def rename_device(self, device_id: str, name: str) -> Optional[dict]:
        """A phone's new name (trimmed to 60 characters). None: no such phone."""
        if self.device(device_id) is None:
            return None
        self.tokens.update(device_id, name=(name or "").strip()[:60] or "Phone")
        return self.device(device_id)

    def remove_device(self, device_id: str) -> Optional[dict]:
        record = self.tokens.get(device_id)
        if record is None or record.get("kind") != "device":
            return None
        self.tokens.revoke(device_id)
        return record

    def remove_devices_of(self, user_id: str) -> int:
        return self.tokens.revoke_where(lambda r: r.get("kind") == "device" and r.get("user_id") == user_id)
