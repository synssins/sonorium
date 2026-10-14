"""
Theme files for plugins (Themes 2.0).

Plugins that create a theme or add to one go through ThemeEdit, so they never
write metadata.json or presets.json themselves: everything is written with
core/theme_metadata.save_theme_folder (spec_version 2, presets in
presets.json, see docs/THEME_FORMAT.md).

    edit = ThemeEdit.new(self.audio_path, "Rainy Tavern", description="...")
    # or: edit = ThemeEdit.open(self.audio_path, theme_id)
    fire = edit.add_file(downloaded_bytes, "Fireplace.mp3", settings={"playback_mode": "continuous"})
    edit.add_file(Path("/tmp/door.wav"), group="Doors", settings={"playback_mode": "sparse", "presence": 0.4})
    edit.set_group("Doors", {"volume": 0.8})
    edit.add_preset("Quiet night", {fire: {"volume": 0.5}})
    edit.add_extra_file("ATTRIBUTION.md", "...")   # never replaces a file the theme has
    result = edit.commit()  # {"theme_id", "folder", "name", "is_new", "added"}

Nothing is written before commit(). commit() writes the audio files first,
then presets.json and metadata.json. A new theme is built in a hidden
staging folder that is renamed into place at the end; an existing theme gets
its new files under hidden temporary names that are renamed once all are
written, and its JSON files are restored if saving fails. Either way a
failure leaves the themes as they were (ThemeEditError, or the OSError).

Rules kept for the plugin:
  - playback_mode is continuous (Background), sparse (Intermittent),
    presence (Ebb & Flow) or unassigned; "auto" is retired and refused
  - seamless_loop True means no crossfade at the loop point (Gapless)
  - a file never replaces another: a clashing name gets " (2)", " (3)", ...
  - every preset added lists every track with all six fields; tracks it
    doesn't name are muted in it (mute_others), and the theme's existing
    presets get the new tracks muted, so they sound as before
  - a new theme is marked as needing none of the load-time conversions
    (its values are already in the 2.0 meaning)
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import uuid
from pathlib import Path
from typing import Optional, Union

from sonorium.core import theme_groups, theme_presets
from sonorium.core.intrusions import file_keys, free_link_key
from sonorium.core.theme_metadata import (
    EBB_TRACK_SETTINGS,
    ThemeMetadata,
    TrackSettings,
    load_theme_folder,
    save_theme_folder,
)
from sonorium.core.theme_presets import METADATA_FILE, PRESETS_FILE, BrokenJsonError
from sonorium.theme_files import AUDIO_EXTENSIONS, theme_audio_files, track_display_name
from sonorium.obs import logger

# Playback modes a plugin can write ("auto" is retired)
TRACK_MODES = ("continuous", "sparse", "presence", "unassigned")

# A preset's fields for one track: the six TrackSettings fields plus Ebb & Flow's
PRESET_TRACK_FIELDS = ("volume", "presence", "playback_mode", "seamless_loop", "exclusive", "muted") + EBB_TRACK_SETTINGS

# A preset's fields for one group (docs/THEME_FORMAT.md 4.1, 2.5)
PRESET_GROUP_FIELDS = ("volume", "presence", "muted", "gap_min", "gap_max", "pause_between")

_UNIT_FIELDS = ("volume", "presence", "volume_low", "rise")   # 0.0-1.0
_BOOL_FIELDS = ("muted", "seamless_loop", "exclusive", "pause_between")
_SECONDS_FIELDS = ("fade_in", "fade_out", "pause_min", "pause_max")

# Hidden names (theme_files ignores names starting with ".")
STAGING_PREFIX = ".sonorium-new-"
PARTIAL_PREFIX = ".sonorium-partial-"


class ThemeEditError(Exception):
    """A theme change a plugin asked for that can't be done (nothing was written)."""


# --- finding themes -------------------------------------------------------------

def _theme_folders(audio_path: Path) -> list[Path]:
    try:
        return sorted(p for p in Path(audio_path).iterdir() if p.is_dir() and not p.name.startswith("."))
    except OSError:
        return []


def _read_metadata_raw(folder: Path) -> Optional[dict]:
    try:
        return theme_presets.read_json(folder / METADATA_FILE)
    except BrokenJsonError:
        return None


