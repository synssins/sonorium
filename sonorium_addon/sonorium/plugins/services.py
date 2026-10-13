"""
The framework's services as plugins see them: one small facade per service,
bound to the plugin, versioned with the plugin API (compat.py).

    self.audio            AudioV1      sounds over channels, text to speech
    self.ramps            RampsV1      volume ramps
    self.audio_outputs    OutputsV1    output types (host audio, ...)
    self.commands         CommandsV1   command maps (remotes, MQTT, webhooks)
    self.mqtt             MqttV1       publish / subscribe on the broker
    self.homeassistant    HomeAssistantV1
    self.timeline         TimelineV1   cues: which sound is heard where and when
    self.devices          DevicesV1    devices of any kind: lights, displays, ...

A facade's methods keep their names, arguments and results for every 1.x
API version: new arguments are optional and new methods are only added.
Behind it the core may change freely. When API 2 comes, the V1 facades stay
for 1.x plugins next to the V2 ones.

Every failure is an ActionError (plugins/actions.py) with a code:
invalid, not_found, conflict or unavailable, as for self.actions.
"""
from __future__ import annotations

from typing import Callable, Optional

from sonorium.plugins.actions import ActionError


def _unavailable(what: str) -> ActionError:
    return ActionError("unavailable", f"{what} isn't available in this Sonorium")


async def _call(work):
    """Run a core call (a coroutine, or a function, sync or async), its errors as ActionError."""
    import inspect
    from sonorium.core.announce import AnnouncementsOff, TtsUnavailable
    from sonorium.core.audio_outputs import OutputBusy, OutputError
    from sonorium.core.decode import DecodeError
    from sonorium.core.overlay import OverlayError
    from sonorium.core.ramps import RampError
    from sonorium.plugins.commands import CommandError
    from sonorium.plugins.homeassistant import HomeAssistantError
    from sonorium.plugins.tts import TtsError
    try:
        result = work() if callable(work) else work
        return (await result) if inspect.isawaitable(result) else result
    except ActionError:
        raise
    except LookupError as e:
        raise ActionError("not_found", str(e).strip("'\""))
    except AnnouncementsOff as e:
        raise ActionError("conflict", str(e))
    except OutputBusy as e:
        raise ActionError("conflict", e.message)
    except TtsUnavailable as e:
        raise ActionError("unavailable", str(e))
    except (OverlayError, DecodeError, RampError, OutputError, CommandError, ValueError) as e:
        raise ActionError("invalid", str(e))
    except (HomeAssistantError, TtsError) as e:
        raise ActionError("unavailable", str(e))


