"""
Audio outputs: other places a channel's stream can go besides speakers that
pull it over HTTP, such as the sound card of the machine Sonorium runs on.

A plugin registers an output type; Sonorium asks it for its devices and
opens a sink on one when a channel is sent there:

    class HostAudio(OutputType):
        id = "host_audio"
        title = "This computer's audio"
        def available(self):            return (True, "") or (False, "why not")
        async def discover(self):       return [{"id": "hw:0", "name": "Built-in speakers"}]
        def open(self, device_id, sample_rate, options):  return MySink(...)

    class MySink(Sink):
        def write(self, block):         # int16 numpy array, shape (1, n), mono, sample_rate
            ...                         # may block: it runs in the attachment's own thread
        def close(self): ...

Sonorium ships no output type of its own except CaptureOutput below (a test
double that keeps what it was sent). A direct "host audio" output is the
first example in docs/PLUGIN_DEVELOPMENT.md: it needs an audio library
(e.g. sounddevice) that isn't part of every install.

An attachment is a channel sent to one device. Its thread reads the
channel's broadcast like a speaker does: it counts as a listener (the channel
isn't released as idle), it hears the speakers-muted switch, and a slow sink
only falls behind itself. Attachments are remembered per channel and made
again when the channel starts; sending a channel to an output also starts
its mix if it isn't running (like Listen here).
"""
from __future__ import annotations

import asyncio
import re
import secrets
import threading
from typing import Callable, Optional

import numpy as np

from sonorium.obs import logger

TYPE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
DISCOVER_TIMEOUT = 10.0
MAX_ATTACHMENTS_PER_CHANNEL = 8


class OutputError(ValueError):
    """An output that can't be used (the message is shown to the caller)."""


class Sink:
    """Where an attachment writes the channel's audio."""
    latency: float = 0.0  # seconds the device adds (shown to the user)

    def write(self, block: np.ndarray) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class OutputType:
    """A kind of output a plugin provides."""
    id: str = ""
    title: str = ""

    def available(self) -> tuple[bool, str]:
        """(usable here, why not): e.g. no sound card in this container."""
        return True, ""

    async def discover(self) -> list[dict]:
        """The devices: [{"id", "name", ...}] (extra fields are passed on to the UI)."""
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


