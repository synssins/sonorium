"""
Virtual speakers (demo branch only): every account has one speaker, named
after the account, that plays wherever that user listens (a browser signed
in as the user, or the app linked to the user).

    speaker id      "virtual:<user id>"  (type "virtual", source ["virtual"])
    made / renamed  with the account (sync() every SYNC_SECONDS, and on every call here)
    removed         with the account: out of channels, speaker groups and Settings › Speakers

A virtual speaker is a speaker like any other: the Use switch, a name, an
area, the Offset, speaker groups, channels, linking. The session manager
plays it through this module (add_speaker_kind), never through the media
controller.

Listening: a client of the user claims the speaker (POST /api/me/speaker/claim
with its own client id). The last claim wins: an earlier client's stream ends
and its status says this_client: false. While a channel that has the speaker
plays, the claiming client streams GET /stream/virtual/<token> (the channel's
broadcast, the same pipeline as a speaker's stream, at the speaker's volume:
the channel's volume with the speaker's Offset, and its own volume and mute).
The speaker is always listed online, so it can be picked for channels like any
speaker (pickers leave offline speakers out). Whether someone listens is the
status's "listening": its claiming client was heard from (a status poll with
its client id, or an open stream) within STALE_SECONDS. While nobody listens,
channels play on without it.

API (docs/APP_API.md, "Virtual speakers"):

    GET    /api/me/speaker?client=<id>          ch.view   status (heartbeat for the claiming client)
    POST   /api/me/speaker/claim  {client, kind} ch.play   this client listens from now on
    DELETE /api/me/speaker/claim?client=<id>    ch.play   this client stops listening
    GET    /stream/virtual/<token>              public    MP3 while playing (the token is the claim's secret)
    GET    /api/auth/me                         "virtual_speaker": the status (null without an account)

Only the caller's own account is ever reached: no route takes a speaker or user id.
Limits (limits.py): the claim and status routes have the request body limit;
a stream needs no stream link (its token is its secret) but counts against
streams_per_address and streams_limit (429), and a newer stream on the same
claim (a reconnect) ends the older one.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from sonorium.obs import logger

OWNER = "virtual_speakers"         # its key in the auth service (routes, /api/auth/me, UI bundle)
PREFIX = "virtual:"
SPEAKER_TYPE = "virtual"
ADDRESS = "Virtual"                # the address column in Settings › Speakers (proposed wording)
STATE_FILE = "virtual_speakers.json"
SYNC_SECONDS = 2.0
STALE_SECONDS = 15.0               # a claiming client not heard from for this long: offline
POLL_SECONDS = 3                   # how often clients should ask for the status
CHECK_SECONDS = 0.5                # how often an open stream checks it may go on
CLIENT_ID = re.compile(r"^[A-Za-z0-9._:-]{8,64}$")
CLIENT_KINDS = ("web", "app")
TOKEN = re.compile(r"^[0-9a-f]{32}$")
NO_TEST = "A virtual speaker plays only in a channel"  # proposed wording
NO_SPEAKER = "No virtual speaker for this sign-in"      # proposed wording

PERMISSIONS = """
GET     /api/me/speaker                     ch.view
POST    /api/me/speaker/claim               ch.play
DELETE  /api/me/speaker/claim               ch.play
GET     /stream/virtual/{token}             public
"""


def speaker_id_for(user_id: str) -> str:
    return f"{PREFIX}{user_id}"


def user_of(speaker_id) -> Optional[str]:
    if isinstance(speaker_id, str) and speaker_id.startswith(PREFIX):
        return speaker_id[len(PREFIX):] or None
    return None


@dataclass
class Claim:
    """The client that listens to a user's virtual speaker (the last one that asked)."""
    client: str
    kind: str
    token: str = field(default_factory=lambda: secrets.token_hex(16))
    seen: float = 0.0
    streams: int = 0
    opened: int = 0     # streams opened on this claim; only the newest plays (a reconnect replaces the old one)