def list_themes(audio_path: Path) -> list[dict]:
    """
    The themes in audio_path, sorted by name, for a plugin's theme picker:
    [{"id", "name", "folder", "track_count", "preset_count"}]. Read only.
    "id" is the theme's id (its folder name when it has none yet).
    """
    themes = []
    for folder in _theme_folders(audio_path):
        raw = _read_metadata_raw(folder)
        files = theme_audio_files(folder)
        if raw is None and not files and not (folder / METADATA_FILE).exists():
            continue  # not a theme
        raw = raw or {}
        links = raw.get("links") if isinstance(raw.get("links"), dict) else {}
        themes.append({
            "id": raw.get("id") if isinstance(raw.get("id"), str) and raw.get("id") else folder.name,
            "name": raw.get("name") if isinstance(raw.get("name"), str) and raw.get("name") else folder.name,
            "folder": folder.name,
            "track_count": len(files) + len(links),
            "preset_count": len(theme_presets.load_presets(folder)),
        })
    themes.sort(key=lambda t: t["name"].casefold())
    return themes


def find_theme(audio_path: Path, theme_id: str) -> Optional[Path]:
    """
    The folder of a theme in audio_path, by its id (or its folder name), or
    None. The value is only compared with the themes found there (never used
    as a path), so a value from a form can't point anywhere else.
    """
    if not isinstance(theme_id, str) or not theme_id.strip():
        return None
    theme_id = theme_id.strip()
    by_name = None
    for folder in _theme_folders(audio_path):
        raw = _read_metadata_raw(folder) or {}
        if raw.get("id") == theme_id:
            return folder
        if folder.name == theme_id:
            by_name = folder
    return by_name


def folder_name_for(name: str) -> str:
    """A folder name for a theme name: no characters folders can't have, spaces as "_"."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name or "")
    safe = re.sub(r'\s+', '_', safe.strip()).strip('._ ')
    return safe[:100] or "Theme"


def preset_id_for(name: str, existing) -> str:
    """A preset id as the app makes one: the name lower-cased, non [a-z0-9] runs as "_"; "_1", "_2" on a clash."""
    base = re.sub(r'[^a-z0-9]+', '_', (name or "").lower()).strip('_') or "preset"
    preset_id, counter = base, 1
    while preset_id in existing:
        preset_id = f"{base}_{counter}"
        counter += 1
    return preset_id


def remove_stale_staging(audio_path: Path) -> list[str]:
    """
    At start-up: remove what a commit interrupted by a crash left behind, the
    hidden staging folders of new themes (.sonorium-new-*) in audio_path and
    the hidden partial files (.sonorium-partial-*) in theme and group folders.
    Returns the names removed.
    """
    removed = []
    try:
        entries = list(Path(audio_path).iterdir())
    except OSError:
        return removed
    for entry in entries:
        try:
            if entry.is_dir() and entry.name.startswith(STAGING_PREFIX):
                shutil.rmtree(entry)
                removed.append(entry.name)
            elif entry.is_dir() and not entry.name.startswith("."):
                for folder in [entry] + [p for p in entry.iterdir() if p.is_dir()]:
                    for partial in folder.glob(f"{PARTIAL_PREFIX}*"):
                        if partial.is_file():
                            partial.unlink()
                            removed.append(str(partial.relative_to(audio_path)))
        except OSError as e:
            logger.warning(f"Couldn't remove an interrupted theme write in {entry.name}: {e}")
    if removed:
        logger.info(f"Removed {len(removed)} leftover(s) of interrupted theme writes: {', '.join(removed)}")
    return removed


# --- checking values ----------------------------------------------------------------

def _check_mode(mode) -> str:
    if mode == "auto":
        raise ThemeEditError('playback_mode "auto" is retired: use continuous, sparse, presence or unassigned')
    if mode not in TRACK_MODES:
        raise ThemeEditError(f"Unknown playback_mode {mode!r} (use {', '.join(TRACK_MODES)})")
    return mode


def _check_field(name: str, value):
    if name == "playback_mode":
        return _check_mode(value)
    if name in _BOOL_FIELDS:
        if not isinstance(value, bool):
            raise ThemeEditError(f"'{name}' must be true or false")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ThemeEditError(f"'{name}' must be a number")
    if name in _UNIT_FIELDS:
        return min(1.0, max(0.0, float(value)))
    if name in _SECONDS_FIELDS:
        return max(0.0, float(value))
    return float(value)


def track_fields(fields: Optional[dict]) -> dict:
    """A track's settings (TrackSettings fields), checked. ThemeEditError for unknown names or bad values."""
    fields = dict(fields or {})
    # (plugin attributes go through self.attributes, checked against the schema)
    unknown = [k for k in fields if k not in TrackSettings.__dataclass_fields__ or k == "plugins"]
    if unknown:
        raise ThemeEditError(f"Unknown track setting(s): {', '.join(unknown)}")
    return {k: _check_field(k, v) for k, v in fields.items()}


