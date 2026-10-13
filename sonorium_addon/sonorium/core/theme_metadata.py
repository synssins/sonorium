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

import json
import shutil
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from sonorium.core import plugin_data, theme_presets
from sonorium.core.theme_presets import (
    METADATA_FILE,
    PRESETS_FILE,
    SPEC_VERSION,
    BrokenJsonError,
)
from sonorium.obs import logger


def default_short_file_threshold() -> float:
    """A new theme's short file threshold: Settings > Advanced (15 s out of the box)."""
    try:
        from sonorium import playback_values
    except ImportError:
        return 15.0
    return playback_values.get("short_file_threshold")

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
    # continuous/sparse/presence/unassigned; "auto" (chosen at play time by
    # the file's length) is only a track's default now (_retire_auto)
    playback_mode: str = "auto"
    seamless_loop: bool = False
    exclusive: bool = False
    fade_in: float = 0.0            # the file's own fades, seconds (0 = off)
    fade_out: float = 0.0
    volume_low: float = 0.2         # Ebb & Flow: the lower knob of the Volume slider
    pause_between: bool = False     # Ebb & Flow: play the file once, then pause
    pause_min: float = 30.0         # Ebb & Flow: the pause, seconds (random in this range)
    pause_max: float = 300.0
    rise: float = 0.0               # Ebb & Flow: how fast it rises, 0 gradual .. 1 sudden
    # Plugin attributes on this track (and, in a group, this file): {plugin_id:
    # {name: value}}, only what installed plugins declare (core/plugin_data.py)
    plugins: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        if not data["plugins"]:
            del data["plugins"]
        return data

    @classmethod
    def from_dict(cls, data: dict) -> TrackSettings:
        if data is None:
            return cls()
        settings = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__ and k != "plugins"})
        settings.plugins = plugin_data.clean_block(data.get("plugins"), plugin_data.TRACK_CLASSES, "Track")
        return settings


# Ebb & Flow settings, in metadata.json and presets under these names
# (recording.EBB_TRACK_SETTINGS is the same list, for playback)
EBB_TRACK_SETTINGS = ("volume_low", "pause_between", "pause_min", "pause_max", "rise")


def reset_ebb_settings(track) -> None:
    """A track's Ebb & Flow settings as a new track has them."""
    defaults = TrackSettings()
    for key in EBB_TRACK_SETTINGS:
        setattr(track, key, getattr(defaults, key))


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
    # (a theme's own; a new theme starts with Settings > Advanced's Short file threshold)
    short_file_threshold: float = field(default_factory=default_short_file_threshold)

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

    # Linked files: files in another theme's folder, not copied (core/intrusions.py).
    # Track key here ("Name" or "Group/Name") -> {"theme": source theme id, "track": source track key}
    links: dict[str, dict] = field(default_factory=dict)

    # Ebb & Flow's slider is Duration (higher is longer). False: the stored
    # values still mean the old Interval (higher was quicker) and are flipped
    # once when the theme loads, if it is from before the change (see
    # _convert_ebb_duration). Any other theme is marked True when it loads.
    ebb_duration: bool = False

    # One-time conversions done when the theme loads (see load_theme_folder):
    # intrusion groups retired (their linked files ungrouped, the groups
    # removed), and "auto" playback modes replaced by the mode they play as.
    intrusions_retired: bool = False
    auto_retired: bool = False

    # Plugin attributes on the theme itself: {plugin_id: {name: value}} (core/plugin_data.py)
    plugins: dict = field(default_factory=dict)

    # Problems found while loading (e.g. a broken file), for the UI. Not saved.
    problems: list[str] = field(default_factory=list, compare=False)

    def __post_init__(self):
        # Generate ID if not present
        if not self.id:
            self.id = str(uuid.uuid4())

        # Only well-formed links ({"theme": id, "track": key}; key "Name" or "Group/Name") are kept
        self.links = {
            key: {"theme": link["theme"], "track": link["track"]}
            for key, link in (self.links or {}).items()
            if isinstance(key, str) and key and key.count("/") <= 1 and isinstance(link, dict)
            and isinstance(link.get("theme"), str) and isinstance(link.get("track"), str)
        } if isinstance(self.links, dict) else {}

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
        if self.links:
            data["links"] = self.links
        for marker in ("ebb_duration", "intrusions_retired", "auto_retired"):
            if getattr(self, marker):
                data[marker] = True
        if self.attribution:
            data["attribution"] = self.attribution
        if self.plugins:
            data["plugins"] = self.plugins
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
                    'spec_version', 'groups', 'links', 'ebb_duration',
                    'intrusions_retired', 'auto_retired']:
            if key in data:
                kwargs[key] = data[key]

        # Handle tracks specially
        if 'tracks' in data and data['tracks']:
            kwargs['tracks'] = {
                k: TrackSettings.from_dict(v) if isinstance(v, dict) else v
                for k, v in data['tracks'].items()
            }

        # Plugin attributes: only what installed plugins declare (core/plugin_data.py)
        kwargs['plugins'] = plugin_data.clean_block(data.get('plugins'), ("theme",), f"Theme '{data.get('name', '')}'")
        return cls(**kwargs)


