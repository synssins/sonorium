"""
Linked files (docs/THEME_FORMAT.md 2.6): audio files that live in another
theme's folder and are not copied. A link is stored in this theme's
metadata.json:

    "links": {"Rain": {"theme": "<source theme id>", "track": "Rain"},
              "Weather/Thunder": {"theme": "<source theme id>", "track": "Storm/Thunder"}}

A linked file is an ordinary track of the theme: ungrouped (key "<name>") or
in any group (key "<group>/<name>"), with the source's display name (" (2)",
" (3)", ... on a clash). It has its own entry in this theme's "tracks" and
presets save it like any other track. Moving it into or out of a group only
re-keys it; no file moves. The source is found by theme id, so renaming the
source theme's folder doesn't break the link. A link whose source theme or
file is gone is reported as missing and left out of playback, but kept.

Intrusion groups (groups[name]["mode"] = "intrusion") held the linked files
before; they are converted once when a theme loads (see
retire_intrusion_groups) and can't be made any more. A preset that turns a
linked file on still gets the ⚭ mark ("has_intrusion", see enables_intrusion).

Everything here works on ThemeMetadata objects and paths; the API saves.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Callable, Iterable, Optional

from sonorium.core.theme_groups import GroupError, find_track_file
from sonorium.theme_files import theme_audio_files, track_display_name, track_key

FolderForId = Callable[[str], Optional[Path]]


INTRUSION_MODE = "intrusion"  # recording.GROUP_MODE_INTRUSION: read from old files, never offered


def is_intrusion_group(group_settings: Optional[dict]) -> bool:
    return isinstance(group_settings, dict) and group_settings.get("mode") == INTRUSION_MODE


def intrusion_group_names(metadata) -> list[str]:
    return sorted(name for name, settings in (metadata.groups or {}).items() if is_intrusion_group(settings))


def link_group(key: str) -> Optional[str]:
    """The group of a track key ("Weather/Rain" -> "Weather"), None when it is ungrouped ("Rain")."""
    group, sep, _ = key.partition("/")
    return group if sep else None


def link_names(metadata, group: Optional[str]) -> set[str]:
    """The display names of the linked files in group (None: the ungrouped ones), taken for a file there."""
    return {track_display_name(k) for k in (metadata.links or {}) if link_group(k) == group}


def resolve_link(link: dict, folder_for_id: FolderForId) -> Optional[Path]:
    """The source file of a link, or None when its theme or file is gone."""
    try:
        folder = folder_for_id(link.get("theme"))
    except Exception:
        folder = None
    if folder is None:
        return None
    try:
        return find_track_file(folder, link.get("track"))
    except GroupError:
        return None


def folder_for_id_in(themes_root: Path) -> FolderForId:
    """A theme id -> folder lookup that reads the metadata.json files under themes_root."""
    ids: dict[str, Path] = {}
    try:
        folders = [p for p in Path(themes_root).iterdir() if p.is_dir()]
    except OSError:
        folders = []
    for folder in folders:
        try:
            data = json.loads((folder / "metadata.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("id"), str):
            ids.setdefault(data["id"], folder)
    return ids.get


def file_keys(folder: Path) -> list[str]:
    """The keys of a theme's own audio files."""
    folder = Path(folder)
    return [track_key(folder, f) for f in theme_audio_files(folder)]


def free_link_key(group: Optional[str], display: str, taken: Iterable[str]) -> str:
    """'<group>/<display>' ('<display>' ungrouped), with ' (2)', ' (3)', ... when taken (compared without case)."""
    used = {k.casefold() for k in taken}
    prefix = f"{group}/" if group else ""
    key = f"{prefix}{display}"
    n = 2
    while key.casefold() in used:
        key = f"{prefix}{display} ({n})"
        n += 1
    return key


def add_links(metadata, folder: Path, group: Optional[str], source_id: str, source_metadata,
              source_keys: list[str]) -> list[dict]:
    """
    Link source_keys (tracks of the theme source_id) into this theme's group,
    or ungrouped with group None. Each new linked track's settings start as a
    copy of the source theme's settings for it, muted, so the theme's own
    sound doesn't change until a preset turns it on; an ungrouped one is
    Unassigned (playback_mode "unassigned": not played until placed). A track
    already linked there is left as is.
    Returns [{"key", "theme", "track", "added": bool}] in the order given.
    """
    taken = set(file_keys(folder)) | set(metadata.tracks or {}) | set(metadata.links or {})
    results = []
    for source_key in source_keys:
        existing = next((k for k, link in metadata.links.items()
                         if link_group(k) == group and link == {"theme": source_id, "track": source_key}), None)
        if existing is not None:
            results.append({"key": existing, "theme": source_id, "track": source_key, "added": False})
            continue
        key = free_link_key(group, track_display_name(source_key), taken)
        taken.add(key)
        metadata.links[key] = {"theme": source_id, "track": source_key}
        source_settings = (source_metadata.tracks or {}).get(source_key) if source_metadata is not None else None
        settings = copy.deepcopy(source_settings) if source_settings is not None else None
        if settings is None:
            from sonorium.core.theme_metadata import TrackSettings
            settings = TrackSettings()
        settings.muted = True
        if group is None:
            settings.playback_mode = "unassigned"
        metadata.tracks[key] = settings
        results.append({"key": key, "theme": source_id, "track": source_key, "added": True})
    return results


