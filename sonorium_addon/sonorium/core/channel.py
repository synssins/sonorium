"""
Sonorium Channel System

Channels are persistent audio streams that speakers connect to.
Each channel can play one theme at a time, with smooth crossfading
when switching between themes.

This uses a broadcast model - ONE audio source, multiple listeners.
Like a radio station: all speakers hear the same stream at the same time.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING, Generator

import numpy as np

from sonorium.obs import logger
from sonorium.mp3_out import Mp3Encoder, RealtimePacer
from sonorium.recording import SAMPLE_RATE, CROSSFADE_SAMPLES

if TYPE_CHECKING:
    from sonorium.theme import ThemeDefinition, ThemeStream


# Default length of a theme-to-theme crossfade and of a channel's start/stop
# fade (seconds). The session manager passes Settings > Audio Settings >
# Crossfade (crossfade_duration) instead; this is only the fallback.
THEME_CROSSFADE_DURATION = 3.0
THEME_CROSSFADE_SAMPLES = int(THEME_CROSSFADE_DURATION * SAMPLE_RATE)

# Track settings that glide when a channel's preset changes (the others,
# e.g. mute, switch at once; a track turning on or off fades by itself)
GLIDE_FIELDS = ("volume", "presence", "volume_low")

# Chunk size for silence generation
CHUNK_SIZE = 1024

# Default output gain
DEFAULT_OUTPUT_GAIN = 6.0

# Buffer size for broadcast (number of chunks to keep for late-joining clients)
# At 1024 samples per chunk @ 44100Hz, each chunk is ~23ms
# 50 chunks = ~1.16 seconds of buffer
BROADCAST_BUFFER_SIZE = 50


def crossfade_curves(samples: int) -> tuple[np.ndarray, np.ndarray]:
    """Equal-power crossfade curves (old fades out, new fades in) over `samples`."""
    x = np.linspace(0, np.pi / 2, samples)
    return np.cos(x).astype(np.float32), np.sin(x).astype(np.float32)


def fade_gain(level):
    """The gain at a point (0..1) of a channel's start/stop fade: equal-power, like the theme crossfade."""
    return np.sin(np.asarray(level, dtype=np.float32) * (np.pi / 2))


class ChannelState(str, Enum):
    """Current state of a channel."""
    IDLE = "idle"              # No theme assigned, outputting silence
    PLAYING = "playing"        # Playing a theme


