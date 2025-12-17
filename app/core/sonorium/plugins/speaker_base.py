"""
Network Speaker Plugin Base Class

Provides base functionality for network speaker plugins like Chromecast, Sonos, etc.
Speaker plugins discover devices on the network and can stream audio to them.
"""

from __future__ import annotations

import asyncio
from abc import abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Callable

from sonorium.plugins.base import BasePlugin
from sonorium.obs import logger


class SpeakerState(str, Enum):
    """State of a network speaker."""
    OFFLINE = "offline"       # Not reachable
    IDLE = "idle"             # Online but not playing
    PLAYING = "playing"       # Currently playing audio
    PAUSED = "paused"         # Paused
    BUFFERING = "buffering"   # Loading/buffering


@dataclass
class NetworkSpeaker:
    """
    Represents a discovered network speaker.

    Speaker plugins return these from discovery.
    """
    id: str                           # Unique identifier (e.g., Chromecast UUID)
    name: str                         # Display name
    model: str = ""                   # Device model (e.g., "Chromecast Audio")
    manufacturer: str = ""            # Manufacturer (e.g., "Google")
    ip_address: str = ""              # IP address
    port: int = 0                     # Control port
    state: SpeakerState = SpeakerState.OFFLINE
    volume: float = 1.0               # Volume 0.0-1.0
    is_muted: bool = False
    current_media: Optional[str] = None  # Currently playing URL
    capabilities: list[str] = field(default_factory=list)  # ["audio", "volume", "mute"]
    extra: dict = field(default_factory=dict)  # Plugin-specific data

    def to_dict(self) -> dict:
        """Serialize to dict for API."""
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "manufacturer": self.manufacturer,
            "ip_address": self.ip_address,
            "state": self.state.value,
            "volume": self.volume,
            "is_muted": self.is_muted,
            "current_media": self.current_media,
            "capabilities": self.capabilities,
        }


