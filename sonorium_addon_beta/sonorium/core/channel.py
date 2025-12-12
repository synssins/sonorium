"""
Sonorium Channel System

Channels are persistent audio streams that speakers connect to.
Each channel can play one theme at a time, with smooth crossfading
when switching between themes.

This allows speakers to stay connected while themes change seamlessly.
"""

from __future__ import annotations

import asyncio
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
    CROSSFADING = "crossfading"  # Transitioning between themes


@dataclass
class Channel:
    """
    A persistent audio stream channel.
    
    Speakers connect to channels, not themes. When a theme changes,
    the channel handles crossfading internally without interrupting
    the stream connection.
    """
    
    id: int
    name: str = ""
    
    # Current and next theme streams (chunk iterators)
    _current_chunks: Optional[Generator] = field(default=None, repr=False)
    _next_chunks: Optional[Generator] = field(default=None, repr=False)
    
    # Theme references
    _current_theme: Optional[ThemeDefinition] = field(default=None, repr=False)
    _next_theme: Optional[ThemeDefinition] = field(default=None, repr=False)
    
    # State tracking
    state: ChannelState = ChannelState.IDLE
    _crossfade_position: int = 0
    
    # Active client count (for resource management)
    _client_count: int = 0
    
    # Output gain
    output_gain: float = DEFAULT_OUTPUT_GAIN
    
    def __post_init__(self):
        if not self.name:
            self.name = f"Channel {self.id}"
        
        # Pre-generate crossfade curves (equal-power)
        self._fade_out = np.cos(np.linspace(0, np.pi/2, THEME_CROSSFADE_SAMPLES)).astype(np.float32)
        self._fade_in = np.sin(np.linspace(0, np.pi/2, THEME_CROSSFADE_SAMPLES)).astype(np.float32)
        
        # Silence chunk for idle state
        self._silence = np.zeros((1, CHUNK_SIZE), dtype=np.int16)
    
    @property
    def current_theme_id(self) -> Optional[str]:
        """Get the current theme ID."""
        return self._current_theme.id if self._current_theme else None
    
    @property
    def current_theme_name(self) -> Optional[str]:
        """Get the current theme name."""
        return self._current_theme.name if self._current_theme else None
    
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
        
        If already playing, initiates a crossfade transition.
        If idle, starts playing immediately.
        """
        if theme == self._current_theme:
            logger.info(f"Channel {self.id}: Theme '{theme.name}' already active, no change needed")
            return
        
        if self.state == ChannelState.IDLE:
            # First theme - start immediately
            logger.info(f"Channel {self.id}: Starting theme '{theme.name}'")
            self._current_theme = theme
            stream = theme.get_stream()
            self._current_chunks = stream.iter_chunks()
            self.state = ChannelState.PLAYING
            
        elif self.state == ChannelState.PLAYING:
            # Initiate crossfade to new theme
            logger.info(f"Channel {self.id}: Crossfading from '{self._current_theme.name}' to '{theme.name}'")
            self._next_theme = theme
            stream = theme.get_stream()
            self._next_chunks = stream.iter_chunks()
            self._crossfade_position = 0
            self.state = ChannelState.CROSSFADING
            
        elif self.state == ChannelState.CROSSFADING:
            # Already crossfading - update the target
            logger.info(f"Channel {self.id}: Redirecting crossfade to '{theme.name}'")
            self._next_theme = theme
            stream = theme.get_stream()
            self._next_chunks = stream.iter_chunks()
            # Keep current crossfade position for smooth transition
    
    def stop(self) -> None:
        """Stop the channel and return to idle."""
        logger.info(f"Channel {self.id}: Stopping playback")
        self._current_chunks = None
        self._current_theme = None
        self._next_chunks = None
        self._next_theme = None
        self.state = ChannelState.IDLE
        self._crossfade_position = 0
    
    def client_connected(self) -> None:
        """Track a new client connection."""
        self._client_count += 1
        logger.debug(f"Channel {self.id}: Client connected ({self._client_count} total)")
    
    def client_disconnected(self) -> None:
        """Track a client disconnection."""
        self._client_count = max(0, self._client_count - 1)
        logger.debug(f"Channel {self.id}: Client disconnected ({self._client_count} remaining)")
    
    def iter_chunks(self) -> Generator[np.ndarray, None, None]:
        """
        Generate audio chunks for this channel.
        
        Handles idle silence, theme playback, and crossfade transitions.
        """
        while True:
            if self.state == ChannelState.IDLE:
                # Output silence when no theme is playing
                yield self._silence
                
            elif self.state == ChannelState.PLAYING:
                # Normal playback from current theme
                if self._current_chunks:
                    try:
                        chunk = next(self._current_chunks)
                        yield chunk
                    except StopIteration:
                        # Theme ended unexpectedly
                        self.state = ChannelState.IDLE
                        yield self._silence
                else:
                    yield self._silence
                    
            elif self.state == ChannelState.CROSSFADING:
                # Crossfade between current and next theme
                if self._current_chunks and self._next_chunks:
                    try:
                        current_chunk = next(self._current_chunks)
                        next_chunk = next(self._next_chunks)
                        
                        # Apply crossfade
                        mixed = self._apply_crossfade(current_chunk, next_chunk)
                        yield mixed
                        
                        # Check if crossfade is complete
                        if self._crossfade_position >= THEME_CROSSFADE_SAMPLES:
                            self._complete_crossfade()
                            
                    except StopIteration:
                        # One stream ended - just switch
                        self._complete_crossfade()
                        yield self._silence
                else:
                    yield self._silence
    
    def _apply_crossfade(self, current: np.ndarray, next_chunk: np.ndarray) -> np.ndarray:
        """Apply crossfade mixing between two chunks."""
        chunk_size = current.shape[1]
        
        # Get fade positions
        fade_start = self._crossfade_position
        fade_end = min(fade_start + chunk_size, THEME_CROSSFADE_SAMPLES)
        fade_len = fade_end - fade_start
        
        if fade_len <= 0 or fade_start >= THEME_CROSSFADE_SAMPLES:
            # Crossfade complete, just return next
            self._crossfade_position += chunk_size
            return next_chunk
        
        # Convert to float for mixing
        current_f = current.astype(np.float32).flatten()
        next_f = next_chunk.astype(np.float32).flatten()
        
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
        mixed = current_f * fade_out + next_f * fade_in
        
        # Convert back to int16
        mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
        
        self._crossfade_position += chunk_size
        return mixed.reshape(1, -1)
    
    def _complete_crossfade(self) -> None:
        """Complete the crossfade transition."""
        logger.info(f"Channel {self.id}: Crossfade complete, now playing '{self._next_theme.name}'")
        
        # Swap next to current
        self._current_theme = self._next_theme
        self._current_chunks = self._next_chunks
        self._next_theme = None
        self._next_chunks = None
        self._crossfade_position = 0
        self.state = ChannelState.PLAYING
    
    def get_stream(self):
        """
        Get an MP3 stream iterator for this channel.
        
        This is what gets sent to the HTTP response.
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
    
    Encodes raw audio chunks from the channel into MP3 packets.
    """
    
    def __init__(self, channel: Channel):
        self.channel = channel
        self.channel.client_connected()
    
    def __iter__(self):
        output = av.open(file='.mp3', mode="w")
        bitrate = 128_000
        out_stream = output.add_stream(codec_name='mp3', rate=SAMPLE_RATE, bit_rate=bitrate)
        chunk_iter = self.channel.iter_chunks()

        start_time = time.time()
        audio_time = 0.0

        try:
            while True:
                for i, data in enumerate(chunk_iter):
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