@dataclass
class Channel:
    """
    A persistent audio stream channel using broadcast model.

    ONE audio generator runs continuously (when playing).
    ALL connected clients read from the same stream.
    New clients join at current playback position.

    Every chunk passes through, in order: a theme crossfade (when the theme
    changes), the preset glide (volume and presence moving to a new preset's
    values), and the channel's output fade (fading the whole mix in when it
    starts and out before it stops). The channel's volume setting (the
    speakers' volume) is never touched by any of these.
    """

    id: int
    name: str = ""

    # Current theme reference
    _current_theme: Optional[ThemeDefinition] = field(default=None, repr=False)

    # Theme version - increments when theme changes
    _theme_version: int = 0

    # State tracking
    state: ChannelState = ChannelState.IDLE

    # Active client count (for resource management)
    _client_count: int = 0

    # When a listener last pulled audio (or playback started), for idle detection.
    # Connection count alone isn't enough: a Cast device stopped from HA can keep
    # its connection open without reading.
    _last_listener_time: float = field(default_factory=time.monotonic)

    # Browsers listening here ("Listen here" on the channel's card, /stream/channel{n}?local=1),
    # and when that last changed or was asked for
    _local_count: int = 0
    _local_time: float = field(default_factory=time.monotonic)

    # The speakers' stream is silenced (every client but the browsers listening here)
    speakers_muted: bool = False

    # Lock for thread-safe operations
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    # Condition for notifying clients of new data
    _data_available: threading.Condition = field(default_factory=threading.Condition, repr=False)

    # Shared audio state
    _theme_stream: Optional[ThemeStream] = field(default=None, repr=False)
    _chunk_generator: Optional[Generator] = field(default=None, repr=False)

    # Pending theme change (for thread-safe crossfade), and its length in seconds
    _pending_theme: Optional[ThemeDefinition] = field(default=None, repr=False)
    _pending_crossfade: float = THEME_CROSSFADE_DURATION

    # This channel's preset layer for the theme it plays ({track: {field: value}},
    # see recording.TrackView), and the one queued with a pending theme
    _track_overrides: dict = field(default_factory=dict, repr=False)
    _pending_overrides: dict = field(default_factory=dict, repr=False)

    # Advanced transitions planned while a theme crossfade was queued:
    # (monotonic time, plans), handed to the new theme's mix when it starts
    _pending_transitions: Optional[tuple] = field(default=None, repr=False)

    # A theme crossfade in progress: the old theme's chunks, how far it is, its curves
    _xfade_old: Optional[Generator] = field(default=None, repr=False)
    _xfade_pos: int = 0
    _xfade_out: Optional[np.ndarray] = field(default=None, repr=False)
    _xfade_in: Optional[np.ndarray] = field(default=None, repr=False)

    # A preset glide in progress: {"start": {(key, field): v}, "end": {...},
    # "pos": samples, "length": samples, "final": overrides when done}
    _glide: Optional[dict] = field(default=None, repr=False)

    # Output fade (start/stop): position along the fade (0 silent .. 1 full),
    # where it is going, and how much it moves per sample
    _out_level: float = 1.0
    _out_target: float = 1.0
    _out_rate: float = 0.0

    # Broadcast buffer - recent chunks for all clients
    _broadcast_buffer: deque = field(default_factory=lambda: deque(maxlen=BROADCAST_BUFFER_SIZE), repr=False)
    _chunk_sequence: int = 0  # Incrementing ID for each chunk

    # Background generator thread
    _generator_thread: Optional[threading.Thread] = field(default=None, repr=False)
    _generator_running: bool = False

    # Silence chunk
    _silence: np.ndarray = field(default=None, repr=False)

    def __post_init__(self):
        if not self.name:
            self.name = f"Channel {self.id}"
        self._silence = np.zeros((1, CHUNK_SIZE), dtype=np.int16)

    @property
    def current_theme(self) -> Optional[ThemeDefinition]:
        """Get the current theme."""
        return self._current_theme

    @property
    def current_theme_id(self) -> Optional[str]:
        """Get the current theme ID."""
        return self._current_theme.id if self._current_theme else None

    @property
    def current_theme_name(self) -> Optional[str]:
        """Get the current theme name."""
        return self._current_theme.name if self._current_theme else None

    @property
    def theme_version(self) -> int:
        """Get current theme version (for change detection)."""
        return self._theme_version

    @property
    def is_active(self) -> bool:
        """Check if channel has connected clients."""
        return self._client_count > 0

    @property
    def output_level(self) -> float:
        """Where the start/stop fade is (0 silent .. 1 full level)."""
        return self._out_level

    @property
    def is_faded_out(self) -> bool:
        """The channel has faded out (or is stopped) and outputs silence."""
        return self.state != ChannelState.PLAYING or (self._out_target <= 0.0 and self._out_level <= 0.0)

    @property
    def is_gliding(self) -> bool:
        """A preset glide is in progress."""
        return self._glide is not None

    def idle_seconds(self) -> float:
        """Seconds since any listener pulled audio from this playing channel."""
        if self.state != ChannelState.PLAYING:
            return 0.0
        return time.monotonic() - self._last_listener_time

    def mark_listener_active(self) -> None:
        """Record that a listener just pulled audio."""
        self._last_listener_time = time.monotonic()

    def local_idle_seconds(self) -> float:
        """Seconds without a browser listening here (0 while one does)."""
        return 0.0 if self._local_count > 0 else time.monotonic() - self._local_time

    def expect_local_listener(self) -> None:
        """A browser is about to connect: count its wait from now."""
        self._local_time = time.monotonic()

    @property
    def stream_path(self) -> str:
        """Get the stream URL path for this channel."""
        return f"/stream/channel{self.id}"

    # --- Preset layer and glide ---

    def _replace_overrides(self, overrides: dict) -> None:
        """Swap the preset layer's contents in place (the playing mix holds this dict)."""
        self._track_overrides.clear()
        self._track_overrides.update(overrides)

    def _base_value(self, key: str, name: str):
        """A track's (or group master's) value when no preset sets it: the theme's own."""
        from sonorium.recording import GROUP_KEY_PREFIX
        theme = self._current_theme
        if theme is None:
            return None
        if key.startswith(GROUP_KEY_PREFIX):
            saved = (getattr(theme, "groups", None) or {}).get(key[len(GROUP_KEY_PREFIX):]) or {}
            value = saved.get(name)
            return 1.0 if value is None else value
        for instance in list(getattr(theme, "instances", None) or []):
            if instance.name == key:
                return getattr(instance, name, None)
        return None

    def set_track_overrides(self, overrides: dict | None, glide_seconds: float = 0.0,
                            fade_seconds: float | None = None) -> None:
        """
        Change this channel's preset layer for the theme it's playing, in place,
        so the playing mix follows. Other channels are not affected.

        With `glide_seconds`, volume and presence (of tracks and group masters)
        move from where they are now to the new values over that long, counted
        in audio played; other settings change at once. A track turning on or
        off fades by itself, over `fade_seconds` (a sequence step passes its
        ramp), else theme.TRACK_TOGGLE_FADE_SECONDS. A new glide starts from
        wherever a running one has got to.
        """
        new = {key: dict(values) for key, values in (overrides or {}).items()}
        with self._lock:
            if self._pending_theme is not None:
                # The change is for the theme that is about to fade in
                self._pending_overrides = new
                return
            stream = self._theme_stream if fade_seconds is not None else None
            before = stream.track_states() if stream is not None else None
            self._change_overrides(new, glide_seconds)
            if before is not None:
                stream.fade_toggles(before, fade_seconds)

    def _change_overrides(self, new: dict, glide_seconds: float) -> None:
        """set_track_overrides' change itself (call with the lock held)."""
        if glide_seconds <= 0 or self._chunk_generator is None:
            self._glide = None
            self._replace_overrides(new)
            return

        start, end = {}, {}
        for key in set(self._track_overrides) | set(new):
            old_values = self._track_overrides.get(key) or {}
            new_values = new.get(key) or {}
            for name in GLIDE_FIELDS:
                if name not in old_values and name not in new_values:
                    continue
                base = self._base_value(key, name)
                a = old_values[name] if name in old_values else base
                b = new_values[name] if name in new_values else base
                if a is None or b is None:
                    continue
                a, b = float(a), float(b)
                if a != b:
                    start[(key, name)], end[(key, name)] = a, b

        if not start:
            self._glide = None
            self._replace_overrides(new)
            return
        # Everything else switches now; the gliding values start where they are
        working = {key: dict(values) for key, values in new.items()}
        for (key, name), value in start.items():
            working.setdefault(key, {})[name] = value
        self._replace_overrides(working)
        self._glide = {
            "start": start,
            "end": end,
            "pos": 0,
            "length": max(1, int(glide_seconds * SAMPLE_RATE)),
            "final": new,
        }
        logger.debug(f"Channel {self.id}: Gliding {len(start)} value(s) over {glide_seconds:g}s")

    def _advance_glide(self, samples: int) -> None:
        """Move a running glide on by `samples` of audio (call with the lock held)."""
        glide = self._glide
        if glide is None:
            return
        glide["pos"] += samples
        fraction = glide["pos"] / glide["length"]
        if fraction >= 1.0:
            self._replace_overrides(glide["final"])
            self._glide = None
            return
        for (key, name), a in glide["start"].items():
            b = glide["end"][(key, name)]
            self._track_overrides.setdefault(key, {})[name] = a + (b - a) * fraction

    # --- Advanced transitions (core/transitions.py) ---

    def _label_stream(self, stream, theme) -> None:
        """Name the mix for its "Playback:" log lines: this channel and the theme."""
        if stream is not None and hasattr(stream, "label"):
            stream.label = f"{self.name}: {theme.name}"

    def schedule_transitions(self, plans: list) -> None:
        """Plan tracks' advanced transitions on the playing mix (see theme.ThemeStream.schedule_transitions)."""
        with self._lock:
            if self._pending_theme is not None:
                self._pending_transitions = (time.monotonic(), [dict(p) for p in plans])
                return
            stream = self._theme_stream
        if stream is not None and hasattr(stream, "schedule_transitions"):
            stream.schedule_transitions(plans)

    def cancel_transitions(self) -> None:
        """Drop planned transitions; running ones glide to what the channel plays now."""
        with self._lock:
            self._pending_transitions = None
            stream = self._theme_stream
        if stream is not None and hasattr(stream, "cancel_transitions"):
            stream.cancel_transitions()

    # --- Theme ---

    def set_theme(self, theme: ThemeDefinition, overrides: dict | None = None,
                  crossfade: float = THEME_CROSSFADE_DURATION, fade_in: float = 0.0) -> None:
        """
        Set or change the theme for this channel, with the channel's preset
        values for it (see recording.TrackView).

        If the channel is playing, the old theme crossfades into the new one
        over `crossfade` seconds (0 cuts). Otherwise playback starts, fading
        the whole mix in from silence over `fade_in` seconds.
        """
        with self._lock:
            if theme == self._current_theme and self._pending_theme is None:
                self._glide = None
                self._replace_overrides({key: dict(values) for key, values in (overrides or {}).items()})
                logger.info(f"Channel {self.id}: Theme '{theme.name}' already active, no change needed")
                return

            old_theme = self._current_theme.name if self._current_theme else "none"
            logger.debug(f"Channel {self.id}: Changing theme from '{old_theme}' to '{theme.name}'")

            self._theme_version += 1

            if self._generator_running:
                # Queue the theme change - generator thread will handle crossfade
                self._pending_theme = theme
                self._pending_overrides = {key: dict(values) for key, values in (overrides or {}).items()}
                self._pending_crossfade = max(0.0, float(crossfade or 0.0))
                logger.info(f"Channel {self.id}: Queued theme change for a {self._pending_crossfade:g}s crossfade")
            else:
                # No generator running, start fresh
                self._current_theme = theme
                self.state = ChannelState.PLAYING
                self._last_listener_time = time.monotonic()
                self._track_overrides = {key: dict(values) for key, values in (overrides or {}).items()}
                self._glide = None
                self._xfade_old = None
                self._theme_stream = theme.get_stream(self._track_overrides)
                self._label_stream(self._theme_stream, theme)
                self._chunk_generator = self._theme_stream.iter_chunks()
                if fade_in and fade_in > 0:
                    self._out_level, self._out_target = 0.0, 1.0
                    self._out_rate = 1.0 / (fade_in * SAMPLE_RATE)
                else:
                    self._out_level, self._out_target, self._out_rate = 1.0, 1.0, 0.0
                self._ensure_generator_running()

    def _begin_crossfade(self) -> None:
        """Start the queued theme change (call with the lock held)."""
        theme, overrides = self._pending_theme, self._pending_overrides
        seconds = self._pending_crossfade
        self._pending_theme = None
        self._pending_overrides = {}
        if theme is None:
            return

        # Create new stream, with the channel's preset layer for the new theme
        new_stream = theme.get_stream(overrides)
        self._label_stream(new_stream, theme)
        planned, self._pending_transitions = self._pending_transitions, None
        if planned is not None and hasattr(new_stream, "schedule_transitions"):
            elapsed = time.monotonic() - planned[0]
            new_stream.schedule_transitions([{**p, "delay": max(0.0, p.get("delay", 0.0) - elapsed)}
                                             for p in planned[1]])
        old_generator = self._chunk_generator
        samples = int(seconds * SAMPLE_RATE)
        if self._xfade_old is not None:
            self._xfade_old.close()  # a crossfade still running: its oldest theme goes
            self._xfade_old = None
        if old_generator is not None and samples > 0:
            logger.info(f"Channel {self.id}: Crossfading to '{theme.name}' over {seconds:g}s")
            self._xfade_old = old_generator
            self._xfade_pos = 0
            self._xfade_out, self._xfade_in = crossfade_curves(samples)
        else:
            logger.info(f"Channel {self.id}: Switching to '{theme.name}'")
            if old_generator is not None:
                old_generator.close()

        self._current_theme = theme
        self._theme_stream = new_stream
        self._track_overrides = overrides
        self._chunk_generator = new_stream.iter_chunks()
        self._glide = None

    def _crossfade_step(self, new_chunk: np.ndarray) -> np.ndarray:
        """Mix one chunk of a running theme crossfade."""
        try:
            old_chunk = next(self._xfade_old)
        except StopIteration:
            old_chunk = self._silence
        size = new_chunk.shape[1]
        pos, total = self._xfade_pos, len(self._xfade_out)
        take = max(0, min(size, total - pos))
        fade_out = np.concatenate([self._xfade_out[pos:pos + take], np.zeros(size - take, dtype=np.float32)])
        fade_in = np.concatenate([self._xfade_in[pos:pos + take], np.ones(size - take, dtype=np.float32)])
        mixed = old_chunk.astype(np.float32).reshape(-1)[:size] * fade_out + new_chunk.astype(np.float32).reshape(-1) * fade_in
        self._xfade_pos += size
        if self._xfade_pos >= total:
            self._xfade_old.close()
            self._xfade_old = None
            logger.info(f"Channel {self.id}: Crossfade complete")
        return np.clip(mixed, -32768, 32767).astype(np.int16).reshape(1, -1)

    # --- Start/stop fade ---

    def fade_in(self, seconds: float) -> None:
        """Fade the whole mix up to full level over `seconds` (from wherever it is)."""
        with self._lock:
            self._out_target = 1.0
            if seconds and seconds > 0:
                self._out_rate = 1.0 / (seconds * SAMPLE_RATE)
            else:
                self._out_level = 1.0

    def fade_out(self, seconds: float) -> None:
        """Fade the whole mix down to silence over `seconds`; the channel keeps running, silent."""
        with self._lock:
            self._out_target = 0.0
            if seconds and seconds > 0 and self.state == ChannelState.PLAYING:
                self._out_rate = 1.0 / (seconds * SAMPLE_RATE)
                logger.info(f"Channel {self.id}: Fading out over {seconds:g}s")
            else:
                self._out_level = 0.0

    def _apply_output_fade(self, chunk: np.ndarray) -> np.ndarray:
        level, target = self._out_level, self._out_target
        if level == target:
            if level >= 1.0:
                return chunk
            if level <= 0.0:
                return np.zeros_like(chunk)
            return (chunk.astype(np.float32) * fade_gain(level)).astype(np.int16)
        size = chunk.shape[-1]
        direction = 1.0 if target > level else -1.0
        levels = level + direction * self._out_rate * np.arange(1, size + 1, dtype=np.float64)
        levels = np.clip(levels, min(level, target), max(level, target))
        self._out_level = float(levels[-1])
        gains = fade_gain(levels)
        return np.clip(chunk.astype(np.float32) * gains, -32768, 32767).astype(np.int16)

    # --- Audio generation ---

    def next_chunk(self) -> np.ndarray:
        """
        The channel's next chunk of audio: the theme's mix, through a theme
        crossfade if one is running, with the preset glide moved on and the
        start/stop fade applied. Called by the generator thread (and tests).
        """
        with self._lock:
            if self._pending_theme is not None:
                self._begin_crossfade()
            self._advance_glide(CHUNK_SIZE)
            generator = self._chunk_generator
            if generator is None:
                chunk = self._silence
            else:
                try:
                    chunk = next(generator)
                except StopIteration:
                    chunk = self._silence
            if self._xfade_old is not None:
                chunk = self._crossfade_step(chunk)
            return self._apply_output_fade(chunk)

    def _add_to_buffer(self, chunk: np.ndarray):
        """Add a chunk to the broadcast buffer and notify waiting clients."""
        self._chunk_sequence += 1
        self._broadcast_buffer.append((self._chunk_sequence, chunk))

        # Notify all waiting clients that new data is available
        with self._data_available:
            self._data_available.notify_all()

    def _ensure_generator_running(self):
        """Start the generator thread if not running."""
        if self._generator_running:
            return

        self._generator_running = True
        self._generator_thread = threading.Thread(target=self._generator_loop, daemon=True)
        self._generator_thread.start()
        logger.debug(f"Channel {self.id}: Started generator thread")

    def _generator_loop(self):
        """Background thread that generates audio chunks, paced in real time."""
        logger.debug(f"Channel {self.id}: Generator loop started")

        pacer = RealtimePacer()
        try:
            while self._generator_running and self.state == ChannelState.PLAYING:
                chunk = self.next_chunk()
                self._add_to_buffer(chunk)  # every listener's stream picks it up from here
                pacer.advance(chunk.shape[1] / SAMPLE_RATE)
        except Exception as e:
            logger.error(f"Channel {self.id}: Generator error: {e}")
        finally:
            self._generator_running = False
            # Wake up any waiting clients so they can exit
            with self._data_available:
                self._data_available.notify_all()
            logger.debug(f"Channel {self.id}: Generator loop stopped")

    def stop(self) -> None:
        """Stop the channel and return to idle (at once: fade_out() first for a smooth stop)."""
        with self._lock:
            logger.info(f"Channel {self.id}: Stopping playback")
            self._generator_running = False
            self._current_theme = None
            self._theme_stream = None
            self._chunk_generator = None
            self._pending_theme = None
            self._xfade_old = None
            self._glide = None
            self._out_level, self._out_target, self._out_rate = 1.0, 1.0, 0.0
            self._theme_version += 1
            self.state = ChannelState.IDLE
            self.speakers_muted = False
            self._broadcast_buffer.clear()

            # Wake up any waiting clients
            with self._data_available:
                self._data_available.notify_all()

    def client_connected(self, local: bool = False) -> None:
        """Track a new client connection (local: a browser listening here)."""
        self._client_count += 1
        self._last_listener_time = time.monotonic()
        if local:
            self._local_count += 1
            self._local_time = self._last_listener_time
        logger.info(f"Channel {self.id}: Client connected ({self._client_count} total)")

    def client_disconnected(self, local: bool = False) -> None:
        """Track a client disconnection."""
        self._client_count = max(0, self._client_count - 1)
        self._last_listener_time = time.monotonic()
        if local:
            self._local_count = max(0, self._local_count - 1)
            self._local_time = self._last_listener_time
        logger.info(f"Channel {self.id}: Client disconnected ({self._client_count} remaining)")

    def get_current_sequence(self) -> int:
        """Get current chunk sequence number."""
        return self._chunk_sequence

    def get_chunks_since(self, since_sequence: int) -> list:
        """Get all chunks since a given sequence number."""
        return [(seq, chunk) for seq, chunk in self._broadcast_buffer if seq > since_sequence]

    def wait_for_data(self, timeout: float = 0.1) -> bool:
        """Wait for new data to be available. Returns True if data available, False on timeout."""
        with self._data_available:
            return self._data_available.wait(timeout)

    def get_stream(self, local: bool = False):
        """
        Get an MP3 stream iterator for this channel (local: for a browser listening here).

        All clients share the same audio source - they just
        encode from the broadcast buffer independently.
        """
        return ChannelStream(self, local)

    def to_dict(self) -> dict:
        """Serialize channel state for API."""
        return {
            "id": self.id,
            "name": self.name,
            "state": self.state.value,
            "current_theme": self.current_theme_id,
            "current_theme_name": self.current_theme_name,
            "client_count": self._client_count,
            "stream_path": self.stream_path,
        }


