"""
Theme editing: what the Theme Editor changes in a theme, as plain functions.

The server's Theme Editor routes (api.py, web/api_v2.py) call these, so every
way of running Sonorium edits a theme with the same code. Nothing here
imports the web server, Home Assistant, MQTT or the settings; numpy comes in
only through sonorium.recording (playback modes, group crossfade range).

Two layers:

1. Checks and changes on values, shared with the server, which keeps its own
   copy of each theme in memory (theme_metadata.ThemeMetadataManager and the
   playing theme.LoadedTheme):

       clean_track_value("volume", 7)        -> 1.0 (clamped as the editor does)
       apply_track(live_track, volume=0.5)   the playing track follows
       store_track(metadata, "Rain", volume=0.5)
       clean_group_update(settings, {"mode": "merry_go_round"})
       add_preset(presets, "Calm", tracks, groups) -> ("calm", "Calm", False)

2. The same operations on a theme folder (metadata.json and presets.json read,
   changed and written as the server writes them):

       from sonorium.core import theme_edit as te
       te.set_track(folder, "Rain", volume=0.5, muted=False)
       te.set_group(folder, "Birds", gap_min=10, gap_max=60)
       te.new_preset(folder, "Evening")          # the theme's settings saved as a preset
       te.load_preset(folder, "evening")         # a preset made the theme's settings

Errors are EditError (a ValueError) with a `status` (400, 404, 409 or 500, the
HTTP status the server answers with) and the server's message.

The functions that move or add files (rename_group, delete_group, add_links,
remove_link, copy_link_in, upload) also return the file operations they did, under "ops",
so an app can repeat them in storage it can't write by path (see FileOps).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Iterable, Optional

from sonorium.core import theme_presets
from sonorium.core import theme_metadata as tm
from sonorium.obs import logger


class EditError(ValueError):
    """A change that can't be made. str(error) says why; status is the HTTP status the server answers with."""
    status = 400


class NotFound(EditError):
    status = 404


class Conflict(EditError):
    status = 409


class SaveFailed(EditError):
    status = 500


# --- tracks ---------------------------------------------------------------------

# A track's settings, as metadata.json and presets store them
TRACK_SETTINGS = ("presence", "muted", "volume", "playback_mode", "seamless_loop", "exclusive",
                  "fade_in", "fade_out") + tm.EBB_TRACK_SETTINGS

# The one-value settings, each with the message the editor answers when it is left out
_REQUIRED = {
    "presence": "Presence is required",
    "volume": "Volume is required",
    "muted": "Muted state is required",
    "playback_mode": "Playback mode is required",
    "seamless_loop": "seamless_loop is required",
    "exclusive": "exclusive is required",
}


def clean_track_value(name: str, value):
    """
    One of presence, volume (0..1, clamped), muted, seamless_loop, exclusive
    (true/false) or playback_mode (continuous, sparse, presence, auto,
    unassigned), as the Theme Editor takes it. Raises EditError (left out, or
    an unknown playback mode); a value that isn't a number raises ValueError.
    """
    if name not in _REQUIRED:
        raise EditError(f"Unknown setting '{name}'")
    if value is None:
        raise EditError(_REQUIRED[name])
    if name in ("presence", "volume"):
        return max(0.0, min(1.0, float(value)))
    if name == "playback_mode":
        from sonorium.recording import PlaybackMode
        valid_modes = [m.value for m in PlaybackMode]
        if value not in valid_modes:
            raise EditError(f"Invalid playback mode. Must be one of: {valid_modes}")
        return value
    return bool(value)


def clean_ebb(body) -> dict:
    """
    A track's Ebb & Flow settings: any of volume_low (0..1), pause_between
    (true/false), pause_min, pause_max (seconds, 0..86400, to 0.1 s) and rise
    (0..1). Raises EditError (400) with the editor's message.
    """
    if not isinstance(body, dict) or not body:
        raise EditError("Expected volume_low, pause_between, pause_min or pause_max")
    values = {}
    for key, raw in body.items():
        if key not in tm.EBB_TRACK_SETTINGS:
            raise EditError(f"Unknown setting '{key}'")
        if key == "pause_between":
            if not isinstance(raw, bool):
                raise EditError("pause_between must be true or false")
            values[key] = raw
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError):
            raise EditError(f"{key} must be a number")
        if number != number or isinstance(raw, bool):
            raise EditError(f"{key} must be a number")
        values[key] = max(0.0, min(1.0, number)) if key in ("volume_low", "rise") else round(max(0.0, min(86400.0, number)), 1)
    return values


def clean_fades(body) -> dict:
    """
    A file's own Fade in / Fade out: {"fade_in": s, "fade_out": s}, either
    left out; 0 or empty is off, at most recording.FILE_FADE_MAX_SECONDS (30),
    to 0.01 s. Raises EditError (400) with the editor's message.
    """
    from sonorium.recording import FILE_FADE_MAX_SECONDS
    if not isinstance(body, dict):
        raise EditError("Invalid JSON body")
    values = {}
    for key in ("fade_in", "fade_out"):
        if key not in body:
            continue
        raw = body[key]
        try:
            seconds = float(raw) if raw not in (None, "") else 0.0
        except (TypeError, ValueError):
            raise EditError(f"{key} must be a number of seconds")
        if seconds != seconds or seconds < 0:
            raise EditError(f"{key} must be 0 or more")
        values[key] = round(min(FILE_FADE_MAX_SECONDS, seconds), 2)
    if not values:
        raise EditError("fade_in or fade_out is required")
    return values


def clean_track_values(values: dict) -> dict:
    """Any of TRACK_SETTINGS, each checked as the Theme Editor checks it (clean_track_value, clean_ebb, clean_fades)."""
    cleaned = {}
    ebb = {k: v for k, v in values.items() if k in tm.EBB_TRACK_SETTINGS}
    fades = {k: v for k, v in values.items() if k in ("fade_in", "fade_out")}
    for key, value in values.items():
        if key in _REQUIRED:
            cleaned[key] = clean_track_value(key, value)
        elif key not in ebb and key not in fades:
            raise EditError(f"Unknown setting '{key}'")
    if ebb:
        cleaned.update(clean_ebb(ebb))
    if fades:
        cleaned.update(clean_fades(fades))
    return cleaned


def find_track(theme, key: str):
    """The playing track of a theme.LoadedTheme by key ("Rain", "Birds/Robin"), or None."""
    return next((inst for inst in theme.tracks if inst.name == key), None)


def apply_track(track, **values) -> None:
    """A playing track (a theme.ThemeTrack) takes checked settings at once, as the editor's changes do."""
    for key, value in values.items():
        if key == "muted":
            track.is_enabled = not value
        elif key == "seamless_loop":
            track.crossfade_enabled = not value
        elif key == "playback_mode":
            from sonorium.recording import PlaybackMode
            track.playback_mode = PlaybackMode(value)
        else:
            setattr(track, key, value)


