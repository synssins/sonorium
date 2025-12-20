"""
Direct Sonos Control via SoCo

Uses SoCo library for reliable Sonos streaming playback.
Key advantage: force_radio=True treats streams as radio stations,
which works reliably for continuous audio streams.

Pause/stop/volume still go through HA's media_player service.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from sonorium.obs import logger

# SoCo is a blocking library, so we run it in a thread pool
_executor = ThreadPoolExecutor(max_workers=4)

# Cache of discovered Sonos devices (room_name -> SoCo device)
_sonos_cache: dict = {}
_cache_initialized = False


def _is_sonos_entity(entity_id: str) -> bool:
    """Check if an entity is likely a Sonos speaker."""
    # Sonos entities typically have 'sonos' in the name
    return 'sonos' in entity_id.lower()


def _discover_sonos_devices() -> dict:
    """
    Discover all Sonos devices on the network using SoCo.

    Returns dict mapping room name (lowercase) to SoCo device.
    """
    global _sonos_cache, _cache_initialized

    if _cache_initialized and _sonos_cache:
        return _sonos_cache

    try:
        import soco
        logger.info("  SoCo: Discovering Sonos devices...")

        devices = soco.discover(timeout=5)
        if devices:
            _sonos_cache = {}
            for device in devices:
                # Cache by room name (lowercase for matching)
                room = device.player_name.lower()
                _sonos_cache[room] = device
                logger.info(f"  SoCo: Found '{device.player_name}' at {device.ip_address}")

            _cache_initialized = True
            logger.info(f"  SoCo: Discovered {len(_sonos_cache)} Sonos device(s)")
        else:
            logger.warning("  SoCo: No Sonos devices found on network")

    except Exception as e:
        logger.error(f"  SoCo: Discovery failed: {e}")

    return _sonos_cache


def _get_sonos_ip_from_attributes(attributes: dict) -> Optional[str]:
    """Extract IP address from HA entity attributes."""
    # HA Sonos integration stores IP in various attributes
    # Try common attribute names
    for attr in ['ip_address', 'soco_ip', 'host', 'address']:
        if attr in attributes:
            return attributes[attr]

    # Some integrations store it nested
    if 'device_info' in attributes:
        device_info = attributes['device_info']
        if isinstance(device_info, dict):
            for attr in ['ip_address', 'host', 'address']:
                if attr in device_info:
                    return device_info[attr]

    return None


def _extract_room_from_entity(entity_id: str, friendly_name: str = None) -> Optional[str]:
    """
    Extract room/speaker name from entity_id or friendly_name.

    Examples:
        media_player.sonos_office -> "office"
        media_player.sonos_living_room -> "living room"
        "Sonos Office" -> "office"
    """
    # Try friendly name first (more reliable)
    if friendly_name:
        # Remove "Sonos" prefix if present
        name = friendly_name.lower()
        if name.startswith('sonos '):
            name = name[6:]
        return name.strip()

    # Fall back to entity_id parsing
    # media_player.sonos_office -> sonos_office -> office
    parts = entity_id.split('.')
    if len(parts) == 2:
        name = parts[1].lower()
        # Remove sonos_ prefix
        if name.startswith('sonos_'):
            name = name[6:]
        # Replace underscores with spaces
        name = name.replace('_', ' ')
        return name.strip()

    return None


def _play_uri_sync(ip: str, uri: str) -> bool:
    """
    Play URI on Sonos speaker (blocking, runs in thread).

    Uses force_radio=True to treat streams as radio stations.
    """
    try:
        import soco
        device = soco.SoCo(ip)

        # force_radio=True is key - makes Sonos treat this as a radio stream
        # rather than a finite file, which works better for continuous streams
        device.play_uri(uri, force_radio=True)

        logger.info(f"  SoCo: Started playback on {ip}")
        return True
    except Exception as e:
        logger.error(f"  SoCo: Failed to play on {ip}: {e}")
        return False


async def play_uri_on_sonos(ip: str, uri: str) -> bool:
    """
    Play URI on Sonos speaker (async wrapper).

    Args:
        ip: Sonos speaker IP address
        uri: Stream URL to play

    Returns:
        True if playback started successfully
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, _play_uri_sync, ip, uri)


