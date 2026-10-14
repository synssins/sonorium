"""
Session Manager

Handles CRUD operations for playback sessions, including:
- Creating sessions with auto-naming
- Updating session configuration
- Play/pause/stop control with channel-based streaming
- Volume management
- Seamless theme transitions via channel crossfading
- Start/stop fades, preset glides, sequences and playlists (core/playback_runner.py)
- A policy (core/channel_policy.py, Zones) may refuse changes and keep a
  speaker for one channel at a time
"""

from __future__ import annotations

import asyncio
import secrets
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

from sonorium.core.state import (
    Session,
    SpeakerSelection,
    SpeakerGroup,
    NameSource,
    StateStore,
)
from sonorium.obs import logger

# Stop a sessiononce its channel has had no listeners this long. Long enough
# to cover a Cast device's ~15s start-up and brief reconnects.
IDLE_RELEASE_SECONDS = 90

# Picking a preset by hand glides volume and presence over this long (the same
# as a track turning on or off, theme.TRACK_TOGGLE_FADE_SECONDS)
PRESET_GLIDE_SECONDS = 3.0

# Settings > Audio Settings > Crossfade when the setting can't be read
DEFAULT_CROSSFADE_SECONDS = 3.0

# A channel's card lists this many of its latest changes (Session.recent)
RECENT_EVENTS = 20

# Listen here: a channel's mix kept running for browsers alone is released
# once no browser has listened for this long (POST .../listen can ask for
# longer, up to LISTEN_HOLD_MAX_SECONDS, e.g. a phone on flaky Wi-Fi)
LISTEN_RELEASE_SECONDS = 15
LISTEN_HOLD_MAX_SECONDS = 300

# Personal channels: one hidden channel per account, heard only on its devices
PERSONAL_NAME = "Personal"
PERSONAL_NO_SPEAKERS = "A personal channel plays only on your own devices: it can't have speakers"


class PersonalSpeakersRefused(ValueError):
    """Speakers were asked for on a personal channel."""

if TYPE_CHECKING:
    from sonorium.ha.registry import HARegistry
    from sonorium.ha.media_controller import HAMediaController
    from sonorium.core.channel import ChannelManager, Channel
    from sonorium.core.theme_metadata import ThemeMetadataManager
    from sonorium.theme import LoadedTheme
    from sonorium.utils import IndexList