class AudioV1:
    def __init__(self, host, plugin_id: str):
        self._host, self._id = host, plugin_id

    def _svc(self):
        if self._host.overlay is None:
            raise _unavailable("Playing sounds")
        return self._host.overlay

    async def play(self, channels, source=None, *, samples=None, sample_rate: Optional[int] = None,
                   volume: float = 100, duck=True, policy: str = "queue", priority: int = 0,
                   max_seconds: Optional[float] = None, start_stopped: bool = False, override: bool = False,
                   label: str = "", announcement: bool = False) -> dict:
        """
        Play a sound over channels: source is a file path, an http(s) URL or the
        bytes of an audio file; or samples (numpy, -1..1 or int16) with
        sample_rate. duck: True (the Advanced defaults), False, or {"db",
        "attack", "release"}. policy: now, queue, replace. override: skip
        zone gates (admin-level: announcements, alarms). announcement: it is
        one, so channels with announcements off skip it.
        """
        return await _call(self._svc().play(
            channels, source, samples=samples, sample_rate=sample_rate, volume=volume, duck=duck, policy=policy,
            priority=priority, max_seconds=max_seconds, start_stopped=start_stopped, override=override,
            label=label, origin=f"plugin:{self._id}", announcement=bool(announcement)))

    async def cancel(self, sound_id: str, channel: Optional[str] = None) -> bool:
        return self._svc().cancel(sound_id, channel)

    async def sounds(self) -> list[dict]:
        return self._svc().sounds()

    def add_gate(self, gate: Callable) -> None:
        """gate(channel, request) -> None to allow a sound, or why it's refused. One per plugin."""
        self._svc().add_gate(self._id, gate)

    def remove_gate(self) -> None:
        self._svc().remove_gate(self._id)

    def register_tts(self, provider_id: str, title: str, speak: Callable, ready: Optional[Callable] = None) -> None:
        """
        Provide text to speech: async speak(message, language=None, voice=None, options=None) -> bytes.
        ready() (optional): whether it can speak now (e.g. its service is reachable).
        """
        self._host.tts.register(self._id, provider_id, title, speak, ready)

    def tts_providers(self) -> list[dict]:
        return self._host.tts.providers()

    async def tts(self, message: str, provider: Optional[str] = None, **options) -> bytes:
        """The audio of a spoken message (bytes of an audio file)."""
        return await _call(self._host.tts.speak(message, provider, **options))

    # Listening (core/taps.py): read-only, never in the way of playback

    def tap(self, channel: str, handler: Callable, track: Optional[str] = None):
        """
        handler(block) for each block a channel plays (core/taps.py TapBlock:
        samples int16 (1, 1024) read-only, mixer_sample, mixer_at), in its own
        thread (an async handler runs on the event loop, waited for). track: one
        track's blocks before mixing instead (a copy per block: opt-in). Closed
        when the plugin is switched off. Returns the Tap (close() ends it).
        """
        from sonorium.core.taps import TapError
        taps = getattr(self._host, "taps", None)
        if taps is None:
            raise _unavailable("Audio taps")
        try:
            return taps.tap(channel, handler, track=track, owner=self._id)
        except LookupError as e:
            raise ActionError("not_found", str(e).strip("'\""))
        except TapError as e:
            raise ActionError("invalid", str(e))

    def untap(self) -> int:
        """Close every tap of the plugin. Returns how many."""
        taps = getattr(self._host, "taps", None)
        return taps.close_owner(self._id) if taps is not None else 0

    async def decode(self, theme_id: str, key: str, max_seconds: Optional[float] = None):
        """
        One of a theme's own files, by track key ("Thunder/Close"), decoded by
        Sonorium: numpy float32 in -1..1, mono, 44.1 kHz (at most max_seconds;
        default Settings' decode limit, core/taps.py LIMITS).
        """
        import asyncio
        from sonorium.core.taps import TapError, decode_theme_file
        sm = getattr(self._host, "session_manager", None)
        folder = sm.theme_folder(theme_id) if sm is not None and hasattr(sm, "theme_folder") else None
        if folder is None:
            raise ActionError("not_found", f"Theme not found: {theme_id}")
        try:
            return await asyncio.to_thread(decode_theme_file, folder, key, max_seconds)
        except LookupError as e:
            raise ActionError("not_found", str(e).strip("'\""))
        except TapError as e:
            raise ActionError("invalid", str(e))

    async def announce(self, channels, text: Optional[str] = None, *, sound=None, provider: Optional[str] = None,
                       language: Optional[str] = None, volume: float = 100, duck=True,
                       start_stopped: bool = True) -> dict:
        """
        An announcement (core/announce.py): a chime (sound: URL, path or bytes)
        and/or text spoken, ducking the channels' mix; admin-level (overrides
        zones and quiet hours), starts stopped channels for its length.
        channels: ids, names, or "all". Channels with Announcements off are
        left out ("skipped"). duck: True, False, dB, or {db, attack, release}.
        Announcements switched off in Settings > Advanced: ActionError "conflict".
        """
        from sonorium.core.announce import announce_to
        return await _call(announce_to(self._svc(), self._host.tts, channels, text=text, sound=sound, volume=volume,
                                       duck=duck, provider=provider, language=language,
                                       start_stopped=start_stopped, origin=f"plugin:{self._id}"))


class RampsV1:
    def __init__(self, host, plugin_id: str):
        self._host, self._id = host, plugin_id

    def _svc(self):
        if self._host.ramps is None:
            raise _unavailable("Volume ramps")
        return self._host.ramps

    async def start(self, channel: str, to: float, seconds: float, *, from_level: Optional[float] = None,
                    curve: str = "db", then: Optional[str] = None, start: bool = False) -> dict:
        """Ramp the channel's mix to `to` (0-1, up to 4) over `seconds`; then: None, "stop" or "pause"."""
        return await _call(self._svc().start(channel, to, seconds, from_level=from_level, curve=curve, then=then,
                                             start=start, origin=f"plugin:{self._id}"))

    async def cancel(self, channel: str, reset: bool = False) -> Optional[dict]:
        return await _call(lambda: (self._svc().cancel(channel, reset)))

    async def status(self, channel: str) -> dict:
        return await _call(lambda: (self._svc().status(channel)))