def remove_link(metadata, key: str) -> bool:
    """Remove a link with its settings and preset entries (never a file). False if it is no link."""
    if key not in (metadata.links or {}):
        return False
    del metadata.links[key]
    (metadata.tracks or {}).pop(key, None)
    for preset in (metadata.presets or {}).values():
        if isinstance(preset, dict) and isinstance(preset.get("tracks"), dict):
            preset["tracks"].pop(key, None)
    return True


def remove_link_keep_settings(metadata, key: str) -> bool:
    """Drop only the link (its file was copied in): settings and preset entries stay for the new key."""
    if key not in (metadata.links or {}):
        return False
    del metadata.links[key]
    return True


def group_link_keys(metadata, group: str) -> list[str]:
    """The keys of a group's linked files."""
    return [k for k in (metadata.links or {}) if link_group(k) == group]


def regroup_link_keys(metadata, folder: Path, keys: Iterable[str], group: Optional[str],
                      taken: Iterable[str] = ()) -> dict:
    """
    New keys for linked files moved into group (None: ungrouped): "<group>/<name>"
    or "<name>", " (2)" etc. on a clash with a file, a track entry, another link
    or a key in `taken`. Returns {old key: new key} for the ones that change;
    nothing is changed here (theme_groups.migrate_keys applies it).
    """
    keys = [k for k in keys if k in (metadata.links or {})]
    used = (set(file_keys(folder)) | set(metadata.tracks or {}) | set(metadata.links or {}) | set(taken)) - set(keys)
    result = {}
    for key in keys:
        if link_group(key) == group:
            used.add(key)
            continue
        new = free_link_key(group, track_display_name(key), used)
        used.add(new)
        result[key] = new
    return result


def linked_track_keys(metadata, playable_links: Optional[set] = None) -> list[str]:
    """The keys of a theme's linked files (only the playable ones when playable_links is given)."""
    return [k for k in (metadata.links or {}) if playable_links is None or k in playable_links]


def enables_intrusion(metadata, keys: Iterable[str], preset: Optional[dict] = None) -> bool:
    """
    The ⚭ mark: True when, with the preset's settings over the theme's own (or
    the theme's own alone when preset is None), at least one of keys (the
    linked files, see linked_track_keys) is on: the track not muted and its
    group, if it has one, not muted.
    """
    preset = preset if isinstance(preset, dict) else {}
    preset_tracks = preset.get("tracks") if isinstance(preset.get("tracks"), dict) else {}
    preset_groups = preset.get("groups") if isinstance(preset.get("groups"), dict) else {}
    for key in keys:
        group = link_group(key)
        if group is not None:
            group_muted = bool(((metadata.groups or {}).get(group) or {}).get("muted", False))
            saved = preset_groups.get(group)
            if isinstance(saved, dict) and "muted" in saved:
                group_muted = bool(saved["muted"])
            if group_muted:
                continue
        settings = (metadata.tracks or {}).get(key)
        muted = bool(getattr(settings, "muted", False))
        saved = preset_tracks.get(key)
        if isinstance(saved, dict) and "muted" in saved:
            muted = bool(saved["muted"])
        if not muted:
            return True
    return False


def retire_intrusion_groups(metadata, folder: Path, sequences: dict):
    """
    The one-time conversion of a theme's intrusion groups (they were the only
    place for linked files): each one's linked files become ungrouped (their
    own playback mode kept), then the group is removed as the "delete group"
    API removes one (its own files move up to the theme folder, renamed
    " (2)" on a clash). Keys follow in metadata (tracks, links, groups and
    metadata.presets) and in `sequences` (presets.json "sequences").
    Moves files: the caller backs up the JSON files first.
    Returns (groups converted, migrated sequences, error): error is the
    GroupError of a group whose files couldn't be moved (it and the groups
    after it are left as they are; the ones before stay converted), else None.
    """
    from sonorium.core import theme_groups
    folder = Path(folder)
    converted = []
    for name in intrusion_group_names(metadata):
        try:
            change = theme_groups.delete_group(folder, name, reserved=link_names(metadata, None))
        except theme_groups.NotFound:
            change = theme_groups.Change(group_renames={name: None})  # only links, no folder
        except theme_groups.GroupError as e:
            return converted, sequences, e
        change.track_keys.update(regroup_link_keys(metadata, folder, group_link_keys(metadata, name), None,
                                                   taken=change.track_keys.values()))
        metadata.presets = theme_groups.migrate_keys(metadata, metadata.presets or {}, change)
        sequences = theme_groups.migrate_sequences(sequences, change.track_keys, change.group_renames)
        converted.append(name)
    return converted, sequences, None


def export_entries(theme_path: Path, metadata_doc: dict, folder_for_id: Optional[FolderForId] = None):
    """
    For a self-contained export: drops "links" from metadata_doc (in place)
    and returns [(path inside the theme, source file)] for every linked file
    that can be found, stored as a normal file where it sits (the theme folder
    or its group's folder), so its settings (kept under the same key) apply to
    it. A link whose source is missing, or whose name a file there already
    has, is left out. Only the linked files are added, never the rest of
    another theme.
    """
    theme_path = Path(theme_path)
    links = metadata_doc.pop("links", None) or {}
    if not links:
        return []
    if folder_for_id is None:
        folder_for_id = folder_for_id_in(theme_path.parent)
    taken = {k.casefold() for k in file_keys(theme_path)}
    entries = []
    for key, link in links.items():
        source = resolve_link(link, folder_for_id) if isinstance(link, dict) else None
        if source is None or key.casefold() in taken:
            continue
        taken.add(key.casefold())
        entries.append((f"{key}{source.suffix.lower()}", source))
    return entries