# --- Theme folder load / save (Themes 2.0) ------------------------------------

PRE_PRESETS_BACKUP = METADATA_FILE + ".pre-presets.bak"


def _default_metadata(folder: Path, theme_id: Optional[str] = None) -> ThemeMetadata:
    """Fresh metadata for a folder: its tracks (top level and group folders) with default settings."""
    from sonorium.theme_files import theme_audio_files, track_key
    tracks = {track_key(folder, f): TrackSettings() for f in theme_audio_files(folder)}
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


PRE_DURATION_SUFFIX = ".pre-duration.bak"

# What Sonorium 1.5.0 - 1.6.0 wrote to metadata.json: the 2.0 layout from
# before Ebb & Flow's Interval became Duration (_written_before_duration).
# Every save wrote these top-level keys and each track entry in full.
_OLD_WRITER_KEYS = ("is_favorite", "categories", "short_file_threshold", "groups")
_OLD_TRACK_FIELDS = frozenset(("presence", "muted", "volume", "playback_mode", "seamless_loop", "exclusive"))
# Added by the builds just before the Duration change (Rise came with it)
_OLD_TRACK_EXTRAS = frozenset(("fade_in", "fade_out", "volume_low", "pause_between", "pause_min", "pause_max"))


def _written_before_duration(raw: Optional[dict]) -> bool:
    """
    True when a metadata.json is from before Ebb & Flow's Interval became
    Duration, so its Ebb & Flow values still mean Interval: a 1.0 theme
    (spec_version missing or below 2, or presets inside metadata.json), or a
    2.0 theme exactly as Sonorium 1.5.0 - 1.6.0 saved it (every track entry
    written in full, without the fields the Duration change added). A theme
    written by hand in the 2.0 layout is already in Duration, flag or not.
    """
    if not isinstance(raw, dict) or raw.get("ebb_duration"):
        return False
    if _is_legacy(raw) or "presets" in raw:
        return True
    if raw.get("intrusions_retired") or raw.get("auto_retired"):
        return False  # written after the change
    if any(key not in raw for key in _OLD_WRITER_KEYS):
        return False
    tracks = raw.get("tracks")
    if not isinstance(tracks, dict) or not tracks:
        return False
    return all(isinstance(entry, dict) and _OLD_TRACK_FIELDS <= set(entry) <= _OLD_TRACK_FIELDS | _OLD_TRACK_EXTRAS
               for entry in tracks.values())


def _convert_ebb_duration(metadata: ThemeMetadata, raw: Optional[dict]) -> bool:
    """
    The Interval -> Duration conversion, only for a theme from before the
    change (_written_before_duration); any other theme is just marked done.
    Returns True when values changed.
    """
    if not metadata.ebb_duration and not _written_before_duration(raw):
        metadata.ebb_duration = True
        return False
    return _flip_ebb_duration(metadata)