def store_track(metadata: tm.ThemeMetadata, key: str, **values) -> tm.TrackSettings:
    """Checked settings into a track's entry in the metadata (made if missing). Unknown names are ignored."""
    settings = metadata.get_track_settings(key)
    for name, value in values.items():
        if hasattr(settings, name):
            setattr(settings, name, value)
    return settings


def apply_saved_settings(theme, metadata: tm.ThemeMetadata) -> None:
    """
    A loaded theme's tracks and groups take the settings saved in its metadata
    (a track without an entry gets the defaults; a linked file without one
    stays off), as at start-up and when the engine reloads a theme.
    """
    from sonorium.recording import PlaybackMode
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


def reset_tracks(theme=None, metadata: Optional[tm.ThemeMetadata] = None) -> None:
    """Every track back to the defaults (linked files stay muted): the playing theme, the metadata, or both."""
    if theme is not None:
        from sonorium.recording import PlaybackMode
        for inst in theme.tracks:
            inst.presence = 1.0
            inst.is_enabled = not getattr(inst.meta, "link", None)
            inst.volume = 1.0
            inst.playback_mode = PlaybackMode.AUTO
            inst.crossfade_enabled = True
            inst.exclusive = False
            inst.fade_in = inst.fade_out = 0.0
            tm.reset_ebb_settings(inst)
    if metadata is not None:
        fresh = {key: tm.TrackSettings(muted=True) for key in (metadata.links or {})}
        # Plugin attributes aren't mix settings: a reset keeps them (core/plugin_data.py)
        for key, settings in (metadata.tracks or {}).items():
            if getattr(settings, "plugins", None):
                fresh.setdefault(key, tm.TrackSettings()).plugins = settings.plugins
        metadata.tracks = fresh


# --- groups ---------------------------------------------------------------------

# A group's master settings: volume and presence multiply each track's own
# value; gap_min/gap_max are seconds between plays; mode "intermittent"
# (default), "merry_go_round" or "ebb_flow" ("intrusion" is retired: refused);
# crossfade is a merry-go-round's overlap in seconds; pause_between and rise
# are Ebb & Flow's. Mode and crossfade are theme settings, not in presets.
GROUP_SETTING_KEYS = ("presence", "volume", "muted", "gap_min", "gap_max", "mode", "crossfade", "pause_between", "rise")


def clean_group_update(current: Optional[dict], body) -> dict:
    """
    A group's settings after a change: `current` with the values of `body`
    (any of GROUP_SETTING_KEYS; null removes one, back to its default),
    checked as the Theme Editor checks them. "remove_links" (from when
    intrusion groups held linked files) is accepted and ignored. Raises
    EditError (400) with the editor's message.
    """
    from sonorium.recording import GROUP_MODE_INTRUSION, GROUP_MODES, group_crossfade_range
    if not isinstance(body, dict):
        raise EditError("Expected a JSON object")
    body = dict(body)
    remove_links = body.pop("remove_links", False)
    if not isinstance(remove_links, bool):
        raise EditError("Invalid value for 'remove_links'")

    settings = dict(current or {})
    for key, value in body.items():
        if key not in GROUP_SETTING_KEYS:
            raise EditError(f"Unknown group setting '{key}'")
        if value is None:
            settings.pop(key, None)
            continue
        try:
            if key in ("presence", "volume", "rise"):
                if isinstance(value, bool):
                    raise ValueError(value)
                value = max(0.0, min(1.0, float(value)))
            elif key in ("gap_min", "gap_max"):
                value = max(0.0, float(value))
            elif key in ("muted", "pause_between"):
                if not isinstance(value, bool):
                    raise ValueError(value)
            elif key == "mode":
                if value not in GROUP_MODES or value == GROUP_MODE_INTRUSION:
                    raise ValueError(value)
            elif key == "crossfade":
                if isinstance(value, bool):
                    raise ValueError(value)
                value = float(value)
                if value != value:  # NaN
                    raise ValueError(value)
                low, high = group_crossfade_range()  # Settings > Advanced, by default 1-60
                value = max(low, min(high, value))
        except (TypeError, ValueError):
            raise EditError(f"Invalid value for '{key}'")
        settings[key] = value
    if "gap_min" in settings and "gap_max" in settings and settings["gap_min"] > settings["gap_max"]:
        settings["gap_min"], settings["gap_max"] = settings["gap_max"], settings["gap_min"]
    return settings


def store_group(metadata: tm.ThemeMetadata, group: str, settings: dict) -> None:
    """A group's settings into the metadata."""
    metadata.groups = {**(metadata.groups or {}), group: settings}


def group_members(folder: Path, metadata: tm.ThemeMetadata) -> dict:
    """
    {group: [track keys]}: the group folders (empty ones too) with their files,
    and the linked files filed under a group.
    """
    from sonorium.core import theme_groups
    from sonorium.core.intrusions import link_group
    members = {name: theme_groups.group_track_keys(folder, name)
               for name in theme_groups.group_folder_names(folder)}
    for key in (getattr(metadata, "links", None) or {}):
        if link_group(key) is not None:
            members.setdefault(link_group(key), []).append(key)
    return members


def save_group_change(folder: Path, metadata: tm.ThemeMetadata, change,
                      save_metadata: Optional[Callable[[tm.ThemeMetadata], bool]] = None) -> None:
    """
    After files moved (or a linked file was re-keyed; a theme_groups.Change):
    the track keys follow in metadata.json and presets.json (presets and
    sequences), and both are saved (with `save_metadata`, default
    theme_metadata.save_theme_folder). Raises SaveFailed (500).
    """
    from sonorium.core import sequences, theme_groups
    presets = theme_presets.load_presets(folder)
    migrated = theme_groups.migrate_keys(metadata, presets, change)
    try:
        if migrated != presets:
            theme_presets.save_presets(folder, migrated)
        sequences.migrate_track_keys(folder, change.track_keys, change.group_renames)
    except OSError as e:
        logger.error(f"Theme '{metadata.name}': files moved but presets.json couldn't be saved ({e})")
        raise SaveFailed("Files were moved, but presets.json couldn't be saved")
    metadata.presets = migrated
    save = save_metadata or (lambda m: tm.save_theme_folder(folder, m))
    if not save(metadata):
        raise SaveFailed("Files were moved, but metadata.json couldn't be saved")


def track_move(folder: Path, metadata: tm.ThemeMetadata, key: str, group) -> tuple:
    """
    Move a track into a group (its folder made if missing) or, with group None,
    to the top level. A linked file only changes key (no file moves). Returns
    (new key, the theme_groups.Change to save with save_group_change, or None
    when nothing moved, True for a linked file). Raises EditError or
    theme_groups.GroupError (its status says which).
    """
    from sonorium.core import intrusions, theme_groups
    if key in (getattr(metadata, "links", None) or {}):
        if group is not None:
            if not isinstance(group, str):
                raise EditError("'group' must be a group name or null")
            group = theme_groups.group_target(folder, group).name
        moves = intrusions.regroup_link_keys(metadata, folder, [key], group)
        return moves.get(key, key), (theme_groups.Change(track_keys=moves) if moves else None), True
    change = theme_groups.move_track(folder, key, group, reserved=intrusions.link_names(metadata, group))
    return change.track_keys.get(key, key), (change if change.track_keys else None), False


