"""
The event bus: what happens in Sonorium, as typed events plugins (and
outside programs, through GET /api/events/stream) can follow.

- Sonorium publishes the events in CATALOGUE from the code that makes them
  happen (session manager, playback runner, speaker registry, theme files,
  plugin manager, scheduler, triggers). The catalogue is a public contract:
  names and payload fields only ever get added to, never renamed or removed.
- A plugin publishes its own events under "plugin.<plugin_id>.<name>".
- publish() never blocks and never raises: each subscriber has its own
  bounded queue and worker task. A slow subscriber only falls behind (its
  oldest events are dropped and counted), a failing one is logged; neither
  reaches the code that published the event, so playback never waits.
- publish() works from any thread: from outside the event loop the event is
  handed to the loop the bus is bound to.

Patterns: "channel.started" (exact), "channel.*" (one prefix, any depth),
"*" (everything).
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import itertools
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Optional, Union

from sonorium import runtime
from sonorium.obs import logger

CATALOGUE_VERSION = 1

# type -> {"description", "fields": {name: description}, "summary": title template}
CATALOGUE: dict[str, dict] = {
    # --- Sonorium ---
    "sonorium.started": {
        "description": "Sonorium finished starting up; plugins are loaded and enabled.",
        "fields": {"version": "Sonorium version"},
        "summary": "Sonorium started",
    },
    # --- Channels ---
    "channel.created": {
        "description": "A channel was added.",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "theme_id": "Theme id or null"},
        "summary": "Channel added: {name}",
    },
    "channel.deleted": {
        "description": "A channel was deleted.",
        "fields": {"channel_id": "Channel id", "name": "Channel name"},
        "summary": "Channel deleted: {name}",
    },
    "channel.started": {
        "description": "A channel started playing on its speakers.",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "theme_id": "Theme id",
                   "preset_id": "Preset id or null", "volume": "0-100", "speakers": "Speaker ids"},
        "summary": "{name} started",
    },
    "channel.stopped": {
        "description": "A channel stopped playing on its speakers.",
        "fields": {"channel_id": "Channel id", "name": "Channel name",
                   "reason": "stopped, paused, idle (no listener pulled audio) or ended (playlist finished)"},
        "summary": "{name} stopped",
    },
    "channel.changed": {
        "description": "A channel's settings changed. Only the fields that changed are in 'changes'.",
        "fields": {"channel_id": "Channel id", "name": "Channel name",
                   "changes": "Changed fields, any of: theme_id, preset_id, sequence_id, playlist_id, "
                              "volume, speakers_muted, name, speakers"},
        "summary": "{name} changed",
    },
    "channel.listening": {
        "description": "Someone started or stopped listening to a channel on their device (Listen here, the "
                       "app). While someone listens the channel's mix runs, with or without its speakers.",
        "fields": {"channel_id": "Channel id", "name": "Channel name",
                   "listening": "True when the first listener came, false when the last one left"},
        "summary": "{name}: listening {listening}",
    },
    "channel.output_added": {
        "description": "The app reported a device (e.g. a Cast device) playing a channel's stream. While it is "
                       "reported the channel's mix runs, with or without its speakers.",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "output_id": "Output id",
                   "kind": "cast, ...", "output_name": "The device's name", "address": "Its address, or null"},
        "summary": "{name}: playing on {output_name}",
    },
    "channel.output_removed": {
        "description": "A reported device no longer plays a channel.",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "output_id": "Output id",
                   "kind": "cast, ...", "output_name": "The device's name", "address": "Its address, or null",
                   "reason": "removed (by the app), expired (no heartbeat), replaced (reported again), stopped, "
                             "paused, idle (it pulled no audio), deleted (the channel)"},
        "summary": "{name}: stopped on {output_name}",
    },
    "sequence.step": {
        "description": "A channel's sequence moved to a step (also its first step when it starts).",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "theme_id": "Theme id",
                   "sequence_id": "Sequence id", "step": "Step number (1-based)", "steps": "Number of steps",
                   "preset_id": "The step's preset id"},
        "summary": "{name}: step {step} of {steps}",
    },
    "playlist.entry": {
        "description": "A channel's playlist moved to an entry.",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "playlist_id": "Playlist id",
                   "position": "Position in this round (1-based)", "entries": "Number of entries",
                   "theme_id": "The entry's theme id"},
        "summary": "{name}: entry {position} of {entries}",
    },
    # --- Sounds over a channel and volume ramps (framework services) ---
    "sound.started": {
        "description": "A sound (announcement, alert, effect) started playing over a channel's mix.",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "sound_id": "Sound id (the same on "
                   "every channel it plays on)", "label": "What the sender called it", "origin": "Who sent it"},
        "summary": "{name}: sound started",
    },
    "sound.ended": {
        "description": "A sound over a channel's mix ended.",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "sound_id": "Sound id",
                   "label": "What the sender called it", "origin": "Who sent it",
                   "reason": "finished, replaced, cancelled, stopped (the channel stopped), error",
                   "capped": "True if it was cut at the longest sound allowed",
                   "played": "Seconds of it that played"},
        "summary": "{name}: sound ended",
    },
    "ramp.started": {
        "description": "A channel's volume ramp started.",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "ramp_id": "Ramp id",
                   "from": "Level it starts at (0-1)", "to": "Level it ends at", "seconds": "How long it takes",
                   "curve": "linear, db, ease or exp", "then": "stop, pause or null: what happens at the end"},
        "summary": "{name}: volume ramp started",
    },
    "ramp.finished": {
        "description": "A channel's volume ramp reached its end.",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "ramp_id": "Ramp id",
                   "level": "The level it ended at", "then": "stop, pause or null"},
        "summary": "{name}: volume ramp finished",
    },
    "ramp.cancelled": {
        "description": "A channel's volume ramp was stopped before its end.",
        "fields": {"channel_id": "Channel id", "name": "Channel name", "ramp_id": "Ramp id",
                   "reason": "cancelled or replaced (another ramp started)", "level": "The level it got to"},
        "summary": "{name}: volume ramp cancelled",
    },
    # --- Speakers ---
    "speaker.added": {
        "description": "A speaker appeared (Home Assistant, the network or added by address).",
        "fields": {"speaker_id": "Speaker id", "name": "Speaker name", "type": "cast, sonos, dlna, ..."},
        "summary": "Speaker found: {name}",
    },
    "speaker.removed": {
        "description": "A speaker is no longer known.",
        "fields": {"speaker_id": "Speaker id", "name": "Speaker name"},
        "summary": "Speaker removed: {name}",
    },
    "speaker.unavailable": {
        "description": "A known speaker went offline.",
        "fields": {"speaker_id": "Speaker id", "name": "Speaker name"},
        "summary": "Speaker offline: {name}",
    },
    "speaker.available": {
        "description": "An offline speaker is back online.",
        "fields": {"speaker_id": "Speaker id", "name": "Speaker name"},
        "summary": "Speaker online: {name}",
    },
    # --- Themes ---
    "theme.created": {
        "description": "A theme was created, uploaded or imported.",
        "fields": {"theme_id": "Theme id", "folder": "Folder name"},
        "summary": "Theme added: {theme_id}",
    },
    "theme.changed": {
        "description": "A theme's files changed (metadata, presets, sequences). Several quick saves are "
                       "reported once.",
        "fields": {"theme_id": "Theme id (null if unknown)", "folder": "Folder name"},
        "summary": "Theme changed: {folder}",
    },
    "theme.deleted": {
        "description": "A theme was deleted.",
        "fields": {"theme_id": "Theme id"},
        "summary": "Theme deleted: {theme_id}",
    },
    # --- Plugins and the framework ---
    "plugin.enabled": {
        "description": "A plugin was enabled.",
        "fields": {"plugin_id": "Plugin id", "name": "Plugin name"},
        "summary": "Plugin enabled: {name}",
    },
    "plugin.disabled": {
        "description": "A plugin was disabled.",
        "fields": {"plugin_id": "Plugin id", "name": "Plugin name"},
        "summary": "Plugin disabled: {name}",
    },
    "schedule.fired": {
        "description": "A scheduled job ran.",
        "fields": {"plugin_id": "Plugin id", "job_id": "Job id", "name": "Job name", "data": "Job data",
                   "scheduled_for": "When it was due (ISO, UTC)", "missed": "True if it was due while "
                   "Sonorium was down and runs late"},
        "summary": "Schedule: {name}",
    },
    "trigger.received": {
        "description": "A plugin's webhook trigger was called (and authenticated).",
        "fields": {"plugin_id": "Plugin id", "trigger": "Trigger id", "payload": "JSON body or query values",
                   "method": "GET or POST"},
        "summary": "Trigger: {plugin_id}/{trigger}",
    },
    "notification": {
        "description": "A plugin (or Sonorium) sent a message. Notification rules deliver it like any event.",
        "fields": {"title": "Title", "body": "Text", "level": "info, warning or error",
                   "data": "Extra values from the sender"},
        "summary": "{title}",
    },
    "error": {
        "description": "Something failed that nobody was waiting for: a plugin handler, a scheduled job, "
                       "a trigger handler, a notifier, a channel that could not start.",
        "fields": {"where": "What failed (e.g. 'trigger', 'schedule', 'channel.start')",
                   "message": "What went wrong", "plugin_id": "Plugin id, if a plugin's"},
        "summary": "Error ({where}): {message}",
    },
}

# This run of Sonorium: event ids start again at 1 when it restarts, so the
# event stream sends "<boot_id>:<n>" as its ids (GET /api/info has it too)
BOOT_ID = runtime.BOOT_ID


def stream_id(event: "Event") -> str:
    """An event's id on the event stream: "<boot_id>:<n>"."""
    return f"{BOOT_ID}:{event.id}"