def _flip_ebb_duration(metadata: ThemeMetadata) -> bool:
    """
    Ebb & Flow's Interval became Duration: higher used to mean quicker, now it
    means longer. Every Ebb & Flow track's value (its own mode, or in an Ebb &
    Flow group) becomes 1 - value, in the theme and in each preset (by the mode
    the preset gives it), so it sounds as before. Once per theme: returns True
    when it changed anything and marks the theme.
    """
    if metadata.ebb_duration:
        return False
    ebb_groups = {name for name, settings in (metadata.groups or {}).items()
                  if isinstance(settings, dict) and settings.get("mode") == "ebb_flow"}

    def is_ebb(key: str, mode) -> bool:
        group = key.split("/", 1)[0] if "/" in key else None
        return mode == "presence" or group in ebb_groups

    changed = False
    for key, settings in (metadata.tracks or {}).items():
        if isinstance(settings, TrackSettings) and is_ebb(key, settings.playback_mode):
            settings.presence = _flip(settings.presence)
            changed = True
    for preset in (metadata.presets or {}).values():
        for key, settings in ((preset or {}).get("tracks") or {}).items():
            if isinstance(settings, dict) and "presence" in settings and is_ebb(key, settings.get("playback_mode")):
                settings["presence"] = _flip(settings["presence"])
                changed = True
    metadata.ebb_duration = True
    return changed


PRE_LINKS_SUFFIX = ".pre-links.bak"
PRE_AUTO_SUFFIX = ".pre-auto.bak"


def _backup_once(folder: Path, suffix: str) -> None:
    """metadata.json and presets.json as they are, once, before a conversion writes. Raises OSError."""
    for name in (METADATA_FILE, PRESETS_FILE):
        original, backup = folder / name, folder / (name + suffix)
        if original.exists() and not backup.exists():
            shutil.copy2(original, backup)


def _retire_intrusions(folder: Path, metadata: ThemeMetadata, doc: dict) -> bool:
    """
    Intrusion groups are retired: once per theme, each one's linked files
    become ungrouped and the group is removed (its own files move up to the
    theme folder), see intrusions.retire_intrusion_groups. The JSON files are
    backed up (*.pre-links.bak) before any file moves; doc["sequences"]
    follows the new keys. Returns True when it changed anything; the theme is
    marked when every group was converted (else it is tried again next time).
    """
    if metadata.intrusions_retired:
        return False
    if not any(isinstance(s, dict) and s.get("mode") == "intrusion" for s in (metadata.groups or {}).values()):
        metadata.intrusions_retired = True
        return False
    from sonorium.core import intrusions
    try:
        _backup_once(folder, PRE_LINKS_SUFFIX)
    except OSError as e:
        _warn_read_only(folder, e)
        return False
    sequences = doc.get("sequences") if isinstance(doc.get("sequences"), dict) else {}
    converted, sequences, error = intrusions.retire_intrusion_groups(metadata, folder, sequences)
    if "sequences" in doc:
        doc["sequences"] = sequences
    if error is not None:
        # The groups converted before it are saved; the rest next time
        logger.warning(f"Theme '{folder.name}': intrusion group not converted yet ({error})")
        return bool(converted)
    metadata.intrusions_retired = True
    logger.info(f"Theme '{metadata.name or folder.name}': intrusion group(s) {', '.join(converted)} converted: "
                f"their linked files are ungrouped (originals kept as *{PRE_LINKS_SUFFIX})")
    return True


def track_seconds(path: Path) -> Optional[float]:
    """
    A file's length in seconds; None when it can't be read, holds no audio,
    or playback can't be loaded. (played_seconds is the length playback uses.)
    """
    try:
        from sonorium.recording import SAMPLE_RATE, file_length_samples
    except Exception:
        return None
    try:
        samples = file_length_samples(Path(path))
    except Exception:
        return None
    return samples / SAMPLE_RATE if samples and samples > 0 else None


def played_seconds(path: Path) -> Optional[float]:
    """
    A file's length in seconds as playback uses it: a file that can't be read
    counts as one minute (recording.AudioFile). None when playback can't be loaded.
    """
    try:
        from sonorium.recording import AudioFile
    except Exception:
        return None
    return AudioFile(Path(path)).duration_seconds


def _track_file(folder: Path, metadata: ThemeMetadata, key: str, folder_for_id) -> Optional[Path]:
    """The audio file of a track key: the theme's own, or a linked file's source (None if gone)."""
    from sonorium.core import intrusions, theme_groups
    link = (metadata.links or {}).get(key)
    if link is not None:
        return intrusions.resolve_link(link, folder_for_id)
    try:
        return theme_groups.find_track_file(folder, key)
    except theme_groups.GroupError:
        return None