# --- presets --------------------------------------------------------------------

def capture_tracks(theme) -> dict:
    """A playing theme's track settings as a preset saves them ({key: {volume, presence, ...}})."""
    tracks = {}
    for inst in theme.tracks:
        tracks[inst.name] = {
            "volume": inst.volume,
            "presence": inst.presence,
            "playback_mode": inst.playback_mode.value if hasattr(inst.playback_mode, 'value') else str(inst.playback_mode),
            "seamless_loop": not inst.crossfade_enabled,
            "exclusive": inst.exclusive,
            "muted": not inst.is_enabled,
            **{key: getattr(inst, key) for key in tm.EBB_TRACK_SETTINGS},
        }
    return tracks


def capture_groups(groups: Optional[dict]) -> dict:
    """A theme's group settings as a preset saves them ({group: {volume, presence, muted, gap_min, gap_max, ...}})."""
    from sonorium.recording import GROUP_PRESET_SETTINGS
    return {
        name: {k: v for k, v in (settings or {}).items() if k in GROUP_PRESET_SETTINGS}
        for name, settings in (groups or {}).items()
        if not (settings or {}).get("legacy_exclusive")
    }


def new_preset_id(name: str, presets: dict) -> str:
    """A preset id from its name ("My Mix!" -> "my_mix"), "_1", "_2" ... when taken."""
    preset_id = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
    if not preset_id:
        preset_id = "preset"
    base_id = preset_id
    counter = 1
    while preset_id in presets:
        preset_id = f"{base_id}_{counter}"
        counter += 1
    return preset_id


_NO_GROUPS = object()


def add_preset(presets: dict, name: Optional[str], tracks: dict, groups=_NO_GROUPS) -> tuple:
    """
    A new preset in `presets` (changed in place) with these track (and group)
    settings: named `name` ("New Preset" if empty), the default if it is the
    theme's first. Returns (preset_id, name, is_default).
    """
    if not name:
        name = "New Preset"
    preset_id = new_preset_id(name, presets)
    is_default = len(presets) == 0
    preset = {"name": name, "is_default": is_default, "tracks": tracks}
    if groups is not _NO_GROUPS:
        preset["groups"] = groups
    presets[preset_id] = preset
    return preset_id, name, is_default


def _preset(presets: dict, preset_id: str) -> dict:
    if preset_id not in presets:
        raise NotFound("Preset not found")
    return presets[preset_id]


def update_preset(presets: dict, preset_id: str, tracks: dict, groups: dict) -> dict:
    """A preset takes new track and group settings (its name and default mark stay). Raises NotFound."""
    preset = _preset(presets, preset_id)
    preset["tracks"] = tracks
    preset["groups"] = groups
    return preset


def remove_preset(presets: dict, preset_id: str) -> None:
    """Delete a preset; if it was the default, the first one left becomes the default. Raises NotFound."""
    was_default = _preset(presets, preset_id).get("is_default", False)
    del presets[preset_id]
    if was_default and presets:
        presets[next(iter(presets))]["is_default"] = True


def mark_default(presets: dict, preset_id: str) -> None:
    """Make a preset the theme's default (the others lose the mark). Raises NotFound."""
    _preset(presets, preset_id)
    for pid, pdata in presets.items():
        pdata["is_default"] = (pid == preset_id)


def clean_preset_name(name) -> str:
    """A preset's new name, trimmed. Raises EditError when empty."""
    name = (name or "").strip()
    if not name:
        raise EditError("Name is required")
    return name


def rename(presets: dict, preset_id: str, name: str) -> None:
    """Rename a preset (name already checked with clean_preset_name). Raises NotFound."""
    _preset(presets, preset_id)["name"] = name


def preset_values(presets: dict, preset_id: str) -> dict:
    """One preset: {"id", "name", "tracks": {key: {...}}, "groups": {name: {...}}}. Raises NotFound."""
    preset = _preset(presets, preset_id)
    return {
        "id": preset_id,
        "name": preset.get("name", preset_id),
        "tracks": preset.get("tracks") or {},
        "groups": preset.get("groups") or {},
    }


def merge_preset_groups(groups: Optional[dict], preset_groups: Optional[dict]) -> dict:
    """A theme's group settings with a preset's group values laid over them."""
    from sonorium.recording import GROUP_PRESET_SETTINGS
    merged = dict(groups or {})
    for name, values in (preset_groups or {}).items():
        merged[name] = {**(merged.get(name) or {}),
                        **{k: v for k, v in (values or {}).items() if k in GROUP_PRESET_SETTINGS}}
    return merged


def apply_preset_to_tracks(theme, preset_tracks: dict) -> None:
    """A playing theme's tracks take a preset's track settings (as the Theme Editor's Load does)."""
    from sonorium.recording import PlaybackMode
    for inst in theme.tracks:
        if inst.name in preset_tracks:
            settings = preset_tracks[inst.name]
            inst.volume = settings.get("volume", 1.0)
            inst.presence = settings.get("presence", 1.0)
            mode_str = settings.get("playback_mode", "auto")
            try:
                inst.playback_mode = PlaybackMode(mode_str)
            except ValueError:
                inst.playback_mode = PlaybackMode.AUTO
            inst.crossfade_enabled = not settings.get("seamless_loop", False)
            inst.exclusive = settings.get("exclusive", False)
            inst.is_enabled = not settings.get("muted", False)
            for key in tm.EBB_TRACK_SETTINGS:
                if key in settings:
                    setattr(inst, key, settings[key])


def store_preset_tracks(metadata: tm.ThemeMetadata, preset_tracks: dict) -> None:
    """A preset's track settings become the theme's own in the metadata (as the Theme Editor's Load saves them)."""
    for track_name, settings in preset_tracks.items():
        track_settings = metadata.get_track_settings(track_name)
        track_settings.presence = settings.get("presence", 1.0)
        track_settings.muted = settings.get("muted", False)
        track_settings.volume = settings.get("volume", 1.0)
        track_settings.playback_mode = settings.get("playback_mode", "auto")
        track_settings.seamless_loop = settings.get("seamless_loop", False)
        track_settings.exclusive = settings.get("exclusive", False)
        for key in tm.EBB_TRACK_SETTINGS:
            if key in settings:
                setattr(track_settings, key, settings[key])


# --- sequences ------------------------------------------------------------------

def _seq():
    from sonorium.core import sequences
    return sequences


def _save_sequences(folder: Path, sequences: dict) -> None:
    try:
        _seq().save_sequences(folder, sequences)
    except OSError as e:
        raise SaveFailed(f"Failed to save: {e}")


def _preset_ids(folder: Path, preset_ids) -> Iterable[str]:
    return preset_ids if preset_ids is not None else theme_presets.load_presets(folder).keys()