class SessionManager:
    """
    Manages playback sessions.
    
    Each session represents one theme playing to one set of speakers.
    Multiple sessions can run simultaneously on different channels.
    """

    policy = None  # core/channel_policy.py (Zones); None: every channel open

    def __init__(
        self,
        state_store: StateStore,
        ha_registry: HARegistry,
        media_controller: HAMediaController = None,
        stream_base_url: str = None,
        channel_manager: ChannelManager = None,
        themes: IndexList[LoadedTheme] = None,
        theme_metadata_manager: ThemeMetadataManager = None,
    ):
        self.state = state_store
        self.registry = ha_registry
        self.media_controller = media_controller
        self.stream_base_url = stream_base_url or "http://localhost:8080"
        self.channel_manager = channel_manager
        self.themes = themes
        self.theme_metadata_manager = theme_metadata_manager

        # Track which session is using which channel: session_id -> channel_id
        self._session_channels: dict[str, int] = {}

        # Sessions fading out before they stop or pause: session_id -> token,
        # so playing again during the fade cancels the stop
        self._fading: dict[str, object] = {}

        # Sessions a browser listens to (Listen here): their mix runs whether
        # or not their speakers play, until no browser listens
        self._listening: set[str] = set()
        self._listeners: dict[str, int] = {}  # how many browsers listen to each
        self._listen_hold: dict[str, float] = {}  # seconds a mix waits for its browsers to come back

        # Devices the app reports playing a channel (a Cast device): their mix
        # runs like a listener's while they are reported (core/outputs.py)
        from sonorium.core.outputs import OutputRegistry
        self.outputs = OutputRegistry()

        # The framework's audio services (sounds on a stopped channel, audio
        # outputs): predicates that keep a channel's mix running (add_mix_hold)
        self._mix_holds: list = []

        # Speakers of other kinds, played without the media controller (a
        # plugin's audio output devices, core/audio_outputs.py): add_speaker_kind
        self._speaker_kinds: list = []

        # Sequence and playlist timers
        from sonorium.core.playback_runner import PlaybackRunner
        self.runner = PlaybackRunner(self)

        # Sleep timers: a channel fades out and stops at a set time (core/sleep_timer.py)
        from sonorium.core.sleep_timer import SleepTimers
        self.sleep = SleepTimers(self)

        # Who may change a channel, and its speakers in use (core/channel_policy.py):
        # None = every channel open, as without zones
        self.policy = None

        # Personal channels' listening devices: session id -> {device key:
        # {"tag": stream tag, "since": when it asked, "hold": seconds}}
        self._personal_devices: dict[str, dict[str, dict]] = {}
        self._clock = time.monotonic

    # --- The channel policy (Zones) ---

    def guard(self, action: str, session: Optional[Session], **details) -> None:
        """Raise ChannelRefused when the policy refuses `action` on the channel (never on a personal one)."""
        check = getattr(self.policy, "check", None)
        if check is not None and session is not None and not self.is_personal(session):
            check(action, session, **details)

    def allowed(self, action: str, session: Session, **details) -> bool:
        if self.policy is None or self.is_personal(session):
            return True
        from sonorium.core.channel_policy import ChannelRefused
        try:
            self.guard(action, session, **details)
        except ChannelRefused:
            return False
        return True

    def _tell(self, what: str, session: Session) -> None:
        """Tell the policy the channel changed, stopped or went (never raises)."""
        hook = getattr(self.policy, what, None)
        if hook is None or self.is_personal(session):
            return
        try:
            hook(session)
        except Exception as e:
            logger.warning(f"Channel policy ({what}) failed for '{session.name}': {e}")
    
    def set_media_controller(self, controller: HAMediaController):
        """Set the media controller (for deferred initialization)."""
        self.media_controller = controller
    
    def set_stream_base_url(self, url: str):
        """Set the stream base URL."""
        self.stream_base_url = url.rstrip("/")
    
    def set_themes(self, themes: IndexList[LoadedTheme]):
        """Update themes reference (called after theme refresh)."""
        self.themes = themes
        logger.info(f"  SessionManager: Updated themes reference ({len(themes)} themes)")

    def set_theme_metadata_manager(self, manager: ThemeMetadataManager):
        """Set the theme metadata manager (for deferred initialization)."""
        self.theme_metadata_manager = manager

    def crossfade_seconds(self) -> float:
        """Settings > Audio Settings > Crossfade: theme changes and channel start/stop fades."""
        try:
            return max(0.0, float(self.state.settings.crossfade_duration))
        except (AttributeError, TypeError, ValueError):
            return DEFAULT_CROSSFADE_SECONDS

    def theme_folder(self, theme_id: str):
        """A theme's folder on disk, or None."""
        if not theme_id:
            return None
        if self.theme_metadata_manager:
            folder = self.theme_metadata_manager.get_folder_for_id(theme_id)
            if folder:
                return folder
        import json
        if self.themes and len(self.themes) > 0:
            first_theme = self.themes[0]
            if hasattr(first_theme, 'library') and hasattr(first_theme.library, 'audio_dir'):
                for folder in first_theme.library.audio_dir.iterdir():
                    metadata_path = folder / "metadata.json"
                    if folder.is_dir() and metadata_path.exists():
                        try:
                            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
                        except Exception:
                            continue
                        if isinstance(metadata, dict) and metadata.get("id") == theme_id:
                            return folder
        return None

    def theme_sequences(self, theme_id: str) -> tuple[dict, dict]:
        """A theme's sequences ({id: data}) and sequence_defaults, from its presets.json."""
        from sonorium.core.sequences import DEFAULTS, load_defaults, load_sequences
        folder = self.theme_folder(theme_id)
        if folder is None:
            return {}, dict(DEFAULTS)
        return load_sequences(folder), load_defaults(folder)

    def _theme_presets(self, theme_id: str) -> dict:
        """A theme's presets ({preset_id: data}), from the metadata cache or its presets.json."""
        from sonorium.core.theme_presets import load_presets

        if self.theme_metadata_manager:
            folder = self.theme_metadata_manager.get_folder_for_id(theme_id)
            if folder:
                metadata = self.theme_metadata_manager.get_metadata_by_folder(folder)
                if metadata:
                    return dict(metadata.presets or {})
                return load_presets(folder)

        # Fallback: find the folder by the id in its metadata.json
        import json
        if self.themes and len(self.themes) > 0:
            first_theme = self.themes[0]
            if hasattr(first_theme, 'library') and hasattr(first_theme.library, 'audio_dir'):
                for folder in first_theme.library.audio_dir.iterdir():
                    metadata_path = folder / "metadata.json"
                    if folder.is_dir() and metadata_path.exists():
                        try:
                            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                        except Exception:
                            continue
                        if isinstance(metadata, dict) and metadata.get("id") == theme_id:
                            return load_presets(folder)
        return {}

    def preset_for_theme(self, theme_id: str, requested: Optional[str] = None) -> Optional[str]:
        """
        The preset a channel uses with a theme: the requested one if the theme
        has it, "" for none on purpose, else the theme's default preset, else
        none. A preset never carries over from a different theme.
        """
        if requested == "" or not theme_id:
            return None
        presets = self._theme_presets(theme_id)
        if requested and requested in presets:
            return requested
        for preset_id, data in presets.items():
            if (data or {}).get("is_default"):
                return preset_id
        return None

    def preset_name(self, theme_id: Optional[str], preset_id: Optional[str]) -> str:
        """A preset's name for the channel's recent changes ("Default settings" for none)."""
        if not preset_id:
            return "Default settings"
        return (self._theme_presets(theme_id).get(preset_id) or {}).get("name", preset_id) if theme_id else preset_id

    def note(self, session: Session, text: str) -> None:
        """
        Add a change to the channel's recent changes (saved with the next state
        save). The same text twice in a row is kept once.
        """
        recent = session.recent
        if recent and recent[-1]["text"] == text:
            return
        recent.append({"at": datetime.now(timezone.utc).isoformat(), "text": text})
        del recent[:-RECENT_EVENTS]

    def publish(self, event_type: str, session: Session, **data) -> None:
        """
        Publish a channel event on the plugin event bus (never raises or waits).
        A personal channel's events carry "personal": true and reach only its
        account's event streams (plugins don't get them).
        """
        try:
            from sonorium.plugins import events
            owner = getattr(session, "owner", None)
            if owner:
                events.publish(event_type, {"channel_id": session.id, "name": session.name, **data, "personal": True},
                               audience=owner)
                return
            events.publish(event_type, {"channel_id": session.id, "name": session.name, **data})
        except Exception as e:  # the event bus must never get in the way of playback
            logger.debug(f"Event {event_type} not published: {e}")

    def _snapshot(self, session: Session) -> dict:
        """What channel.changed reports changes of."""
        return {"theme_id": session.theme_id, "preset_id": session.preset_id,
                "sequence_id": session.sequence_id, "playlist_id": session.playlist_id,
                "volume": session.volume, "speakers_muted": session.speakers_muted, "name": session.name,
                "speakers": sorted(self.get_resolved_speakers(session))}

    def publish_changes(self, session: Session, before: dict) -> None:
        """channel.changed with the fields that differ from `before` (a _snapshot), if any."""
        try:
            after = self._snapshot(session)
        except Exception:
            return
        changes = {k: v for k, v in after.items() if before.get(k) != v}
        if changes:
            self.publish("channel.changed", session, changes=changes)

    def preset_overrides(self, theme_id: str, preset_id: Optional[str], step: Optional[dict] = None) -> dict:
        """
        A channel's preset layer for a theme ({} for no preset, or one that
        isn't found), with a sequence step's While playing values on top
        (core/sequences.merge_step: a null keeps the preset's value).
        """
        from sonorium.core.sequences import merge_step
        from sonorium.recording import preset_group_overrides, preset_track_overrides
        if not preset_id:
            return {}
        preset = self._theme_presets(theme_id).get(preset_id)
        if not preset:
            logger.warning(f"  Preset '{preset_id}' not found for theme '{theme_id}'")
            return {}
        preset = merge_step(preset, step)
        return {**preset_track_overrides(preset.get("tracks", {})), **preset_group_overrides(preset.get("groups", {}))}

    def step_layer(self, session: Session, preset_id: Optional[str] = None) -> Optional[dict]:
        """The While playing layer of the sequence step the channel is on (None when no sequence runs)."""
        preset_id = preset_id if preset_id is not None else session.preset_id
        if not session.sequence_id or not preset_id:
            return None
        sequence = self.runner.sequence(session.theme_id, session.sequence_id)
        return ((sequence or {}).get("while") or {}).get(preset_id)

    def session_overrides(self, session: Session, preset_id: Optional[str] = None) -> dict:
        """What the channel plays: its preset, with its sequence step's While playing values on top."""
        preset_id = preset_id if preset_id is not None else session.preset_id
        return self.preset_overrides(session.theme_id, preset_id, self.step_layer(session, preset_id))

    def step_saved(self, theme_ids, sequence_id: str, preset_id: str) -> int:
        """A step's While playing layer was saved: channels on that step glide to it. Returns how many."""
        theme_ids = {t for t in theme_ids if t}
        count = 0
        for session in self._all_sessions():
            if (self._live(session) and session.theme_id in theme_ids and session.sequence_id == sequence_id
                    and session.preset_id == preset_id):
                channel = self._channel_for(session)
                if channel:
                    channel.set_track_overrides(self.session_overrides(session), glide_seconds=PRESET_GLIDE_SECONDS)
                    count += 1
        return count

    def preset_saved(self, theme_ids, preset_id: str) -> int:
        """
        A preset was saved: channels playing it (by hand or as a sequence's
        step) glide to its new values. A channel keeps its own copy of the
        preset from when it was picked, so without this it went on playing
        the old one. Returns how many channels followed.
        """
        theme_ids = {t for t in theme_ids if t}
        count = 0
        for session in self._all_sessions():
            if not (self._live(session) and session.theme_id in theme_ids and session.preset_id == preset_id):
                continue
            channel = self._channel_for(session)
            if channel:
                channel.set_track_overrides(self.session_overrides(session, preset_id),
                                            glide_seconds=PRESET_GLIDE_SECONDS)
                count += 1
        if count:
            logger.info(f"  Preset '{preset_id}' saved: {count} playing channel(s) follow it")
        return count

    def set_announcements(self, session_id: str, on: bool) -> bool:
        """Let announcements play on the channel, or not (e.g. a nap). False: no such channel."""
        session = self._find(session_id)
        if session is None:
            return False
        self.guard("mute", session)
        session.announcements = bool(on)
        self.note(session, "Announcements on" if on else "Announcements off")
        self.publish("channel.changed", session, changes={"announcements": session.announcements})
        self.state.save()
        return True

    def set_speakers_muted(self, session_id: str, muted: bool) -> bool:
        """Silence (or not) the speakers' stream; browsers listening keep hearing it. False: no such channel."""
        session = self.state.sessions.get(session_id)
        if session is None:
            return False
        self.guard("mute", session)
        session.speakers_muted = bool(muted)
        channel = self.channel_manager.get_channel(self._session_channels.get(session_id) or 0) if self.channel_manager else None
        if channel is not None:
            channel.speakers_muted = session.speakers_muted
        self.note(session, "Speakers muted" if muted else "Speakers unmuted")
        self.publish("channel.changed", session, changes={"speakers_muted": session.speakers_muted})
        self.state.save()
        return True

    def is_listened(self, session: Session) -> bool:
        """A browser listens to the channel."""
        return session.id in self._listening

    def _live(self, session: Session) -> bool:
        """The channel's mix runs: its speakers play, a browser listens, or the app reports a device playing it."""
        return session.is_playing or session.id in self._listening or self.outputs.held(session.id)

    def _kept(self, session_id: str) -> bool:
        """Something other than the speakers keeps the channel's mix: a browser listening, a reported output, a hook."""
        return session_id in self._listening or self.outputs.held(session_id) or self._held_by_hooks(session_id)

    # --- Hooks for the framework's audio services (core/overlay_service.py,
    # core/audio_outputs.py): they reach a channel's Channel and keep its mix
    # running through these, never through the session manager's internals ---

    def add_speaker_kind(self, kind) -> None:
        """
        Speakers this Sonorium plays itself, not through the media controller:
        kind.claims(speaker_id) -> bool, async kind.start(session, ids,
        take_over=None), async kind.stop(session, ids=None) (all of the
        channel's when None), kind.conflicts(session, ids) -> [sentence].
        """
        self._speaker_kinds.append(kind)

    def speaker_kind(self, speaker_id: str):
        """The kind that plays this speaker, None for the media controller's speakers."""
        for kind in self._speaker_kinds:
            try:
                if kind.claims(speaker_id):
                    return kind
            except Exception:
                continue
        return None

    def media_speakers(self, speakers) -> list[str]:
        """The speakers the media controller plays (the others left out)."""
        return [s for s in speakers or [] if self.speaker_kind(s) is None]

    def _kind_speakers(self, speakers) -> list[tuple]:
        """[(kind, its speakers)] for the speakers of other kinds."""
        groups: list[tuple] = []
        for speaker_id in speakers or []:
            kind = self.speaker_kind(speaker_id)
            if kind is None:
                continue
            for existing, ids in groups:
                if existing is kind:
                    ids.append(speaker_id)
                    break
            else:
                groups.append((kind, [speaker_id]))
        return groups

    def speaker_conflicts(self, session: Session) -> list[str]:
        """Why some of the channel's speakers can't play it now unless taken over (one local device at a time)."""
        messages = []
        for kind, ids in self._kind_speakers(self.get_resolved_speakers(session)):
            try:
                messages.extend(kind.conflicts(session, ids) or [])
            except Exception as e:
                logger.debug(f"  Speaker conflicts not checked: {e}")
        return messages

    async def take_over_speakers(self, session: Session) -> None:
        """
        The channel takes its speakers of other kinds over from the channels
        playing them now (kind.take_over(session); the channel editor's Move).
        """
        for kind in self._speaker_kinds:
            take = getattr(kind, "take_over", None)
            if take is None:
                continue
            try:
                await take(session)
            except Exception as e:
                logger.warning(f"  '{session.name}': speakers not taken over: {e}")

    async def _stop_kinds(self, session: Session) -> None:
        """The channel stopped or paused: speakers of other kinds stop playing it."""
        for kind in self._speaker_kinds:
            try:
                await kind.stop(session)
            except Exception as e:
                logger.warning(f"  '{session.name}': speakers not stopped: {e}")

    def add_mix_hold(self, predicate) -> None:
        """predicate(session_id) -> True keeps the channel's mix running (like a browser listening)."""
        self._mix_holds.append(predicate)

    def _held_by_hooks(self, session_id: str) -> bool:
        for predicate in self._mix_holds:
            try:
                if predicate(session_id):
                    return True
            except Exception:
                pass
        return False

    def channel_of(self, session_id):
        """The Channel a channel's mix runs on now, or None (takes a channel id or its Session)."""
        session_id = getattr(session_id, "id", session_id)
        channel_id = self._session_channels.get(session_id)
        return self.channel_manager.get_channel(channel_id) if channel_id and self.channel_manager else None

    def claim_channel(self, session_id: str):
        """A Channel for the channel's mix (the one it has, else a free one; None when none is free)."""
        session = self._find(session_id)
        return self._assign_channel(session) if session is not None else None

    def run_mix(self, session_id: str):
        """
        Run the channel's theme mix without its speakers (like Listen here), for
        an output that isn't a speaker. Returns the Channel, None when none is
        free. ValueError: no theme, or it isn't found.
        """
        session = self.state.sessions.get(session_id)
        if session is None:
            return None
        channel, fresh_start = self._run_mix(session)
        if channel is not None and fresh_start:
            self.runner.after_play(session)
        return channel

    async def release_unused(self, session_id: str, why: str) -> None:
        """Nothing holds the channel's mix any more: fade out and free its Channel (if it isn't playing)."""
        await self._release_unused(session_id, why)

    def _channel_for(self, session: Session):
        channel_id = self._session_channels.get(session.id)
        if not channel_id or not self.channel_manager:
            return None
        return self.channel_manager.get_channel(channel_id)

    def apply_preset_to_theme(self, theme_id: str, preset_id: str) -> bool:
        """
        Apply a preset's track settings to a theme.

        This modifies the live theme instances so audio generation reflects
        the preset's track settings (enabled/disabled, volume, presence, etc.).

        Returns True if preset was applied successfully.
        """
        from sonorium.recording import PlaybackMode

        if not preset_id:
            return False

        theme = self.get_theme(theme_id)
        if not theme:
            logger.warning(f"  Cannot apply preset: theme '{theme_id}' not found")
            return False

        preset = self._theme_presets(theme_id).get(preset_id)
        preset_tracks = preset.get("tracks", {}) if preset else None

        if not preset_tracks:
            logger.warning(f"  Preset '{preset_id}' not found for theme '{theme_id}'")
            return False

        # Apply preset settings to live theme instances
        applied_count = 0
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
                from sonorium.core.theme_metadata import EBB_TRACK_SETTINGS
                for key in EBB_TRACK_SETTINGS:
                    if key in settings:
                        setattr(inst, key, settings[key])
                applied_count += 1

        logger.info(f"  Applied preset '{preset_id}' to theme '{theme.name}' ({applied_count} tracks)")
        return True

    def get_theme(self, theme_id: str) -> Optional[LoadedTheme]:
        """Get a theme by ID, handling both UUID-based and folder-based IDs."""
        if not self.themes:
            return None

        # First try direct lookup (folder-based ID)
        theme = self.themes.id.get(theme_id)
        if theme:
            return theme

        # Try UUID lookup via metadata.json files
        import json
        from pathlib import Path

        # Get audio path from device
        if hasattr(self, 'themes') and self.themes and len(self.themes) > 0:
            # Get path from first theme's sonorium device
            first_theme = self.themes[0]
            if hasattr(first_theme, 'library') and hasattr(first_theme.library, 'audio_dir'):
                audio_path = first_theme.library.audio_dir
            else:
                return None
        else:
            return None

        # Scan folders for UUID match
        if audio_path and audio_path.exists():
            for folder in audio_path.iterdir():
                if folder.is_dir():
                    metadata_path = folder / "metadata.json"
                    if metadata_path.exists():
                        try:
                            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
                            if metadata.get("id") == theme_id:
                                # Found the folder, now find the theme by folder name
                                from sonorium.utils import sanitize
                                folder_id = sanitize(folder.name)
                                return self.themes.id.get(folder_id)
                        except Exception:
                            pass

        return None
    
    def get_stream_url(self, session: Session) -> str:
        """
        Get the stream URL for a session.
        
        Uses channel-based URL if channel is assigned, otherwise falls back to theme URL.
        While Require login is on, it carries a stream link (auth/stream_links.py).
        """
        from sonorium.auth.stream_links import signed
        channel_id = self._session_channels.get(session.id)
        if channel_id:
            return signed(f"{self.stream_base_url}/stream/channel{channel_id}")
        # Fallback to theme-based URL (legacy)
        return signed(f"{self.stream_base_url}/stream/{session.theme_id}")
    
    def _assign_channel(self, session: Session) -> Optional[Channel]:
        """
        Assign an available channel to a session.
        
        Returns the assigned channel, or None if no channels available.
        """
        if not self.channel_manager:
            return None
        
        # Check if session already has a channel
        existing_id = self._session_channels.get(session.id)
        if existing_id:
            return self.channel_manager.get_channel(existing_id)
        
        # Get an available channel
        channel = self.channel_manager.get_available_channel()
        if channel:
            self._session_channels[session.id] = channel.id
            channel.speakers_muted = session.speakers_muted
            logger.debug(f"  Assigned channel {channel.id} to session {session.id}")
        
        return channel
    
    def channel_limit_refusal(self, session: Session) -> Optional[str]:
        """
        Why the session can't start now: every channel (Max channels) is in
        use by other channels. None when it has a channel or one is free.
        """
        if not self.channel_manager or session.id in self._session_channels:
            return None
        if self.channel_manager.get_available_channel() is not None:
            return None
        limit = getattr(self.channel_manager, "max_channels", None) or len(self._session_channels)
        if limit == 1:
            return "1 channel is already playing (the most at once is 1). Stop it first."
        return f"{limit} channels are already playing (the most at once is {limit}). Stop one first."

    def _release_channel(self, session_id: str):
        """Release a channel from a session."""
        channel_id = self._session_channels.pop(session_id, None)
        if channel_id and self.channel_manager:
            channel = self.channel_manager.get_channel(channel_id)
            if channel:
                channel.stop()
                logger.debug(f"  Released channel {channel_id} from session {session_id}")
    
    def get_session_channel(self, session_id: str) -> Optional[int]:
        """Get the channel ID assigned to a session."""
        return self._session_channels.get(session_id)
    
    # --- Auto-naming ---
    
    def generate_session_name(
        self,
        selection: SpeakerSelection = None,
        group: SpeakerGroup = None,
    ) -> tuple[str, NameSource]:
        """
        Generate a session name based on speaker selection.
        
        Priority:
        1. If using a saved group -> group name
        2. If single floor selected -> floor name
        3. If single area selected -> area name
        4. If multiple areas -> "Area1 & Area2" or "Area1 + N more"
        5. If single speaker -> speaker name
        6. Fallback -> "N Speakers"
        
        Returns:
            Tuple of (name, source)
        """
        # If using a saved group, use its name
        if group:
            return (group.name, NameSource.AUTO_GROUP)
        
        if not selection:
            return ("New Session", NameSource.CUSTOM)
        
        # Single floor selected (with possible exclusions)
        if (len(selection.include_floors) == 1 and 
            not selection.include_areas and 
            not selection.include_speakers):
            floor_name = self.registry.get_floor_name(selection.include_floors[0])
            return (floor_name, NameSource.AUTO_FLOOR)
        
        # Single area selected
        if (len(selection.include_areas) == 1 and 
            not selection.include_floors and 
            not selection.include_speakers):
            area_name = self.registry.get_area_name(selection.include_areas[0])
            return (area_name, NameSource.AUTO_AREA)
        
        # Multiple areas (no floors)
        if selection.include_areas and not selection.include_floors:
            area_names = [self.registry.get_area_name(a) for a in selection.include_areas]
            if len(area_names) == 2:
                return (f"{area_names[0]} & {area_names[1]}", NameSource.AUTO_AREA)
            elif len(area_names) > 2:
                return (f"{area_names[0]} + {len(area_names) - 1} more", NameSource.AUTO_AREA)
        
        # Single speaker selected
        if (len(selection.include_speakers) == 1 and 
            not selection.include_floors and 
            not selection.include_areas):
            speaker_name = self.registry.get_speaker_name(selection.include_speakers[0])
            return (speaker_name, NameSource.AUTO_AREA)
        
        # Fallback: count resolved speakers
        resolved = self.registry.resolve_selection(
            include_floors=selection.include_floors,
            include_areas=selection.include_areas,
            include_speakers=selection.include_speakers,
            exclude_areas=selection.exclude_areas,
            exclude_speakers=selection.exclude_speakers,
        )
        return (f"{len(resolved)} Speakers", NameSource.AUTO_AREA)
    
    # --- CRUD Operations ---

    def _get_next_channel_number(self) -> int:
        """
        Get the next available channel number.

        Finds the lowest unused channel number (1-based).
        """
        # Get all existing channel numbers from session names
        used_numbers = set()
        for session in self.state.sessions.values():
            if session.name.startswith("Channel "):
                try:
                    num = int(session.name[8:])  # Extract number after "Channel "
                    used_numbers.add(num)
                except ValueError:
                    pass

        # Find the lowest unused number
        channel_num = 1
        while channel_num in used_numbers:
            channel_num += 1

        return channel_num

    @logger.instrument("Creating new session...")
    def create(
        self,
        theme_id: str = None,
        preset_id: str = None,
        speaker_group_id: str = None,
        adhoc_selection: SpeakerSelection = None,
        custom_name: str = None,
        volume: int = None,
    ) -> Session:
        """
        Create a new session.

        Args:
            theme_id: Theme to play (optional, can set later)
            preset_id: Theme preset to apply (optional)
            speaker_group_id: Saved speaker group to use
            adhoc_selection: Ad-hoc speaker selection (if not using group)
            custom_name: Custom name (overrides auto-naming)
            volume: Initial volume (uses default if not specified)

        Returns:
            Created session

        Raises:
            ValueError: If max sessions exceeded, or the sequence chosen as
                the preset ("seq:<id>") has no presets
            LookupError: The sequence chosen as the preset is unknown
        """
        # Check limits (hardcoded max of 20 sessions)
        max_sessions = 20
        if len(self.state.sessions) >= max_sessions:
            raise ValueError(f"Maximum of {max_sessions} sessions allowed")

        # A sequence chosen as the preset: it starts on its first preset
        from sonorium.core.sequences import sequence_choice
        sequence_id = sequence_choice(preset_id)
        if sequence_id is not None:
            preset_id = self.runner.check_sequence(theme_id, sequence_id)["presets"][0]

        # Generate ID
        session_id = str(uuid.uuid4())[:8]

        # Determine name - default to "Channel N" unless custom name provided
        if custom_name:
            name = custom_name
            name_source = NameSource.CUSTOM
        else:
            # Use sequential channel naming by default
            channel_num = self._get_next_channel_number()
            name = f"Channel {channel_num}"
            name_source = NameSource.CUSTOM  # Treat as custom so it doesn't auto-update

        # Use default volume if not specified
        if volume is None:
            volume = self.state.settings.default_volume

        preset_id = self.preset_for_theme(theme_id, preset_id)

        # Create session
        session = Session(
            id=session_id,
            name=name,
            name_source=name_source,
            theme_id=theme_id,
            preset_id=preset_id,
            speaker_group_id=speaker_group_id,
            adhoc_selection=adhoc_selection,
            volume=volume,
            is_playing=False,
        )
        
        # Store and save
        self.state.sessions[session_id] = session
        if sequence_id is not None:
            self.runner.start_sequence(session, sequence_id, apply=False)
        self.state.save()
        
        logger.info(f"  Created session '{session.name}' ({session_id})")
        self._tell("created", session)
        self.publish("channel.created", session, theme_id=session.theme_id)
        return session
    
    def get(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        return self.state.sessions.get(session_id)
    
    def list(self) -> list[Session]:
        """List all sessions, sorted by creation time (never a personal channel)."""
        sessions = list(self.state.sessions.values())
        sessions.sort(key=lambda s: s.created_at)
        return sessions

    # --- Personal channels: one per account, hidden, local only ---
    #
    # A personal channel is a Session with `owner` set, kept in
    # state.personal_sessions: get() and list() never return it, so nothing
    # that lists or addresses channels (the API, Home Assistant and MQTT
    # entities, Status, zones and links) sees it. It never has speakers. Its
    # mix runs while at least one of its account's devices listens: each
    # device is counted once (listen_personal), and is let go when it asks
    # (unlisten_personal) or when its stream has been closed for its hold
    # (release_idle_sessions, as Listen here).

    def _find(self, session_id) -> Optional[Session]:
        """Any channel by id, a personal one included (what the API addresses is get())."""
        try:
            return self.state.sessions.get(session_id) or self.state.personal_sessions.get(session_id)
        except TypeError:
            return None

    def get_any(self, session_id) -> Optional[Session]:
        """Any channel by id, a personal one included (announcements reach a listened personal channel)."""
        return self._find(session_id)

    def _all_sessions(self) -> list[Session]:
        return list(self.state.sessions.values()) + list(self.state.personal_sessions.values())

    def is_personal(self, session) -> bool:
        """A personal channel (a Session or an id)."""
        if isinstance(session, str):
            return session in self.state.personal_sessions
        owner = getattr(session, "owner", None)
        return isinstance(owner, str) and bool(owner)

    def personal_channel(self, owner: str, create: bool = True) -> Optional[Session]:
        """The account's personal channel, made on first use (None if create is False and it has none)."""
        for session in self.state.personal_sessions.values():
            if session.owner == owner:
                return session
        if not create:
            return None
        session_id = str(uuid.uuid4())[:8]
        while session_id in self.state.sessions or session_id in self.state.personal_sessions:
            session_id = str(uuid.uuid4())[:8]
        session = Session(id=session_id, name=PERSONAL_NAME, name_source=NameSource.CUSTOM, owner=owner,
                          volume=self.state.settings.default_volume, is_playing=False)
        self.state.personal_sessions[session_id] = session
        self.state.save()
        logger.info(f"  Made a personal channel ({session_id})")
        return session

    def personal_listeners(self, session_id: str) -> dict[str, dict]:
        """The devices listening to a personal channel: {device key: {"tag", "since", "hold"}}."""
        return dict(self._personal_devices.get(session_id) or {})

    def listened_personal(self) -> list[Session]:
        """Personal channels some device listens to now."""
        return [s for sid, s in self.state.personal_sessions.items() if sid in self._listening]

    def listen_personal(self, session_id: str, device: str,
                        hold_seconds: Optional[float] = None) -> tuple[Optional[Channel], Optional[str]]:
        """
        A device of the account listens to its personal channel: the mix runs
        (started if it didn't) while at least one device listens. A device is
        counted once however often it asks. Returns (the channel, the device's
        stream tag), (None, None) when no channel is free. KeyError: no such
        personal channel; ValueError: no theme, it isn't found, or a bad hold.

        hold_seconds (0..LISTEN_HOLD_MAX_SECONDS, default LISTEN_RELEASE_SECONDS):
        how long the device counts after its stream closed.
        """
        if hold_seconds is not None:
            hold_seconds = float(hold_seconds)
            if not 0 <= hold_seconds <= LISTEN_HOLD_MAX_SECONDS:
                raise ValueError(f"hold_seconds must be between 0 and {LISTEN_HOLD_MAX_SECONDS}")
        session = self.state.personal_sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        channel, fresh_start = self._run_mix(session)
        if channel is None:
            return None, None
        expect = getattr(channel, "expect_local_listener", None)
        if callable(expect):
            expect()
        devices = self._personal_devices.setdefault(session_id, {})
        entry = devices.get(device)
        new = entry is None
        if new:
            entry = devices[device] = {"tag": secrets.token_hex(8), "hold": float(LISTEN_RELEASE_SECONDS)}
        entry["since"] = self._clock()
        if hold_seconds is not None:
            entry["hold"] = hold_seconds
        self._listening.add(session_id)
        self._listeners[session_id] = len(devices)
        self._listen_hold[session_id] = max(e["hold"] for e in devices.values())
        if fresh_start:
            self.runner.after_play(session)  # its timers run while a device listens
        if new:
            self._listening_changed(session_id, True)
            logger.info(f"  Personal channel {session_id}: {len(devices)} device(s) listen on channel {channel.id}")
        return channel, entry["tag"]

    def unlisten_personal(self, session_id: str, device: str) -> bool:
        """
        This device stopped listening (only this one). When it was the last,
        the mix fades out and its channel is freed (in the background).
        False: it wasn't listening.
        """
        if not self._drop_personal_device(session_id, device):
            return False
        if session_id not in self._listening:
            self._background(self._release_unused(session_id, "no device listens"))
        return True

    def _drop_personal_device(self, session_id: str, device: str) -> bool:
        devices = self._personal_devices.get(session_id)
        if not devices or devices.pop(device, None) is None:
            return False
        if devices:
            self._listeners[session_id] = len(devices)
            self._listen_hold[session_id] = max(e["hold"] for e in devices.values())
            self._listening_changed(session_id, True)  # one device fewer
        else:
            self._personal_devices.pop(session_id, None)
            self._listeners.pop(session_id, None)
            self._listening.discard(session_id)
            self._listening_changed(session_id, False)
        return True

    def _device_idle(self, channel, entry: dict, now: float) -> float:
        """Seconds a personal channel's device has been without its stream (0 while it plays)."""
        if channel is None:
            return float("inf")
        tag_idle = getattr(channel, "tag_idle_seconds", None)
        seen = tag_idle(entry["tag"]) if callable(tag_idle) else None
        if seen is not None:
            return seen
        # Its own stream never came (or it plays the stream without its tag):
        # counted from when it asked, kept while an untagged listener plays
        untagged = getattr(channel, "untagged_local_idle_seconds", None)
        local_idle = untagged() if callable(untagged) else channel.local_idle_seconds()
        return min(now - entry["since"], local_idle)

    def _release_personal_devices(self) -> None:
        """Let go of each device whose stream has been closed for its hold; the last one stops the mix."""
        now = self._clock()
        for session_id, devices in list(self._personal_devices.items()):
            channel = self.channel_of(session_id)
            for device, entry in list(devices.items()):
                if self._device_idle(channel, entry, now) < entry["hold"]:
                    continue
                logger.info(f"  Personal channel {session_id}: a device stopped listening")
                self._drop_personal_device(session_id, device)
            if session_id not in self._listening and session_id in self._session_channels:
                self.runner.on_stop(session_id)
                self._release_channel(session_id)
                logger.info(f"  Personal channel {session_id}: no device listens any more, channel released")
    
    @logger.instrument("Updating session {session_id}...")
    def update(
        self,
        session_id: str,
        theme_id: str = None,
        preset_id: str = None,
        speaker_group_id: str = None,
        adhoc_selection: SpeakerSelection = None,
        custom_name: str = None,
        volume: int = None,
    ) -> tuple[Optional[Session], set, set]:
        """
        Update an existing session (by hand: web UI, API, MQTT).

        Only provided fields are updated.
        If theme changes on a playing session, triggers seamless crossfade.
        Choosing a different theme stops the channel's playlist and sequence;
        choosing a different preset stops its sequence and glides to the preset.
        A preset choice "seq:<sequence_id>" starts that sequence of the
        (new) theme from its first preset, like start_sequence().

        Returns:
            Tuple of (session, added_speakers, removed_speakers)
            Returns (None, set(), set()) if session not found

        Raises:
            LookupError: the chosen sequence is unknown for the theme
            ValueError: the chosen sequence has no presets
            (both before anything changes)
        """
        session = self._find(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return None, set(), set()
        if self.is_personal(session) and (speaker_group_id or adhoc_selection is not None):
            raise PersonalSpeakersRefused(PERSONAL_NO_SPEAKERS)

        from sonorium.core.sequences import sequence_choice
        try:
            before = self._snapshot(session)
        except Exception:
            before = None
        sequence_id = sequence_choice(preset_id)
        if sequence_id is not None:
            target_theme = theme_id if theme_id is not None else session.theme_id
            preset_id = self.runner.check_sequence(target_theme, sequence_id)["presets"][0]

        # Track if theme or preset is changing
        theme_changed = theme_id is not None and theme_id != session.theme_id
        preset_changed = preset_id is not None and preset_id != session.preset_id

        # Get old speakers before update (for live speaker management)
        old_speakers = set(self.get_resolved_speakers(session)) if session.is_playing else set()
        speakers_changing = speaker_group_id is not None or adhoc_selection is not None

        if self.policy is not None:
            new_speakers = None
            if speakers_changing:
                group, selection = ((None, adhoc_selection) if adhoc_selection is not None
                                    else (speaker_group_id, None))
                chosen = self.resolve_choice(group, selection)
                if set(chosen) != set(self.selected_speakers(session)):
                    new_speakers = chosen
            self.guard("update", session,
                       theme=theme_id if theme_changed else None,
                       preset=preset_id if (preset_id is not None and (preset_id or None) != session.preset_id) else None,
                       name=custom_name if (custom_name is not None and custom_name != session.name) else None,
                       volume=volume if (volume is not None and volume != session.volume) else None,
                       speakers=new_speakers)

        # Update fields if provided. A new theme brings its own preset (the one
        # sent with the change, else its default); "" clears the preset.
        if theme_changed:
            self.runner.clear_playlist(session)
            self.runner.stop_sequence(session)
            session.theme_id = theme_id
            session.preset_id = self.preset_for_theme(theme_id, preset_id)
            preset_changed = False
            theme = self.get_theme(theme_id)
            self.note(session, f"Theme: {theme.name if theme else theme_id}")
        elif preset_id is not None:
            if (preset_id or None) != session.preset_id:
                self.runner.stop_sequence(session)
                if sequence_id is None:
                    self.note(session, f"Preset: {self.preset_name(session.theme_id, preset_id)}")
            session.preset_id = preset_id or None

        if speaker_group_id is not None:
            session.speaker_group_id = speaker_group_id
            session.adhoc_selection = None  # Clear ad-hoc if using group

        if adhoc_selection is not None:
            session.adhoc_selection = adhoc_selection
            session.speaker_group_id = None  # Clear group if using ad-hoc

        if custom_name is not None:
            session.name = custom_name
            session.name_source = NameSource.CUSTOM

        if volume is not None:
            session.volume = max(0, min(100, volume))

        # Re-generate auto-name if needed
        if custom_name is None and session.name_source != NameSource.CUSTOM:
            group = None
            if session.speaker_group_id:
                group = self.state.speaker_groups.get(session.speaker_group_id)
            session.name, session.name_source = self.generate_session_name(
                session.adhoc_selection, group
            )

        if sequence_id is not None:
            # The channel is on (or crossfading/gliding to) its first preset
            self.runner.start_sequence(session, sequence_id, apply=False)

        if speakers_changing:
            self._tell("changed", session)  # the policy's speakers in use follow

        self.state.save()

        # If session is playing and theme changed, trigger crossfade (with the
        # channel's preset for the new theme; the shared theme is never changed)
        if self._live(session) and theme_changed and session.theme_id:
            self._trigger_theme_crossfade(session)

        # If session is playing and preset changed (same theme), change only its
        # channel, gliding to the new values
        elif self._live(session) and preset_changed and session.theme_id:
            channel = self._channel_for(session)
            if channel:
                channel.set_track_overrides(self.session_overrides(session),
                                            glide_seconds=PRESET_GLIDE_SECONDS)
                logger.info(f"  Applied preset change for playing session")

        # Calculate speaker changes for live management
        added_speakers = set()
        removed_speakers = set()
        if session.is_playing and speakers_changing:
            new_speakers = set(self.get_resolved_speakers(session))
            added_speakers = new_speakers - old_speakers
            removed_speakers = old_speakers - new_speakers
            if added_speakers:
                logger.info(f"  Speakers added: {added_speakers}")
            if removed_speakers:
                logger.info(f"  Speakers removed: {removed_speakers}")

        logger.info(f"  Updated session '{session.name}'")
        if before is not None:
            self.publish_changes(session, before)
        return session, added_speakers, removed_speakers

    async def apply_speaker_changes(
        self,
        session: Session,
        added_speakers: set,
        removed_speakers: set,
    ) -> None:
        """
        Apply live speaker changes to a playing session.

        Stops playback on removed speakers and starts on added speakers.
        """
        for kind, ids in self._kind_speakers(removed_speakers):
            await kind.stop(session, ids)
        for kind, ids in self._kind_speakers(added_speakers):
            await kind.start(session, ids)
        removed_speakers = set(self.media_speakers(removed_speakers))
        added_speakers = set(self.media_speakers(added_speakers))
        if not self.media_controller:
            return

        stream_url = self.get_stream_url(session)
        volume_level = session.volume / 100.0

        # Stop removed speakers
        if removed_speakers:
            logger.info(f"  Stopping {len(removed_speakers)} removed speaker(s)")
            await self.media_controller.stop_multi(list(removed_speakers))

        # Start added speakers
        if added_speakers:
            logger.info(f"  Starting {len(added_speakers)} added speaker(s)")
            await self.media_controller.play_media_multi(list(added_speakers), stream_url)
            await self.media_controller.set_volume_multi(list(added_speakers), volume_level)
    
    # --- Changes made by sequences and playlists (not by hand) ---

    def _apply_preset(self, session: Session, preset_id: Optional[str], glide: float,
                      fade: Optional[float] = None) -> None:
        """
        Switch the channel to a preset of its theme, gliding over `glide` seconds
        if it plays. Tracks it turns on or off fade over `fade` seconds (a
        sequence step's ramp), else the usual theme.TRACK_TOGGLE_FADE_SECONDS.
        """
        session.preset_id = preset_id or None
        if self._live(session) and session.theme_id:
            channel = self._channel_for(session)
            if channel:
                channel.set_track_overrides(self.session_overrides(session),
                                            glide_seconds=glide, fade_seconds=fade)

    def transition_plans(self, session: Session, from_preset: Optional[str], to_preset: str,
                         step_in: Optional[dict], step_out: Optional[dict]) -> list:
        """
        The tracks with an advanced transition at a sequence step from one
        preset of the channel's theme to another (core/transitions.step_plans).
        Merry-go-round tracks play as their group's bed and are left out.
        """
        from sonorium.core.transitions import step_plans
        from sonorium.recording import GROUP_MODE_MERRY_GO_ROUND, TrackView, group_mode
        theme = self.get_theme(session.theme_id) if session.theme_id else None
        if theme is None:
            return []
        groups = getattr(theme, "groups", None) or {}

        def bed(instance):
            group = getattr(instance.meta, "group", None)
            return bool(group) and group_mode(groups.get(group)) == GROUP_MODE_MERRY_GO_ROUND

        return step_plans(list(theme.tracks),
                          self.session_overrides(session, from_preset) if from_preset else {},
                          self.session_overrides(session, to_preset),
                          step_in, step_out, to_preset, from_preset or "",
                          lambda instance, overrides: TrackView(instance, overrides), skip=bed)

    def schedule_transitions(self, session: Session, plans: list) -> None:
        """Hand a step's planned transitions to the channel's mix."""
        channel = self._channel_for(session)
        schedule = getattr(channel, "schedule_transitions", None)
        if schedule is not None:
            schedule(plans)

    def cancel_transitions(self, session: Session) -> None:
        """Drop the channel's planned transitions; running ones glide to what it plays now."""
        channel = self._channel_for(session)
        cancel = getattr(channel, "cancel_transitions", None)
        if cancel is not None:
            cancel()

    def _apply_theme(self, session: Session, theme_id: str, preset_id: Optional[str]) -> None:
        """
        Switch the channel to a theme with a preset (None: the theme's default),
        crossfading if it plays. On the same theme the preset glides in.
        """
        if theme_id == session.theme_id:
            self._apply_preset(session, self.preset_for_theme(theme_id, preset_id), PRESET_GLIDE_SECONDS)
            return
        session.theme_id = theme_id
        session.preset_id = self.preset_for_theme(theme_id, preset_id)
        if self._live(session):
            self._trigger_theme_crossfade(session)
        self.publish("channel.changed", session, changes={"theme_id": theme_id, "preset_id": session.preset_id})

    # --- Sequences and playlists (by hand) ---

    def start_sequence(self, session_id: str, sequence_id: str) -> Session:
        """
        Run a sequence of the channel's theme. Raises KeyError (no such
        channel), LookupError (no such sequence) or ValueError (it can't run).
        """
        session = self._find(session_id)
        if not session:
            raise KeyError(session_id)
        self.guard("playback", session)
        if not session.theme_id:
            raise ValueError("The channel has no theme")
        self.runner.start_sequence(session, sequence_id)
        return session

    def stop_sequence(self, session_id: str) -> Optional[Session]:
        """Stop the channel's sequence (it stays on its current preset)."""
        session = self._find(session_id)
        if not session:
            return None
        self.guard("playback", session)
        self.runner.stop_sequence(session)
        self.state.save()
        return session

    def set_playlist(self, session_id: str, playlist_id: str) -> Session:
        """Play a playlist on the channel. Raises KeyError, LookupError or ValueError."""
        session = self._find(session_id)
        if not session:
            raise KeyError(session_id)
        self.guard("playback", session)
        self.runner.set_playlist(session, playlist_id)
        return session

    def clear_playlist(self, session_id: str) -> Optional[Session]:
        """Stop the channel's playlist; it keeps playing its current theme."""
        session = self._find(session_id)
        if not session:
            return None
        self.guard("playback", session)
        self.runner.clear_playlist(session)
        self.state.save()
        return session

    async def skip(self, session_id: str) -> Optional[str]:
        """
        Next playlist entry (or the sequence's next preset). Returns what moved
        ('playlist', 'sequence'), 'end' if a non-repeating playlist ended (the
        channel stops), or None if nothing runs. KeyError if no such channel.
        """
        session = self._find(session_id)
        if not session:
            raise KeyError(session_id)
        self.guard("playback", session)
        result = self.runner.skip(session)
        if result == "end":
            await self.stop(session_id, reason="ended")
        return result

    def playback_status(self, session: Session) -> dict:
        """The channel's sequence and playlist (see PlaybackRunner.status)."""
        return self.runner.status(session)

    def forget_playlist(self, playlist_id: str) -> list[Session]:
        """A playlist was deleted: channels playing it stop playing it."""
        cleared = []
        for session in self._all_sessions():
            if session.playlist_id == playlist_id:
                self.runner.clear_playlist(session)
                cleared.append(session)
        if cleared:
            self.state.save()
        return cleared

    def forget_sequence(self, theme_id: str, sequence_id: str) -> list[Session]:
        """A sequence was deleted: channels running it stop it."""
        cleared = []
        for session in self._all_sessions():
            if session.theme_id == theme_id and session.sequence_id == sequence_id:
                self.runner.stop_sequence(session)
                cleared.append(session)
        if cleared:
            self.state.save()
        return cleared

    def _trigger_theme_crossfade(self, session: Session):
        """Trigger a theme crossfade on the session's channel."""
        channel_id = self._session_channels.get(session.id)
        if not channel_id or not self.channel_manager:
            return
        
        channel = self.channel_manager.get_channel(channel_id)
        if not channel:
            return
        
        theme = self.get_theme(session.theme_id)
        if not theme:
            return
        
        logger.info(f"  Triggering crossfade to '{theme.name}' on channel {channel_id}")
        channel.set_theme(theme, self.session_overrides(session),
                          crossfade=self.crossfade_seconds())
    
    @logger.instrument("Deleting session {session_id}...")
    def delete(self, session_id: str) -> bool:
        """
        Delete a session.
        
        Returns:
            True if deleted, False if not found
        """
        if session_id not in self.state.sessions:
            logger.warning(f"  Session {session_id} not found")
            return False
        self.guard("delete", self.state.sessions[session_id])

        # Release channel if assigned
        self.drop_outputs(session_id, "deleted")
        self._release_channel(session_id)
        self.runner.forget(session_id)
        self._fading.pop(session_id, None)
        self._listening.discard(session_id)
        self._listeners.pop(session_id, None)
        self._listen_hold.pop(session_id, None)

        session = self.state.sessions.pop(session_id)
        self._tell("deleted", session)
        self.state.save()
        
        logger.info(f"  Deleted session '{session.name}'")
        self.publish("channel.deleted", session)
        return True
    
    # --- Speaker Resolution ---
    
    def get_resolved_speakers(self, session: Session) -> list[str]:
        """
        Get the list of speaker entity_ids for a session.
        
        Resolves speaker group or ad-hoc selection to final list. Speakers
        disabled in Settings > Speakers are left out: disabled means invisible
        to the rest of the app, including channels saved before it was disabled.
        A personal channel never has speakers.
        """
        if self.is_personal(session):
            return []
        speakers = self.selected_speakers(session)
        hook = getattr(self.policy, "speakers", None)
        return list(hook(session, speakers)) if hook is not None else speakers

    def selected_speakers(self, session: Session) -> list[str]:
        """All the (switched on) speakers the channel has, whether or not another channel uses them now."""
        if self.is_personal(session):
            return []
        return self._only_enabled(self._resolve_selection(session))

    def resolve_choice(self, speaker_group_id: Optional[str], selection: Optional[SpeakerSelection]) -> list[str]:
        """The switched on speakers a group or a selection names (before a channel is given it)."""
        probe = Session(id="", name="", speaker_group_id=speaker_group_id, adhoc_selection=selection)
        return self._only_enabled(self._resolve_selection(probe))

    def _only_enabled(self, speaker_ids: list[str]) -> list[str]:
        return [speaker_id for speaker_id in speaker_ids if self.state.settings.speaker_enabled(speaker_id)]

    def _resolve_selection(self, session: Session) -> list[str]:
        if session.speaker_group_id:
            group = self.state.speaker_groups.get(session.speaker_group_id)
            if group:
                return self.registry.resolve_selection(
                    include_floors=group.include_floors,
                    include_areas=group.include_areas,
                    include_speakers=group.include_speakers,
                    exclude_areas=group.exclude_areas,
                    exclude_speakers=group.exclude_speakers,
                )
        
        if session.adhoc_selection:
            sel = session.adhoc_selection
            return self.registry.resolve_selection(
                include_floors=sel.include_floors,
                include_areas=sel.include_areas,
                include_speakers=sel.include_speakers,
                exclude_areas=sel.exclude_areas,
                exclude_speakers=sel.exclude_speakers,
            )
        
        return []
    
    def get_speaker_summary(self, session: Session) -> str:
        """
        Get human-readable speaker summary for a session.
        
        Examples:
        - "3 speakers"
        - "Bedroom Level (2 excluded)"
        - "Office Echo"
        """
        resolved = self.get_resolved_speakers(session)
        if not resolved:
            return "No speakers"
        # Speakers of other kinds (a plugin's audio output devices) by name, after the others
        speakers = self.media_speakers(resolved)
        devices = ", ".join(self.registry.get_speaker_name(s) for s in resolved if s not in speakers)
        if not speakers:
            return devices
        summary = self._media_summary(session, speakers)
        return f"{summary}, {devices}" if devices else summary

    def _media_summary(self, session: Session, speakers: list[str]) -> str:
        if len(speakers) == 1:
            return self.registry.get_speaker_name(speakers[0])
        
        # Check for exclusions
        excluded_count = 0
        if session.speaker_group_id:
            group = self.state.speaker_groups.get(session.speaker_group_id)
            if group:
                excluded_count = len(group.exclude_areas) + len(group.exclude_speakers)
        elif session.adhoc_selection:
            sel = session.adhoc_selection
            excluded_count = len(sel.exclude_areas) + len(sel.exclude_speakers)
        
        if excluded_count > 0:
            return f"{len(speakers)} speakers ({excluded_count} excluded)"
        
        return f"{len(speakers)} speakers"
    
    # --- Playback Control ---
    
    @logger.instrument("Playing session {session_id}...")
    async def play(self, session_id: str, take_over: Optional[bool] = None) -> bool:
        """
        Start playback for a session.
        
        Assigns a channel, sets the theme, and sends stream URL to speakers.
        The mix fades in over the Crossfade setting. A playlist carries on
        with its current entry; a sequence starts from its first preset.

        take_over: a local device while another channel plays a local
        device (one at a time) is taken from it when True; otherwise this
        channel starts without it.

        Returns:
            True if started successfully, False otherwise
        """
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return False

        logger.info(f"Playing session '{session.name}'...")
        if self.policy is not None:  # Zones: may refuse, and picks the speakers it may use
            from sonorium.core.channel_policy import ChannelRefused
            try:
                self.guard("play", session)
            except ChannelRefused as e:
                logger.warning(f"  '{session.name}' not started: {e}")
                self._start_failed(session, str(e))
                return False
        refusal = self.channel_limit_refusal(session)
        if refusal:  # Max channels: never "playing" without a channel of its own
            logger.warning(f"  '{session.name}' not started: {refusal}")
            self._start_failed(session, refusal)
            if session.is_playing:
                session.is_playing = False
                self.state.save()
            return False
        self._fading.pop(session_id, None)# cancels a stop or pause still fading out

        # Not playing yet as far as the runner is concerned, so this only sets
        # the theme and preset for the playlist entry, or the sequence's first
        # preset. (Play on a channel whose timers already run changes nothing.)
        fresh_start = (self.runner.running_sequence(session_id) is None
                       and self.runner.running_playlist(session_id) is None)
        if fresh_start:
            was_playing = session.is_playing
            session.is_playing = False
            self.runner.before_play(session)
            session.is_playing = was_playing

        if not session.theme_id:
            logger.warning(f"  Session has no theme selected")
            self._start_failed(session, "No theme selected")
            return False
        # An unknown theme would leave the channel silent and every speaker
        # would get an empty stream, so refuse it instead.
        if not self.get_theme(session.theme_id):
            logger.warning(f"  Theme {session.theme_id} not found")
            self._start_failed(session, f"Theme {session.theme_id} not found")
            session.is_playing = False
            return False
        
        speakers = self.get_resolved_speakers(session)
        if not speakers:
            logger.warning(f"  Session has no speakers")
            self._start_failed(session, "No speakers selected")
            return False
        
        if not self.media_controller and self.media_speakers(speakers):
            logger.warning(f"  No media controller available")
            self._start_failed(session, "No media controller available")
            return False
        
        # Channels saved before presets followed the theme may hold another theme's preset
        if session.preset_id and session.preset_id not in self._theme_presets(session.theme_id):
            session.preset_id = self.preset_for_theme(session.theme_id)
            logger.info(f"  Preset reset to the theme's default ({session.preset_id or 'none'})")

        # Assign channel and set theme, with the session's preset as the channel's
        # own layer (the shared theme, and other channels on it, are unaffected)
        channel = self._assign_channel(session)
        if channel:
            theme = self.get_theme(session.theme_id)
            if theme:
                fade = self.crossfade_seconds()
                channel.set_theme(theme, self.session_overrides(session),
                                  crossfade=fade, fade_in=fade)
                channel.fade_in(fade)  # also after a pause, or a stop still fading out
                logger.debug(f"  Channel {channel.id}: theme '{theme.name}'")
        
        # Build stream URL (channel-based if available)
        stream_url = self.get_stream_url(session)
        logger.debug(f"  Stream URL: {stream_url}")
        
        # Mark as playing immediately (optimistic update)
        session.is_playing = True
        session.mark_played()
        self.note(session, "Started")
        self.state.save()
        self.publish("channel.started", session, theme_id=session.theme_id, preset_id=session.preset_id,
                     volume=session.volume, speakers=list(speakers))

        # Sequence and playlist timers start now
        if fresh_start:
            self.runner.after_play(session)

        # Play on all speakers (fire-and-forget)
        import asyncio
        asyncio.create_task(self._play_on_speakers(session, speakers, stream_url, take_over))
        
        return True
    
    def _start_failed(self, session: Session, message: str) -> None:
        try:
            from sonorium.plugins import events
            events.publish("error", {"where": "channel.start", "message": message, "channel_id": session.id,
                                     "name": session.name})
        except Exception:
            pass

    async def _play_on_speakers(self, session: Session, speakers: list[str], stream_url: str,
                                take_over: Optional[bool] = None):
        """Background task to play media on speakers."""
        for kind, ids in self._kind_speakers(speakers):
            try:
                await kind.start(session, ids, take_over=take_over)
            except Exception as e:
                logger.error(f"  Error starting playback: {e}")
        speakers = self.media_speakers(speakers)
        if not speakers or not self.media_controller:
            return
        try:
            results = await self.media_controller.play_media_multi(speakers, stream_url)
            
            # Set volume on all speakers
            volume_level = session.volume / 100.0
            await self.media_controller.set_volume_multi(speakers, volume_level)
            
            success_count = sum(1 for v in results.values() if v)
            logger.info(f"  '{session.name}': {success_count}/{len(speakers)} speakers started")
            
        except Exception as e:
            logger.error(f"  Error starting playback: {e}")
    
    async def _fade_out(self, session: Session) -> bool:
        """
        Fade the session's channel out over the Crossfade setting and wait for
        it. False if the session was played again meanwhile (then nothing
        more should be stopped).
        """
        token = object()
        self._fading[session.id] = token
        channel = self._channel_for(session)
        seconds = self.crossfade_seconds()
        if channel is not None and seconds > 0 and not channel.is_faded_out:
            channel.fade_out(seconds)
            loop = asyncio.get_running_loop()
            give_up = loop.time() + seconds + 1.0
            while not channel.is_faded_out and loop.time() < give_up:
                await asyncio.sleep(0.05)
                if self._fading.get(session.id) is not token:
                    break
        if self._fading.get(session.id) is not token or session.is_playing:
            logger.info(f"  '{session.name}' was played again while fading out")
            return False
        self._fading.pop(session.id, None)
        return True

    @logger.instrument("Pausing session {session_id}...")
    async def pause(self, session_id: str) -> bool:
        """
        Pause playback for a session: the mix fades out, then the speakers pause.

        Returns:
            True if paused, False if session not found
        """
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return False
        self.guard("stop", session)

        was_running = session.is_playing or session_id in self._session_channels
        session.is_playing = False
        self.drop_outputs(session_id, "paused")# a reported Cast device pauses like the speakers
        self.note(session, "Stopped")
        self.state.save()
        if was_running:
            self.publish("channel.stopped", session, reason="paused")
        if session_id not in self._listening:
            self.runner.on_stop(session_id)

        # A browser listening keeps the mix: only the speakers pause
        if session_id not in self._listening and not await self._fade_out(session) and session.is_playing:
            return True

        speakers = self.get_resolved_speakers(session)
        self._tell("released", session)

        await self._stop_kinds(session)
        speakers = self.media_speakers(speakers)
        if self.media_controller and speakers:
            await self.media_controller.pause_multi(speakers)

        logger.info(f"  Paused session '{session.name}'")
        return True
    
    @logger.instrument("Stopping session {session_id}...")
    async def stop(self, session_id: str, reason: str = "stopped") -> bool:
        """
        Stop playback for a session.

        The mix fades out over the Crossfade setting; then the speakers are
        told to stop and the channel is released. Marked stopped at once.

        Returns:
            True if stopped, False if session not found
        """
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return False
        self.guard("stop", session)

        was_running = session.is_playing or session_id in self._session_channels
        session.is_playing = False
        self.drop_outputs(session_id, reason)# a reported Cast device stops like the speakers
        self.note(session, "Stopped")
        self.state.save()
        if was_running:
            self.publish("channel.stopped", session, reason=reason)
        if session_id not in self._listening:
            self.runner.on_stop(session_id)

        # A browser listening keeps the mix (and the channel): only the speakers stop
        if session_id not in self._listening and not await self._fade_out(session) and session.is_playing:
            return True

        speakers = self.get_resolved_speakers(session)
        self._tell("released", session)

        await self._stop_kinds(session)
        speakers = self.media_speakers(speakers)
        if self.media_controller and speakers:
            await self.media_controller.stop_multi(speakers)

        # Release channel (a browser may have started listening during the fade)
        if session_id not in self._listening:
            self._release_channel(session_id)

        logger.info(f"  Stopped session '{session.name}'")
        return True
    
    @logger.instrument("Setting volume for session {session_id} to {volume}...")
    async def set_volume(self, session_id: str, volume: int) -> bool:
        """
        Set volume for a session.
        
        Args:
            session_id: Session to update
            volume: Volume level 0-100
        
        Returns:
            True if set, False if session not found
        """
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return False
        self.guard("volume", session)

        old_volume = session.volume
        session.volume = max(0, min(100, volume))
        self.state.save()
        if session.volume != old_volume:
            self.publish("channel.changed", session, changes={"volume": session.volume})
        
        # If playing, update volume on speakers
        if session.is_playing and self.media_controller:
            speakers = self.media_speakers(self.get_resolved_speakers(session))  # devices follow by themselves
            if speakers:
                volume_level = session.volume / 100.0
                await self.media_controller.set_volume_multi(speakers, volume_level)
        
        logger.info(f"  Set volume to {session.volume}%")
        return True
    
    # --- Listen here (a browser plays the channel's stream) ---

    def _run_mix(self, session: Session) -> tuple[Optional[Channel], bool]:
        """
        For a listener or a reported output: the channel's mix runs, started
        without the speakers if they don't play. Returns (the channel or None
        when none is free, whether its sequence or playlist starts fresh: then
        call runner.after_play once the listener is counted). ValueError: no
        theme, or it isn't found.
        """
        theme = self.get_theme(session.theme_id) if session.theme_id else None
        if theme is None:
            raise ValueError("No theme selected" if not session.theme_id else "Theme not found")
        channel = self._assign_channel(session)
        if channel is None:
            return None, False
        fresh_start = not self._live(session) and (self.runner.running_sequence(session.id) is None
                                                   and self.runner.running_playlist(session.id) is None)
        if fresh_start:
            self.runner.before_play(session)  # the playlist entry, or the sequence's first preset
            theme = self.get_theme(session.theme_id) or theme
        if not session.is_playing:
            # Speakers off: (re)start the mix, or cancel its fade-out after a stop or pause
            self._fading.pop(session.id, None)
            fade = self.crossfade_seconds()
            channel.set_theme(theme, self.session_overrides(session), crossfade=fade, fade_in=fade)
            channel.fade_in(fade)
        return channel, fresh_start

    def listen_hold(self, session_id: str) -> float:
        """Seconds the channel's mix waits for its browsers to come back after the last one left."""
        return self._listen_hold.get(session_id, LISTEN_RELEASE_SECONDS)

    def _listening_changed(self, session_id: str, listening: bool) -> None:
        if not listening:
            self._listen_hold.pop(session_id, None)
        session = self._find(session_id)
        if session is not None:
            self.publish("channel.listening", session, listening=listening,
                         devices=self.listener_count(session_id), playing=self._live(session))

    def listener_count(self, session_id: str) -> int:
        """How many listeners count for the channel: devices on a personal channel, else listen calls."""
        if session_id in self._personal_devices:
            return len(self._personal_devices[session_id])
        return self._listeners.get(session_id, 0) if session_id in self._listening else 0

    def listen(self, session_id: str, hold_seconds: Optional[float] = None) -> Optional[Channel]:
        """
        A browser listens to the channel: its mix runs (started without the
        speakers if they don't play) until no browser listens. Returns the
        channel, None if none is free. KeyError: no such session;
        ValueError: no theme, or it isn't found.

        hold_seconds (0..LISTEN_HOLD_MAX_SECONDS, default LISTEN_RELEASE_SECONDS):
        how long the mix waits for a browser whose stream dropped to come
        back. With several browsers the longest asked for counts.
        """
        if hold_seconds is not None:
            hold_seconds = float(hold_seconds)
            if not 0 <= hold_seconds <= LISTEN_HOLD_MAX_SECONDS:
                raise ValueError(f"hold_seconds must be between 0 and {LISTEN_HOLD_MAX_SECONDS}")
        session = self.state.sessions.get(session_id)
        if not session:
            raise KeyError(session_id)
        channel, fresh_start = self._run_mix(session)
        if channel is None:
            return None
        channel.expect_local_listener()
        was_listening = session_id in self._listening
        if hold_seconds is not None:
            held = self._listen_hold.get(session_id, 0.0) if was_listening else 0.0
            self._listen_hold[session_id] = max(held, hold_seconds)
        self._listening.add(session_id)
        self._listeners[session_id] = self._listeners.get(session_id, 0) + 1
        if fresh_start:
            self.runner.after_play(session)  # its timers run while the browser listens
        if not was_listening:
            self._listening_changed(session_id, True)
        logger.info(f"  '{session.name}': a browser listens on channel {channel.id}")
        return channel

    async def unlisten(self, session_id: str) -> None:
        """The browser stopped listening: the mix stops too unless the speakers play or another browser listens."""
        left = self._listeners.get(session_id, 0) - 1
        if left > 0:
            self._listeners[session_id] = left
            return
        self._listeners.pop(session_id, None)
        if session_id in self._listening:
            self._listening.discard(session_id)
            self._listening_changed(session_id, False)
        await self._release_unused(session_id, "no browser listens")

    async def _release_unused(self, session_id: str, why: str) -> None:
        """Nothing keeps the channel's mix any more (no speakers, listener or output): fade out, release."""
        session = self._find(session_id)
        if session and not session.is_playing and not self._kept(session_id):
            self.runner.on_stop(session_id)  # nothing plays it now: its sequence or playlist pauses
        if session and not session.is_playing and not self._kept(session_id) and session_id in self._session_channels:
            if await self._fade_out(session) and not self._kept(session_id):
                self._release_channel(session_id)
                logger.info(f"  '{session.name}': {why}, channel released")

    # --- Outputs the app reports (a Cast device playing a channel; core/outputs.py) ---

    def _publish_output(self, event_type: str, output, **extra) -> None:
        session = self._find(output.session_id)
        if session is not None:
            self.publish(event_type, session, output_id=output.output_id, kind=output.kind,
                         output_name=output.name, address=output.address, **extra)

    def add_output(self, session_id: str, report: dict, device_id: Optional[str] = None):
        """
        The app reports a device playing the channel's stream: the mix runs
        while it is reported. Returns (output, channel); channel None when no
        channel is free (nothing recorded). KeyError: no such session;
        ValueError (OutputError): no theme, or a bad report.
        """
        from sonorium.core.outputs import MAX_PER_CHANNEL, OutputError, clean_report
        report = clean_report(report)
        session = self.state.sessions.get(session_id)
        if not session:
            raise KeyError(session_id)
        if len(self.outputs.of(session_id)) >= MAX_PER_CHANNEL:
            raise OutputError(f"A channel can have at most {MAX_PER_CHANNEL} outputs")
        channel, fresh_start = self._run_mix(session)
        if channel is None:
            return None, None
        output, replaced = self.outputs.add(session_id, report, device_id)
        for old in replaced:
            self._publish_output("channel.output_removed", old, reason="replaced")
        if fresh_start:
            self.runner.after_play(session)  # its timers run while the device plays it
        self._publish_output("channel.output_added", output)
        for old in replaced:
            if old.session_id != session_id:
                self._background(self._release_unused(old.session_id, f"{old.name} moved to another channel"))
        logger.info(f"  '{session.name}': {output.kind} output '{output.name}' plays channel {channel.id}")
        return output, channel

    def touch_output(self, session_id: str, output_id: str):
        """The app's heartbeat for an output. None: no such output (expired or dropped: report it again)."""
        return self.outputs.touch(session_id, output_id)

    def remove_output(self, session_id: str, output_id: str, reason: str = "removed"):
        """
        The device stopped playing the channel. Returns the output, None if
        unknown. The mix fades out and is released in the background when
        nothing else keeps it.
        """
        output = self.outputs.remove(session_id, output_id)
        if output is None:
            return None
        self._publish_output("channel.output_removed", output, reason=reason)
        self._background(self._release_unused(session_id, f"{output.name} stopped"))
        return output

    def drop_outputs(self, session_id: str, reason: str) -> list:
        """Forget every output of a channel (stopped, paused, deleted, idle), each announced."""
        dropped = self.outputs.drop(session_id)
        for output in dropped:
            self._publish_output("channel.output_removed", output, reason=reason)
        return dropped

    _tasks: set = set()  # background fades still running (a reference keeps them alive)

    @classmethod
    def _background(cls, coro) -> None:
        try:
            task = asyncio.get_running_loop().create_task(coro)
        except RuntimeError:  # no event loop running (tests): now
            asyncio.run(coro)
            return
        cls._tasks.add(task)
        task.add_done_callback(cls._tasks.discard)

    async def stop_all(self) -> int:
        """
        Stop all playing sessions.
        
        Returns:
            Number of sessions stopped
        """
        # A paused session still holds its channel (#29). They fade out together.
        stopping = [session.id for session in list(self.state.sessions.values())
                    if (session.is_playing or session.id in self._session_channels) and self.allowed("stop", session)]
        await asyncio.gather(*(self.stop(session_id) for session_id in stopping))
        return len(stopping)

    def release_idle_sessions(self, idle_after: float = IDLE_RELEASE_SECONDS) -> list[Session]:
        """
        Stop sessions whose channel has had no listeners for `idle_after` seconds,
        e.g. when the speaker was stopped or switched to something else outside
        Sonorium (#29). Speakers aren't told to stop: they may be playing
        something else by now.

        Returns:
            Sessions that were stopped
        """
        if not self.channel_manager:
            return []

        # Personal channels: each device that stopped listening a while ago
        self._release_personal_devices()

        # Listen here: no browser for a while (closed without unlistening)
        for session_id in list(self._listening):
            if session_id in self.state.personal_sessions:
                continue
            channel = self.channel_manager.get_channel(self._session_channels.get(session_id) or 0)
            if channel and channel.local_idle_seconds() < self.listen_hold(session_id):
                continue
            self._listening.discard(session_id)
            self._listeners.pop(session_id, None)
            self._listening_changed(session_id, False)
            session = self.state.sessions.get(session_id)
            if session and not session.is_playing and not self.outputs.held(session_id):
                logger.info(f"  '{session.name}': no browser listens any more, channel released")
                self._release_channel(session_id)

        # Outputs the app reported and stopped sending heartbeats for
        for output in self.outputs.expired():
            self._publish_output("channel.output_removed", output, reason="expired")
            session = self.state.sessions.get(output.session_id)
            if session and not session.is_playing and not self._kept(session.id):
                logger.info(f"  '{session.name}': {output.name} was not heard from, channel released")
                self.runner.on_stop(session.id)
                self._release_channel(session.id)

        stopped = []
        for session_id, channel_id in list(self._session_channels.items()):
            channel = self.channel_manager.get_channel(channel_id)
            if not channel or channel.idle_seconds() < idle_after or session_id in self._listening:
                continue
            if session_id in self.state.personal_sessions:  # nobody listens to it any more
                self._release_channel(session_id)
                self.runner.on_stop(session_id)
                continue
            logger.info(f"  Channel {channel_id}: no listener has pulled audio for {int(channel.idle_seconds())}s, stopping its session")
            self.drop_outputs(session_id, "idle")  # a reported device that pulls nothing isn't playing
            self._release_channel(session_id)
            self.runner.on_stop(session_id)
            session = self.state.sessions.get(session_id)
            if session:
                session.is_playing = False
                self._tell("released", session)
                self.note(session, "Stopped")
                stopped.append(session)
                self.publish("channel.stopped", session, reason="idle")
        if stopped:
            self.state.save()
        return stopped
