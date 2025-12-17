"""
Chromecast Speaker Plugin

Discovers and controls Chromecast and Google Cast devices on the local network.

Requirements:
    pip install pychromecast

The plugin uses mDNS/Zeroconf for discovery and the Cast protocol for control.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from sonorium.plugins.speaker_base import SpeakerPlugin, NetworkSpeaker, SpeakerState
from sonorium.obs import logger

# Try to import pychromecast
try:
    import pychromecast
    from pychromecast.controllers.media import MediaController
    PYCHROMECAST_AVAILABLE = True
except ImportError:
    PYCHROMECAST_AVAILABLE = False
    pychromecast = None


class ChromecastPlugin(SpeakerPlugin):
    """
    Chromecast/Google Cast speaker plugin.

    Discovers Cast devices on the network and streams audio to them.
    Supports:
    - Chromecast Audio
    - Chromecast (video devices)
    - Google Home / Nest speakers
    - Android TV with Cast support
    - Sonos (Cast-enabled models)
    - Other Cast-enabled speakers
    """

    id = "chromecast"
    name = "Chromecast"
    version = "1.0.0"
    description = "Stream audio to Chromecast and Google Cast devices"
    author = "Sonorium"
    builtin = True

    def __init__(self, plugin_dir: Path, settings: dict, audio_path: Optional[Path] = None):
        super().__init__(plugin_dir, settings, audio_path)

        # Cast browser for discovery
        self._browser = None
        self._zeroconf = None

        # Active cast connections
        self._casts: dict[str, 'pychromecast.Chromecast'] = {}

        # Discovery lock
        self._discovery_lock = asyncio.Lock()

    async def on_load(self) -> None:
        """Check for pychromecast availability."""
        if not PYCHROMECAST_AVAILABLE:
            logger.warning(
                f"{self.name}: pychromecast not installed. "
                "Install with: pip install pychromecast"
            )

    async def on_unload(self) -> None:
        """Clean up Cast connections."""
        await self._cleanup_browser()
        await self._disconnect_all()

    async def _cleanup_browser(self) -> None:
        """Stop the Cast browser."""
        if self._browser:
            try:
                self._browser.stop_discovery()
            except Exception as e:
                logger.debug(f"Browser cleanup: {e}")
            self._browser = None

        if self._zeroconf:
            try:
                self._zeroconf.close()
            except Exception as e:
                logger.debug(f"Zeroconf cleanup: {e}")
            self._zeroconf = None

    async def _disconnect_all(self) -> None:
        """Disconnect from all Cast devices."""
        for cast in self._casts.values():
            try:
                cast.disconnect()
            except Exception:
                pass
        self._casts.clear()

    async def discover_speakers(self) -> list[NetworkSpeaker]:
        """Discover Chromecast devices on the network."""
        if not PYCHROMECAST_AVAILABLE:
            return []

        async with self._discovery_lock:
            try:
                # Run discovery in thread pool (blocking operation)
                loop = asyncio.get_event_loop()
                speakers = await loop.run_in_executor(
                    None,
                    self._discover_sync
                )
                return speakers
            except Exception as e:
                logger.error(f"{self.name}: Discovery error: {e}")
                return []

    def _discover_sync(self) -> list[NetworkSpeaker]:
        """Synchronous discovery (runs in thread pool)."""
        speakers = []

        try:
            # Get Chromecast devices (short timeout for responsiveness)
            chromecasts, browser = pychromecast.get_chromecasts(timeout=5)

            for cc in chromecasts:
                try:
                    # Get device info
                    device = cc.cast_info

                    speaker = NetworkSpeaker(
                        id=device.uuid,
                        name=device.friendly_name or device.host,
                        model=device.model_name or "Chromecast",
                        manufacturer="Google",
                        ip_address=device.host,
                        port=device.port or 8009,
                        state=SpeakerState.IDLE,  # Will update when we connect
                        capabilities=["volume", "mute", "pause"],
                        extra={
                            "cast_type": device.cast_type,
                            "uuid": str(device.uuid),
                        }
                    )
                    speakers.append(speaker)

                    # Store cast object for later use
                    self._casts[str(device.uuid)] = cc

                except Exception as e:
                    logger.debug(f"Error processing Chromecast: {e}")

            # Stop browser after discovery
            browser.stop_discovery()

        except Exception as e:
            logger.error(f"Chromecast discovery failed: {e}")

        return speakers

    async def _get_cast(self, speaker_id: str) -> Optional['pychromecast.Chromecast']:
        """Get or connect to a Cast device."""
        if speaker_id in self._casts:
            cast = self._casts[speaker_id]
            # Check if still connected
            if cast.socket_client and cast.socket_client.is_connected:
                return cast

        # Need to reconnect - run discovery to find the device
        await self.refresh_speakers()
        return self._casts.get(speaker_id)

    async def play_url(self, speaker_id: str, url: str) -> bool:
        """Play a URL on a Chromecast device."""
        if not PYCHROMECAST_AVAILABLE:
            return False

        cast = await self._get_cast(speaker_id)
        if not cast:
            logger.error(f"{self.name}: Speaker {speaker_id} not found")
            return False

        try:
            # Run in thread pool (blocking operation)
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                None,
                self._play_sync,
                cast,
                url
            )
            return success
        except Exception as e:
            logger.error(f"{self.name}: Play failed: {e}")
            return False

    def _play_sync(self, cast: 'pychromecast.Chromecast', url: str) -> bool:
        """Synchronous play (runs in thread pool)."""
        try:
            # Wait for connection
            cast.wait(timeout=10)

            # Get media controller
            mc = cast.media_controller

            # Play the URL
            mc.play_media(
                url,
                "audio/mpeg",
                title="Sonorium",
                thumb=None,
                stream_type="LIVE"  # Continuous stream
            )

            mc.block_until_active(timeout=10)
            return True

        except Exception as e:
            logger.error(f"Chromecast play error: {e}")
            return False

    async def stop(self, speaker_id: str) -> bool:
        """Stop playback on a Chromecast device."""
        if not PYCHROMECAST_AVAILABLE:
            return False

        cast = await self._get_cast(speaker_id)
        if not cast:
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._stop_sync, cast)
            return True
        except Exception as e:
            logger.error(f"{self.name}: Stop failed: {e}")
            return False

    def _stop_sync(self, cast: 'pychromecast.Chromecast') -> None:
        """Synchronous stop."""
        try:
            cast.quit_app()
        except Exception as e:
            logger.debug(f"Stop error: {e}")

    async def pause(self, speaker_id: str) -> bool:
        """Pause playback."""
        if not PYCHROMECAST_AVAILABLE:
            return False

        cast = await self._get_cast(speaker_id)
        if not cast:
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: cast.media_controller.pause()
            )
            return True
        except Exception as e:
            logger.error(f"{self.name}: Pause failed: {e}")
            return False

    async def resume(self, speaker_id: str) -> bool:
        """Resume playback."""
        if not PYCHROMECAST_AVAILABLE:
            return False

        cast = await self._get_cast(speaker_id)
        if not cast:
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: cast.media_controller.play()
            )
            return True
        except Exception as e:
            logger.error(f"{self.name}: Resume failed: {e}")
            return False

    async def set_volume(self, speaker_id: str, level: float) -> bool:
        """Set volume (0.0-1.0)."""
        if not PYCHROMECAST_AVAILABLE:
            return False

        cast = await self._get_cast(speaker_id)
        if not cast:
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: cast.set_volume(max(0.0, min(1.0, level)))
            )

            # Update cached speaker
            speaker = self.get_speaker(speaker_id)
            if speaker:
                speaker.volume = level
                self._update_speaker(speaker)

            return True
        except Exception as e:
            logger.error(f"{self.name}: Volume failed: {e}")
            return False

    async def mute(self, speaker_id: str, muted: bool) -> bool:
        """Mute/unmute."""
        if not PYCHROMECAST_AVAILABLE:
            return False

        cast = await self._get_cast(speaker_id)
        if not cast:
            return False

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: cast.set_volume_muted(muted)
            )

            speaker = self.get_speaker(speaker_id)
            if speaker:
                speaker.is_muted = muted
                self._update_speaker(speaker)

            return True
        except Exception as e:
            logger.error(f"{self.name}: Mute failed: {e}")
            return False

    async def get_speaker_state(self, speaker_id: str) -> Optional[SpeakerState]:
        """Get current speaker state from device."""
        if not PYCHROMECAST_AVAILABLE:
            return None

        cast = await self._get_cast(speaker_id)
        if not cast:
            return SpeakerState.OFFLINE

        try:
            status = cast.media_controller.status
            if status:
                if status.player_is_playing:
                    return SpeakerState.PLAYING
                elif status.player_is_paused:
                    return SpeakerState.PAUSED
                elif status.player_is_idle:
                    return SpeakerState.IDLE
            return SpeakerState.IDLE
        except Exception:
            return SpeakerState.OFFLINE

    def get_capabilities(self) -> list[str]:
        """Chromecast supports volume, mute, pause, resume."""
        return ["volume", "mute", "pause", "resume"]

    def get_settings_schema(self) -> dict:
        """Plugin settings."""
        return {
            "discovery_timeout": {
                "type": "number",
                "default": 5,
                "label": "Discovery Timeout (seconds)",
                "min": 1,
                "max": 30
            },
            "auto_reconnect": {
                "type": "boolean",
                "default": True,
                "label": "Auto-reconnect on disconnect"
            }
        }