def preset_track_fields(fields: Optional[dict]) -> dict:
    """A preset's entry for one track, checked (PRESET_TRACK_FIELDS)."""
    fields = dict(fields or {})
    unknown = [k for k in fields if k not in PRESET_TRACK_FIELDS]
    if unknown:
        raise ThemeEditError(f"Unknown preset track setting(s): {', '.join(unknown)}")
    return {k: _check_field(k, v) for k, v in fields.items()}


def preset_group_fields(fields: Optional[dict]) -> dict:
    """A preset's entry for one group, checked (PRESET_GROUP_FIELDS)."""
    fields = dict(fields or {})
    unknown = [k for k in fields if k not in PRESET_GROUP_FIELDS]
    if unknown:
        raise ThemeEditError(f"Unknown preset group setting(s): {', '.join(unknown)}")
    return {k: _check_field(k, v) for k, v in fields.items()}


def preset_entry(settings: TrackSettings) -> dict:
    """A track's theme settings as a preset entry (all its fields; an unresolved "auto" is left out)."""
    entry = {k: getattr(settings, k) for k in PRESET_TRACK_FIELDS}
    if entry["playback_mode"] not in TRACK_MODES:
        del entry["playback_mode"]  # follows the theme
    return entry


def resolve_auto(key: str, settings: TrackSettings, path: Optional[Path], threshold: float) -> TrackSettings:
    """
    A copy of a track's settings with "auto" replaced by the mode it plays as
    (as theme_metadata._retire_auto does): presence 1 is Background; below 1
    a short file is Intermittent, a long one Ebb & Flow (an ungrouped track's
    presence then becomes 1 - presence: the slider means Duration). A file
    whose length can't be read counts as short.
    """
    settings = copy.deepcopy(settings)
    if settings.playback_mode != "auto":
        return settings
    if settings.presence >= 1.0:
        settings.playback_mode = "continuous"
        return settings
    seconds = None
    if path is not None:
        from sonorium.core.theme_metadata import played_seconds
        try:
            seconds = played_seconds(path)
        except Exception:
            seconds = None
    if seconds is not None and seconds >= threshold:
        settings.playback_mode = "presence"
        if "/" not in key:
            settings.presence = round(1.0 - min(1.0, max(0.0, settings.presence)), 4)
    else:
        settings.playback_mode = "sparse"
    return settings


# --- the edit -------------------------------------------------------------------

Source = Union[Path, bytes]


