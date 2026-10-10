"""
Playlists: themes played in order on a channel (docs/PLAYBACK.md).

Global, stored in state.json under "playlists" ({playlist_id: data}):

    {
      "name": "Evening",
      "entries": [
        {"theme_id": "...", "preset_id": "slow_night", "sequence_id": null},
        {"theme_id": "...", "preset_id": null, "sequence_id": "gentle_storm"}
      ],
      "order": "in_order" | "shuffle",
      "repeat": true,
      "play_min": 1800, "play_max": 3600,
      "fit_sequences": false,
      "owner": null
    }

Each entry plays for a random time between play_min and play_max seconds
(one range for the whole playlist, drawn fresh for every entry), then the
next one starts. An entry with a sequence plays one full pass of it (each
of its presets once, at the sequence's own hold and ramp times) instead;
with fit_sequences the pass is squeezed into a play_min-play_max time like
any other entry. The runtime is in core/playback_runner.py.

Theme cycling (a channel's cycle_config) was replaced by playlists:
migrate_cycling() turns each channel's cycling into a playlist once.
"""

from __future__ import annotations

import uuid
from typing import Callable, Optional

from sonorium.obs import logger

ORDERS = ("in_order", "shuffle")
DEFAULT_PLAY_MIN = 1800.0
DEFAULT_PLAY_MAX = 3600.0
MIN_PLAY_SECONDS = 1.0
MAX_PLAY_SECONDS = 7 * 24 * 3600
MAX_NAME = 100
MAX_ENTRIES = 500


class PlaylistError(ValueError):
    """A playlist that can't be saved: the message says why."""


