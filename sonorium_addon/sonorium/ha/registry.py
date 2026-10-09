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
from dataclasses import dataclass, field, replace
from typing import Callable, Optional

from fmtr.tools import http
from sonorium.obs import logger

# Try to import websockets, fall back gracefully if not available
try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets library not available - floor/area hierarchy will be limited")


# Speaker types shown in the UI, from the Home Assistant integration (entity
# registry "platform") that provides the media player
HA_PLATFORM_TYPES = {
    "cast": "cast",
    "sonos": "sonos",
    "dlna_dmr": "dlna",
    "linkplay": "linkplay",
    "wiim": "linkplay",
    "heos": "heos",
    "denonavr": "denon",  # Denon and Marantz AV receivers
    "esphome": "esphome",
    "apple_tv": "airplay",
    "airplay": "airplay",
}

# Home Assistant states that mean the speaker can't be reached
OFFLINE_STATES = ("unavailable", "unknown")


def speaker_type_for_platform(platform: Optional[str]) -> str:
    """UI speaker type for a Home Assistant integration name ("other" if not a known one)."""
    return HA_PLATFORM_TYPES.get((platform or "").lower(), "other")


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
    # Where Sonorium knows the speaker from: "ha", "discovered", "manual"
    source: list[str] = field(default_factory=lambda: ["ha"])
    # cast, sonos, dlna, airplay, linkplay, heos, esphome, other
    type: str = "other"
    online: bool = True
    # IP/host, or the HA entity ID when the IP isn't known
    address: Optional[str] = None
    # Per-speaker settings applied (see core/speaker_settings.py)
    original_name: Optional[str] = None
    default_area_id: Optional[str] = None  # the HA area, before any room override
    volume_offset: int = 0
    # Network speakers found at the same IP as this HA speaker, shown as one
    # speaker: [{"id": "net:dlna:x", "type": "dlna", "source": "discovered"}]
    merged: list[dict] = field(default_factory=list)
    play_via: str = "ha"

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "floor_id": self.floor_id,
            "floor_name": self.floor_name,
            "ip_address": self.ip_address,
            "source": list(self.source),
            "type": self.type,
            "online": self.online,
            "address": self.address or self.ip_address or self.entity_id,
            "original_name": self.original_name or self.name,
            "default_area_id": self.default_area_id,
            "volume_offset": self.volume_offset,
            "merged": [dict(m) for m in self.merged],
            "play_via": self.play_via,
        }


@dataclass
class Area:
    """A Home Assistant area (room)."""
    area_id: str
    name: str
    floor_id: Optional[str] = None
    floor_name: Optional[str] = None
    speakers: list[Speaker] = field(default_factory=list)
    # "ha", "local" (made in Sonorium), or both when the same name is in each
    source: list[str] = field(default_factory=lambda: ["ha"])
    # Sonorium's own ID when it merged into a Home Assistant area of the same name
    local_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "area_id": self.area_id,
            "name": self.name,
            "floor_id": self.floor_id,
            "floor_name": self.floor_name,
            "speakers": [s.to_dict() for s in self.speakers],
            "source": list(self.source),
            "local_id": self.local_id,
        }