class OutputsV1:
    def __init__(self, host, plugin_id: str):
        self._host, self._id = host, plugin_id

    def _svc(self):
        if self._host.outputs is None:
            raise _unavailable("Audio outputs")
        return self._host.outputs

    def register_type(self, output_type) -> None:
        """An OutputType (sonorium.core.audio_outputs): removed when the plugin is disabled."""
        try:
            self._svc().register_type(self._id, output_type)
        except ValueError as e:
            raise ActionError("invalid", str(e))

    def unregister_types(self) -> None:
        self._svc().unregister_owner(self._id)

    async def types(self) -> list[dict]:
        """[{"id", "title", "plugin_id", "available", "message", "local"}]"""
        return self._svc().types()

    async def devices(self, type_id: str) -> list[dict]:
        return await _call(self._svc().discover(type_id))

    async def attach(self, channel: str, type_id: str, device_id: str, options: Optional[dict] = None,
                     take_over: bool = False) -> dict:
        """Add the device to the channel's speakers (it plays at once if the channel plays)."""
        return await _call(lambda: self._svc().attach(channel, type_id, device_id, options, take_over=take_over))

    async def detach(self, channel: str, attachment_id: str) -> bool:
        """Take the device out of the channel's speakers and close its sink."""
        return await _call(lambda: self._svc().detach(channel, attachment_id))

    async def attachments(self, channel: Optional[str] = None) -> list[dict]:
        return self._svc().attachments(channel)


class CommandsV1:
    def __init__(self, host, plugin_id: str):
        self._host, self._id = host, plugin_id

    async def received(self, map_id: str, code, value=None) -> dict:
        """A code came in on one of the plugin's maps: the user's entries for it run (or it's learnt)."""
        return await self._host.commands.received(self._id, map_id, code, self._host.actions_for(self._id), value)

    async def entries(self, map_id: str) -> list[dict]:
        return self._host.commands.entries(self._id, map_id)

    async def set_entries(self, map_id: str, entries: list) -> list[dict]:
        return await _call(lambda: (self._host.commands.set_entries(self._id, map_id, entries)))


class MqttV1:
    def __init__(self, host, plugin_id: str):
        self._host, self._id = host, plugin_id

    @property
    def available(self) -> bool:
        return self._host.mqtt.available

    @property
    def prefix(self) -> str:
        """Sonorium's topic prefix ("sonorium"): its entities' topics start with it."""
        return self._host.mqtt.prefix

    @property
    def device_info(self) -> dict:
        """The "device" of Sonorium's Home Assistant entities, to group a plugin's entities with them."""
        return dict(self._host.mqtt.device_info)

    async def publish(self, topic: str, payload, retain: bool = False) -> bool:
        return await _call(self._host.mqtt.publish(topic, payload, retain))

    def subscribe(self, topic_filter: str, handler: Callable) -> None:
        """handler(topic, payload) for messages on the filter ("+" and "#" wildcards); async or not."""
        try:
            self._host.mqtt.subscribe(self._id, topic_filter, handler)
        except ValueError as e:
            raise ActionError("invalid", str(e))

    def unsubscribe(self, topic_filter: Optional[str] = None) -> int:
        return self._host.mqtt.unsubscribe(self._id, topic_filter)


class HomeAssistantV1:
    def __init__(self, host, plugin_id: str):
        self._host, self._id = host, plugin_id

    def _svc(self):
        if self._host.homeassistant is None:
            raise _unavailable("Home Assistant")
        return self._host.homeassistant

    @property
    def available(self) -> bool:
        return self._host.homeassistant is not None and self._host.homeassistant.available

    async def request(self, method: str, path: str, json=None) -> tuple[int, object]:
        return await _call(self._svc().request(method, path, json))

    async def fetch(self, path: str) -> bytes:
        return await _call(self._svc().fetch(path))

    async def tts(self, message: str, engine: Optional[str] = None, language: Optional[str] = None,
                  options: Optional[dict] = None) -> bytes:
        return await _call(self._svc().tts(message, engine, language, options))

    async def tts_engines(self) -> list[str]:
        return await _call(self._svc().tts_engines())


