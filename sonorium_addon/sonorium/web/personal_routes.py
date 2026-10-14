"""
The personal channel (docs/APP_API.md, section 7.11): one hidden channel per
account, heard only on that account's own devices (the app's headphone
button). add_personal_routes() adds its routes to the /api router that
create_api_router() builds; each has its permission in
sonorium/auth/permissions.py.

    GET    /api/me/channel          ch.view    the channel (made on first use) + "personal": true
    PUT    /api/me/channel          ch.change  theme, preset, sequence, playlist, volume, announcements, name
    POST   /api/me/channel/listen   ch.play    this device listens: {stream_path, listen_path, ...}
    DELETE /api/me/channel/listen   ch.play    this device stops listening (only this one)

The account (account_of):

    a signed-in user, or a linked phone or browser of a user   "user:<user id>"
    accounts loaded, nobody signed in (or a key)                refused (401)
    no accounts loaded: Home Assistant's sidebar                "ha:<HA user id>" (each HA user their own)
    no accounts: anyone else (local, BYPASS, phones, keys)      "local" (one shared personal channel)

The device (device_of): a linked phone's id, else the browser session, else
the key, else the caller's address.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, ValidationError

from sonorium.obs import logger

SPEAKER_FIELDS = ("speaker_group_id", "adhoc_selection")


def _principal(request: Request):
    from sonorium.auth.middleware import principal_of
    return principal_of(request)


def _accounts_on(request: Request) -> bool:
    """Users (accounts) have loaded: a caller without an account has no personal channel."""
    service = (getattr(request, "scope", None) or {}).get("state", {}).get("auth_service")
    authenticators = getattr(service, "authenticators", None) or {}
    return "users" in authenticators


def account_of(principal, accounts_on: bool) -> Optional[str]:
    """The account a caller's personal channel belongs to, or None (no personal channel for it)."""
    if principal is None or principal.kind in ("anonymous", "open"):
        return None
    if principal.user_id:
        return f"user:{principal.user_id}"
    if accounts_on:
        return None
    ha_id = (principal.ha_user or {}).get("id") if principal.kind == "ingress" else None
    if ha_id:
        return f"ha:{ha_id}"
    return "local"


def audience_of(request: Request) -> Optional[str]:
    """The account whose personal channel's events this caller's event stream gets (None: none)."""
    try:
        return account_of(_principal(request), _accounts_on(request))
    except Exception:
        return None


def device_of(principal, request: Request) -> str:
    """Who listens, counted once: the linked phone, else the browser session, else the key, else the address."""
    if principal.device_id:
        return f"device:{principal.device_id}"
    if principal.session_id:
        return f"session:{principal.session_id}"
    if principal.key_id:
        return f"key:{principal.key_id}"
    client = request.client.host if request.client else "unknown"
    return f"{principal.kind}:{client}"


class PersonalChannelUpdate(BaseModel):
    """PUT /api/me/channel: only the fields sent change."""
    theme_id: Optional[str] = None
    preset_id: Optional[str] = None  # a preset id, "" for none, or "seq:<sequence_id>" to run a sequence
    sequence_id: Optional[str] = None  # run this sequence of the theme; null stops the sequence
    playlist_id: Optional[str] = None  # play this playlist; null stops the playlist
    custom_name: Optional[str] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)
    announcements: Optional[bool] = None