@dataclass
class Floor:
    """A Home Assistant floor (level of home)."""
    floor_id: str
    name: str
    level: int = 0
    areas: list[Area] = field(default_factory=list)
    source: list[str] = field(default_factory=lambda: ["ha"])
    local_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "floor_id": self.floor_id,
            "name": self.name,
            "level": self.level,
            "areas": [a.to_dict() for a in self.areas],
            "source": list(self.source),
            "local_id": self.local_id,
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

        # Speakers from Home Assistant as fetched (before per-speaker settings)
        self._ha_speakers: dict[str, Speaker] = {}

        # Speakers from outside Home Assistant (standalone network speakers).
        # None in the HA add-on.
        self._extra_speaker_source: Optional[Callable[[], list[dict]]] = None

        # Per-speaker settings (name, room, volume_offset, play_via) by speaker ID
        self._settings_source: Optional[Callable[[], dict]] = None

        # Network speaker ID -> HA speaker ID it was merged into (same IP)
        self._merged_into: dict[str, str] = {}

        # Floors and areas from Home Assistant as fetched, and Sonorium's own
        # (Settings > Floors & Areas). self._floors/_areas hold both, merged.
        self._ha_floors: dict[str, Floor] = {}
        self._ha_areas: dict[str, Area] = {}
        self._local_spaces_source: Optional[Callable[[], dict]] = None
        # Sonorium floor/area ID -> Home Assistant ID it merged into (same name)
        self._space_alias: dict[str, str] = {}

    def set_extra_speaker_source(self, source: Optional[Callable[[], list[dict]]]):
        """
        Add speakers that don't come from Home Assistant. `source` returns
        dicts with entity_id, name, ip_address and optionally type, online and
        source (list); it's read on every refresh() and merge_extra_speakers().
        """
        self._extra_speaker_source = source

    def set_local_spaces_source(self, source: Optional[Callable[[], dict]]):
        """Sonorium's own floors and areas ({"floors": [...], "areas": [...]}), read on every rebuild."""
        self._local_spaces_source = source

    def resolve_space_id(self, space_id: Optional[str]) -> Optional[str]:
        """The ID a floor or area is listed under (a Sonorium one may have merged into HA's)."""
        return self._space_alias.get(space_id, space_id) if space_id else space_id

    def _read_local_spaces(self) -> dict:
        if self._local_spaces_source is None:
            return {}
        try:
            return self._local_spaces_source() or {}
        except Exception as e:
            logger.warning(f"Could not read floors and areas: {e}")
            return {}

    def _merge_spaces(self) -> None:
        """
        Home Assistant's floors and areas plus Sonorium's own. One with the same
        name (ignoring case) as Home Assistant's becomes that one, keeping HA's
        name; anything that pointed at the Sonorium ID follows it.
        """
        local = self._read_local_spaces()
        floors = {fid: replace(f, areas=[], source=["ha"], local_id=None) for fid, f in self._ha_floors.items()}
        areas = {aid: replace(a, speakers=[], source=["ha"], local_id=None) for aid, a in self._ha_areas.items()}
        alias: dict[str, str] = {}

        floor_by_name = {f.name.casefold(): f for f in floors.values()}
        next_level = max((f.level for f in floors.values()), default=-1) + 1
        for item in local.get("floors") or []:
            fid, name = item.get("id"), (item.get("name") or "").strip()
            if not fid or not name:
                continue
            match = floor_by_name.get(name.casefold())
            if match is not None:
                match.source.append("local")
                match.local_id = fid
                alias[fid] = match.floor_id
                continue
            floors[fid] = Floor(floor_id=fid, name=name, level=next_level, source=["local"])
            floor_by_name[name.casefold()] = floors[fid]
            next_level += 1

        area_by_name = {a.name.casefold(): a for a in areas.values()}
        for item in local.get("areas") or []:
            aid, name = item.get("id"), (item.get("name") or "").strip()
            if not aid or not name:
                continue
            match = area_by_name.get(name.casefold())
            if match is not None:
                match.source.append("local")
                match.local_id = aid
                alias[aid] = match.area_id
                if not match.floor_id and item.get("floor_id"):
                    match.floor_id = alias.get(item["floor_id"], item["floor_id"])
                continue
            floor_id = item.get("floor_id")
            areas[aid] = Area(area_id=aid, name=name, floor_id=alias.get(floor_id, floor_id), source=["local"])
            area_by_name[name.casefold()] = areas[aid]

        self._floors, self._areas, self._space_alias = floors, areas, alias

    def set_speaker_settings_source(self, source: Optional[Callable[[], dict]]):
        """Per-speaker settings by speaker ID (see core/speaker_settings.py), read on every rebuild."""
        self._settings_source = source

    def merge_extra_speakers(self) -> SpeakerHierarchy:
        """Re-read the extra speakers into the hierarchy (no Home Assistant calls)."""
        return self._rebuild()

    def apply_speaker_settings(self) -> SpeakerHierarchy:
        """Rebuild the hierarchy after per-speaker settings changed (no Home Assistant calls)."""
        return self._rebuild()

    def _read_extras(self) -> list[dict]:
        if self._extra_speaker_source is None:
            return []
        try:
            return list(self._extra_speaker_source())
        except Exception as e:
            logger.warning(f"Could not list network speakers: {e}")
            return []

    def _read_settings(self) -> dict:
        if self._settings_source is None:
            return {}
        try:
            return self._settings_source() or {}
        except Exception as e:
            logger.warning(f"Could not read speaker settings: {e}")
            return {}

    def _rebuild(self) -> SpeakerHierarchy:
        """
        Build the hierarchy from the Home Assistant speakers and areas last
        fetched, the extra (network) speakers and the per-speaker settings:
        - a network speaker at the same IP as an HA speaker is merged into it
          (one speaker, both sources; play_via picks the path);
        - name overrides rename, room overrides move speakers between areas
          (network speakers can be placed in HA areas too).
        """
        settings = self._read_settings()
        self._merge_spaces()

        speakers: dict[str, Speaker] = {}
        for base in self._ha_speakers.values():
            speakers[base.entity_id] = replace(
                base, source=list(base.source), merged=[], area_name=None, floor_id=None, floor_name=None,
            )

        by_ip = {s.ip_address: s for s in speakers.values() if s.ip_address}
        self._merged_into = {}
        for item in self._read_extras():
            entity_id = item.get("entity_id")
            if not entity_id or entity_id in speakers:
                continue
            sources = list(item.get("source") or ["discovered"])
            ip = item.get("ip_address")
            target = by_ip.get(ip) if ip else None
            if target is not None:
                for src in sources:
                    if src not in target.source:
                        target.source.append(src)
                target.online = target.online or bool(item.get("online", True))
                target.merged.append({"id": entity_id, "type": item.get("type") or "other", "source": sources[0]})
                self._merged_into[entity_id] = target.entity_id
                continue
            speakers[entity_id] = Speaker(
                entity_id=entity_id,
                name=item.get("name") or entity_id,
                ip_address=ip,
                source=sources,
                type=item.get("type") or "other",
                online=bool(item.get("online", True)),
                address=ip or entity_id,
            )

        for speaker in speakers.values():
            overrides = settings.get(speaker.entity_id) or {}
            speaker.original_name = speaker.name
            if overrides.get("name"):
                speaker.name = overrides["name"]
            speaker.default_area_id = speaker.area_id
            if "room" in overrides:
                speaker.area_id = self.resolve_space_id(overrides["room"]) or None
            speaker.volume_offset = int(overrides.get("volume_offset", 0) or 0)
            play_via = overrides.get("play_via")
            speaker.play_via = play_via if any(m["id"] == play_via for m in speaker.merged) else "ha"

        self._speakers = speakers
        self._hierarchy = self._link(speakers)
        return self._hierarchy

    def _link(self, speakers: dict[str, Speaker]) -> SpeakerHierarchy:
        """Place speakers in their areas and areas on their floors."""
        hierarchy = SpeakerHierarchy()

        for area in self._areas.values():
            area.speakers = []
            floor = self._floors.get(area.floor_id) if area.floor_id else None
            area.floor_name = floor.name if floor else None

        linked_count = 0
        for speaker in speakers.values():
            area = self._areas.get(speaker.area_id) if speaker.area_id else None
            if area is not None:
                speaker.area_name = area.name
                speaker.floor_id = area.floor_id if area.floor_id in self._floors else None
                speaker.floor_name = area.floor_name
                area.speakers.append(speaker)
                linked_count += 1
            else:
                if speaker.area_id:
                    logger.debug(f"  Speaker '{speaker.name}' has area_id '{speaker.area_id}' but area not found")
                    speaker.area_id = None
                hierarchy.unassigned_speakers.append(speaker)

        if linked_count > 0:
            logger.debug(f"  Linked {linked_count} speakers to areas")
        elif self._areas and speakers:
            logger.warning(f"  No speakers linked to areas! Area keys sample: {list(self._areas.keys())[:5]}")

        for area in self._areas.values():
            area.speakers.sort(key=lambda s: s.name.lower())
        hierarchy.unassigned_speakers.sort(key=lambda s: s.name.lower())

        for floor in sorted(self._floors.values(), key=lambda f: f.level):
            floor.areas = sorted(
                (a for a in self._areas.values() if a.floor_id == floor.floor_id), key=lambda a: a.name
            )
            hierarchy.floors.append(floor)

        hierarchy.unassigned_areas = sorted(
            (a for a in self._areas.values() if not a.floor_id or a.floor_id not in self._floors),
            key=lambda a: a.name,
        )
        return hierarchy

    def get_play_target(self, speaker_id: str) -> str:
        """
        The ID to play a speaker through: a merged speaker whose play_via
        names one of its network speakers plays through that one.
        """
        speaker = self._speakers.get(speaker_id)
        if speaker is not None and speaker.play_via != "ha":
            return speaker.play_via
        return speaker_id

    def get_merged_owner(self, network_id: str) -> Optional[str]:
        """The HA speaker a network speaker was merged into, if any."""
        return self._merged_into.get(network_id)

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
        from sonorium.runtime import ha_websocket_url
        return ha_websocket_url(self.api_url)

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
        logger.debug(f"Connecting to HA WebSocket: {ws_url}")

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

                logger.debug("  WebSocket authenticated successfully")

                # Step 4: Fetch floor registry
                await websocket.send(json.dumps({
                    "id": 1,
                    "type": "config/floor_registry/list"
                }))
                floors_response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                floors_msg = json.loads(floors_response)
                if floors_msg.get("success"):
                    floors_data = floors_msg.get("result", [])
                    logger.debug(f"  WebSocket: Found {len(floors_data)} floors")

                # Step 5: Fetch area registry
                await websocket.send(json.dumps({
                    "id": 2,
                    "type": "config/area_registry/list"
                }))
                areas_response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                areas_msg = json.loads(areas_response)
                if areas_msg.get("success"):
                    areas_data = areas_msg.get("result", [])
                    logger.debug(f"  WebSocket: Found {len(areas_data)} areas")

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
                        logger.debug(f"  WebSocket: Found {len(entities_data)} media_player entities")
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
                        logger.debug(f"  WebSocket: Found {len(devices_data)} devices")
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
            logger.debug(f"  Found {len(floors)} floors")
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
            logger.debug(f"  Found {len(areas)} areas")
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
            logger.debug(f"  Found {len(entity_map)} media_player entities in registry")
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
            logger.debug("Fetching media players from HA states...")
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
                    source=["ha"],
                    type=speaker_type_for_platform(entity_entry.get("platform")),
                    online=state.get("state") not in OFFLINE_STATES,
                    address=ip_address or entity_id,
                )
                speakers[entity_id] = speaker

            logger.debug(f"  Found {len(speakers)} media players (from {media_player_count} total)")
            if matched_by_device > 0:
                logger.debug(f"  Matched {matched_by_device} speakers to areas via device inheritance")
            if matched_by_name > 0:
                logger.debug(f"  Matched {matched_by_name} speakers to areas by name")

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
        from sonorium.runtime import ha_configured
        if not ha_configured():
            # Standalone without Home Assistant: no HA speakers to load
            self._ha_floors, self._ha_areas, self._ha_speakers = {}, {}, {}
            return self._rebuild()

        logger.debug("Building speaker hierarchy from Home Assistant...")

        # Try WebSocket API first for registries (floors, areas, entity registry, device registry)
        ws_floors, ws_areas, ws_entity_registry, ws_device_registry = self._fetch_registries_via_websocket()

        device_registry = {}
        if ws_floors or ws_areas or ws_entity_registry:
            logger.debug("  Using WebSocket API data for hierarchy")
            self._ha_floors = ws_floors
            self._ha_areas = ws_areas
            entity_registry = ws_entity_registry
            device_registry = ws_device_registry
        else:
            # Fall back to REST API (will likely fail for registries, but try anyway)
            logger.info("  WebSocket unavailable, trying REST API fallback...")
            self._ha_floors = self._fetch_floors()
            self._ha_areas = self._fetch_areas()
            entity_registry = self._fetch_entity_registry()
            # Note: device registry not available via REST API fallback

        # Always fetch speakers from states (REST API works for this)
        # Pass device_registry for inherited area lookups, areas for name-based matching fallback
        self._ha_speakers = self._fetch_speakers(entity_registry, device_registry, self._ha_areas)

        # Build hierarchy (adds network speakers and per-speaker settings)
        hierarchy = self._rebuild()

        total_speakers = len(hierarchy.get_all_speakers())
        logger.debug(f"  Hierarchy complete: {len(hierarchy.floors)} floors, {len(hierarchy.unassigned_areas)} unassigned areas, {len(hierarchy.unassigned_speakers)} unassigned speakers, {total_speakers} total speakers")

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
            speakers.update(self.get_speakers_on_floor(self.resolve_space_id(floor_id)))

        for area_id in (include_areas or []):
            speakers.update(self.get_speakers_in_area(self.resolve_space_id(area_id)))
        
        speakers.update(include_speakers or [])
        
        # Exclusions
        for area_id in (exclude_areas or []):
            speakers -= set(self.get_speakers_in_area(self.resolve_space_id(area_id)))
        
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
        api_url=settings.ha_core_api,
        token=settings.token,
    )