def _flip(value):
    """An Ebb & Flow Interval value as Duration (1 - value, kept within 0-1)."""
    try:
        return round(1.0 - min(1.0, max(0.0, float(value))), 4)
    except (TypeError, ValueError):
        return value


def _retire_auto(folder: Path, metadata: ThemeMetadata) -> bool:
    """
    "auto" is retired as a playback mode: once per theme, every track whose
    playback_mode is "auto" in metadata.json or in a preset gets the mode it
    plays as (recording.ThemeTrack._resolve_playback_mode): a
    short file (under short_file_threshold) Intermittent ("sparse") when its
    presence is under 1, a long one Ebb & Flow ("presence"), else Background
    ("continuous"). Becoming Ebb & Flow, a track that plays its own mode (not
    in a group) gets presence = 1 - presence (the slider is Duration now, see
    _flip_ebb_duration); in a group the group's mode decides how it plays, so
    its value is kept. A preset that sets presence for such a track gets the
    mode its own value gives (and the flip), so every preset sounds as before.
    Tracks without an entry keep "auto" (decided at play time), and so does a
    track whose file can't be found. Needs the files' lengths: not done (and
    not marked) when playback can't be loaded. Returns True when it changed anything.
    """
    if metadata.auto_retired:
        return False
    keys = _auto_keys(metadata)
    if not keys:
        metadata.auto_retired = True
        return False
    try:
        from sonorium.core import intrusions
    except ImportError:
        return False  # loaded without the sonorium.core package (tests): next time

    folder_for_id = intrusions.folder_for_id_in(folder.parent)
    seconds: dict[str, Optional[float]] = {}
    for key in keys:
        path = _track_file(folder, metadata, key, folder_for_id)
        if path is None:
            seconds[key] = None  # missing: left as "auto"
            continue
        seconds[key] = played_seconds(path)
        if seconds[key] is None:
            return False  # lengths can't be read here: next time

    try:
        threshold = float(metadata.short_file_threshold)
    except (TypeError, ValueError):
        threshold = default_short_file_threshold()

    def resolve(key, presence) -> Optional[str]:
        if seconds.get(key) is None:
            return None
        try:
            below = float(presence) < 1.0
        except (TypeError, ValueError):
            below = False
        short = seconds[key] < threshold
        return ("sparse" if short else "presence") if below else "continuous"

    def own_mode(key) -> bool:
        """The track plays its own mode: ungrouped (an intrusion group's tracks played so too)."""
        group = intrusions.link_group(key)
        return group is None or intrusions.is_intrusion_group((metadata.groups or {}).get(group))

    before = {key: (t.playback_mode, t.presence) for key, t in (metadata.tracks or {}).items()
              if isinstance(t, TrackSettings)}
    changed = False
    for key, settings in (metadata.tracks or {}).items():
        if isinstance(settings, TrackSettings) and settings.playback_mode == "auto":
            mode = resolve(key, settings.presence)
            if mode is None:
                continue
            settings.playback_mode = mode
            if mode == "presence" and own_mode(key):
                settings.presence = _flip(settings.presence)
            changed = True
    for preset in (metadata.presets or {}).values():
        tracks = preset.get("tracks") if isinstance(preset, dict) else None
        for key, entry in (tracks.items() if isinstance(tracks, dict) else ()):
            if not isinstance(entry, dict):
                continue
            theme_mode, theme_presence = before.get(key, ("auto", 1.0))
            if entry.get("playback_mode", theme_mode) != "auto":
                continue
            presence = entry.get("presence", theme_presence)
            mode = resolve(key, presence)
            if mode is None:
                continue
            now = (metadata.tracks or {}).get(key)
            if "playback_mode" in entry or mode != getattr(now, "playback_mode", "auto"):
                entry["playback_mode"] = mode
                changed = True
            if mode == "presence" and own_mode(key):
                flipped = _flip(presence)
                if "presence" in entry or getattr(now, "presence", 1.0) != flipped:
                    entry["presence"] = flipped
                    changed = True
    metadata.auto_retired = True
    return changed


