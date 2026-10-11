"""
The Users plugin's accounts: users.json (owner-only) in Sonorium's config
folder, outside the plugin's own folder so updates and reinstalls keep it.

    {"require_login": bool, "users": {id: {username, role, password, ha_user_id,
                                           created, last_seen}}}

- Passwords are Argon2id hashes (passwords.py).
- Browser sessions are core tokens (kind "session", "ss_..."), kept as hashes
  in security.json: 30 days at most, and lapse after 14 days unused.
- One-time sign-in codes (from a linked phone) live in memory only.

New installs start with Require login on; an install that already had
state.json (an upgrade) starts with it off until an admin turns it on.

User ids never change, so channels, presets and playlists can name an owner
later (zones: ownership, Kid mode, speaker homes).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Optional

from sonorium.auth import permissions as P
from sonorium.auth.secrets import JsonFile
from sonorium.auth.tokens import iso
from sonorium.obs import logger

USERS_FILE = "users.json"
SESSION_PREFIX = "ss_"
SESSION_DAYS = 30            # a browser stays signed in at most this long
SESSION_IDLE_DAYS = 14       # ... and is signed out sooner after this long unused
RESET_SESSION_SECONDS = 600  # after a sign-in code: time to choose a new password
SIGNIN_CODE_SECONDS = 300    # one-time sign-in code from the app
SIGNIN_CODE_TRIES = 5        # wrong guesses before a sign-in code stops working
USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,31}$")

ROLES = ("admin", "user")
ROLE_LABELS = {"admin": "Admin", "user": "User"}
USER_PERMS = frozenset({
    "ch.view", "ch.play", "ch.change", "ch.speakers", "ch.add",
    "th.view",
    "pl.view", "pl.run", "pl.edit",
    "sp.view", "sp.mute",
    "ev.read",
    P.SELF,
})
ROLE_PERMS = {"admin": P.ALL_PERMS, "user": USER_PERMS}
# Nobody signed in while Require login is off: open access, but managing
# users and keys needs a sign-in once there are accounts
OPEN_PERMS = frozenset(P.ALL_PERMS - {"admin.users", "admin.keys"})


def role_perms(role: str) -> frozenset:
    return ROLE_PERMS.get(role, frozenset())


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthError(Exception):
    """A request that can't be done; the message is shown to the user."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class Accounts:
    def __init__(self, service, file: Optional[JsonFile] = None):
        """service: the core AuthService (tokens, devices, signing secret)."""
        self.service = service
        self.clock = service.clock
        self.file = file or JsonFile(service.folder / USERS_FILE, private=True)
        self.signin_codes: dict[str, dict] = {}  # user_id -> {hash, expires, tries}
        data = self.file.data
        if "require_login" not in data:
            fresh = service.fresh_install
            data["require_login"] = True if fresh is None else bool(fresh)
            logger.info("Users: " + ("new install, an admin account is made on the first visit"
                                     if data["require_login"] else
                                     "Require login is off until an admin turns it on (Settings > Users & devices)"))
        if not isinstance(data.get("users"), dict):
            data["users"] = {}
        self.file.save()

    def save(self) -> None:
        self.file.save()

    # ------------------------------------------------------------ settings

    @property
    def require_login(self) -> bool:
        return bool(self.file.data.get("require_login"))

    def set_require_login(self, on: bool) -> None:
        if on and not any(u.get("password") and u.get("role") == "admin" for u in self.users.values()):
            raise AuthError("Give an admin account a password first.", 409)
        self.file.data["require_login"] = bool(on)
        self.save()

    # ------------------------------------------------------------ users

    @property
    def users(self) -> dict:
        return self.file.data["users"]

    def has_users(self) -> bool:
        return bool(self.users)

    def find(self, username: str) -> Optional[tuple[str, dict]]:
        name = (username or "").strip().lower()
        for user_id, user in self.users.items():
            if user["username"].lower() == name:
                return user_id, user
        return None

    def user(self, user_id: Optional[str]) -> Optional[dict]:
        return self.users.get(user_id) if user_id else None

    def brief(self, user_id: Optional[str]) -> Optional[dict]:
        user = self.user(user_id)
        if user is None:
            return None
        return {"id": user_id, "username": user["username"], "role": user["role"],
                "role_label": ROLE_LABELS.get(user["role"], user["role"])}

    def admins(self) -> list[str]:
        return [i for i, u in self.users.items() if u.get("role") == "admin"]

    def add(self, username: str, password_hash: Optional[str], role: str, ha_user_id: Optional[str] = None) -> str:
        username = (username or "").strip()
        if not USERNAME.match(username):
            raise AuthError("Use 1 to 32 letters, numbers, dots, dashes or underscores.")
        if role not in ROLES:
            raise AuthError("Unknown role.")
        if self.find(username):
            raise AuthError("That username is taken.", 409)
        user_id = "u_" + secrets.token_hex(8)
        self.users[user_id] = {"username": username, "role": role, "password": password_hash,
                               "ha_user_id": ha_user_id, "created": iso(self.clock()), "last_seen": None}
        self.save()
        return user_id

    def set_role(self, user_id: str, role: str) -> None:
        user = self._require(user_id)
        if role not in ROLES:
            raise AuthError("Unknown role.")
        if user["role"] == "admin" and role != "admin" and self.admins() == [user_id]:
            raise AuthError("Sonorium needs at least one admin.", 409)
        user["role"] = role
        self.save()

    def set_password(self, user_id: str, password_hash: str, keep_session: Optional[str] = None) -> None:
        """New password; every browser of the user is signed out (but keep_session). Phones stay linked."""
        user = self._require(user_id)
        user["password"] = password_hash
        self.service.tokens.revoke_where(lambda r: r.get("kind") == "session" and r.get("user_id") == user_id
                                         and r is not self.service.tokens.get(keep_session or ""))
        self.signin_codes.pop(user_id, None)
        self.save()

    def remove(self, user_id: str) -> None:
        user = self._require(user_id)
        if user["role"] == "admin" and self.admins() == [user_id]:
            raise AuthError("Sonorium needs at least one admin.", 409)
        self.sign_out_everywhere(user_id)
        self.users.pop(user_id, None)
        self.save()

    def sign_out_everywhere(self, user_id: str) -> int:
        """Every browser signed out and every phone unlinked."""
        sessions = self.service.tokens.revoke_where(lambda r: r.get("kind") == "session" and r.get("user_id") == user_id)
        phones = self.service.remove_devices_of(user_id)
        self.signin_codes.pop(user_id, None)
        return sessions + phones

    def _require(self, user_id: str) -> dict:
        user = self.user(user_id)
        if user is None:
            raise AuthError("No such user.", 404)
        return user

    def ha_user(self, ha_user_id: str, ha_username: str) -> str:
        """The Sonorium account of a Home Assistant user (HA's sidebar), made as an admin on first sight."""
        for user_id, user in self.users.items():
            if user.get("ha_user_id") == ha_user_id:
                return user_id
        found = self.find(ha_username) if ha_username else None
        if found and not found[1].get("ha_user_id"):
            found[1]["ha_user_id"] = ha_user_id
            self.save()
            return found[0]
        base = re.sub(r"[^A-Za-z0-9._@-]", "", ha_username or "")[:28] or "ha-user"
        if not base[0].isalnum():
            base = "u" + base
        name, n = base, 1
        while self.find(name):
            n += 1
            name = f"{base}-{n}"
        logger.info(f"Users: Home Assistant user {ha_username or ha_user_id} gets the Sonorium account {name}")
        return self.add(name, None, "admin", ha_user_id=ha_user_id)

    def seen(self, user_id: Optional[str]) -> None:
        user = self.user(user_id)
        if user is not None:
            user["last_seen"] = iso(self.clock())

    # ------------------------------------------------------------ browser sessions

    def new_session(self, user_id: str, user_agent: str = "", reset: bool = False) -> str:
        """A browser sign-in. Returns the cookie value (only its hash is kept)."""
        expires = self.clock() + (RESET_SESSION_SECONDS if reset else SESSION_DAYS * 86400)
        _, token = self.service.tokens.issue(
            "session", prefix=SESSION_PREFIX, user_id=user_id, owner="users", expires=expires,
            idle=SESSION_IDLE_DAYS * 86400, data={"user_agent": (user_agent or "")[:300], "reset": bool(reset)})
        self.seen(user_id)
        self.save()
        return token

    def session(self, value: Optional[str]) -> Optional[tuple[str, dict]]:
        """(session id, record) for a cookie value, or None when unknown or expired."""
        if not value or not value.startswith(SESSION_PREFIX):
            return None
        found = self.service.tokens.verify(value, "session")
        if not found or found[1].get("user_id") not in self.users:
            return None
        self.seen(found[1]["user_id"])
        return found

    def end_session(self, session_id: str) -> bool:
        return self.service.tokens.revoke(session_id)

    def sessions(self, user_id: str) -> list[tuple[str, dict]]:
        return [(i, r) for i, r in self.service.tokens.list("session", user_id)
                if not (r.get("data") or {}).get("reset")]

    def csrf(self, session_id: str) -> str:
        return self.service.sign("csrf:session:" + session_id)

    # ------------------------------------------------------------ one-time sign-in codes (recovery)

    def new_signin_code(self, user_id: str) -> tuple[str, float]:
        """A 6-digit code for the user's own sign-in in a browser: once, for 5 minutes."""
        self._require(user_id)
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = self.clock() + SIGNIN_CODE_SECONDS
        self.signin_codes[user_id] = {"hash": _sha(code), "expires": expires, "tries": 0}
        return code, expires

    def use_signin_code(self, username: str, code: str) -> Optional[str]:
        """The user id when the code is right (the code is used up), else None."""
        found = self.find(username)
        entry = self.signin_codes.get(found[0]) if found else None
        given = _sha((code or "").strip())
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
                logger.warning(f"Users: the one-time code for {found[1]['username']} stopped working after "
                               f"{SIGNIN_CODE_TRIES} wrong tries")
            return None
        self.signin_codes.pop(found[0], None)
        return found[0]
