"""
The cue timeline: ahead of time, which sound will be heard on which speaker
and when. Core announces sounds; what a plugin does with them (a light, a
display, a fan) is the plugin's business.

Where cues come from. Every mix (theme.ThemeMix) has a CueRelay; a channel
points it at this timeline (Channel._label_stream). The players announce
each sound they start (recording.py):

    intermittent  an Intermittent track plays. Decided and announced
                  Look-ahead early (Settings > Advanced, timeline_lookahead,
                  default 0.5 s) unless it is in a group, whose turn is
                  decided when it comes
    loop          a Background track starts a pass (and each loop after)
    ebb           an Ebb & Flow track's file starts a pass
    group         a Merry-go-round group's next file starts
    preset, step, theme   the channel's preset, sequence step or theme
                  changed: the cue carries the tracks' new levels
    sound         a sound or announcement over the channel (sound_id, label)

The mixing thread only notes the start (a few attribute reads) and hands it
to the event loop, where the cue is made: the channel, theme, track, file,
how long, how loud, the plugin attributes of the theme, track and file, and
for each of the channel's speakers when it will be heard:

    audible_at = mixer_at + the speaker's Sync

mixer_at is when the sound's first sample leaves the mixer (Unix seconds on
the server's clock); Sync is the speaker's own setting, else for a plugin's
output device its sink's latency, else the estimate for its type
(delay_source "setting", "device" or "estimate"; core/speaker_settings.py).

Events on the timeline (not the general bus, which drops the oldest of 256
waiting events; a timeline subscriber has its own queue of TIMELINE_QUEUE
and asks only for the channels it wants):

    cue.scheduled  a sound will be heard (lead_ms: how long before it leaves
                   the mixer; confirmed false while it is only decided)
    cue.cancelled  it won't be after all: the channel stopped, paused or
                   changed theme, the track was muted, or an announced play
                   didn't start (reason)
    cue.ended      its last speaker finished it (reason finished, or
                   stopped when the channel stopped while it was heard)

Plugins: self.timeline.subscribe(handler, channels=None) (services.py).
Outside programs: GET /api/timeline/stream (Server-Sent Events, the event
stream's sign-in), GET /api/timeline (the catalogue and the cues pending).

A personal channel's cues reach only its account's streams, as its events do.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from typing import Callable, Iterable, Optional

SAMPLE_RATE = 44100
# How long after an announced sound should have left the mixer it is called off if it didn't
CONFIRM_GRACE_SECONDS = 1.0
# Each timeline subscriber's own queue (oldest dropped and counted when full)
TIMELINE_QUEUE = 2048
# Cues kept for GET /api/timeline and for cancelling (oldest forgotten first)
PENDING_MAX = 4096

CATALOGUE: dict[str, dict] = {
    "cue.scheduled": {
        "description": "A sound will be heard: when it leaves the mixer and when each of the channel's speakers "
                       "plays it.",
        "fields": {
            "cue_id": "Cue id (the same in its cancelled and ended events)",
            "channel_id": "Channel id", "name": "Channel name", "theme_id": "Theme id",
            "start": "intermittent, loop, ebb, group, preset, step, theme or sound",
            "track": "Track key (null for preset, step, theme and sound)",
            "group": "The track's group, or null", "file": "The file, relative to the theme's folder, or null",
            "duration_ms": "How long it plays, or null when not known",
            "gain": "The track's volume, 0..1, or null",
            "mixer_at": "When it leaves the mixer (Unix seconds, server clock)",
            "lead_ms": "How long before it leaves the mixer this cue was sent",
            "confirmed": "True once it started in the mixer; false while it is only decided (Look-ahead)",
            "speakers": "[{speaker_id, area, sync_ms, delay_source (setting, device, estimate), audible_at}]",
            "attributes": "Plugin attributes: {theme|track|file: {plugin_id: {name: value}}}",
            "levels": "preset, step, theme: {track key: {volume, on}}",
            "preset_id": "preset, step: the preset", "step": "step: its number",
            "sound_id": "sound: the sound's id", "label": "sound: what the sender called it",
        },
    },
    "cue.cancelled": {
        "description": "A scheduled sound won't be heard after all.",
        "fields": {"cue_id": "Cue id", "channel_id": "Channel id",
                   "reason": "stopped, paused, theme_changed, skipped (muted, its turn went elsewhere), no_audio, "
                             "not_played (announced, but the track didn't start it)"},
    },
    "cue.ended": {
        "description": "A sound finished on its last speaker.",
        "fields": {"cue_id": "Cue id", "channel_id": "Channel id", "reason": "finished or stopped"},
    },
}
EVENT_TYPES = frozenset(CATALOGUE)


class _ChannelCues:
    """A channel's relay target (recording.CueRelay): runs in the mixing thread, so only notes and hands over."""

    __slots__ = ("timeline", "channel", "theme_id")

    def __init__(self, timeline: "Timeline", channel: int, theme_id: Optional[str]):
        self.timeline, self.channel, self.theme_id = timeline, channel, theme_id

    def announce(self, stream, start: str, track, duration_samples, lead: int):
        tl = self.timeline
        if not tl.listening:
            return None
        token = tl.next_id()
        instance = getattr(stream, "instance", None)
        group = getattr(getattr(instance, "meta", None), "group", None) if instance is not None else None
        if group is None and start == "group":
            group = getattr(stream, "group", None)
        path = None
        try:
            path = getattr(getattr(instance, "meta", None), "path", None)
            if start == "group" and track:
                view = next((v for v in stream._members() if v.name == track), None)  # the file starting
                path = getattr(getattr(view, "meta", None), "path", None) if view is not None else None
        except Exception:
            pass
        gain = None
        try:
            gain = float(getattr(instance, "volume")) if instance is not None and start != "group" else None
        except Exception:
            pass
        now = tl.clock()
        tl.hand_over(("announce", {
            "token": token, "channel": self.channel, "theme_id": self.theme_id, "start": start,
            "track": track, "group": group, "path": path, "gain": gain,
            "duration_samples": int(duration_samples) if duration_samples else None,
            "mixer_at": now + max(0, int(lead or 0)) / SAMPLE_RATE, "lead": max(0, int(lead or 0)) / SAMPLE_RATE,
            "confirmed": not lead, "noted_at": now}))
        return token

    def confirm(self, token) -> None:
        if token is not None:
            self.timeline.hand_over(("confirm", {"token": token, "at": self.timeline.clock()}))

    def cancel(self, token, reason: str) -> None:
        if token is not None:
            self.timeline.hand_over(("cancel", {"token": token, "reason": reason}))


