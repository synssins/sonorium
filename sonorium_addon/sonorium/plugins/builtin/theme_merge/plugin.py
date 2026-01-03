"""
Theme Merge Plugin

Merge two themes together into a new theme or into one of the existing themes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Optional

from sonorium.plugins.base import BasePlugin
from sonorium.obs import logger


class ThemeMergePlugin(BasePlugin):
    """Plugin for merging two themes together."""

    id = "theme_merge"
    name = "Theme Merge"
    version = "1.0.0"
    description = "Merge two themes together - combines tracks and presets"
    author = "Sonorium"
    builtin = False  # Allow users to delete this plugin

    def get_ui_schema(self) -> dict:
        """Return the UI schema for theme merge."""
        themes = self._list_existing_themes()

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
                        {"value": "overwrite", "label": "Overwrite - replace existing file"},
                    ],
                    "default": "skip",
                    "help": "How to handle files with the same name",
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

    def _list_existing_themes(self) -> list[dict]:
        """List all existing themes with metadata."""
        themes = []
        if not self.audio_path or not self.audio_path.exists():
            return themes

        for folder in sorted(self.audio_path.iterdir()):
            if not folder.is_dir():
                continue
            if folder.name.startswith('.'):
                continue

            theme_info = {
                "id": folder.name,
                "name": folder.name,
                "path": str(folder),
                "track_count": 0,
                "preset_count": 0,
                "tracks": [],
                "presets": [],
            }

            # Count audio files
            audio_extensions = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac'}
            for f in folder.iterdir():
                if f.is_file() and f.suffix.lower() in audio_extensions:
                    theme_info["track_count"] += 1
                    theme_info["tracks"].append(f.name)

            # Load metadata for presets
            metadata_path = folder / "metadata.json"
            if metadata_path.exists():
                try:
                    with open(metadata_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    theme_info["name"] = meta.get("name", folder.name)
                    presets = meta.get("presets", [])
                    theme_info["preset_count"] = len(presets)
                    theme_info["presets"] = presets
                    theme_info["metadata"] = meta
                except Exception as e:
                    logger.warning(f"Error loading metadata for {folder.name}: {e}")

            themes.append(theme_info)

        return themes

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

    async def _merge_themes(self, data: dict) -> dict:
        """Merge two themes together."""
        try:
            # Safely extract and convert inputs to strings
            source1_raw = data.get("source_theme_1", "")
            source2_raw = data.get("source_theme_2", "")
            target_raw = data.get("target", "__new__")
            new_name_raw = data.get("new_theme_name", "")
            duplicates_raw = data.get("handle_duplicates", "skip")

            # Convert to strings and strip whitespace
            source1_id = str(source1_raw).strip() if source1_raw is not None else ""
            source2_id = str(source2_raw).strip() if source2_raw is not None else ""
            target = str(target_raw).strip() if target_raw is not None else "__new__"
            new_theme_name = str(new_name_raw).strip() if new_name_raw is not None else ""
            handle_duplicates = str(duplicates_raw).strip() if duplicates_raw is not None else "skip"

            # Validate inputs
            if not source1_id:
                return {"success": False, "message": "Please select Source Theme 1"}
            if not source2_id:
                return {"success": False, "message": "Please select Source Theme 2"}
            if source1_id == source2_id:
                return {"success": False, "message": "Please select two different themes to merge"}

            # Load theme data
            all_themes = {str(t["id"]): t for t in self._list_existing_themes()}

            if source1_id not in all_themes:
                return {"success": False, "message": f"Source Theme 1 not found: {source1_id}"}
            if source2_id not in all_themes:
                return {"success": False, "message": f"Source Theme 2 not found: {source2_id}"}

            source1 = all_themes[source1_id]
            source2 = all_themes[source2_id]
            source1_path = Path(source1["path"])
            source2_path = Path(source2["path"])

            # Determine target
            if target == "__new__":
                # Create new theme
                if not new_theme_name:
                    new_theme_name = f"{source1['name']} + {source2['name']}"

                theme_name = self._sanitize_filename(new_theme_name)
                theme_path = self.audio_path / theme_name

                # Handle existing folder
                if theme_path.exists():
                    counter = 1
                    while (self.audio_path / f"{theme_name}_{counter}").exists():
                        counter += 1
                    theme_name = f"{theme_name}_{counter}"
                    theme_path = self.audio_path / theme_name

                theme_path.mkdir(parents=True, exist_ok=True)

                # Start with empty metadata
                target_metadata = {
                    "name": new_theme_name,
                    "description": f"Merged from: {source1['name']} and {source2['name']}",
                    "tracks": [],
                    "presets": [],
                }
                is_new_theme = True

                # For new theme, we copy from both sources
                sources_to_copy = [source1, source2]

            elif target == "__source1__":
                # Merge into source1 (copy from source2)
                theme_name = source1["name"]
                theme_path = source1_path
                target_metadata = self._load_metadata(theme_path)
                is_new_theme = False
                sources_to_copy = [source2]  # Only copy from source2

            elif target == "__source2__":
                # Merge into source2 (copy from source1)
                theme_name = source2["name"]
                theme_path = source2_path
                target_metadata = self._load_metadata(theme_path)
                is_new_theme = False
                sources_to_copy = [source1]  # Only copy from source1

            else:
                return {"success": False, "message": f"Invalid target: {target}"}

            # Track statistics
            stats = {
                "tracks_copied": 0,
                "tracks_skipped": 0,
                "tracks_renamed": 0,
                "presets_merged": 0,
                "presets_skipped": 0,
            }
            warnings = []

            # Get existing files in target
            existing_files = set(f.name.lower() for f in theme_path.iterdir() if f.is_file())

            # Build track ID mapping for preset adjustment
            track_id_map = {}  # old_id -> new_id (for renamed files)

            # Copy tracks from each source
            for source in sources_to_copy:
                source_path = Path(source["path"])

                for track_file in source["tracks"]:
                    src_file = source_path / track_file
                    if not src_file.exists():
                        continue

                    dest_file = theme_path / track_file
                    file_lower = track_file.lower()
                    original_track_id = self._generate_track_id(track_file)
                    new_track_file = track_file

                    if file_lower in existing_files:
                        if handle_duplicates == "skip":
                            stats["tracks_skipped"] += 1
                            # Map to existing file's track ID
                            track_id_map[original_track_id] = self._generate_track_id(track_file)
                            continue
                        elif handle_duplicates == "rename":
                            # Generate unique name
                            stem = src_file.stem
                            suffix = src_file.suffix
                            counter = 1
                            while f"{stem}_{counter}{suffix}".lower() in existing_files:
                                counter += 1
                            new_track_file = f"{stem}_{counter}{suffix}"
                            dest_file = theme_path / new_track_file
                            stats["tracks_renamed"] += 1
                        # else overwrite - continue with same dest_file

                    try:
                        shutil.copy2(src_file, dest_file)
                        existing_files.add(new_track_file.lower())
                        stats["tracks_copied"] += 1

                        # Track the ID mapping
                        new_track_id = self._generate_track_id(new_track_file)
                        track_id_map[original_track_id] = new_track_id

                        # Add to tracks list in metadata
                        target_metadata.setdefault("tracks", []).append({
                            "id": new_track_id,
                            "file": new_track_file,
                            "name": Path(new_track_file).stem,
                        })
                    except Exception as e:
                        warnings.append(f"Failed to copy {track_file}: {e}")

            # Merge presets from sources
            existing_preset_names = set()
            for p in target_metadata.get("presets", []):
                if isinstance(p, dict):
                    existing_preset_names.add(p.get("name", "").lower())

            for source in sources_to_copy:
                source_presets = source.get("presets", [])

                for preset in source_presets:
                    # Skip non-dict presets
                    if not isinstance(preset, dict):
                        continue

                    preset_name = preset.get("name", "Unnamed")

                    # Check for duplicate preset name
                    if preset_name.lower() in existing_preset_names:
                        if handle_duplicates == "skip":
                            stats["presets_skipped"] += 1
                            continue
                        elif handle_duplicates in ("rename", "overwrite"):
                            # Rename the preset
                            counter = 1
                            new_name = f"{preset_name} ({source['name']})"
                            while new_name.lower() in existing_preset_names:
                                counter += 1
                                new_name = f"{preset_name} ({source['name']} {counter})"
                            preset_name = new_name

                    # Create new preset with remapped track IDs
                    new_preset = {
                        "id": str(uuid.uuid4()),
                        "name": preset_name,
                        "is_default": False,
                        "tracks": {},
                    }

                    # Remap track references
                    old_tracks = preset.get("tracks", {})
                    if isinstance(old_tracks, dict):
                        for old_track_id, track_settings in old_tracks.items():
                            new_track_id = track_id_map.get(old_track_id, old_track_id)
                            # Handle track_settings being dict or simple value
                            if isinstance(track_settings, dict):
                                new_preset["tracks"][new_track_id] = track_settings.copy()
                            else:
                                # Simple value (e.g., just volume)
                                new_preset["tracks"][new_track_id] = {"volume": track_settings, "enabled": True}

                    target_metadata.setdefault("presets", []).append(new_preset)
                    existing_preset_names.add(preset_name.lower())
                    stats["presets_merged"] += 1

            # Ensure at least one default preset
            presets = target_metadata.get("presets", [])
            if presets:
                has_default = any(isinstance(p, dict) and p.get("is_default") for p in presets)
                if not has_default and isinstance(presets[0], dict):
                    presets[0]["is_default"] = True

            # If no presets exist, create a default one with all tracks
            if not presets:
                default_preset = {
                    "id": str(uuid.uuid4()),
                    "name": "Default",
                    "is_default": True,
                    "tracks": {},
                }
                for track in target_metadata.get("tracks", []):
                    default_preset["tracks"][track["id"]] = {
                        "volume": 0.7,
                        "enabled": True,
                    }
                target_metadata["presets"] = [default_preset]

            # Save metadata
            metadata_path = theme_path / "metadata.json"
            with open(metadata_path, 'w', encoding='utf-8') as f:
                json.dump(target_metadata, f, indent=2, ensure_ascii=False)

            # Build result message
            parts = []
            if stats["tracks_copied"]:
                parts.append(f"{stats['tracks_copied']} tracks copied")
            if stats["tracks_skipped"]:
                parts.append(f"{stats['tracks_skipped']} skipped")
            if stats["tracks_renamed"]:
                parts.append(f"{stats['tracks_renamed']} renamed")
            if stats["presets_merged"]:
                parts.append(f"{stats['presets_merged']} presets merged")
            if stats["presets_skipped"]:
                parts.append(f"{stats['presets_skipped']} presets skipped")

            if is_new_theme:
                message = f"Created merged theme '{theme_name}'"
            else:
                message = f"Merged into '{theme_name}'"

            if parts:
                message += f": {', '.join(parts)}"

            return {
                "success": True,
                "message": message,
                "refresh_themes": True,  # Signal API to auto-refresh themes
                "warnings": warnings if warnings else None,
                "data": {
                    "theme_name": theme_name,
                    "theme_path": str(theme_path),
                    "is_new_theme": is_new_theme,
                    "stats": stats,
                },
            }

        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            logger.error(f"Error merging themes: {e}\n{tb}")
            # Include more detail in error message for debugging
            error_detail = f"{type(e).__name__}: {e}"
            return {
                "success": False,
                "message": f"Error merging themes: {error_detail}",
            }

    def _load_metadata(self, theme_path: Path) -> dict:
        """Load metadata.json for a theme, with repair if needed."""
        metadata_path = theme_path / "metadata.json"

        if not metadata_path.exists():
            # Generate basic metadata from files
            return self._generate_metadata(theme_path)

        try:
            with open(metadata_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.warning(f"Corrupted metadata.json in {theme_path.name}, repairing: {e}")
            return self._repair_metadata(theme_path)

    def _generate_metadata(self, theme_path: Path) -> dict:
        """Generate metadata from audio files in theme folder."""
        metadata = {
            "name": theme_path.name,
            "tracks": [],
            "presets": [],
        }

        audio_extensions = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac'}
        for f in sorted(theme_path.iterdir()):
            if f.is_file() and f.suffix.lower() in audio_extensions:
                track_id = self._generate_track_id(f.name)
                metadata["tracks"].append({
                    "id": track_id,
                    "file": f.name,
                    "name": f.stem,
                })

        return metadata

    def _repair_metadata(self, theme_path: Path) -> dict:
        """Attempt to repair corrupted metadata.json."""
        metadata_path = theme_path / "metadata.json"
        repaired = self._generate_metadata(theme_path)

        # Try to salvage data from corrupted file
        if metadata_path.exists():
            try:
                import re
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Try to extract name
                name_match = re.search(r'"name"\s*:\s*"([^"]+)"', content)
                if name_match:
                    repaired["name"] = name_match.group(1)

                # Try to extract description
                desc_match = re.search(r'"description"\s*:\s*"([^"]+)"', content)
                if desc_match:
                    repaired["description"] = desc_match.group(1)

                # Backup corrupted file
                backup_path = theme_path / "metadata.json.corrupted"
                shutil.copy2(metadata_path, backup_path)

            except Exception:
                pass

        # Create a default preset if we have tracks
        if repaired["tracks"]:
            default_preset = {
                "id": str(uuid.uuid4()),
                "name": "Default",
                "is_default": True,
                "tracks": {},
            }
            for track in repaired["tracks"]:
                default_preset["tracks"][track["id"]] = {
                    "volume": 0.7,
                    "enabled": True,
                }
            repaired["presets"] = [default_preset]

        # Save repaired metadata
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(repaired, f, indent=2, ensure_ascii=False)

        logger.info(f"Repaired metadata.json for {theme_path.name}")
        return repaired

    def _generate_track_id(self, filename: str) -> str:
        """Generate a consistent track ID from filename."""
        return hashlib.md5(filename.encode()).hexdigest()[:12]

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize a string for use as a filename."""
        # Remove/replace invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        # Limit length
        if len(name) > 100:
            name = name[:100]
        return name.strip()
