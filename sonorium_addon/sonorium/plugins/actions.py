"""
The actions API: what a plugin may do with Sonorium, as one stable facade
(self.actions in a plugin). Plugins never import Sonorium's internals; these
methods keep their names and meaning across versions (new ones may be
added).

Every method is async and returns plain data (dicts, lists, strings) that
is safe to keep or send on. Failures raise ActionError with a code:
"not_found", "invalid", "conflict" or "unavailable". Changes made here are
the same as changes made in the UI: events are published (with origin
"plugin:<id>"), Home Assistant entities are updated, and so on.

A "channel" is what the UI calls a channel (internally a session).
"""

from __future__ import annotations

import contextlib
from typing import Callable, Optional

from sonorium.obs import logger
from sonorium.plugins.events import current_origin


class ActionError(Exception):
    """An action that couldn't be done. code: not_found, invalid, conflict, unavailable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Actions:
    """
    session_manager: the SessionManager
    state_store: the StateStore (playlists, speaker groups)
    registry: the speaker registry (HARegistry), optional
    on_change: async callback(session) after a change (Home Assistant entities)
    origin: "plugin:<id>" for a plugin's own facade
    """

    def __init__(self, session_manager, state_store, registry=None,
                 on_change: Optional[Callable] = None, origin: str = "sonorium"):
        self._sessions = session_manager
        self._state = state_store
        self._registry = registry
        self._on_change = on_change
        self.origin = origin
        self.overlay = None  # core/overlay_service.py, set by the plugin host

    def for_plugin(self, plugin_id: str) -> "Actions":
        actions = Actions(self._sessions, self._state, self._registry, self._on_change, f"plugin:{plugin_id}")
        actions.overlay = self.overlay
        return actions

    @contextlib.contextmanager
    def _as_origin(self):
        token = current_origin.set(self.origin)
        try:
            yield
        finally:
            current_origin.reset(token)

    def _need(self):
        if self._sessions is None:
            raise ActionError("unavailable", "Playback isn't available yet")
        return self._sessions

    def _session(self, channel_id: str):
        session = self._need().get(channel_id) if isinstance(channel_id, str) else None
        if session is None:
            raise ActionError("not_found", f"No channel '{channel_id}'")
        return session

    async def _changed(self, session) -> None:
        if self._on_change is not None:
            try:
                await self._on_change(session)
            except Exception as e:
                logger.debug(f"Actions: entity update after a change failed: {e}")

    def _channel(self, session) -> dict:
        manager = self._sessions
        theme = manager.get_theme(session.theme_id) if session.theme_id else None
        try:
            speakers = manager.get_resolved_speakers(session)
        except Exception:
            speakers = []
        return {
            "id": session.id,
            "name": session.name,
            "theme_id": session.theme_id,
            "theme_name": getattr(theme, "name", None),
            "preset_id": session.preset_id,
            "sequence_id": session.sequence_id,
            "playlist_id": session.playlist_id,
            "volume": session.volume,
            "is_playing": bool(session.is_playing),
            "speakers_muted": bool(session.speakers_muted),
            "announcements": bool(getattr(session, "announcements", True)),
            "speakers": list(speakers),
            "speaker_group_id": session.speaker_group_id,
            "entity_slug": session.get_entity_slug() if hasattr(session, "get_entity_slug") else None,
        }

    # --- Reading ---

    async def list_channels(self) -> list[dict]:
        return [self._channel(s) for s in self._need().list()]

    async def get_channel(self, channel_id: str) -> dict:
        return self._channel(self._session(channel_id))

    async def list_themes(self) -> list[dict]:
        themes = getattr(self._need(), "themes", None) or []
        return sorted(({"id": t.id, "name": t.name} for t in themes), key=lambda t: (t["name"] or "").lower())

    async def list_presets(self, theme_id: str) -> list[dict]:
        self._theme(theme_id)
        presets = self._sessions._theme_presets(theme_id)
        return [{"id": pid, "name": (p or {}).get("name", pid), "is_default": bool((p or {}).get("is_default"))}
                for pid, p in presets.items()]

    async def list_sequences(self, theme_id: str) -> list[dict]:
        self._theme(theme_id)
        sequences, _ = self._sessions.theme_sequences(theme_id)
        return [{"id": sid, "name": (s or {}).get("name", sid), "presets": list((s or {}).get("presets") or [])}
                for sid, s in sequences.items()]

    async def list_playlists(self) -> list[dict]:
        playlists = getattr(self._state, "playlists", None) or {}
        return [{"id": pid, "name": (p or {}).get("name", pid), "entries": len((p or {}).get("entries") or [])}
                for pid, p in playlists.items()]

    async def list_speakers(self) -> list[dict]:
        if self._registry is None:
            return []
        enabled = set(getattr(self._state.settings, "enabled_speakers", []) or [])
        result = []
        for speaker in self._registry.hierarchy.get_all_speakers():
            result.append({"id": speaker.entity_id, "name": speaker.name, "type": speaker.type,
                           "online": bool(speaker.online), "area_id": speaker.area_id,
                           "area_name": speaker.area_name, "enabled": speaker.entity_id in enabled})
        return result

    async def list_speaker_groups(self) -> list[dict]:
        groups = getattr(self._state, "speaker_groups", None) or {}
        return [{"id": gid, "name": g.name} for gid, g in groups.items()]

    def _theme(self, theme_id: str):
        theme = self._need().get_theme(theme_id) if isinstance(theme_id, str) else None
        if theme is None:
            raise ActionError("not_found", f"No theme '{theme_id}'")
        return theme

    # --- Channels ---

    async def create_channel(self, theme_id: Optional[str] = None, speakers: Optional[list] = None,
                             speaker_group_id: Optional[str] = None, name: Optional[str] = None,
                             preset_id: Optional[str] = None, volume: Optional[int] = None) -> dict:
        """A new channel (stopped). speakers: speaker ids; or speaker_group_id."""
        from sonorium.core.state import SpeakerSelection
        if theme_id is not None:
            self._theme(theme_id)
        if volume is not None:
            self._check_volume(volume)
        if speaker_group_id is not None and speaker_group_id not in (self._state.speaker_groups or {}):
            raise ActionError("not_found", f"No speaker group '{speaker_group_id}'")
        selection = SpeakerSelection(include_speakers=list(speakers)) if speakers else None
        with self._as_origin():
            try:
                session = self._need().create(theme_id=theme_id, preset_id=preset_id,
                                              speaker_group_id=speaker_group_id, adhoc_selection=selection,
                                              custom_name=name, volume=volume)
            except LookupError as e:
                raise ActionError("not_found", str(e).strip("'\""))
            except ValueError as e:
                raise ActionError("invalid", str(e))
        await self._changed(session)
        return self._channel(session)

    async def start_channel(self, channel_id: str) -> dict:
        """Play the channel on its speakers."""
        session = self._session(channel_id)
        if not session.theme_id:
            raise ActionError("invalid", "The channel has no theme")
        self._theme(session.theme_id)
        if not self._sessions.get_resolved_speakers(session):
            raise ActionError("invalid", "The channel has no speakers")
        refusal = self._sessions.channel_limit_refusal(session)
        if refusal:
            raise ActionError("conflict", refusal)
        with self._as_origin():
            ok = await self._sessions.play(channel_id)
        if not ok:
            raise ActionError("conflict", "The channel could not start (see the log)")
        await self._changed(session)
        return self._channel(session)

    async def stop_channel(self, channel_id: str) -> dict:
        """Stop the channel (it fades out first)."""
        session = self._session(channel_id)
        with self._as_origin():
            await self._sessions.stop(channel_id)
        await self._changed(session)
        return self._channel(session)

    async def pause_channel(self, channel_id: str) -> dict:
        session = self._session(channel_id)
        with self._as_origin():
            await self._sessions.pause(channel_id)
        await self._changed(session)
        return self._channel(session)

    async def stop_all(self) -> int:
        with self._as_origin():
            return await self._need().stop_all()

    async def set_theme(self, channel_id: str, theme_id: str, preset_id: Optional[str] = None) -> dict:
        """Change the channel's theme (crossfades if it plays); preset_id None = the theme's default."""
        self._session(channel_id)
        self._theme(theme_id)
        return await self._update(channel_id, theme_id=theme_id, preset_id=preset_id)

    async def set_preset(self, channel_id: str, preset_id: str) -> dict:
        """Glide the channel to a preset of its theme ("" = no preset)."""
        session = self._session(channel_id)
        if preset_id and preset_id not in self._sessions._theme_presets(session.theme_id or ""):
            raise ActionError("not_found", f"No preset '{preset_id}' in the channel's theme")
        return await self._update(channel_id, preset_id=preset_id)

    async def _update(self, channel_id: str, **changes) -> dict:
        with self._as_origin():
            try:
                session, added, removed = self._sessions.update(channel_id, **changes)
            except LookupError as e:
                raise ActionError("not_found", str(e).strip("'\""))
            except ValueError as e:
                raise ActionError("conflict", str(e))
            if session is None:
                raise ActionError("not_found", f"No channel '{channel_id}'")
            if added or removed:
                await self._sessions.apply_speaker_changes(session, added, removed)
        await self._changed(session)
        return self._channel(session)

    async def set_speakers(self, channel_id: str, speakers: Optional[list] = None,
                           speaker_group_id: Optional[str] = None) -> dict:
        """Play the channel on these speaker ids, or on a speaker group."""
        from sonorium.core.state import SpeakerSelection
        self._session(channel_id)
        if speaker_group_id is not None:
            if speaker_group_id not in (self._state.speaker_groups or {}):
                raise ActionError("not_found", f"No speaker group '{speaker_group_id}'")
            return await self._update(channel_id, speaker_group_id=speaker_group_id)
        if not isinstance(speakers, list):
            raise ActionError("invalid", "speakers must be a list of speaker ids")
        return await self._update(channel_id, adhoc_selection=SpeakerSelection(include_speakers=list(speakers)))

    @staticmethod
    def _check_volume(volume) -> int:
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0 <= volume <= 100:
            raise ActionError("invalid", "volume must be 0-100")
        return int(volume)

    async def set_volume(self, channel_id: str, volume: int) -> dict:
        session = self._session(channel_id)
        volume = self._check_volume(volume)
        with self._as_origin():
            await self._sessions.set_volume(channel_id, volume)
        await self._changed(session)
        return self._channel(session)

    async def mute_speakers(self, channel_id: str, muted: bool = True) -> dict:
        """Silence (or not) what the channel's speakers get; browsers listening keep hearing it."""
        session = self._session(channel_id)
        with self._as_origin():
            self._sessions.set_speakers_muted(channel_id, bool(muted))
        await self._changed(session)
        return self._channel(session)

    # --- Sequences and playlists ---

    async def start_sequence(self, channel_id: str, sequence_id: str) -> dict:
        session = self._session(channel_id)
        with self._as_origin():
            try:
                self._sessions.start_sequence(channel_id, sequence_id)
            except LookupError as e:
                raise ActionError("not_found", str(e).strip("'\""))
            except ValueError as e:
                raise ActionError("conflict", str(e))
        await self._changed(session)
        return self._channel(session)

    async def stop_sequence(self, channel_id: str) -> dict:
        session = self._session(channel_id)
        with self._as_origin():
            self._sessions.stop_sequence(channel_id)
        await self._changed(session)
        return self._channel(session)

    async def set_playlist(self, channel_id: str, playlist_id: str) -> dict:
        session = self._session(channel_id)
        with self._as_origin():
            try:
                self._sessions.set_playlist(channel_id, playlist_id)
            except LookupError as e:
                raise ActionError("not_found", str(e).strip("'\""))
            except ValueError as e:
                raise ActionError("conflict", str(e))
        await self._changed(session)
        return self._channel(session)

    async def clear_playlist(self, channel_id: str) -> dict:
        session = self._session(channel_id)
        with self._as_origin():
            self._sessions.clear_playlist(channel_id)
        await self._changed(session)
        return self._channel(session)

    async def next_step(self, channel_id: str) -> str:
        """Next playlist entry, or the sequence's next preset. Returns 'playlist', 'sequence' or 'end'."""
        session = self._session(channel_id)
        if not (session.is_playing or self._sessions.is_listened(session)):
            raise ActionError("conflict", "The channel is not playing")
        with self._as_origin():
            result = await self._sessions.skip(channel_id)
        if result is None:
            raise ActionError("conflict", "No playlist or sequence is running")
        await self._changed(session)
        return result

    # --- Sounds over channels (self.audio has every option) ---

    async def play_sound(self, channels, source, volume: float = 100, duck=True, policy: str = "queue",
                         priority: int = 0, start_stopped: bool = False) -> dict:
        """Play a sound file or URL over channels (ducking their mix). Returns what each channel did."""
        if self.overlay is None:
            raise ActionError("unavailable", "Playing sounds isn't available yet")
        from sonorium.core.decode import DecodeError
        from sonorium.core.overlay import OverlayError
        try:
            return await self.overlay.play(channels, source, volume=volume, duck=duck, policy=policy,
                                           priority=priority, start_stopped=start_stopped, origin=self.origin)
        except (OverlayError, DecodeError) as e:
            raise ActionError("invalid", str(e))

    async def cancel_sound(self, sound_id: str) -> bool:
        if self.overlay is None:
            raise ActionError("unavailable", "Playing sounds isn't available yet")
        return self.overlay.cancel(sound_id)
