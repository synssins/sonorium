"""
Session Manager

Handles CRUD operations for playback sessions, including:
- Creating sessions with auto-naming
- Updating session configuration
- Play/pause/stop control with channel-based streaming
- Volume management
- Seamless theme transitions via channel crossfading
- Start/stop fades, preset glides, sequences and playlists (core/playback_runner.py)
"""

from __future__ import annotations

import asyncio
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

# Stop a session once its channel has had no listeners this long. Long enough
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
# once no browser has listened for this long
LISTEN_RELEASE_SECONDS = 15

if TYPE_CHECKING:
    from sonorium.ha.registry import HARegistry
    from sonorium.ha.media_controller import HAMediaController
    from sonorium.core.channel import ChannelManager, Channel
    from sonorium.core.theme_metadata import ThemeMetadataManager
    from sonorium.theme import ThemeDefinition
    from fmtr.tools.iterator_tools import IndexList


class SessionManager:
    """
    Manages playback sessions.
    
    Each session represents one theme playing to one set of speakers.
    Multiple sessions can run simultaneously on different channels.
    """
    
    def __init__(
        self,
        state_store: StateStore,
        ha_registry: HARegistry,
        media_controller: HAMediaController = None,
        stream_base_url: str = None,
        channel_manager: ChannelManager = None,
        themes: IndexList[ThemeDefinition] = None,
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

        # Sequence and playlist timers
        from sonorium.core.playback_runner import PlaybackRunner
        self.runner = PlaybackRunner(self)
    
    def set_media_controller(self, controller: HAMediaController):
        """Set the media controller (for deferred initialization)."""
        self.media_controller = controller
    
    def set_stream_base_url(self, url: str):
        """Set the stream base URL."""
        self.stream_base_url = url.rstrip("/")
    
    def set_themes(self, themes: IndexList[ThemeDefinition]):
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
            if hasattr(first_theme, 'sonorium') and hasattr(first_theme.sonorium, 'path_audio'):
                for folder in first_theme.sonorium.path_audio.iterdir():
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
            if hasattr(first_theme, 'sonorium') and hasattr(first_theme.sonorium, 'path_audio'):
                for folder in first_theme.sonorium.path_audio.iterdir():
                    metadata_path = folder / "metadata.json"
                    if folder.is_dir() and metadata_path.exists():
                        try:
                            metadata = json.loads(metadata_path.read_text())
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
        for session in list(self.state.sessions.values()):
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
        for session in list(self.state.sessions.values()):
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

    def is_listened(self, session: Session) -> bool:
        """A browser listens to the channel."""
        return session.id in self._listening

    def _live(self, session: Session) -> bool:
        """The channel's mix runs: its speakers play, or a browser listens."""
        return session.is_playing or session.id in self._listening

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
        for inst in theme.instances:
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

    def get_theme(self, theme_id: str) -> Optional[ThemeDefinition]:
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
            if hasattr(first_theme, 'sonorium') and hasattr(first_theme.sonorium, 'path_audio'):
                audio_path = first_theme.sonorium.path_audio
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
                            metadata = json.loads(metadata_path.read_text())
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
        """
        channel_id = self._session_channels.get(session.id)
        if channel_id:
            return f"{self.stream_base_url}/stream/channel{channel_id}"
        # Fallback to theme-based URL (legacy)
        return f"{self.stream_base_url}/stream/{session.theme_id}"
    
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
            logger.debug(f"  Assigned channel {channel.id} to session {session.id}")
        
        return channel
    
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
        return session
    
    def get(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        return self.state.sessions.get(session_id)
    
    def list(self) -> list[Session]:
        """List all sessions, sorted by creation time."""
        sessions = list(self.state.sessions.values())
        sessions.sort(key=lambda s: s.created_at)
        return sessions
    
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
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return None, set(), set()

        from sonorium.core.sequences import sequence_choice
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

        return step_plans(list(theme.instances),
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

    # --- Sequences and playlists (by hand) ---

    def start_sequence(self, session_id: str, sequence_id: str) -> Session:
        """
        Run a sequence of the channel's theme. Raises KeyError (no such
        channel), LookupError (no such sequence) or ValueError (it can't run).
        """
        session = self.state.sessions.get(session_id)
        if not session:
            raise KeyError(session_id)
        if not session.theme_id:
            raise ValueError("The channel has no theme")
        self.runner.start_sequence(session, sequence_id)
        return session

    def stop_sequence(self, session_id: str) -> Optional[Session]:
        """Stop the channel's sequence (it stays on its current preset)."""
        session = self.state.sessions.get(session_id)
        if not session:
            return None
        self.runner.stop_sequence(session)
        self.state.save()
        return session

    def set_playlist(self, session_id: str, playlist_id: str) -> Session:
        """Play a playlist on the channel. Raises KeyError, LookupError or ValueError."""
        session = self.state.sessions.get(session_id)
        if not session:
            raise KeyError(session_id)
        self.runner.set_playlist(session, playlist_id)
        return session

    def clear_playlist(self, session_id: str) -> Optional[Session]:
        """Stop the channel's playlist; it keeps playing its current theme."""
        session = self.state.sessions.get(session_id)
        if not session:
            return None
        self.runner.clear_playlist(session)
        self.state.save()
        return session

    async def skip(self, session_id: str) -> Optional[str]:
        """
        Next playlist entry (or the sequence's next preset). Returns what moved
        ('playlist', 'sequence'), 'end' if a non-repeating playlist ended (the
        channel stops), or None if nothing runs. KeyError if no such channel.
        """
        session = self.state.sessions.get(session_id)
        if not session:
            raise KeyError(session_id)
        result = self.runner.skip(session)
        if result == "end":
            await self.stop(session_id)
        return result

    def playback_status(self, session: Session) -> dict:
        """The channel's sequence and playlist (see PlaybackRunner.status)."""
        return self.runner.status(session)

    def forget_playlist(self, playlist_id: str) -> list[Session]:
        """A playlist was deleted: channels playing it stop playing it."""
        cleared = []
        for session in self.state.sessions.values():
            if session.playlist_id == playlist_id:
                self.runner.clear_playlist(session)
                cleared.append(session)
        if cleared:
            self.state.save()
        return cleared

    def forget_sequence(self, theme_id: str, sequence_id: str) -> list[Session]:
        """A sequence was deleted: channels running it stop it."""
        cleared = []
        for session in self.state.sessions.values():
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
        
        # Release channel if assigned
        self._release_channel(session_id)
        self.runner.forget(session_id)
        self._fading.pop(session_id, None)
        self._listening.discard(session_id)

        session = self.state.sessions.pop(session_id)
        self.state.save()
        
        logger.info(f"  Deleted session '{session.name}'")
        return True
    
    # --- Speaker Resolution ---
    
    def get_resolved_speakers(self, session: Session) -> list[str]:
        """
        Get the list of speaker entity_ids for a session.
        
        Resolves speaker group or ad-hoc selection to final list. Speakers
        disabled in Settings > Speakers are left out: disabled means invisible
        to the rest of the app, including channels saved before it was disabled.
        """
        return self._only_enabled(self._resolve_selection(session))

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
        speakers = self.get_resolved_speakers(session)
        
        if not speakers:
            return "No speakers"
        
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
    async def play(self, session_id: str) -> bool:
        """
        Start playback for a session.
        
        Assigns a channel, sets the theme, and sends stream URL to speakers.
        The mix fades in over the Crossfade setting. A playlist carries on
        with its current entry; a sequence starts from its first preset.

        Returns:
            True if started successfully, False otherwise
        """
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return False

        logger.info(f"Playing session '{session.name}'...")
        self._fading.pop(session_id, None)  # cancels a stop or pause still fading out

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
            return False
        # An unknown theme would leave the channel silent and every speaker
        # would get an empty stream, so refuse it instead.
        if not self.get_theme(session.theme_id):
            logger.warning(f"  Theme {session.theme_id} not found")
            session.is_playing = False
            return False
        
        speakers = self.get_resolved_speakers(session)
        if not speakers:
            logger.warning(f"  Session has no speakers")
            return False
        
        if not self.media_controller:
            logger.warning(f"  No media controller available")
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

        # Sequence and playlist timers start now
        if fresh_start:
            self.runner.after_play(session)

        # Play on all speakers (fire-and-forget)
        import asyncio
        asyncio.create_task(self._play_on_speakers(session, speakers, stream_url))
        
        return True
    
    async def _play_on_speakers(self, session: Session, speakers: list[str], stream_url: str):
        """Background task to play media on speakers."""
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

        session.is_playing = False
        self.note(session, "Stopped")
        self.state.save()
        if session_id not in self._listening:
            self.runner.on_stop(session_id)

        # A browser listening keeps the mix: only the speakers pause
        if session_id not in self._listening and not await self._fade_out(session) and session.is_playing:
            return True

        speakers = self.get_resolved_speakers(session)

        if self.media_controller and speakers:
            await self.media_controller.pause_multi(speakers)

        logger.info(f"  Paused session '{session.name}'")
        return True
    
    @logger.instrument("Stopping session {session_id}...")
    async def stop(self, session_id: str) -> bool:
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

        session.is_playing = False
        self.note(session, "Stopped")
        self.state.save()
        if session_id not in self._listening:
            self.runner.on_stop(session_id)

        # A browser listening keeps the mix (and the channel): only the speakers stop
        if session_id not in self._listening and not await self._fade_out(session) and session.is_playing:
            return True

        speakers = self.get_resolved_speakers(session)

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
        
        session.volume = max(0, min(100, volume))
        self.state.save()
        
        # If playing, update volume on speakers
        if session.is_playing and self.media_controller:
            speakers = self.get_resolved_speakers(session)
            if speakers:
                volume_level = session.volume / 100.0
                await self.media_controller.set_volume_multi(speakers, volume_level)
        
        logger.info(f"  Set volume to {session.volume}%")
        return True
    
    # --- Listen here (a browser plays the channel's stream) ---

    def listen(self, session_id: str) -> Optional[Channel]:
        """
        A browser listens to the channel: its mix runs (started without the
        speakers if they don't play) until no browser listens. Returns the
        channel, None if none is free. KeyError: no such session;
        ValueError: no theme, or it isn't found.
        """
        session = self.state.sessions.get(session_id)
        if not session:
            raise KeyError(session_id)
        theme = self.get_theme(session.theme_id) if session.theme_id else None
        if theme is None:
            raise ValueError("No theme selected" if not session.theme_id else "Theme not found")
        channel = self._assign_channel(session)
        if channel is None:
            return None
        fresh_start = not self._live(session) and (self.runner.running_sequence(session_id) is None
                                                   and self.runner.running_playlist(session_id) is None)
        if fresh_start:
            self.runner.before_play(session)  # the playlist entry, or the sequence's first preset
            theme = self.get_theme(session.theme_id) or theme
        if not session.is_playing:
            # Speakers off: (re)start the mix, or cancel its fade-out after a stop or pause
            self._fading.pop(session_id, None)
            fade = self.crossfade_seconds()
            channel.set_theme(theme, self.session_overrides(session), crossfade=fade, fade_in=fade)
            channel.fade_in(fade)
        channel.expect_local_listener()
        self._listening.add(session_id)
        if fresh_start:
            self.runner.after_play(session)  # its timers run while the browser listens
        logger.info(f"  '{session.name}': a browser listens on channel {channel.id}")
        return channel

    async def unlisten(self, session_id: str) -> None:
        """The browser stopped listening: the mix stops too unless the speakers play."""
        self._listening.discard(session_id)
        session = self.state.sessions.get(session_id)
        if session and not session.is_playing and session_id not in self._listening:
            self.runner.on_stop(session_id)  # nothing plays it now: its sequence or playlist pauses
        if session and not session.is_playing and session_id in self._session_channels:
            if await self._fade_out(session) and session_id not in self._listening:
                self._release_channel(session_id)
                logger.info(f"  '{session.name}': no browser listens, channel released")

    async def stop_all(self) -> int:
        """
        Stop all playing sessions.
        
        Returns:
            Number of sessions stopped
        """
        # A paused session still holds its channel (#29). They fade out together.
        stopping = [session.id for session in list(self.state.sessions.values())
                    if session.is_playing or session.id in self._session_channels]
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

        # Listen here: no browser for a while (closed without unlistening)
        for session_id in list(self._listening):
            channel = self.channel_manager.get_channel(self._session_channels.get(session_id) or 0)
            if channel and channel.local_idle_seconds() < LISTEN_RELEASE_SECONDS:
                continue
            self._listening.discard(session_id)
            session = self.state.sessions.get(session_id)
            if session and not session.is_playing:
                logger.info(f"  '{session.name}': no browser listens any more, channel released")
                self._release_channel(session_id)

        stopped = []
        for session_id, channel_id in list(self._session_channels.items()):
            channel = self.channel_manager.get_channel(channel_id)
            if not channel or channel.idle_seconds() < idle_after or session_id in self._listening:
                continue
            logger.info(f"  Channel {channel_id}: no listener has pulled audio for {int(channel.idle_seconds())}s, stopping its session")
            self._release_channel(session_id)
            self.runner.on_stop(session_id)
            session = self.state.sessions.get(session_id)
            if session:
                session.is_playing = False
                self.note(session, "Stopped")
                stopped.append(session)
        if stopped:
            self.state.save()
        return stopped