class VirtualSpeakers:
    """One speaker per account, the claims, and the streams."""

    def __init__(self, session_manager, accounts: Callable[[], Optional[dict]], folder: Optional[Path] = None,
                 clock: Callable[[], float] = time.monotonic):
        """accounts(): {user id: account dict with "username"}, or None while accounts aren't loaded."""
        self.sm = session_manager
        self._accounts = accounts
        self.clock = clock
        self._lock = threading.RLock()
        self._claims: dict[str, Claim] = {}          # user id -> the claim
        self._live: dict[str, dict] = {}             # speaker id -> {"volume", "muted"} (PUT /api/speakers/...)
        self._task = None
        if folder is not None:
            from sonorium.auth.secrets import JsonFile
            self._file = JsonFile(Path(folder) / STATE_FILE)
        else:
            self._file = None
            self._memory: dict = {}
        if session_manager is not None and hasattr(session_manager, "add_speaker_kind"):
            session_manager.add_speaker_kind(self)
        registry = self._registry()
        if registry is not None and hasattr(registry, "add_speaker_source"):
            registry.add_speaker_source(self.speaker_entries)
            # The speakers were listed before this source was added: list them now, not at the next change
            self._refresh_registry()

    # --- Storage: the accounts that have a speaker (to notice one removed while Sonorium was off) ---

    def _known(self) -> dict:
        if self._file is None:
            return self._memory.setdefault("known", {})
        return self._file.section("known")

    def _save(self) -> None:
        if self._file is not None:
            self._file.save()

    def _registry(self):
        return getattr(self.sm, "registry", None)

    def _settings(self):
        return getattr(getattr(self.sm, "state", None), "settings", None)

    # --- Accounts -> speakers ---

    def accounts(self) -> Optional[dict]:
        """{user id: name} of every account, or None while accounts aren't loaded."""
        try:
            users = self._accounts()
        except Exception as e:
            logger.debug(f"Virtual speakers: accounts not read: {e}")
            return None
        if users is None:
            return None
        return {uid: str(u.get("username") or uid) for uid, u in list(users.items()) if isinstance(u, dict)}

    def sync(self) -> bool:
        """Speakers follow the accounts (made, renamed, removed); True when anything changed."""
        accounts = self.accounts()
        changed = False
        with self._lock:
            if accounts is not None:
                known = self._known()
                for uid, name in accounts.items():
                    if uid not in known:
                        known[uid] = name
                        self._enable(speaker_id_for(uid))
                        logger.info(f"Virtual speakers: '{name}' added")
                        changed = True
                    elif known[uid] != name:
                        known[uid] = name
                        changed = True
                for uid in [u for u in known if u not in accounts]:
                    logger.info(f"Virtual speakers: '{known[uid]}' removed with its account")
                    known.pop(uid, None)
                    self._forget(uid)
                    changed = True
                if changed:
                    self._save()
        if changed:
            self._refresh_registry()
        return changed

    def _enable(self, speaker_id: str) -> None:
        """A new speaker is switched on (an empty list already means every speaker is on)."""
        settings = self._settings()
        if settings is None or settings.speaker_enabled(speaker_id):
            return
        settings.enabled_speakers.append(speaker_id)
        self._save_state()

    def _forget(self, user_id: str) -> None:
        """The account is gone: its speaker leaves Settings, channels and speaker groups."""
        sid = speaker_id_for(user_id)
        self._claims.pop(user_id, None)
        self._live.pop(sid, None)
        state = getattr(self.sm, "state", None)
        settings = self._settings()
        if settings is not None:
            while sid in settings.enabled_speakers:
                settings.enabled_speakers.remove(sid)
            settings.speaker_settings.pop(sid, None)
        holders = []
        if state is not None:
            holders.extend(s.adhoc_selection for s in getattr(state, "sessions", {}).values() if s.adhoc_selection)
            holders.extend(getattr(state, "speaker_groups", {}).values())
        for holder in holders:
            for name in ("include_speakers", "exclude_speakers"):
                ids = getattr(holder, name, None)
                while ids and sid in ids:
                    ids.remove(sid)
        self._save_state()

    def _save_state(self) -> None:
        state = getattr(self.sm, "state", None)
        if state is not None and hasattr(state, "save"):
            try:
                state.save()
            except Exception as e:
                logger.warning(f"Virtual speakers: settings not saved: {e}")

    def _refresh_registry(self) -> None:
        registry = self._registry()
        if registry is not None and hasattr(registry, "merge_extra_speakers"):
            try:
                registry.merge_extra_speakers()
            except Exception as e:
                logger.debug(f"Virtual speakers: speakers not refreshed: {e}")

    def speaker_entries(self) -> list[dict]:
        """For the registry (add_speaker_source): one speaker per account."""
        accounts = self.accounts()
        if accounts is None:  # not loaded (yet): the remembered ones, offline
            accounts = dict(self._known())
        return [{"entity_id": speaker_id_for(uid), "name": name, "type": SPEAKER_TYPE,
                 "online": True, "source": [SPEAKER_TYPE], "address": ADDRESS}
                for uid, name in accounts.items()]

    def name_of(self, user_id: str) -> str:
        """The speaker's name as listed (a rename in Settings › Speakers counts)."""
        sid = speaker_id_for(user_id)
        registry = self._registry()
        if registry is not None and hasattr(registry, "get_speaker_name"):
            try:
                name = registry.get_speaker_name(sid)
                if name and name != sid:
                    return name
            except Exception:
                pass
        return (self.accounts() or {}).get(user_id) or self._known().get(user_id) or user_id

    # --- Claims: the last client to ask listens ---

    def has_speaker(self, user_id: Optional[str]) -> bool:
        return bool(user_id) and user_id in (self.accounts() or {})

    def listening(self, user_id: str) -> bool:
        """Someone of the user listens: the claiming client was heard from lately, or streams now."""
        claim = self._claims.get(user_id)
        return bool(claim and (claim.streams > 0 or self.clock() - claim.seen < STALE_SECONDS))

    def claim(self, user_id: str, client: str, kind: str = "app") -> dict:
        """This client listens from now on (an earlier client's stream ends). Asking again keeps its stream."""
        if not self.has_speaker(user_id):
            raise LookupError(NO_SPEAKER)
        if not isinstance(client, str) or not CLIENT_ID.match(client):
            raise ValueError("client: 8 to 64 letters, digits, '.', '_', ':' or '-'")
        kind = kind if kind in CLIENT_KINDS else "app"
        with self._lock:
            current = self._claims.get(user_id)
            if current is None or current.client != client:
                current = self._claims[user_id] = Claim(client=client, kind=kind)
            current.kind = kind
            current.seen = self.clock()
        self.sync()
        return self.status(user_id, client)

    def release(self, user_id: str, client: Optional[str]) -> bool:
        """This client stops listening (only when it is the one listening)."""
        with self._lock:
            current = self._claims.get(user_id)
            if current is None or current.client != client:
                return False
            self._claims.pop(user_id, None)
        self.sync()
        return True

    def playing_session(self, user_id: str):
        """The playing channel that has the user's speaker (its first, in channel order), or None."""
        sid = speaker_id_for(user_id)
        lister = getattr(self.sm, "list", None)
        for session in (lister() if lister is not None else []):
            if not session.is_playing:
                continue
            try:
                if sid in self.sm.get_resolved_speakers(session):
                    return session
            except Exception:
                continue
        return None

    def status(self, user_id: Optional[str], client: Optional[str] = None) -> Optional[dict]:
        """What GET /api/me/speaker answers (None: the caller has no account here)."""
        if not self.has_speaker(user_id):
            return None
        claim = self._claims.get(user_id)
        this = bool(claim and client and claim.client == client)
        if this:
            claim.seen = self.clock()  # the claiming client's poll is its heartbeat
        session = self.playing_session(user_id)
        sid = speaker_id_for(user_id)
        listening = self.listening(user_id)
        return {
            "id": sid, "name": self.name_of(user_id),
            "playing": session is not None,
            "channel": {"id": session.id, "name": session.name} if session is not None else None,
            "listening": listening,
            "listener_kind": claim.kind if claim and listening else None,
            "this_client": this,
            "stream_path": f"/stream/virtual/{claim.token}" if this else None,
            "status_url": "/api/me/speaker", "claim_url": "/api/me/speaker/claim",
            "poll_seconds": POLL_SECONDS,
        }

    # --- The speaker kind (SessionManager.add_speaker_kind) ---

    def claims(self, speaker_id) -> bool:
        return user_of(speaker_id) is not None

    async def start(self, session, speaker_ids, take_over=None) -> None:
        """Nothing to open: the listening client asks for the status and streams."""
        return None

    async def stop(self, session, speaker_ids=None) -> None:
        """Open streams notice it themselves (they check their channel still plays the speaker)."""
        return None

    def conflicts(self, session, speaker_ids) -> list:
        return []

    async def test(self, speaker_id: str) -> dict:
        from sonorium.core.audio_outputs import OutputBusy
        if not self.has_speaker(user_of(speaker_id)):
            raise LookupError("Speaker not found")
        raise OutputBusy(NO_TEST)

    # --- Volume and mute of the speaker itself (core/speaker_live.py) ---

    def set_volume(self, speaker_id: str, volume: int) -> bool:
        if not self.has_speaker(user_of(speaker_id)):
            return False
        self._live.setdefault(speaker_id, {})["volume"] = max(0, min(100, int(volume)))
        return True

    def set_muted(self, speaker_id: str, muted: bool) -> bool:
        if not self.has_speaker(user_of(speaker_id)):
            return False
        self._live.setdefault(speaker_id, {})["muted"] = bool(muted)
        return True

    def gain(self, user_id: str, session) -> float:
        """The stream's level (0..1): the channel's volume with the Offset, the speaker's volume and mute."""
        from sonorium.core.speaker_settings import offset_volume
        sid = speaker_id_for(user_id)
        settings = self._settings()
        offset = ((settings.speaker_settings.get(sid) or {}).get("volume_offset", 0)) if settings is not None else 0
        level = offset_volume(session.volume / 100.0, offset)
        live = self._live.get(sid) or {}
        if live.get("muted"):
            return 0.0
        return level * (live.get("volume", 100) / 100.0)

    # --- Streams ---

    def _claim_by_token(self, token: str) -> tuple[Optional[str], Optional[Claim]]:
        for uid, claim in list(self._claims.items()):
            if secrets.compare_digest(claim.token, token):
                return uid, claim
        return None, None

    def still_on(self, user_id: str, claim: Claim, session_id: str, number: Optional[int] = None) -> bool:
        """A stream goes on: its claim is the current one, it is the claim's newest, and its channel still plays the speaker."""
        if self._claims.get(user_id) is not claim or (number is not None and number != claim.opened):
            return False
        session = self.playing_session(user_id)
        return session is not None and session.id == session_id

    def open_stream(self, token: str, release: Optional[Callable[[], None]] = None):
        """
        The MP3 stream for a claim's token (release() is called once when it ends).
        LookupError: no such claim, or nothing plays.
        """
        if not isinstance(token, str) or not TOKEN.match(token):
            raise LookupError("Not found")
        user_id, claim = self._claim_by_token(token)
        if claim is None:
            raise LookupError("Not found")
        session = self.playing_session(user_id)
        channel = self.sm.channel_of(session.id) if session is not None else None
        if channel is None:
            raise LookupError("Not playing")
        claim.opened += 1
        return _stream_class()(channel, self, user_id, claim, session, release)


