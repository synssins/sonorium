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
from typing import Optional, TYPE_CHECKING

import numpy as np

from sonorium.obs import logger
from sonorium.recording import SAMPLE_RATE, CROSSFADE_SAMPLES

if TYPE_CHECKING:
    from sonorium.theme import ThemeDefinition, ThemeStream


# Crossfade duration for theme transitions (in seconds)
THEME_CROSSFADE_DURATION = 3.0
THEME_CROSSFADE_SAMPLES = int(THEME_CROSSFADE_DURATION * SAMPLE_RATE)


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
    
    # Current and next theme streams
    _current_stream: Optional[ThemeStream] = field(default=None, repr=False)
    _next_stream: Optional[ThemeStream] = field(default=None, repr=False)
    
    # Theme references
    _current_theme: Optional[ThemeDefinition] = field(default=None, repr=False)
    _next_theme: Optional[ThemeDefinition] = field(default=None, repr=False)
    
    # State tracking
    state: ChannelState = ChannelState.IDLE
    _crossfade_position: int = 0
    
    # Active client count (for resource management)
    _client_count: int = 0
    
    # Lock for thread-safe theme changes
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    
    def __post_init__(self):
        if not self.name:
            self.name = f"Channel {self.id}"
        
        # Pre-generate crossfade curves (equal-power)
        self._fade_out = np.cos(np.linspace(0, np.pi/2, THEME_CROSSFADE_SAMPLES)).astype(np.float32)
        self._fade_in = np.sin(np.linspace(0, np.pi/2, THEME_CROSSFADE_SAMPLES)).astype(np.float32)
    
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
            self._current_stream = theme.get_stream()
            self.state = ChannelState.PLAYING
            
        elif self.state == ChannelState.PLAYING:
            # Initiate crossfade to new theme
            logger.info(f"Channel {self.id}: Crossfading from '{self._current_theme.name}' to '{theme.name}'")
            self._next_theme = theme
            self._next_stream = theme.get_stream()
            self._crossfade_position = 0
            self.state = ChannelState.CROSSFADING
            
        elif self.state == ChannelState.CROSSFADING:
            # Already crossfading - update the target
            logger.info(f"Channel {self.id}: Redirecting crossfade to '{theme.name}'")
            self._next_theme = theme
            self._next_stream = theme.get_stream()
            # Keep current crossfade position for smooth transition
    
    def stop(self) -> None:
        """Stop the channel and return to idle."""
        logger.info(f"Channel {self.id}: Stopping playback")
        self._current_stream = None
        self._current_theme = None
        self._next_stream = None
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
