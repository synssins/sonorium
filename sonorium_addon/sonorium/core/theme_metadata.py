"""
Theme Metadata Management

All theme-specific data is stored in the theme folder: metadata.json for the
theme and its tracks, presets.json for presets (Themes 2.0, see theme_presets.py).
This makes themes portable - renaming folders or moving themes preserves all settings.

Loading a folder converts a 1.0 theme (presets inside metadata.json) to 2.0 and
recovers from broken JSON files; see load_theme_folder().

The theme_id in metadata.json is the canonical identifier. Folder names are just
filesystem paths that Sonorium discovers and maps to the persistent theme_id.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from sonorium.core import theme_presets
from sonorium.core.theme_presets import (
    METADATA_FILE,
    PRESETS_FILE,
    SPEC_VERSION,
    BrokenJsonError,
)
from sonorium.obs import logger

AUDIO_EXTENSIONS = ('.mp3', '.wav', '.flac', '.ogg')

# Name of the group that 1.0 "exclusive" tracks belong to after conversion
LEGACY_EXCLUSIVE_GROUP = "Exclusive"

# Theme folders already warned about being read-only (warn once per theme)
_warned_read_only: set[str] = set()


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

    # Presets. Kept in memory here but stored in presets.json, not metadata.json.
    presets: dict[str, dict] = field(default_factory=dict)

    # Attribution info (for imported themes)
    attribution: Optional[dict] = None

    # Theme file format version (1 = presets inside metadata.json, 2 = presets.json)
    spec_version: int = SPEC_VERSION

    # Group settings keyed by group name. Only stored for now. A 1.0 theme with
    # exclusive tracks gets {"Exclusive": {"legacy_exclusive": True}}; the tracks
    # keep their own exclusive flag, so playback is unchanged.
    groups: dict[str, dict] = field(default_factory=dict)

    # Problems found while loading (e.g. a broken file), for the UI. Not saved.
    problems: list[str] = field(default_factory=list, compare=False)

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
        """Convert to the metadata.json dict (presets are stored in presets.json)."""
        data = {
            "spec_version": self.spec_version,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "is_favorite": self.is_favorite,
            "categories": self.categories,
            "short_file_threshold": self.short_file_threshold,
            "tracks": {k: v.to_dict() if isinstance(v, TrackSettings) else v
                      for k, v in self.tracks.items()},
            "groups": self.groups,
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
                    'categories', 'short_file_threshold', 'presets', 'attribution',
                    'spec_version', 'groups']:
            if key in data:
                kwargs[key] = data[key]

        # Handle tracks specially
        if 'tracks' in data and data['tracks']:
            kwargs['tracks'] = {
                k: TrackSettings.from_dict(v) if isinstance(v, dict) else v
                for k, v in data['tracks'].items()
            }

        return cls(**kwargs)


# --- Theme folder load / save (Themes 2.0) ------------------------------------

PRE_PRESETS_BACKUP = METADATA_FILE + ".pre-presets.bak"


def _default_metadata(folder: Path, theme_id: Optional[str] = None) -> ThemeMetadata:
    """Fresh metadata for a folder: its audio files with default track settings."""
    tracks = {}
    try:
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                tracks[f.name] = TrackSettings()
    except OSError:
        pass
    return ThemeMetadata(id=theme_id or "", name=folder.name, tracks=tracks)


def _is_legacy(raw: dict) -> bool:
    """True for a 1.0 metadata.json (no spec_version, or below 2)."""
    version = raw.get("spec_version")
    return not isinstance(version, int) or version < SPEC_VERSION


def _convert_fields(metadata: ThemeMetadata) -> None:
    """
    1.0 -> 2.0 changes inside metadata.json. Tracks marked exclusive are
    recorded as members of an "Exclusive" group; their flag is kept as is,
    so playback doesn't change.
    """
    metadata.spec_version = SPEC_VERSION
    if any(getattr(t, "exclusive", False) for t in metadata.tracks.values()):
        metadata.groups.setdefault(LEGACY_EXCLUSIVE_GROUP, {"legacy_exclusive": True})


def _note_broken(folder: Path, error: BrokenJsonError, problems: list[str], rename: bool = True) -> None:
    """Keep a broken file aside, log it and record it for the UI."""
    kept = theme_presets.keep_broken(error.path) if rename else None
    kept_as = f", kept as {kept.name}" if kept else ""
    logger.warning(f"Theme '{folder.name}': can't read {error}{kept_as}. Using defaults")
    problems.append(f"Couldn't read {error}; defaults were used")


def _warn_read_only(folder: Path, error: Exception) -> None:
    key = str(folder)
    if key in _warned_read_only:
        return
    _warned_read_only.add(key)
    logger.warning(f"Theme '{folder.name}': can't write to the folder, using its files as they are ({error})")


def _read_metadata(folder: Path, problems: list[str], rename_broken: bool):
    """(metadata, raw dict or None). A broken file gives rebuilt defaults."""
    path = folder / METADATA_FILE
    try:
        raw = theme_presets.read_json(path)
        if raw is None:
            return None, None
        try:
            return ThemeMetadata.from_dict(raw), raw
        except (TypeError, ValueError, AttributeError) as e:
            raise BrokenJsonError(path, f"unexpected content ({e})") from e
    except BrokenJsonError as e:
        theme_id = theme_presets.recover_theme_id(path)
        _note_broken(folder, e, problems, rename_broken)
        return _default_metadata(folder, theme_id), None


def _read_presets_doc(folder: Path, problems: list[str], rename_broken: bool):
    """(presets.json document, was_broken)."""
    try:
        return theme_presets.load_presets_doc(folder), False
    except BrokenJsonError as e:
        _note_broken(folder, e, problems, rename_broken)
        return {}, True


def _save_presets_verified(folder: Path, presets: dict) -> None:
    """Save presets.json and check it reads back with the same presets. Raises OSError."""
    theme_presets.save_presets(folder, presets)
    try:
        saved = theme_presets.load_presets_doc(folder).get("presets")
    except BrokenJsonError as e:
        raise OSError(str(e)) from e
    if saved != presets:
        raise OSError(f"{PRESETS_FILE} did not read back the same")


def load_theme_folder(folder: Path) -> ThemeMetadata:
    """
    Load a theme folder's metadata and presets, converting a 1.0 theme to 2.0.

    Conversion (idempotent):
      - presets in metadata.json move to presets.json (presets.json wins on the
        same id), presets.json is verified, metadata.json is backed up once to
        metadata.json.pre-presets.bak, then "presets" is removed from it
      - spec_version becomes 2; exclusive tracks are recorded in an "Exclusive" group
    A broken metadata.json/presets.json is kept as <name>.broken-<time> and
    rebuilt with defaults; the problem is listed in metadata.problems.
    If the folder can't be written, the files are used as they are (presets
    still read from metadata.json) and a warning is logged once.
    """
    folder = Path(folder)
    problems: list[str] = []
    meta_path = folder / METADATA_FILE

    metadata, raw = _read_metadata(folder, problems, rename_broken=True)
    is_new = metadata is None
    if is_new:
        metadata = ThemeMetadata(name=folder.name)

    legacy_presets = {}
    has_legacy_key = raw is not None and "presets" in raw
    if has_legacy_key and isinstance(raw["presets"], dict):
        legacy_presets = dict(raw["presets"])
    legacy = raw is not None and _is_legacy(raw)

    doc, presets_broken = _read_presets_doc(folder, problems, rename_broken=True)
    presets = dict(legacy_presets)
    presets.update(doc.get("presets") or {})
    metadata.presets = presets

    if legacy:
        _convert_fields(metadata)

    write_meta = raw is None or legacy or has_legacy_key or "id" not in raw
    if not metadata.name:
        metadata.name = folder.name
        write_meta = True
    write_presets = has_legacy_key or presets_broken or not (folder / PRESETS_FILE).exists()

    try:
        if write_presets:
            _save_presets_verified(folder, presets)
        if has_legacy_key and meta_path.exists() and not (folder / PRE_PRESETS_BACKUP).exists():
            shutil.copy2(meta_path, folder / PRE_PRESETS_BACKUP)
        if write_meta:
            theme_presets.write_json_atomic(meta_path, metadata.to_dict())
    except OSError as e:
        _warn_read_only(folder, e)
    else:
        if legacy or has_legacy_key:
            logger.info(f"Theme '{metadata.name}' converted to the 2.0 format")
        elif is_new:
            logger.debug(f"Created new metadata for theme '{folder.name}' with id={metadata.id[:8]}...")

    metadata.problems = problems
    return metadata


def save_theme_folder(folder: Path, metadata: ThemeMetadata) -> bool:
    """Save metadata.json and presets.json (presets first, so none are lost). Returns True on success."""
    folder = Path(folder)
    try:
        try:
            current = theme_presets.load_presets_doc(folder).get("presets")
        except BrokenJsonError:
            current = None
        if current != metadata.presets or not (folder / PRESETS_FILE).exists():
            theme_presets.save_presets(folder, metadata.presets)
        theme_presets.write_json_atomic(folder / METADATA_FILE, metadata.to_dict())
        return True
    except OSError as e:
        logger.error(f"Failed to save theme files in {folder}: {e}")
        return False


def theme_documents_for_export(folder: Path) -> tuple[dict, dict]:
    """(metadata.json dict, presets.json dict) in the 2.0 layout, without writing anything."""
    folder = Path(folder)
    problems: list[str] = []
    metadata, raw = _read_metadata(folder, problems, rename_broken=False)
    if metadata is None:
        metadata = ThemeMetadata(name=folder.name)
    if raw is not None and _is_legacy(raw):
        _convert_fields(metadata)
    legacy_presets = raw.get("presets") if raw is not None else None
    doc, _ = _read_presets_doc(folder, problems, rename_broken=False)
    doc = dict(doc)
    presets = dict(legacy_presets) if isinstance(legacy_presets, dict) else {}
    presets.update(doc.get("presets") or {})
    doc["presets"] = presets
    return metadata.to_dict(), doc


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
        """Load metadata.json and presets.json (converting 1.0 themes), or create defaults."""
        try:
            return load_theme_folder(folder)
        except Exception as e:
            # One bad theme must never stop the others from loading
            logger.error(f"Theme '{folder.name}': failed to load ({e})")
            metadata = _default_metadata(folder, theme_presets.recover_theme_id(folder / METADATA_FILE))
            metadata.problems = [f"Couldn't load the theme files: {e}"]
            return metadata

    def _save_metadata(self, folder: Path, metadata: ThemeMetadata) -> bool:
        """Save metadata to folder's metadata.json and its presets to presets.json."""
        return save_theme_folder(folder, metadata)

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