def create_sequence(folder, data: dict, preset_ids: Optional[Iterable[str]] = None) -> tuple:
    """
    A new sequence in the theme's presets.json: {"name", "presets": [ids],
    "at_end", "hold_min", "hold_max", "ramp"} (timing null = the theme's
    default). preset_ids: the theme's presets (default: read from the folder).
    Returns (sequence_id, the sequence as saved, sequence_defaults).
    Raises sequences.SequenceError (400), Conflict (a sequence with that name) or SaveFailed.
    """
    seq, folder = _seq(), Path(folder)
    defaults = seq.load_defaults(folder)
    existing = seq.load_sequences(folder)
    cleaned = seq.clean_sequence(data, _preset_ids(folder, preset_ids))
    seq.check_timing(cleaned, defaults)
    from sonorium.core.playlists import same_name
    if same_name(existing, cleaned["name"]):
        raise Conflict("This theme already has a sequence with that name")
    sequence_id = seq.new_sequence_id(cleaned["name"], existing)
    existing[sequence_id] = cleaned
    _save_sequences(folder, existing)
    return sequence_id, cleaned, defaults


def update_sequence(folder, sequence_id: str, data: dict, preset_ids: Optional[Iterable[str]] = None) -> tuple:
    """
    Change a sequence: only the fields in `data` change. Returns (the sequence
    as saved, sequence_defaults). Raises NotFound, sequences.SequenceError,
    Conflict or SaveFailed.
    """
    seq, folder = _seq(), Path(folder)
    existing = seq.load_sequences(folder)
    if sequence_id not in existing:
        raise NotFound("Sequence not found")
    defaults = seq.load_defaults(folder)
    cleaned = seq.clean_sequence(data, _preset_ids(folder, preset_ids), existing[sequence_id])
    seq.check_timing(cleaned, defaults)
    from sonorium.core.playlists import same_name
    if same_name(existing, cleaned["name"], except_id=sequence_id):
        raise Conflict("This theme already has a sequence with that name")
    existing[sequence_id] = cleaned
    _save_sequences(folder, existing)
    return cleaned, defaults


def delete_sequence(folder, sequence_id: str) -> None:
    """Delete a sequence from presets.json. Raises NotFound or SaveFailed."""
    seq, folder = _seq(), Path(folder)
    existing = seq.load_sequences(folder)
    if sequence_id not in existing:
        raise NotFound("Sequence not found")
    del existing[sequence_id]
    _save_sequences(folder, existing)


# --- a theme folder ---------------------------------------------------------------
#
# Each call reads the folder's metadata.json and presets.json as the server
# reads them (theme_metadata.load_theme_folder, which also makes its one-time
# conversions of older themes), changes them and writes them back as the
# server writes them (theme_metadata.save_theme_folder).

# False: every folder function here reads a theme without writing its one-time
# conversions (theme_metadata.load_theme_folder(convert=False)). For an app that
# edits a copy of a theme (e.g. with stand-ins for its audio files); the server
# leaves it True.
convert_on_load = True


def load(folder, convert: Optional[bool] = None) -> tm.ThemeMetadata:
    """
    A theme folder's metadata, with its presets. convert=False (default:
    convert_on_load) writes nothing: see theme_metadata.load_theme_folder.
    """
    return tm.load_theme_folder(Path(folder), convert=convert_on_load if convert is None else convert)


def save(folder, metadata: tm.ThemeMetadata) -> None:
    """Write metadata.json and presets.json. Raises SaveFailed."""
    if not tm.save_theme_folder(Path(folder), metadata):
        raise SaveFailed("Failed to save changes")


def track_keys(folder, metadata: Optional[tm.ThemeMetadata] = None) -> list[str]:
    """The theme's track keys: its audio files ("Rain", "Birds/Robin"), then its linked files."""
    from sonorium.theme_files import theme_audio_files, track_key
    folder = Path(folder)
    metadata = metadata if metadata is not None else load(folder)
    keys = [track_key(folder, path) for path in theme_audio_files(folder)]
    return keys + [key for key in (metadata.links or {}) if key not in keys]


def track_settings(folder, key: Optional[str] = None):
    """A track's settings as metadata.json has them (dict), or every track's ({key: dict}) with key None."""
    metadata = load(folder)
    if key is None:
        return {k: s.to_dict() if isinstance(s, tm.TrackSettings) else dict(s) for k, s in metadata.tracks.items()}
    if key not in track_keys(folder, metadata):
        raise NotFound("Track not found")
    return (metadata.tracks.get(key) or tm.TrackSettings()).to_dict()


def set_track(folder, key: str, **values) -> dict:
    """
    Change a track's settings: any of presence, volume, muted, playback_mode,
    seamless_loop, exclusive, fade_in, fade_out, volume_low, pause_between,
    pause_min, pause_max, rise, checked and clamped as the Theme Editor does.
    Returns the track's settings as saved. Raises NotFound, EditError or SaveFailed.
    """
    metadata = load(folder)
    if key not in track_keys(folder, metadata):
        raise NotFound("Track not found")
    cleaned = clean_track_values(values)
    settings = store_track(metadata, key, **cleaned)
    save(folder, metadata)
    return settings.to_dict()


def reset_theme_tracks(folder) -> None:
    """Every track back to its defaults (linked files stay muted)."""
    metadata = load(folder)
    reset_tracks(metadata=metadata)
    save(folder, metadata)


def list_groups(folder) -> dict:
    """{group: {"settings": {...}, "tracks": [keys]}} for every group of the theme."""
    folder = Path(folder)
    metadata = load(folder)
    return {name: {"settings": {k: v for k, v in dict((metadata.groups or {}).get(name) or {}).items()
                                if k in GROUP_SETTING_KEYS},
                   "tracks": keys}
            for name, keys in sorted(group_members(folder, metadata).items())}


def set_group(folder, group: str, **values) -> dict:
    """
    Change a group's settings: any of GROUP_SETTING_KEYS (None removes one).
    Returns the group's settings as saved. Raises NotFound, EditError or SaveFailed.
    """
    folder = Path(folder)
    metadata = load(folder)
    if group not in group_members(folder, metadata):
        raise NotFound("Group not found")
    settings = clean_group_update((metadata.groups or {}).get(group), values)
    store_group(metadata, group, settings)
    save(folder, metadata)
    return settings


def create_group(folder, name: str) -> str:
    """A new, empty group (a subfolder). Returns its name. Raises theme_groups.GroupError."""
    from sonorium.core import theme_groups
    return theme_groups.create_group(Path(folder), name)


def move_track(folder, key: str, group: Optional[str]) -> str:
    """
    Move a track into a group (made if missing) or, with None, to the top
    level; its settings, preset values and sequence entries follow its new
    key. Returns the new key. Raises EditError, theme_groups.GroupError or SaveFailed.
    """
    folder = Path(folder)
    metadata = load(folder)
    new_key, change, _ = track_move(folder, metadata, key, group)
    if change is not None:
        save_group_change(folder, metadata, change)
    return new_key


class _Track:
    """A track for capture_tracks, from a key alone."""

    def __init__(self, key: str, link):
        from types import SimpleNamespace
        self.name = key
        self.meta = SimpleNamespace(link=link)


