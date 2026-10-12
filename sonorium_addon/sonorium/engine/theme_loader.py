"""
A theme folder loaded for the engine: its tracks with their settings, its
groups and presets, as the server loads them (api.py's theme scan and
start-up), read only.

Nothing is written to the folder. The one-time conversions the server makes
when it loads an older theme (1.0 fields, Ebb & Flow Duration, "auto" modes)
are made in memory only, so the theme plays as the server plays it; an old
intrusion group (whose conversion moves files) is played as it is.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from sonorium.core import intrusions, theme_presets
from sonorium.core import theme_metadata as tm
from sonorium.obs import logger
from sonorium.recording import AudioFile, PlaybackMode
from sonorium.theme import LoadedTheme
from sonorium.theme_files import theme_audio_files
from sonorium.utils import IndexList

# Settings > Audio Settings > Master output gain out of the box (percent)
DEFAULT_MASTER_GAIN = 60.0


class Library:
    """What a LoadedTheme reads from the server's device: its files and the master output gain."""

    def __init__(self, name: str, files: list, master_gain: float = DEFAULT_MASTER_GAIN):
        self.files_by_theme = {name: IndexList(files)}
        self.all_files = IndexList(files)
        self.master_volume = 0.0
        self.set_master_gain(master_gain)

    def set_master_gain(self, percent: float) -> None:
        """Master output gain, 0-100 % (60 % = 6.0, as api.py's _apply_master_gain)."""
        self.master_volume = max(0.0, min(100.0, float(percent))) / 100 * 10.0


@dataclass
class ThemeFolder:
    folder: Path
    theme: LoadedTheme
    metadata: tm.ThemeMetadata
    presets: dict = field(default_factory=dict)
    missing_links: list = field(default_factory=list)

    def default_preset(self) -> Optional[str]:
        """The theme's default preset (the one marked is_default), if any."""
        for preset_id, data in self.presets.items():
            if (data or {}).get("is_default"):
                return preset_id
        return None


def _read_metadata(folder: Path) -> tuple[tm.ThemeMetadata, Optional[dict]]:
    raw = theme_presets.read_json(folder / theme_presets.METADATA_FILE)
    if raw is None:
        return tm.ThemeMetadata(name=folder.name), None  # no metadata.json: every track at its defaults
    return tm.ThemeMetadata.from_dict(raw), raw


def _convert_in_memory(folder: Path, metadata: tm.ThemeMetadata, raw: Optional[dict]) -> None:
    """The server's load-time conversions (theme_metadata.load_theme_folder), without writing."""
    if raw is not None and tm._is_legacy(raw):
        tm._convert_fields(metadata)
    tm._convert_ebb_duration(metadata, raw)
    tm._retire_auto(folder, metadata)


def _apply_track_settings(theme: LoadedTheme, metadata: tm.ThemeMetadata) -> None:
    """Each track's saved settings, as api.py's _apply_saved_track_settings applies them."""
    theme.short_file_threshold = metadata.short_file_threshold
    theme.groups = dict(getattr(metadata, "groups", None) or {})
    for inst in theme.tracks:
        settings = metadata.tracks.get(inst.name)
        if not settings:
            inst.presence = 1.0
            inst.is_enabled = True
            inst.volume = 1.0
            inst.playback_mode = PlaybackMode.AUTO
            inst.crossfade_enabled = True
            inst.exclusive = False
            inst.fade_in = inst.fade_out = 0.0
            tm.reset_ebb_settings(inst)
            if getattr(inst.meta, "link", None):
                inst.is_enabled = False  # a linked file without settings stays off
            continue
        inst.presence = settings.presence
        inst.is_enabled = not settings.muted
        inst.volume = settings.volume
        try:
            inst.playback_mode = PlaybackMode(settings.playback_mode)
        except ValueError:
            inst.playback_mode = PlaybackMode.AUTO
        inst.crossfade_enabled = not settings.seamless_loop
        inst.exclusive = settings.exclusive
        inst.fade_in, inst.fade_out = settings.fade_in, settings.fade_out
        for key in tm.EBB_TRACK_SETTINGS:
            setattr(inst, key, getattr(settings, key))


def load_theme(theme_dir, master_gain: float = DEFAULT_MASTER_GAIN, themes_root=None) -> ThemeFolder:
    """
    Load a theme folder. Linked files (another theme's files, metadata.json
    "links") are looked for in the theme folders beside it, or under
    `themes_root`; a missing one is left out, as on the server.
    Call inside the engine's scope: file lengths are read with its decoder.
    """
    folder = Path(theme_dir)
    if not folder.is_dir():
        raise FileNotFoundError(f"No theme folder at {folder}")
    metadata, raw = _read_metadata(folder)
    metadata.presets = theme_presets.load_presets(folder)
    _convert_in_memory(folder, metadata, raw)

    files = [AudioFile(path, folder) for path in theme_audio_files(folder)]
    missing = []
    if metadata.links:
        folder_for_id = intrusions.folder_for_id_in(Path(themes_root) if themes_root else folder.parent)
        for key, link in metadata.links.items():
            path = intrusions.resolve_link(link, folder_for_id)
            if path is None:
                missing.append(key)
                logger.warning(f'Theme "{folder.name}": linked track "{key}" is missing; it is skipped')
                continue
            files.append(AudioFile(path, key=key, group=intrusions.link_group(key), link=dict(link)))
    if not files:
        raise ValueError(f'Theme folder "{folder.name}" holds no audio files')

    library = Library(folder.name, files, master_gain)
    theme_id = raw.get("id") if isinstance(raw, dict) and isinstance(raw.get("id"), str) else None
    theme = LoadedTheme(library, folder.name, theme_id=theme_id)
    _apply_track_settings(theme, metadata)
    return ThemeFolder(folder, theme, metadata, dict(metadata.presets or {}), missing)