class ThemeEdit:
    """Changes to one theme (new or existing), written all at once by commit()."""

    def __init__(self, audio_path: Path, folder: Optional[Path], metadata: ThemeMetadata, is_new: bool):
        self.audio_path = Path(audio_path)
        self.folder = folder
        self.metadata = metadata
        self.is_new = is_new
        self._files: list[tuple[Optional[str], str, Source]] = []  # (group, file name, source)
        self._extras: list[tuple[str, bytes]] = []  # other files at the top of the theme folder
        self._added: list[str] = []
        self._presets_before = set(metadata.presets)
        self._new_presets: dict[str, bool] = {}  # preset id -> mute the tracks it doesn't name
        self._done = False
        self._opened_json: dict[str, Optional[bytes]] = {}  # metadata.json/presets.json as open() read them

    @classmethod
    def new(cls, audio_path: Path, name: str, description: str = "", icon: str = "",
            attribution: Optional[dict] = None, categories: Optional[list] = None) -> ThemeEdit:
        """A new theme named name (its folder is made from the name at commit)."""
        if not isinstance(name, str) or not name.strip():
            raise ThemeEditError("A theme name is required")
        if not Path(audio_path).is_dir():
            raise ThemeEditError(f"Themes folder not found: {audio_path}")
        metadata = ThemeMetadata(
            name=name.strip(), description=description or "", icon=icon or "",
            attribution=attribution or None, categories=list(categories or []),
            ebb_duration=True, intrusions_retired=True, auto_retired=True,
        )
        return cls(audio_path, None, metadata, True)

    @classmethod
    def open(cls, audio_path: Path, theme_id: str) -> ThemeEdit:
        """An existing theme, by id or folder name. ThemeEditError if it isn't found or its files are broken."""
        folder = find_theme(audio_path, theme_id)
        if folder is None:
            raise ThemeEditError(f"Theme not found: {theme_id}")
        metadata = load_theme_folder(folder)
        if metadata.problems:
            raise ThemeEditError(f"Theme '{metadata.name}' has problems ({'; '.join(metadata.problems)}); "
                                 "open it in Sonorium and check it first")
        edit = cls(audio_path, folder, metadata, False)
        # The files as loaded (after any load-time conversion); commit refuses if they change
        edit._opened_json = {name: (folder / name).read_bytes() if (folder / name).exists() else None
                             for name in (METADATA_FILE, PRESETS_FILE)}
        return edit

    # --- reading ----------------------------------------------------------------

    @property
    def theme_id(self) -> str:
        return self.metadata.id

    @property
    def name(self) -> str:
        return self.metadata.name

    def keys(self) -> list[str]:
        """Every track key: the theme's files, linked files and the files added so far."""
        keys = list(self.metadata.tracks)
        own_files = file_keys(self.folder) if self.folder else []
        for key in own_files + list(self.metadata.links) + self._added:
            if key not in keys:
                keys.append(key)
        return keys

    def groups(self) -> list[str]:
        """The theme's group names (folders, group settings and groups of added files)."""
        names = list(theme_groups.group_folder_names(self.folder)) if self.folder else []
        for name in list(self.metadata.groups) + [g for g, _, _ in self._files if g]:
            if name not in names:
                names.append(name)
        return names

    def _taken(self, key: str) -> bool:
        return key.casefold() in {k.casefold() for k in self.keys()}

    # --- changing ------------------------------------------------------------------

    def _check_open(self):
        if self._done:
            raise ThemeEditError("This edit was already committed")

    def group_name(self, name: str) -> str:
        """The group name to use: an existing group with the same name in any case, else the name checked."""
        try:
            name = theme_groups.validate_group_name(name)
        except theme_groups.GroupError as e:
            raise ThemeEditError(str(e)) from e
        for existing in self.groups():
            if existing.casefold() == name.casefold():
                return existing
        return name

    def add_file(self, source: Source, filename: Optional[str] = None, group: Optional[str] = None,
                 settings: Optional[dict] = None) -> str:
        """
        Add an audio file (a path to copy, or the file's bytes) as a track,
        in group's folder if given. A name already taken gets " (2)" etc.
        settings are the track's settings (default: Background). Returns the track key.
        """
        self._check_open()
        if filename is None:
            if not isinstance(source, Path):
                raise ThemeEditError("A file name is needed for file content")
            filename = source.name
        try:
            filename = theme_groups.safe_upload_name(filename)
        except theme_groups.GroupError as e:
            raise ThemeEditError(f"{filename!r}: {e}") from e
        if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
            raise ThemeEditError(f"{filename}: not an audio file ({', '.join(sorted(AUDIO_EXTENSIONS))})")
        if isinstance(source, Path) and not source.is_file():
            raise ThemeEditError(f"File not found: {source}")
        fields = {"playback_mode": "continuous", **track_fields(settings)}
        group = self.group_name(group) if group else None

        # Free name: files in that folder, files added there and linked files' names
        prefix = f"{group}/" if group else ""
        reserved = [track_display_name(k) for k in self.keys() if k.startswith(prefix) and "/" not in k[len(prefix):]]
        reserved += [Path(f).stem for g, f, _ in self._files if g == group]
        # (a new theme has no folder yet: only the reserved names count)
        folder = self.folder or self.audio_path / f"{STAGING_PREFIX}none"
        filename = theme_groups.free_file_name(folder / group if group else folder, filename, reserved=reserved)

        key = f"{prefix}{Path(filename).stem}"
        self._files.append((group, filename, source))
        self._added.append(key)
        self.metadata.tracks[key] = TrackSettings(**fields)
        return key

    def add_extra_file(self, filename: str, content: Union[str, bytes]) -> None:
        """
        A file that isn't a track (e.g. MANIFEST.json, ATTRIBUTION.md) at the
        top of the theme folder. It never replaces a file the theme has.
        """
        self._check_open()
        try:
            filename = theme_groups.safe_upload_name(filename)
        except theme_groups.GroupError as e:
            raise ThemeEditError(f"{filename!r}: {e}") from e
        if Path(filename).suffix.lower() in AUDIO_EXTENSIONS or filename in (METADATA_FILE, PRESETS_FILE):
            raise ThemeEditError(f"{filename}: use add_file for audio; the theme's JSON files are written by commit")
        data = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        self._extras = [(n, d) for n, d in self._extras if n != filename] + [(filename, data)]

    def add_link(self, theme_id: str, track: str, group: Optional[str] = None,
                 settings: Optional[dict] = None) -> str:
        """Link another theme's track (theme id + its track key) as a track of this theme. Returns its key."""
        self._check_open()
        if theme_id == self.metadata.id:
            raise ThemeEditError("A theme can't link its own files")
        group = self.group_name(group) if group else None
        key =free_link_key(group, track_display_name(track), self.keys())
        self.metadata.links[key] = {"theme": theme_id, "track": track}
        self.metadata.tracks[key] = TrackSettings(**{"playback_mode": "continuous", **track_fields(settings)})
        self._added.append(key)
        return key

    def set_track(self, key: str, **fields) -> None:
        """Change a track's settings."""
        self._check_open()
        if key not in self.keys():
            raise ThemeEditError(f"No track '{key}'")
        settings = self.metadata.tracks.setdefault(key, TrackSettings(playback_mode="continuous"))
        for name, value in track_fields(fields).items():
            setattr(settings, name, value)

    def set_group(self, name: str, settings: dict) -> str:
        """Set (merge) a group's settings (docs/THEME_FORMAT.md 2.5). Returns the group name used."""
        self._check_open()
        name = self.group_name(name)
        if not isinstance(settings, dict):
            raise ThemeEditError("Group settings must be an object")
        if settings.get("mode") == "intrusion":
            raise ThemeEditError('Group mode "intrusion" is retired')
        current = dict(self.metadata.groups.get(name) or {})
        current.update(copy.deepcopy(settings))
        self.metadata.groups[name] = current
        return name

    def preset_names(self) -> list[str]:
        return [p.get("name", "") for p in self.metadata.presets.values() if isinstance(p, dict)]

    def free_preset_name(self, name: str) -> str:
        """name, or "name (2)", "name (3)", ... when a preset already has it (compared without case)."""
        taken = {n.casefold() for n in self.preset_names()}
        candidate, n = name, 2
        while candidate.casefold() in taken:
            candidate = f"{name} ({n})"
            n += 1
        return candidate

    def add_preset(self, name: str, tracks: dict, groups: Optional[dict] = None,
                   is_default: Optional[bool] = None, mute_others: bool = True) -> str:
        """
        Add a preset: tracks is track key -> settings for it (missing fields
        come from the track's settings at commit), groups is group name ->
        {volume, presence, muted, gap_min, gap_max, pause_between}. Tracks it
        doesn't name are muted in it (mute_others), so it sounds the same
        whatever is added to the theme later. is_default None: default when
        the theme has no default preset yet. Returns the preset id.
        """
        self._check_open()
        if not isinstance(name, str) or not name.strip():
            raise ThemeEditError("A preset name is required")
        name = name.strip()
        keys = self.keys()
        entries = {}
        for key, fields in (tracks or {}).items():
            if key not in keys:
                raise ThemeEditError(f"Preset '{name}': no track '{key}'")
            entries[key] = preset_track_fields(fields)
        group_entries = {}
        for group, fields in (groups or {}).items():
            group = self.group_name(group)
            if group not in self.groups():
                raise ThemeEditError(f"Preset '{name}': no group '{group}'")
            group_entries[group] = preset_group_fields(fields)

        presets = self.metadata.presets
        if is_default is None:
            is_default = not any(isinstance(p, dict) and p.get("is_default") for p in presets.values())
        if is_default:
            for preset in presets.values():
                if isinstance(preset, dict):
                    preset["is_default"] = False
        preset_id = preset_id_for(name, presets)
        preset = {"name": name, "is_default": bool(is_default), "tracks": entries}
        if group_entries:
            preset["groups"] = group_entries
        presets[preset_id] = preset
        self._new_presets[preset_id] = mute_others
        return preset_id

    def _complete_presets(self) -> None:
        """Every new preset lists every track with all its fields; existing presets get the new tracks muted."""
        keys = self.keys()
        for preset_id, preset in self.metadata.presets.items():
            if not isinstance(preset, dict):
                continue
            tracks = preset.setdefault("tracks", {})
            if not isinstance(tracks, dict):
                continue
            if preset_id in self._new_presets:
                mute_others = self._new_presets[preset_id]
                for key in keys:
                    base = preset_entry(self.metadata.tracks.get(key) or TrackSettings())
                    if key in tracks:
                        tracks[key] = {**base, **tracks[key]}
                    elif mute_others:
                        tracks[key] = {**base, "muted": True}
            elif preset_id in self._presets_before:
                for key in self._added:
                    if key not in tracks:
                        tracks[key] = {**preset_entry(self.metadata.tracks[key]), "muted": True}

    # --- writing ---------------------------------------------------------------------

    def _unique_folder(self) -> Path:
        base = folder_name_for(self.metadata.name)
        candidate, n = self.audio_path / base, 2
        while candidate.exists():
            candidate = self.audio_path / f"{base}_{n}"
            n += 1
        return candidate

    @staticmethod
    def _write(source: Source, target: Path) -> None:
        if isinstance(source, Path):
            shutil.copy2(source, target)
        else:
            target.write_bytes(bytes(source))

    def commit(self) -> dict:
        """
        Write everything. Returns {"theme_id", "folder", "name", "is_new",
        "added"}. On failure nothing is left changed and the error is raised.
        """
        self._check_open()
        self._complete_presets()
        if self.is_new:
            self._commit_new()
        else:
            self._commit_existing()
        self._done = True
        # Its audio becomes MP3 in the background on the server (core/theme_mp3.py)
        from sonorium.core import theme_mp3
        theme_mp3.request(self.folder)
        logger.info(f"Theme '{self.metadata.name}' {'created' if self.is_new else 'updated'} "
                    f"in {self.folder.name}: {len(self._added)} track(s) added")
        return {"theme_id": self.metadata.id, "folder": self.folder, "name": self.metadata.name,
                "is_new": self.is_new, "added": list(self._added)}

    def _commit_new(self) -> None:
        staging = self.audio_path / f"{STAGING_PREFIX}{uuid.uuid4().hex[:12]}"
        staging.mkdir()
        try:
            for group, filename, source in self._files:
                target_dir = staging / group if group else staging
                target_dir.mkdir(exist_ok=True)
                self._write(source, target_dir / filename)
            for filename, data in self._extras:
                (staging / filename).write_bytes(data)
            if not save_theme_folder(staging, self.metadata):
                raise ThemeEditError("The theme files couldn't be saved")
            final = self._unique_folder()
            os.rename(staging, final)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        self.folder = final

    def _commit_existing(self) -> None:
        folder = self.folder
        saved_json = {}
        for name in (METADATA_FILE, PRESETS_FILE):
            path = folder / name
            saved_json[name] = path.read_bytes() if path.exists() else None
        # Saved by anyone else since open(): writing now would lose that change
        if saved_json != self._opened_json:
            raise ThemeEditError("The theme was changed meanwhile. Try again.")
        created_dirs: list[Path] = []
        partial: list[tuple[Path, Path]] = []
        placed: list[Path] = []
        try:
            for group, filename, source in self._files:
                target_dir = folder / group if group else folder
                if not target_dir.exists():
                    target_dir.mkdir()
                    created_dirs.append(target_dir)
                temp = target_dir / f"{PARTIAL_PREFIX}{uuid.uuid4().hex[:12]}{Path(filename).suffix}"
                partial.append((temp, target_dir / filename))
                self._write(source, temp)
            for filename, data in self._extras:
                if (folder / filename).exists():
                    continue  # never replaced
                temp = folder / f"{PARTIAL_PREFIX}{uuid.uuid4().hex[:12]}"
                partial.append((temp, folder / filename))
                temp.write_bytes(data)
            for temp, final in partial:
                if final.exists():
                    raise ThemeEditError(f"'{final.name}' appeared in the theme folder meanwhile")
                os.rename(temp, final)
                placed.append(final)
            partial = []
            if not save_theme_folder(folder, self.metadata):
                raise ThemeEditError("The theme files couldn't be saved")
        except BaseException:
            for path in [t for t, _ in partial] + placed:
                try:
                    path.unlink()
                except OSError:
                    pass
            for directory in reversed(created_dirs):
                try:
                    directory.rmdir()
                except OSError:
                    pass
            for name, data in saved_json.items():
                path = folder / name
                try:
                    if data is None:
                        if path.exists():
                            path.unlink()
                    elif not path.exists() or path.read_bytes() != data:
                        temp = path.with_name(path.name + ".restore")
                        temp.write_bytes(data)
                        os.replace(temp, path)
                except OSError as e:
                    logger.error(f"Theme '{self.metadata.name}': couldn't restore {name} ({e})")
            raise
