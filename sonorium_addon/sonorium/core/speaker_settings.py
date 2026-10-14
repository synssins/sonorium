"""
Per-speaker settings (Settings > Speakers): a name override, the room
(Home Assistant area) the speaker is listed in, a volume offset, and which
path plays a speaker that both Home Assistant and network discovery found.

Stored in SonoriumSettings.speaker_settings, keyed by speaker ID:
    {"media_player.office": {"name": "Office", "room": "office",
                             "volume_offset": -10, "play_via": "net:dlna:abc",
                             "sync": 4200, "plugins": {"wled": {"light_sync": 300}}}}
Only keys that differ from the default are stored. "room" is an area ID, or
"" for "no room"; a missing "room" means the speaker's own Home Assistant area.

"sync" is Sync: how far behind the mixer the speaker plays, in milliseconds
(its own buffer of the stream). Without it the estimate for the speaker's
type is used (SPEAKER_BUFFER_SECONDS). Status's "as heard" and anything timed
to the sound (plugins, later light cues) use this one number (sync_ms).

"plugins" holds plugin attributes on the speaker (object class "speaker",
core/plugin_data.py): only declared attributes, checked against the schema.

SpeakerOutputs wraps the media controller the session manager uses, so every
play/stop/volume call honours play_via and the volume offsets. No network
imports here: the HA add-on loads this module too.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

OFFSET_MIN = -20
OFFSET_MAX = 20
NAME_MAX_LENGTH = 100
PLAY_VIA_HA = "ha"
SETTING_KEYS = ("name", "room", "volume_offset", "play_via", "sync", "plugins")

# How far behind the mixer each kind of speaker plays (seconds): its own
# buffer of the stream, estimated. A speaker's Sync starts from this.
SPEAKER_BUFFER_SECONDS = {
    "cast": 5.0,
    "sonos": 3.0,
    "airplay": 2.0,
    "dlna": 4.0,
    "linkplay": 3.0,
    "heos": 4.0,
    "denon": 4.0,
    "esphome": 1.5,
}
DEFAULT_SPEAKER_BUFFER_SECONDS = 3.0


class SpeakerSettingsError(ValueError):
    """An invalid speaker setting (message is shown to the user)."""


def estimated_sync_ms(speaker_type: Optional[str]) -> int:
    """The Sync estimate for a kind of speaker, in milliseconds."""
    return int(round(SPEAKER_BUFFER_SECONDS.get(speaker_type or "", DEFAULT_SPEAKER_BUFFER_SECONDS) * 1000))


def sync_ms(settings: Optional[dict], speaker_type: Optional[str]) -> int:
    """A speaker's Sync in milliseconds: its own setting, else the estimate for its type."""
    value = (settings or {}).get("sync")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return estimated_sync_ms(speaker_type)


def clean_speaker_settings(current: dict, updates: dict) -> dict:
    """
    `current` with `updates` applied and validated. An update value of None
    removes that setting (back to the default). Returns only non-default keys.

    "plugins" is {plugin_id: {attribute: value or None} or None}: each plugin's
    values are checked against its declared speaker attributes
    (core/plugin_data.py); other plugins' values stay.
    """
    result = dict(current or {})
    for key, value in updates.items():
        if key not in SETTING_KEYS:
            raise SpeakerSettingsError(f"Unknown speaker setting '{key}'")
        if value is None:
            result.pop(key, None)
            continue
        if key == "plugins":
            if not isinstance(value, dict):
                raise SpeakerSettingsError("plugins must be an object of plugin ids")
            from sonorium.core import plugin_data
            block = result.get("plugins") or {}
            try:
                for plugin_id, values in value.items():
                    block = plugin_data.registry.update_block(block, plugin_id, "speaker", values)
            except plugin_data.PluginDataError as e:
                raise SpeakerSettingsError(str(e))
            if block:
                result["plugins"] = block
            else:
                result.pop("plugins", None)
        elif key == "sync":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (
                    isinstance(value, float) and not value.is_integer()):
                raise SpeakerSettingsError("Sync must be a whole number of milliseconds")
            if value < 0:
                raise SpeakerSettingsError("Sync can't be negative")
            result["sync"] = int(value)
        elif key == "name":
            name = str(value).strip()
            if len(name) > NAME_MAX_LENGTH:
                raise SpeakerSettingsError(f"Name is too long (at most {NAME_MAX_LENGTH} characters)")
            if name:
                result["name"] = name
            else:
                result.pop("name", None)  # empty name = the original name
        elif key == "room":
            result["room"] = str(value).strip()
        elif key == "volume_offset":
            try:
                offset = int(value)
            except (TypeError, ValueError):
                raise SpeakerSettingsError("Volume offset must be a whole number")
            if not OFFSET_MIN <= offset <= OFFSET_MAX:
                raise SpeakerSettingsError(f"Volume offset must be between {OFFSET_MIN} and +{OFFSET_MAX}")
            if offset:
                result["volume_offset"] = offset
            else:
                result.pop("volume_offset", None)
        elif key == "play_via":
            via = str(value).strip()
            if via and via != PLAY_VIA_HA:
                result["play_via"] = via
            else:
                result.pop("play_via", None)
    return result