class _Tracks:
    def __init__(self, tracks):
        self.tracks = tracks


def current_mix(folder, metadata: Optional[tm.ThemeMetadata] = None) -> dict:
    """
    The theme's own settings as a preset saves them: {"tracks": {key: {...}},
    "groups": {name: {...}}}, every track with its values (defaults where
    metadata.json has none), as the server captures them from the playing theme.
    """
    metadata = metadata if metadata is not None else load(folder)
    links = metadata.links or {}
    theme = _Tracks([_Track(key, links.get(key)) for key in track_keys(folder, metadata)])
    apply_saved_settings(theme, metadata)
    return {"tracks": capture_tracks(theme), "groups": capture_groups(metadata.groups)}


def list_presets(folder) -> list[dict]:
    """The theme's presets: [{"id", "name", "is_default", "track_count"}]."""
    return [{"id": pid, "name": data.get("name", pid), "is_default": data.get("is_default", False),
             "track_count": len(data.get("tracks", {}))}
            for pid, data in load(folder).presets.items()]


def get_preset(folder, preset_id: str) -> dict:
    """One preset: {"id", "name", "tracks", "groups"}. Raises NotFound."""
    return preset_values(load(folder).presets, preset_id)


def new_preset(folder, name: Optional[str] = None) -> dict:
    """
    The theme's own settings (current_mix) saved as a new preset, the default
    if it is the first. Returns {"preset_id", "name", "is_default"}.
    """
    metadata = load(folder)
    mix = current_mix(folder, metadata)
    preset_id, name, is_default = add_preset(metadata.presets, name, mix["tracks"], mix["groups"])
    save(folder, metadata)
    return {"preset_id": preset_id, "name": name, "is_default": is_default}


def save_preset(folder, preset_id: str) -> dict:
    """The theme's own settings (current_mix) saved into a preset (its name and default mark stay). Raises NotFound."""
    metadata = load(folder)
    _preset(metadata.presets, preset_id)
    mix = current_mix(folder, metadata)
    update_preset(metadata.presets, preset_id, mix["tracks"], mix["groups"])
    save(folder, metadata)
    return preset_values(metadata.presets, preset_id)


def load_preset(folder, preset_id: str) -> dict:
    """
    A preset's values become the theme's own settings (as the Theme Editor's
    Load does), saved to metadata.json. Returns the preset ({"id", "name",
    "tracks", "groups"}). Raises NotFound.
    """
    metadata = load(folder)
    values = preset_values(metadata.presets, preset_id)
    preset = metadata.presets[preset_id]
    if preset.get("groups"):
        metadata.groups = merge_preset_groups(metadata.groups, preset.get("groups"))
    store_preset_tracks(metadata, preset.get("tracks", {}))
    save(folder, metadata)
    return values


def set_default_preset(folder, preset_id: str) -> None:
    """Make a preset the theme's default. Raises NotFound."""
    metadata = load(folder)
    mark_default(metadata.presets, preset_id)
    save(folder, metadata)


def rename_preset(folder, preset_id: str, name: str) -> str:
    """Rename a preset. Returns the name as saved (trimmed). Raises EditError or NotFound."""
    name = clean_preset_name(name)
    metadata = load(folder)
    rename(metadata.presets, preset_id, name)
    save(folder, metadata)
    return name


def delete_preset(folder, preset_id: str) -> list[str]:
    """
    Delete a preset (the first left becomes the default if it was) and take
    it out of the theme's sequences. Returns the sequences it was taken out of.
    """
    folder = Path(folder)
    metadata = load(folder)
    remove_preset(metadata.presets, preset_id)
    save(folder, metadata)
    return drop_from_sequences(folder, preset_id)


def drop_from_sequences(folder, preset_id: str) -> list[str]:
    """A deleted preset taken out of the theme's sequences. Returns the sequences changed (never raises)."""
    try:
        changed = _seq().drop_preset(Path(folder), preset_id)
        if changed:
            logger.info(f"  Preset '{preset_id}' removed from sequence(s): {', '.join(changed)}")
        return changed
    except Exception as e:
        logger.warning(f"  Could not remove preset '{preset_id}' from the theme's sequences: {e}")
        return []


# --- the theme's details ---------------------------------------------------------

_UNSET = object()


def _save_with(folder: Path, metadata: tm.ThemeMetadata, save_metadata, message: str) -> None:
    """Save metadata.json and presets.json with `save_metadata` (default: theme_metadata.save_theme_folder)."""
    saved = save_metadata(metadata) if save_metadata is not None else tm.save_theme_folder(folder, metadata)
    if not saved:
        raise SaveFailed(message)


def rename_theme(folder, name, metadata: Optional[tm.ThemeMetadata] = None,
                 save_metadata: Optional[Callable[[tm.ThemeMetadata], bool]] = None) -> tuple:
    """
    The theme's name as shown (metadata.json "name"; the folder keeps its
    name, the theme its id). Returns (old name, new name). Raises EditError
    ("Name is required") or SaveFailed.
    """
    folder = Path(folder)
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        raise EditError("Name is required")
    metadata = metadata if metadata is not None else load(folder)
    old = metadata.name
    metadata.name = name
    _save_with(folder, metadata, save_metadata, "Failed to save metadata")
    return old, name


def set_metadata_fields(folder, values: dict) -> dict:
    """
    The Theme Editor's details (PUT /api/themes/{id}/metadata): any of
    "description", "icon" (empty: removed, the icon is picked from the name)
    and "short_file_threshold" (seconds, 0 or more), written into
    metadata.json as it is (every other key kept). Returns metadata.json as
    written. Raises Conflict (metadata.json can't be read), EditError, SaveFailed;
    a threshold that isn't a number raises ValueError.
    """
    folder = Path(folder)
    path = folder / theme_presets.METADATA_FILE
    try:
        metadata = theme_presets.read_json(path) or {}
    except theme_presets.BrokenJsonError as e:
        raise Conflict(f"metadata.json can't be read ({e}); refresh themes to repair it first")
    if "description" in values:
        metadata["description"] = values["description"]
    if "icon" in values:
        if values["icon"]:
            metadata["icon"] = values["icon"]
        elif "icon" in metadata:
            del metadata["icon"]  # picked from the name again
    if "short_file_threshold" in values:
        threshold = float(values["short_file_threshold"])
        if threshold < 0:
            raise EditError("short_file_threshold must be >= 0")
        metadata["short_file_threshold"] = threshold
    try:
        theme_presets.write_json_atomic(path, metadata)
    except Exception as e:
        logger.error(f"Failed to write metadata: {e}")
        raise SaveFailed("Could not write metadata")
    return metadata


