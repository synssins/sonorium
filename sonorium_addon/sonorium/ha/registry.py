"""
Home Assistant Registry Integration

Queries HA's floor, area, and device registries to build
the speaker hierarchy for Sonorium's speaker selection UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from fmtr.tools import http
from sonorium.obs import logger


@dataclass
class Speaker:
    """A media player entity that can play audio."""
    entity_id: str
    name: str
    area_id: Optional[str] = None
    area_name: Optional[str] = None
    floor_id: Optional[str] = None
    floor_name: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "name": self.name,
            "area_id": self.area_id,
            "area_name": self.area_name,
            "floor_id": self.floor_id,
            "floor_name": self.floor_name,
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
    
    def _get(self, endpoint: str) -> dict | list:
        """Make GET request to HA API."""
        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        logger.debug(f"HARegistry GET: {url}")
        with http.Client() as client:
            response = client.get(url, headers=self.headers)
            data = response.json()
            logger.debug(f"HARegistry response: {type(data)} with {len(data) if isinstance(data, list) else 'N/A'} items")
            return data
    
    def _fetch_floors(self) -> dict[str, Floor]:
        """Fetch floor registry."""
        floors = {}
        try:
            logger.info("Fetching floors from HA...")
            # Try the config API endpoint
            data = self._get("/config/floor_registry")
            if isinstance(data, list):
                for item in data:
                    floor = Floor(
                        floor_id=item.get("floor_id", ""),
                        name=item.get("name", ""),
                        level=item.get("level", 0),
                    )
                    floors[floor.floor_id] = floor
            logger.info(f"  Found {len(floors)} floors")
        except Exception as e:
            logger.warning(f"  Could not fetch floors (this is OK if floors aren't configured): {e}")
        return floors
    
    def _fetch_areas(self) -> dict[str, Area]:
        """Fetch area registry."""
        areas = {}
        try:
            logger.info("Fetching areas from HA...")
            data = self._get("/config/area_registry")
            if isinstance(data, list):
                for item in data:
                    area = Area(
                        area_id=item.get("area_id", ""),
                        name=item.get("name", ""),
                        floor_id=item.get("floor_id"),
                    )
                    areas[area.area_id] = area
            logger.info(f"  Found {len(areas)} areas")
        except Exception as e:
            logger.warning(f"  Could not fetch areas (this is OK if areas aren't configured): {e}")
        return areas
    
    def _fetch_entity_registry(self) -> dict[str, dict]:
        """Fetch entity registry for area assignments."""
        entity_map = {}
        try:
            logger.info("Fetching entity registry from HA...")
            data = self._get("/config/entity_registry")
            if isinstance(data, list):
                for entity in data:
                    entity_id = entity.get("entity_id", "")
                    if entity_id.startswith("media_player."):
                        entity_map[entity_id] = entity
                logger.info(f"  Found {len(entity_map)} media_player entities in registry")
        except Exception as e:
            logger.warning(f"  Could not fetch entity registry: {e}")
        return entity_map
    
    def _fetch_speakers(self, entity_registry: dict[str, dict] = None) -> dict[str, Speaker]:
        """Fetch media_player entities from states."""
        speakers = {}
        entity_registry = entity_registry or {}
        
        try:
            logger.info("Fetching media players from HA states...")
            states = self._get("/states")
            
            if not isinstance(states, list):
                logger.error(f"  Unexpected states response type: {type(states)}")
                return speakers
            
            media_player_count = 0
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
                if entity_id in entity_registry:
                    area_id = entity_registry[entity_id].get("area_id")
                
                speaker = Speaker(
                    entity_id=entity_id,
                    name=name,
                    area_id=area_id,
                )
                speakers[entity_id] = speaker
            
            logger.info(f"  Found {len(speakers)} media players (from {media_player_count} total)")
            
        except Exception as e:
            logger.error(f"  Failed to fetch media players from states: {e}")
            import traceback
            traceback.print_exc()
        
        return speakers
    
    def refresh(self) -> SpeakerHierarchy:
        """
        Refresh all data from HA and rebuild hierarchy.
        Call this to update after HA configuration changes.
        """
        logger.info("Building speaker hierarchy from Home Assistant...")
        
        # Fetch all data
        self._floors = self._fetch_floors()
        self._areas = self._fetch_areas()
        entity_registry = self._fetch_entity_registry()
        self._speakers = self._fetch_speakers(entity_registry)
        
        # Build hierarchy
        hierarchy = SpeakerHierarchy()
        
        # Link areas to floors, and track floor names
        for area in self._areas.values():
            if area.floor_id and area.floor_id in self._floors:
                floor = self._floors[area.floor_id]
                area.floor_name = floor.name
        
        # Link speakers to areas, and track area/floor names
        for speaker in self._speakers.values():
            if speaker.area_id and speaker.area_id in self._areas:
                area = self._areas[speaker.area_id]
                speaker.area_name = area.name
                speaker.floor_id = area.floor_id
                speaker.floor_name = area.floor_name
                area.speakers.append(speaker)
            else:
                # Speaker has no area assignment
                hierarchy.unassigned_speakers.append(speaker)
        
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
    
    @property
    def hierarchy(self) -> SpeakerHierarchy:
        """Get cached hierarchy, refreshing if needed."""
        if self._hierarchy is None:
            self.refresh()
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
