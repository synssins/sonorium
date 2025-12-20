"""
Direct Sonos Control via SoCo

Uses SoCo library for reliable Sonos streaming playback.
Key advantage: force_radio=True treats streams as radio stations,
which works reliably for continuous audio streams.

Pause/stop/volume still go through HA's media_player service.

IP Resolution:
Since Docker containers can't use mDNS/SSDP discovery, we use:
1. Manual IP mapping from addon config (sonos_ips setting)
2. Fallback to HA's Sonos integration config entries
"""

from __future__ import annotations

import asyncio
import os
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

from sonorium.obs import logger

# SoCo is a blocking library, so we run it in a thread pool
_executor = ThreadPoolExecutor(max_workers=4)

# Manual IP mappings loaded from config (room_name -> IP)
_manual_ip_map: dict[str, str] = {}


def load_sonos_ip_config():
    """
    Load manual Sonos IP mappings from environment/config.

    Expected format in addon options:
        sonos_ips: "office=192.168.1.50,living_room=192.168.1.51"

    Or as environment variable:
        SONORIUM__SONOS_IPS="office=192.168.1.50,living_room=192.168.1.51"
    """
    global _manual_ip_map

    # Try environment variable first
    ip_config = os.environ.get('SONORIUM__SONOS_IPS', '')

    if not ip_config:
        # Try reading from options file
        try:
            import json
            options_path = '/data/options.json'
            if os.path.exists(options_path):
                with open(options_path) as f:
                    options = json.load(f)
                    ip_config = options.get('sonos_ips', '')
        except Exception as e:
            logger.debug(f"  SoCo: Could not read options.json: {e}")

    if ip_config:
        _manual_ip_map = {}
        for mapping in ip_config.split(','):
            mapping = mapping.strip()
            if '=' in mapping:
                room, ip = mapping.split('=', 1)
                room = room.strip().lower().replace(' ', '_')
                ip = ip.strip()
                _manual_ip_map[room] = ip
                logger.info(f"  SoCo: Manual IP mapping: {room} -> {ip}")

        if _manual_ip_map:
            logger.info(f"  SoCo: Loaded {len(_manual_ip_map)} manual IP mapping(s)")
    else:
        logger.debug("  SoCo: No manual IP mappings configured")


# Load config on module import
load_sonos_ip_config()


async def _get_sonos_ips_from_ha(media_controller) -> dict[str, str]:
    """
    Get Sonos IPs by querying HA's device registry via WebSocket API.

    Returns dict mapping entity_id -> IP address
    """
    from sonorium.ha.registry import WEBSOCKETS_AVAILABLE

    if not WEBSOCKETS_AVAILABLE:
        logger.warning("  SoCo: websockets not available for HA device query")
        return {}

    try:
        import websockets
        import json

        # Connect to HA WebSocket API
        token = media_controller.token
        ws_url = media_controller.api_url.replace('http://', 'ws://').replace('/api', '/api/websocket')

        logger.debug(f"  SoCo: Connecting to HA WebSocket: {ws_url}")

        async with websockets.connect(ws_url) as ws:
            # Wait for auth_required
            msg = json.loads(await ws.recv())
            if msg.get('type') != 'auth_required':
                return {}

            # Authenticate
            await ws.send(json.dumps({
                "type": "auth",
                "access_token": token
            }))

            msg = json.loads(await ws.recv())
            if msg.get('type') != 'auth_ok':
                logger.warning(f"  SoCo: HA WebSocket auth failed: {msg}")
                return {}

            # Query device registry
            await ws.send(json.dumps({
                "id": 1,
                "type": "config/device_registry/list"
            }))

            msg = json.loads(await ws.recv())
            if not msg.get('success'):
                return {}

            devices = msg.get('result', [])
            sonos_ips = {}

            for device in devices:
                # Check if it's a Sonos device
                identifiers = device.get('identifiers', [])
                is_sonos = any('sonos' in str(ident).lower() for ident in identifiers)

                if is_sonos:
                    # Get connections which may contain IP
                    connections = device.get('connections', [])
                    name = device.get('name', '').lower()

                    for conn_type, conn_value in connections:
                        # IP is stored as ('ip', '192.168.1.x') or similar
                        if conn_type == 'ip' or '.' in conn_value and conn_value.replace('.', '').isdigit():
                            # This might be an IP
                            if conn_type == 'ip':
                                sonos_ips[name] = conn_value
                                logger.info(f"  SoCo: Found Sonos '{name}' at {conn_value} from HA device registry")
                            break

            return sonos_ips

    except Exception as e:
        logger.warning(f"  SoCo: Failed to query HA device registry: {e}")
        return {}


