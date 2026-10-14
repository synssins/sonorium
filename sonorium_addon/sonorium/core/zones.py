"""
Zones: channels that belong to a user, Child Mode, speaker homes and linked
channels. Off until an admin turns on Settings > Advanced > Zones; while off,
Sonorium behaves as if this file didn't exist (the session manager has no
policy).

Think of the house's speakers as rooms' radios:

- A channel is an open zone: anyone who may play channels may change it.
  Given an owner (a user, set by an admin), it's that user's zone: only the
  owner and admins change it. Channels that existed before Zones were turned
  on have no owner until an admin gives them one.
- Child Mode (per zone, set by an admin): only an admin changes its speakers.
  Its hours (also the admin's): outside them the owner can't play it or
  change what it plays; at the end, what the owner started fades out
  (KID_FADE_SECONDS), what an admin started keeps playing.
- A speaker can be in several channels, but plays for one at a time: a
  speaker in use by a playing channel can't be pulled into another. Its home
  is the channel it was added to first; the home can take it back even while
  the other plays (that one keeps playing on its other speakers). When the
  home is deleted, the next channel that has the speaker becomes its home. An
  admin can choose another home.
- An admin overrides all of this (alarms, announcements). Home Assistant
  (MQTT), plugins and Sonorium's own timers act as an admin.
- Links: an admin links channels to play one theme (and preset) or one
  playlist in all of them; each keeps its own volume. Unlinking (or leaving)
  puts a channel back to what it was playing. Whether a user may leave a link
  with their zone is set per user (Users & devices: Can leave a link); an
  admin can pull a channel back in.

zones.json (Sonorium's config folder):

    {"enabled": bool,
     "zones": {channel_id: {"owner": user_id|null, "kid_mode": bool,
                            "schedule": {"start": "HH:MM", "end": "HH:MM"}|null}},
     "homes": {speaker_id: [channel_id, ...]},     first = home
     "started_by": {channel_id: {"user_id": id|null, "admin": bool}},
     "links": {link_id: {"name", "channels": [...], "left": [...], "theme_id",
                         "preset_id", "playlist_id", "saved": {channel_id: {...}}}}}

Which channel plays a speaker right now is kept in memory (claims).
"""
from __future__ import annotations

import asyncio
import contextlib
import secrets
from contextvars import ContextVar
from datetime import datetime
from typing import Callable, Optional

from sonorium.auth.principal import current_caller
from sonorium.auth.secrets import JsonFile
from sonorium.core.channel_policy import ChannelRefused
from sonorium.obs import logger

ZONES_FILE = "zones.json"
KID_FADE_SECONDS = 30.0   # what a kid started fades out over this long when Child Mode's hours end
TICK_SECONDS = 20.0       # how often Child Mode's hours are checked

_SYSTEM: ContextVar[bool] = ContextVar("sonorium_zones_system", default=False)


def publish(event_type: str, data: dict) -> None:
    try:
        from sonorium.plugins import events
        events.publish(event_type, data)
    except Exception as e:  # events never get in the way
        logger.debug(f"Event {event_type} not published: {e}")


def minutes(text) -> Optional[int]:
    """'07:30' -> 450; None when it isn't a time."""
    try:
        h, m = str(text).split(":")
        h, m = int(h), int(m)
    except (TypeError, ValueError):
        return None
    return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None


def clean_schedule(value) -> Optional[dict]:
    """{"start": "HH:MM", "end": "HH:MM"} (end before start: over midnight), or None. ValueError when bad."""
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError("Hours need a start and an end.")
    start, end = minutes(value.get("start")), minutes(value.get("end"))
    if start is None or end is None or start == end:
        raise ValueError("Hours need a start and an end, like 07:00 and 20:00.")
    return {"start": f"{start // 60:02d}:{start % 60:02d}", "end": f"{end // 60:02d}:{end % 60:02d}"}


def in_hours(schedule: Optional[dict], now: datetime) -> bool:
    if not schedule:
        return True
    start, end, t = minutes(schedule["start"]), minutes(schedule["end"]), now.hour * 60 + now.minute
    return start <= t < end if start < end else (t >= start or t < end)