def set_categories(folder, categories) -> list:
    """
    The theme's categories (metadata.json "categories"), written as the
    server's Themes page writes them (presets.json rewritten with its
    presets). Returns them. Raises SaveFailed.
    """
    folder = Path(folder)
    try:
        metadata = theme_presets.read_json(folder / theme_presets.METADATA_FILE)
    except theme_presets.BrokenJsonError:
        metadata = None
    presets = theme_presets.load_presets(folder) if metadata is not None else None
    metadata = metadata if metadata is not None else {}
    metadata["categories"] = categories
    try:
        if presets is not None:
            theme_presets.save_presets(folder, presets)
        theme_presets.write_json_atomic(folder / theme_presets.METADATA_FILE, metadata)
    except Exception as e:
        logger.error(f"Failed to write metadata for theme {folder.name}: {e}")
        raise SaveFailed("Failed to save categories")
    return categories


def set_details(folder, name=_UNSET, description=_UNSET, icon=_UNSET, categories=_UNSET,
                short_file_threshold=_UNSET) -> dict:
    """
    The theme's details, any of: name (as shown), description, icon (""
    removes it), categories (a list of names), short_file_threshold (seconds).
    Leave out what stays. Written as the server's rename, details and
    categories routes write them, in that order. The folder is never renamed:
    returns {"folder": the theme folder (unchanged), "name", "description",
    "icon", "categories"} as saved. Raises EditError, Conflict or SaveFailed.
    """
    folder = Path(folder)
    if name is not _UNSET:
        rename_theme(folder, name)
    fields = {key: value for key, value in (("description", description), ("icon", icon),
                                              ("short_file_threshold", short_file_threshold))
              if value is not _UNSET}
    if fields:
        set_metadata_fields(folder, fields)
    if categories is not _UNSET:
        set_categories(folder, categories)
    try:
        saved = theme_presets.read_json(folder / theme_presets.METADATA_FILE) or {}
    except theme_presets.BrokenJsonError:
        saved = {}
    return {"folder": str(folder), "name": saved.get("name"), "description": saved.get("description"),
            "icon": saved.get("icon"), "categories": saved.get("categories") or []}


# --- importing a preset ------------------------------------------------------------

def clean_imported_preset(preset_json, name: Optional[str], track_names) -> tuple:
    """
    A preset to import (the Theme Editor's Import preset): preset_json is the
    text of a preset ({"name", "tracks": {...}}) or of its tracks alone.
    Only the theme's tracks (track_names) are kept, each with volume,
    presence, playback_mode, seamless_loop, exclusive and muted. Returns
    (name, tracks, unknown track keys, preset id from the name). Raises
    EditError with the server's message.
    """
    if not preset_json:
        raise EditError("No preset JSON provided")
    try:
        preset_data = json.loads(preset_json)
    except json.JSONDecodeError as e:
        raise EditError(f"Invalid JSON: {e}")
    if not isinstance(preset_data, dict):
        raise EditError("Preset must be a JSON object")
    if "tracks" in preset_data:
        tracks = preset_data["tracks"]
        imported_name = preset_data.get("name", name or "Imported Preset")
    else:
        tracks = preset_data  # the tracks alone
        imported_name = name or "Imported Preset"
    if not isinstance(tracks, dict):
        raise EditError("Tracks must be a JSON object")

    valid = set(track_names)
    validated, unknown = {}, []
    for track_name, settings in tracks.items():
        if track_name not in valid:
            unknown.append(track_name)
            continue
        if not isinstance(settings, dict):
            continue
        validated[track_name] = {
            "volume": float(settings.get("volume", 1.0)),
            "presence": float(settings.get("presence", 1.0)),
            "playback_mode": str(settings.get("playback_mode", "auto")),
            "seamless_loop": bool(settings.get("seamless_loop", False)),
            "exclusive": bool(settings.get("exclusive", False)),
            "muted": bool(settings.get("muted", False)),
        }
    if not validated:
        raise EditError("No valid track settings found in preset")
    preset_id = re.sub(r'[^a-z0-9]+', '_', imported_name.lower()).strip('_') or "imported"
    return imported_name, validated, unknown, preset_id


def unique_preset_id(preset_id: str, presets: dict) -> str:
    """preset_id, or preset_id_1, _2 ... when taken."""
    base_id, counter = preset_id, 1
    while preset_id in presets:
        preset_id = f"{base_id}_{counter}"
        counter += 1
    return preset_id


def import_preset(folder, preset_json, name: Optional[str] = None, track_names=None,
                  metadata: Optional[tm.ThemeMetadata] = None,
                  save_metadata: Optional[Callable[[tm.ThemeMetadata], bool]] = None) -> dict:
    """
    Import a preset from its JSON text (clean_imported_preset): a new preset,
    not the default. track_names: the theme's tracks (default: track_keys).
    Returns {"status": "ok", "preset_id", "name", "tracks_imported"} and, when
    some weren't the theme's, "unknown_tracks" and "warning". Raises EditError or SaveFailed.
    """
    folder = Path(folder)
    metadata = metadata if metadata is not None else load(folder)
    names = track_names if track_names is not None else track_keys(folder, metadata)
    imported_name, tracks, unknown, preset_id = clean_imported_preset(preset_json, name, names)
    preset_id = unique_preset_id(preset_id, metadata.presets)
    metadata.presets[preset_id] = {"name": imported_name, "is_default": False, "tracks": tracks}
    _save_with(folder, metadata, save_metadata, "Failed to save preset")
    result = {"status": "ok", "preset_id": preset_id, "name": imported_name, "tracks_imported": len(tracks)}
    if unknown:
        result["unknown_tracks"] = unknown
        result["warning"] = f"{len(unknown)} track(s) not found in theme"
    return result


# --- a sequence step's tracks ------------------------------------------------------

def preset_layer(preset: Optional[dict]) -> dict:
    """What a channel playing a preset lays over the theme's own settings (its track and group overrides)."""
    from sonorium.recording import preset_group_overrides, preset_track_overrides
    if not preset:
        return {}
    return {**preset_track_overrides(preset.get("tracks", {})), **preset_group_overrides(preset.get("groups", {}))}


def transition_tracks(folder, preset: str, prev: Optional[str] = None, next: Optional[str] = None,
                      themes_root=None, decoder=None) -> dict:
    """
    The tracks a sequence step changes, for the Advanced dialog, as GET
    /api/themes/{id}/transition_tracks answers: {"theme_id", "preset",
    "prev", "next", "in": [rows], "out": [rows]} (transitions.step_tracks).
    Linked files are looked for beside the theme or under themes_root; file
    lengths are read with `decoder` (default: decoders' default). Nothing is
    written. Raises NotFound ("Preset 'x' not found").
    """
    from contextlib import nullcontext
    from sonorium import decoders
    from sonorium.core.transitions import step_tracks
    from sonorium.engine.theme_loader import load_theme, read_settings
    folder = Path(folder)
    with decoders.use_decoder(decoder) if decoder is not None else nullcontext():
        try:
            loaded = load_theme(folder, themes_root=themes_root)
            tracks, metadata = loaded.theme.tracks, loaded.metadata
            groups = loaded.theme.groups
        except ValueError:  # no audio files: no tracks change
            metadata, _ = read_settings(folder)
            tracks, groups = [], metadata.groups
        presets = dict(metadata.presets or {})
        for value in (preset, prev, next):
            if value and value not in presets:
                raise NotFound(f"Preset '{value}' not found")
        rows = step_tracks(tracks, groups, lambda preset_id: preset_layer(presets.get(preset_id)), preset, prev, next)
    return {"theme_id": metadata.id, "preset": preset, "prev": prev, "next": next, **rows}


