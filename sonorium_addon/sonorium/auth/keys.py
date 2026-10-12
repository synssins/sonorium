"""
Keys for devices, programs and people (Settings > Keys): a core feature with
a switch in Settings > Advanced ("keys", runtime.FEATURES, on out of the box).

A key ("sk_...") allows only the abilities checked on it (permissions.AREAS:
channels, themes and presets, playlists and sequences, speakers, triggers,
events, plugin pages, settings), optionally for some channels only. Keys
never manage users or keys. What a key may do can be changed after it's made
and takes effect on its next request; revoking it does too.

Plugin triggers and the event stream accept a key with "Run triggers" or
"Read events"; the older per-plugin, per-trigger and event-stream tokens keep
working whether keys are on or off.

Keys are core tokens (kind "key"), kept as hashes in security.json.

Switched off (set_enabled(service, False)): the /api/keys routes answer 404
and no key is accepted; the keys stay stored and work again once switched
back on. The switch applies at once.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from sonorium.auth import permissions as P
from sonorium.auth.principal import Principal, Reject
from sonorium.auth.routes import json_body
from sonorium.obs import logger

OWNER = "keys"   # in the permission registry and the authenticators (never a plugin id: those can't clash)
KEY_PREFIX = "sk_"
EXPIRY_CHOICES = {"7d": 7, "30d": 30, "90d": 90, "1y": 365, "never": None}

PERMISSIONS = """
GET     /api/keys               admin.keys
POST    /api/keys               admin.keys
PUT     /api/keys/{key_id}      admin.keys
DELETE  /api/keys/{key_id}      admin.keys
"""


class KeysAuth:
    def __init__(self, service):
        self.service = service

    def authenticate(self, ctx) -> Optional[Principal]:
        token = ctx.token
        if not token or not token.startswith(KEY_PREFIX):
            return None
        found = self.service.tokens.verify(token, "key")
        if not found:
            return Reject(401, {"detail": "This key is not valid. It may have expired or been revoked."})
        key_id, record = found
        data = record.get("data") or {}
        if self.service.user_blocked(data.get("created_by")):
            return Reject(401, {"detail": "The user who made this key is switched off. Make a new key."})
        return Principal("key", frozenset(P.clean_abilities(data.get("abilities"))), key_id=key_id,
                         channels=data.get("channels"))


class KeysOff:
    """Keys switched off: a key is refused (it stays stored), so it never falls through to other sign-in."""

    def authenticate(self, ctx) -> Optional[Principal]:
        if ctx.token and ctx.token.startswith(KEY_PREFIX):
            return Reject(401, {"detail": "Keys are off. Switch them on in Settings › Advanced."})
        return None


def key_row(service, key_id: str, record: dict) -> dict:
    data = record.get("data") or {}
    maker = service.describe_user(data.get("created_by"))
    abilities = P.clean_abilities(data.get("abilities"))
    return {"id": key_id, "name": record.get("name"), "abilities": abilities, "chips": P.ability_chips(abilities),
            "channels": data.get("channels"), "expires": record.get("expires"), "created": record.get("created"),
            "created_by": maker["username"] if maker else None, "last_used": record.get("last_used")}


def create_router(service) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["keys"])

    def p_of(request: Request):
        from sonorium.auth.middleware import principal_of
        return principal_of(request)

    def expiry(choice) -> Optional[float]:
        if choice not in EXPIRY_CHOICES:
            raise HTTPException(400, "Unknown expiry")
        days = EXPIRY_CHOICES[choice]
        return None if days is None else service.clock() + days * 86400

    def name_of(data: dict) -> str:
        name = str(data.get("name") or "").strip()
        if not name or len(name) > 60:
            raise HTTPException(400, "Give the key a name.")
        return name

    def channels_of(data: dict):
        channels = data.get("channels")
        if channels is None:
            return None
        if not isinstance(channels, list):
            raise HTTPException(400, "channels: a list of channel ids, or null for all channels")
        return [str(c) for c in channels if isinstance(c, (str, int))][:200]

    def key_record(key_id: str) -> dict:
        record = service.tokens.get(key_id)
        if record is None or record.get("kind") != "key":
            raise HTTPException(404, "No such key.")
        return record

    @router.get("/keys")
    async def list_keys():
        rows = [key_row(service, i, r) for i, r in service.tokens.list("key")]
        rows.sort(key=lambda r: r.get("created") or "")
        return {"keys": rows, "areas": P.catalogue(), "expiry": list(EXPIRY_CHOICES)}

    @router.post("/keys")
    async def make_key(request: Request):
        """A new key; its value is in this answer only."""
        data = await json_body(request)
        key_id, token = service.tokens.issue(
            "key", prefix=KEY_PREFIX, name=name_of(data), owner=OWNER, expires=expiry(data.get("expires", "90d")),
            data={"abilities": P.clean_abilities(data.get("abilities")), "channels": channels_of(data),
                  "created_by": p_of(request).user_id})
        logger.info(f"Keys: {service.tokens.get(key_id)['name']} made")
        return {"key": token, **key_row(service, key_id, service.tokens.get(key_id))}

    @router.put("/keys/{key_id}")
    async def edit_key(key_id: str, request: Request):
        """Change what a key may do, its name or expiry; takes effect on its next request."""
        record = key_record(key_id)
        data = await json_body(request)
        changes = {}
        if "name" in data:
            changes["name"] = name_of(data)
        if "expires" in data and data["expires"] != "keep":
            changes["expires"] = expiry(data["expires"])
        key_data = dict(record.get("data") or {})
        if "abilities" in data:
            key_data["abilities"] = P.clean_abilities(data["abilities"])
        if "channels" in data:
            key_data["channels"] = channels_of(data)
        changes["data"] = key_data
        service.tokens.update(key_id, **changes)
        logger.info(f"Keys: {record['name']} changed")
        return key_row(service, key_id, service.tokens.get(key_id))

    @router.delete("/keys/{key_id}")
    async def revoke_key(key_id: str):
        record = key_record(key_id)
        service.tokens.revoke(key_id)
        logger.info(f"Keys: {record['name']} revoked")
        return {"revoked": True}

    return router


def install(service) -> None:
    """At startup: the routes (in the app and the permission table) and keys accepted (switched on)."""
    service.add_routes(OWNER, create_router(service), PERMISSIONS)
    set_enabled(service, True)


def set_enabled(service, on: bool) -> None:
    """Settings > Advanced > Keys: applies at once (routes 404 and keys refused while off)."""
    service.authenticators.pop(OWNER, None)
    service.register_authenticator(OWNER, KeysAuth(service) if on else KeysOff())
    service.registry.set_active(OWNER, bool(on))


def enabled(service) -> bool:
    return isinstance(service.authenticators.get(OWNER), KeysAuth)
