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
class CycleConfig:
    """
    Theme cycling configuration for a session.
    
    When enabled, the session will automatically rotate through themes
    at the specified interval.
    """
    
    enabled: bool = False
    interval_minutes: int = 60  # How often to change themes
    randomize: bool = False     # True = random order, False = sequential
    
    # Optional: specific themes to cycle through (empty = all themes)
    theme_ids: list[str] = field(default_factory=list)
    
    # Runtime state (not persisted)
    current_index: int = 0      # Current position in theme list
    last_change: Optional[str] = None  # ISO timestamp of last theme change
    
    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "interval_minutes": self.interval_minutes,
            "randomize": self.randomize,
            "theme_ids": self.theme_ids,
            # Don't persist runtime state
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> CycleConfig:
        if data is None:
            return cls()
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class SonoriumSettings:
    """Global settings for Sonorium."""

    default_volume: int = 60
    crossfade_duration: float = 3.0
    max_groups: int = 20
    entity_prefix: str = "sonorium"
    show_in_sidebar: bool = True
    auto_create_quick_play: bool = True

    # Master output gain (0-100, applied to all streams)
    master_gain: int = 60

    # Default cycling settings (applied to new sessions)
    default_cycle_interval: int = 60  # minutes
    default_cycle_randomize: bool = False

    # Speaker availability - only these speakers are visible/targetable in Sonorium
    # Empty list = all speakers enabled (backwards compatibility)
    enabled_speakers: list[str] = field(default_factory=list)

    # Favorite themes (by theme ID)
    favorite_themes: list[str] = field(default_factory=list)

    # Theme categories (user-defined groupings)
    # List of category names that have been created
    theme_categories: list[str] = field(default_factory=list)

    # Theme to categories mapping
    # Format: {"theme_id": ["Weather", "Nature"]}
    theme_category_assignments: dict[str, list[str]] = field(default_factory=dict)

    # Manual speaker area assignments (fallback when HA areas unavailable)
    # Format: {"area_name": ["media_player.entity1", "media_player.entity2"]}
    custom_speaker_areas: dict[str, list[str]] = field(default_factory=dict)

    # Per-track presence settings for themes (how often track plays in mix)
    # Format: {"theme_id": {"track_name": 0.5, "track_name2": 1.0}}
    # Presence: 1.0 = always playing, 0.5 = plays ~50% of time, 0.0 = never plays
    track_presence: dict[str, dict[str, float]] = field(default_factory=dict)

    # Per-track mute/enabled settings for themes
    # Format: {"theme_id": {"track_name": false}} - only stores disabled tracks
    track_muted: dict[str, dict[str, bool]] = field(default_factory=dict)

    # Per-track volume settings (amplitude control, independent of presence)
    # Format: {"theme_id": {"track_name": 0.8}} - 0.0 to 1.0, default 1.0
    track_volume: dict[str, dict[str, float]] = field(default_factory=dict)

    # Per-track playback mode settings
    # Format: {"theme_id": {"track_name": "sparse"}} - auto/continuous/sparse/presence
    track_playback_mode: dict[str, dict[str, str]] = field(default_factory=dict)

    # Per-track seamless loop settings (disable crossfade)
    # Format: {"theme_id": {"track_name": true}} - only stores tracks with seamless enabled
    track_seamless_loop: dict[str, dict[str, bool]] = field(default_factory=dict)

    # Per-track exclusive playback settings (mutual exclusion group)
    # Format: {"theme_id": {"track_name": true}} - only stores tracks with exclusive enabled
    # Tracks marked exclusive will not play simultaneously - only one can play at a time
    track_exclusive: dict[str, dict[str, bool]] = field(default_factory=dict)

    # Plugin settings (keyed by plugin_id)
    # Format: {"ambient_mixer": {"download_path": "/media/sonorium", "auto_create_metadata": true}}
    plugin_settings: dict[str, dict] = field(default_factory=dict)

    # Enabled plugins list (by plugin_id)
    enabled_plugins: list[str] = field(default_factory=list)

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
    preset_id: Optional[str] = None  # Theme preset to apply

    # Where to play - either a saved group OR ad-hoc selection
    speaker_group_id: Optional[str] = None
    adhoc_selection: Optional[SpeakerSelection] = None

    # Playback state
    volume: int = 60
    is_playing: bool = False

    # Theme cycling configuration
    cycle_config: CycleConfig = field(default_factory=CycleConfig)

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
        
        # Convert cycle_config from dict if needed
        if isinstance(self.cycle_config, dict):
            self.cycle_config = CycleConfig.from_dict(self.cycle_config)
    
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
        # Handle cycle_config separately to avoid nested asdict issues
        data['cycle_config'] = self.cycle_config.to_dict() if self.cycle_config else None
        return data
    
    @classmethod
    def from_dict(cls, data: dict) -> Session:
        # Handle nested objects
        if 'adhoc_selection' in data and data['adhoc_selection'] is not None:
            if isinstance(data['adhoc_selection'], dict):
                data['adhoc_selection'] = SpeakerSelection.from_dict(data['adhoc_selection'])
        if 'cycle_config' in data and data['cycle_config'] is not None:
            if isinstance(data['cycle_config'], dict):
                data['cycle_config'] = CycleConfig.from_dict(data['cycle_config'])
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

            # Reset all sessions to stopped state on startup
            # (playback doesn't survive addon restarts)
            for session in self.state.sessions.values():
                if session.is_playing:
                    session.is_playing = False
                    logger.info(f"  Reset session '{session.name}' to stopped state")

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