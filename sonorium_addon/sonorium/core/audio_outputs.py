"""
Audio outputs: devices a plugin plays a channel on, as speakers next to the
network speakers (the sound devices of the machine Sonorium runs on, a
Bluetooth speaker the OS exposes, a file).

A plugin registers an output type; Sonorium asks it for its devices and
opens a sink on one for each channel that plays there:

    class HostAudio(OutputType):
        id = "host_audio"
        title = "This computer's audio"
        local = True                    # devices of the machine Sonorium runs on
        def available(self):            return (True, "") or (False, "why not")
        async def discover(self):       return [{"id": "hw:0", "name": "Built-in speakers", "api": "ALSA"}]
        def open(self, device_id, sample_rate, options):  return MySink(...)

    class MySink(Sink):
        def write(self, block):         # int16 numpy array, shape (1, n), mono, sample_rate
                                        # (a type with stereo = True: (2, n), left and right)
            ...                         # may block: it runs in the attachment's own thread
        def close(self): ...

Every device a type lists is remembered (store section "output_devices",
by speaker id) and is a speaker: its id is "output:<type id>:<16 hex of
sha256(device id)>", it is in the registry's speakers (type "output", with
an "output" dict of live details), it has the Use switch, a name, a room
and an Offset in Settings > Speakers like any speaker, and channels have
it in their speakers. The session manager plays it through this module
(add_speaker_kind): start() opens a sink for each of a playing channel's
device speakers, stop() ends them after the stop fade.

An attachment is one channel on one device: a thread reading the channel's
broadcast like a speaker does (it counts as a listener, it hears the
speakers-muted switch, a slow sink only falls behind itself), scaling the
blocks by the channel's volume with the speaker's Offset (a linear gain on
the samples, never the device's own volume), and calling write() with
each. Options given with attach() are kept per channel and device (store
section "audio_outputs") and passed to every open() for it.

Local devices: a type with local = True lists hardware of the machine
Sonorium runs on (its sound cards, USB and HDMI audio, Bluetooth paired to
it). At most one local device plays at a time, across every local type and
every channel; a channel that wants one while another channel plays a
local device asks first (take_over). The setting "Allow output to more than
one local device" (allow_multiple_local_outputs) lifts this: local devices
then play like network speakers.

Mono or stereo: a sink gets mono blocks (1, n), whatever the channel plays
(a stereo mix, Settings > Audio Settings > Stereo, is folded to mono for it:
left and right averaged). A type with stereo = True gets (2, n) blocks, left
then right, always (a mono mix on both sides).

Sonorium ships no output type of its own except CaptureOutput below (a test
double that keeps what it was sent).
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import threading
import time
from typing import Callable, Optional

import numpy as np

from sonorium.decoders import to_channels
from sonorium.obs import logger

TYPE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
DISCOVER_TIMEOUT = 10.0
MAX_ATTACHMENTS_PER_CHANNEL = 8
SPEAKER_PREFIX = "output:"
SPEAKER_TYPE = "output"
TEST_VOLUME = 0.2  # the test sound's level, as for network speakers
TEST_BLOCK = 1024
JOIN_SECONDS = 5.0  # how long the old local device's sink may take to close
SWITCHED_OFF = "Switch the speaker on in Settings › Speakers first"


def speaker_id_for(type_id: str, device_id: str) -> str:
    """A device's speaker id: URL-safe, made by Sonorium (the device id never goes in a URL)."""
    digest = hashlib.sha256(device_id.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{SPEAKER_PREFIX}{type_id}:{digest}"


class OutputError(ValueError):
    """An output that can't be used (the message is shown to the caller)."""


class OutputBusy(Exception):
    """
    The device, or another local device (one plays at a time), plays
    another channel (409, ActionError "conflict"). take_over: answering
    with take_over would move it.
    """

    def __init__(self, message: str, take_over: bool = False):
        super().__init__(message)
        self.message = message
        self.take_over = take_over


class OutputFailed(Exception):
    """
    A plugin's output type raised something other than OutputError or
    ActionError: "<type title>: <message>" (the route answers 502).
    """

    def __init__(self, title: str, error: BaseException):
        self.title = title
        self.error = error
        super().__init__(f"{title}: {str(error) or type(error).__name__}")


def _raise_plugin_failure(type_id: str, output_type, what: str, error: Exception) -> None:
    """
    Raise what a plugin's output code raised as the caller should see it:
    OutputError and ActionError as they are, anything else (logged with its
    traceback) as OutputFailed.
    """
    from sonorium.plugins.actions import ActionError
    if isinstance(error, (OutputError, ActionError)):
        raise error
    title = getattr(output_type, "title", "") or type_id
    logger.error(f"Audio output {type_id}: {what} failed: {error}", exc_info=error)
    raise OutputFailed(title, error) from error


def _message(error: BaseException) -> str:
    return getattr(error, "message", None) or str(error) or type(error).__name__


class Sink:
    """Where an attachment writes the channel's audio."""
    latency: float = 0.0  # seconds from write() to sound (shown to the user)

    def write(self, block: np.ndarray) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class OutputType:
    """A kind of output a plugin provides."""
    id: str = ""
    title: str = ""
    local: bool = False  # devices of the machine Sonorium runs on (read at register_type())
    stereo: bool = False  # its sinks take (2, n) stereo blocks; else (1, n) mono (read at each open())

    def available(self) -> tuple[bool, str]:
        """(usable here, why not): e.g. no sound card in this container."""
        return True, ""

    async def discover(self) -> list[dict]:
        """The devices: [{"id", "name", "api"?, "is_default"?, "channels"?, "sample_rates"?}]."""
        return []

    def open(self, device_id: str, sample_rate: int, options: dict) -> Sink:
        raise NotImplementedError


class CaptureSink(Sink):
    def __init__(self, store: list):
        self.store = store
        self.closed = False

    def write(self, block):
        self.store.append(np.array(block, copy=True))

    def close(self):
        self.closed = True


class CaptureOutput(OutputType):
    """A test double: keeps every block it is sent, per device. Not registered by default."""
    id = "capture"
    title = "Capture (test)"

    def __init__(self):
        self.blocks: dict[str, list] = {}
        self.sinks: list[CaptureSink] = []

    async def discover(self):
        return [{"id": "memory", "name": "Memory"}]

    def open(self, device_id, sample_rate, options):
        sink = CaptureSink(self.blocks.setdefault(device_id, []))
        self.sinks.append(sink)
        return sink


def _scaled(block: np.ndarray, gain) -> np.ndarray:
    """A copy of the block times gain (a number, or one per sample)."""
    return np.clip(block.astype(np.float32) * gain, -32768, 32767).astype(block.dtype)


class Attachment:
    """One channel on one device: a thread copying its broadcast to the sink."""

    def __init__(self, attachment_id: str, session_id: str, type_id: str, device_id: str, options: dict,
                 speaker_id: str = "", level: Optional[Callable[[], float]] = None,
                 on_end: Optional[Callable[["Attachment"], None]] = None, stereo: bool = False):
        self.id = attachment_id
        self.channels = 2 if stereo else 1  # what its sink is given: mono (1, n) or stereo (2, n)
        self.session_id = session_id
        self.type_id = type_id
        self.device_id = device_id
        self.speaker_id = speaker_id or speaker_id_for(type_id, device_id)
        self.options = options
        self.level = level or (lambda: 1.0)  # the channel's volume with the speaker's Offset, 0..1
        self.on_end = on_end
        self.sink: Optional[Sink] = None
        self.channel = None
        self.error = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def playing(self) -> bool:
        """Open and fed, not told to stop."""
        return self.running and not self._stop.is_set()

    def start(self, channel, sink: Sink) -> None:
        self.channel, self.sink = channel, sink
        self._thread = threading.Thread(target=self._run, name=f"output-{self.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: Optional[float] = None) -> bool:
        """Wait for the writer thread (close() included). True when it has ended."""
        if self._thread is not None:
            self._thread.join(timeout)
        return not self.running

    def _target(self, channel) -> float:
        if channel.speakers_muted:
            return 0.0
        try:
            return max(0.0, min(1.0, float(self.level())))
        except Exception:
            return 1.0

    def _run(self) -> None:
        from sonorium.core.channel import ChannelState
        channel, sink = self.channel, self.sink
        channel.client_connected()
        last = channel.get_current_sequence()
        gain = self._target(channel)
        try:
            while not self._stop.is_set() and (channel.state == ChannelState.PLAYING or channel._generator_running):
                fresh = channel.get_chunks_since(last)
                if not fresh:
                    channel.wait_for_data(timeout=0.05)
                    continue
                channel.mark_listener_active()
                for last, block in fresh:
                    block = to_channels(block, self.channels)
                    target = self._target(channel)
                    if target != gain:  # mute, volume or Offset changed: ramp over this block
                        block = _scaled(block, np.linspace(gain, target, block.shape[-1], dtype=np.float32))
                        gain = target
                    elif gain != 1.0:
                        block = _scaled(block, gain)
                    sink.write(block)
        except Exception as e:
            self.error = str(e) or type(e).__name__
            logger.warning(f"Audio output {self.type_id}/{self.device_id} stopped: {self.error}")
        finally:
            channel.client_disconnected()
            try:
                sink.close()
            except Exception:
                pass
            if self.on_end is not None:
                try:
                    self.on_end(self)
                except Exception:
                    pass

    def to_dict(self) -> dict:
        return {"attachment_id": self.id, "channel_id": self.session_id, "type": self.type_id,
                "device": self.device_id, "options": self.options, "running": self.playing,
                "latency": getattr(self.sink, "latency", 0.0) if self.sink else 0.0, "error": self.error}


class AudioOutputs:
    """The output types plugins registered, their devices as speakers, and which channels play on them."""

    def __init__(self, session_manager, store=None, rate: int = 44100):
        self.sessions = session_manager
        self.store = store
        self.rate = rate
        self._types: dict[str, tuple[str, OutputType, bool]] = {}  # type id -> (owner, type, local)
        self._live: dict[tuple, Attachment] = {}                   # (session id, speaker id) -> Attachment
        self._failed: dict[tuple, str] = {}                        # (session id, speaker id) -> why open() failed
        self._online: dict[str, set] = {}                          # type id -> device ids of the last discover()
        self._rows: dict[str, dict] = {}                           # speaker id -> live details (Speaker.output)
        self._tests: dict[str, Attachment] = {}                    # speaker id -> Test playing
        self._tasks: set = set()
        if session_manager is not None and hasattr(session_manager, "add_speaker_kind"):
            session_manager.add_speaker_kind(self)
        registry = getattr(session_manager, "registry", None)
        if registry is not None and hasattr(registry, "add_speaker_source"):
            registry.add_speaker_source(self.speaker_entries)
        self._migrate()
        if self._devices():  # remembered devices are listed (offline) until their plugin registers
            self._refresh_registry()

    # --- Storage ---

    def _section(self, name: str) -> dict:
        if self.store is None:
            if not hasattr(self, "_memory"):
                self._memory = {}
            return self._memory.setdefault(name, {})
        return self.store.main.section(name)

    def _devices(self) -> dict:
        """Remembered devices: speaker id -> {type, device, owner, type_title, name, api, ...}."""
        return self._section("output_devices")

    def _channel_entries(self) -> dict:
        """Per channel: [{"id", "speaker", "type", "device", "options"}] (attachment ids and options)."""
        return self._section("audio_outputs")

    def _save(self) -> None:
        if self.store is not None:
            self.store.main.save()

    def _settings(self):
        state = getattr(self.sessions, "state", None)
        return getattr(state, "settings", None)

    def _migrate(self) -> None:
        """
        Channels sent to an output before devices were speakers (plugin API
        1.1): the device becomes one of the channel's speakers, switched on.
        """
        try:
            changed = False
            for session_id, entries in list(self._channel_entries().items()):
                for entry in entries:
                    if entry.get("speaker"):
                        continue
                    sid = speaker_id_for(entry["type"], entry["device"])
                    entry["speaker"] = sid
                    self._devices().setdefault(sid, {"type": entry["type"], "device": entry["device"],
                                                     "owner": "", "type_title": entry["type"],
                                                     "name": entry["device"]})
                    changed = True
                    session = self.sessions.get(session_id) if self.sessions is not None else None
                    settings = self._settings()
                    if session is None or settings is None:
                        continue
                    self._add_to_channel(session, sid)
                    if not settings.speaker_enabled(sid):
                        settings.enabled_speakers.append(sid)
            if changed:
                self._save()
        except Exception as e:
            logger.warning(f"Audio outputs: could not update channels saved before 1.2: {e}")

    # --- Types ---

    def register_type(self, owner: str, output_type: OutputType) -> None:
        type_id = getattr(output_type, "id", "")
        if not isinstance(type_id, str) or not TYPE_ID.match(type_id):
            raise OutputError("An output type needs an id: letters, digits, '_', '-', '.'")
        if type_id in self._types and self._types[type_id][0] != owner:
            raise OutputError(f"The output type '{type_id}' is already registered by {self._types[type_id][0]}")
        local = bool(getattr(output_type, "local", False))
        self._types[type_id] = (owner, output_type, local)
        for sid, record in self._devices().items():
            if record.get("type") == type_id:
                record["owner"] = owner
                record["type_title"] = self._title(type_id)
                self._update_row(sid)
        self._spawn(self._registered(type_id, output_type))

    async def _registered(self, type_id: str, output_type: OutputType) -> None:
        """A type was registered: list its devices, then play the playing channels that have one."""
        if self._types.get(type_id, (None, None))[1] is not output_type:
            return
        try:
            await self.discover(type_id)
        except Exception as e:
            logger.warning(f"Audio output {type_id}: devices not listed: {_message(e)}")
            self._refresh_registry()
        if self._types.get(type_id, (None, None))[1] is not output_type or self.sessions is None:
            return
        for session in self._sessions():
            if not session.is_playing:
                continue
            ids = [sid for sid in self._enabled_devices(session) if self._type_of(sid) == type_id]
            if ids:
                await self.start(session, ids)

    def _spawn(self, coro) -> None:
        """Run a coroutine: as a task on the running loop, else to the end here (no loop: tests, threads)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(coro)
            return
        task = loop.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def unregister_owner(self, owner: str) -> None:
        """The plugin is off: its types go and their sinks stop. Its devices stay, offline."""
        gone = [t for t, (o, _, _) in self._types.items() if o == owner]
        for type_id in gone:
            for attachment in [a for a in self._live.values() if a.type_id == type_id]:
                attachment.stop()
            for attachment in [a for a in self._tests.values() if a.type_id == type_id]:
                attachment.stop()
            self._types.pop(type_id, None)
            self._online.pop(type_id, None)
        if gone:
            for sid in list(self._rows):
                self._update_row(sid)
            self._refresh_registry()

    def forget_owner(self, owner: str) -> None:
        """The plugin was uninstalled: its devices are forgotten and taken out of the channels."""
        self.unregister_owner(owner)
        for sid in [s for s, r in self._devices().items() if r.get("owner") == owner]:
            self._forget(sid)
        self._refresh_registry()

    def types(self) -> list[dict]:
        result = []
        for type_id, (owner, output_type, local) in sorted(self._types.items()):
            try:
                ok, why = output_type.available()
            except Exception as e:
                ok, why = False, str(e)
            result.append({"id": type_id, "title": output_type.title or type_id, "plugin_id": owner,
                           "available": bool(ok), "message": why or "", "local": local})
        return result

    def _type(self, type_id: str) -> OutputType:
        entry = self._types.get(type_id)
        if entry is None:
            raise LookupError(f"No output type '{type_id}'")
        return entry[1]

    def _title(self, type_id: str) -> str:
        entry = self._types.get(type_id)
        if entry is not None:
            return entry[1].title or type_id
        for record in self._devices().values():
            if record.get("type") == type_id and record.get("type_title"):
                return record["type_title"]
        return type_id

    def _local(self, type_id: Optional[str]) -> bool:
        entry = self._types.get(type_id) if type_id else None
        return bool(entry and entry[2])

    def _multiple_local(self) -> bool:
        """The setting "Allow output to more than one local device"."""
        return bool(getattr(self._settings(), "allow_multiple_local_outputs", False))

    def _one_local(self, type_id: Optional[str]) -> bool:
        """The type's devices are local and only one local device may play at a time."""
        return self._local(type_id) and not self._multiple_local()

    def _available(self, type_id: str) -> None:
        """OutputError when the type can't be used here (available() False); its own errors as raised."""
        output_type = self._type(type_id)
        try:
            ok, why = output_type.available()
        except Exception as e:
            _raise_plugin_failure(type_id, output_type, "available()", e)
        if not ok:
            raise OutputError(why or "This output can't be used here")

    async def discover(self, type_id: str) -> list[dict]:
        """The type's devices (filtered); each is remembered as a speaker."""
        output_type = self._type(type_id)
        try:
            devices = await asyncio.wait_for(output_type.discover(), DISCOVER_TIMEOUT)
        except asyncio.TimeoutError:
            raise OutputError("The output didn't list its devices in time")
        except Exception as e:
            _raise_plugin_failure(type_id, output_type, "discover()", e)
        result, seen = [], set()
        for device in devices or []:
            if isinstance(device, dict) and isinstance(device.get("id"), str) and device["id"]:
                if device["id"] not in seen:
                    seen.add(device["id"])
                    result.append(device)
        if self._types.get(type_id, (None, None))[1] is output_type:
            self._remember(type_id, result)
        return result

    async def discover_all(self) -> list[dict]:
        """
        Every registered type listed again (Settings > Speakers opened or
        Rescan, the channel editor's speaker picker): types() with "error"
        (why the listing failed, else "") and "speakers" (its devices'
        speaker ids, remembered ones included).
        """
        result = []
        for info in self.types():
            error = ""
            try:
                await self.discover(info["id"])
            except LookupError:
                continue
            except Exception as e:
                error = _message(e)
            info["error"] = error
            info["speakers"] = sorted(s for s, r in self._devices().items() if r.get("type") == info["id"])
            result.append(info)
        return result

    # --- Devices as speakers ---

    def _remember(self, type_id: str, devices: list[dict]) -> None:
        owner = self._types[type_id][0]
        changed = False
        for device in devices:
            sid = speaker_id_for(type_id, device["id"])
            record = self._devices().get(sid)
            fresh = {"type": type_id, "device": device["id"], "owner": owner, "type_title": self._title(type_id),
                     "name": str(device.get("name") or device["id"]),
                     "api": str(device.get("api") or "") if device.get("api") is not None else "",
                     "is_default": bool(device.get("is_default", False))}
            if isinstance(device.get("channels"), int) and not isinstance(device.get("channels"), bool):
                fresh["channels"] = device["channels"]
            rates = device.get("sample_rates")
            if isinstance(rates, list) and all(isinstance(r, int) and not isinstance(r, bool) for r in rates):
                fresh["sample_rates"] = list(rates)
            if record != fresh:
                self._devices()[sid] = fresh
                changed = True
        self._online[type_id] = {d["id"] for d in devices}
        if changed:
            self._save()
        for sid, record in self._devices().items():
            if record.get("type") == type_id:
                self._update_row(sid)
        self._refresh_registry()

    def _refresh_registry(self) -> None:
        registry = getattr(self.sessions, "registry", None)
        if registry is not None and hasattr(registry, "merge_extra_speakers"):
            try:
                registry.merge_extra_speakers()
            except Exception as e:
                logger.debug(f"Audio outputs: speakers not refreshed: {e}")

    def claims(self, speaker_id) -> bool:
        """A device speaker (played here, not through the media controller)."""
        return isinstance(speaker_id, str) and speaker_id.startswith(SPEAKER_PREFIX)

    def _type_of(self, speaker_id: str) -> Optional[str]:
        record = self._devices().get(speaker_id)
        return record.get("type") if record else None

    def _online_now(self, speaker_id: str) -> bool:
        record = self._devices().get(speaker_id)
        return bool(record and record.get("type") in self._types
                    and record.get("device") in self._online.get(record["type"], set()))

    def _update_row(self, speaker_id: str) -> dict:
        """The speaker's live details (one dict per speaker, shared with the registry's Speaker.output)."""
        record = self._devices().get(speaker_id) or {}
        row = self._rows.setdefault(speaker_id, {})
        type_id = record.get("type", "")
        failures = [why for (_, sid), why in list(self._failed.items()) if sid == speaker_id]
        live = [a for a in list(self._live.values()) if a.speaker_id == speaker_id]
        errors = failures + [a.error for a in live if a.error]
        row.update({
            "type": type_id, "type_title": self._title(type_id) if type_id else "",
            "plugin_id": record.get("owner", ""), "device": record.get("device", ""),
            "api": record.get("api", ""), "is_default": bool(record.get("is_default")),
            "channels": record.get("channels"), "sample_rates": record.get("sample_rates"),
            "local": self._local(type_id), "type_available": type_id in self._types,
            "playing": sorted({a.session_id for a in live if a.playing}),
            "error": errors[-1] if errors else "",
        })
        return row

    def speaker_entries(self) -> list[dict]:
        """For the registry (add_speaker_source): every remembered device as a speaker."""
        result = []
        for sid, record in self._devices().items():
            result.append({"entity_id": sid, "name": record.get("name") or record.get("device") or sid,
                           "type": SPEAKER_TYPE, "online": self._online_now(sid), "source": ["output"],
                           "output": self._update_row(sid)})
        return result

    def remove_device(self, speaker_id: str) -> None:
        """⋯ › Remove on an offline device: forgotten, and taken out of the channels that have it."""
        if speaker_id not in self._devices():
            raise LookupError("Speaker not found")
        if self._online_now(speaker_id):
            raise OutputError("Only an offline speaker can be removed")
        self._forget(speaker_id)
        self._refresh_registry()

    def _forget(self, speaker_id: str) -> None:
        for key in [k for k in self._live if k[1] == speaker_id]:
            self._live.pop(key).stop()
        for key in [k for k in self._failed if k[1] == speaker_id]:
            self._failed.pop(key, None)
        if self.sessions is not None:
            for session in self._sessions():
                if speaker_id in self._channel_devices(session):
                    try:
                        self._take_out(session, speaker_id)
                    except Exception as e:
                        logger.warning(f"Audio outputs: '{session.name}' still has a removed speaker: {e}")
        for session_id, entries in list(self._channel_entries().items()):
            kept = [e for e in entries if e.get("speaker") != speaker_id]
            if kept:
                self._channel_entries()[session_id] = kept
            else:
                self._channel_entries().pop(session_id, None)
        self._devices().pop(speaker_id, None)
        self._rows.pop(speaker_id, None)
        settings = self._settings()
        if settings is not None:
            settings.speaker_settings.pop(speaker_id, None)
            if speaker_id in settings.enabled_speakers:
                settings.enabled_speakers.remove(speaker_id)
            self.sessions.state.save()
        self._save()

    # --- Channels' device speakers ---

    def _sessions(self) -> list:
        lister = getattr(self.sessions, "list", None)
        return list(lister()) if lister is not None else []

    def _channel_devices(self, session) -> list[str]:
        """The channel's device speakers, switched on or not."""
        resolve = getattr(self.sessions, "_resolve_selection", None)
        ids = resolve(session) if resolve is not None else []
        return [sid for sid in ids if self.claims(sid)]

    def _enabled_devices(self, session) -> list[str]:
        settings = self._settings()
        return [sid for sid in self._channel_devices(session) if settings is None or settings.speaker_enabled(sid)]

    def _entry(self, session_id: str, speaker_id: str, options: Optional[dict] = None) -> dict:
        """The channel's entry for a device (attachment id and options), made when missing."""
        record = self._devices().get(speaker_id) or {}
        entries = self._channel_entries().setdefault(session_id, [])
        entry = next((e for e in entries if e.get("speaker") == speaker_id), None)
        if entry is None:
            entry = {"id": "a_" + secrets.token_hex(5), "speaker": speaker_id, "type": record.get("type", ""),
                     "device": record.get("device", ""), "options": dict(options or {})}
            entries.append(entry)
            self._save()
        return entry

    def _selection(self, session):
        """A copy of the channel's speaker choice as an ad-hoc selection (a group's choice copied)."""
        from sonorium.core.state import SpeakerSelection
        source = session.adhoc_selection
        if session.speaker_group_id:
            source = self.sessions.state.speaker_groups.get(session.speaker_group_id) or source
        fields = ("include_floors", "include_areas", "include_speakers", "exclude_areas", "exclude_speakers")
        return SpeakerSelection(**{f: list(getattr(source, f, None) or []) for f in fields})

    def _resolves(self, selection, speaker_id: str) -> bool:
        from sonorium.core.state import Session
        probe = Session(id="", name="", adhoc_selection=selection)
        return speaker_id in self.sessions._resolve_selection(probe)

    def _set_selection(self, session, selection) -> None:
        self.sessions.update(session.id, adhoc_selection=selection)

    def _add_to_channel(self, session, speaker_id: str) -> None:
        selection = self._selection(session)
        if speaker_id in selection.exclude_speakers:
            selection.exclude_speakers.remove(speaker_id)
        if not self._resolves(selection, speaker_id):
            selection.include_speakers.append(speaker_id)
        self._set_selection(session, selection)

    def _take_out(self, session, speaker_id: str) -> None:
        """The device leaves the channel's speakers (saved)."""
        selection = self._selection(session)
        while speaker_id in selection.include_speakers:
            selection.include_speakers.remove(speaker_id)
        if self._resolves(selection, speaker_id):  # in a room the channel plays
            selection.exclude_speakers.append(speaker_id)
        self._set_selection(session, selection)

    def _level(self, session, speaker_id: str) -> Callable[[], float]:
        """The gain for the blocks: the channel's volume with the speaker's Offset (linear, 0..1)."""
        from sonorium.core.speaker_settings import offset_volume

        def level() -> float:
            settings = self._settings()
            offset = ((settings.speaker_settings.get(speaker_id) or {}).get("volume_offset", 0)
                      if settings is not None else 0)
            return offset_volume(session.volume / 100.0, offset)
        return level

    def _ended(self, attachment: Attachment) -> None:
        """A writer thread ended (after close())."""
        if attachment.speaker_id in self._rows:
            self._update_row(attachment.speaker_id)

    # --- One local device at a time ---

    def _name(self, speaker_id: str) -> str:
        """The device's name as Settings > Speakers shows it (renamed or its own)."""
        registry = getattr(self.sessions, "registry", None)
        speaker = registry.get_speaker(speaker_id) if registry is not None and hasattr(registry, "get_speaker") else None
        if speaker is not None and getattr(speaker, "name", None):
            return speaker.name
        record = self._devices().get(speaker_id) or {}
        return record.get("name") or record.get("device") or speaker_id

    def _playing_local(self, other_than: Optional[str] = None) -> list[Attachment]:
        """The local devices playing a channel (other than this one)."""
        return [a for a in self._live.values()
                if self._local(a.type_id) and a.playing and a.session_id != other_than]

    def _busy_message(self, attachment: Attachment) -> str:
        return f"{self._name(attachment.speaker_id)} is playing '{self._session_name(attachment)}'"

    def conflicts(self, session, speaker_ids: Optional[list] = None) -> list[str]:
        """
        For a channel about to play: when one of its (switched on) devices is
        local and another channel plays a local device now, a sentence saying
        which (only one local device plays at a time).
        """
        ids = speaker_ids if speaker_ids is not None else self._enabled_devices(session)
        if not any(self._one_local(self._type_of(sid)) for sid in ids if self.claims(sid)):
            return []
        busy = self._playing_local(other_than=session.id)
        return [self._busy_message(busy[0])] if busy else []

    async def _take_over(self, session) -> None:
        """Other channels play local devices: each leaves its channel's speakers for good, its sink closes."""
        for attachment in self._playing_local(other_than=session.id):
            other = self.sessions.get(attachment.session_id)
            attachment.stop()
            if other is None:
                continue
            logger.info(f"  '{session.name}' takes over from '{other.name}' ({self._name(attachment.speaker_id)})")
            try:
                self._take_out(other, attachment.speaker_id)
            except Exception as e:
                logger.warning(f"  '{other.name}': the device wasn't taken out of its speakers: {e}")
            await asyncio.to_thread(attachment.join, JOIN_SECONDS)
            if other.is_playing and not self.sessions.get_resolved_speakers(other):
                try:
                    await self.sessions.stop(other.id, reason="taken over")
                except Exception as e:
                    logger.warning(f"  '{other.name}' not stopped: {e}")

    async def take_over(self, session) -> None:
        """
        The channel editor's Move: the channel has a local device, so the
        other channels playing a local device lose it (and stop when it was
        their last speaker); a playing channel starts on its devices.
        """
        ids = self._enabled_devices(session)
        if not any(self._one_local(self._type_of(sid)) for sid in ids):
            return
        await self._take_over(session)
        if session.is_playing:
            await self.start(session, ids, take_over=True)

    async def _closed_local(self, other_than: Optional[str] = None) -> bool:
        """Every other local sink has closed (one local device plays at a time)."""
        pending = [a for a in list(self._live.values()) + list(self._tests.values())
                   if self._local(a.type_id) and a.running and a.session_id != other_than]
        for attachment in pending:
            attachment.stop()
            await asyncio.to_thread(attachment.join, JOIN_SECONDS)
        return not any(a.running for a in pending)

    # --- Playing (the session manager's speaker kind) ---

    async def start(self, session, speaker_ids: list, take_over: Optional[bool] = None) -> None:
        """
        The channel plays: open a sink on each of these device speakers that
        isn't playing it already. A failure is logged and shown on the
        speaker (no retry until the next start). A local device while
        another channel plays a local device: taken over only with
        take_over=True (one local device at a time).
        """
        for sid in speaker_ids:
            try:
                await self._start_one(session, sid, take_over, raise_errors=False)
            except Exception as e:  # never stops the channel's other speakers
                logger.warning(f"  '{session.name}': {sid} not started: {_message(e)}")

    async def _start_one(self, session, sid: str, take_over: Optional[bool], raise_errors: bool) -> Optional[Attachment]:
        key = (session.id, sid)
        live = self._live.get(key)
        if live is not None and live.playing:
            return live
        record = self._devices().get(sid)
        if record is None:
            return self._fail(key, "This device isn't known any more", raise_errors)
        type_id = record["type"]
        if type_id not in self._types:
            self._failed.pop(key, None)  # it starts when its plugin registers the type again
            logger.info(f"  '{session.name}': {self._title(type_id)} isn't available now; "
                        f"'{record.get('name')}' starts when it is")
            if raise_errors:
                raise LookupError(f"No output type '{type_id}'")
            return None
        if self._one_local(type_id):
            busy = self._playing_local(other_than=session.id)
            if busy:
                if not take_over:
                    message = self._busy_message(busy[0])
                    if raise_errors:
                        raise OutputBusy(message, take_over=True)
                    return self._fail(key, message, False)
                await self._take_over(session)
            mine = [a for a in self._live.values() if a.session_id == session.id and self._local(a.type_id)
                    and a.playing and a.speaker_id != sid]
            if mine:  # choose-one: the channel already plays a local device
                return self._fail(key, f"'{session.name}' already plays on {self._name(mine[0].speaker_id)}",
                                  raise_errors)
            if not await self._closed_local(other_than=session.id):
                return self._fail(key, "The other local device didn't close in time", raise_errors)
        return self._open(session, sid, raise_errors)

    def _fail(self, key: tuple, message: str, raise_errors: bool) -> None:
        self._failed[key] = message
        self._update_row(key[1])
        if raise_errors:
            raise OutputError(message)
        return None

    def _open(self, session, sid: str, raise_errors: bool) -> Optional[Attachment]:
        key = (session.id, sid)
        record = self._devices()[sid]
        type_id, device_id = record["type"], record["device"]
        output_type = self._type(type_id)
        entry = self._entry(session.id, sid)
        options = dict(entry.get("options") or {})
        channel = self.sessions.channel_of(session.id) if self.sessions is not None else None
        try:
            if channel is None:
                raise OutputError("The channel isn't playing")
            self._available(type_id)
            try:
                sink = output_type.open(device_id, self.rate, options)
            except Exception as e:
                _raise_plugin_failure(type_id, output_type, "open()", e)
            if sink is None:
                raise OutputError(f"{self._title(type_id)} gave no sink")
        except Exception as e:
            message = _message(e)
            logger.warning(f"Audio output {type_id}/{device_id}: '{session.name}' not started: {message}")
            self._failed[key] = message
            self._update_row(sid)
            if raise_errors:
                raise
            return None
        self._failed.pop(key, None)
        attachment = Attachment(entry["id"], session.id, type_id, device_id, options, speaker_id=sid,
                                level=self._level(session, sid), on_end=self._ended,
                                stereo=bool(getattr(output_type, "stereo", False)))
        old = self._live.get(key)
        if old is not None:
            old.stop()
        self._live[key] = attachment
        attachment.start(channel, sink)
        self._update_row(sid)
        logger.info(f"  '{session.name}': playing on {type_id}/{device_id}")
        return attachment

    async def stop(self, session, speaker_ids: Optional[list] = None) -> None:
        """The channel stopped or paused (after its fade), or these speakers left it: their sinks close."""
        for key, attachment in list(self._live.items()):
            if key[0] == session.id and (speaker_ids is None or key[1] in speaker_ids):
                attachment.stop()
                self._update_row(key[1])
        if speaker_ids is not None:  # they left the channel: their failures go too (a stop keeps them shown)
            for key in [k for k in self._failed if k[0] == session.id and k[1] in speaker_ids]:
                self._failed.pop(key, None)
                self._update_row(key[1])

    def stop_all(self) -> None:
        for attachment in list(self._live.values()) + list(self._tests.values()):
            attachment.stop()
        self._live.clear()

    # --- Test (Settings > Speakers) ---

    async def test(self, speaker_id: str) -> dict:
        """
        Play the test sound (the chime at TEST_VOLUME) through the device:
        open(device, rate, {}), real-time blocks from a writer thread, close().
        OutputBusy: it plays a channel (or, being local, another local device does).
        """
        from sonorium.core.speaker_test_tone import DURATION, tone_samples
        record = self._devices().get(speaker_id)
        if record is None:
            raise LookupError("Speaker not found")
        type_id, device_id = record["type"], record["device"]
        if type_id not in self._types:
            raise OutputError(f"{self._title(type_id)} isn't available now")
        for attachment in self._live.values():
            if attachment.speaker_id == speaker_id and attachment.playing:
                raise OutputBusy(f"This speaker is playing '{self._session_name(attachment)}'. Stop it first.")
        test = self._tests.get(speaker_id)
        if test is not None and test.running:
            raise OutputBusy("A test sound is already playing on this speaker")
        if self._one_local(type_id):
            busy = self._playing_local()
            if busy:
                raise OutputBusy(f"{self._busy_message(busy[0])}. Stop it first.")
            testing = [t for t in self._tests.values() if self._local(t.type_id) and t.running]
            if testing:
                raise OutputBusy(f"A test sound is already playing on {self._name(testing[0].speaker_id)}")
            await self._closed_local()
        self._available(type_id)
        output_type = self._type(type_id)
        try:
            sink = output_type.open(device_id, self.rate, {})
        except Exception as e:
            _raise_plugin_failure(type_id, output_type, "open()", e)
        samples = _scaled(tone_samples(rate=self.rate), TEST_VOLUME)
        attachment = Attachment("test", "", type_id, device_id, {}, speaker_id=speaker_id,
                                stereo=bool(getattr(output_type, "stereo", False)))
        attachment.sink = sink
        attachment._thread = threading.Thread(target=self._play_test, args=(attachment, samples),
                                              name=f"output-test-{type_id}", daemon=True)
        self._tests[speaker_id] = attachment
        attachment._thread.start()
        return {"speaker_id": speaker_id, "playing": True, "seconds": DURATION}

    def _session_name(self, attachment: Attachment) -> str:
        session = self.sessions.get(attachment.session_id) if self.sessions is not None else None
        return session.name if session is not None else attachment.session_id

    def _play_test(self, attachment: Attachment, samples: np.ndarray) -> None:
        sink = attachment.sink
        started = time.monotonic()
        try:
            for index, start in enumerate(range(0, len(samples), TEST_BLOCK)):
                if attachment._stop.is_set():
                    break
                wait = started + start / self.rate - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                sink.write(to_channels(samples[start:start + TEST_BLOCK], attachment.channels))
        except Exception as e:
            logger.warning(f"Speaker test on {attachment.type_id}/{attachment.device_id} stopped: "
                           f"{str(e) or type(e).__name__}")
        finally:
            try:
                sink.close()
            except Exception:
                pass

    def latency(self, session_id: str, speaker_id: str) -> Optional[float]:
        """Seconds from write() to sound on the device playing this channel now (its sink's own), else None."""
        live = self._live.get((session_id, speaker_id))
        sink = getattr(live, "sink", None) if live is not None else None
        value = getattr(sink, "latency", None) if sink is not None else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return float(value)
        return None

    # --- The channel's outputs (self.audio_outputs, /api/sessions/{id}/audio-outputs) ---

    def attachments(self, session_id: Optional[str] = None) -> list[dict]:
        """The device speakers of one channel (or of all), as outputs."""
        result = []
        if self.sessions is None:
            return result
        sessions = [self.sessions.get(session_id)] if session_id is not None else self._sessions()
        for session in sessions:
            if session is None:
                continue
            for sid in self._channel_devices(session):
                record = self._devices().get(sid)
                if record is None:
                    continue
                entry = self._entry(session.id, sid)
                live = self._live.get((session.id, sid))
                item = live.to_dict() if live is not None else {
                    "attachment_id": entry["id"], "channel_id": session.id, "type": record["type"],
                    "device": record["device"], "options": dict(entry.get("options") or {}), "running": False,
                    "latency": 0.0, "error": ""}
                item["attachment_id"] = entry["id"]
                item["error"] = self._failed.get((session.id, sid)) or item["error"]
                item["speaker_id"] = sid
                result.append(item)
        return result

    async def attach(self, session_id: str, type_id: str, device_id: str, options: Optional[dict] = None,
                     take_over: bool = False) -> dict:
        """
        Add the device to the channel's speakers; it plays at once if the
        channel is playing. LookupError: no such channel or type;
        OutputError: it can't be used; OutputBusy: it is local and another
        channel plays a local device (unless take_over).
        """
        session = self.sessions.get(session_id) if self.sessions is not None else None
        if session is None:
            raise LookupError(f"No channel '{session_id}'")
        self._type(type_id)
        self._available(type_id)
        if not isinstance(device_id, str) or not device_id:
            raise OutputError("'device' must be a device id")
        if options is not None and not isinstance(options, dict):
            raise OutputError("'options' must be an object")
        sid = speaker_id_for(type_id, device_id)
        if sid not in self._devices():
            try:
                await self.discover(type_id)
            except Exception:
                pass
        settings = self._settings()
        if sid not in self._devices() or (settings is not None and not settings.speaker_enabled(sid)):
            raise OutputError(SWITCHED_OFF)
        devices = self._channel_devices(session)
        if sid in devices:
            if session.is_playing:
                await self._start_one(session, sid, take_over, raise_errors=True)
            return self._output(session, sid)
        if len(devices) >= MAX_ATTACHMENTS_PER_CHANNEL:
            raise OutputError(f"A channel can have at most {MAX_ATTACHMENTS_PER_CHANNEL} devices")
        entry = self._entry(session.id, sid, options)
        entry["options"] = dict(options or {})
        self._save()
        if session.is_playing:
            await self._start_one(session, sid, take_over, raise_errors=True)
        self._add_to_channel(session, sid)
        logger.info(f"  '{session.name}': {type_id}/{device_id} added to its speakers")
        return self._output(session, sid)

    def _output(self, session, sid: str) -> dict:
        for item in self.attachments(session.id):
            if item["speaker_id"] == sid:
                return item
        live = self._live.get((session.id, sid))
        return live.to_dict() if live is not None else {}

    async def detach(self, session_id: str, attachment_id: str) -> bool:
        """Take the device out of the channel's speakers and close its sink. False: the channel doesn't have it."""
        session = self.sessions.get(session_id) if self.sessions is not None else None
        if session is None:
            return False
        entry = next((e for e in self._channel_entries().get(session_id) or [] if e.get("id") == attachment_id), None)
        if entry is None or entry.get("speaker") not in self._channel_devices(session):
            return False
        sid = entry["speaker"]
        await self.stop(session, [sid])
        self._take_out(session, sid)
        self._live.pop((session_id, sid), None)
        kept = [e for e in self._channel_entries().get(session_id) or [] if e.get("id") != attachment_id]
        if kept:
            self._channel_entries()[session_id] = kept
        else:
            self._channel_entries().pop(session_id, None)
        self._save()
        self._update_row(sid)
        return True