_STREAM_CLASS = None


def _stream_class():
    """VirtualStream: the channel's own speaker stream (core/channel.py), at the speaker's level."""
    global _STREAM_CLASS
    if _STREAM_CLASS is not None:
        return _STREAM_CLASS
    import numpy as np
    from sonorium.core.channel import ChannelStream

    class VirtualStream(ChannelStream):
        def __init__(self, channel, owner: VirtualSpeakers, user_id: str, claim: Claim, session, release=None):
            self.owner, self.user_id, self.claim, self.session = owner, user_id, claim, session
            self.number, self._release = claim.opened, release
            self._level = None
            super().__init__(channel, local=False)  # a speaker: the speakers-muted switch applies

        def _apply_mute(self, chunk):
            chunk = super()._apply_mute(chunk)
            target = self.owner.gain(self.user_id, self.session)
            start = target if self._level is None else self._level
            self._level = target
            if start == target == 1.0:
                return chunk
            ramp = np.linspace(start, target, chunk.shape[-1], dtype=np.float32)
            return (chunk.astype(np.float32) * ramp).astype(chunk.dtype)

        def __iter__(self):
            owner, claim = self.owner, self.claim
            claim.streams += 1
            inner = super().__iter__()
            next_check = 0.0
            try:
                for data in inner:
                    now = time.monotonic()
                    if now >= next_check:
                        next_check = now + CHECK_SECONDS
                        if not owner.still_on(self.user_id, claim, self.session.id, self.number):
                            break
                    yield data
            finally:
                inner.close()
                claim.streams = max(0, claim.streams - 1)
                claim.seen = owner.clock()
                release, self._release = self._release, None
                if release is not None:
                    release()

    _STREAM_CLASS = VirtualStream
    return VirtualStream