async def fade_out(session_manager, session, seconds: float) -> bool:
    """
    Fade a channel's mix to silence over `seconds` and wait for it. False if
    it was played again by someone else meanwhile (then it's faded back in).

    A minimal fade with the mixer's channel fade (Channel.fade_out); when the
    mixer's ramp API (framework-2) lands, this is the one place to call it.
    """
    channel = session_manager.channel_of(session)
    if channel is None or seconds <= 0 or channel.is_faded_out:
        return True
    channel.fade_out(seconds)
    loop = asyncio.get_running_loop()
    give_up = loop.time() + seconds + 1.0
    while not channel.is_faded_out and loop.time() < give_up and session.is_playing:
        await asyncio.sleep(0.25)
    return True


class Actor:
    """Who is changing a channel: a user, or an admin (also Home Assistant, plugins and Sonorium itself)."""
    __slots__ = ("user_id", "admin")

    def __init__(self, user_id: Optional[str], admin: bool):
        self.user_id, self.admin = user_id, admin

    @classmethod
    def current(cls) -> "Actor":
        p = current_caller()
        if p is None or _SYSTEM.get():
            return cls(None, True)
        if p.user_id and p.role != "admin":
            return cls(p.user_id, False)
        return cls(p.user_id, True)  # an admin, a key, or open access (nobody signed in)


