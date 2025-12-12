"""
Speaker Group Manager

Handles CRUD operations for saved speaker group configurations.
"""

from __future__ import annotations

import uuid
from typing import Optional, TYPE_CHECKING

from sonorium.core.state import (
    SpeakerGroup,
    SpeakerSelection,
    StateStore,
)
from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.ha.registry import HARegistry


class GroupManager:
    """
    Manages saved speaker group configurations.
    
    Speaker groups are reusable speaker selections that can be
    referenced by multiple sessions.
    """
    
    def __init__(self, state_store: StateStore, ha_registry: HARegistry):
        self.state = state_store
        self.registry = ha_registry
    
    # --- CRUD Operations ---
    
    @logger.instrument("Creating speaker group '{name}'...")
    def create(
        self,
        name: str,
        include_floors: list[str] = None,
        include_areas: list[str] = None,
        include_speakers: list[str] = None,
        exclude_areas: list[str] = None,
        exclude_speakers: list[str] = None,
        icon: str = "mdi:speaker-group",
    ) -> SpeakerGroup:
        """
        Create a new speaker group.
        
        Args:
            name: Display name for the group
            include_floors: Floor IDs to include
            include_areas: Area IDs to include
            include_speakers: Speaker entity_ids to include
            exclude_areas: Area IDs to exclude
            exclude_speakers: Speaker entity_ids to exclude
            icon: MDI icon name
        
        Returns:
            Created speaker group
        
        Raises:
            ValueError: If max groups exceeded or name already exists
        """
        # Check limits
        max_groups = self.state.settings.max_groups
        if len(self.state.speaker_groups) >= max_groups:
            raise ValueError(f"Maximum of {max_groups} speaker groups allowed")
        
        # Check for duplicate name
        for group in self.state.speaker_groups.values():
            if group.name.lower() == name.lower():
                raise ValueError(f"Speaker group '{name}' already exists")
        
        # Generate ID
        group_id = str(uuid.uuid4())[:8]
        
        # Create group
        group = SpeakerGroup(
            id=group_id,
            name=name,
            icon=icon,
            include_floors=include_floors or [],
            include_areas=include_areas or [],
            include_speakers=include_speakers or [],
            exclude_areas=exclude_areas or [],
            exclude_speakers=exclude_speakers or [],
        )
        
        # Store and save
        self.state.speaker_groups[group_id] = group
        self.state.save()
        
        resolved = self.resolve(group)
        logger.info(f"  Created group with {len(resolved)} speakers")
        return group
    
    def create_from_selection(
        self,
        name: str,
        selection: SpeakerSelection,
        icon: str = "mdi:speaker-group",
    ) -> SpeakerGroup:
        """
        Create a speaker group from an ad-hoc selection.
        
        Convenience method for "Save as Group" functionality.
        """
        return self.create(
            name=name,
            include_floors=selection.include_floors,
            include_areas=selection.include_areas,
            include_speakers=selection.include_speakers,
            exclude_areas=selection.exclude_areas,
            exclude_speakers=selection.exclude_speakers,
            icon=icon,
        )
    
    def get(self, group_id: str) -> Optional[SpeakerGroup]:
        """Get a speaker group by ID."""
        return self.state.speaker_groups.get(group_id)
    
    def get_by_name(self, name: str) -> Optional[SpeakerGroup]:
        """Get a speaker group by name (case-insensitive)."""
        for group in self.state.speaker_groups.values():
            if group.name.lower() == name.lower():
                return group
        return None
    
    def list(self) -> list[SpeakerGroup]:
        """List all speaker groups, sorted by name."""
        groups = list(self.state.speaker_groups.values())
        groups.sort(key=lambda g: g.name.lower())
        return groups
    
    @logger.instrument("Updating speaker group {group_id}...")
    def update(
        self,
        group_id: str,
        name: str = None,
        include_floors: list[str] = None,
        include_areas: list[str] = None,
        include_speakers: list[str] = None,
        exclude_areas: list[str] = None,
        exclude_speakers: list[str] = None,
        icon: str = None,
    ) -> Optional[SpeakerGroup]:
        """
        Update an existing speaker group.
        
        Only provided fields are updated.
        
        Returns:
            Updated group, or None if not found
        """
        group = self.state.speaker_groups.get(group_id)
        if not group:
            logger.warning(f"  Group {group_id} not found")
            return None
        
        # Check for duplicate name if changing
        if name and name.lower() != group.name.lower():
            for other in self.state.speaker_groups.values():
                if other.id != group_id and other.name.lower() == name.lower():
                    raise ValueError(f"Speaker group '{name}' already exists")
        
        # Update fields if provided
        if name is not None:
            group.name = name
        if icon is not None:
            group.icon = icon
        if include_floors is not None:
            group.include_floors = include_floors
        if include_areas is not None:
            group.include_areas = include_areas
        if include_speakers is not None:
            group.include_speakers = include_speakers
        if exclude_areas is not None:
            group.exclude_areas = exclude_areas
        if exclude_speakers is not None:
            group.exclude_speakers = exclude_speakers
        
        group.touch()
        self.state.save()
        
        logger.info(f"  Updated group '{group.name}'")
        return group
    
    @logger.instrument("Deleting speaker group {group_id}...")
    def delete(self, group_id: str) -> bool:
        """
        Delete a speaker group.
        
        Note: Sessions using this group will need to be updated.
        
        Returns:
            True if deleted, False if not found
        """
        if group_id not in self.state.speaker_groups:
            logger.warning(f"  Group {group_id} not found")
            return False
        
        group = self.state.speaker_groups.pop(group_id)
        self.state.save()
        
        logger.info(f"  Deleted group '{group.name}'")
        return True
    
    # --- Resolution ---
    
    def resolve(self, group: SpeakerGroup) -> list[str]:
        """
        Resolve a speaker group to a list of speaker entity_ids.
        """
        return self.registry.resolve_selection(
            include_floors=group.include_floors,
            include_areas=group.include_areas,
            include_speakers=group.include_speakers,
            exclude_areas=group.exclude_areas,
            exclude_speakers=group.exclude_speakers,
        )
    
    def get_summary(self, group: SpeakerGroup) -> str:
        """
        Get a human-readable summary of a speaker group.
        
        Examples:
        - "Bedroom Level"
        - "Bedroom Level, excluding: Master Echo"
        - "Kitchen, Living Room"
        - "3 individual speakers"
        """
        parts = []
        
        # Floors
        if group.include_floors:
            floor_names = [self.registry.get_floor_name(f) for f in group.include_floors]
            parts.append(", ".join(floor_names))
        
        # Areas
        if group.include_areas:
            area_names = [self.registry.get_area_name(a) for a in group.include_areas]
            parts.append(", ".join(area_names))
        
        # Individual speakers
        if group.include_speakers and not group.include_floors and not group.include_areas:
            if len(group.include_speakers) == 1:
                parts.append(self.registry.get_speaker_name(group.include_speakers[0]))
            else:
                parts.append(f"{len(group.include_speakers)} individual speakers")
        
        summary = "; ".join(parts) if parts else "No speakers"
        
        # Add exclusions
        exclusions = []
        if group.exclude_areas:
            area_names = [self.registry.get_area_name(a) for a in group.exclude_areas]
            exclusions.extend(area_names)
        if group.exclude_speakers:
            speaker_names = [self.registry.get_speaker_name(s) for s in group.exclude_speakers]
            exclusions.extend(speaker_names)
        
        if exclusions:
            summary += f", excluding: {', '.join(exclusions)}"
        
        return summary
    
    def get_speaker_count(self, group: SpeakerGroup) -> int:
        """Get the number of resolved speakers in a group."""
        return len(self.resolve(group))
    
    # --- Validation ---
    
    def get_sessions_using_group(self, group_id: str) -> list[str]:
        """
        Get session IDs that use a speaker group.
        
        Useful for warning before deletion.
        """
        session_ids = []
        for session in self.state.sessions.values():
            if session.speaker_group_id == group_id:
                session_ids.append(session.id)
        return session_ids
