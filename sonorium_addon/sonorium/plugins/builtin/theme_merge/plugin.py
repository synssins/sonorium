"""
Theme Merge Plugin

Merge two themes together into a new theme or into one of the existing themes
(Themes 2.0, docs/THEME_FORMAT.md).

What is carried over from each theme merged in:
- every audio file, keyed by its track key, in its group folder ("Group/Name")
  with the track's settings; a key the target already has is skipped (the
  target's track is used) or the file is copied as "Name (2)"
- group settings, for groups the target doesn't have settings for
- linked files: a link to the theme the files go into, or to a theme merged
  in, becomes that track; a link to any other theme stays a link
- presets, renamed "Name (Theme)" on a name clash, with their track and
  group keys following; tracks a preset didn't have are muted in it, and
  the target's own presets get the new tracks muted, so every preset sounds
  as before
Sequences are not carried over (they belong to the source's presets).

Everything is read and checked before anything is written, and the theme
files are written through plugins/theme_api.py: a merge either completes or
leaves both themes as they were.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from sonorium.plugins.base import BasePlugin
from sonorium.obs import logger

TARGETS = ("__new__", "__source1__", "__source2__")
DUPLICATE_MODES = ("skip", "rename")


class ThemeMergePlugin(BasePlugin):
    """Plugin for merging two themes together."""

    id = "theme_merge"
    name = "Theme Merge"
    version = "2.0.0"
    description = "Merge two themes together - combines tracks, groups and presets"
    author = "Sonorium"
    builtin = True  # Shipped with Sonorium

    def get_ui_schema(self) -> dict:
        """Return the UI schema for theme merge."""
        themes = self.list_themes()

        # Source theme options (required)
        source_options = [{"value": "", "label": "-- Select a theme --"}]
        source_options.extend([
            {"value": t["id"], "label": f"{t['name']} ({t['track_count']} tracks, {t['preset_count']} presets)"}
            for t in themes
        ])

        # Target options - can create new or merge into existing
        target_options = [
            {"value": "__new__", "label": "Create new merged theme"},
            {"value": "__source1__", "label": "Merge into Source Theme 1"},
            {"value": "__source2__", "label": "Merge into Source Theme 2"},
        ]

        return {
            "type": "form",
            "fields": [
                {
                    "name": "source_theme_1",
                    "type": "select",
                    "label": "Source Theme 1",
                    "required": True,
                    "options": source_options,
                    "help": "First theme to merge",
                },
                {
                    "name": "source_theme_2",
                    "type": "select",
                    "label": "Source Theme 2",
                    "required": True,
                    "options": source_options,
                    "help": "Second theme to merge",
                },
                {
                    "name": "target",
                    "type": "select",
                    "label": "Merge Target",
                    "required": True,
                    "options": target_options,
                    "default": "__new__",
                    "help": "Where to put the merged result",
                },
                {
                    "name": "new_theme_name",
                    "type": "string",
                    "label": "New Theme Name",
                    "required": False,
                    "placeholder": "Leave blank to auto-generate (e.g. 'Theme1 + Theme2')",
                    "help": "Only used when creating a new theme",
                },
                {
                    "name": "handle_duplicates",
                    "type": "select",
                    "label": "Duplicate Files",
                    "required": True,
                    "options": [
                        {"value": "skip", "label": "Skip - keep existing file"},
                        {"value": "rename", "label": "Rename - add suffix to new file"},
                    ],
                    "default": "skip",
                    "help": "How to handle tracks with the same name",
                },
            ],
            "actions": [
                {
                    "id": "merge",
                    "label": "Merge Themes",
                    "primary": True,
                },
                {
                    "id": "refresh_themes",
                    "label": "Refresh Theme List",
                    "primary": False,
                },
            ],
        }

    async def handle_action(self, action: str, data: dict) -> dict:
        """Handle plugin actions."""
        if action == "merge":
            return await self._merge_themes(data)
        elif action == "refresh_themes":
            return {
                "success": True,
                "message": "Theme list refreshed",
                "refresh_ui": True,
            }
        return {"success": False, "message": f"Unknown action: {action}"}

    # =========================================================================
    # Merge
    # =========================================================================

    async def _merge_themes(self, data: dict) -> dict:
        """Merge two themes together."""
        from sonorium.plugins.theme_api import ThemeEdit, ThemeEditError, find_theme
        from sonorium.core.theme_metadata import load_theme_folder

        def text(name, default=""):
            value = data.get(name, default)
            return str(value).strip() if value is not None else default

        source1_id = text("source_theme_1")
        source2_id = text("source_theme_2")
        target = text("target", "__new__") or "__new__"
        new_theme_name = text("new_theme_name")
        handle_duplicates = text("handle_duplicates", "skip") or "skip"

        # Validate inputs
        if not source1_id:
            return {"success": False, "message": "Please select Source Theme 1"}
        if not source2_id:
            return {"success": False, "message": "Please select Source Theme 2"}
        if target not in TARGETS:
            return {"success": False, "message": f"Invalid target: {target}"}
        if handle_duplicates not in DUPLICATE_MODES:
            return {"success": False, "message": f"Invalid duplicate handling: {handle_duplicates} (use skip or rename)"}

        folder1 = find_theme(self.audio_path, source1_id)
        folder2 = find_theme(self.audio_path, source2_id)
        if folder1 is None:
            return {"success": False, "message": f"Source Theme 1 not found: {source1_id}"}
        if folder2 is None:
            return {"success": False, "message": f"Source Theme 2 not found: {source2_id}"}
        if folder1 == folder2:
            return {"success": False, "message": "Please select two different themes to merge"}

        try:
            # Read and check everything first; nothing is written until commit()
            sources = []
            for folder in (folder1, folder2):
                metadata = load_theme_folder(folder)
                if metadata.problems:
                    return {"success": False,
                            "message": f"Theme '{metadata.name}' has problems ({'; '.join(metadata.problems)}); "
                                       "open it in Sonorium and check it first"}
                sources.append((metadata, folder))
            (meta1, _), (meta2, _) = sources

            if target == "__new__":
                name = new_theme_name or f"{meta1.name} + {meta2.name}"
                edit = ThemeEdit.new(self.audio_path, name,
                                     description=f"Merged from: {meta1.name} and {meta2.name}")
                to_copy = sources
            elif target == "__source1__":
                edit = ThemeEdit.open(self.audio_path, meta1.id)
                to_copy = [sources[1]]
            else:
                edit = ThemeEdit.open(self.audio_path, meta2.id)
                to_copy = [sources[0]]

            stats = {"tracks_copied": 0, "tracks_skipped": 0, "tracks_renamed": 0,
                     "links_kept": 0, "presets_merged": 0, "presets_renamed": 0}
            warnings: list[str] = []

            # Files first (links may point at another source's files)
            key_maps = {}
            for metadata, folder in to_copy:
                key_maps[metadata.id] = self._copy_files(edit, metadata, folder, handle_duplicates, stats)
            group_maps = {}
            for metadata, folder in to_copy:
                self._map_links(edit, metadata, key_maps, stats, warnings)
                group_maps[metadata.id] = self._copy_groups(edit, metadata, warnings)
            for metadata, folder in to_copy:
                self._copy_presets(edit, metadata, key_maps[metadata.id], group_maps[metadata.id], stats, warnings)
                if self._has_sequences(folder):
                    warnings.append(f"Sequences of '{metadata.name}' were not merged")

            result = edit.commit()

        except ThemeEditError as e:
            return {"success": False, "message": f"Nothing was merged: {e}"}
        except Exception as e:
            logger.error(f"Error merging themes: {e}", exc_info=True)
            return {"success": False, "message": f"Nothing was merged: {type(e).__name__}: {e}"}

        # Build result message
        parts = []
        if stats["tracks_copied"]:
            parts.append(f"{stats['tracks_copied']} tracks copied")
        if stats["tracks_skipped"]:
            parts.append(f"{stats['tracks_skipped']} skipped")
        if stats["tracks_renamed"]:
            parts.append(f"{stats['tracks_renamed']} renamed")
        if stats["links_kept"]:
            parts.append(f"{stats['links_kept']} linked files")
        if stats["presets_merged"]:
            parts.append(f"{stats['presets_merged']} presets merged")
        if stats["presets_renamed"]:
            parts.append(f"{stats['presets_renamed']} presets renamed")

        theme_name = result["name"]
        message = f"Created merged theme '{theme_name}'" if result["is_new"] else f"Merged into '{theme_name}'"
        if parts:
            message += f": {', '.join(parts)}"

        return {
            "success": True,
            "message": message,
            "refresh_themes": True,  # Signal API to auto-refresh themes
            "warnings": warnings if warnings else None,
            "data": {
                "theme_id": result["theme_id"],
                "theme_name": theme_name,
                "theme_path": str(result["folder"]),
                "is_new_theme": result["is_new"],
                "stats": stats,
            },
        }

    # =========================================================================
    # Steps (each only plans changes on the ThemeEdit)
    # =========================================================================

    def _copy_files(self, edit, metadata, folder: Path, handle_duplicates: str, stats: dict) -> dict:
        """Plan copying a source's audio files. Returns source key -> key in the target."""
        from sonorium.core.theme_metadata import TrackSettings
        from sonorium.plugins.theme_api import resolve_auto
        from sonorium.theme_files import theme_audio_files, track_group, track_key

        key_map = {}
        threshold = self._threshold(metadata)
        for path in theme_audio_files(folder):
            key = track_key(folder, path)
            group = track_group(folder, path)
            existing = self._existing_key(edit, edit.group_name(group) + "/" + path.stem if group else key)
            if existing is not None and handle_duplicates == "skip":
                key_map[key] = existing
                stats["tracks_skipped"] += 1
                continue
            settings = resolve_auto(key, metadata.tracks.get(key) or TrackSettings(), path, threshold)
            new_key = edit.add_file(path, path.name, group=group, settings=self._track_settings(settings))
            key_map[key] = new_key
            stats["tracks_copied"] += 1
            if existing is not None:
                stats["tracks_renamed"] += 1
        return key_map

    def _map_links(self, edit, metadata, key_maps: dict, stats: dict, warnings: list) -> None:
        """
        A source's linked files: a link to the target theme or to a theme
        merged in becomes that track; a link to another theme stays a link.
        """
        from sonorium.core import intrusions
        from sonorium.core.theme_metadata import TrackSettings
        from sonorium.plugins.theme_api import resolve_auto

        key_map = key_maps[metadata.id]
        folder_for_id = intrusions.folder_for_id_in(self.audio_path)
        for key, link in (metadata.links or {}).items():
            theme_id, track = link["theme"], link["track"]
            if theme_id == edit.theme_id:
                found = self._existing_key(edit, track)
                if found is not None:
                    key_map[key] = found
                else:
                    warnings.append(f"Linked file '{key}' of '{metadata.name}' was not found and was left out")
                continue
            if theme_id in key_maps:
                if track in key_maps[theme_id]:
                    key_map[key] = key_maps[theme_id][track]
                else:
                    warnings.append(f"Linked file '{key}' of '{metadata.name}' was not found and was left out")
                continue
            group = intrusions.link_group(key)
            same = next((k for k, l in edit.metadata.links.items()
                         if l == {"theme": theme_id, "track": track} and intrusions.link_group(k) == group), None)
            if same is not None:
                key_map[key] = same
                continue
            path = intrusions.resolve_link(link, folder_for_id)
            settings = resolve_auto(key, metadata.tracks.get(key) or TrackSettings(muted=True), path,
                                    self._threshold(metadata))
            key_map[key] = edit.add_link(theme_id, track, group=group, settings=self._track_settings(settings))
            stats["links_kept"] += 1

    def _copy_groups(self, edit, metadata, warnings: list) -> dict:
        """A source's group settings, for groups the target has none for. Returns source group -> target group."""
        from sonorium.plugins.theme_api import ThemeEditError

        before = {name.casefold(): name for name in edit.metadata.groups}
        group_map = {}
        names = list(metadata.groups or {})
        names += [key.split("/", 1)[0] for key in metadata.tracks if "/" in key and key.split("/", 1)[0] not in names]
        for name in names:
            try:
                target_name = edit.group_name(name)
            except ThemeEditError as e:
                warnings.append(f"Group '{name}' of '{metadata.name}' was left out: {e}")
                continue
            group_map[name] = target_name
            settings = (metadata.groups or {}).get(name)
            if not isinstance(settings, dict):
                continue
            existing = before.get(target_name.casefold())
            if existing is not None:
                if (edit.metadata.groups[existing] or {}).get("mode") != settings.get("mode"):
                    warnings.append(f"Group '{existing}' keeps its settings; the tracks from "
                                    f"'{metadata.name}' play as it does")
                continue
            if settings.get("mode") == "intrusion":
                settings = {k: v for k, v in settings.items() if k != "mode"}
            edit.set_group(target_name, settings)
        return group_map

    def _copy_presets(self, edit, metadata, key_map: dict, group_map: dict, stats: dict, warnings: list) -> None:
        """A source's presets, with their keys following and a name clash renamed "Name (Theme)"."""
        from sonorium.plugins.theme_api import PRESET_GROUP_FIELDS, PRESET_TRACK_FIELDS

        first_default = True
        for preset in (metadata.presets or {}).values():
            if not isinstance(preset, dict):
                continue
            name = str(preset.get("name") or "Preset").strip() or "Preset"
            if edit.free_preset_name(name) != name:
                name = edit.free_preset_name(f"{name} ({metadata.name})")
                stats["presets_renamed"] += 1

            tracks = {}
            for key, entry in (preset.get("tracks") or {}).items():
                if key not in key_map or not isinstance(entry, dict):
                    continue
                clean = self._clean(entry, PRESET_TRACK_FIELDS)
                if clean.get("playback_mode") == "auto" or (
                        "playback_mode" not in clean and getattr(metadata.tracks.get(key), "playback_mode", None) == "auto"):
                    clean.update(self._resolved_entry(metadata, key, clean))
                tracks.setdefault(key_map[key], clean)
            groups = {}
            for group, entry in (preset.get("groups") or {}).items():
                if group in group_map and isinstance(entry, dict):
                    groups[group_map[group]] = self._clean(entry, PRESET_GROUP_FIELDS)

            # The source's default may become the target's default, if it has none
            is_default = None if (preset.get("is_default") and first_default) else False
            if preset.get("is_default"):
                first_default = False
            edit.add_preset(name, tracks, groups=groups or None, is_default=is_default)
            stats["presets_merged"] += 1

    # =========================================================================
    # Helpers
    # =========================================================================

    @staticmethod
    def _existing_key(edit, key: str) -> Optional[str]:
        """The target's key equal to key without case, if any."""
        lowered = key.casefold()
        return next((k for k in edit.keys() if k.casefold() == lowered), None)

    @staticmethod
    def _threshold(metadata) -> float:
        try:
            return float(metadata.short_file_threshold)
        except (TypeError, ValueError):
            return 15.0

    @staticmethod
    def _track_settings(settings) -> dict:
        """A TrackSettings as the dict ThemeEdit takes."""
        return {k: v for k, v in settings.to_dict().items()}

    @staticmethod
    def _clean(entry: dict, fields: tuple) -> dict:
        """Only the known fields of a preset entry whose values are usable (others are dropped)."""
        from sonorium.plugins.theme_api import ThemeEditError, _check_field

        clean = {}
        for name in fields:
            if name not in entry:
                continue
            if name == "playback_mode" and entry[name] == "auto":
                clean[name] = "auto"  # resolved by the caller
                continue
            try:
                clean[name] = _check_field(name, entry[name])
            except ThemeEditError:
                pass
        return clean

    def _resolved_entry(self, metadata, key: str, entry: dict) -> dict:
        """playback_mode (and presence) for a preset entry that plays as "auto"."""
        from sonorium.core import theme_groups
        from sonorium.core.theme_metadata import TrackSettings
        from sonorium.plugins.theme_api import resolve_auto

        base = metadata.tracks.get(key) or TrackSettings()
        settings = TrackSettings.from_dict({**base.to_dict(), **entry, "playback_mode": "auto"})
        folder = self._folder_of(metadata)
        try:
            path = theme_groups.find_track_file(folder, key) if folder else None
        except theme_groups.GroupError:
            path = None
        resolved = resolve_auto(key, settings, path, self._threshold(metadata))
        return {"playback_mode": resolved.playback_mode, "presence": resolved.presence}

    def _folder_of(self, metadata) -> Optional[Path]:
        from sonorium.plugins.theme_api import find_theme
        return find_theme(self.audio_path, metadata.id)

    @staticmethod
    def _has_sequences(folder: Path) -> bool:
        from sonorium.core.theme_presets import BrokenJsonError, load_presets_doc
        try:
            return bool(load_presets_doc(folder).get("sequences"))
        except BrokenJsonError:
            return False