class Timeline:
    """
    The cue timeline. attach() gives it the session manager (channels and
    speakers), the attribute service and the audio outputs; bind_loop() the
    event loop cues are made on.
    """

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.session_manager = None
        self.attributes = None
        self.outputs = None
        self._bus = None
        self._main = None  # the general event bus (channel changes, sounds)
        self._loop = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._inbox: deque = deque()
        self._pending: dict[str, dict] = {}  # cue id -> the cue (until it ended or was cancelled)
        self._raw: dict[str, dict] = {}      # cue id -> what the mixer noted (to start it again when late)
        self._timers: dict[str, list] = {}
        self._watching = []

    # --- set-up ---

    def attach(self, session_manager=None, attributes=None, outputs=None, bus=None) -> None:
        if session_manager is not None:
            self.session_manager = session_manager
        if attributes is not None:
            self.attributes = attributes
        if outputs is not None:
            self.outputs = outputs
        if bus is not None and bus is not self._main:
            for sub in self._watching:
                try:
                    sub.close()
                except Exception:
                    pass
            self._main = bus
            self._watching = [
                bus.subscribe("channel.stopped", self._on_stopped, owner=None, queue_size=1024),
                bus.subscribe(["channel.changed", "sequence.step"], self._on_changed, owner=None, queue_size=1024),
                bus.subscribe(["sound.started", "sound.ended"], self._on_sound, owner=None, queue_size=1024),
            ]

    def bind_loop(self, loop=None) -> None:
        import asyncio
        self._loop = loop or asyncio.get_running_loop()
        self.bus.bind_loop(self._loop)

    @property
    def bus(self):
        """The timeline's own bus: cue events only, subscribers filtered by channel."""
        if self._bus is None:
            self._bus = _make_bus(self)
        return self._bus

    @property
    def listening(self) -> bool:
        """Anyone following cues (else the mixer announces nothing)."""
        bus = self._bus
        return bus is not None and bool(bus.subscriptions())

    def next_id(self) -> str:
        return f"c{next(self._ids)}"

    def for_channel(self, channel: int, theme_id: Optional[str]) -> _ChannelCues:
        """What a channel's mix announces to (recording.CueRelay.target)."""
        return _ChannelCues(self, channel, theme_id)

    # --- subscribing ---

    def subscribe(self, handler, channels: Optional[Iterable[str]] = None, *, owner: Optional[str] = None,
                  types: Optional[Iterable[str]] = None, audience: Optional[str] = None, queue_size: int = TIMELINE_QUEUE):
        """
        handler(event) for cue events (types: "cue.*" by default) of the
        channels given (None: every channel). Returns the Subscription.
        """
        patterns = list(types) if types else ["cue.*"]
        sub = self.bus.subscribe(patterns, handler, owner=owner, queue_size=queue_size, audience=audience)
        sub.channels = frozenset(str(c) for c in channels) if channels else None
        return sub

    def unsubscribe_owner(self, owner: str) -> int:
        return self.bus.unsubscribe_owner(owner) if self._bus is not None else 0

    def pending(self, channel: Optional[str] = None, audience: Optional[str] = None) -> list[dict]:
        """The cues scheduled and not yet ended, oldest first."""
        with self._lock:
            cues = list(self._pending.values())
        return [dict(c) for c in cues if (channel is None or c["channel_id"] == channel)
                and (c.get("_audience") is None or c.get("_audience") == audience)]

    # --- from the mixing thread ---

    def hand_over(self, item) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        self._inbox.append(item)
        try:
            loop.call_soon_threadsafe(self._drain)
        except RuntimeError:
            pass

    def _drain(self) -> None:
        while self._inbox:
            kind, data = self._inbox.popleft()
            try:
                if kind == "announce":
                    self._announced(data)
                elif kind == "confirm":
                    self._confirmed(data)
                elif kind == "cancel":
                    self._cancel(data["token"], data["reason"])
            except Exception as e:
                from sonorium.obs import logger
                logger.debug(f"Cue {kind} not handled: {e}")

    # --- making cues ---

    def _session_of(self, channel: int):
        sm = self.session_manager
        if sm is None:
            return None
        for session_id, channel_id in list((getattr(sm, "_session_channels", None) or {}).items()):
            if channel_id == channel:
                getter = getattr(sm, "get_any", None) or getattr(sm, "get", None)
                return getter(session_id) if getter is not None else None
        return None

    def _speakers(self, session) -> list[dict]:
        """The channel's speakers now, each with its Sync and where that came from."""
        from sonorium.core.speaker_settings import estimated_sync_ms
        sm = self.session_manager
        if sm is None or not getattr(session, "is_playing", False) or getattr(session, "speakers_muted", False):
            return []
        try:
            ids = list(sm.get_resolved_speakers(session))
        except Exception:
            return []
        try:
            all_settings = sm.state.settings.speaker_settings or {}
        except Exception:
            all_settings = {}
        registry = getattr(sm, "registry", None)
        out = []
        for sid in ids:
            speaker = None
            try:
                speaker = registry.get_speaker(sid) if registry is not None else None
            except Exception:
                speaker = None
            own = (all_settings.get(sid) or {}).get("sync")
            if isinstance(own, int) and not isinstance(own, bool) and own >= 0:
                ms, source = own, "setting"
            else:
                latency = None
                if self.outputs is not None and hasattr(self.outputs, "latency"):
                    try:
                        latency = self.outputs.latency(session.id, sid)
                    except Exception:
                        latency = None
                if latency is not None:
                    ms, source = int(round(latency * 1000)), "device"
                else:
                    kind = getattr(speaker, "type", None)
                    if kind is None and str(sid).startswith("net:"):
                        kind = str(sid).split(":")[1]
                    ms, source = estimated_sync_ms(kind), "estimate"
            out.append({"speaker_id": sid, "area": getattr(speaker, "area_id", None), "sync_ms": ms,
                        "delay_source": source})
        return out

    def _attributes(self, theme_id, track, group) -> dict:
        service = self.attributes
        if service is None or not theme_id:
            return {}
        refs = [("theme", {"kind": "theme", "id": theme_id})]
        if track:
            refs.append(("track", {"kind": "track", "theme": theme_id, "id": track}))
            if group:
                refs.append(("file", {"kind": "file", "theme": theme_id, "id": track}))
        out = {}
        for name, ref in refs:
            try:
                values = service.get(ref)
            except Exception:
                values = None
            if values:
                out[name] = values
        return out

    def _file(self, session, path) -> Optional[str]:
        if not path:
            return None
        try:
            from pathlib import Path
            folder = self.session_manager.theme_folder(session.theme_id)
            if folder is not None:
                return Path(path).relative_to(Path(folder)).as_posix()
        except Exception:
            pass
        try:
            from pathlib import Path
            return Path(path).name
        except Exception:
            return None

    def _publish(self, event_type: str, data: dict, audience: Optional[str]) -> None:
        self.bus.publish(event_type, data, audience=audience)

    def _schedule(self, session, data: dict, *, theme_id=None, start: str, mixer_at: float, lead: float,
                  confirmed: bool, duration: Optional[float], extra: Optional[dict] = None,
                  cue_id: Optional[str] = None) -> Optional[dict]:
        if not self.listening:
            return None
        speakers = self._speakers(session)
        for speaker in speakers:
            speaker["audible_at"] = round(mixer_at + speaker["sync_ms"] / 1000.0, 3)
        audience = getattr(session, "owner", None) or None
        cue = {
            "cue_id": cue_id or self.next_id(), "channel_id": session.id, "name": session.name,
            "theme_id": theme_id or session.theme_id, "start": start, "track": None, "group": None, "file": None,
            "duration_ms": None if duration is None else int(round(duration * 1000)), "gain": None,
            "mixer_at": round(mixer_at, 3), "lead_ms": int(round(lead * 1000)), "confirmed": bool(confirmed),
            "speakers": speakers, "attributes": {},
        }
        cue.update(data)
        if extra:
            cue.update(extra)
        record = dict(cue, _audience=audience)
        with self._lock:
            self._pending[cue["cue_id"]] = record
            while len(self._pending) > PENDING_MAX:
                old = next(iter(self._pending))
                self._pending.pop(old, None)
                self._raw.pop(old, None)
                self._clear_timers(old)
        self._publish("cue.scheduled", cue, audience)
        if start != "sound":  # a sound ends with its sound.ended
            heard_until = max([s["audible_at"] for s in speakers] or [mixer_at]) + (duration or 0.0)
            self._later(cue["cue_id"], heard_until - self.clock(), self._ended, cue["cue_id"], "finished")
        if not confirmed:
            self._later(cue["cue_id"], mixer_at + CONFIRM_GRACE_SECONDS - self.clock(), self._unconfirmed,
                        cue["cue_id"])
        return cue

    def _announced(self, raw: dict) -> None:
        session = self._session_of(raw["channel"])
        if session is None:
            return
        duration = raw["duration_samples"] / SAMPLE_RATE if raw.get("duration_samples") else None
        data = {"track": raw.get("track"), "group": raw.get("group"), "file": self._file(session, raw.get("path")),
                "gain": None if raw.get("gain") is None else round(raw["gain"], 3),
                "attributes": self._attributes(raw.get("theme_id") or session.theme_id, raw.get("track"),
                                               raw.get("group"))}
        cue = self._schedule(session, data, theme_id=raw.get("theme_id"), start=raw["start"],
                             mixer_at=raw["mixer_at"], lead=raw["lead"], confirmed=raw["confirmed"],
                             duration=duration, cue_id=raw["token"])
        if cue is not None and not raw["confirmed"]:
            with self._lock:
                self._raw[cue["cue_id"]] = raw

    def _confirmed(self, data: dict) -> None:
        token = data["token"]
        with self._lock:
            record = self._pending.get(token)
            raw = self._raw.pop(token, None)
        if record is not None:
            record["confirmed"] = True
            return
        if raw is not None:  # called off as late, and it started after all: a cue for it now
            late = dict(raw, token=self.next_id(), mixer_at=data["at"], lead=0.0, confirmed=True)
            self._announced(late)

    def _unconfirmed(self, cue_id: str) -> None:
        with self._lock:
            record = self._pending.get(cue_id)
        if record is not None and not record.get("confirmed"):
            self._cancel(cue_id, "not_played", keep_raw=True)

    def _cancel(self, cue_id: str, reason: str, keep_raw: bool = False) -> None:
        with self._lock:
            record = self._pending.pop(cue_id, None)
            if not keep_raw:
                self._raw.pop(cue_id, None)
        self._clear_timers(cue_id)
        if record is not None:
            self._publish("cue.cancelled", {"cue_id": cue_id, "channel_id": record["channel_id"], "reason": reason},
                          record.get("_audience"))

    def _ended(self, cue_id: str, reason: str) -> None:
        with self._lock:
            record = self._pending.pop(cue_id, None)
            self._raw.pop(cue_id, None)
        self._clear_timers(cue_id)
        if record is not None:
            self._publish("cue.ended", {"cue_id": cue_id, "channel_id": record["channel_id"], "reason": reason},
                          record.get("_audience"))

    def _later(self, cue_id: str, delay: float, fn, *args) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        handle = loop.call_later(max(0.0, delay), fn, *args)
        self._timers.setdefault(cue_id, []).append(handle)

    def _clear_timers(self, cue_id: str) -> None:
        for handle in self._timers.pop(cue_id, []):
            handle.cancel()

    # --- the channel's own changes (general bus) ---

    def _session_by_id(self, session_id):
        sm = self.session_manager
        if sm is None:
            return None
        getter = getattr(sm, "get_any", None) or getattr(sm, "get", None)
        try:
            return getter(session_id) if getter is not None else None
        except Exception:
            return None

    def call_off_channel(self, session_id: str, reason: str, only=None) -> int:
        """Cancel the channel's cues not yet heard (only(cue): which); the ones being heard end. Returns how many."""
        now = self.clock()
        with self._lock:
            cues = [c for c in self._pending.values() if c["channel_id"] == session_id and (only is None or only(c))]
        count = 0
        for cue in cues:
            heard = [s["audible_at"] for s in cue["speakers"]] or [cue["mixer_at"]]
            if min(heard) > now or not cue.get("confirmed"):
                self._cancel(cue["cue_id"], reason)
            else:
                self._ended(cue["cue_id"], "stopped")
            count += 1
        return count

    def _on_stopped(self, event) -> None:
        data = event.data or {}
        reason = "paused" if data.get("reason") == "paused" else "stopped"
        if data.get("channel_id"):
            self.call_off_channel(data["channel_id"], reason)

    def _levels(self, session) -> dict:
        sm = self.session_manager
        try:
            channel = sm._channel_for(session)
            mix = getattr(channel, "_theme_stream", None)
            views = dict(getattr(mix, "_views", None) or {})
        except Exception:
            return {}
        out = {}
        for key, view in views.items():
            try:
                out[key] = {"volume": round(float(view.volume), 3), "on": bool(view.is_enabled)}
            except Exception:
                continue
        return out

    def _on_changed(self, event) -> None:
        if not self.listening:
            return
        data = event.data or {}
        session = self._session_by_id(data.get("channel_id"))
        if session is None or not (getattr(session, "is_playing", False)
                                   or getattr(self.session_manager, "is_listened", lambda s: False)(session)):
            return
        now = self.clock()
        if event.type == "sequence.step":
            self._schedule(session, {"levels": self._levels(session)}, start="step", mixer_at=now, lead=0.0,
                           confirmed=True, duration=None,
                           extra={"preset_id": data.get("preset_id"), "step": data.get("step")})
            return
        changes = data.get("changes") or {}
        if "theme_id" in changes:
            new_theme = changes.get("theme_id")
            self.call_off_channel(session.id, "theme_changed",
                                  only=lambda c: c["theme_id"] != new_theme and c["mixer_at"] > now)
            self._schedule(session, {"levels": self._levels(session)}, theme_id=new_theme, start="theme",
                           mixer_at=now, lead=0.0, confirmed=True, duration=None,
                           extra={"preset_id": changes.get("preset_id", getattr(session, "preset_id", None))})
        elif "preset_id" in changes and not getattr(session, "sequence_id", None):
            self._schedule(session, {"levels": self._levels(session)}, start="preset", mixer_at=now, lead=0.0,
                           confirmed=True, duration=None, extra={"preset_id": changes.get("preset_id")})

    def _on_sound(self, event) -> None:
        data = event.data or {}
        if event.type == "sound.ended":
            with self._lock:
                ids = [c["cue_id"] for c in self._pending.values() if c.get("start") == "sound"
                       and c.get("sound_id") == data.get("sound_id") and c["channel_id"] == data.get("channel_id")]
            for cue_id in ids:
                self._ended(cue_id, "finished" if data.get("reason") in (None, "finished") else "stopped")
            return
        if not self.listening:
            return
        session = self._session_by_id(data.get("channel_id"))
        if session is None:
            return
        self._schedule(session, {}, start="sound", mixer_at=self.clock(), lead=0.0, confirmed=True, duration=None,
                       extra={"sound_id": data.get("sound_id"), "label": data.get("label")})

    def describe(self) -> dict:
        from sonorium.playback_values import get as value
        return {"events": {k: dict(v) for k, v in CATALOGUE.items()},
                "look_ahead_ms": int(round(float(value("timeline_lookahead")) * 1000)),
                "confirm_grace_ms": int(CONFIRM_GRACE_SECONDS * 1000)}


