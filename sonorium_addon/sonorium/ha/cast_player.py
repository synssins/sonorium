"""
Direct Google Cast Control via pychromecast

Uses pychromecast library for reliable Cast streaming playback.
Bypasses Home Assistant's play_media service for more reliable streaming.

Cast Detection:
1. Entity ID patterns (_display, _hub, nest_, chromecast_, google_home_)
2. HA device registry (cast integration, Google manufacturer)
3. Entity attributes (device_class, supported_features)

IP Resolution:
1. HA device registry (configuration_url, connections)
2. Entity state attributes
3. Manual IP mappings (fallback)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Optional, TYPE_CHECKING
from concurrent.futures import ThreadPoolExecutor

from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.ha.media_controller import HAMediaController

# pychromecast is a blocking library, so we run it in a thread pool
_executor = ThreadPoolExecutor(max_workers=4)

# Entity ID patterns that indicate Cast devices
CAST_ENTITY_PATTERNS = [
    '_display',      # Nest Hub, smart displays
    '_hub',          # Nest Hub
    'nest_',         # Nest devices
    'chromecast_',   # Chromecast devices
    'google_home_',  # Google Home speakers
    'google_mini',   # Google Home Mini
    'google_max',    # Google Home Max
    '_speaker',      # Generic speaker suffix
    'cast_',         # Generic cast prefix
]

# Manufacturer strings that indicate Cast devices
CAST_MANUFACTURERS = [
    'google',
    'google inc',
    'google inc.',
    'google llc',
]

# Integration identifiers for Cast
CAST_INTEGRATIONS = [
    'cast',
    'google_cast',
]


def _is_cast_by_entity_pattern(entity_id: str) -> bool:
    """Check if entity ID matches Cast device patterns."""
    entity_lower = entity_id.lower()
    return any(pattern in entity_lower for pattern in CAST_ENTITY_PATTERNS)


def _is_cast_by_attributes(attributes: dict) -> bool:
    """Check if entity attributes indicate a Cast device."""
    # Check device_class
    device_class = attributes.get('device_class', '').lower()
    if device_class in ('speaker', 'tv', 'receiver'):
        # Could be Cast, but need more evidence
        pass

    # Check for Cast-specific attributes
    if 'app_id' in attributes or 'app_name' in attributes:
        return True

    # Check supported features for Cast-like capabilities
    # Cast devices typically support PLAY_MEDIA, VOLUME_SET, etc.
    supported = attributes.get('supported_features', 0)
    # Cast typically has: PAUSE, VOLUME_SET, VOLUME_MUTE, TURN_ON, TURN_OFF, PLAY_MEDIA
    # Value ~21437 or similar
    if supported > 20000:
        # High feature count often indicates Cast
        pass

    return False


class CastPlayer:
    """
    Direct Cast player using pychromecast.

    Gets Cast device IPs from HA's device registry.
    Streams directly to Cast devices without going through HA's play_media.
    """

    def __init__(self, media_controller: 'HAMediaController'):
        """
        Initialize with reference to media controller for HA API access.

        Args:
            media_controller: HAMediaController instance for getting entity states
        """
        self.media_controller = media_controller
        # Cache of entity_id -> IP mappings
        self._ip_cache: dict[str, str] = {}
        # Cache of entity_id -> is_cast determination
        self._cast_cache: dict[str, bool] = {}
        # Cache of device name -> IP from HA registry
        self._ha_device_ips: dict[str, str] = {}
        self._ha_ips_loaded = False
        # Active Cast connections (entity_id -> Chromecast object)
        self._connections: dict[str, object] = {}

    async def _load_ha_device_ips(self):
        """Load Cast device IPs from HA device registry."""
        if self._ha_ips_loaded:
            return

        self._ha_ips_loaded = True
        self._ha_device_ips = await self._get_cast_ips_from_ha()

        if self._ha_device_ips:
            logger.info(f"  Cast: Found {len(self._ha_device_ips)} Cast device(s) in HA registry")
        else:
            logger.warning("  Cast: No Cast IPs found in HA device registry")

    async def _get_cast_ips_from_ha(self) -> dict[str, str]:
        """
        Get Cast device IPs by querying HA's device registry via WebSocket API.

        Returns dict mapping device/entity name (lowercase) -> IP address
        """
        from sonorium.ha.registry import WEBSOCKETS_AVAILABLE
        import re

        if not WEBSOCKETS_AVAILABLE:
            logger.warning("  Cast: websockets not available for HA query")
            return {}

        try:
            import websockets
            import json

            # Connect to HA WebSocket API
            token = self.media_controller.token
            ws_url = self.media_controller.api_url.replace('http://', 'ws://').replace('/api', '/api/websocket')

            logger.info(f"  Cast: Connecting to HA WebSocket: {ws_url}")

            # Increase max_size for large HA installations (default 1MB is too small)
            async with websockets.connect(ws_url, max_size=10 * 1024 * 1024) as ws:
                # Wait for auth_required
                msg = json.loads(await ws.recv())
                if msg.get('type') != 'auth_required':
                    logger.warning(f"  Cast: Unexpected WebSocket message: {msg}")
                    return {}

                # Authenticate
                await ws.send(json.dumps({
                    "type": "auth",
                    "access_token": token
                }))

                msg = json.loads(await ws.recv())
                if msg.get('type') != 'auth_ok':
                    logger.warning(f"  Cast: HA WebSocket auth failed: {msg}")
                    return {}

                logger.info("  Cast: WebSocket authenticated, querying device registry...")

                # Query device registry
                await ws.send(json.dumps({
                    "id": 1,
                    "type": "config/device_registry/list"
                }))

                msg = json.loads(await ws.recv())
                if not msg.get('success'):
                    logger.warning(f"  Cast: Device registry query failed: {msg}")
                    return {}

                devices = msg.get('result', [])
                cast_ips = {}

                logger.debug(f"  Cast: Scanning {len(devices)} devices in registry")

                for device in devices:
                    # Check if it's a Cast device
                    identifiers = device.get('identifiers', [])
                    manufacturer = (device.get('manufacturer') or '').lower()
                    name = (device.get('name') or '').lower()  # Handle None name

                    # Broader Cast detection - include 'nest', 'chromecast', 'google' in name
                    is_cast = (
                        any('cast' in str(ident).lower() for ident in identifiers) or
                        manufacturer in CAST_MANUFACTURERS or
                        'nest' in name or
                        'chromecast' in name or
                        'google home' in name
                    )

                    if not is_cast:
                        continue

                    logger.debug(f"  Cast: Found Cast device '{name}' (mfr: {manufacturer})")

                    name_normalized = name.replace(' ', '_')

                    # Try configuration_url - often contains IP
                    config_url = device.get('configuration_url', '')
                    if config_url:
                        ip_match = re.search(r'://(\d+\.\d+\.\d+\.\d+)', config_url)
                        if ip_match:
                            ip = ip_match.group(1)
                            cast_ips[name] = ip
                            cast_ips[name_normalized] = ip
                            logger.info(f"  Cast: Found '{name}' at {ip} from configuration_url")
                            continue

                    # Try connections field
                    connections = device.get('connections', [])
                    for conn in connections:
                        if isinstance(conn, (list, tuple)) and len(conn) >= 2:
                            conn_type, conn_value = conn[0], conn[1]
                            if conn_type == 'ip':
                                cast_ips[name] = conn_value
                                cast_ips[name_normalized] = conn_value
                                logger.info(f"  Cast: Found '{name}' at {conn_value} from connections")
                                break

                    # If still no IP, log what we have for debugging
                    if name not in cast_ips:
                        logger.debug(f"  Cast: Device '{name}' has no IP in config_url or connections")
                        logger.debug(f"  Cast:   config_url: {config_url}")
                        logger.debug(f"  Cast:   connections: {connections}")

                # If WebSocket found no IPs, try REST API fallback
                if not cast_ips:
                    logger.info("  Cast: No IPs from WebSocket, trying REST API fallback...")
                    cast_ips = await self._get_cast_ips_via_rest()

                return cast_ips

        except Exception as e:
            logger.warning(f"  Cast: Failed to query HA: {e}")
            import traceback
            logger.debug(f"  Cast: Traceback: {traceback.format_exc()}")
            return {}

    async def _get_cast_ips_via_rest(self) -> dict[str, str]:
        """
        Fallback: Get Cast device IPs via REST API (device registry).
        Similar to sonos_player.py's REST fallback.
        """
        import re
        import httpx

        try:
            url = f"{self.media_controller.api_url}/config/device_registry/list"
            logger.info(f"  Cast: REST API fallback: {url}")

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url, headers=self.media_controller.headers)
                if response.status_code != 200:
                    logger.warning(f"  Cast: REST API returned {response.status_code}")
                    return {}

                devices = response.json()
                cast_ips = {}

                for device in devices:
                    identifiers = device.get('identifiers', [])
                    manufacturer = (device.get('manufacturer') or '').lower()
                    name = device.get('name', '').lower()

                    # Broader Cast detection
                    is_cast = (
                        any('cast' in str(ident).lower() for ident in identifiers) or
                        manufacturer in CAST_MANUFACTURERS or
                        'nest' in name or
                        'chromecast' in name or
                        'google home' in name
                    )

                    if not is_cast:
                        continue

                    name_normalized = name.replace(' ', '_')

                    # Try configuration_url
                    config_url = device.get('configuration_url', '')
                    if config_url:
                        ip_match = re.search(r'://(\d+\.\d+\.\d+\.\d+)', config_url)
                        if ip_match:
                            ip = ip_match.group(1)
                            cast_ips[name] = ip
                            cast_ips[name_normalized] = ip
                            logger.info(f"  Cast: REST found '{name}' at {ip}")
                            continue

                    # Try connections field
                    connections = device.get('connections', [])
                    for conn in connections:
                        if isinstance(conn, (list, tuple)) and len(conn) >= 2:
                            if conn[0] == 'ip':
                                cast_ips[name] = conn[1]
                                cast_ips[name_normalized] = conn[1]
                                logger.info(f"  Cast: REST found '{name}' at {conn[1]}")
                                break

                return cast_ips

        except Exception as e:
            logger.warning(f"  Cast: REST API fallback failed: {e}")
            return {}

    async def is_cast(self, entity_id: str) -> bool:
        """
        Check if entity is a Cast device.

        Uses multiple detection methods:
        1. Entity ID pattern matching
        2. HA device registry (manufacturer, identifiers)
        3. Entity attributes

        Results are cached for performance.
        """
        # Check cache first
        if entity_id in self._cast_cache:
            return self._cast_cache[entity_id]

        # Quick check: entity ID patterns
        if _is_cast_by_entity_pattern(entity_id):
            self._cast_cache[entity_id] = True
            logger.debug(f"  Cast: {entity_id} detected by entity pattern")
            return True

        # Check entity attributes
        state = await self.media_controller.get_state(entity_id)
        if state:
            attributes = state.get('attributes', {})

            # Check for Cast-specific attributes
            if _is_cast_by_attributes(attributes):
                self._cast_cache[entity_id] = True
                logger.debug(f"  Cast: {entity_id} detected by attributes")
                return True

            # Check friendly_name for patterns
            friendly_name = attributes.get('friendly_name', '').lower()
            if any(pattern.replace('_', ' ') in friendly_name for pattern in CAST_ENTITY_PATTERNS):
                self._cast_cache[entity_id] = True
                logger.debug(f"  Cast: {entity_id} detected by friendly_name")
                return True

        # Load HA device IPs if not done
        await self._load_ha_device_ips()

        # Check if entity name matches any Cast device in registry
        entity_name = entity_id.split('.')[-1].lower() if '.' in entity_id else entity_id.lower()
        for device_name in self._ha_device_ips.keys():
            if entity_name in device_name or device_name in entity_name:
                self._cast_cache[entity_id] = True
                logger.debug(f"  Cast: {entity_id} detected by HA registry match")
                return True

        # Not a Cast device
        self._cast_cache[entity_id] = False
        return False

    async def get_cast_ip(self, entity_id: str) -> Optional[str]:
        """
        Get IP address for a Cast entity.

        Resolution order:
        1. Cache (previous lookups)
        2. Entity state attributes
        3. HA device registry

        Returns:
            IP address string, or None if not found
        """
        # Check cache first
        if entity_id in self._ip_cache:
            return self._ip_cache[entity_id]

        # Get entity state
        state = await self.media_controller.get_state(entity_id)
        friendly_name = None

        if state:
            attributes = state.get('attributes', {})
            friendly_name = attributes.get('friendly_name')

            # Log all attributes for debugging
            logger.debug(f"  Cast: Entity {entity_id} attributes: {list(attributes.keys())}")

            # Try to find IP in various attribute names
            ip_attrs = ['ip_address', 'host', 'address', 'device_ip', 'cast_info', 'ip']
            for attr in ip_attrs:
                if attr in attributes:
                    value = attributes[attr]
                    # Handle dict (cast_info might be a dict with host inside)
                    if isinstance(value, dict) and 'host' in value:
                        ip = value['host']
                    elif isinstance(value, str) and value:
                        ip = value
                    else:
                        continue
                    self._ip_cache[entity_id] = ip
                    logger.info(f"  Cast: Found IP {ip} in entity attributes ({attr})")
                    return ip

        # Load HA device IPs
        await self._load_ha_device_ips()

        # Try to match by name
        names_to_try = []

        # Extract entity name from entity_id
        entity_name = entity_id.split('.')[-1].lower() if '.' in entity_id else entity_id.lower()
        names_to_try.append(entity_name)
        names_to_try.append(entity_name.replace('_', ' '))

        # Add friendly name
        if friendly_name:
            names_to_try.append(friendly_name.lower())
            names_to_try.append(friendly_name.lower().replace(' ', '_'))

        # Search in HA device IPs
        for name in names_to_try:
            if name in self._ha_device_ips:
                ip = self._ha_device_ips[name]
                self._ip_cache[entity_id] = ip
                logger.info(f"  Cast: Found IP {ip} for '{name}' from HA registry")
                return ip

            # Partial match
            for device_name, ip in self._ha_device_ips.items():
                if name in device_name or device_name in name:
                    self._ip_cache[entity_id] = ip
                    logger.info(f"  Cast: Partial match '{name}' -> '{device_name}' at {ip}")
                    return ip

        # Final fallback: mDNS discovery
        logger.info(f"  Cast: Trying mDNS discovery for {entity_id}...")
        ip = await self._discover_cast_ip_via_mdns(friendly_name, entity_name)
        if ip:
            self._ip_cache[entity_id] = ip
            return ip

        logger.warning(f"  Cast: Could not find IP for {entity_id}")
        if self._ha_device_ips:
            logger.debug(f"  Cast: Available devices: {list(self._ha_device_ips.keys())}")

        return None

    async def _discover_cast_ip_via_mdns(
        self,
        friendly_name: Optional[str],
        entity_name: Optional[str]
    ) -> Optional[str]:
        """
        Discover Cast device IP via mDNS/zeroconf.

        Uses pychromecast's built-in discovery mechanism.
        This is a last resort when HA doesn't have the IP.

        Args:
            friendly_name: The friendly name to search for
            entity_name: The entity name (from entity_id) to search for

        Returns:
            IP address string, or None if not found
        """
        try:
            import pychromecast
            from concurrent.futures import ThreadPoolExecutor

            def discover_sync():
                """Run blocking discovery in thread."""
                try:
                    # Quick discovery with short timeout
                    services, browser = pychromecast.discovery.discover_chromecasts(timeout=5)
                    pychromecast.discovery.stop_discovery(browser)
                    return services
                except Exception as e:
                    logger.debug(f"  Cast: mDNS discovery error: {e}")
                    return []

            # Run in thread pool since pychromecast is blocking
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor(max_workers=1) as pool:
                services = await loop.run_in_executor(pool, discover_sync)

            if not services:
                logger.debug("  Cast: mDNS discovered no devices")
                return None

            # Build search names
            search_names = []
            if friendly_name:
                search_names.append(friendly_name.lower())
            if entity_name:
                search_names.append(entity_name.lower().replace('_', ' '))
                search_names.append(entity_name.lower())

            logger.debug(f"  Cast: mDNS found {len(services)} device(s), searching for: {search_names}")

            # Search through discovered devices
            for service in services:
                try:
                    device_name = (service.friendly_name or '').lower()
                    device_ip = service.host

                    if not device_ip:
                        continue

                    # Cache all discovered Cast devices for future use
                    self._ha_device_ips[device_name] = device_ip
                    self._ha_device_ips[device_name.replace(' ', '_')] = device_ip

                    # Check if this matches our target
                    for search_name in search_names:
                        if search_name in device_name or device_name in search_name:
                            logger.info(f"  Cast: mDNS found '{device_name}' at {device_ip}")
                            return device_ip

                except Exception as e:
                    logger.debug(f"  Cast: Error processing mDNS service: {e}")
                    continue

            logger.debug(f"  Cast: mDNS did not find matching device")
            return None

        except ImportError:
            logger.debug("  Cast: pychromecast not available for mDNS discovery")
            return None
        except Exception as e:
            logger.warning(f"  Cast: mDNS discovery failed: {e}")
            return None

    def _create_cast_connection(self, ip: str, port: int = 8009):
        """Create a pychromecast connection to a Cast device."""
        try:
            import pychromecast
            from pychromecast import Chromecast
            from pychromecast.models import CastInfo, HostServiceInfo

            cast_info = CastInfo(
                services={HostServiceInfo(ip, port)},
                uuid=uuid.uuid4(),
                model_name=None,
                friendly_name=None,
                host=ip,
                port=port,
                cast_type="cast",
                manufacturer="Google Inc.",
            )

            cast = Chromecast(cast_info=cast_info)
            cast.wait(timeout=10)
            return cast
        except Exception as e:
            logger.error(f"  Cast: Failed to connect to {ip}: {e}")
            return None

    def _play_media_sync(self, ip: str, url: str, content_type: str = "audio/mpeg") -> bool:
        """
        Play media URL on Cast device (blocking, runs in thread).

        Args:
            ip: Cast device IP address
            url: Media URL to play
            content_type: MIME type (default: audio/mpeg)

        Returns:
            True if playback started successfully
        """
        try:
            cast = self._create_cast_connection(ip)
            if not cast:
                return False

            mc = cast.media_controller
            mc.play_media(url, content_type)

            # Wait for playback to start
            import time
            for _ in range(10):
                time.sleep(0.5)
                if mc.status.player_state in ('PLAYING', 'BUFFERING'):
                    logger.info(f"  Cast: Started playback on {ip} (state: {mc.status.player_state})")
                    return True
                if mc.status.idle_reason:
                    logger.warning(f"  Cast: Playback failed on {ip}: {mc.status.idle_reason}")
                    return False

            logger.warning(f"  Cast: Timeout waiting for playback on {ip}")
            return False

        except Exception as e:
            logger.error(f"  Cast: Failed to play on {ip}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return False

    async def _play_via_ha_api(self, entity_id: str, media_url: str) -> bool:
        """
        Play media using HA's media_player.play_media service.

        This is the fallback when we can't find the device IP for pychromecast.
        HA's Cast integration already knows how to reach the device (even across VLANs).

        Args:
            entity_id: HA entity ID
            media_url: Stream URL to play

        Returns:
            True if the service call succeeded
        """
        try:
            import httpx

            url = f"{self.media_controller.api_url}/services/media_player/play_media"
            data = {
                "entity_id": entity_id,
                "media_content_id": media_url,
                "media_content_type": "music",
            }

            logger.info(f"  Cast: Using HA API fallback for {entity_id}")
            logger.debug(f"  Cast: POST {url}")
            logger.debug(f"  Cast: Data: {data}")

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    url,
                    json=data,
                    headers=self.media_controller.headers,
                )

                if response.status_code == 200:
                    logger.info(f"  Cast: HA API play_media succeeded for {entity_id}")
                    return True
                else:
                    logger.warning(f"  Cast: HA API returned {response.status_code}: {response.text}")
                    return False

        except Exception as e:
            logger.error(f"  Cast: HA API fallback failed for {entity_id}: {e}")
            return False

    async def play_media(self, entity_id: str, media_url: str) -> bool:
        """
        Play media URL on a Cast device using pychromecast.

        Falls back to HA's media_player.play_media service if IP cannot be found
        (e.g., device on different VLAN where mDNS doesn't work).

        Args:
            entity_id: HA entity ID
            media_url: Stream URL to play

        Returns:
            True if playback started successfully
        """
        if not await self.is_cast(entity_id):
            logger.warning(f"  Cast: {entity_id} is not a Cast device")
            return False

        ip = await self.get_cast_ip(entity_id)
        if not ip:
            # Fall back to HA API - HA's Cast integration knows how to reach the device
            logger.info(f"  Cast: No IP found for {entity_id}, using HA API fallback")
            return await self._play_via_ha_api(entity_id, media_url)

        logger.info(f"  Cast: Playing {media_url} on {entity_id} ({ip})")

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(_executor, self._play_media_sync, ip, media_url)

    async def play_media_multi(
        self,
        entity_ids: list[str],
        media_url: str,
    ) -> dict[str, bool]:
        """
        Play media on multiple Cast devices.

        Args:
            entity_ids: List of HA entity IDs
            media_url: Stream URL to play

        Returns:
            Dict mapping entity_id to success status
        """
        if not entity_ids:
            return {}

        # Filter to only Cast devices
        cast_checks = await asyncio.gather(*[self.is_cast(eid) for eid in entity_ids])
        cast_ids = [eid for eid, is_cast in zip(entity_ids, cast_checks) if is_cast]
        non_cast_ids = [eid for eid, is_cast in zip(entity_ids, cast_checks) if not is_cast]

        if non_cast_ids:
            logger.debug(f"  Cast: Skipping non-Cast devices: {non_cast_ids}")

        if not cast_ids:
            return {}

        # Play on all Cast devices concurrently
        tasks = [self.play_media(eid, media_url) for eid in cast_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        status = {}
        for entity_id, result in zip(cast_ids, results):
            if isinstance(result, Exception):
                logger.error(f"  Cast: Exception for {entity_id}: {result}")
                status[entity_id] = False
            else:
                status[entity_id] = result

        success_count = sum(1 for v in status.values() if v)
        logger.info(f"  Cast: Started playback on {success_count}/{len(cast_ids)} Cast devices")

        return status

    def clear_cache(self):
        """Clear all caches and force reload from HA."""
        self._ip_cache.clear()
        self._cast_cache.clear()
        self._ha_device_ips.clear()
        self._ha_ips_loaded = False
        logger.info("  Cast: Cleared all caches")
