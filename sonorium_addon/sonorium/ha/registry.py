"""
Home Assistant Registry Integration

Queries HA's floor, area, and device registries to build
the speaker hierarchy for Sonorium's speaker selection UI.

Uses WebSocket API for registry data (floors, areas, entity registry)
since these are not available via REST API.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Optional

from fmtr.tools import http
from sonorium.obs import logger

# Try to import websockets, fall back gracefully if not available
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets library not available - floor/area hierarchy will be limited")


@dataclass
class Speaker:
    """A media player entity that can play audio."""
    entity_id: str
    name: str
    area_id: Optional[str] = None
    area_name: Optional[str] = None
    floor_id: Optional[str] = None
    floor_name: Optional[str] = None
    ip_address: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "floor_id": self.floor_id,
            "floor_name": self.floor_name,
            "ip_address": self.ip_address,
        }


@dataclass
class Area:
    """A Home Assistant area (room)."""
    area_id: str
    name: str
    floor_id: Optional[str] = None
    floor_name: Optional[str] = None
    speakers: list[Speaker] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "area_id": self.area_id,
            "name": self.name,
            "floor_id": self.floor_id,
            "floor_name": self.floor_name,
            "speakers": [s.to_dict() for s in self.speakers],
        }


@dataclass
class Floor:
    """A Home Assistant floor (level of home)."""
    floor_id: str
    name: str
    level: int = 0
    areas: list[Area] = field(default_factory=list)
    
    def to_dict(self) -> dict:
        return {
            "floor_id": self.floor_id,
            "name": self.name,
            "level": self.level,
            "areas": [a.to_dict() for a in self.areas],
        }


@dataclass
class SpeakerHierarchy:
    """Complete floor/area/speaker hierarchy."""
    floors: list[Floor] = field(default_factory=list)
    unassigned_areas: list[Area] = field(default_factory=list)  # Areas with no floor
    unassigned_speakers: list[Speaker] = field(default_factory=list)  # Speakers with no area
    
    def to_dict(self) -> dict:
        return {
            "floors": [f.to_dict() for f in self.floors],
            "unassigned_areas": [a.to_dict() for a in self.unassigned_areas],
            "unassigned_speakers": [s.to_dict() for s in self.unassigned_speakers],
        }
    
    def get_all_speakers(self) -> list[Speaker]:
        """Get flat list of all speakers."""
        speakers = []
        for floor in self.floors:
            for area in floor.areas:
                speakers.extend(area.speakers)
        for area in self.unassigned_areas:
            speakers.extend(area.speakers)
        speakers.extend(self.unassigned_speakers)
        return speakers


class HARegistry:
    """
    Queries Home Assistant registries to build speaker hierarchy.
    
    Uses the HA REST API to fetch:
    - Floor registry
    - Area registry  
    - Entity registry (filtered to media_player domain)
    - Entity states (to get friendly names)
    """
    
    def __init__(self, api_url: str, token: str):
        """
        Initialize with HA API connection details.
        
        Args:
            api_url: Base URL for HA API (e.g., "http://supervisor/core/api")
            token: Long-lived access token or supervisor token
        """
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        
        # Cached data
        self._floors: dict[str, Floor] = {}
        self._areas: dict[str, Area] = {}
        self._speakers: dict[str, Speaker] = {}
        self._hierarchy: Optional[SpeakerHierarchy] = None
    
    def _get(self, endpoint: str) -> dict | list | None:
        """Make GET request to HA API."""
        import json
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        logger.debug(f"HARegistry GET: {url}")
        with http.Client() as client:
            response = client.get(url, headers=self.headers)
            # Get raw text first for debugging
            text = response.text

            # Check for error responses
            if response.status_code != 200:
                logger.warning(f"HARegistry got status {response.status_code} for {endpoint}")
                return None

            # Check if response looks like JSON
            text_stripped = text.strip()
            if not text_stripped or text_stripped[0] not in '[{':
                logger.warning(f"HARegistry got non-JSON response for {endpoint}: {text_stripped[:100]}")
                return None

            logger.debug(f"HARegistry raw response (first 200 chars): {text[:200]}")

            # Parse JSON
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                logger.warning(f"HARegistry JSON parse error for {endpoint}: {e}")
                return None

            # HA API sometimes wraps responses in {"result": "ok", "data": [...]}
            if isinstance(data, dict) and "data" in data:
                data = data["data"]

            logger.debug(f"HARegistry response: {type(data)} with {len(data) if isinstance(data, list) else 'N/A'} items")
            return data

    def _get_websocket_url(self) -> str:
        """Convert REST API URL to WebSocket URL."""
        # api_url is like "http://supervisor/core/api"
        # WebSocket is at "ws://supervisor/core/websocket"
        ws_url = self.api_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url = ws_url.replace("/api", "/websocket")
        return ws_url

    async def _ws_fetch_registries(self) -> tuple[list, list, list, list]:
        """
        Fetch floor, area, entity, and device registries via WebSocket API.

        Returns:
            Tuple of (floors_data, areas_data, entities_data, devices_data)
        """
        if not WEBSOCKETS_AVAILABLE:
            logger.warning("WebSocket library not available")
            return [], [], [], []

        ws_url = self._get_websocket_url()
        logger.info(f"Connecting to HA WebSocket: {ws_url}")

        floors_data = []
        areas_data = []
        entities_data = []
        devices_data = []

        try:
            # Increase max message size to 64MB to handle very large entity registries
            async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as websocket:
                # Step 1: Receive auth_required message
                auth_required = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                auth_msg = json.loads(auth_required)
                if auth_msg.get("type") != "auth_required":
                    logger.error(f"Unexpected WebSocket message: {auth_msg}")
                    return [], [], []

                # Step 2: Send auth message
                await websocket.send(json.dumps({
                    "type": "auth",
                    "access_token": self.token
                }))

                # Step 3: Receive auth result
                auth_result = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                auth_result_msg = json.loads(auth_result)
                if auth_result_msg.get("type") != "auth_ok":
                    logger.error(f"WebSocket auth failed: {auth_result_msg}")
                    return [], [], []

                logger.info("  WebSocket authenticated successfully")

                # Step 4: Fetch floor registry
                await websocket.send(json.dumps({
                    "id": 1,
                    "type": "config/floor_registry/list"
                }))
                floors_response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                floors_msg = json.loads(floors_response)
                if floors_msg.get("success"):
                    floors_data = floors_msg.get("result", [])
                    logger.info(f"  WebSocket: Found {len(floors_data)} floors")

                # Step 5: Fetch area registry
                await websocket.send(json.dumps({
                    "id": 2,
                    "type": "config/area_registry/list"
                }))
                areas_response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                areas_msg = json.loads(areas_response)
                if areas_msg.get("success"):
                    areas_data = areas_msg.get("result", [])
                    logger.info(f"  WebSocket: Found {len(areas_data)} areas")

                # Step 6: Fetch entity registry (may be very large)
                try:
                    await websocket.send(json.dumps({
                        "id": 3,
                        "type": "config/entity_registry/list"
                    }))
                    # Longer timeout for large entity registries
                    entities_response = await asyncio.wait_for(websocket.recv(), timeout=30.0)
                    entities_msg = json.loads(entities_response)
                    if entities_msg.get("success"):
                        entities_data = entities_msg.get("result", [])
                        # Filter to media_player entities only
                        entities_data = [e for e in entities_data if e.get("entity_id", "").startswith("media_player.")]
                        logger.info(f"  WebSocket: Found {len(entities_data)} media_player entities")
                except Exception as entity_err:
                    logger.warning(f"  WebSocket: Could not fetch entity registry (large install?): {entity_err}")
                    logger.info("  WebSocket: Will try to match speakers to areas by name instead")

                # Step 7: Fetch device registry (for inherited area assignments)
                try:
                    await websocket.send(json.dumps({
                        "id": 4,
                        "type": "config/device_registry/list"
                    }))
                    devices_response = await asyncio.wait_for(websocket.recv(), timeout=10.0)
                    devices_msg = json.loads(devices_response)
                    if devices_msg.get("success"):
                        devices_data = devices_msg.get("result", [])
                        logger.info(f"  WebSocket: Found {len(devices_data)} devices")
                except Exception as device_err:
                    logger.warning(f"  WebSocket: Could not fetch device registry: {device_err}")

        except asyncio.TimeoutError:
            logger.error("WebSocket connection timed out")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")

        return floors_data, areas_data, entities_data, devices_data

    def _fetch_registries_via_websocket(self) -> tuple[dict[str, Floor], dict[str, Area], dict[str, dict], dict[str, dict]]:
        """
        Synchronous wrapper for WebSocket registry fetch.

        Handles the case where we're already inside an async event loop
        (e.g., during FastAPI startup) by running in a separate thread.

        Returns:
            Tuple of (floors_dict, areas_dict, entity_registry_dict, device_registry_dict)
        """
        import concurrent.futures

        floors = {}
        areas = {}
        entity_registry = {}
        device_registry = {}

        if not WEBSOCKETS_AVAILABLE:
            return floors, areas, entity_registry, device_registry

        def run_in_thread():
            """Run the async WebSocket fetch in a new thread with its own event loop."""
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self._ws_fetch_registries())
            finally:
                loop.close()

        try:
            # Run in a separate thread to avoid "event loop already running" error
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_thread)
                floors_data, areas_data, entities_data, devices_data = future.result(timeout=30)

            # Process floors
            for item in floors_data:
                floor = Floor(
                    floor_id=item.get("floor_id", ""),
                    name=item.get("name", ""),
                    # Handle explicit null from HA - .get() only defaults when key is missing
                    level=item.get("level") or 0,
                )
                floors[floor.floor_id] = floor

            # Process areas
            for item in areas_data:
                area = Area(
                    area_id=item.get("area_id", ""),
                    name=item.get("name", ""),
                    floor_id=item.get("floor_id"),
                )
                areas[area.area_id] = area

            # Process entity registry (keep as dict for area lookups)
            for item in entities_data:
                entity_id = item.get("entity_id", "")
                entity_registry[entity_id] = item

            # Process device registry (for inherited area lookups)
            # Map device_id -> device info (including area_id)
            for item in devices_data:
                device_id = item.get("id", "")
                if device_id:
                    device_registry[device_id] = item

        except concurrent.futures.TimeoutError:
            logger.error("WebSocket fetch timed out after 30 seconds")
        except Exception as e:
            logger.error(f"Failed to fetch registries via WebSocket: {e}")

        return floors, areas, entity_registry, device_registry

    def _fetch_floors(self) -> dict[str, Floor]:
        """Fetch floor registry."""
        floors = {}
        logger.info("Fetching floors from HA...")
        # Try the config API endpoint (may not be available via REST)
        data = self._get("/config/floor_registry")
        if data and isinstance(data, list):
            for item in data:
                floor = Floor(
                    floor_id=item.get("floor_id", ""),
                    name=item.get("name", ""),
                    # Handle explicit null from HA - .get() only defaults when key is missing
                    level=item.get("level") or 0,
                )
                floors[floor.floor_id] = floor
            logger.info(f"  Found {len(floors)} floors")
        else:
            logger.info("  Floor registry not available via REST API (this is normal - floors may need WebSocket API)")
        return floors
    
    def _fetch_areas(self) -> dict[str, Area]:
        """Fetch area registry."""
        areas = {}
        logger.info("Fetching areas from HA...")
        data = self._get("/config/area_registry")
        if data and isinstance(data, list):
            for item in data:
                area = Area(
                    area_id=item.get("area_id", ""),
                    name=item.get("name", ""),
                    floor_id=item.get("floor_id"),
                )
                areas[area.area_id] = area
            logger.info(f"  Found {len(areas)} areas")
        else:
            logger.info("  Area registry not available via REST API (this is normal - areas may need WebSocket API)")
        return areas
    
    def _fetch_entity_registry(self) -> dict[str, dict]:
        """Fetch entity registry for area assignments."""
        entity_map = {}
        logger.info("Fetching entity registry from HA...")
        data = self._get("/config/entity_registry")
        if data and isinstance(data, list):
            for entity in data:
                entity_id = entity.get("entity_id", "")
                if entity_id.startswith("media_player."):
                    entity_map[entity_id] = entity
            logger.info(f"  Found {len(entity_map)} media_player entities in registry")
        else:
            logger.info("  Entity registry not available via REST API (area assignments may be unavailable)")
        return entity_map
    
    def _match_speaker_to_area_by_name(self, speaker_name: str, areas: dict[str, Area]) -> Optional[str]:
        """
        Try to match a speaker to an area by name similarity.

        This is a fallback when entity registry isn't available.
        Looks for area names contained in the speaker's friendly name.
        Prefers longer (more specific) area name matches.
        """
        speaker_name_lower = speaker_name.lower()
        best_match = None
        best_match_len = 0

        # Try containment, preferring longer area names (more specific)
        # e.g., "Home Theater 2" should match "Home Theater" over "Theater" if both exist
        for area_id, area in areas.items():
            area_name_lower = area.name.lower()
            if area_name_lower in speaker_name_lower:
                if len(area_name_lower) > best_match_len:
                    best_match = area_id
                    best_match_len = len(area_name_lower)

        if best_match:
            logger.debug(f"  Name match: '{speaker_name}' -> area '{areas[best_match].name}'")
            return best_match

        # Fallback: Try word matching (e.g., "Kitchen" matches "Kitchen Sonos")
        speaker_words = set(speaker_name_lower.split())
        for area_id, area in areas.items():
            area_words = set(area.name.lower().split())
            # If ALL area words are in speaker name words (more precise)
            if area_words and area_words.issubset(speaker_words):
                logger.debug(f"  Word match: '{speaker_name}' -> area '{area.name}'")
                return area_id

        return None

    def _extract_ip_address(
        self,
        attributes: dict,
        entity_entry: dict,
        device_registry: dict[str, dict]
    ) -> Optional[str]:
        """
        Extract IP address from various HA data sources.

        Priority:
        1. State attributes (some integrations expose ip_address directly)
        2. Device configuration_url (parse IP from URL)
        3. Device connections (may contain IP addresses)

        Note: Not all integrations expose IP addresses. This is a best-effort
        extraction that depends on how each integration reports device info.
        """
        import re
        from urllib.parse import urlparse

        # Priority 1: Direct ip_address attribute
        ip = attributes.get("ip_address")
        if ip:
            return ip

        # Get device entry if available
        device_id = entity_entry.get("device_id")
        if not device_id or not device_registry:
            return None

        device = device_registry.get(device_id, {})
        if not device:
            return None

        # Priority 2: Parse IP from configuration_url
        config_url = device.get("configuration_url")
        if config_url:
            try:
                parsed = urlparse(config_url)
                host = parsed.hostname
                if host:
                    # Check if it's an IP address (not a hostname)
                    ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
                    if re.match(ip_pattern, host):
                        return host
            except Exception:
                pass

        # Priority 3: Check device connections
        # Connections is a list of [type, identifier] pairs
        # e.g., [["mac", "AA:BB:CC:DD:EE:FF"], ["ip", "192.168.1.100"]]
        connections = device.get("connections", [])
        for conn in connections:
            if isinstance(conn, (list, tuple)) and len(conn) >= 2:
                conn_type, conn_value = conn[0], conn[1]
                if conn_type == "ip":
                    return conn_value

        return None

    def _fetch_speakers(self, entity_registry: dict[str, dict] = None, device_registry: dict[str, dict] = None, areas: dict[str, Area] = None) -> dict[str, Speaker]:
        """
        Fetch media_player entities from states.

        Area assignment priority:
        1. Direct area_id on entity registry entry
        2. Inherited area_id from device (via device_id -> device.area_id)
        3. Name-based matching as fallback
        """
        speakers = {}
        entity_registry = entity_registry or {}
        device_registry = device_registry or {}
        areas = areas or {}

        try:
            logger.info("Fetching media players from HA states...")
            states = self._get("/states")

            if not isinstance(states, list):
                logger.error(f"  Unexpected states response type: {type(states)}")
                return speakers

            media_player_count = 0
            matched_by_name = 0
            matched_by_device = 0
            for state in states:
                entity_id = state.get("entity_id", "")
                if not entity_id.startswith("media_player."):
                    continue

                media_player_count += 1

                # Get friendly name from state attributes
                attributes = state.get("attributes", {})
                name = attributes.get("friendly_name", entity_id.replace("media_player.", "").replace("_", " ").title())

                # Get area from entity registry if available
                area_id = None
                entity_entry = entity_registry.get(entity_id, {})

                # Priority 1: Direct area_id on entity
                area_id = entity_entry.get("area_id")

                # Priority 2: Inherited area_id from device
                if not area_id and device_registry:
                    device_id = entity_entry.get("device_id")
                    if device_id and device_id in device_registry:
                        device = device_registry[device_id]
                        area_id = device.get("area_id")
                        if area_id:
                            matched_by_device += 1
                            logger.debug(f"  Device area: '{name}' -> device '{device.get('name', device_id)}' -> area_id '{area_id}'")

                # Priority 3: Fallback to name matching
                if not area_id and areas:
                    area_id = self._match_speaker_to_area_by_name(name, areas)
                    if area_id:
                        matched_by_name += 1

                # Try to extract IP address from various sources
                ip_address = self._extract_ip_address(attributes, entity_entry, device_registry)

                speaker = Speaker(
                    entity_id=entity_id,
                    name=name,
                    area_id=area_id,
                    ip_address=ip_address,
                )
                speakers[entity_id] = speaker

            logger.info(f"  Found {len(speakers)} media players (from {media_player_count} total)")
            if matched_by_device > 0:
                logger.info(f"  Matched {matched_by_device} speakers to areas via device inheritance")
            if matched_by_name > 0:
                logger.info(f"  Matched {matched_by_name} speakers to areas by name")

        except Exception as e:
            logger.error(f"  Failed to fetch media players from states: {e}")
            import traceback
            traceback.print_exc()

        return speakers
    
    def refresh(self) -> SpeakerHierarchy:
        """
        Refresh all data from HA and rebuild hierarchy.
        Call this to update after HA configuration changes.

        Tries WebSocket API first (required for floor/area/entity/device registries),
        falls back to REST API for states.
        """
        logger.info("Building speaker hierarchy from Home Assistant...")

        # Try WebSocket API first for registries (floors, areas, entity registry, device registry)
        ws_floors, ws_areas, ws_entity_registry, ws_device_registry = self._fetch_registries_via_websocket()

        device_registry = {}
        if ws_floors or ws_areas or ws_entity_registry:
            logger.info("  Using WebSocket API data for hierarchy")
            self._floors = ws_floors
            self._areas = ws_areas
            entity_registry = ws_entity_registry
            device_registry = ws_device_registry
        else:
            # Fall back to REST API (will likely fail for registries, but try anyway)
            logger.info("  WebSocket unavailable, trying REST API fallback...")
            self._floors = self._fetch_floors()
            self._areas = self._fetch_areas()
            entity_registry = self._fetch_entity_registry()
            # Note: device registry not available via REST API fallback

        # Always fetch speakers from states (REST API works for this)
        # Pass device_registry for inherited area lookups, areas for name-based matching fallback
        self._speakers = self._fetch_speakers(entity_registry, device_registry, self._areas)
        
        # Build hierarchy
        hierarchy = SpeakerHierarchy()
        
        # Link areas to floors, and track floor names
        for area in self._areas.values():
            if area.floor_id and area.floor_id in self._floors:
                floor = self._floors[area.floor_id]
                area.floor_name = floor.name
        
        # Link speakers to areas, and track area/floor names
        linked_count = 0
        for speaker in self._speakers.values():
            if speaker.area_id and speaker.area_id in self._areas:
                area = self._areas[speaker.area_id]
                speaker.area_name = area.name
                speaker.floor_id = area.floor_id
                speaker.floor_name = area.floor_name
                area.speakers.append(speaker)
                linked_count += 1
            else:
                if speaker.area_id:
                    logger.debug(f"  Speaker '{speaker.name}' has area_id '{speaker.area_id}' but area not found in self._areas")
                # Speaker has no area assignment
                hierarchy.unassigned_speakers.append(speaker)

        if linked_count > 0:
            logger.info(f"  Linked {linked_count} speakers to areas")
        elif self._areas:
            logger.warning(f"  No speakers linked to areas! Area keys sample: {list(self._areas.keys())[:5]}")
        
        # Sort unassigned speakers by name
        hierarchy.unassigned_speakers.sort(key=lambda s: s.name)
        
        # Build floor list with their areas
        for floor in sorted(self._floors.values(), key=lambda f: f.level):
            floor.areas = []
            for area in self._areas.values():
                if area.floor_id == floor.floor_id:
                    floor.areas.append(area)
            floor.areas.sort(key=lambda a: a.name)
            hierarchy.floors.append(floor)
        
        # Collect areas with no floor
        for area in self._areas.values():
            if not area.floor_id:
                hierarchy.unassigned_areas.append(area)
        hierarchy.unassigned_areas.sort(key=lambda a: a.name)
        
        self._hierarchy = hierarchy

        total_speakers = len(hierarchy.get_all_speakers())
        logger.info(f"  Hierarchy complete: {len(hierarchy.floors)} floors, {len(hierarchy.unassigned_areas)} unassigned areas, {len(hierarchy.unassigned_speakers)} unassigned speakers, {total_speakers} total speakers")

        return hierarchy

    def apply_custom_areas(self, custom_areas: dict[str, list[str]]) -> SpeakerHierarchy:
        """
        Apply custom speaker area assignments to the hierarchy.

        This creates "custom" areas for speakers that aren't assigned to HA areas,
        useful as a fallback when WebSocket is unavailable.

        Args:
            custom_areas: Dict of {"Area Name": ["media_player.entity1", ...]}

        Returns:
            Updated hierarchy with custom areas applied
        """
        if not custom_areas:
            return self._hierarchy

        hierarchy = self._hierarchy
        if not hierarchy:
            return hierarchy

        # Build a set of speakers in custom areas
        speakers_in_custom = set()
        for speaker_ids in custom_areas.values():
            speakers_in_custom.update(speaker_ids)

        # Create custom areas and move speakers from unassigned
        for area_name, speaker_ids in custom_areas.items():
            custom_area = Area(
                area_id=f"custom_{area_name.lower().replace(' ', '_')}",
                name=area_name,
                floor_id=None,
                floor_name=None,
            )

            # Find and move speakers to this custom area
            for entity_id in speaker_ids:
                # Check if speaker is in unassigned_speakers
                for i, speaker in enumerate(hierarchy.unassigned_speakers):
                    if speaker.entity_id == entity_id:
                        speaker.area_id = custom_area.area_id
                        speaker.area_name = custom_area.name
                        custom_area.speakers.append(speaker)
                        hierarchy.unassigned_speakers.pop(i)
                        break

            if custom_area.speakers:
                hierarchy.unassigned_areas.append(custom_area)

        # Re-sort unassigned areas
        hierarchy.unassigned_areas.sort(key=lambda a: a.name)

        return hierarchy

    @property
    def hierarchy(self) -> SpeakerHierarchy:
        """Get cached hierarchy (returns empty if not loaded)."""
        if self._hierarchy is None:
            # Return empty hierarchy instead of blocking on refresh
            # User must click "Refresh from HA" button to load speakers
            logger.warning("HARegistry: hierarchy not loaded yet - returning empty")
            return SpeakerHierarchy()
        return self._hierarchy
    
    # Convenience methods for lookups
    
    def get_floor(self, floor_id: str) -> Optional[Floor]:
        """Get floor by ID."""
        return self._floors.get(floor_id)
    
    def get_floor_name(self, floor_id: str) -> str:
        """Get floor name by ID, or ID if not found."""
        floor = self._floors.get(floor_id)
        return floor.name if floor else floor_id
    
    def get_area(self, area_id: str) -> Optional[Area]:
        """Get area by ID."""
        return self._areas.get(area_id)
    
    def get_area_name(self, area_id: str) -> str:
        """Get area name by ID, or ID if not found."""
        area = self._areas.get(area_id)
        return area.name if area else area_id
    
    def get_speaker(self, entity_id: str) -> Optional[Speaker]:
        """Get speaker by entity ID."""
        return self._speakers.get(entity_id)
    
    def get_speaker_name(self, entity_id: str) -> str:
        """Get speaker friendly name by entity ID."""
        speaker = self._speakers.get(entity_id)
        return speaker.name if speaker else entity_id
    
    def get_speakers_on_floor(self, floor_id: str) -> list[str]:
        """Get all speaker entity_ids on a floor."""
        speakers = []
        for area in self._areas.values():
            if area.floor_id == floor_id:
                for speaker in area.speakers:
                    speakers.append(speaker.entity_id)
        return speakers
    
    def get_speakers_in_area(self, area_id: str) -> list[str]:
        """Get all speaker entity_ids in an area."""
        area = self._areas.get(area_id)
        if not area:
            return []
        return [s.entity_id for s in area.speakers]
    
    def get_hierarchy_dict(self) -> dict:
        """Get hierarchy as a dictionary for API responses."""
        return self.hierarchy.to_dict()

    def get_all_speaker_ids(self) -> list[str]:
        """Get all speaker entity IDs as a flat list."""
        return [s.entity_id for s in self.hierarchy.get_all_speakers()]

    def resolve_selection(self,
                          include_floors: list[str] = None,
                          include_areas: list[str] = None,
                          include_speakers: list[str] = None,
                          exclude_areas: list[str] = None,
                          exclude_speakers: list[str] = None) -> list[str]:
        """
        Resolve a speaker selection to a final list of entity_ids.
        
        1. Start with empty set
        2. Add all speakers from included floors
        3. Add all speakers from included areas
        4. Add individually included speakers
        5. Remove all speakers from excluded areas
        6. Remove individually excluded speakers
        7. Return sorted list
        """
        speakers = set()
        
        # Additions
        for floor_id in (include_floors or []):
            speakers.update(self.get_speakers_on_floor(floor_id))
        
        for area_id in (include_areas or []):
            speakers.update(self.get_speakers_in_area(area_id))
        
        speakers.update(include_speakers or [])
        
        # Exclusions
        for area_id in (exclude_areas or []):
            speakers -= set(self.get_speakers_in_area(area_id))
        
        speakers -= set(exclude_speakers or [])
        
        return sorted(list(speakers))


# Factory function to create registry from supervisor
def create_registry_from_supervisor() -> HARegistry:
    """
    Create HARegistry using supervisor API.
    For use within Home Assistant addons.
    """
    from sonorium.settings import settings
    
    return HARegistry(
        api_url=f"{settings.ha_supervisor_api.replace('/core', '')}/core/api",
        token=settings.token,
    )