def replay_after(last_event_id: Optional[str]) -> Optional[int]:
    """
    Where a reconnecting stream picks up from its Last-Event-ID: "<boot_id>:<n>"
    of this run, or a bare "<n>" (older clients) -> n; an id from an earlier
    run -> 0 (everything still held of this run); none or unreadable -> None.
    """
    text = (last_event_id or "").strip()
    boot, sep, number = text.rpartition(":")
    if not number.isdigit():
        return None
    if sep and boot != BOOT_ID:
        return 0
    return int(number)


PLUGIN_EVENT_PREFIX = "plugin."
_NAME = re.compile(r"^[a-z0-9_][a-z0-9_.-]{0,63}$")
_PATTERN = re.compile(r"\*|[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*(\.\*)?")

# Who caused what happens now: "sonorium" (UI, API, MQTT, timers) or "plugin:<id>"
# (set by the actions API around a plugin's calls). Copied into every event.
current_origin: contextvars.ContextVar[str] = contextvars.ContextVar("sonorium_event_origin",
                                                                     default="sonorium")


def plugin_event_type(plugin_id: str, name: str) -> str:
    """'plugin.<plugin_id>.<name>' for a plugin's own event; ValueError for a bad name."""
    if not isinstance(name, str) or not _NAME.match(name):
        raise ValueError(f"Invalid event name {name!r}: use a-z, 0-9, '_', '-' and '.'")
    return f"{PLUGIN_EVENT_PREFIX}{plugin_id}.{name}"