def _auto_keys(metadata: ThemeMetadata) -> list[str]:
    """The track keys with playback_mode "auto" in metadata.json or a preset."""
    keys = [k for k, t in (metadata.tracks or {}).items() if getattr(t, "playback_mode", None) == "auto"]
    for preset in (metadata.presets or {}).values():
        tracks = preset.get("tracks") if isinstance(preset, dict) else None
        for key, entry in (tracks.items() if isinstance(tracks, dict) else ()):
            if isinstance(entry, dict) and entry.get("playback_mode") == "auto" and key not in keys:
                keys.append(key)
    return keys


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
        # The backup made when the theme was converted still has its presets
        try:
            backup = theme_presets.read_json(folder / PRE_PRESETS_BACKUP) or {}
        except BrokenJsonError:
            backup = {}
        restored = backup.get("presets") if isinstance(backup.get("presets"), dict) else None
        if restored:
            logger.warning(f"Theme '{folder.name}': restored {len(restored)} presets from {PRE_PRESETS_BACKUP}")
            problems.append(f"Presets were restored from {PRE_PRESETS_BACKUP}; changes made after the theme was converted are in the .broken file")
            return {"presets": dict(restored)}, True
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


def load_theme_folder(folder: Path, convert: bool = True) -> ThemeMetadata:
    """
    Load a theme folder's metadata and presets, converting a 1.0 theme to 2.0.

    convert=False writes nothing at all (no conversion saved, no backup, no
    broken file renamed): the 1.0 fields and Ebb & Flow Duration are converted
    in memory only (a later save writes them, as the server would have), and
    the conversions that move files or need the audio files' lengths
    (intrusion groups, "auto" modes) are left undone and unmarked, so the
    server makes them when it next loads the theme.

    Conversion (idempotent):
      - presets in metadata.json move to presets.json (presets.json wins on the
        same id), presets.json is verified, metadata.json is backed up once to
        metadata.json.pre-presets.bak, then "presets" is removed from it
      - spec_version becomes 2; exclusive tracks are recorded in an "Exclusive" group
    Once per theme, each with a backup of both files made before it writes:
      - Ebb & Flow Interval values become Duration, for a theme from before that
        change (_convert_ebb_duration, *.pre-duration.bak)
      - intrusion groups are retired (_retire_intrusions, *.pre-links.bak)
      - "auto" playback modes become the mode they play as (_retire_auto, *.pre-auto.bak)
    A broken metadata.json/presets.json is kept as <name>.broken-<time> and
    rebuilt with defaults; the problem is listed in metadata.problems.
    If the folder can't be written, the files are used as they are (presets
    still read from metadata.json) and a warning is logged once.
    """
    folder = Path(folder)
    problems: list[str] = []
    meta_path = folder / METADATA_FILE

    metadata, raw = _read_metadata(folder, problems, rename_broken=convert)
    is_new = metadata is None
    if is_new:
        metadata = ThemeMetadata(name=folder.name)

    legacy_presets = {}
    has_legacy_key = raw is not None and "presets" in raw
    if has_legacy_key and isinstance(raw["presets"], dict):
        legacy_presets = dict(raw["presets"])
    legacy = raw is not None and _is_legacy(raw)

    doc, presets_broken = _read_presets_doc(folder, problems, rename_broken=convert)
    presets = dict(legacy_presets)
    presets.update(doc.get("presets") or {})
    plugin_data.filter_presets(presets, f"Theme '{metadata.name or folder.name}'")
    metadata.presets = presets

    if legacy:
        _convert_fields(metadata)
    flipped = _convert_ebb_duration(metadata, raw)
    if not convert:
        if not metadata.name:
            metadata.name = folder.name
        metadata.problems = problems
        return metadata
    # Moves files, so it backs up (and checks the folder can be written) first
    regrouped = _retire_intrusions(folder, metadata, doc)
    presets = metadata.presets
    resolved = _retire_auto(folder, metadata)
    converted = flipped or regrouped or resolved
    markers = ("ebb_duration", "intrusions_retired", "auto_retired")

    write_meta = (raw is None or legacy or has_legacy_key or "id" not in raw or converted
                  or any(getattr(metadata, m) and not raw.get(m) for m in markers))
    if not metadata.name:
        metadata.name = folder.name
        write_meta = True
    write_presets = has_legacy_key or presets_broken or not (folder / PRESETS_FILE).exists() or converted

    try:
        # The files as they were, once, before the values are changed
        if flipped:
            _backup_once(folder, PRE_DURATION_SUFFIX)
        if resolved:
            _backup_once(folder, PRE_AUTO_SUFFIX)
        if write_presets:
            _save_presets_verified(folder, presets)
        if regrouped and isinstance(doc.get("sequences"), dict):
            saved = theme_presets.load_presets_doc(folder)
            if saved.get("sequences") != doc["sequences"]:
                theme_presets.write_json_atomic(folder / PRESETS_FILE, {**saved, "sequences": doc["sequences"]})
        if has_legacy_key and meta_path.exists() and not (folder / PRE_PRESETS_BACKUP).exists():
            shutil.copy2(meta_path, folder / PRE_PRESETS_BACKUP)
        if write_meta:
            theme_presets.write_json_atomic(meta_path, metadata.to_dict())
    except OSError as e:
        _warn_read_only(folder, e)
    else:
        if legacy or has_legacy_key:
            logger.info(f"Theme '{metadata.name}' converted to the 2.0 format")
        if flipped:
            logger.info(f"Theme '{metadata.name}': Ebb & Flow Interval values converted to Duration "
                        f"(originals kept as *{PRE_DURATION_SUFFIX})")
        if resolved:
            logger.info(f"Theme '{metadata.name}': Auto playback modes replaced by the mode each track plays as "
                        f"(originals kept as *{PRE_AUTO_SUFFIX})")
        if is_new and not converted:
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