# --- The routes and the auth service hooks ---

class _Me:
    """/api/auth/me gets "virtual_speaker" (registered like a sign-in plugin, with only a me() hook)."""

    def __init__(self, speakers: VirtualSpeakers):
        self.speakers = speakers

    def me(self, ctx, principal) -> dict:
        return {"virtual_speaker": self.speakers.status(getattr(principal, "user_id", None))}


def create_router(speakers: VirtualSpeakers):
    router = APIRouter(tags=["virtual speakers"])

    def principal(request: Request):
        from sonorium.auth.middleware import principal_of
        return principal_of(request)

    def user(request: Request) -> str:
        uid = principal(request).user_id
        if not speakers.has_speaker(uid):
            raise HTTPException(status_code=404, detail=NO_SPEAKER)
        return uid

    async def body_of(request: Request) -> dict:
        try:
            data = await request.json()
        except Exception:
            data = None
        return data if isinstance(data, dict) else {}

    @router.get("/api/me/speaker")
    async def get_speaker(request: Request, client: Optional[str] = None) -> dict:
        """The caller's virtual speaker; ?client=<id> also tells whether that client is the one listening."""
        return speakers.status(user(request), client)

    @router.post("/api/me/speaker/claim")
    async def claim_speaker(request: Request) -> dict:
        """{"client": "<id>", "kind": "app"|"web"}: this client listens from now on (the last one wins)."""
        uid = user(request)
        data = await body_of(request)
        client = data.get("client") or request.query_params.get("client")
        try:
            return speakers.claim(uid, client, str(data.get("kind") or request.query_params.get("kind") or "app"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.delete("/api/me/speaker/claim")
    async def release_speaker(request: Request, client: Optional[str] = None) -> dict:
        """This client stops listening (nothing changes when another client listens)."""
        uid = user(request)
        if client is None:
            client = (await body_of(request)).get("client")
        released = speakers.release(uid, client)
        return {"released": released, **speakers.status(uid, client)}

    @router.get("/stream/virtual/{token}")
    async def stream_virtual(token: str, request: Request):
        """
        The speaker's MP3 stream for the listening client (404 when its claim is gone or nothing plays).
        Its token is its own secret (no ?t= stream link); it counts against the stream limits
        (streams_per_address, streams_limit: 429) like any stream, by the caller's address
        (behind a trusted proxy: the forwarded one).
        """
        from sonorium import limits
        from sonorium.auth import stream_links
        client = request.client.host if request.client else "unknown"
        slots = stream_links.STREAMS
        if not slots.take(client, limits.count(limits.STREAMS_PER_ADDRESS), limits.count(limits.STREAMS)):
            raise HTTPException(status_code=429, detail="Too many streams are open. Close one and try again.")
        try:
            stream = speakers.open_stream(token, release=lambda: slots.give(client))
        except LookupError as e:
            slots.give(client)
            raise HTTPException(status_code=404, detail=str(e).strip("'\""))
        return StreamingResponse(stream, media_type="audio/mpeg")

    return router


def install(service, session_manager) -> Optional[VirtualSpeakers]:
    """At startup, once the channels exist: the speakers, their routes, /api/auth/me and the web UI part."""
    from sonorium.core import speaker_live

    def users():
        accounts = service.plugin_state.get("users")
        return accounts.users if accounts is not None else None

    folder = getattr(getattr(getattr(session_manager, "state", None), "state_file", None), "parent", None)
    speakers = VirtualSpeakers(session_manager, users, folder)
    speaker_live.LOCAL_HANDLERS[PREFIX] = speakers
    service.add_routes(OWNER, create_router(speakers), PERMISSIONS)
    service.register_authenticator(OWNER, _Me(speakers))
    service.register_ui(OWNER, Path(__file__).parent / "web" / "static" / "virtual", js="virtual_speakers.js")
    speakers.sync()
    try:
        loop = asyncio.get_running_loop()
        speakers._task = loop.create_task(_sync_loop(speakers))
    except RuntimeError:
        pass
    logger.info(f"Virtual speakers: on ({len(speakers.accounts() or {})} accounts)")
    return speakers


async def _sync_loop(speakers: VirtualSpeakers) -> None:
    while True:
        await asyncio.sleep(SYNC_SECONDS)
        try:
            speakers.sync()
        except Exception as e:
            logger.debug(f"Virtual speakers: sync failed: {e}")
