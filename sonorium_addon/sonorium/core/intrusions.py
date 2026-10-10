"""
Intrusion groups and linked files (docs/THEME_FORMAT.md 2.6).

An intrusion group is a group in Intrusion mode (groups[name]["mode"] =
"intrusion"): its tracks play as their own settings say, like ungrouped
tracks (no turns, no bed). Besides its own files it can hold linked files: audio files that live in another
theme's folder and are not copied. A link is stored in this theme's
metadata.json:

    "links": {"Weather/Rain": {"theme": "<source theme id>", "track": "Rain"}}

The key is "<group>/<source display name>" (" (2)", " (3)", ... on a clash).
A linked track has its own entry in this theme's "tracks" and presets save
it like any other track. The source is found by theme id, so renaming the
source theme's folder doesn't break the link. A link whose source theme or
file is gone is reported as missing and left out of playback, but kept.

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


INTRUSION_MODE = "intrusion"  # recording.GROUP_MODE_INTRUSION


def is_intrusion_group(group_settings: Optional[dict]) -> bool:
    return isinstance(group_settings, dict) and group_settings.get("mode") == INTRUSION_MODE


def intrusion_group_names(metadata) -> list[str]:
    return sorted(name for name, settings in (metadata.groups or {}).items() if is_intrusion_group(settings))


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


def free_link_key(group: str, display: str, taken: Iterable[str]) -> str:
    """'<group>/<display>', or with ' (2)', ' (3)', ... when that key is taken (compared without case)."""
    used = {k.casefold() for k in taken}
    key = f"{group}/{display}"
    n = 2
    while key.casefold() in used:
        key = f"{group}/{display} ({n})"
        n += 1
    return key


def add_links(metadata, folder: Path, group: str, source_id: str, source_metadata,
              source_keys: list[str]) -> list[dict]:
    """
    Link source_keys (tracks of the theme source_id) into this theme's group.
    Each new linked track's settings start as a copy of the source theme's
    settings for it, muted, so the theme's own sound doesn't change until a
    preset turns it on. A track already linked into the group is left as is.
    Returns [{"key", "theme", "track", "added": bool}] in the order given.
    """
    taken = set(file_keys(folder)) | set(metadata.tracks or {}) | set(metadata.links or {})
    results = []
    for source_key in source_keys:
        existing = next((k for k, link in metadata.links.items()
                         if k.split("/", 1)[0] == group and link == {"theme": source_id, "track": source_key}), None)
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


def intrusion_track_keys(metadata, local_keys: Iterable[str], playable_links: Optional[set] = None) -> dict:
    """
    {intrusion group: [its track keys]}: its own files plus its links (only
    the playable ones when playable_links is given).
    """
    groups = intrusion_group_names(metadata)
    result = {g: [] for g in groups}
    for key in local_keys:
        group = key.split("/", 1)[0] if "/" in key else None
        if group in result:
            result[group].append(key)
    for key in metadata.links or {}:
        group = key.split("/", 1)[0]
        if group in result and (playable_links is None or key in playable_links) and key not in result[group]:
            result[group].append(key)
    return result


def enables_intrusion(metadata, keys_by_group: dict, preset: Optional[dict] = None) -> bool:
    """
    True when, with the preset's settings over the theme's own (or the theme's
    own alone when preset is None), at least one intrusion-group track is on:
    the track not muted and its group not muted.
    """
    preset = preset if isinstance(preset, dict) else {}
    preset_tracks = preset.get("tracks") if isinstance(preset.get("tracks"), dict) else {}
    preset_groups = preset.get("groups") if isinstance(preset.get("groups"), dict) else {}
    for group, keys in keys_by_group.items():
        group_muted = bool(((metadata.groups or {}).get(group) or {}).get("muted", False))
        saved = preset_groups.get(group)
        if isinstance(saved, dict) and "muted" in saved:
            group_muted = bool(saved["muted"])
        if group_muted:
            continue
        for key in keys:
            settings = (metadata.tracks or {}).get(key)
            muted = bool(getattr(settings, "muted", False))
            saved = preset_tracks.get(key)
            if isinstance(saved, dict) and "muted" in saved:
                muted = bool(saved["muted"])
            if not muted:
                return True
    return False


def export_entries(theme_path: Path, metadata_doc: dict, folder_for_id: Optional[FolderForId] = None):
    """
    For a self-contained export: drops "links" from metadata_doc (in place)
    and returns [(path inside the theme, source file)] for every linked file
    that can be found, stored as a normal file in its group folder, so its
    settings (kept under the same key) apply to it. A link whose source is
    missing, or whose name a file of the group already has, is left out.
    Only the linked files are added, never the rest of another theme.
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
        group, display = key.split("/", 1)
        entries.append((f"{group}/{display}{source.suffix.lower()}", source))
    return entries
