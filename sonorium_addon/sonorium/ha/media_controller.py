"""
Home Assistant Media Player Control

Handles service calls to Home Assistant for controlling media players:
- Play media (stream URL)
- Pause/Stop playback
- Volume control

For Sonos speakers, uses SoCo library directly for more reliable streaming.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING
import asyncio

import httpx
from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.ha.sonos_player import SonosPlayer


# Short timeout - we just want to fire the request, not wait for completion
REQUEST_TIMEOUT = 5.0


class HAMediaController:
    """
    Controls Home Assistant media players via REST API.

    Used to send stream URLs to speakers and control playback.
    For Sonos speakers, uses SoCo library for more reliable streaming.
    """

    def __init__(self, api_url: str, token: str, use_soco_for_sonos: bool = True):
        """
        Initialize with HA API connection details.

        Args:
            api_url: Base URL for HA API (e.g., "http://supervisor/core/api")
            token: Long-lived access token or supervisor token
            use_soco_for_sonos: If True, use SoCo library for Sonos speakers
        """
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        # Initialize SonosPlayer for direct Sonos control
        self._sonos_player: Optional[SonosPlayer] = None
        self._use_soco_for_sonos = use_soco_for_sonos

        if use_soco_for_sonos:
            try:
                from sonorium.ha.sonos_player import SonosPlayer
                self._sonos_player = SonosPlayer(self)
                logger.info(f"HAMediaController initialized with SoCo support for Sonos")
            except ImportError:
                logger.warning("SoCo not available - using HA API for Sonos speakers")
                self._use_soco_for_sonos = False

        logger.info(f"HAMediaController initialized with API URL: {self.api_url}")
    
    async def _post_service(self, domain: str, service: str, data: dict) -> bool:
        """
        Call a Home Assistant service (fire-and-forget style).
        
        Args:
            domain: Service domain (e.g., "media_player")
            service: Service name (e.g., "play_media")
            data: Service data
        
        Returns:
            True if request was sent, False on immediate failure
        """
        url = f"{self.api_url}/services/{domain}/{service}"
        logger.info(f"  POST {url}")
        logger.debug(f"    Data: {data}")
        
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.post(url, headers=self.headers, json=data)
                logger.debug(f"    Response: {response.status_code}")
                if response.status_code not in (200, 201):
                    logger.error(f"    HA API error {response.status_code}: {response.text[:500]}")
                return response.status_code in (200, 201)
        except httpx.TimeoutException:
            # Timeout is OK - request was sent, speaker might just be slow
            logger.debug(f"    Request sent (timed out waiting for response)")
            return True
        except Exception as e:
            logger.error(f"    Service call failed: {e}")
            return False
    
    # --- Playback Control ---
    
    @logger.instrument("Playing media on {entity_id}...")
    async def play_media(
        self,
        entity_id: str,
        media_url: str,
        media_type: str = "music"
    ) -> bool:
        """
        Play media URL on a speaker.

        Args:
            entity_id: Media player entity ID
            media_url: URL to stream
            media_type: Media content type (default: "music")

        Returns:
            True if request was sent successfully

        Note for Google Cast devices:
            Cast devices fetch the stream URL themselves, so the URL must be
            accessible from the Cast device's network location. If playback
            starts but no audio is heard, verify:
            1. The stream URL is reachable from the Cast device
            2. Port 8008 is accessible (check firewall rules)
            3. The IP address in the URL is the correct host IP, not a Docker internal IP
        """
        # For Cast devices, use audio/mpeg content type for better compatibility
        # This helps Cast devices understand the stream format
        if "cast" in entity_id.lower() or "google" in entity_id.lower():
            logger.info(f"  Detected Cast device, using audio/mpeg content type")
            media_type = "audio/mpeg"

        data = {
            "entity_id": entity_id,
            "media_content_id": media_url,
            "media_content_type": media_type,
            "extra": {
                "enqueue": "replace",  # Replace current queue/stream
            }
        }

        # Log the full request for debugging
        logger.info(f"  play_media: URL={media_url}")
        logger.info(f"  play_media: type={media_type}, entity={entity_id}")

        success = await self._post_service("media_player", "play_media", data)
        if success:
            logger.info(f"  Started playback on {entity_id}")
            logger.info(f"  NOTE: If no audio, verify the stream URL is reachable from the speaker")
        return success
    
    @logger.instrument("Playing media on multiple speakers...")
    async def play_media_multi(
        self,
        entity_ids: list[str],
        media_url: str,
        media_type: str = "music"
    ) -> dict[str, bool]:
        """
        Play media URL on multiple speakers simultaneously.

        For Sonos speakers, uses SoCo library for more reliable streaming.
        For other speakers, uses HA's media_player.play_media service.

        Args:
            entity_ids: List of media player entity IDs
            media_url: URL to stream
            media_type: Media content type

        Returns:
            Dict mapping entity_id to success status
        """
        if not entity_ids:
            return {}

        status = {}

        # Separate Sonos from non-Sonos speakers
        sonos_ids = []
        other_ids = []

        if self._sonos_player and self._use_soco_for_sonos:
            for eid in entity_ids:
                if self._sonos_player.is_sonos(eid):
                    sonos_ids.append(eid)
                else:
                    other_ids.append(eid)
        else:
            other_ids = entity_ids

        # Play on Sonos speakers using SoCo (if any)
        if sonos_ids:
            logger.info(f"  Using SoCo for {len(sonos_ids)} Sonos speaker(s)")
            sonos_results = await self._sonos_player.play_media_multi(sonos_ids, media_url)
            status.update(sonos_results)

        # Play on other speakers using HA API
        if other_ids:
            logger.info(f"  Using HA API for {len(other_ids)} speaker(s)")
            tasks = [
                self.play_media(entity_id, media_url, media_type)
                for entity_id in other_ids
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for entity_id, result in zip(other_ids, results):
                if isinstance(result, Exception):
                    logger.error(f"  Exception for {entity_id}: {result}")
                    status[entity_id] = False
                else:
                    status[entity_id] = result

        success_count = sum(1 for v in status.values() if v)
        logger.info(f"  {success_count}/{len(entity_ids)} speakers started")
        return status
    
    @logger.instrument("Pausing {entity_id}...")
    async def pause(self, entity_id: str) -> bool:
        """Pause playback on a speaker."""
        data = {"entity_id": entity_id}
        return await self._post_service("media_player", "media_pause", data)
    
    async def pause_multi(self, entity_ids: list[str]) -> dict[str, bool]:
        """Pause playback on multiple speakers."""
        if not entity_ids:
            return {}
        tasks = [self.pause(eid) for eid in entity_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            eid: (r if not isinstance(r, Exception) else False)
            for eid, r in zip(entity_ids, results)
        }
    
    @logger.instrument("Stopping {entity_id}...")
    async def stop(self, entity_id: str) -> bool:
        """Stop playback on a speaker."""
        data = {"entity_id": entity_id}
        return await self._post_service("media_player", "media_stop", data)
    
    async def stop_multi(self, entity_ids: list[str]) -> dict[str, bool]:
        """Stop playback on multiple speakers."""
        if not entity_ids:
            return {}
        tasks = [self.stop(eid) for eid in entity_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            eid: (r if not isinstance(r, Exception) else False)
            for eid, r in zip(entity_ids, results)
        }
    
    # --- Volume Control ---
    
    @logger.instrument("Setting volume on {entity_id} to {volume_level}...")
    async def set_volume(self, entity_id: str, volume_level: float) -> bool:
        """
        Set volume on a speaker.
        
        Args:
            entity_id: Media player entity ID
            volume_level: Volume 0.0 - 1.0
        
        Returns:
            True if successful
        """
        data = {
            "entity_id": entity_id,
            "volume_level": max(0.0, min(1.0, volume_level)),
        }
        return await self._post_service("media_player", "volume_set", data)
    
    async def set_volume_multi(
        self, 
        entity_ids: list[str], 
        volume_level: float
    ) -> dict[str, bool]:
        """Set volume on multiple speakers."""
        if not entity_ids:
            return {}
        tasks = [self.set_volume(eid, volume_level) for eid in entity_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            eid: (r if not isinstance(r, Exception) else False)
            for eid, r in zip(entity_ids, results)
        }
    
    @logger.instrument("Muting {entity_id}...")
    async def mute(self, entity_id: str, mute: bool = True) -> bool:
        """Mute or unmute a speaker."""
        data = {
            "entity_id": entity_id,
            "is_volume_muted": mute,
        }
        return await self._post_service("media_player", "volume_mute", data)
    
    # --- State Queries ---
    
    async def get_state(self, entity_id: str) -> Optional[dict]:
        """
        Get current state of a media player.
        
        Returns:
            State dict with 'state' and 'attributes', or None if not found
        """
        url = f"{self.api_url}/states/{entity_id}"
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(url, headers=self.headers)
                if response.status_code == 200:
                    return response.json()
        except Exception as e:
            logger.error(f"Failed to get state for {entity_id}: {e}")
        return None
    
    async def is_playing(self, entity_id: str) -> bool:
        """Check if a media player is currently playing."""
        state = await self.get_state(entity_id)
        if state:
            return state.get("state") == "playing"
        return False
    
    async def get_playing_states(self, entity_ids: list[str]) -> dict[str, bool]:
        """Check playing state for multiple speakers."""
        if not entity_ids:
            return {}
        tasks = [self.get_state(eid) for eid in entity_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            eid: (r.get("state") == "playing" if isinstance(r, dict) else False)
            for eid, r in zip(entity_ids, results)
        }


# Factory function
def create_media_controller_from_supervisor() -> HAMediaController:
    """
    Create HAMediaController using supervisor API.
    For use within Home Assistant addons.
    """
    from sonorium.settings import settings
    
    return HAMediaController(
        api_url=f"{settings.ha_supervisor_api.replace('/core', '')}/core/api",
        token=settings.token,
    )
