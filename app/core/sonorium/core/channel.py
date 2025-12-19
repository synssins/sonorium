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
from sonorium.recording import SAMPLE_RATE

try:
    import av
except ImportError:
    from sonorium._av_compat import av

if TYPE_CHECKING:
    from sonorium.theme import ThemeDefinition, ThemeStream


# Crossfade duration for theme transitions (in seconds)
THEME_CROSSFADE_DURATION = 3.0
THEME_CROSSFADE_SAMPLES = int(THEME_CROSSFADE_DURATION * SAMPLE_RATE)

# Chunk size for silence generation
CHUNK_SIZE = 1024

# Default output gain multiplier for network streams
DEFAULT_OUTPUT_GAIN = 6.0

# Buffer size for broadcast (number of chunks to keep for late-joining clients)
# At 1024 samples per chunk @ 44100Hz, each chunk is ~23ms
# 50 chunks = ~1.16 seconds of buffer
BROADCAST_BUFFER_SIZE = 50


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
    """

    id: int
    name: str = ""

    # Output gain for this channel's streams
    output_gain: float = DEFAULT_OUTPUT_GAIN

    # Current theme reference
    _current_theme: Optional[ThemeDefinition] = field(default=None, repr=False)

    # Theme version - increments when theme changes
    _theme_version: int = 0

    # State tracking
    state: ChannelState = ChannelState.IDLE

    # Active client count (for resource management)
    _client_count: int = 0

    # Lock for thread-safe operations
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # Condition for notifying clients of new data
    _data_available: threading.Condition = field(default_factory=threading.Condition, repr=False)

    # Shared audio state
    _theme_stream: Optional[ThemeStream] = field(default=None, repr=False)
    _chunk_generator: Optional[Generator] = field(default=None, repr=False)

    # Pending theme change (for thread-safe crossfade)
    _pending_theme: Optional[ThemeDefinition] = field(default=None, repr=False)

    # Broadcast buffer - recent chunks for all clients
    _broadcast_buffer: deque = field(default_factory=lambda: deque(maxlen=BROADCAST_BUFFER_SIZE), repr=False)
    _chunk_sequence: int = 0  # Incrementing ID for each chunk

    # Background generator thread
    _generator_thread: Optional[threading.Thread] = field(default=None, repr=False)
    _generator_running: bool = False

    # Pre-generated crossfade curves
    _fade_out: np.ndarray = field(default=None, repr=False)
    _fade_in: np.ndarray = field(default=None, repr=False)

    # Silence chunk
    _silence: np.ndarray = field(default=None, repr=False)

    def __post_init__(self):
        if not self.name:
            self.name = f"Channel {self.id}"
        # Initialize crossfade curves
        self._fade_out = np.cos(np.linspace(0, np.pi/2, THEME_CROSSFADE_SAMPLES)).astype(np.float32)
        self._fade_in = np.sin(np.linspace(0, np.pi/2, THEME_CROSSFADE_SAMPLES)).astype(np.float32)
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
    def stream_path(self) -> str:
        """Get the stream URL path for this channel."""
        return f"/stream/channel{self.id}"

    def set_theme(self, theme: ThemeDefinition) -> None:
        """
        Set or change the theme for this channel.
        If generator is running, queues a crossfade; otherwise starts immediately.
        """
        with self._lock:
            if theme == self._current_theme:
                logger.info(f"Channel {self.id}: Theme '{theme.name}' already active, no change needed")
                return

            old_theme = self._current_theme.name if self._current_theme else "none"
            logger.info(f"Channel {self.id}: Changing theme from '{old_theme}' to '{theme.name}'")

            self._theme_version += 1

            if self._generator_running:
                # Queue the theme change - generator thread will handle crossfade
                self._pending_theme = theme
                logger.info(f"Channel {self.id}: Queued theme change for crossfade")
            else:
                # No generator running, start fresh
                self._current_theme = theme
                self.state = ChannelState.PLAYING
                self._theme_stream = theme.get_stream()
                self._chunk_generator = self._theme_stream.iter_chunks()
                self._ensure_generator_running()

    def _apply_crossfade(self, old_chunk: np.ndarray, new_chunk: np.ndarray, position: int) -> np.ndarray:
        """Apply crossfade mixing between two chunks."""
        chunk_size = old_chunk.shape[1]

        fade_start = position
        fade_end = min(fade_start + chunk_size, THEME_CROSSFADE_SAMPLES)
        fade_len = fade_end - fade_start

        if fade_len <= 0 or fade_start >= THEME_CROSSFADE_SAMPLES:
            return new_chunk

        old_f = old_chunk.astype(np.float32).flatten()
        new_f = new_chunk.astype(np.float32).flatten()

        if fade_len < chunk_size:
            fade_out = np.concatenate([
                self._fade_out[fade_start:fade_end],
                np.zeros(chunk_size - fade_len, dtype=np.float32)
            ])
            fade_in = np.concatenate([
                self._fade_in[fade_start:fade_end],
                np.ones(chunk_size - fade_len, dtype=np.float32)
            ])
        else:
            fade_out = self._fade_out[fade_start:fade_end]
            fade_in = self._fade_in[fade_start:fade_end]

        mixed = old_f[:len(fade_out)] * fade_out + new_f[:len(fade_in)] * fade_in
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
        return mixed.reshape(1, -1)

    def _apply_output_gain(self, chunk: np.ndarray) -> np.ndarray:
        """Apply output gain to a chunk."""
        if self.output_gain == 1.0:
            return chunk

        # Convert to float, apply gain, clip, convert back
        float_chunk = chunk.astype(np.float32) * self.output_gain
        float_chunk = np.clip(float_chunk, -32768, 32767)
        return float_chunk.astype(np.int16)

    def _add_to_buffer(self, chunk: np.ndarray):
        """Add a chunk to the broadcast buffer and notify waiting clients."""
        # Apply output gain before broadcasting
        chunk = self._apply_output_gain(chunk)

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
        logger.info(f"Channel {self.id}: Started generator thread")

    def _generator_loop(self):
        """Background thread that generates audio chunks."""
        logger.info(f"Channel {self.id}: Generator loop started")

        start_time = time.time()
        audio_time = 0.0

        try:
            while self._generator_running and self.state == ChannelState.PLAYING:
                # Check for pending theme change
                if self._pending_theme is not None:
                    self._do_crossfade_in_thread()

                # Get next chunk from current generator
                if self._chunk_generator is None:
                    chunk = self._silence
                else:
                    try:
                        chunk = next(self._chunk_generator)
                    except StopIteration:
                        chunk = self._silence

                # Add to broadcast buffer (this notifies clients)
                self._add_to_buffer(chunk)

                # Maintain real-time pacing
                chunk_duration = chunk.shape[1] / SAMPLE_RATE
                audio_time += chunk_duration

                now = time.time()
                ahead = audio_time - (now - start_time)
                if ahead > 0:
                    time.sleep(ahead)

        except Exception as e:
            logger.error(f"Channel {self.id}: Generator error: {e}")
        finally:
            self._generator_running = False
            # Wake up any waiting clients so they can exit
            with self._data_available:
                self._data_available.notify_all()
            logger.info(f"Channel {self.id}: Generator loop stopped")

    def _do_crossfade_in_thread(self):
        """Perform crossfade to pending theme (called from generator thread)."""
        theme = self._pending_theme
        self._pending_theme = None

        if theme is None:
            return

        logger.info(f"Channel {self.id}: Performing crossfade to '{theme.name}'")

        # Create new stream
        new_stream = theme.get_stream()
        new_generator = new_stream.iter_chunks()

        # Get references to old generator
        old_generator = self._chunk_generator

        # Do crossfade
        crossfade_position = 0
        while crossfade_position < THEME_CROSSFADE_SAMPLES:
            try:
                old_chunk = next(old_generator) if old_generator else self._silence
                new_chunk = next(new_generator)

                # Apply crossfade
                mixed = self._apply_crossfade(old_chunk, new_chunk, crossfade_position)
                crossfade_position += mixed.shape[1]

                # Add to broadcast buffer
                self._add_to_buffer(mixed)

            except StopIteration:
                break

        # Switch to new theme
        self._current_theme = theme
        self._theme_stream = new_stream
        self._chunk_generator = new_generator

        logger.info(f"Channel {self.id}: Crossfade complete")

    def stop(self) -> None:
        """Stop the channel and return to idle."""
        with self._lock:
            logger.info(f"Channel {self.id}: Stopping playback")
            self._generator_running = False
            self._current_theme = None
            self._theme_stream = None
            self._chunk_generator = None
            self._pending_theme = None
            self._theme_version += 1
            self.state = ChannelState.IDLE
            self._broadcast_buffer.clear()

            # Wake up any waiting clients
            with self._data_available:
                self._data_available.notify_all()

    def client_connected(self) -> None:
        """Track a new client connection."""
        self._client_count += 1
        logger.info(f"Channel {self.id}: Client connected ({self._client_count} total)")

    def client_disconnected(self) -> None:
        """Track a client disconnection."""
        self._client_count = max(0, self._client_count - 1)
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

    def get_stream(self):
        """
        Get an MP3 stream iterator for this channel.

        All clients share the same audio source - they just
        encode from the broadcast buffer independently.
        """
        return ChannelStream(self)

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
            "output_gain": self.output_gain,
        }


class ChannelStream:
    """
    MP3 streaming client for a Channel.

    Reads from the shared broadcast buffer and encodes to MP3.
    All clients hear the same audio at (approximately) the same time.
    """

    def __init__(self, channel: Channel):
        self.channel = channel
        self.channel.client_connected()

        # Start from current position
        self._last_sequence = channel.get_current_sequence()

    def __iter__(self):
        from io import BytesIO
        import numpy as np

        # Create in-memory MP3 encoder (stereo for AirPlay compatibility)
        buffer = BytesIO()
        output = av.open(buffer, mode="w", format='mp3')
        bitrate = 128_000
        out_stream = output.add_stream(codec_name='mp3', rate=SAMPLE_RATE)
        out_stream.bit_rate = bitrate
        # Set stereo layout (channels is read-only in newer PyAV)
        out_stream.layout = 'stereo'

        try:
            while self.channel.state == ChannelState.PLAYING or self.channel._generator_running:
                # Get new chunks from broadcast buffer
                chunks = self.channel.get_chunks_since(self._last_sequence)

                if chunks:
                    for seq, chunk in chunks:
                        self._last_sequence = seq

                        # Convert mono to stereo for AirPlay/pyatv compatibility
                        # chunk shape is (1, samples), need (2, samples)
                        if chunk.shape[0] == 1:
                            stereo_chunk = np.vstack([chunk, chunk])
                        else:
                            stereo_chunk = chunk

                        # Encode to MP3 (s16p = signed 16-bit planar for stereo arrays)
                        frame = av.AudioFrame.from_ndarray(stereo_chunk, format='s16p', layout='stereo')
                        frame.rate = SAMPLE_RATE

                        for packet in out_stream.encode(frame):
                            yield bytes(packet)
                else:
                    # No new chunks - wait for notification (blocks until data or timeout)
                    self.channel.wait_for_data(timeout=0.05)

        finally:
            logger.info(f'Channel {self.channel.id}: Client stream closed')
            self.channel.client_disconnected()
            output.close()


class ChannelManager:
    """
    Manages all channels for the Sonorium system.

    Provides channel creation, lookup, and lifecycle management.
    """

    def __init__(self, max_channels: int = 4, output_gain: float = DEFAULT_OUTPUT_GAIN):
        self.max_channels = max_channels
        self.output_gain = output_gain
        self._channels: dict[int, Channel] = {}
        self._lock = threading.Lock()

        # Pre-create all channels (starting from 1)
        for i in range(1, max_channels + 1):
            self._channels[i] = Channel(id=i, output_gain=output_gain)

        logger.info(f"ChannelManager initialized with {max_channels} channels, gain={output_gain}")

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

    def set_output_gain(self, gain: float) -> None:
        """Set output gain for all channels."""
        self.output_gain = gain
        for channel in self._channels.values():
            channel.output_gain = gain
        logger.info(f"ChannelManager: Set output gain to {gain}")

    def get_output_gain(self) -> float:
        """Get current output gain setting."""
        return self.output_gain
