"""
Users, browser sessions, linked phones and keys, in auth.json (owner-only, 0600)
in Sonorium's config folder.

Nothing secret is stored as given:
- passwords: Argon2id hashes (passwords.py);
- session cookies, device tokens and keys: SHA-256 of 256 random bits
  (no slow hash needed for random values that long), compared by hash lookup;
- one-time Link App codes and sign-in codes live in memory only.

Revoking (removing a phone, signing out a browser, revoking a key, removing a
user) deletes the record, and every request looks the record up, so it takes
effect on the next request.

New installs start with Require login on; an install that already has
state.json (an upgrade) starts with it off, until an admin turns it on.

Users carry an id that never changes, so channels, presets and playlists can
later name an owner (PLAN-mobile-api.md section 10-11).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from sonorium.auth import permissions as P
from sonorium.obs import logger

AUTH_FILE = "auth.json"
SESSION_DAYS = 30            # a browser stays signed in at most this long
SESSION_IDLE_DAYS = 14       # ... and is signed out sooner after this long unused
PAIR_CODE_SECONDS = 300      # Link App code
SIGNIN_CODE_SECONDS = 300    # one-time sign-in code from the app
SIGNIN_CODE_TRIES = 5        # wrong guesses before a sign-in code stops working
RESET_SESSION_SECONDS = 600  # after a sign-in code: time to choose a new password
TOUCH_SAVE_SECONDS = 60      # "last seen" is written at most this often
PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I

USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,31}$")
DEVICE_PREFIX = "sd_"
KEY_PREFIX = "sk_"
SESSION_PREFIX = "ss_"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _iso(ts: Optional[float]) -> Optional[str]:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts else None


def _ts(iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return None


class AuthError(Exception):
    """A request that can't be done; the message is shown to the user."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class AuthStore:
    def __init__(self, folder: Path, clock: Callable[[], float] = time.time):
        self.folder = Path(folder)
        self.path = self.folder / AUTH_FILE
        self.clock = clock
        self._lock = threading.RLock()
        self._last_save = 0.0
        self._dirty = False
        self.pair_codes: dict[str, dict] = {}      # code -> {user_id, expires}
        self.signin_codes: dict[str, dict] = {}    # user_id -> {hash, expires, tries}
        self.data = self._load()
        self._index()

    # ------------------------------------------------------------------ file

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not a JSON object")
        except FileNotFoundError:
            upgrade = (self.folder / "state.json").exists()
            data = {"require_login": not upgrade, "created": _iso(self.clock())}
            logger.info("Sign-in: " + ("existing install, Require login is off until an admin turns it on"
                                       if upgrade else "new install, an admin account is needed (first visit)"))
            self._new = True
        except Exception as e:
            # Never start open because the file is damaged: keep it aside and require login
            broken = self.path.with_name(f"{AUTH_FILE}.broken-{int(self.clock())}")
            try:
                os.replace(self.path, broken)
            except OSError:
                pass
            logger.error(f"auth.json could not be read ({e}); kept as {broken.name}. "
                         "Set BYPASS_LOGIN (Docker) or bypass_login (add-on) to get in and set up accounts again.")
            data = {"require_login": True, "created": _iso(self.clock())}
            self._new = True
        data.setdefault("version", 1)
        data.setdefault("secret", secrets.token_hex(32))
        data.setdefault("require_login", True)
        for section in ("users", "sessions", "devices", "keys"):
            if not isinstance(data.get(section), dict):
                data[section] = {}
        for key in data["keys"].values():
            key["abilities"] = P.clean_abilities(key.get("abilities"))
        if getattr(self, "_new", False):
            self.data = data
            self.save()
        return data

    def save(self) -> None:
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.data, indent=2, sort_keys=True))
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
            self._last_save = self.clock()
            self._dirty = False

    def _touched(self) -> None:
        """A "last seen" changed: write it now and then, not on every request."""
        self._dirty = True
        if self.clock() - self._last_save >= TOUCH_SAVE_SECONDS:
            self.save()

    def flush(self) -> None:
        if self._dirty:
            self.save()

    def _index(self) -> None:
        self._device_by_hash = {d["hash"]: i for i, d in self.data["devices"].items() if d.get("hash")}
        self._key_by_hash = {k["hash"]: i for i, k in self.data["keys"].items() if k.get("hash")}

    # ------------------------------------------------------------- settings

    @property
    def secret(self) -> bytes:
        return bytes.fromhex(self.data["secret"])

    def sign(self, purpose: str) -> str:
        return hmac.new(self.secret, purpose.encode("utf-8"), hashlib.sha256).hexdigest()[:40]

    @property
    def require_login(self) -> bool:
        return bool(self.data.get("require_login"))

    def set_require_login(self, on: bool) -> None:
        if on and not any(u.get("password") and u.get("role") == "admin" for u in self.users.values()):
            raise AuthError("Give an admin account a password first.", 409)
        self.data["require_login"] = bool(on)
        self.save()

    # ----------------------------------------------------------------- users

    @property
    def users(self) -> dict:
        return self.data["users"]

    def has_users(self) -> bool:
        return bool(self.users)

    def find_user(self, username: str) -> Optional[tuple[str, dict]]:
        name = (username or "").strip().lower()
        for user_id, user in self.users.items():
            if user["username"].lower() == name:
                return user_id, user
        return None

    def user(self, user_id: Optional[str]) -> Optional[dict]:
        return self.users.get(user_id) if user_id else None

    def admins(self) -> list[str]:
        return [i for i, u in self.users.items() if u.get("role") == "admin"]

    def add_user(self, username: str, password_hash: Optional[str], role: str, ha_user_id: Optional[str] = None) -> str:
        username = (username or "").strip()
        if not USERNAME.match(username):
            raise AuthError("Use 1 to 32 letters, numbers, dots, dashes or underscores.")
        if role not in P.ROLES:
            raise AuthError("Unknown role.")
        if self.find_user(username):
            raise AuthError("That username is taken.", 409)
        user_id = "u_" + secrets.token_hex(8)
        self.users[user_id] = {"username": username, "role": role, "password": password_hash,
                               "ha_user_id": ha_user_id, "created": _iso(self.clock()), "last_seen": None}
        self.save()
        return user_id

    def set_role(self, user_id: str, role: str) -> None:
        user = self._require_user(user_id)
        if role not in P.ROLES:
            raise AuthError("Unknown role.")
        if user["role"] == "admin" and role != "admin" and self.admins() == [user_id]:
            raise AuthError("Sonorium needs at least one admin.", 409)
        user["role"] = role
        self.save()

    def set_password(self, user_id: str, password_hash: str, keep_session: Optional[str] = None) -> None:
        """New password; every browser of the user is signed out (but keep_session). Phones stay linked."""
        user = self._require_user(user_id)
        user["password"] = password_hash
        for h in [h for h, s in self.data["sessions"].items() if s["user_id"] == user_id and h != keep_session]:
            self.data["sessions"].pop(h, None)
        self.signin_codes.pop(user_id, None)
        self.save()

    def remove_user(self, user_id: str) -> None:
        user = self._require_user(user_id)
        if user["role"] == "admin" and self.admins() == [user_id]:
            raise AuthError("Sonorium needs at least one admin.", 409)
        self.sign_out_everywhere(user_id, save=False)
        self.users.pop(user_id, None)
        self.save()

    def sign_out_everywhere(self, user_id: str, save: bool = True) -> int:
        """Every browser signed out and every phone unlinked."""
        sessions = [h for h, s in self.data["sessions"].items() if s["user_id"] == user_id]
        devices = [d for d, v in self.data["devices"].items() if v["user_id"] == user_id]
        for h in sessions:
            self.data["sessions"].pop(h, None)
        for d in devices:
            self.data["devices"].pop(d, None)
        for code in [c for c, v in self.pair_codes.items() if v["user_id"] == user_id]:
            self.pair_codes.pop(code, None)
        self.signin_codes.pop(user_id, None)
        self._index()
        if save:
            self.save()
        return len(sessions) + len(devices)

    def _require_user(self, user_id: str) -> dict:
        user = self.user(user_id)
        if user is None:
            raise AuthError("No such user.", 404)
        return user

    def ha_user(self, ha_user_id: str, ha_username: str) -> str:
        """The Sonorium account for a Home Assistant user (HA's sidebar), made on first sight as an admin."""
        for user_id, user in self.users.items():
            if user.get("ha_user_id") == ha_user_id:
                return user_id
        found = self.find_user(ha_username) if ha_username else None
        if found and not found[1].get("ha_user_id"):
            found[1]["ha_user_id"] = ha_user_id
            self.save()
            return found[0]
        base = re.sub(r"[^A-Za-z0-9._@-]", "", ha_username or "")[:28] or "ha-user"
        if not base[0].isalnum():
            base = "u" + base
        name, n = base, 1
        while self.find_user(name):
            n += 1
            name = f"{base}-{n}"
        logger.info(f"Sign-in: Home Assistant user {ha_username or ha_user_id} gets the Sonorium account {name}")
        return self.add_user(name, None, "admin", ha_user_id=ha_user_id)

    def seen(self, user_id: Optional[str]) -> None:
        user = self.user(user_id)
        if user is not None:
            user["last_seen"] = _iso(self.clock())

    # -------------------------------------------------------------- sessions

    def new_session(self, user_id: str, user_agent: str = "", reset: bool = False) -> str:
        """A browser sign-in. Returns the cookie value (only its hash is kept)."""
        value = SESSION_PREFIX + secrets.token_urlsafe(32)
        now = self.clock()
        self.data["sessions"][sha256(value)] = {
            "user_id": user_id, "created": _iso(now), "last_seen": _iso(now),
            "user_agent": (user_agent or "")[:300], "reset": bool(reset),
            "expires": _iso(now + (RESET_SESSION_SECONDS if reset else SESSION_DAYS * 86400)),
        }
        self.seen(user_id)
        self.save()
        return value

    def session(self, value: Optional[str]) -> Optional[tuple[str, dict]]:
        """(hash, session) for a cookie value, or None when unknown or expired."""
        if not value or not value.startswith(SESSION_PREFIX):
            return None
        h = sha256(value)
        entry = self.data["sessions"].get(h)
        if entry is None:
            return None
        now = self.clock()
        expired = (now >= (_ts(entry.get("expires")) or 0)
                   or now - (_ts(entry.get("last_seen")) or 0) > SESSION_IDLE_DAYS * 86400
                   or entry["user_id"] not in self.users)
        if expired:
            self.data["sessions"].pop(h, None)
            self.save()
            return None
        if now - (_ts(entry.get("last_seen")) or 0) > 30:
            entry["last_seen"] = _iso(now)
            self.seen(entry["user_id"])
            self._touched()
        return h, entry

    def end_session(self, session_hash: str) -> bool:
        if self.data["sessions"].pop(session_hash, None) is None:
            return False
        self.save()
        return True

    def csrf(self, session_hash: str) -> str:
        return self.sign("csrf:" + session_hash)

    # --------------------------------------------------------------- devices

    def new_pair_code(self, user_id: str) -> tuple[str, float]:
        """A Link App code for the user: works once, for 5 minutes. Older codes of the user stop working."""
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

    def use_pair_code(self, code: str) -> Optional[str]:
        """The user a code links to; the code is used up. None when unknown or expired."""
        self._expire_codes()
        code = (code or "").strip().upper().replace("-", "").replace(" ", "")
        match = None
        for known in list(self.pair_codes):
            if hmac.compare_digest(known, code):
                match = known
        if match is None:
            return None
        entry = self.pair_codes.pop(match)
        return entry["user_id"] if entry["user_id"] in self.users else None

    def add_device(self, user_id: str, name: str, platform: str = "", model: str = "") -> tuple[str, str]:
        """A linked phone for the user. Returns (device id, token); the token is never shown again."""
        self._require_user(user_id)
        token = DEVICE_PREFIX + secrets.token_urlsafe(32)
        device_id = "d_" + secrets.token_hex(8)
        now = _iso(self.clock())
        self.data["devices"][device_id] = {
            "user_id": user_id, "name": (name or "Phone").strip()[:60] or "Phone",
            "platform": (platform or "").strip()[:30], "model": (model or "").strip()[:60],
            "hash": sha256(token), "created": now, "last_seen": now,
        }
        self._index()
        self.save()
        return device_id, token

    def device(self, token: Optional[str]) -> Optional[tuple[str, dict]]:
        if not token or not token.startswith(DEVICE_PREFIX):
            return None
        device_id = self._device_by_hash.get(sha256(token))
        entry = self.data["devices"].get(device_id) if device_id else None
        if entry is None or entry["user_id"] not in self.users:
            return None
        now = self.clock()
        if now - (_ts(entry.get("last_seen")) or 0) > 30:
            entry["last_seen"] = _iso(now)
            self.seen(entry["user_id"])
            self._touched()
        return device_id, entry

    def remove_device(self, device_id: str) -> Optional[dict]:
        """A phone (d_...) or a browser session (its hash) removed. Returns what was removed."""
        entry = self.data["devices"].pop(device_id, None)
        if entry is None:
            entry = self.data["sessions"].pop(device_id, None)
        if entry is not None:
            self._index()
            self.save()
        return entry

    def owner_of(self, device_id: str) -> Optional[str]:
        entry = self.data["devices"].get(device_id) or self.data["sessions"].get(device_id)
        return entry["user_id"] if entry else None

    # --------------------------------------------- one-time sign-in codes (recovery)

    def new_signin_code(self, user_id: str) -> tuple[str, float]:
        """A 6-digit code for the user's own sign-in in a browser: once, for 5 minutes."""
        self._require_user(user_id)
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = self.clock() + SIGNIN_CODE_SECONDS
        self.signin_codes[user_id] = {"hash": sha256(code), "expires": expires, "tries": 0}
        return code, expires

    def use_signin_code(self, username: str, code: str) -> Optional[str]:
        """The user id when the code is right (the code is used up), else None."""
        found = self.find_user(username)
        entry = self.signin_codes.get(found[0]) if found else None
        given = sha256((code or "").strip())
        if entry is None:
            hmac.compare_digest(given, "0" * 64)
            return None
        if self.clock() >= entry["expires"]:
            self.signin_codes.pop(found[0], None)
            return None
        if not hmac.compare_digest(given, entry["hash"]):
            entry["tries"] += 1
            if entry["tries"] >= SIGNIN_CODE_TRIES:
                self.signin_codes.pop(found[0], None)
                logger.warning(f"Sign-in: the one-time code for {found[1]['username']} stopped working after {SIGNIN_CODE_TRIES} wrong tries")
            return None
        self.signin_codes.pop(found[0], None)
        return found[0]

    # ------------------------------------------------------------------ keys

    @property
    def keys(self) -> dict:
        return self.data["keys"]

    def add_key(self, name: str, abilities, channels, expires: Optional[float], created_by: Optional[str]) -> tuple[str, str]:
        name = (name or "").strip()
        if not name or len(name) > 60:
            raise AuthError("Give the key a name.")
        token = KEY_PREFIX + secrets.token_urlsafe(32)
        key_id = "k_" + secrets.token_hex(8)
        self.keys[key_id] = {
            "name": name, "hash": sha256(token), "abilities": P.clean_abilities(abilities),
            "channels": self._clean_channels(channels), "expires": _iso(expires),
            "created": _iso(self.clock()), "created_by": created_by, "last_used": None,
        }
        self._index()
        self.save()
        return key_id, token

    def update_key(self, key_id: str, *, name=None, abilities=None, channels=..., expires=...) -> dict:
        key = self.keys.get(key_id)
        if key is None:
            raise AuthError("No such key.", 404)
        if name is not None:
            name = name.strip()
            if not name or len(name) > 60:
                raise AuthError("Give the key a name.")
            key["name"] = name
        if abilities is not None:
            key["abilities"] = P.clean_abilities(abilities)
        if channels is not ...:
            key["channels"] = self._clean_channels(channels)
        if expires is not ...:
            key["expires"] = _iso(expires)
        self.save()
        return key

    @staticmethod
    def _clean_channels(channels):
        if channels is None:
            return None
        return [str(c) for c in channels if isinstance(c, (str, int))][:200]

    def revoke_key(self, key_id: str) -> bool:
        if self.keys.pop(key_id, None) is None:
            return False
        self._index()
        self.save()
        return True

    def key(self, token: Optional[str]) -> Optional[tuple[str, dict]]:
        if not token or not token.startswith(KEY_PREFIX):
            return None
        key_id = self._key_by_hash.get(sha256(token))
        entry = self.keys.get(key_id) if key_id else None
        if entry is None:
            return None
        now = self.clock()
        expires = _ts(entry.get("expires"))
        if expires is not None and now >= expires:
            return None
        if now - (_ts(entry.get("last_used")) or 0) > 30:
            entry["last_used"] = _iso(now)
            self._touched()
        return key_id, entry