def _is_sonos_entity(entity_id: str) -> bool:
    """Check if an entity is likely a Sonos speaker."""
    # Sonos entities typically have 'sonos' in the name
    return 'sonos' in entity_id.lower()


def _create_soco_device(ip: str):
    """Create a SoCo device object for a known IP address."""
    try:
        import soco
        device = soco.SoCo(ip)
        # Verify it's reachable by getting player name
        _ = device.player_name
        return device
    except Exception as e:
        logger.warning(f"  SoCo: Could not connect to {ip}: {e}")
        return None


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

    Gets Sonos IPs from HA's device registry (automatic, no manual config).
    Falls back to manual IP mappings if device registry fails.
    """

    def __init__(self, media_controller):
        """
        Initialize with reference to media controller for HA API access.

        Args:
            media_controller: HAMediaController instance for getting entity states
        """
        self.media_controller = media_controller
        # Cache of entity_id -> IP mappings
        self._ip_cache: dict[str, str] = {}
        # Cache of device name -> IP from HA registry
        self._ha_device_ips: dict[str, str] = {}
        self._ha_ips_loaded = False

    async def _load_ha_device_ips(self):
        """Load Sonos IPs from HA device registry (one-time)."""
        if self._ha_ips_loaded:
            return

        self._ha_ips_loaded = True
        self._ha_device_ips = await _get_sonos_ips_from_ha(self.media_controller)

        if not self._ha_device_ips:
            logger.warning("  SoCo: No Sonos IPs found in HA device registry")
            # Fall back to manual mappings if available
            if _manual_ip_map:
                logger.info(f"  SoCo: Using {len(_manual_ip_map)} manual IP mapping(s)")

    async def get_sonos_ip(self, entity_id: str) -> Optional[str]:
        """
        Get IP address for a Sonos entity.

        Resolution order:
        1. Cache (previous lookups)
        2. HA device registry (automatic)
        3. Manual IP mappings (fallback)

        Returns:
            IP address string, or None if not found
        """
        # Check cache first
        if entity_id in self._ip_cache:
            return self._ip_cache[entity_id]

        # Load HA device IPs if not done yet
        await self._load_ha_device_ips()

        # Get entity state to find friendly name
        state = await self.media_controller.get_state(entity_id)
        friendly_name = None
        if state:
            attributes = state.get('attributes', {})
            friendly_name = attributes.get('friendly_name')
            logger.debug(f"  SoCo: Entity {entity_id} friendly_name: {friendly_name}")

        # Extract room name from entity
        room_name = _extract_room_from_entity(entity_id, friendly_name)
        logger.info(f"  SoCo: Looking for IP for '{room_name}' ({entity_id})")

        # Try HA device registry first
        if self._ha_device_ips:
            # Try exact match
            if room_name and room_name in self._ha_device_ips:
                ip = self._ha_device_ips[room_name]
                self._ip_cache[entity_id] = ip
                logger.info(f"  SoCo: Found IP {ip} for '{room_name}' from HA registry")
                return ip

            # Try partial match
            if room_name:
                for device_name, ip in self._ha_device_ips.items():
                    if room_name in device_name or device_name in room_name:
                        self._ip_cache[entity_id] = ip
                        logger.info(f"  SoCo: Partial match '{room_name}' -> '{device_name}' at {ip}")
                        return ip

        # Fall back to manual mappings
        if _manual_ip_map:
            room_key = room_name.replace(' ', '_') if room_name else None
            if room_key and room_key in _manual_ip_map:
                ip = _manual_ip_map[room_key]
                self._ip_cache[entity_id] = ip
                logger.info(f"  SoCo: Using manual IP {ip} for '{room_name}'")
                return ip

        # Log what we have for debugging
        logger.warning(f"  SoCo: Could not find IP for '{room_name}'")
        if self._ha_device_ips:
            logger.info(f"  SoCo: Available from HA: {list(self._ha_device_ips.keys())}")
        if _manual_ip_map:
            logger.info(f"  SoCo: Available manual: {list(_manual_ip_map.keys())}")

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

        ip = await self.get_sonos_ip(entity_id)
        if not ip:
            logger.error(f"  SoCo: Cannot play - no IP found for {entity_id}")
            return False

        logger.info(f"  SoCo: Playing {media_url} on {entity_id} ({ip})")
        return await play_uri_on_sonos(ip, media_url)

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
        """Clear the IP cache and force reload from HA."""
        self._ip_cache.clear()
        self._ha_device_ips.clear()
        self._ha_ips_loaded = False
        logger.info("  SoCo: Cleared IP cache")