class SpeakerPlugin(BasePlugin):
    """
    Base class for network speaker plugins.

    Provides discovery, connection management, and playback control
    for network audio devices like Chromecast, Sonos, AirPlay, etc.

    Subclasses must implement:
    - discover_speakers(): Find devices on the network
    - play_url(speaker_id, url): Start playing a URL
    - stop(speaker_id): Stop playback
    - set_volume(speaker_id, level): Set volume

    Optional overrides:
    - pause(speaker_id): Pause playback
    - resume(speaker_id): Resume playback
    - mute(speaker_id, muted): Mute/unmute
    - get_speaker_state(speaker_id): Get current state
    """

    # Speaker plugin type identifier
    plugin_type: str = "speaker"

    def __init__(self, plugin_dir: Path, settings: dict, audio_path: Optional[Path] = None):
        super().__init__(plugin_dir, settings, audio_path)

        # Discovered speakers
        self._speakers: dict[str, NetworkSpeaker] = {}

        # Discovery state
        self._discovery_running = False
        self._discovery_task: Optional[asyncio.Task] = None

        # Stream URL provider callback (set by manager)
        self._get_stream_url: Optional[Callable[[str], str]] = None

        # State change callback
        self._on_speaker_change: Optional[Callable[[NetworkSpeaker], None]] = None

    def set_stream_url_provider(self, provider: Callable[[str], str]) -> None:
        """
        Set the callback to get stream URLs.

        Args:
            provider: Function that takes theme_id and returns stream URL
        """
        self._get_stream_url = provider

    def set_speaker_change_callback(self, callback: Callable[[NetworkSpeaker], None]) -> None:
        """Set callback for speaker state changes."""
        self._on_speaker_change = callback

    def get_stream_url(self, theme_id: str, preset_id: str = None) -> str:
        """
        Get the stream URL for a theme.

        Args:
            theme_id: Theme to stream
            preset_id: Optional preset to apply

        Returns:
            Full URL to the audio stream
        """
        if self._get_stream_url:
            url = self._get_stream_url(theme_id)
            if preset_id:
                url = f"{url}?preset_id={preset_id}"
            return url
        raise RuntimeError("Stream URL provider not configured")

    @property
    def speakers(self) -> list[NetworkSpeaker]:
        """Get list of discovered speakers."""
        return list(self._speakers.values())

    def get_speaker(self, speaker_id: str) -> Optional[NetworkSpeaker]:
        """Get a speaker by ID."""
        return self._speakers.get(speaker_id)

    def _update_speaker(self, speaker: NetworkSpeaker) -> None:
        """Update a speaker in the cache and notify listeners."""
        self._speakers[speaker.id] = speaker
        if self._on_speaker_change:
            self._on_speaker_change(speaker)

    def _remove_speaker(self, speaker_id: str) -> None:
        """Remove a speaker from the cache."""
        if speaker_id in self._speakers:
            del self._speakers[speaker_id]

    # --- Abstract methods (must implement) ---

    @abstractmethod
    async def discover_speakers(self) -> list[NetworkSpeaker]:
        """
        Discover speakers on the network.

        This is called periodically while discovery is running.

        Returns:
            List of discovered NetworkSpeaker objects
        """
        raise NotImplementedError

    @abstractmethod
    async def play_url(self, speaker_id: str, url: str) -> bool:
        """
        Play a URL on a speaker.

        Args:
            speaker_id: Target speaker ID
            url: Audio stream URL to play

        Returns:
            True if playback started successfully
        """
        raise NotImplementedError

    @abstractmethod
    async def stop(self, speaker_id: str) -> bool:
        """
        Stop playback on a speaker.

        Args:
            speaker_id: Target speaker ID

        Returns:
            True if stopped successfully
        """
        raise NotImplementedError

    @abstractmethod
    async def set_volume(self, speaker_id: str, level: float) -> bool:
        """
        Set volume on a speaker.

        Args:
            speaker_id: Target speaker ID
            level: Volume level 0.0-1.0

        Returns:
            True if volume was set
        """
        raise NotImplementedError

    # --- Optional overrides ---

    async def pause(self, speaker_id: str) -> bool:
        """Pause playback. Default: stop."""
        return await self.stop(speaker_id)

    async def resume(self, speaker_id: str) -> bool:
        """Resume playback. Default: not supported."""
        logger.warning(f"{self.name}: Resume not supported")
        return False

    async def mute(self, speaker_id: str, muted: bool) -> bool:
        """Mute/unmute speaker. Default: set volume to 0."""
        if muted:
            return await self.set_volume(speaker_id, 0.0)
        return True

    async def get_speaker_state(self, speaker_id: str) -> Optional[SpeakerState]:
        """Get current speaker state. Default: return cached state."""
        speaker = self.get_speaker(speaker_id)
        return speaker.state if speaker else None

    # --- Discovery control ---

    async def start_discovery(self, interval: float = 30.0) -> None:
        """
        Start continuous speaker discovery.

        Args:
            interval: Seconds between discovery scans
        """
        if self._discovery_running:
            return

        self._discovery_running = True
        self._discovery_task = asyncio.create_task(
            self._discovery_loop(interval)
        )
        logger.info(f"{self.name}: Started speaker discovery (interval: {interval}s)")

    async def stop_discovery(self) -> None:
        """Stop speaker discovery."""
        self._discovery_running = False
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass
            self._discovery_task = None
        logger.info(f"{self.name}: Stopped speaker discovery")

    async def _discovery_loop(self, interval: float) -> None:
        """Background discovery loop."""
        while self._discovery_running:
            try:
                speakers = await self.discover_speakers()

                # Update cache
                found_ids = set()
                for speaker in speakers:
                    found_ids.add(speaker.id)
                    self._update_speaker(speaker)

                # Mark missing speakers as offline
                for speaker_id in list(self._speakers.keys()):
                    if speaker_id not in found_ids:
                        speaker = self._speakers[speaker_id]
                        if speaker.state != SpeakerState.OFFLINE:
                            speaker.state = SpeakerState.OFFLINE
                            self._update_speaker(speaker)

                logger.debug(f"{self.name}: Found {len(speakers)} speakers")

            except Exception as e:
                logger.error(f"{self.name}: Discovery error: {e}")

            await asyncio.sleep(interval)

    async def refresh_speakers(self) -> list[NetworkSpeaker]:
        """
        Perform a single discovery scan.

        Returns:
            List of discovered speakers
        """
        try:
            speakers = await self.discover_speakers()
            for speaker in speakers:
                self._update_speaker(speaker)
            return speakers
        except Exception as e:
            logger.error(f"{self.name}: Refresh failed: {e}")
            return []

    # --- High-level playback control ---

    async def play_theme(
        self,
        speaker_id: str,
        theme_id: str,
        preset_id: str = None
    ) -> bool:
        """
        Play a theme on a speaker.

        Args:
            speaker_id: Target speaker
            theme_id: Theme to play
            preset_id: Optional preset

        Returns:
            True if playback started
        """
        try:
            url = self.get_stream_url(theme_id, preset_id)
            success = await self.play_url(speaker_id, url)

            if success:
                speaker = self.get_speaker(speaker_id)
                if speaker:
                    speaker.state = SpeakerState.PLAYING
                    speaker.current_media = url
                    self._update_speaker(speaker)

            return success
        except Exception as e:
            logger.error(f"{self.name}: Failed to play theme on {speaker_id}: {e}")
            return False

    async def stop_all(self) -> dict[str, bool]:
        """
        Stop playback on all speakers.

        Returns:
            Dict mapping speaker_id to success status
        """
        results = {}
        for speaker_id in self._speakers:
            results[speaker_id] = await self.stop(speaker_id)
        return results

    # --- Lifecycle ---

    async def on_enable(self) -> None:
        """Start discovery when plugin is enabled."""
        await self.start_discovery()

    async def on_disable(self) -> None:
        """Stop discovery and playback when disabled."""
        await self.stop_discovery()
        await self.stop_all()

    # --- UI Schema ---

    def get_ui_schema(self) -> dict:
        """Return speaker control UI schema."""
        return {
            "type": "speaker_control",
            "features": {
                "discovery": True,
                "volume": True,
                "mute": "mute" in self.get_capabilities(),
                "pause": "pause" in self.get_capabilities(),
            },
            "actions": [
                {"id": "refresh", "label": "Refresh", "icon": "refresh"},
                {"id": "play", "label": "Play", "icon": "play", "primary": True},
                {"id": "stop", "label": "Stop", "icon": "stop"},
            ]
        }

    def get_capabilities(self) -> list[str]:
        """
        Get plugin capabilities.

        Override to advertise supported features.

        Returns:
            List of capability strings: ["volume", "mute", "pause", "resume"]
        """
        return ["volume"]

    async def handle_action(self, action: str, data: dict) -> dict:
        """Handle UI actions for speaker control."""
        if action == "refresh":
            speakers = await self.refresh_speakers()
            return {
                "success": True,
                "message": f"Found {len(speakers)} speakers",
                "speakers": [s.to_dict() for s in speakers]
            }

        elif action == "play":
            speaker_id = data.get("speaker_id")
            theme_id = data.get("theme_id")
            preset_id = data.get("preset_id")

            if not speaker_id or not theme_id:
                return {"success": False, "message": "Missing speaker_id or theme_id"}

            success = await self.play_theme(speaker_id, theme_id, preset_id)
            return {
                "success": success,
                "message": "Playback started" if success else "Failed to start playback"
            }

        elif action == "stop":
            speaker_id = data.get("speaker_id")
            if speaker_id:
                success = await self.stop(speaker_id)
            else:
                results = await self.stop_all()
                success = all(results.values()) if results else True
            return {
                "success": success,
                "message": "Stopped" if success else "Failed to stop"
            }

        elif action == "volume":
            speaker_id = data.get("speaker_id")
            level = data.get("level", 1.0)
            if not speaker_id:
                return {"success": False, "message": "Missing speaker_id"}
            success = await self.set_volume(speaker_id, float(level))
            return {"success": success}

        return await super().handle_action(action, data)

    def to_dict(self) -> dict:
        """Serialize plugin info with speaker list."""
        result = super().to_dict()
        result["plugin_type"] = self.plugin_type
        result["speakers"] = [s.to_dict() for s in self.speakers]
        result["capabilities"] = self.get_capabilities()
        return result