# --- file operations ------------------------------------------------------------------

class FileOps:
    """
    The file operations a folder function did, in order, for an app that
    repeats them in storage it can't write by path (Android's shared storage):

        {"op": "mkdir",  "src": None,    "dst": "Birds"}           a group folder made
        {"op": "move",   "src": "Wind.wav", "dst": "Birds/Wind.wav"} a file or folder moved/renamed
        {"op": "copy",   "src": <the source path given>, "dst": "Rain.wav"}
        {"op": "write",  "src": None,    "dst": "metadata.json"}   written (take it from the folder)
        {"op": "delete", "src": "Birds", "dst": None}              an empty folder removed

    Paths are relative to the theme folder with "/" (a copy's src is the path
    the caller gave; copy_link_in's is relative to the linked theme's folder). metadata.json and presets.json are listed as "write"
    when their bytes changed, after the file operations.
    """

    JSON_FILES = (theme_presets.METADATA_FILE, theme_presets.PRESETS_FILE)

    def __init__(self, folder):
        self.folder = Path(folder)
        self.ops: list[dict] = []
        self._json = self._read_json_files()
        try:
            self._dirs = {p.name for p in self.folder.iterdir() if p.is_dir()}
        except OSError:
            self._dirs = set()

    def _read_json_files(self) -> dict:
        found = {}
        for name in self.JSON_FILES:
            try:
                found[name] = (self.folder / name).read_bytes()
            except OSError:
                found[name] = None
        return found

    def path(self, path) -> str:
        path = Path(path)
        try:
            return path.relative_to(self.folder).as_posix()
        except ValueError:
            return str(path)

    def add(self, op: str, src=None, dst=None) -> None:
        self.ops.append({"op": op, "src": None if src is None else self.path(src),
                         "dst": None if dst is None else self.path(dst)})

    def made_dir(self, path) -> None:
        """A folder that group_target / upload_path may just have made."""
        path = Path(path)
        if path.parent == self.folder and path.name not in self._dirs and path.is_dir():
            self.add("mkdir", dst=path)
            self._dirs.add(path.name)

    def change(self, change) -> None:
        """A theme_groups.Change's file operations."""
        for op, src, dst in getattr(change, "ops", None) or ():
            self.add(op, src, dst)
            if op == "mkdir":
                self._dirs.add(Path(dst).name)

    def done(self) -> list[dict]:
        after = self._read_json_files()
        for name in self.JSON_FILES:
            if after[name] is not None and after[name] != self._json[name]:
                self.add("write", dst=self.folder / name)
        self._json = after
        return self.ops


# --- groups, linked files and uploads, with their file operations ----------------------

def rename_group(folder, group: str, name, metadata: Optional[tm.ThemeMetadata] = None,
                 save_metadata: Optional[Callable[[tm.ThemeMetadata], bool]] = None) -> dict:
    """
    Rename a group (its folder). Track keys follow in metadata.json, presets
    and sequences. Returns {"name": new name, "tracks": {old key: new key},
    "ops": FileOps}. Raises theme_groups.GroupError (its status says which) or SaveFailed.
    """
    from sonorium.core import theme_groups
    folder = Path(folder)
    ops = FileOps(folder)
    metadata = metadata if metadata is not None else load(folder)
    change = theme_groups.rename_group(folder, group, name)
    ops.change(change)
    if change.group_renames:
        save_group_change(folder, metadata, change, save_metadata)
    return {"name": change.group_renames.get(group, group), "tracks": change.track_keys, "ops": ops.done()}


def delete_group(folder, group: str, metadata: Optional[tm.ThemeMetadata] = None,
                 save_metadata: Optional[Callable[[tm.ThemeMetadata], bool]] = None) -> dict:
    """
    Delete a group: its audio files move up to the theme folder ("x (2)" on a
    clash) and keep their settings; its linked files become ungrouped the
    same way (re-keyed, nothing moves); the folder goes when nothing else is
    in it. Audio files are never deleted. Returns {"name", "tracks": {old key:
    new key}, "folder_removed"} (+ "left_behind" and "message" when the folder
    stays), and "ops". Raises theme_groups.GroupError or SaveFailed.
    """
    from sonorium.core import intrusions, theme_groups
    folder = Path(folder)
    ops = FileOps(folder)
    metadata = metadata if metadata is not None else load(folder)
    links = intrusions.group_link_keys(metadata, group)
    try:
        change = theme_groups.delete_group(folder, group, reserved=intrusions.link_names(metadata, None))
    except theme_groups.NotFound:
        if not links:
            raise
        change = theme_groups.Change(group_renames={group: None})  # only links, no folder
    ops.change(change)
    change.track_keys.update(intrusions.regroup_link_keys(metadata, folder, links, None,
                                                          taken=change.track_keys.values()))
    save_group_change(folder, metadata, change, save_metadata)
    result = {"name": group, "tracks": change.track_keys, "folder_removed": change.folder_removed}
    if not change.folder_removed:
        result["left_behind"] = change.left_behind
        result["message"] = (f"The tracks moved to the top level; the folder '{group}' was kept "
                             f"because it still holds other files")
    result["ops"] = ops.done()
    return result


def clean_link_request(group, tracks) -> list[str]:
    """The track keys to link (duplicates dropped). Raises EditError with the server's message."""
    if group is not None and (not isinstance(group, str) or not group):
        raise EditError("'group' must be a group name or null")
    if (not isinstance(tracks, list) or not tracks or len(tracks) > 500
            or not all(isinstance(k, str) and k for k in tracks)):
        raise EditError("'tracks' must be a list of track keys")
    return list(dict.fromkeys(tracks))


def add_links(folder, source_folder, tracks, group: Optional[str] = None,
              metadata: Optional[tm.ThemeMetadata] = None, source_metadata: Optional[tm.ThemeMetadata] = None,
              save_metadata: Optional[Callable[[tm.ThemeMetadata], bool]] = None) -> dict:
    """
    Link files of another theme (source_folder) into this theme: nothing is
    copied. Each new linked track starts with the source theme's settings
    for it, muted; ungrouped (group None) it is Unassigned, in a group it is
    the group's. A file already linked there is not linked again ("added":
    false). Returns {"links": [{"key", "theme", "track", "added"}], "ops"}.
    Raises EditError / NotFound with the server's messages,
    theme_groups.GroupError or SaveFailed.
    """
    from sonorium.core import intrusions, theme_groups
    keys = clean_link_request(group, tracks)
    folder, source_folder = Path(folder), Path(source_folder)
    ops = FileOps(folder)
    metadata = metadata if metadata is not None else load(folder)
    if group is not None and group not in group_members(folder, metadata):
        raise NotFound("Group not found")
    source_metadata = source_metadata if source_metadata is not None else load(source_folder)
    if source_metadata.id == metadata.id:
        raise EditError("A theme's own files can't be linked; move them into the group instead")
    unknown = []
    for key in keys:
        try:
            theme_groups.find_track_file(source_folder, key)
        except theme_groups.GroupError:
            unknown.append(key)
    if unknown:
        raise NotFound(f"Not a track of the source theme: {', '.join(unknown)}")
    if group is not None:
        # The group's folder exists, even with only links in it
        ops.made_dir(theme_groups.group_target(folder, group))
    results = intrusions.add_links(metadata, folder, group, source_metadata.id, source_metadata, keys)
    _save_with(folder, metadata, save_metadata, "metadata.json couldn't be saved")
    return {"links": results, "ops": ops.done()}