def is_known_type(event_type: str) -> bool:
    """A catalogue event, or a plugin's own event (plugin.<id>.<name>)."""
    if event_type in CATALOGUE:
        return True
    if event_type.startswith(PLUGIN_EVENT_PREFIX) and event_type not in ("plugin.enabled", "plugin.disabled"):
        rest = event_type[len(PLUGIN_EVENT_PREFIX):]
        plugin_id, _, name = rest.partition(".")
        return bool(re.fullmatch(r"[A-Za-z0-9_-]+", plugin_id)) and bool(_NAME.match(name))
    return False


def check_pattern(pattern: str) -> str:
    """The pattern if valid: "*", "a.b" or "a.*" (segments of letters, digits, '_' and '-')."""
    if not isinstance(pattern, str) or not _PATTERN.fullmatch(pattern.strip()):
        raise ValueError(f"Invalid event pattern {pattern!r}")
    return pattern.strip()


def matches(pattern: str, event_type: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return event_type.startswith(pattern[:-1])
    return pattern == event_type


def summary(event: "Event") -> str:
    """A one-line description of the event for notifications ("{name} started")."""
    template = (CATALOGUE.get(event.type) or {}).get("summary")
    if not template:
        return event.type
    try:
        return template.format_map(_Missing(event.data))
    except Exception:
        return event.type


class _Missing(dict):
    def __missing__(self, key):
        return "?"


@dataclass
class Event:
    type: str
    data: dict
    id: int
    time: str
    origin: str = "sonorium"

    def to_dict(self) -> dict:
        return {"id": self.id, "boot_id": BOOT_ID, "type": self.type, "time": self.time, "origin": self.origin,
                "data": self.data}


Handler = Callable[[Event], Union[None, Awaitable[None]]]


class Subscription:
    """One subscriber: its own queue and worker, so it can't hold up anyone else."""

    def __init__(self, bus: "EventBus", patterns: list[str], handler: Handler, owner: Optional[str],
                 queue_size: int, timeout: float):
        self.bus = bus
        self.patterns = patterns
        self.handler = handler
        self.owner = owner
        self.timeout = timeout
        self.dropped = 0
        self.failures = 0
        self.delivered = 0
        self.active = True
        self._queue: deque = deque(maxlen=max(1, queue_size))
        self._wake: Optional[asyncio.Event] = None
        self._task: Optional[asyncio.Task] = None
        self._busy = False
        self._is_async = inspect.iscoroutinefunction(handler) or \
            inspect.iscoroutinefunction(getattr(handler, "__call__", None))

    def matches(self, event_type: str) -> bool:
        return any(matches(p, event_type) for p in self.patterns)

    def close(self) -> None:
        """Stop receiving events (queued ones are dropped)."""
        self.bus.unsubscribe(self)

    # Called on the loop thread only
    def _enqueue(self, event: Event) -> None:
        if not self.active:
            return
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1
        self._queue.append(event)
        self._ensure_worker()
        self._wake.set()

    def _ensure_worker(self) -> None:
        loop = asyncio.get_running_loop()
        if self._task is None or self._task.done() or self._task.get_loop() is not loop:
            self._wake = asyncio.Event()
            self._task = loop.create_task(self._run())

    @property
    def idle(self) -> bool:
        return not self._queue and not self._busy

    async def _run(self) -> None:
        while self.active:
            while self._queue and self.active:
                event = self._queue.popleft()
                self._busy = True
                try:
                    await self._call(event)
                finally:
                    self._busy = False
            self._wake.clear()
            if self._queue:
                continue
            await self._wake.wait()

    async def _call(self, event: Event) -> None:
        try:
            if self._is_async:
                await asyncio.wait_for(self.handler(event), self.timeout)
            else:
                # A plain function runs in a thread: it can never block the event loop
                result = await asyncio.wait_for(asyncio.to_thread(self.handler, event), self.timeout)
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, self.timeout)
            self.delivered += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.failures += 1
            what = "timed out" if isinstance(e, asyncio.TimeoutError) else f"failed: {e}"
            if self.failures <= 5 or self.failures % 100 == 0:
                logger.warning(f"Event handler of {self.owner or 'sonorium'} for {event.type} {what}")
            if event.type != "error":  # an error handler's failure doesn't make another error event
                self.bus.publish("error", {"where": "event_handler", "message": f"{event.type}: {what}",
                                           "plugin_id": self.owner})