def add_personal_routes(router, *, session_manager, to_response) -> None:
    """The personal channel's routes, on create_api_router()'s /api router."""

    def caller(request: Request):
        principal = _principal(request)
        account = account_of(principal, _accounts_on(request))
        if account is None:
            raise HTTPException(status_code=401, detail="Sign in to use your personal channel", headers=None)
        return principal, account

    def answer(session, principal, request: Request) -> dict:
        body = to_response(session, session_manager).model_dump()
        listeners = session_manager.personal_listeners(session.id)
        body.update(personal=True, devices=len(listeners),
                    this_device=device_of(principal, request) in listeners)
        return body

    @router.get("/me/channel")
    async def get_personal_channel(request: Request) -> dict:
        """The caller's personal channel (made on first use)."""
        principal, account = caller(request)
        return answer(session_manager.personal_channel(account), principal, request)

    @router.put("/me/channel")
    async def update_personal_channel(request: Request) -> dict:
        """Change the personal channel: the same fields as a channel, no speakers. It changes live."""
        from sonorium.web.app_routes import _json_or_empty
        principal, account = caller(request)
        data = await _json_or_empty(request)
        if any(data.get(k) not in (None, {}, []) for k in SPEAKER_FIELDS):
            from sonorium.core.session_manager import PERSONAL_NO_SPEAKERS
            raise HTTPException(status_code=400, detail=PERSONAL_NO_SPEAKERS)
        try:
            change = PersonalChannelUpdate(**{k: v for k, v in data.items() if k not in SPEAKER_FIELDS})
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False))
        sent = change.model_fields_set
        session = session_manager.personal_channel(account)
        sid = session.id
        try:
            session_manager.update(sid, theme_id=change.theme_id, preset_id=change.preset_id,
                                   custom_name=change.custom_name if change.custom_name else None,
                                   volume=change.volume)
            if "sequence_id" in sent:
                if change.sequence_id:
                    session_manager.start_sequence(sid, change.sequence_id)
                else:
                    session_manager.stop_sequence(sid)
            if "playlist_id" in sent:
                if change.playlist_id:
                    session_manager.set_playlist(sid, change.playlist_id)
                else:
                    session_manager.clear_playlist(sid)
        except KeyError:
            raise HTTPException(status_code=404, detail="Not found")
        except LookupError as e:  # an unknown sequence or playlist
            raise HTTPException(status_code=404, detail=str(e).strip("'\""))
        except ValueError as e:  # it can't run (no presets, no theme, no entries)
            raise HTTPException(status_code=409, detail=str(e))
        if change.announcements is not None and change.announcements != session.announcements:
            session_manager.set_announcements(sid, change.announcements)
        return answer(session, principal, request)

    @router.post("/me/channel/listen")
    async def listen_personal_channel(request: Request) -> dict:
        """
        This device listens to the personal channel: play listen_path (HTTP
        port). Optional {"hold_seconds": 0-300} (or ?hold_seconds=): how long
        this device counts after its stream closed (default 15).
        """
        from sonorium.auth import stream_links
        from sonorium.web.app_routes import hold_seconds_of
        principal, account = caller(request)
        hold = await hold_seconds_of(request)
        session = session_manager.personal_channel(account)
        device = device_of(principal, request)
        try:
            channel, tag = session_manager.listen_personal(session.id, device, hold)
        except KeyError:
            raise HTTPException(status_code=404, detail="Not found")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if channel is None:
            raise HTTPException(status_code=409, detail="No free channel")
        listeners = session_manager.personal_listeners(session.id)
        logger.debug(f"Personal channel {session.id}: {device.split(':', 1)[0]} listens")
        return {"session_id": session.id, "personal": True, "channel_id": channel.id,
                "stream_path": channel.stream_path,
                # with a stream link while Require login is on (auth/stream_links.py)
                "listen_path": stream_links.signed(f"{channel.stream_path}?local=1&listener={tag}"),
                "hold_seconds": listeners[device]["hold"], "devices": len(listeners)}

    @router.delete("/me/channel/listen")
    async def unlisten_personal_channel(request: Request) -> dict:
        """This device stopped listening (other devices keep listening; the last one stops the mix)."""
        principal, account = caller(request)
        session = session_manager.personal_channel(account)
        removed = session_manager.unlisten_personal(session.id, device_of(principal, request))
        return {"status": "ok", "removed": removed,
                "devices": len(session_manager.personal_listeners(session.id))}