def offset_volume(volume_level: float, offset: int) -> float:
    """Volume (0.0-1.0) with a percentage offset: clamp(volume + offset, 0, 100)."""
    percent = round(volume_level * 100) + int(offset or 0)
    return max(0, min(100, percent)) / 100.0


class SpeakerOutputs:
    """
    Stands in for the media controller (HAMediaController or SpeakerRouter):
    - play_target(id) picks the path for merged duplicates (play_via), so a
      merged speaker keeps its Home Assistant ID in selections;
    - set_volume_multi adds each speaker's volume offset.
    Results are reported under the IDs the caller passed.
    """

    def __init__(
        self,
        controller,
        settings_source: Callable[[], dict],
        play_target: Optional[Callable[[str], str]] = None,
    ):
        self.controller = controller
        self._settings_source = settings_source
        self._play_target = play_target

    def _settings(self) -> dict:
        try:
            return self._settings_source() or {}
        except Exception:
            return {}

    def target_for(self, speaker_id: str) -> str:
        """The ID to send commands to (a network speaker ID when play_via chooses it)."""
        if self._play_target is None:
            return speaker_id
        try:
            return self._play_target(speaker_id) or speaker_id
        except Exception:
            return speaker_id

    def volume_offset(self, speaker_id: str) -> int:
        return int((self._settings().get(speaker_id) or {}).get("volume_offset", 0) or 0)

    async def _mapped(self, method: str, speaker_ids, *args) -> dict:
        ids = list(speaker_ids or [])
        targets = {sid: self.target_for(sid) for sid in ids}
        unique = list(dict.fromkeys(targets.values()))
        result = await getattr(self.controller, method)(unique, *args) if unique else {}
        result = result or {}
        return {sid: result.get(target, False) for sid, target in targets.items()}

    async def play_media_multi(self, entity_ids, media_url: str, media_type: str = "music") -> dict:
        return await self._mapped("play_media_multi", entity_ids, media_url, media_type)

    async def stop_multi(self, entity_ids) -> dict:
        return await self._mapped("stop_multi", entity_ids)

    async def pause_multi(self, entity_ids) -> dict:
        return await self._mapped("pause_multi", entity_ids)

    async def get_playing_states(self, entity_ids) -> dict:
        return await self._mapped("get_playing_states", entity_ids)

    async def set_volume_multi(self, entity_ids, volume_level: float) -> dict:
        """Set each speaker to volume + its offset; speakers with the same level share one call."""
        by_level: dict[float, list[str]] = {}
        for sid in list(entity_ids or []):
            level = offset_volume(volume_level, self.volume_offset(sid))
            by_level.setdefault(level, []).append(sid)
        results = await asyncio.gather(
            *(self._mapped("set_volume_multi", ids, level) for level, ids in by_level.items()),
            return_exceptions=True,
        )
        status: dict[str, Any] = {}
        for ids, result in zip(by_level.values(), results):
            if isinstance(result, BaseException):
                status.update({sid: False for sid in ids})
            else:
                status.update(result)
        return status

    def __getattr__(self, name):
        # Everything else (get_state, play_media, ...) goes to the wrapped controller
        controller = self.__dict__.get("controller")
        if controller is None:
            raise AttributeError(name)
        return getattr(controller, name)
