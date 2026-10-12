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
"""
from __future__ import annotations

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

def load(folder) -> tm.ThemeMetadata:
    """A theme folder's metadata, with its presets."""
    return tm.load_theme_folder(Path(folder))


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