def _seconds(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlaylistError(f"{name} must be a number of seconds")
    value = float(value)
    if value != value or value < MIN_PLAY_SECONDS or value > MAX_PLAY_SECONDS:
        raise PlaylistError(f"{name} must be between {MIN_PLAY_SECONDS:g} and {MAX_PLAY_SECONDS} seconds")
    return value


def clean_entry(entry, theme_exists: Callable[[str], bool],
                theme_presets: Callable[[str], set], theme_sequences: Callable[[str], set]) -> dict:
    """One checked entry: a known theme, plus at most one of its presets or sequences."""
    if not isinstance(entry, dict):
        raise PlaylistError("each entry must be an object")
    theme_id = entry.get("theme_id")
    if not isinstance(theme_id, str) or not theme_id:
        raise PlaylistError("each entry needs a theme_id")
    if not theme_exists(theme_id):
        raise PlaylistError(f"unknown theme '{theme_id}'")
    preset_id, sequence_id = entry.get("preset_id"), entry.get("sequence_id")
    if preset_id is not None and sequence_id is not None:
        raise PlaylistError("an entry has a preset or a sequence, not both")
    if preset_id is not None:
        if not isinstance(preset_id, str) or preset_id not in theme_presets(theme_id):
            raise PlaylistError(f"unknown preset '{preset_id}' for theme '{theme_id}'")
    if sequence_id is not None:
        if not isinstance(sequence_id, str) or sequence_id not in theme_sequences(theme_id):
            raise PlaylistError(f"unknown sequence '{sequence_id}' for theme '{theme_id}'")
    return {"theme_id": theme_id, "preset_id": preset_id, "sequence_id": sequence_id}


def clean_playlist(data: dict, theme_exists, theme_presets, theme_sequences,
                   current: Optional[dict] = None) -> dict:
    """A checked playlist from request data; with `current`, only the fields sent change."""
    if not isinstance(data, dict):
        raise PlaylistError("expected a JSON object")
    result = dict(current or {})
    result.setdefault("order", "in_order")
    result.setdefault("repeat", True)
    result.setdefault("play_min", DEFAULT_PLAY_MIN)
    result.setdefault("play_max", DEFAULT_PLAY_MAX)
    result.setdefault("fit_sequences", False)
    result.setdefault("owner", None)

    if "name" in data or current is None:
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise PlaylistError("name is required")
        if len(name.strip()) > MAX_NAME:
            raise PlaylistError(f"name must be at most {MAX_NAME} characters")
        result["name"] = name.strip()

    if "entries" in data or current is None:
        entries = data.get("entries")
        if not isinstance(entries, list) or not entries:
            raise PlaylistError("entries must be a list of at least one entry")
        if len(entries) > MAX_ENTRIES:
            raise PlaylistError(f"a playlist has at most {MAX_ENTRIES} entries")
        result["entries"] = [clean_entry(e, theme_exists, theme_presets, theme_sequences) for e in entries]

    if "order" in data:
        if data["order"] not in ORDERS:
            raise PlaylistError(f"order must be one of {', '.join(ORDERS)}")
        result["order"] = data["order"]

    if "repeat" in data:
        if not isinstance(data["repeat"], bool):
            raise PlaylistError("repeat must be true or false")
        result["repeat"] = data["repeat"]

    if "fit_sequences" in data:
        if not isinstance(data["fit_sequences"], bool):
            raise PlaylistError("fit_sequences must be true or false")
        result["fit_sequences"] = data["fit_sequences"]

    for key in ("play_min", "play_max"):
        if key in data:
            result[key] = _seconds(data[key], key)
    if result["play_min"] > result["play_max"]:
        raise PlaylistError("play_min must not be more than play_max")

    if "owner" in data and data["owner"] is not None:
        raise PlaylistError("owner must be null (personal playlists are not available yet)")
    return result


def new_playlist_id(existing) -> str:
    while True:
        playlist_id = uuid.uuid4().hex[:8]
        if playlist_id not in existing:
            return playlist_id


def to_response(playlist_id: str, playlist: dict) -> dict:
    return {
        "id": playlist_id,
        "name": playlist.get("name", playlist_id),
        "entries": [dict(e) for e in playlist.get("entries") or []],
        "order": playlist.get("order", "in_order"),
        "repeat": bool(playlist.get("repeat", True)),
        "play_min": playlist.get("play_min", DEFAULT_PLAY_MIN),
        "play_max": playlist.get("play_max", DEFAULT_PLAY_MAX),
        "fit_sequences": bool(playlist.get("fit_sequences", False)),
        "owner": playlist.get("owner"),
    }


def migrate_cycling(state, all_theme_ids: list[str]) -> int:
    """
    Turn each channel's theme cycling (cycle_config) into a playlist, once.

    - Cycling on: a playlist "<channel> cycle" with its themes (all themes if
      it named none), each playing for exactly the old interval, shuffled if
      it was random, repeating; the channel plays it (from its current theme).
    - Cycling off but a theme list set: the playlist is made but not assigned,
      so the list isn't lost.
    - Cycling off with no list: nothing.

    `state` is a SonoriumState. Returns how many playlists were made; sets
    state.cycling_migrated so it never runs again.
    """
    if getattr(state, "cycling_migrated", False):
        return 0
    made = 0
    for session in state.sessions.values():
        cycle = getattr(session, "cycle_config", None)
        if cycle is None:
            continue
        enabled = bool(getattr(cycle, "enabled", False))
        theme_ids = [t for t in (getattr(cycle, "theme_ids", None) or []) if t]
        if not enabled and not theme_ids:
            continue
        if not theme_ids:
            theme_ids = list(all_theme_ids)
        if not theme_ids:
            logger.warning(f"Channel '{session.name}': theme cycling had no themes, nothing to convert")
            continue
        seconds = float(max(1, int(getattr(cycle, "interval_minutes", 60) or 60)) * 60)
        playlist_id = new_playlist_id(state.playlists)
        state.playlists[playlist_id] = {
            "name": f"{session.name} cycle",
            "entries": [{"theme_id": t, "preset_id": None, "sequence_id": None} for t in theme_ids],
            "order": "shuffle" if getattr(cycle, "randomize", False) else "in_order",
            "repeat": True,
            "play_min": seconds,
            "play_max": seconds,
            "fit_sequences": False,
            "owner": None,
        }
        made += 1
        if enabled:
            session.playlist_id = playlist_id
            session.playlist_order = list(range(len(theme_ids)))
            session.playlist_position = theme_ids.index(session.theme_id) if session.theme_id in theme_ids else 0
            if session.theme_id not in theme_ids:
                session.theme_id = theme_ids[session.playlist_order[session.playlist_position]]
                session.preset_id = None
            logger.info(f"Channel '{session.name}': theme cycling (every {seconds / 60:g} min, "
                        f"{len(theme_ids)} themes) converted to playlist '{session.name} cycle', which it now plays")
        else:
            logger.info(f"Channel '{session.name}': its cycling theme list was saved as playlist "
                        f"'{session.name} cycle' (cycling was off, so the channel doesn't play it)")
    state.cycling_migrated = True
    if made:
        logger.info(f"Theme cycling is replaced by playlists: {made} playlist(s) made from channels' cycling settings")
    return made