def drop_unknown_plugin_data(folder: Path) -> bool:
    """
    An imported theme folder's metadata.json and presets.json with only the
    plugin attributes this Sonorium's installed plugins declare (strict: no
    plugin, no values). What goes is logged at info. Run before the theme
    loads. Returns True when a file changed; a broken or missing file is left
    for load_theme_folder.
    """
    folder = Path(folder)
    label = f"Imported theme '{folder.name}'"
    changed = False
    try:
        raw = theme_presets.read_json(folder / METADATA_FILE)
        if raw is not None:
            before = json.dumps(raw, sort_keys=True)
            if "plugins" in raw:
                kept = plugin_data.clean_block(raw.get("plugins"), ("theme",), label, strict=True)
                if kept:
                    raw["plugins"] = kept
                else:
                    del raw["plugins"]
            for key, entry in (raw.get("tracks") or {}).items() if isinstance(raw.get("tracks"), dict) else ():
                if isinstance(entry, dict) and "plugins" in entry:
                    kept = plugin_data.clean_block(entry.get("plugins"), plugin_data.TRACK_CLASSES,
                                                   f"{label}, track '{key}'", strict=True)
                    if kept:
                        entry["plugins"] = kept
                    else:
                        del entry["plugins"]
            plugin_data.filter_presets(raw.get("presets"), label, strict=True)  # a 1.0 theme's presets
            if json.dumps(raw, sort_keys=True) != before:
                theme_presets.write_json_atomic(folder / METADATA_FILE, raw)
                changed = True
        doc = theme_presets.read_json(folder / PRESETS_FILE)
        if doc is not None and isinstance(doc.get("presets"), dict):
            before = json.dumps(doc, sort_keys=True)
            plugin_data.filter_presets(doc["presets"], label, strict=True)
            if json.dumps(doc, sort_keys=True) != before:
                theme_presets.write_json_atomic(folder / PRESETS_FILE, doc)
                changed = True
    except (BrokenJsonError, OSError) as e:
        logger.debug(f"{label}: plugin attributes not checked before loading ({e})")
    return changed


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
            # Hidden folders aren't themes (e.g. a plugin's theme still being written)
            if not folder.is_dir() or folder.name.startswith("."):
                continue

            # Check for audio files (top level and group folders)
            from sonorium.theme_files import theme_audio_files
            audio_files = theme_audio_files(folder)

            # No audio yet is fine for a theme made in Sonorium (it has metadata.json)
            if not audio_files and not (folder / METADATA_FILE).is_file():
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