def _make_bus(timeline: Timeline):
    """An event bus for cue events only, whose subscribers can ask for some channels."""
    from datetime import datetime, timezone

    from sonorium.plugins import events as ev

    class CueSubscription(ev.Subscription):
        channels = None

        def receives(self, event) -> bool:
            if not super().receives(event):
                return False
            return self.channels is None or str((event.data or {}).get("channel_id")) in self.channels

    class CueBus(ev.EventBus):
        def subscribe(self, patterns, handler, *, owner=None, queue_size=TIMELINE_QUEUE, timeout=30.0,
                      audience=None):
            if isinstance(patterns, str):
                patterns = [patterns]
            checked = [ev.check_pattern(p) for p in patterns]
            sub = CueSubscription(self, checked, handler, owner, queue_size, timeout, audience)
            with self._lock:
                self._subs.append(sub)
            self._target_loop()
            return sub

        def publish(self, event_type, data=None, *, origin=None, audience=None):
            if event_type not in EVENT_TYPES:
                # a subscriber's failure ("error"): the alert bar, through the general bus
                main = timeline._main or ev.get_bus()
                return main.publish(event_type, data, origin=origin, audience=audience)
            event = ev.Event(type=event_type, data=dict(data or {}), id=next(self._ids),
                             time=datetime.now(timezone.utc).isoformat(), origin=origin or "sonorium",
                             audience=audience or None)
            with self._lock:
                self._history.append(event)
                subs = [s for s in self._subs if s.receives(event)]
            if subs:
                self._dispatch(subs, event)
            return event

    return CueBus(history=500)


_timeline = Timeline()


def get_timeline() -> Timeline:
    return _timeline


def set_timeline(timeline: Timeline) -> Timeline:
    """Replace the shared timeline (tests). Returns the old one."""
    global _timeline
    old, _timeline = _timeline, timeline
    return old
