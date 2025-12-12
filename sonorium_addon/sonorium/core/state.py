"""
Sonorium State Management

Handles persistence of sessions, speaker groups, and settings to JSON.
State is stored in /config/sonorium/state.json to survive addon updates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from enum import Enum

from sonorium.obs import logger


# Default paths
DEFAULT_STATE_DIR = Path("/config/sonorium")
DEFAULT_STATE_FILE = DEFAULT_STATE_DIR / "state.json"


class NameSource(str, Enum):
    """How a session name was determined."""
    AUTO_FLOOR = "auto_floor"      # Named after selected floor
    AUTO_AREA = "auto_area"        # Named after selected area(s)
    AUTO_GROUP = "auto_group"      # Named after speaker group
    CUSTOM = "custom"              # User-defined name


@dataclass
class SonoriumSettings:
    """Global settings for Sonorium."""
    
    default_volume: int = 60
    crossfade_duration: float = 3.0
    max_sessions: int = 10
    max_groups: int = 20
    entity_prefix: str = "sonorium"
    show_in_sidebar: bool = True
    auto_create_quick_play: bool = True
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> SonoriumSettings:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SpeakerSelection:
    """
    Inline speaker selection (not saved as a group).
    Used for ad-hoc selections in sessions.
    """
    
    # Additive selections (union of all)
    include_floors: list[str] = field(default_factory=list)
    include_areas: list[str] = field(default_factory=list)
    include_speakers: list[str] = field(default_factory=list)
    
    # Subtractive exclusions
    exclude_areas: list[str] = field(default_factory=list)
    exclude_speakers: list[str] = field(default_factory=list)
    
    def is_empty(self) -> bool:
        """Check if no speakers are selected."""
        return (
            not self.include_floors and 
            not self.include_areas and 
            not self.include_speakers
        )
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> SpeakerSelection:
        if data is None:
            return None
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SpeakerGroup:
    """
    A saved speaker selection configuration.
    Can be reused across multiple sessions.
    """
    
    id: str
    name: str
    icon: str = "mdi:speaker-group"
    
    # Additive selections (union of all)
    include_floors: list[str] = field(default_factory=list)
    include_areas: list[str] = field(default_factory=list)
    include_speakers: list[str] = field(default_factory=list)
    
    # Subtractive exclusions
    exclude_areas: list[str] = field(default_factory=list)
    exclude_speakers: list[str] = field(default_factory=list)
    
    created_at: str = ""  # ISO format
    updated_at: str = ""  # ISO format
    
    def __post_init__(self):
        now = datetime.utcnow().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
    
    def touch(self):
        """Update the updated_at timestamp."""
        self.updated_at = datetime.utcnow().isoformat()
    
    def to_selection(self) -> SpeakerSelection:
        """Convert to a SpeakerSelection for resolution."""
        return SpeakerSelection(
            include_floors=self.include_floors,
            include_areas=self.include_areas,
            include_speakers=self.include_speakers,
            exclude_areas=self.exclude_areas,
            exclude_speakers=self.exclude_speakers,
        )
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> SpeakerGroup:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Session:
    """
    An active or configured playback session.
    Each session plays one theme to one speaker group/selection.
    """
    
    id: str
    name: str
    name_source: NameSource = NameSource.AUTO_AREA
    
    # What to play
    theme_id: Optional[str] = None
    
    # Where to play - either a saved group OR ad-hoc selection
    speaker_group_id: Optional[str] = None
    adhoc_selection: Optional[SpeakerSelection] = None
    
    # Playback state
    volume: int = 60
    is_playing: bool = False
    
    # Metadata
    created_at: str = ""  # ISO format
    last_played_at: Optional[str] = None  # ISO format
    
    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()
        
        # Convert name_source from string if needed
        if isinstance(self.name_source, str):
            self.name_source = NameSource(self.name_source)
        
        # Convert adhoc_selection from dict if needed
        if isinstance(self.adhoc_selection, dict):
            self.adhoc_selection = SpeakerSelection.from_dict(self.adhoc_selection)
    
    def get_entity_slug(self) -> str:
        """Generate HA entity slug from name."""
        # "Bedroom Level" -> "bedroom_level"
        # "Night Mode Speakers" -> "night_mode_speakers"
        slug = self.name.lower()
        slug = slug.replace(" ", "_")
        slug = slug.replace("-", "_")
        # Remove non-alphanumeric except underscore
        slug = "".join(c for c in slug if c.isalnum() or c == "_")
        # Collapse multiple underscores
        while "__" in slug:
            slug = slug.replace("__", "_")
        return slug.strip("_")
    
    def mark_played(self):
        """Update last_played_at timestamp."""
        self.last_played_at = datetime.utcnow().isoformat()
    
    def to_dict(self) -> dict:
        data = asdict(self)
        data['name_source'] = self.name_source.value
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> Session:
        # Handle nested objects
        if 'adhoc_selection' in data and data['adhoc_selection'] is not None:
            if isinstance(data['adhoc_selection'], dict):
                data['adhoc_selection'] = SpeakerSelection.from_dict(data['adhoc_selection'])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SonoriumState:
    """
    Complete persisted state for Sonorium.
    Saved to /config/sonorium/state.json
    """
    
    settings: SonoriumSettings = field(default_factory=SonoriumSettings)
    speaker_groups: dict[str, SpeakerGroup] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    
    # Version for future migrations
    version: int = 1
    
    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "settings": self.settings.to_dict(),
            "speaker_groups": {k: v.to_dict() for k, v in self.speaker_groups.items()},
            "sessions": {k: v.to_dict() for k, v in self.sessions.items()},
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> SonoriumState:
        state = cls()
        state.version = data.get("version", 1)
        
        if "settings" in data:
            state.settings = SonoriumSettings.from_dict(data["settings"])
        
        if "speaker_groups" in data:
            for k, v in data["speaker_groups"].items():
                state.speaker_groups[k] = SpeakerGroup.from_dict(v)
        
        if "sessions" in data:
            for k, v in data["sessions"].items():
                state.sessions[k] = Session.from_dict(v)
        
        return state


class StateStore:
    """
    Manages loading and saving of Sonorium state.
    """
    
    def __init__(self, state_file: Path = DEFAULT_STATE_FILE):
        self.state_file = state_file
        self.state: SonoriumState = SonoriumState()
    
    @logger.instrument("Loading state from {self.state_file}...")
    def load(self) -> SonoriumState:
        """Load state from disk, or create default if not exists."""
        if not self.state_file.exists():
            logger.info("  No existing state file, using defaults")
            self.state = SonoriumState()
            return self.state
        
        try:
            data = json.loads(self.state_file.read_text())
            self.state = SonoriumState.from_dict(data)
            logger.info(f"  Loaded {len(self.state.sessions)} sessions, {len(self.state.speaker_groups)} groups")
        except Exception as e:
            logger.error(f"  Failed to load state: {e}")
            self.state = SonoriumState()
        
        return self.state
    
    @logger.instrument("Saving state to {self.state_file}...")
    def save(self):
        """Persist state to disk."""
        try:
            # Ensure directory exists
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            
            # Write with pretty formatting
            data = self.state.to_dict()
            self.state_file.write_text(json.dumps(data, indent=2))
            
            logger.info(f"  Saved {len(self.state.sessions)} sessions, {len(self.state.speaker_groups)} groups")
        except Exception as e:
            logger.error(f"  Failed to save state: {e}")
            raise
    
    # Convenience accessors
    @property
    def settings(self) -> SonoriumSettings:
        return self.state.settings
    
    @property
    def sessions(self) -> dict[str, Session]:
        return self.state.sessions
    
    @property
    def speaker_groups(self) -> dict[str, SpeakerGroup]:
        return self.state.speaker_groups
