"""
Theme Metadata Management

All theme-specific data is stored in the theme's metadata.json file.
This makes themes portable - renaming folders or moving themes preserves all settings.

The theme_id in metadata.json is the canonical identifier. Folder names are just
filesystem paths that Sonorium discovers and maps to the persistent theme_id.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from sonorium.obs import logger


@dataclass
class TrackSettings:
    """Per-track settings stored in metadata.json."""
    presence: float = 1.0           # 0.0-1.0, how often track plays
    muted: bool = False
    volume: float = 1.0             # 0.0-1.0, amplitude
    playback_mode: str = "auto"     # auto/continuous/sparse/presence
    seamless_loop: bool = False
    exclusive: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> TrackSettings:
        if data is None:
            return cls()
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ThemeMetadata:
    """
    Complete theme metadata stored in metadata.json.

    The 'id' field is the canonical, persistent identifier for the theme.
    All other parts of Sonorium reference themes by this ID.
    """

    # Persistent unique identifier (generated once, never changes)
    id: str = ""

    # Display name (can be changed without affecting ID)
    name: str = ""

    # User-editable metadata
    description: str = ""
    icon: str = ""  # Emoji or empty for auto-detect

    # Organization
    is_favorite: bool = False
    categories: list[str] = field(default_factory=list)

    # Audio settings
    short_file_threshold: float = 15.0

    # Per-track settings (keyed by filename)
    tracks: dict[str, TrackSettings] = field(default_factory=dict)

    # Presets (existing functionality)
    presets: dict[str, dict] = field(default_factory=dict)

    # Attribution info (for imported themes)
    attribution: Optional[dict] = None

    def __post_init__(self):
        # Generate ID if not present
        if not self.id:
            self.id = str(uuid.uuid4())

        # Convert track dicts to TrackSettings objects
        if self.tracks:
            for track_name, settings in self.tracks.items():
                if isinstance(settings, dict):
                    self.tracks[track_name] = TrackSettings.from_dict(settings)

    def get_track_settings(self, track_name: str) -> TrackSettings:
        """Get settings for a track, creating defaults if not exists."""
        if track_name not in self.tracks:
            self.tracks[track_name] = TrackSettings()
        return self.tracks[track_name]

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "is_favorite": self.is_favorite,
            "categories": self.categories,
            "short_file_threshold": self.short_file_threshold,
            "tracks": {k: v.to_dict() if isinstance(v, TrackSettings) else v
                      for k, v in self.tracks.items()},
            "presets": self.presets,
        }
        if self.attribution:
            data["attribution"] = self.attribution
        return data

    @classmethod
    def from_dict(cls, data: dict) -> ThemeMetadata:
        """Create from dict, handling nested objects."""
        if data is None:
            return cls()

        # Extract known fields
        kwargs = {}
        for key in ['id', 'name', 'description', 'icon', 'is_favorite',
                    'categories', 'short_file_threshold', 'presets', 'attribution']:
            if key in data:
                kwargs[key] = data[key]

        # Handle tracks specially
        if 'tracks' in data and data['tracks']:
            kwargs['tracks'] = {
                k: TrackSettings.from_dict(v) if isinstance(v, dict) else v
                for k, v in data['tracks'].items()
            }

        return cls(**kwargs)


class ThemeMetadataManager:
    """
    Manages theme metadata across the audio directory.

    Maintains a mapping of theme_id -> folder_path based on metadata.json files.
    """

    def __init__(self, audio_path: Path):
        self.audio_path = audio_path

        # theme_id -> folder_path mapping
        self._id_to_folder: dict[str, Path] = {}

        # folder_path -> ThemeMetadata cache
        self._metadata_cache: dict[Path, ThemeMetadata] = {}

    def scan_themes(self) -> dict[str, ThemeMetadata]:
        """
        Scan audio directory and build theme_id -> metadata mapping.

        Returns dict of theme_id -> ThemeMetadata for all valid themes.
        """
        self._id_to_folder.clear()
        self._metadata_cache.clear()

        if not self.audio_path.exists():
            logger.warning(f"Audio path does not exist: {self.audio_path}")
            return {}

        themes = {}

        for folder in self.audio_path.iterdir():
            if not folder.is_dir():
                continue

            # Check for audio files
            audio_files = [f for f in folder.iterdir()
                         if f.is_file() and f.suffix.lower() in ['.mp3', '.wav', '.flac', '.ogg']]

            if not audio_files:
                logger.debug(f"Skipping folder with no audio: {folder.name}")
                continue

            # Load or create metadata
            metadata = self._load_or_create_metadata(folder)

            # Store mappings
            self._id_to_folder[metadata.id] = folder
            self._metadata_cache[folder] = metadata
            themes[metadata.id] = metadata

            logger.debug(f"Loaded theme '{metadata.name}' (id={metadata.id[:8]}...) from {folder.name}")

        return themes

    def _load_or_create_metadata(self, folder: Path) -> ThemeMetadata:
        """Load metadata.json or create with defaults if not exists."""
        metadata_path = folder / "metadata.json"

        if metadata_path.exists():
            try:
                data = json.loads(metadata_path.read_text(encoding='utf-8'))
                metadata = ThemeMetadata.from_dict(data)

                # Ensure name is set (use folder name as fallback)
                if not metadata.name:
                    metadata.name = folder.name
                    self._save_metadata(folder, metadata)

                return metadata
            except Exception as e:
                logger.error(f"Failed to load metadata from {metadata_path}: {e}")

        # Create new metadata with folder name as default name
        metadata = ThemeMetadata(
            name=folder.name
        )

        # Save immediately so ID is persisted
        self._save_metadata(folder, metadata)
        logger.debug(f"Created new metadata for theme '{folder.name}' with id={metadata.id[:8]}...")

        return metadata

    def _save_metadata(self, folder: Path, metadata: ThemeMetadata) -> bool:
        """Save metadata to folder's metadata.json."""
        metadata_path = folder / "metadata.json"

        try:
            metadata_path.write_text(
                json.dumps(metadata.to_dict(), indent=2, ensure_ascii=False),
                encoding='utf-8'
            )
            return True
        except Exception as e:
            logger.error(f"Failed to save metadata to {metadata_path}: {e}")
            return False

    def get_folder_for_id(self, theme_id: str) -> Optional[Path]:
        """Get the folder path for a theme ID."""
        return self._id_to_folder.get(theme_id)

    def get_metadata(self, theme_id: str) -> Optional[ThemeMetadata]:
        """Get metadata for a theme by ID."""
        folder = self._id_to_folder.get(theme_id)
        if folder:
            return self._metadata_cache.get(folder)
        return None

    def get_metadata_by_folder(self, folder: Path) -> Optional[ThemeMetadata]:
        """Get metadata for a theme by folder path."""
        return self._metadata_cache.get(folder)

    def save_metadata(self, theme_id: str, metadata: ThemeMetadata) -> bool:
        """Save updated metadata for a theme."""
        folder = self._id_to_folder.get(theme_id)
        if not folder:
            logger.error(f"Cannot save metadata: unknown theme_id '{theme_id}'")
            return False

        if self._save_metadata(folder, metadata):
            self._metadata_cache[folder] = metadata
            return True
        return False

    def all_categories(self) -> list[str]:
        """Every category used by a theme, in first-seen order."""
        found: list[str] = []
        for metadata in self._metadata_cache.values():
            for category in metadata.categories or []:
                if category not in found:
                    found.append(category)
        return found

    def remove_category(self, category: str) -> int:
        """Take a category off every theme that has it; returns how many changed."""
        changed = 0
        for folder, metadata in self._metadata_cache.items():
            if category in (metadata.categories or []):
                metadata.categories = [c for c in metadata.categories if c != category]
                if self._save_metadata(folder, metadata):
                    changed += 1
        return changed

    def update_metadata(self, theme_id: str, **updates) -> Optional[ThemeMetadata]:
        """Update specific fields in theme metadata."""
        metadata = self.get_metadata(theme_id)
        if not metadata:
            return None

        for key, value in updates.items():
            if hasattr(metadata, key):
                setattr(metadata, key, value)

        if self.save_metadata(theme_id, metadata):
            return metadata
        return None

    def update_track_settings(self, theme_id: str, track_name: str,
                             **settings) -> Optional[TrackSettings]:
        """Update settings for a specific track."""
        metadata = self.get_metadata(theme_id)
        if not metadata:
            return None

        track_settings = metadata.get_track_settings(track_name)

        for key, value in settings.items():
            if hasattr(track_settings, key):
                setattr(track_settings, key, value)

        if self.save_metadata(theme_id, metadata):
            return track_settings
        return None

    def migrate_from_state(self, theme_id: str, state_settings: dict) -> bool:
        """
        Migrate theme data from state.json to metadata.json.

        Called during upgrade to move favorites, categories, track settings
        from the global state to per-theme metadata.
        """
        metadata = self.get_metadata(theme_id)
        if not metadata:
            logger.warning(f"Cannot migrate: theme '{theme_id}' not found")
            return False

        changed = False

        # Migrate favorite status
        if 'is_favorite' in state_settings:
            metadata.is_favorite = state_settings['is_favorite']
            changed = True

        # Migrate categories
        if 'categories' in state_settings:
            metadata.categories = state_settings['categories']
            changed = True

        # Migrate track settings
        track_fields = ['track_presence', 'track_muted', 'track_volume',
                       'track_playback_mode', 'track_seamless_loop', 'track_exclusive']

        field_to_attr = {
            'track_presence': 'presence',
            'track_muted': 'muted',
            'track_volume': 'volume',
            'track_playback_mode': 'playback_mode',
            'track_seamless_loop': 'seamless_loop',
            'track_exclusive': 'exclusive',
        }

        for field_name in track_fields:
            if field_name in state_settings:
                attr_name = field_to_attr[field_name]
                for track_name, value in state_settings[field_name].items():
                    track_settings = metadata.get_track_settings(track_name)
                    setattr(track_settings, attr_name, value)
                    changed = True

        if changed:
            return self.save_metadata(theme_id, metadata)

        return True
