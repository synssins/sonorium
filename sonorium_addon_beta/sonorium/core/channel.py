"""
Sonorium Channel System

Channels are persistent audio streams that speakers connect to.
Each channel can play one theme at a time, with smooth crossfading
when switching between themes.

This allows speakers to stay connected while themes change seamlessly.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, TYPE_CHECKING, Generator

import numpy as np

from sonorium.obs import logger
from sonorium.recording import SAMPLE_RATE, CROSSFADE_SAMPLES
from fmtr.tools import av

if TYPE_CHECKING:
    from sonorium.theme import ThemeDefinition, ThemeStream


# Crossfade duration for theme transitions (in seconds)
THEME_CROSSFADE_DURATION = 3.0
THEME_CROSSFADE_SAMPLES = int(THEME_CROSSFADE_DURATION * SAMPLE_RATE)

# Chunk size for silence generation
CHUNK_SIZE = 1024

# Default output gain
DEFAULT_OUTPUT_GAIN = 6.0


class ChannelState(str, Enum):
    """Current state of a channel."""
    IDLE = "idle"              # No theme assigned, outputting silence
    PLAYING = "playing"        # Playing a theme


@dataclass
class Channel:
    """
    A persistent audio stream channel.
    
    Speakers connect to channels, not themes. When a theme changes,
    each connected client handles crossfading independently.
    
    The Channel just tracks WHAT theme is playing - each client
    gets its own independent audio generator.
    """
    
    id: int
    name: str = ""
    
    # Current theme reference
    _current_theme: Optional[ThemeDefinition] = field(default=None, repr=False)
    
    # Theme version - increments when theme changes, clients use this to detect changes
    _theme_version: int = 0
    
    # State tracking
    state: ChannelState = ChannelState.IDLE
    
    # Active client count (for resource management)
    _client_count: int = 0
    
    # Lock for thread-safe theme changes
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    
    def __post_init__(self):
        if not self.name:
            self.name = f"Channel {self.id}"
    
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
        
        Connected clients will detect the change via theme_version
        and handle crossfading independently.
        """
        with self._lock:
            if theme == self._current_theme:
                logger.info(f"Channel {self.id}: Theme '{theme.name}' already active, no change needed")
                return
            
            old_theme = self._current_theme.name if self._current_theme else "none"
            logger.info(f"Channel {self.id}: Changing theme from '{old_theme}' to '{theme.name}'")
            
            self._current_theme = theme
            self._theme_version += 1
            self.state = ChannelState.PLAYING
    
    def stop(self) -> None:
        """Stop the channel and return to idle."""
        with self._lock:
            logger.info(f"Channel {self.id}: Stopping playback")
            self._current_theme = None
            self._theme_version += 1
            self.state = ChannelState.IDLE
    
    def client_connected(self) -> None:
        """Track a new client connection."""
        self._client_count += 1
        logger.info(f"Channel {self.id}: Client connected ({self._client_count} total)")
    
    def client_disconnected(self) -> None:
        """Track a client disconnection."""
        self._client_count = max(0, self._client_count - 1)
        logger.info(f"Channel {self.id}: Client disconnected ({self._client_count} remaining)")
    
    def get_stream(self):
        """
        Get an MP3 stream iterator for this channel.
        
        Each call creates a NEW independent stream - multiple clients
        can connect without sharing generators.
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
        }


class ChannelStream:
    """
    MP3 streaming wrapper for a Channel.
    
    Each ChannelStream has its OWN independent audio generator.
    When the channel's theme changes, this stream handles crossfading
    from the old theme to the new one independently.
    """
    
    def __init__(self, channel: Channel):
        self.channel = channel
        self.channel.client_connected()
        
        # Pre-generate crossfade curves (equal-power)
        self._fade_out = np.cos(np.linspace(0, np.pi/2, THEME_CROSSFADE_SAMPLES)).astype(np.float32)
        self._fade_in = np.sin(np.linspace(0, np.pi/2, THEME_CROSSFADE_SAMPLES)).astype(np.float32)
        
        # Silence chunk for idle state
        self._silence = np.zeros((1, CHUNK_SIZE), dtype=np.int16)
    
    def __iter__(self):
        output = av.open(file='.mp3', mode="w")
        bitrate = 128_000
        out_stream = output.add_stream(codec_name='mp3', rate=SAMPLE_RATE, bit_rate=bitrate)

        start_time = time.time()
        audio_time = 0.0
        
        # Track which theme version we're playing
        current_version = -1
        current_chunks = None
        
        # For crossfading
        old_chunks = None
        crossfade_position = 0
        is_crossfading = False

        try:
            while True:
                # Check if theme has changed
                channel_version = self.channel.theme_version
                if channel_version != current_version:
                    # Theme changed!
                    if current_chunks is not None and self.channel.current_theme is not None:
                        # We have an old stream - start crossfading
                        old_chunks = current_chunks
                        crossfade_position = 0
                        is_crossfading = True
                        logger.info(f"Channel {self.channel.id} client: Starting crossfade to new theme")
                    
                    # Get new theme stream
                    current_version = channel_version
                    if self.channel.current_theme:
                        stream = self.channel.current_theme.get_stream()
                        current_chunks = stream.iter_chunks()
                    else:
                        current_chunks = None
                
                # Generate audio chunk
                if current_chunks is None:
                    # No theme - output silence
                    data = self._silence
                elif is_crossfading and old_chunks is not None:
                    # Crossfading between old and new theme
                    try:
                        old_chunk = next(old_chunks)
                        new_chunk = next(current_chunks)
                        data = self._apply_crossfade(old_chunk, new_chunk, crossfade_position)
                        crossfade_position += data.shape[1]
                        
                        if crossfade_position >= THEME_CROSSFADE_SAMPLES:
                            # Crossfade complete
                            is_crossfading = False
                            old_chunks = None
                            logger.info(f"Channel {self.channel.id} client: Crossfade complete")
                    except StopIteration:
                        # Old stream ended - just use new
                        is_crossfading = False
                        old_chunks = None
                        data = next(current_chunks) if current_chunks else self._silence
                else:
                    # Normal playback
                    try:
                        data = next(current_chunks)
                    except StopIteration:
                        data = self._silence
                
                # Encode to MP3
                frame = av.AudioFrame.from_ndarray(data, format='s16', layout='mono')
                frame.rate = SAMPLE_RATE

                frame_duration = frame.samples / frame.rate
                audio_time += frame_duration

                for packet in out_stream.encode(frame):
                    yield bytes(packet)

                # Maintain real-time pacing
                now = time.time()
                ahead = audio_time - (now - start_time)
                if ahead > 0:
                    time.sleep(ahead)

        finally:
            logger.info(f'Channel {self.channel.id}: Stream closed')
            self.channel.client_disconnected()
            output.close()
    
    def _apply_crossfade(self, old_chunk: np.ndarray, new_chunk: np.ndarray, position: int) -> np.ndarray:
        """Apply crossfade mixing between two chunks."""
        chunk_size = old_chunk.shape[1]
        
        # Get fade positions
        fade_start = position
        fade_end = min(fade_start + chunk_size, THEME_CROSSFADE_SAMPLES)
        fade_len = fade_end - fade_start
        
        if fade_len <= 0 or fade_start >= THEME_CROSSFADE_SAMPLES:
            # Crossfade complete, just return new
            return new_chunk
        
        # Convert to float for mixing
        old_f = old_chunk.astype(np.float32).flatten()
        new_f = new_chunk.astype(np.float32).flatten()
        
        # Get fade curves for this chunk
        if fade_len < chunk_size:
            # Partial fade at end
            fade_out = np.concatenate([
                self._fade_out[fade_start:fade_end],
                np.zeros(chunk_size - fade_len, dtype=np.float32)
            ])
            fade_in = np.concatenate([
                self._fade_in[fade_start:fade_end],
                np.ones(chunk_size - fade_len, dtype=np.float32)
            ])
        else:
            fade_out = self._fade_out[fade_start:fade_start + chunk_size]
            fade_in = self._fade_in[fade_start:fade_start + chunk_size]
        
        # Mix with crossfade
        mixed = old_f * fade_out + new_f * fade_in
        
        # Convert back to int16
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
        
        return mixed.reshape(1, -1)


class ChannelManager:
    """
    Manages a pool of audio channels.
    
    Channels are created at startup based on configuration.
    Sessions are assigned to channels for playback.
    """
    
    def __init__(self, max_channels: int = 6):
        self.max_channels = max_channels
        self.channels: dict[int, Channel] = {}
        
        # Create channel pool
        for i in range(1, max_channels + 1):
            self.channels[i] = Channel(id=i)
        
        logger.info(f"ChannelManager: Created {max_channels} channels")
    
    def get_channel(self, channel_id: int) -> Optional[Channel]:
        """Get a channel by ID."""
        return self.channels.get(channel_id)
    
    def get_available_channel(self) -> Optional[Channel]:
        """
        Get an available (idle) channel.
        
        Returns the lowest-numbered idle channel, or None if all are in use.
        """
        for i in range(1, self.max_channels + 1):
            channel = self.channels[i]
            if channel.state == ChannelState.IDLE:
                return channel
        return None
    
    def get_channel_for_theme(self, theme_id: str) -> Optional[Channel]:
        """
        Find a channel already playing the given theme.
        
        Useful for sharing channels when multiple sessions use the same theme.
        """
        for channel in self.channels.values():
            if channel.current_theme_id == theme_id:
                return channel
        return None
    
    def list_channels(self) -> list[dict]:
        """Get status of all channels for API."""
        return [ch.to_dict() for ch in self.channels.values()]
    
    def get_active_count(self) -> int:
        """Count channels with connected clients."""
        return sum(1 for ch in self.channels.values() if ch.is_active)