class ChannelStream:
    """
    MP3 streaming client for a Channel.

    Reads from the shared broadcast buffer and encodes to MP3.
    All clients hear the same audio at (approximately) the same time.
    """

    def __init__(self, channel: Channel, local: bool = False):
        self.channel = channel
        self.local = local
        self.channel.client_connected(local)

        # Start from current position
        self._last_sequence = channel.get_current_sequence()
        self._gain = 0.0 if self._muted() else 1.0

    def _muted(self) -> bool:
        return not self.local and self.channel.speakers_muted

    def _apply_mute(self, chunk):
        """Silence a speaker's stream when muted, ramping over one chunk so it doesn't click."""
        target = 0.0 if self._muted() else 1.0
        if target == self._gain == 1.0:
            return chunk
        if target == self._gain == 0.0:
            return np.zeros_like(chunk)
        ramp = np.linspace(self._gain, target, chunk.shape[-1], dtype=np.float32)
        self._gain = target
        return (chunk.astype(np.float32) * ramp).astype(chunk.dtype)

    def __iter__(self):
        """This listener's MP3 stream: every chunk the channel broadcasts from now on, as it is made."""
        encoder = Mp3Encoder()
        try:
            while self.channel.state == ChannelState.PLAYING or self.channel._generator_running:
                fresh = self.channel.get_chunks_since(self._last_sequence)
                if not fresh:
                    self.channel.wait_for_data(timeout=0.05)  # woken as soon as the next chunk is in
                    continue
                self.channel.mark_listener_active()
                for self._last_sequence, chunk in fresh:
                    yield from encoder.encode(self._apply_mute(chunk))
        finally:
            logger.debug(f'Channel {self.channel.id}: Client stream closed')
            self.channel.client_disconnected(self.local)
            encoder.close()