class SonosPlayer:
    """
    Direct Sonos player using SoCo.

    Uses SoCo discovery to find Sonos devices, matching by room name.
    Falls back to HA entity attributes if available.
    """

    def __init__(self, media_controller):
        """
        Initialize with reference to media controller for HA API access.

        Args:
            media_controller: HAMediaController instance for getting entity states
        """
        self.media_controller = media_controller
        # Cache of entity_id -> SoCo device mappings
        self._device_cache: dict[str, any] = {}

    async def get_sonos_device(self, entity_id: str) -> Optional[any]:
        """
        Get SoCo device for a Sonos entity.

        Uses SoCo discovery to find devices, matching by room name.
        Falls back to HA attributes for IP if discovery fails.

        Returns:
            SoCo device object, or None if not found
        """
        # Check cache first
        if entity_id in self._device_cache:
            return self._device_cache[entity_id]

        # Get entity state to find friendly name
        state = await self.media_controller.get_state(entity_id)
        friendly_name = None
        if state:
            attributes = state.get('attributes', {})
            friendly_name = attributes.get('friendly_name')
            logger.debug(f"  SoCo: Entity {entity_id} friendly_name: {friendly_name}")
            logger.debug(f"  SoCo: Available attributes: {list(attributes.keys())}")

        # Extract room name from entity
        room_name = _extract_room_from_entity(entity_id, friendly_name)
        logger.info(f"  SoCo: Looking for room '{room_name}' for {entity_id}")

        # Discover Sonos devices (uses cache after first call)
        loop = asyncio.get_event_loop()
        devices = await loop.run_in_executor(_executor, _discover_sonos_devices)

        if not devices:
            logger.warning(f"  SoCo: No Sonos devices discovered")
            return None

        # Try to match by room name
        if room_name and room_name in devices:
            device = devices[room_name]
            self._device_cache[entity_id] = device
            logger.info(f"  SoCo: Matched '{room_name}' to {device.ip_address}")
            return device

        # Try partial matching if exact match fails
        if room_name:
            for cached_room, device in devices.items():
                if room_name in cached_room or cached_room in room_name:
                    self._device_cache[entity_id] = device
                    logger.info(f"  SoCo: Partial match '{room_name}' -> '{cached_room}' at {device.ip_address}")
                    return device

        # Log available rooms for debugging
        logger.warning(f"  SoCo: Could not match '{room_name}' to any discovered device")
        logger.info(f"  SoCo: Available rooms: {list(devices.keys())}")

        return None

    def is_sonos(self, entity_id: str) -> bool:
        """Check if entity is a Sonos speaker."""
        return _is_sonos_entity(entity_id)

    async def play_media(self, entity_id: str, media_url: str) -> bool:
        """
        Play media URL on a Sonos speaker using SoCo.

        Args:
            entity_id: HA entity ID (e.g., media_player.sonos_office)
            media_url: Stream URL to play

        Returns:
            True if playback started successfully
        """
        if not self.is_sonos(entity_id):
            logger.warning(f"  SoCo: {entity_id} is not a Sonos speaker")
            return False

        device = await self.get_sonos_device(entity_id)
        if not device:
            logger.error(f"  SoCo: Cannot play - no device found for {entity_id}")
            return False

        logger.info(f"  SoCo: Playing {media_url} on {entity_id} ({device.ip_address})")

        # Play URI with force_radio=True in thread pool
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                _executor,
                lambda: device.play_uri(media_url, force_radio=True)
            )
            logger.info(f"  SoCo: Started playback on {device.player_name}")
            return True
        except Exception as e:
            logger.error(f"  SoCo: Failed to play on {device.player_name}: {e}")
            return False

    async def play_media_multi(
        self,
        entity_ids: list[str],
        media_url: str,
    ) -> dict[str, bool]:
        """
        Play media on multiple Sonos speakers.

        Args:
            entity_ids: List of HA entity IDs
            media_url: Stream URL to play

        Returns:
            Dict mapping entity_id to success status
        """
        if not entity_ids:
            return {}

        # Filter to only Sonos speakers
        sonos_ids = [eid for eid in entity_ids if self.is_sonos(eid)]
        non_sonos_ids = [eid for eid in entity_ids if not self.is_sonos(eid)]

        if non_sonos_ids:
            logger.debug(f"  SoCo: Skipping non-Sonos speakers: {non_sonos_ids}")

        if not sonos_ids:
            return {}

        # Play on all Sonos speakers concurrently
        tasks = [self.play_media(eid, media_url) for eid in sonos_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        status = {}
        for entity_id, result in zip(sonos_ids, results):
            if isinstance(result, Exception):
                logger.error(f"  SoCo: Exception for {entity_id}: {result}")
                status[entity_id] = False
            else:
                status[entity_id] = result

        success_count = sum(1 for v in status.values() if v)
        logger.info(f"  SoCo: Started playback on {success_count}/{len(sonos_ids)} Sonos speakers")

        return status

    def clear_cache(self):
        """Clear the device cache and force rediscovery."""
        global _sonos_cache, _cache_initialized
        self._device_cache.clear()
        _sonos_cache.clear()
        _cache_initialized = False
        logger.info("  SoCo: Cleared device cache")
