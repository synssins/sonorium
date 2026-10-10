"""
Group folders on disk (Themes 2.0): create, rename and delete groups, move
tracks between groups, and carry track keys along in metadata and presets.

A group is a subfolder of a theme folder (one level, see theme_files.py). A
track's key is "File" at the top level or "Group/File" in a group, without the
extension. Moving or renaming changes keys, so every place that stores a key
(metadata.json "tracks", "groups", and every preset's "tracks"/"groups") has to
follow.

The functions here only touch files; they return what changed so the caller
can migrate the keys (migrate_keys) and save. File moves come first: if one
fails, the moves already done are undone and nothing else changes.
Audio files are never deleted.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sonorium.theme_files import AUDIO_EXTENSIONS

MAX_GROUP_NAME = 60
_FORBIDDEN_CHARS = set('/\\:*?"<>|')


class GroupError(Exception):
    """A group operation that can't be done. status is the HTTP status to answer with."""
    status = 400


class InvalidName(GroupError):
    status = 400


class NotFound(GroupError):
    status = 404


class Conflict(GroupError):
    status = 409


@dataclass
class Change:
    """What an operation changed, for migrate_keys and the API response."""
    track_keys: dict[str, str] = field(default_factory=dict)        # old key -> new key
    group_renames: dict[str, Optional[str]] = field(default_factory=dict)  # old -> new, None = removed
    folder_removed: bool = True
    left_behind: list[str] = field(default_factory=list)              # files kept in a group folder


# --- names and lookups ------------------------------------------------------

def validate_group_name(name) -> str:
    """The trimmed group name, or InvalidName."""
    if not isinstance(name, str):
        raise InvalidName("Group name must be text")
    name = name.strip()
    if not name:
        raise InvalidName("Group name is required")
    if len(name) > MAX_GROUP_NAME:
        raise InvalidName(f"Group name can be at most {MAX_GROUP_NAME} characters")
    if name in (".", ".."):
        raise InvalidName("Invalid group name")
    if any(c in _FORBIDDEN_CHARS or ord(c) < 32 for c in name):
        raise InvalidName('Group name can\'t contain / \\ : * ? " < > |')
    if name.startswith((".", "_")):
        raise InvalidName('Group name can\'t start with "." or "_"')
    if name.endswith((".", " ")):
        raise InvalidName('Group name can\'t end with "." or a space')
    return name


def _ignored(path: Path) -> bool:
    return path.name.startswith((".", "_"))