class TimelineV1:
    """The cue timeline (core/timeline.py): sounds announced before they are heard."""

    def __init__(self, host, plugin_id: str):
        self._host, self._id = host, plugin_id

    def _svc(self):
        timeline = getattr(self._host, "timeline", None)
        if timeline is None:
            raise _unavailable("The cue timeline")
        return timeline

    def subscribe(self, handler: Callable, channels=None, types=None):
        """
        handler(event) for cue.scheduled, cue.cancelled and cue.ended (types:
        some of them) of these channels (ids; None: every channel). An async
        handler runs on the event loop, a plain one in a thread. Removed when
        the plugin is disabled. Returns the Subscription (close() ends it).
        """
        if channels is not None and (isinstance(channels, str) or not all(isinstance(c, str) for c in channels)):
            raise ActionError("invalid", "channels: a list of channel ids, or None for every channel")
        try:
            return self._svc().subscribe(handler, channels, owner=self._id, types=types)
        except ValueError as e:
            raise ActionError("invalid", str(e))

    def unsubscribe(self) -> int:
        """End every subscription of the plugin. Returns how many."""
        return self._svc().unsubscribe_owner(self._id)

    def pending(self, channel: Optional[str] = None) -> list[dict]:
        """The cues scheduled and not yet ended (one channel's, or every channel's)."""
        return [{k: v for k, v in c.items() if not k.startswith("_")}
                for c in self._svc().pending(channel) if c.get("_audience") is None]


class DevicesV1:
    """Devices of any kind (core/devices.py): Sonorium lists, names, places and assigns them."""

    def __init__(self, host, plugin_id: str):
        self._host, self._id = host, plugin_id

    def _svc(self):
        devices = getattr(self._host, "devices", None)
        if devices is None:
            raise _unavailable("Devices")
        return devices

    @staticmethod
    async def _guard(work):
        from sonorium.core.devices import DeviceError
        try:
            return await _call(work)
        except DeviceError as e:
            raise ActionError("invalid", str(e))

    def register_kind(self, kind: dict) -> dict:
        """A kind of device (as get_device_kinds() gives them); gone when the plugin is switched off."""
        from sonorium.core.devices import DeviceError
        try:
            return self._svc().register_kind(self._id, kind)
        except DeviceError as e:
            raise ActionError("invalid", str(e))

    def unregister_kinds(self) -> None:
        self._svc().unregister_owner(self._id)

    def kinds(self) -> list[dict]:
        """Every kind registered now, by any plugin."""
        return self._svc().kinds()

    async def discover(self, kind: Optional[str] = None) -> list[dict]:
        """List the devices of one of the plugin's kinds again (or all of them)."""
        svc = self._svc()
        if kind is not None and (svc.kind(kind) or {}).get("plugin_id") != self._id:
            raise ActionError("not_found", f"The plugin has no device kind '{kind}'")
        kinds = [kind] if kind else [k["id"] for k in svc.kinds() if k["plugin_id"] == self._id]
        out = []
        for one in kinds:
            out.extend(await self._guard(svc.discover(one)))
        return out

    def list(self, kind: Optional[str] = None) -> list[dict]:
        """The devices of a kind (any plugin's, so plugins can work together through the kind contracts)."""
        return self._svc().list(kind)

    def get(self, device_id: str) -> dict:
        try:
            return self._svc().get(device_id)
        except LookupError as e:
            raise ActionError("not_found", str(e).strip("'\""))

    async def assigned(self, channel_id: str, kind: Optional[str] = None) -> list[dict]:
        """The devices (switched on) following a channel: assigned to it, or in an area of its speakers."""
        return self._svc().assigned(channel_id, kind)

    def following(self, device_id: str) -> list[str]:
        """The channels a device follows now."""
        try:
            return self._svc().following(device_id)
        except LookupError as e:
            raise ActionError("not_found", str(e).strip("'\""))

    def set_status(self, device: str, online: Optional[bool] = None, error: Optional[str] = None) -> dict:
        """One of the plugin's devices (core id or its own): online or not, its last error ("" clears it)."""
        try:
            return self._svc().set_status(self._id, device, online, error)
        except LookupError as e:
            raise ActionError("not_found", str(e).strip("'\""))

    async def invoke(self, device_id: str, action: str, params: Optional[dict] = None) -> dict:
        """An action on any plugin's device, through its owner (the kind contracts in the docs say which)."""
        return await self._guard(self._svc().action(device_id, action, params))


FACADES_V1 = {"audio": AudioV1, "ramps": RampsV1, "audio_outputs": OutputsV1, "commands": CommandsV1,
              "mqtt": MqttV1, "homeassistant": HomeAssistantV1, "timeline": TimelineV1,
              "devices": DevicesV1}


def facade(host, plugin_id: str, name: str, api_major: int = 1):
    """The facade a plugin of this API major version gets for a service."""
    table = {1: FACADES_V1}.get(api_major)
    if table is None or name not in table:
        raise _unavailable(name)
    return table[name](host, plugin_id)