class Zones:
    def __init__(self, accounts, file: Optional[JsonFile] = None, now: Callable[[], datetime] = datetime.now):
        self.accounts = accounts
        self.file = file or JsonFile(accounts.service.folder / ZONES_FILE)
        self.now = now
        data = self.file.data
        data.setdefault("enabled", False)
        for key in ("zones", "homes", "started_by", "links"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        self.sm = None                       # the SessionManager
        self.on_change = None                # async (session): Home Assistant's entities follow
        self.claims: dict[str, str] = {}     # speaker -> the channel playing it
        self._fading: set[str] = set()
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------ storage and switch

    def save(self) -> None:
        self.file.save()

    @property
    def data(self) -> dict:
        return self.file.data

    @property
    def enabled(self) -> bool:
        return bool(self.data.get("enabled"))

    def attach(self, session_manager, on_change=None) -> None:
        self.sm = session_manager
        self.on_change = on_change
        if session_manager is not None and self.enabled:
            self._install()

    def detach(self) -> None:
        if self.sm is not None and self.sm.policy is self:
            self.sm.policy = None
        self.claims.clear()
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def _install(self) -> None:
        self.sm.policy = self
        self.sync_homes()
        # What plays now holds its speakers (first come, in the order the channels were made)
        for session in self.sm.list():
            if session.is_playing:
                for sp in self.sm.selected_speakers(session):
                    self.claims.setdefault(sp, session.id)

    def set_enabled(self, on: bool) -> None:
        on = bool(on)
        if on == self.enabled:
            return
        self.data["enabled"] = on
        self.save()
        if self.sm is not None:
            if on:
                self._install()
            else:
                self.sm.policy = None
                self.claims.clear()
        logger.info(f"Zones are {'on' if on else 'off'}")
        publish("zone.switched", {"on": on})

    def start(self) -> None:
        """Child Mode's hours, checked every TICK_SECONDS (while Sonorium runs)."""
        if self._task is None:
            self._task = asyncio.get_event_loop().create_task(self._run())

    async def _run(self) -> None:
        token = _SYSTEM.set(True)
        try:
            while True:
                await asyncio.sleep(TICK_SECONDS)
                try:
                    await self.tick()
                except Exception as e:
                    logger.warning(f"Zones: checking Child Mode's hours failed: {e}")
        finally:
            _SYSTEM.reset(token)

    @contextlib.contextmanager
    def system(self):
        """Changes made by zones themselves (links): as an admin."""
        token = _SYSTEM.set(True)
        try:
            yield
        finally:
            _SYSTEM.reset(token)

    # ------------------------------------------------------------ lookups

    def zone(self, channel_id: str) -> dict:
        return self.data["zones"].get(channel_id) or {}

    def owner(self, channel_id: str) -> Optional[str]:
        owner = self.zone(channel_id).get("owner")
        return owner if owner and self.accounts.user(owner) else None

    def username(self, user_id: Optional[str]) -> str:
        user = self.accounts.user(user_id)
        return user["username"] if user else ""

    def can_leave_link(self, user_id: Optional[str]) -> bool:
        user = self.accounts.user(user_id)
        return bool(user and (user.get("role") == "admin" or user.get("can_leave_link")))

    def speaker_name(self, speaker_id: str) -> str:
        try:
            return self.sm.registry.get_speaker_name(speaker_id) or speaker_id
        except Exception:
            return speaker_id

    def home(self, speaker_id: str) -> Optional[str]:
        homes = self.data["homes"].get(speaker_id) or []
        return homes[0] if homes else None

    def holder(self, speaker_id: str, other_than: Optional[str] = None):
        """The playing channel that has the speaker now (other than `other_than`), or None."""
        channel_id = self.claims.get(speaker_id)
        if not channel_id or channel_id == other_than or self.sm is None:
            return None
        session = self.sm.get(channel_id)
        return session if session is not None and session.is_playing else None

    def link_of(self, channel_id: str) -> tuple[Optional[str], Optional[dict]]:
        for link_id, link in self.data["links"].items():
            if channel_id in link.get("channels", []):
                return link_id, link
        return None, None

    def linked(self, channel_id: str) -> Optional[dict]:
        """The link the channel plays in (not one it left)."""
        _, link = self.link_of(channel_id)
        return link if link is not None and channel_id not in link.get("left", []) else None

    # ------------------------------------------------------------ the policy (core/channel_policy.py)

    def _refuse(self, message: str, status: int = 403):
        raise ChannelRefused(message, status)

    def _may_change(self, actor: Actor, session) -> None:
        owner = self.owner(session.id)
        if owner and not actor.admin and actor.user_id != owner:
            self._refuse(f"{session.name} belongs to {self.username(owner)}.")

    def _in_hours(self, actor: Actor, session) -> None:
        zone = self.zone(session.id)
        if actor.admin or not zone.get("kid_mode") or in_hours(zone.get("schedule"), self.now()):
            return
        schedule = zone["schedule"]
        self._refuse(f"{session.name} plays from {schedule['start']} to {schedule['end']}.")

    def _not_linked(self, actor: Actor, session) -> None:
        link = self.linked(session.id)
        if link is not None and not actor.admin:
            self._refuse(f"{session.name} is linked: {link['name']}.", 409)

    def check(self, action: str, session, **details) -> None:
        actor = Actor.current()
        if action == "play":
            self._may_change(actor, session)
            self._in_hours(actor, session)
            self._claim(session, actor, strict=True)
            self.data["started_by"][session.id] = {"user_id": actor.user_id, "admin": actor.admin}
            self.save()
        elif action in ("stop", "delete"):
            self._may_change(actor, session)
        elif action in ("volume", "mute"):
            self._may_change(actor, session)
            self._in_hours(actor, session)
        elif action == "playback":
            self._may_change(actor, session)
            self._in_hours(actor, session)
            self._not_linked(actor, session)
        elif action == "update":
            if not any(details.get(k) is not None for k in ("theme", "preset", "name", "volume", "speakers")):
                return
            self._may_change(actor, session)
            if details.get("speakers") is not None:
                self._check_speakers(actor, session, details["speakers"])
            if details.get("theme") is not None or details.get("preset") is not None:
                self._not_linked(actor, session)
            if any(details.get(k) is not None for k in ("theme", "preset", "volume")):
                self._in_hours(actor, session)

    def _check_speakers(self, actor: Actor, session, speakers: list) -> None:
        zone = self.zone(session.id)
        if actor.admin:
            return
        if zone.get("kid_mode"):
            self._refuse(f"Only an admin can change the speakers of {session.name}.")
        for sp in set(speakers) - set(self.sm.selected_speakers(session)):
            other = self.holder(sp, session.id)
            if other is not None:
                self._refuse(f"{self.speaker_name(sp)} is in use by {other.name}.", 409)

    def _claim(self, session, actor: Actor, strict: bool = False) -> None:
        """The channel plays its speakers: free ones, and busy ones it may take (its home ones; an admin: all)."""
        self.sync_homes([session])  # e.g. a speaker group's speakers changed meanwhile
        selected = self.sm.selected_speakers(session)
        got, blocked = 0, None
        for sp in selected:
            other = self.holder(sp, session.id)
            if other is not None:
                home = self.home(sp) == session.id
                if not (home or actor.admin):
                    blocked = blocked or (sp, other)
                    continue
                logger.info(f"Zones: {session.name} takes {self.speaker_name(sp)} from {other.name}")
                publish("zone.speaker_taken", {"channel_id": session.id, "name": session.name, "speaker_id": sp,
                                               "from_channel_id": other.id, "by": "home" if home else "admin"})
            self.claims[sp] = session.id
            got += 1
        if strict and selected and not got and blocked:
            sp, other = blocked
            self._refuse(f"{self.speaker_name(sp)} is in use by {other.name}.", 409)

    def speakers(self, session, speaker_ids: list) -> list:
        """What the channel plays on: its speakers less those another playing channel has."""
        return [sp for sp in speaker_ids if self.holder(sp, session.id) is None]

    def created(self, session) -> None:
        self.sync_homes([session])

    def changed(self, session) -> None:
        self.sync_homes([session])
        if session.is_playing:
            selected = set(self.sm.selected_speakers(session))
            for sp, channel_id in list(self.claims.items()):
                if channel_id == session.id and sp not in selected:
                    del self.claims[sp]
            self._claim(session, Actor.current())

    def released(self, session) -> None:
        for sp, channel_id in list(self.claims.items()):
            if channel_id == session.id:
                del self.claims[sp]

    def deleted(self, session) -> None:
        self.released(session)
        channel_id = session.id
        self.data["zones"].pop(channel_id, None)
        self.data["started_by"].pop(channel_id, None)
        for sp, homes in list(self.data["homes"].items()):
            if channel_id not in homes:
                continue
            was_home = homes[0] == channel_id
            homes.remove(channel_id)
            if not homes:
                del self.data["homes"][sp]
            if was_home:
                self._home_event(sp, "deleted")
        for link_id, link in list(self.data["links"].items()):
            if channel_id in link["channels"]:
                link["channels"].remove(channel_id)
                link["left"] = [c for c in link.get("left", []) if c != channel_id]
                link.get("saved", {}).pop(channel_id, None)
                if not link["channels"]:
                    del self.data["links"][link_id]
                    publish("link.removed", {"link_id": link_id, "name": link["name"]})
        self.save()

    # ------------------------------------------------------------ homes

    def _home_event(self, speaker_id: str, reason: str) -> None:
        home = self.home(speaker_id)
        session = self.sm.get(home) if home and self.sm is not None else None
        logger.info(f"Zones: {self.speaker_name(speaker_id)}'s home is "
                    f"{session.name if session else 'no channel'} ({reason})")
        publish("zone.home_changed", {"speaker_id": speaker_id, "channel_id": home,
                                      "name": session.name if session else None, "reason": reason})

    def sync_homes(self, sessions=None) -> None:
        """Each speaker's channels, in the order they got it; channels that no longer have it drop out."""
        if self.sm is None:
            return
        homes = self.data["homes"]
        everything = sessions is None
        sessions = sorted(self.sm.list() if everything else sessions, key=lambda s: s.created_at or "")
        has = {s.id: set(self.sm.selected_speakers(s)) for s in sessions}
        known = {s.id for s in self.sm.list()}
        changed = False
        for sp, channels in list(homes.items()):
            keep = [c for c in channels if c in known and (c not in has or sp in has[c])]
            if keep != channels:
                first = channels[0] if channels else None
                homes[sp] = keep
                changed = True
                if not keep:
                    del homes[sp]
                if first != (keep[0] if keep else None):
                    self._home_event(sp, "deleted" if first not in known else "removed")
        for s in sessions:
            for sp in has[s.id]:
                channels = homes.setdefault(sp, [])
                if s.id not in channels:
                    channels.append(s.id)
                    changed = True
        if changed:
            self.save()

    def set_home(self, speaker_id: str, channel_id: str) -> None:
        self.sync_homes()
        channels = self.data["homes"].get(speaker_id) or []
        if channel_id not in channels:
            raise ChannelRefused("That channel doesn't have this speaker.", 409)
        channels.remove(channel_id)
        channels.insert(0, channel_id)
        self.save()
        self._home_event(speaker_id, "admin")

    # ------------------------------------------------------------ owners and Child Mode

    def set_zone(self, channel_id: str, changes: dict) -> dict:
        """An admin sets a channel's owner, Child Mode and hours. Returns what changed."""
        zone = dict(self.zone(channel_id))
        done = {}
        if "owner" in changes:
            owner = changes["owner"] or None
            if owner is not None and self.accounts.user(owner) is None:
                raise ChannelRefused("No such user.", 404)
            if owner != zone.get("owner"):
                zone["owner"] = done["owner"] = owner
        if "kid_mode" in changes and bool(changes["kid_mode"]) != bool(zone.get("kid_mode")):
            zone["kid_mode"] = done["kid_mode"] = bool(changes["kid_mode"])
        if "schedule" in changes:
            try:
                schedule = clean_schedule(changes["schedule"])
            except ValueError as e:
                raise ChannelRefused(str(e), 400)
            if schedule != zone.get("schedule"):
                zone["schedule"] = done["schedule"] = schedule
        if done:
            if not zone.get("owner") and not zone.get("kid_mode") and not zone.get("schedule"):
                self.data["zones"].pop(channel_id, None)
            else:
                self.data["zones"][channel_id] = zone
            self.save()
        return done

    def forget_user(self, user_id: str) -> None:
        """A removed user's zones become open."""
        for zone in self.data["zones"].values():
            if zone.get("owner") == user_id:
                zone["owner"] = None
        self.save()

    async def tick(self) -> None:
        """Child Mode's hours ended: what the zone's user started fades out and stops (an admin's plays on)."""
        if not self.enabled or self.sm is None:
            return
        now = self.now()
        for channel_id, zone in list(self.data["zones"].items()):
            if not zone.get("kid_mode") or not zone.get("schedule") or in_hours(zone["schedule"], now):
                continue
            session = self.sm.get(channel_id)
            who = self.data["started_by"].get(channel_id) or {"admin": True}
            if session is None or not session.is_playing or who.get("admin") or channel_id in self._fading:
                continue
            self._fading.add(channel_id)
            try:
                logger.info(f"Zones: {session.name}'s hours ended, fading out what {self.username(who.get('user_id')) or 'its user'} started")
                publish("zone.schedule_ended", {"channel_id": channel_id, "name": session.name})
                with self.system():
                    await fade_out(self.sm, session, KID_FADE_SECONDS)
                    still_kids = not (self.data["started_by"].get(channel_id) or {}).get("admin", True)
                    if session.is_playing and still_kids:
                        await self.sm.stop(channel_id, reason="schedule")
                    elif session.is_playing:  # an admin played it meanwhile: back up
                        channel = self.sm.channel_of(session)
                        if channel is not None:
                            channel.fade_in(self.sm.crossfade_seconds())
                await self._changed(session)
            finally:
                self._fading.discard(channel_id)

    async def _changed(self, session) -> None:
        if self.on_change is not None:
            try:
                await self.on_change(session)
            except Exception as e:
                logger.debug(f"Zones: Home Assistant's entities not updated: {e}")

    # ------------------------------------------------------------ links

    def _session(self, channel_id: str):
        session = self.sm.get(channel_id) if self.sm is not None else None
        if session is None:
            raise ChannelRefused("Channel not found", 404)
        return session

    @staticmethod
    def _snapshot(session) -> dict:
        return {"theme_id": session.theme_id, "preset_id": session.preset_id, "playlist_id": session.playlist_id,
                "sequence_id": session.sequence_id, "playing": bool(session.is_playing)}

    async def _play_link(self, session, link: dict) -> None:
        """The channel plays what the link plays (its volume stays its own)."""
        with self.system():
            if link.get("playlist_id"):
                if session.playlist_id != link["playlist_id"]:
                    self.sm.set_playlist(session.id, link["playlist_id"])
            else:
                if session.playlist_id:
                    self.sm.clear_playlist(session.id)
                self.sm.update(session.id, theme_id=link.get("theme_id"), preset_id=link.get("preset_id") or "")
            if not session.is_playing:
                await self.sm.play(session.id)
        await self._changed(session)

    async def _restore(self, session, saved: Optional[dict]) -> None:
        """Back to what the channel played before it was linked."""
        if not saved:
            return
        with self.system():
            try:
                if saved.get("playlist_id"):
                    if session.playlist_id != saved["playlist_id"]:
                        self.sm.set_playlist(session.id, saved["playlist_id"])
                else:
                    if session.playlist_id:
                        self.sm.clear_playlist(session.id)
                    if saved.get("theme_id"):
                        self.sm.update(session.id, theme_id=saved["theme_id"], preset_id=saved.get("preset_id") or "")
                    if saved.get("sequence_id") and saved.get("theme_id") == session.theme_id:
                        self.sm.start_sequence(session.id, saved["sequence_id"])
            except (LookupError, ValueError) as e:  # a playlist or sequence that's gone meanwhile
                logger.info(f"Zones: {session.name} can't go back to all it played: {e}")
            if not saved.get("playing") and session.is_playing:
                await self.sm.stop(session.id, reason="stopped")
        await self._changed(session)

    def _check_plays(self, data: dict) -> dict:
        if data.get("playlist_id"):
            if data["playlist_id"] not in (self.sm.state.playlists if self.sm is not None else {}):
                raise ChannelRefused("Playlist not found", 404)
            return {"theme_id": None, "preset_id": None, "playlist_id": data["playlist_id"]}
        theme_id = data.get("theme_id")
        if not theme_id or (self.sm is not None and not self.sm.get_theme(theme_id)):
            raise ChannelRefused("Choose a theme or a playlist.", 400)
        return {"theme_id": theme_id, "preset_id": data.get("preset_id") or None, "playlist_id": None}

    def _check_channels(self, channels, link_id: Optional[str] = None) -> list:
        out = []
        for channel_id in channels or []:
            channel_id = str(channel_id)
            session = self._session(channel_id)
            other_id, other = self.link_of(channel_id)
            if other is not None and other_id != link_id:
                raise ChannelRefused(f"{session.name} is already linked: {other['name']}.", 409)
            if channel_id not in out:
                out.append(channel_id)
        if len(out) < 2:
            raise ChannelRefused("Choose at least two channels.", 400)
        return out

    def link_view(self, link_id: str, link: dict) -> dict:
        return {"id": link_id, "name": link["name"], "channels": list(link["channels"]), "left": list(link.get("left", [])),
                "theme_id": link.get("theme_id"), "preset_id": link.get("preset_id"),
                "playlist_id": link.get("playlist_id")}

    async def create_link(self, data: dict) -> dict:
        name = str(data.get("name") or "").strip()[:40]
        if not name:
            raise ChannelRefused("Give the link a name.", 400)
        plays = self._check_plays(data)
        channels = self._check_channels(data.get("channels"))
        link_id = "l_" + secrets.token_hex(4)
        link = {"name": name, "channels": channels, "left": [], **plays,
                "saved": {c: self._snapshot(self.sm.get(c)) for c in channels}}
        self.data["links"][link_id] = link
        self.save()
        for channel_id in channels:
            await self._play_link(self.sm.get(channel_id), link)
        self.save()
        logger.info(f"Zones: linked {', '.join(self.sm.get(c).name for c in channels)} as {name}")
        publish("link.created", {"link_id": link_id, "name": name, "channels": channels, **plays})
        return self.link_view(link_id, link)

    def _link(self, link_id: str) -> dict:
        link = self.data["links"].get(link_id)
        if link is None:
            raise ChannelRefused("Link not found", 404)
        return link

    async def change_link(self, link_id: str, data: dict) -> dict:
        link = self._link(link_id)
        changes = {}
        if "name" in data:
            name = str(data.get("name") or "").strip()[:40]
            if not name:
                raise ChannelRefused("Give the link a name.", 400)
            if name != link["name"]:
                link["name"] = changes["name"] = name
        if any(k in data for k in ("theme_id", "preset_id", "playlist_id")):
            plays = self._check_plays({**{k: link.get(k) for k in ("theme_id", "preset_id")}, **data}
                                      if not data.get("playlist_id") else data)
            for key, value in plays.items():
                if link.get(key) != value:
                    link[key] = changes[key] = value
        if "channels" in data:
            channels = self._check_channels(data["channels"], link_id)
            gone = [c for c in link["channels"] if c not in channels]
            added = [c for c in channels if c not in link["channels"]]
            for channel_id in gone:
                saved = link["saved"].pop(channel_id, None)
                if channel_id not in link.get("left", []):
                    await self._restore(self.sm.get(channel_id), saved)
            link["left"] = [c for c in link.get("left", []) if c in channels]
            for channel_id in added:
                link["saved"][channel_id] = self._snapshot(self.sm.get(channel_id))
            link["channels"] = channels
            if gone or added:
                changes["channels"] = channels
        self.save()
        if changes:
            for channel_id in link["channels"]:
                if channel_id not in link["left"]:
                    await self._play_link(self.sm.get(channel_id), link)
            publish("link.changed", {"link_id": link_id, "name": link["name"], "changes": changes})
        return self.link_view(link_id, link)

    async def remove_link(self, link_id: str) -> None:
        link = self._link(link_id)
        del self.data["links"][link_id]
        self.save()
        for channel_id in link["channels"]:
            session = self.sm.get(channel_id)
            if session is not None and channel_id not in link.get("left", []):
                await self._restore(session, link["saved"].get(channel_id))
        logger.info(f"Zones: unlinked {link['name']}")
        publish("link.removed", {"link_id": link_id, "name": link["name"]})

    def _may_leave(self, actor: Actor, channel_id: str) -> None:
        if actor.admin:
            return
        owner = self.owner(channel_id)
        if owner is None or owner != actor.user_id or not self.can_leave_link(actor.user_id):
            raise ChannelRefused("Only an admin can take this channel out of the link.")

    async def leave(self, link_id: str, channel_id: str, actor: Actor) -> dict:
        link = self._link(link_id)
        if channel_id not in link["channels"]:
            raise ChannelRefused("That channel isn't in this link.", 404)
        self._may_leave(actor, channel_id)
        if channel_id not in link["left"]:
            link["left"].append(channel_id)
            self.save()
            await self._restore(self._session(channel_id), link["saved"].get(channel_id))
            publish("link.left", {"link_id": link_id, "name": link["name"], "channel_id": channel_id})
        return self.link_view(link_id, link)

    async def rejoin(self, link_id: str, channel_id: str, actor: Actor) -> dict:
        link = self._link(link_id)
        if channel_id not in link["channels"]:
            raise ChannelRefused("That channel isn't in this link.", 404)
        self._may_leave(actor, channel_id)
        if channel_id in link["left"]:
            session = self._session(channel_id)
            link["left"].remove(channel_id)
            link["saved"][channel_id] = self._snapshot(session)
            self.save()
            await self._play_link(session, link)
            publish("link.rejoined", {"link_id": link_id, "name": link["name"], "channel_id": channel_id,
                                      "by": "admin" if actor.admin else "owner"})
        return self.link_view(link_id, link)

    # ------------------------------------------------------------ what the web UI shows

    def view(self, actor: Actor) -> dict:
        out = {"on": self.enabled, "admin": actor.admin, "me": actor.user_id,
               "can_leave_link": actor.admin or self.can_leave_link(actor.user_id)}
        if not self.enabled:
            return out
        self.sync_homes()
        zones = {}
        for channel_id, zone in self.data["zones"].items():
            owner = self.owner(channel_id)
            who = self.data["started_by"].get(channel_id) or {}
            zones[channel_id] = {"owner": owner, "owner_name": self.username(owner) or None,
                                 "kid_mode": bool(zone.get("kid_mode")), "schedule": zone.get("schedule"),
                                 "started_by_admin": bool(who.get("admin", True))}
        speakers = {}
        for sp, channels in self.data["homes"].items():
            holder = self.holder(sp)
            speakers[sp] = {"home": channels[0] if channels else None, "channels": list(channels),
                            "in_use": holder.id if holder is not None else None}
        out.update(zones=zones, speakers=speakers,
                   links=[self.link_view(i, link) for i, link in self.data["links"].items()])
        if actor.admin:
            out["users"] = [{"id": i, "username": u["username"], "role": u["role"],
                             "can_leave_link": u.get("role") == "admin" or bool(u.get("can_leave_link"))}
                            for i, u in sorted(self.accounts.users.items(), key=lambda x: x[1]["username"].lower())
                            if not u.get("disabled")]
        return out