class EventBus:
    def __init__(self, history: int = 200):
        self._subs: list[Subscription] = []
        self._history: deque = deque(maxlen=history)
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._later: dict[str, Any] = {}

    # --- Loop ---

    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        """The loop subscribers run on (the running one if not given); events from other threads go to it."""
        self._loop = loop or asyncio.get_running_loop()

    def _target_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """The bound loop; once it is closed (or never bound), the running one."""
        loop = self._loop
        if loop is not None and not loop.is_closed():
            return loop
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            return None
        self._loop = running
        return running

    # --- Subscribing ---

    def subscribe(self, patterns: Union[str, Iterable[str]], handler: Handler, *, owner: Optional[str] = None,
                  queue_size: int = 256, timeout: float = 30.0) -> Subscription:
        """
        Call handler(event) for each event matching one of the patterns. An
        async handler runs on the event loop, a plain one in a worker thread.
        ValueError for a bad pattern.
        """
        if isinstance(patterns, str):
            patterns = [patterns]
        checked = [check_pattern(p) for p in patterns]
        if not checked:
            raise ValueError("No event pattern given")
        sub = Subscription(self, checked, handler, owner, queue_size, timeout)
        with self._lock:
            self._subs.append(sub)
        self._target_loop()
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        sub.active = False
        with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)
        sub._queue.clear()
        if sub._wake is not None and sub._task is not None and not sub._task.done():
            try:
                loop = sub._task.get_loop()
                if loop.is_closed():
                    return
                if self._in_loop_thread(loop):
                    sub._task.cancel()
                else:
                    loop.call_soon_threadsafe(sub._task.cancel)
            except Exception:
                pass

    def unsubscribe_owner(self, owner: str) -> int:
        """Remove every subscription of a plugin. Returns how many."""
        with self._lock:
            subs = [s for s in self._subs if s.owner == owner]
        for sub in subs:
            self.unsubscribe(sub)
        return len(subs)

    def subscriptions(self) -> list[Subscription]:
        with self._lock:
            return list(self._subs)

    # --- Publishing ---

    def _in_loop_thread(self, loop) -> bool:
        try:
            return asyncio.get_running_loop() is loop
        except RuntimeError:
            return False

    def publish(self, event_type: str, data: Optional[dict] = None, *, origin: Optional[str] = None) -> Optional[Event]:
        """
        Publish an event. Never raises and never waits: returns the event, or
        None if it couldn't be published (logged).
        """
        try:
            if not is_known_type(event_type):
                raise ValueError(f"unknown event type {event_type!r}")
            event = Event(type=event_type, data=dict(data or {}), id=next(self._ids),
                          time=datetime.now(timezone.utc).isoformat(),
                          origin=origin or current_origin.get())
            with self._lock:
                self._history.append(event)
                subs = [s for s in self._subs if s.matches(event_type)]
            if subs:
                self._dispatch(subs, event)
            return event
        except Exception as e:
            logger.warning(f"Event {event_type} not published: {e}")
            return None

    def _dispatch(self, subs: list[Subscription], event: Event) -> None:
        loop = self._target_loop()
        if loop is not None and self._in_loop_thread(loop):
            for sub in subs:
                sub._enqueue(event)
            return
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._dispatch_in_loop, subs, event)
            return
        # No event loop running yet (start-up, tests): kept until a worker starts
        for sub in subs:
            if len(sub._queue) == sub._queue.maxlen:
                sub.dropped += 1
            sub._queue.append(event)

    def _dispatch_in_loop(self, subs, event) -> None:
        for sub in subs:
            sub._enqueue(event)

    def publish_later(self, key: str, delay: float, event_type: str, make_data: Callable[[], dict]) -> None:
        """
        Publish once `delay` seconds after the last call with the same key
        (quick repeated saves become one event). Without an event loop the
        event is published at once.
        """
        try:
            loop = self._target_loop()
            if loop is None or not (self._in_loop_thread(loop) or loop.is_running()):
                data = make_data()
                if data is not None:
                    self.publish(event_type, data)
                return
            if not self._in_loop_thread(loop):
                loop.call_soon_threadsafe(self.publish_later, key, delay, event_type, make_data)
                return
            handle = self._later.pop(key, None)
            if handle is not None:
                handle.cancel()

            def fire():
                self._later.pop(key, None)
                try:
                    data = make_data()
                except Exception as e:
                    logger.debug(f"Event {event_type} skipped: {e}")
                    return
                if data is not None:
                    self.publish(event_type, data)
            self._later[key] = loop.call_later(delay, fire)
        except Exception as e:
            logger.warning(f"Event {event_type} not published: {e}")

    def history(self, after_id: int = 0, patterns: Optional[list[str]] = None) -> list[Event]:
        """Recent events (the last 200) newer than after_id, oldest first."""
        with self._lock:
            events = [e for e in self._history if e.id > after_id]
        if patterns:
            events = [e for e in events if any(matches(p, e.type) for p in patterns)]
        return events

    async def drain(self, timeout: float = 5.0) -> None:
        """Wait until every subscriber has handled what it was given (for tests and shutdown)."""
        loop = asyncio.get_running_loop()
        give_up = loop.time() + timeout
        while loop.time() < give_up:
            await asyncio.sleep(0)
            for sub in self.subscriptions():
                if sub._queue and (sub._task is None or sub._task.done()):
                    sub._ensure_worker()
                    sub._wake.set()
            if all(s.idle for s in self.subscriptions()) and not self._later:
                await asyncio.sleep(0)
                if all(s.idle for s in self.subscriptions()):
                    return
            await asyncio.sleep(0.005)


_bus = EventBus()


def get_bus() -> EventBus:
    return _bus


def set_bus(bus: EventBus) -> EventBus:
    """Replace the shared bus (tests). Returns the old one."""
    global _bus
    old, _bus = _bus, bus
    return old


def publish(event_type: str, data: Optional[dict] = None, **kwargs) -> Optional[Event]:
    """Publish on the shared bus (never raises, never waits)."""
    return _bus.publish(event_type, data, **kwargs)


def publish_later(key: str, delay: float, event_type: str, make_data: Callable[[], dict]) -> None:
    _bus.publish_later(key, delay, event_type, make_data)