def _is_audio(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS and not _ignored(path)


def group_folder_names(theme_folder: Path) -> list[str]:
    """Every group folder, including empty ones (subfolders not starting with "." or "_")."""
    try:
        return sorted(p.name for p in Path(theme_folder).iterdir() if p.is_dir() and not _ignored(p))
    except OSError:
        return []


def group_track_keys(theme_folder: Path, group: str) -> list[str]:
    """Track keys of a group folder's audio files, sorted by file name."""
    folder = Path(theme_folder) / group
    try:
        files = sorted((p for p in folder.iterdir() if _is_audio(p)), key=lambda p: p.name)
    except OSError:
        return []
    return [f"{group}/{p.stem}" for p in files]


def find_group(theme_folder: Path, name: str) -> Path:
    """The group folder with exactly this name, or NotFound."""
    if isinstance(name, str) and name in group_folder_names(theme_folder):
        return Path(theme_folder) / name
    raise NotFound(f"Group '{name}' not found")


def _entry_named(theme_folder: Path, name: str, exclude: Optional[str] = None) -> Optional[str]:
    """Name of a file or folder in the theme folder matching name case-insensitively."""
    lowered = name.casefold()
    try:
        for p in Path(theme_folder).iterdir():
            if p.name != exclude and p.name.casefold() == lowered:
                return p.name
    except OSError:
        pass
    return None


def find_track_file(theme_folder: Path, key: str) -> Path:
    """The audio file for a track key ("File" or "Group/File"), or NotFound."""
    theme_folder = Path(theme_folder)
    if not isinstance(key, str) or not key:
        raise NotFound("Track not found")
    group, _, stem = key.rpartition("/")
    if group:
        if "/" in group or group not in group_folder_names(theme_folder):
            raise NotFound(f"Track '{key}' not found")
        folder = theme_folder / group
    else:
        folder = theme_folder
    try:
        matches = sorted((p for p in folder.iterdir() if _is_audio(p) and p.stem == stem), key=lambda p: p.name)
    except OSError:
        matches = []
    if not matches:
        raise NotFound(f"Track '{key}' not found")
    return matches[0]


def free_file_name(folder: Path, filename: str, ignore: Optional[Path] = None) -> str:
    """
    filename, or "name (2).ext", "name (3).ext", ... if a file with that name,
    or an audio file with the same track name (stem), is already in folder.
    Compared without case, so it's the same on Windows and Linux.
    """
    folder = Path(folder)
    stem, ext = os.path.splitext(filename)
    try:
        entries = [p for p in folder.iterdir() if ignore is None or p != ignore]
    except OSError:
        entries = []
    names = {p.name.casefold() for p in entries}
    stems = {p.stem.casefold() for p in entries if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS}

    def taken(candidate_stem: str) -> bool:
        return (candidate_stem + ext).casefold() in names or candidate_stem.casefold() in stems

    if not taken(stem):
        return filename
    n = 2
    while taken(f"{stem} ({n})"):
        n += 1
    return f"{stem} ({n}){ext}"


def safe_upload_name(filename) -> str:
    """Only the base name of an uploaded file (no folders), or InvalidName."""
    if not isinstance(filename, str):
        raise InvalidName("No file name")
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or name in (".", "..") or name.startswith(".") or any(c in ':*?"<>|' or ord(c) < 32 for c in name):
        raise InvalidName("Invalid file name")
    return name


def group_target(theme_folder: Path, name: str, create: bool = True) -> Path:
    """
    The folder for group `name` (validated). An existing group with the same
    name in another case is used as is. Created if missing (when create=True).
    """
    name = validate_group_name(name)
    existing = _entry_named(theme_folder, name)
    if existing is not None:
        path = Path(theme_folder) / existing
        if not path.is_dir():
            raise Conflict(f"A file named '{existing}' is in the way")
        return path
    path = Path(theme_folder) / name
    if create:
        try:
            path.mkdir()
        except OSError as e:
            raise GroupError(f"Couldn't create group folder: {e}") from e
    return path


def upload_path(theme_folder: Path, filename, group=None) -> Path:
    """
    Where an uploaded file goes: its base name only, in the theme folder or in
    group's folder (created if missing). InvalidName or Conflict.
    """
    name = safe_upload_name(filename)
    folder = group_target(theme_folder, group) if group else Path(theme_folder)
    return folder / name


# --- operations ---------------------------------------------------------------

def create_group(theme_folder: Path, name: str) -> str:
    """Create an empty group folder; returns its name. InvalidName or Conflict."""
    name = validate_group_name(name)
    existing = _entry_named(theme_folder, name)
    if existing is not None:
        raise Conflict(f"'{existing}' already exists")
    try:
        (Path(theme_folder) / name).mkdir()
    except FileExistsError as e:
        raise Conflict(f"'{name}' already exists") from e
    except OSError as e:
        raise GroupError(f"Couldn't create group folder: {e}") from e
    return name


def rename_group(theme_folder: Path, old: str, new: str) -> Change:
    """Rename a group folder. NotFound, InvalidName or Conflict."""
    theme_folder = Path(theme_folder)
    source = find_group(theme_folder, old)
    new = validate_group_name(new)
    if new == old:
        return Change()
    existing = _entry_named(theme_folder, new, exclude=old)
    if existing is not None:
        raise Conflict(f"'{existing}' already exists")
    keys = group_track_keys(theme_folder, old)
    target = theme_folder / new
    try:
        if new.casefold() == old.casefold():
            # Case-only rename: go through a temporary name (case-insensitive file systems)
            temp = theme_folder / f".rename-{os.getpid()}-{old}"
            os.rename(source, temp)
            try:
                os.rename(temp, target)
            except OSError:
                os.rename(temp, source)
                raise
        else:
            os.rename(source, target)
    except OSError as e:
        raise GroupError(f"Couldn't rename group folder: {e}") from e
    return Change(
        track_keys={k: f"{new}/{k.split('/', 1)[1]}" for k in keys},
        group_renames={old: new},
    )


def _move_files(moves: list[tuple[Path, Path]]) -> None:
    """Move every (source, target); on a failure undo the ones done and raise GroupError."""
    done: list[tuple[Path, Path]] = []
    for source, target in moves:
        try:
            if target.exists():
                raise OSError(f"'{target.name}' already exists")
            os.rename(source, target)
        except OSError as e:
            for s, t in reversed(done):
                try:
                    os.rename(t, s)
                except OSError:
                    pass
            raise GroupError(f"Couldn't move '{source.name}': {e}") from e
        done.append((source, target))


def delete_group(theme_folder: Path, name: str) -> Change:
    """
    Move a group's audio files up to the theme folder (renamed "x (2).wav" etc.
    on a clash) and remove the folder if it's then empty. Other files stay in
    the folder, and so does the folder. Audio files are never deleted.
    """
    theme_folder = Path(theme_folder)
    group = find_group(theme_folder, name)
    files = sorted((p for p in group.iterdir() if _is_audio(p)), key=lambda p: p.name)
    moves: list[tuple[Path, Path]] = []
    keys: dict[str, str] = {}
    planned: list[str] = []
    for f in files:
        new_name = _free_name_with(theme_folder, f.name, planned)
        planned.append(new_name)
        target = theme_folder / new_name
        moves.append((f, target))
        keys[f"{name}/{f.stem}"] = target.stem
    _move_files(moves)

    change = Change(track_keys=keys, group_renames={name: None})
    try:
        left = sorted(p.name for p in group.iterdir())
    except OSError:
        left = []
    if left:
        change.folder_removed = False
        change.left_behind = left
    else:
        try:
            group.rmdir()
        except OSError:
            change.folder_removed = False
    return change


def _free_name_with(folder: Path, filename: str, planned: list[str]) -> str:
    """free_file_name, also avoiding names already planned for this batch."""
    stem, ext = os.path.splitext(filename)
    planned_stems = {os.path.splitext(n)[0].casefold() for n in planned}
    candidate = free_file_name(folder, filename)
    n = 2
    while os.path.splitext(candidate)[0].casefold() in planned_stems:
        candidate = free_file_name(folder, f"{stem} ({n}){ext}")
        n += 1
    return candidate


def move_track(theme_folder: Path, key: str, group: Optional[str]) -> Change:
    """
    Move a track into group (created if missing) or, with group None, to the
    top level. Renamed "x (2).wav" etc. on a clash. Returns the key change
    (empty if it's already there).
    """
    theme_folder = Path(theme_folder)
    source = find_track_file(theme_folder, key)
    if group is None:
        target_folder = theme_folder
    else:
        target_folder = group_target(theme_folder, group, create=False)
    if source.parent == target_folder:
        return Change(group_renames={})
    created = not target_folder.exists()
    if created:
        try:
            target_folder.mkdir()
        except OSError as e:
            raise GroupError(f"Couldn't create group folder: {e}") from e
    target = target_folder / free_file_name(target_folder, source.name)
    try:
        _move_files([(source, target)])
    except GroupError:
        if created:
            try:
                target_folder.rmdir()
            except OSError:
                pass
        raise
    new_key = target.stem if target_folder == theme_folder else f"{target_folder.name}/{target.stem}"
    return Change(track_keys={key: new_key}, group_renames={})


# --- key migration ------------------------------------------------------------

def remap_key(key: str, track_keys: dict, group_renames: Optional[dict] = None) -> str:
    """The new key for key: an exact entry in track_keys, else its renamed group, else unchanged."""
    if key in track_keys:
        return track_keys[key]
    group, sep, rest = key.partition("/")
    if sep and group_renames and group_renames.get(group):
        return f"{group_renames[group]}/{rest}"
    return key


def remap_tracks(tracks: dict, track_keys: dict, group_renames: Optional[dict] = None) -> dict:
    """tracks with keys changed, order kept. A moved key wins over a stale key it lands on."""
    new_keys = {key: remap_key(key, track_keys, group_renames) for key in tracks}
    moved_targets = {new for key, new in new_keys.items() if new != key}
    result: dict = {}
    for key, value in tracks.items():
        new = new_keys[key]
        if new != key:
            result[new] = value
        elif key not in moved_targets:
            result[key] = value
        # else: a stale entry where a moved track now lands; the moved track's settings win
    return result


def remap_groups(groups: dict, group_renames: dict) -> dict:
    """groups with renamed groups renamed and removed (None) groups dropped, order kept."""
    result: dict = {}
    for name, value in groups.items():
        if name in group_renames:
            new = group_renames[name]
            if new is None:
                continue
            result[new] = value
        elif name not in result:
            result[name] = value
    return result


def migrate_presets(presets: dict, track_keys: dict, group_renames: Optional[dict] = None) -> dict:
    """A copy of presets ({id: preset}) with track keys and group names changed."""
    group_renames = group_renames or {}
    result = {}
    for preset_id, preset in (presets or {}).items():
        preset = copy.deepcopy(preset)
        if isinstance(preset, dict):
            if isinstance(preset.get("tracks"), dict):
                preset["tracks"] = remap_tracks(preset["tracks"], track_keys, group_renames)
            if isinstance(preset.get("groups"), dict) and group_renames:
                preset["groups"] = remap_groups(preset["groups"], group_renames)
        result[preset_id] = preset
    return result


def migrate_keys(metadata, presets: dict, change: Change) -> dict:
    """
    Apply a Change to metadata (its .tracks, .groups and .links, changed in
    place) and return the migrated presets ({id: preset}). Linked files follow
    a renamed group; a deleted group's links are dropped (never any file).
    """
    metadata.tracks = remap_tracks(dict(metadata.tracks or {}), change.track_keys, change.group_renames)
    metadata.groups = remap_groups(dict(metadata.groups or {}), change.group_renames)
    if getattr(metadata, "links", None) and change.group_renames:
        links = {}
        for key, link in metadata.links.items():
            group, _, rest = key.partition("/")
            if group in change.group_renames:
                if change.group_renames[group] is None:
                    continue
                key = f"{change.group_renames[group]}/{rest}"
            links[key] = link
        metadata.links = links
    return migrate_presets(presets, change.track_keys, change.group_renames)