def remove_link(folder, key: str, metadata: Optional[tm.ThemeMetadata] = None,
                save_metadata: Optional[Callable[[tm.ThemeMetadata], bool]] = None) -> dict:
    """
    Remove a linked file from the theme: its link, settings and preset
    entries (never a file). Returns {"removed": key, "ops"}. Raises NotFound
    ("Linked track not found") or SaveFailed.
    """
    from sonorium.core import intrusions
    folder = Path(folder)
    ops = FileOps(folder)
    metadata = metadata if metadata is not None else load(folder)
    if not intrusions.remove_link(metadata, key):
        raise NotFound("Linked track not found")
    _save_with(folder, metadata, save_metadata, "metadata.json couldn't be saved")
    return {"removed": key, "ops": ops.done()}


class CopyFailed(EditError):
    status = 500


def copy_link_in(folder, key: str, source_folder, group: Optional[str] = None,
                 metadata: Optional[tm.ThemeMetadata] = None,
                 save_metadata: Optional[Callable[[tm.ThemeMetadata], bool]] = None) -> dict:
    """
    Copy a linked file into the theme (the Theme Editor's "copy in"): the
    source file is copied (never moved) into group's folder, or the theme
    folder with group None ("x (2)" etc. on a clash), the link is removed and
    the track's settings, preset values and sequence entries move to its new
    key. `source_folder`: the folder of the link's theme (None when that
    theme is gone). Returns {"track": new key, "ops"}; the copy's "src" is
    the source file relative to source_folder. Raises NotFound ("Linked
    track not found", "Group not found"), Conflict (409, the source file is
    missing), theme_groups.GroupError, CopyFailed (500) or SaveFailed.
    """
    import shutil
    from sonorium.core import intrusions, theme_groups
    from sonorium.theme_files import track_display_name, track_key
    folder = Path(folder)
    ops = FileOps(folder)
    metadata = metadata if metadata is not None else load(folder)
    link = (getattr(metadata, "links", None) or {}).get(key)
    if link is None:
        raise NotFound("Linked track not found")
    if group is not None and (not isinstance(group, str) or group not in group_members(folder, metadata)):
        raise NotFound("Group not found")
    source = intrusions.resolve_link(link, lambda _id: None if source_folder is None else Path(source_folder))
    if source is None:
        raise Conflict("The linked file is missing; it can't be copied")
    target_folder = folder if group is None else theme_groups.group_target(folder, group)
    ops.made_dir(target_folder)
    # Another linked file there keeps its name; this one's key is free once it is copied in
    reserved = intrusions.link_names(metadata, group) - (
        {track_display_name(key)} if intrusions.link_group(key) == group else set())
    target = target_folder / theme_groups.free_file_name(target_folder, source.name, reserved=reserved)
    try:
        shutil.copy2(source, target)
    except OSError as e:
        raise CopyFailed(f"Couldn't copy the file: {e}")
    try:
        src = source.relative_to(Path(source_folder)).as_posix()
    except ValueError:
        src = str(source)
    ops.ops.append({"op": "copy", "src": src, "dst": ops.path(target)})
    new_key = track_key(folder, target)
    intrusions.remove_link_keep_settings(metadata, key)
    # Its settings, preset values and sequence entries move to the new key
    save_group_change(folder, metadata, theme_groups.Change(
        track_keys={key: new_key} if new_key != key else {}), save_metadata)
    return {"track": new_key, "ops": ops.done()}


UPLOAD_EXTENSIONS = ('.mp3', '.wav', '.flac', '.ogg')


def upload(folder, filename, content, group: Optional[str] = None, metadata=_UNSET, manager=None) -> dict:
    """
    Add an audio file to the theme (the Theme Editor's upload), in the theme
    folder or in group's folder (made if missing). `content`: the file's
    bytes, or the path of a file to copy. Only the file's own name is used;
    on a linked file's name it becomes "x (2)" etc.; a file of that name is
    replaced and keeps its settings, a new one starts Unassigned (not played
    until placed). Returns {"filename", "size", "group", "track", "ops"}.
    Raises theme_groups.GroupError (a bad name) or EditError (not .mp3, .wav,
    .flac or .ogg). `metadata`/`manager`: the server's loaded copy and its
    saver (metadata None: metadata.json is changed as it is on disk).
    """
    import shutil
    from sonorium.core import theme_groups
    folder = Path(folder)
    ops = FileOps(folder)
    filename = theme_groups.safe_upload_name(filename)
    ext = '.' + filename.split('.')[-1].lower() if '.' in filename else ''
    if ext not in UPLOAD_EXTENSIONS:
        raise EditError(f"Invalid file type. Supported: {', '.join(UPLOAD_EXTENSIONS)}")
    file_path = theme_groups.upload_path(folder, filename, group)
    target_folder = file_path.parent
    ops.made_dir(target_folder)
    group_name = target_folder.name if target_folder != folder else None
    if metadata is _UNSET:
        metadata = load(folder)
    # A linked file's name there is taken: " (2)" etc. instead
    if metadata is not None and metadata.links:
        from sonorium.core.intrusions import link_names
        reserved = link_names(metadata, group_name)
        if file_path.stem.casefold() in {n.casefold() for n in reserved}:
            file_path = target_folder / theme_groups.free_file_name(target_folder, file_path.name, reserved=reserved)
            filename = file_path.name
    key = f"{group_name}/{file_path.stem}" if group_name else file_path.stem
    is_new = not file_path.exists()
    if isinstance(content, (bytes, bytearray, memoryview)):
        file_path.write_bytes(content)
        size = len(content)
        ops.add("write", dst=file_path)
    else:
        shutil.copyfile(content, file_path)
        size = file_path.stat().st_size
        ops.add("copy", src=content, dst=file_path)
    if is_new:
        try:
            tm.add_unassigned_track(folder, key, metadata, manager)
        except OSError as e:
            # The file is there and plays as a track without settings would
            logger.warning(f"Theme '{folder.name}': '{key}' uploaded, but it couldn't be made Unassigned ({e})")
    # theme.changed even when no settings were written (a file uploaded over one)
    theme_presets.theme_changed(folder)
    return {"filename": filename, "size": size, "group": group_name, "track": key, "ops": ops.done()}