class ChannelManager:
    """
    Manages all channels for the Sonorium system.

    Provides channel creation, lookup, and lifecycle management.
    """

    def __init__(self, max_channels: int = 6):
        self.max_channels = max_channels
        self._channels: dict[int, Channel] = {}
        self._lock = threading.Lock()

        # Pre-create all channels
        for i in range(1, max_channels + 1):
            self._channels[i] = Channel(id=i)

        logger.debug(f"ChannelManager initialized with {max_channels} channels")

    def get_channel(self, channel_id: int) -> Optional[Channel]:
        """Get a channel by ID."""
        return self._channels.get(channel_id)

    def get_all_channels(self) -> list[Channel]:
        """Get all channels."""
        return list(self._channels.values())

    def get_active_channels(self) -> list[Channel]:
        """Get channels that are currently playing."""
        return [c for c in self._channels.values() if c.state == ChannelState.PLAYING]

    def list_channels(self) -> list[dict]:
        """Get all channels as serialized dicts for API."""
        return [c.to_dict() for c in self._channels.values()]

    def get_active_count(self) -> int:
        """Get number of currently playing channels."""
        return len(self.get_active_channels())

    def get_available_channel(self) -> Optional[Channel]:
        """Get first available (idle) channel, or None if all busy."""
        for channel in self._channels.values():
            if channel.state == ChannelState.IDLE:
                return channel
        return None