class Attachment:
    """A channel sent to one device: a thread copying its broadcast to the sink."""

    def __init__(self, attachment_id: str, session_id: str, type_id: str, device_id: str, options: dict):
        self.id = attachment_id
        self.session_id = session_id
        self.type_id = type_id
        self.device_id = device_id
        self.options = options
        self.sink: Optional[Sink] = None
        self.channel = None
        self.error = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, channel, sink: Sink) -> None:
        self.channel, self.sink = channel, sink
        self._thread = threading.Thread(target=self._run, name=f"output-{self.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        from sonorium.core.channel import ChannelState
        channel, sink = self.channel, self.sink
        channel.client_connected()
        last = channel.get_current_sequence()
        gain = 0.0 if channel.speakers_muted else 1.0
        try:
            while not self._stop.is_set() and (channel.state == ChannelState.PLAYING or channel._generator_running):
                fresh = channel.get_chunks_since(last)
                if not fresh:
                    channel.wait_for_data(timeout=0.05)
                    continue
                channel.mark_listener_active()
                for last, block in fresh:
                    target = 0.0 if channel.speakers_muted else 1.0
                    if target != gain or gain == 0.0:
                        ramp = np.linspace(gain, target, block.shape[-1], dtype=np.float32)
                        block = (block.astype(np.float32) * ramp).astype(block.dtype)
                        gain = target
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

    def to_dict(self) -> dict:
        return {"attachment_id": self.id, "channel_id": self.session_id, "type": self.type_id,
                "device": self.device_id, "options": self.options, "running": self.running,
                "latency": getattr(self.sink, "latency", 0.0) if self.sink else 0.0, "error": self.error}


class AudioOutputs:
    """The output types plugins registered, and which channels are sent where."""

    def __init__(self, session_manager, store=None, rate: int = 44100):
        self.sessions = session_manager
        self.store = store
        self.rate = rate
        self._types: dict[str, tuple[str, OutputType]] = {}     # type id -> (owner, type)
        self._live: dict[str, Attachment] = {}                  # attachment id -> Attachment
        if session_manager is not None:
            session_manager.add_mix_hold(self._holds)

    def _holds(self, session_id: str) -> bool:
        return any(a.session_id == session_id and a.running and not a._stop.is_set() for a in self._live.values())

    # --- Types ---

    def register_type(self, owner: str, output_type: OutputType) -> None:
        type_id = getattr(output_type, "id", "")
        if not isinstance(type_id, str) or not TYPE_ID.match(type_id):
            raise OutputError("An output type needs an id: letters, digits, '_', '-', '.'")
        if type_id in self._types and self._types[type_id][0] != owner:
            raise OutputError(f"The output type '{type_id}' is already registered by {self._types[type_id][0]}")
        self._types[type_id] = (owner, output_type)
        self._reattach(type_id=type_id)

    def unregister_owner(self, owner: str) -> None:
        for type_id in [t for t, (o, _) in self._types.items() if o == owner]:
            for attachment in [a for a in self._live.values() if a.type_id == type_id]:
                self._detach(attachment, forget=False)
            self._types.pop(type_id, None)

    def types(self) -> list[dict]:
        result = []
        for type_id, (owner, output_type) in sorted(self._types.items()):
            try:
                ok, why = output_type.available()
            except Exception as e:
                ok, why = False, str(e)
            result.append({"id": type_id, "title": output_type.title or type_id, "plugin_id": owner,
                           "available": bool(ok), "message": why or ""})
        return result

    def _type(self, type_id: str) -> OutputType:
        entry = self._types.get(type_id)
        if entry is None:
            raise LookupError(f"No output type '{type_id}'")
        return entry[1]

    async def discover(self, type_id: str) -> list[dict]:
        output_type = self._type(type_id)
        try:
            devices = await asyncio.wait_for(output_type.discover(), DISCOVER_TIMEOUT)
        except asyncio.TimeoutError:
            raise OutputError("The output didn't list its devices in time")
        return [d for d in (devices or []) if isinstance(d, dict) and isinstance(d.get("id"), str)]

    # --- Attachments ---

    def _remembered(self) -> dict:
        return self.store.main.section("audio_outputs") if self.store is not None else {}

    def _save(self) -> None:
        if self.store is not None:
            self.store.main.save()

    def attachments(self, session_id: Optional[str] = None) -> list[dict]:
        live = [a.to_dict() for a in self._live.values() if session_id in (None, a.session_id)]
        known = {(a["type"], a["device"]) for a in live}
        for sid, saved in self._remembered().items():
            if session_id not in (None, sid):
                continue
            for item in saved:
                if (item["type"], item["device"]) not in known:
                    live.append({"attachment_id": item["id"], "channel_id": sid, "type": item["type"],
                                 "device": item["device"], "options": item.get("options") or {}, "running": False,
                                 "latency": 0.0, "error": ""})
        return live

    def attach(self, session_id: str, type_id: str, device_id: str, options: Optional[dict] = None,
               remember: bool = True) -> dict:
        """
        Send a channel to a device (its mix starts if it isn't running).
        LookupError: no such channel or type; OutputError: it can't be used.
        """
        session = self.sessions.get(session_id) if self.sessions is not None else None
        if session is None:
            raise LookupError(f"No channel '{session_id}'")
        output_type = self._type(type_id)
        ok, why = output_type.available()
        if not ok:
            raise OutputError(why or "This output can't be used here")
        if not isinstance(device_id, str) or not device_id:
            raise OutputError("'device' must be a device id")
        options = dict(options or {})
        saved = self._remembered().setdefault(session_id, [])
        entry = next((s for s in saved if s["type"] == type_id and s["device"] == device_id), None)
        if entry is None:
            if len(saved) >= MAX_ATTACHMENTS_PER_CHANNEL:
                raise OutputError(f"A channel can be sent to at most {MAX_ATTACHMENTS_PER_CHANNEL} outputs")
            entry = {"id": "a_" + secrets.token_hex(5), "type": type_id, "device": device_id, "options": options}
            if remember:
                saved.append(entry)
                self._save()
        attachment = self._live.get(entry["id"])
        if attachment is not None and attachment.running:
            return attachment.to_dict()
        attachment = Attachment(entry["id"], session_id, type_id, device_id, options)
        self._live[attachment.id] = attachment  # holds the mix from here
        try:
            channel = self._running_channel(session_id)
            attachment.start(channel, output_type.open(device_id, self.rate, options))
        except Exception:
            self._live.pop(attachment.id, None)
            raise
        logger.info(f"  '{session.name}': sent to {type_id}/{device_id}")
        return attachment.to_dict()

    def _running_channel(self, session_id: str):
        from sonorium.core.channel import ChannelState
        channel = self.sessions.channel_of(session_id)
        if channel is None or channel.state != ChannelState.PLAYING:
            try:
                channel = self.sessions.run_mix(session_id)
            except ValueError as e:
                raise OutputError(str(e))
        if channel is None:
            raise OutputError("No free channel")
        return channel

    def _detach(self, attachment: Attachment, forget: bool) -> None:
        attachment.stop()
        self._live.pop(attachment.id, None)
        if forget:
            saved = self._remembered().get(attachment.session_id) or []
            kept = [s for s in saved if s["id"] != attachment.id]
            if kept:
                self._remembered()[attachment.session_id] = kept
            else:
                self._remembered().pop(attachment.session_id, None)
            self._save()

    async def detach(self, session_id: str, attachment_id: str) -> bool:
        """Stop sending the channel there and forget it. The mix stops if nothing else keeps it."""
        attachment = self._live.get(attachment_id)
        if attachment is None or attachment.session_id != session_id:
            saved = self._remembered().get(session_id) or []
            if not any(s["id"] == attachment_id for s in saved):
                return False
            attachment = Attachment(attachment_id, session_id, "", "", {})
        self._detach(attachment, forget=True)
        if self.sessions is not None:
            await self.sessions.release_unused(session_id, "no output plays it")
        return True

    def channel_started(self, session_id: str) -> None:
        """The channel started: send it again where it was sent before."""
        self._reattach(session_id=session_id)

    def _reattach(self, session_id: Optional[str] = None, type_id: Optional[str] = None) -> None:
        from sonorium.core.channel import ChannelState
        for sid, saved in list(self._remembered().items()):
            if session_id not in (None, sid) or self.sessions is None:
                continue
            channel = self.sessions.channel_of(sid)
            if channel is None or channel.state != ChannelState.PLAYING:
                continue
            for item in list(saved):
                if type_id not in (None, item["type"]) or item["type"] not in self._types:
                    continue
                live = self._live.get(item["id"])
                if live is not None and live.running:
                    continue
                try:
                    self.attach(sid, item["type"], item["device"], item.get("options"))
                except Exception as e:
                    logger.warning(f"Audio output {item['type']}/{item['device']}: not started again: {e}")

    def stop_all(self) -> None:
        for attachment in list(self._live.values()):
            attachment.stop()
        self._live.clear()