def add_unassigned_track(folder: Path, key: str, metadata: Optional[ThemeMetadata] = None,
                         manager: Optional[ThemeMetadataManager] = None) -> bool:
    """
    A new (uploaded) file's track entry: Unassigned (playback_mode
    "unassigned": not played until it is placed). Through the loaded metadata
    when there is one, saved by its manager (so a later save keeps it), else
    in metadata.json directly. A track that has an entry keeps it. Returns
    True when it added one. Raises OSError when it can't be saved.
    """
    folder = Path(folder)
    if metadata is not None:
        if key in metadata.tracks:
            return False
        metadata.tracks[key] = TrackSettings(playback_mode="unassigned")
        saved = manager.save_metadata(metadata.id, metadata) if manager is not None else save_theme_folder(folder, metadata)
        if not saved:
            raise OSError(f"{METADATA_FILE} couldn't be saved")
        return True
    path = folder / METADATA_FILE
    try:
        raw = theme_presets.read_json(path)
    except BrokenJsonError as e:
        raise OSError(str(e)) from e
    if raw is None:
        return False  # loaded later with defaults, as a folder without metadata.json is
    tracks = raw.setdefault("tracks", {})
    if not isinstance(tracks, dict) or key in tracks:
        return False
    tracks[key] = TrackSettings(playback_mode="unassigned").to_dict()
    theme_presets.write_json_atomic(path, raw)
    return True


def sync_entries_with_files(folder: Path, metadata: ThemeMetadata) -> Optional[dict]:
    """
    Make metadata.json and the presets match the theme's files: a track that
    has no entry gets one with the default settings; entries for tracks and
    group folders that no longer exist are removed, from metadata.json and from
    every preset. Returns what changed ({"added", "removed", "groups_removed",
    "preset_entries_removed"}), or None when nothing did.

    Nothing is removed when the folder holds no audio at all (e.g. a share
    that is briefly unreachable), so a hiccup can't wipe a theme's settings.

    Linked files (metadata.links) count as present while their link exists,
    even when the source file is missing; links themselves are only removed
    through the API. A linked key without an entry gets one, muted. A group
    that holds links counts as present even without its folder.
    """
    from sonorium.core.theme_groups import group_folder_names
    from sonorium.theme_files import theme_audio_files, track_key

    folder = Path(folder)
    if not folder.is_dir():
        return None
    keys = [track_key(folder, f) for f in theme_audio_files(folder)]
    if not keys:
        return None
    links = dict(getattr(metadata, "links", None) or {})
    present = set(keys) | set(links)
    groups = set(group_folder_names(folder)) | {key.split("/", 1)[0] for key in links if "/" in key}

    def stale_group(name, settings):
        return name not in groups and not (settings or {}).get("legacy_exclusive") and name != LEGACY_EXCLUSIVE_GROUP

    added = [k for k in keys if k not in metadata.tracks]
    for key in added:
        metadata.tracks[key] = TrackSettings()
    linked_added = [k for k in links if k not in metadata.tracks]
    for key in linked_added:
        metadata.tracks[key] = TrackSettings(muted=True)
    added += linked_added
    removed = [k for k in metadata.tracks if k not in present]
    for key in removed:
        del metadata.tracks[key]
    groups_removed = [g for g, s in (metadata.groups or {}).items() if stale_group(g, s)]
    for name in groups_removed:
        del metadata.groups[name]

    preset_entries_removed = 0
    for preset in (metadata.presets or {}).values():
        if not isinstance(preset, dict):
            continue
        tracks = preset.get("tracks")
        if isinstance(tracks, dict):
            for key in [k for k in tracks if k not in present]:
                del tracks[key]
                preset_entries_removed += 1
        preset_groups = preset.get("groups")
        if isinstance(preset_groups, dict):
            for name in [g for g, s in preset_groups.items() if stale_group(g, s)]:
                del preset_groups[name]
                preset_entries_removed += 1

    if not (added or removed or groups_removed or preset_entries_removed):
        return None
    return {"added": added, "removed": removed, "groups_removed": groups_removed,
            "preset_entries_removed": preset_entries_removed}
