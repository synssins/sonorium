"""
Session Manager

Handles CRUD operations for playback sessions, including:
- Creating sessions with auto-naming
- Updating session configuration
- Play/pause/stop control with channel-based streaming
- Volume management
- Seamless theme transitions via channel crossfading
- Theme cycling integration
"""

from __future__ import annotations

import uuid
from typing import Optional, TYPE_CHECKING

from sonorium.core.state import (
    Session,
    SpeakerSelection,
    SpeakerGroup,
    CycleConfig,
    NameSource,
    StateStore,
)
from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.ha.registry import HARegistry
    from sonorium.ha.media_controller import HAMediaController
    from sonorium.core.channel import ChannelManager, Channel
    from sonorium.core.cycle_manager import CycleManager
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
        cycle_manager: CycleManager = None,
        themes: IndexList[ThemeDefinition] = None,
        theme_metadata_manager: ThemeMetadataManager = None,
    ):
        self.state = state_store
        self.registry = ha_registry
        self.media_controller = media_controller
        self.stream_base_url = stream_base_url or "http://localhost:8080"
        self.channel_manager = channel_manager
        self.cycle_manager = cycle_manager
        self.themes = themes
        self.theme_metadata_manager = theme_metadata_manager

        # Track which session is using which channel: session_id -> channel_id
        self._session_channels: dict[str, int] = {}
    
    def set_media_controller(self, controller: HAMediaController):
        """Set the media controller (for deferred initialization)."""
        self.media_controller = controller
    
    def set_stream_base_url(self, url: str):
        """Set the stream base URL."""
        self.stream_base_url = url.rstrip("/")
    
    def set_cycle_manager(self, cycle_manager: CycleManager):
        """Set the cycle manager (for deferred initialization)."""
        self.cycle_manager = cycle_manager

    def set_themes(self, themes: IndexList[ThemeDefinition]):
        """Update themes reference (called after theme refresh)."""
        self.themes = themes
        logger.info(f"  SessionManager: Updated themes reference ({len(themes)} themes)")

    def set_theme_metadata_manager(self, manager: ThemeMetadataManager):
        """Set the theme metadata manager (for deferred initialization)."""
        self.theme_metadata_manager = manager

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

        # Get preset data from metadata manager or file
        preset_tracks = None

        if self.theme_metadata_manager:
            # Use metadata manager (has the cache)
            folder = self.theme_metadata_manager.get_folder_for_id(theme_id)
            if folder:
                metadata = self.theme_metadata_manager.get_metadata_by_folder(folder)
                if metadata and preset_id in metadata.presets:
                    preset_tracks = metadata.presets[preset_id].get("tracks", {})

        if preset_tracks is None:
            # Fallback: read directly from file
            import json
            from pathlib import Path

            # Find theme folder
            if self.themes and len(self.themes) > 0:
                first_theme = self.themes[0]
                if hasattr(first_theme, 'sonorium') and hasattr(first_theme.sonorium, 'path_audio'):
                    audio_path = first_theme.sonorium.path_audio
                    for folder in audio_path.iterdir():
                        if folder.is_dir():
                            metadata_path = folder / "metadata.json"
                            if metadata_path.exists():
                                try:
                                    metadata = json.loads(metadata_path.read_text())
                                    if metadata.get("id") == theme_id:
                                        presets = metadata.get("presets", {})
                                        if preset_id in presets:
                                            preset_tracks = presets[preset_id].get("tracks", {})
                                        break
                                except Exception:
                                    pass

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
            logger.info(f"  Assigned channel {channel.id} to session {session.id}")
        
        return channel
    
    def _release_channel(self, session_id: str):
        """Release a channel from a session."""
        channel_id = self._session_channels.pop(session_id, None)
        if channel_id and self.channel_manager:
            channel = self.channel_manager.get_channel(channel_id)
            if channel:
                channel.stop()
                logger.info(f"  Released channel {channel_id} from session {session_id}")
    
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
        cycle_config: CycleConfig = None,
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
            cycle_config: Theme cycling configuration (optional)

        Returns:
            Created session

        Raises:
            ValueError: If max sessions exceeded
        """
        # Check limits (hardcoded max of 20 sessions)
        max_sessions = 20
        if len(self.state.sessions) >= max_sessions:
            raise ValueError(f"Maximum of {max_sessions} sessions allowed")

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

        # Use provided cycle_config or create default
        if cycle_config is None:
            cycle_config = CycleConfig(
                enabled=False,
                interval_minutes=self.state.settings.default_cycle_interval,
                randomize=self.state.settings.default_cycle_randomize,
            )

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
            cycle_config=cycle_config,
        )
        
        # Store and save
        self.state.sessions[session_id] = session
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
        cycle_config: CycleConfig = None,
    ) -> tuple[Optional[Session], set, set]:
        """
        Update an existing session.

        Only provided fields are updated.
        If theme changes on a playing session, triggers seamless crossfade.

        Returns:
            Tuple of (session, added_speakers, removed_speakers)
            Returns (None, set(), set()) if session not found
        """
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return None, set(), set()

        # Track if theme or preset is changing
        theme_changed = theme_id is not None and theme_id != session.theme_id
        preset_changed = preset_id is not None and preset_id != session.preset_id

        # Get old speakers before update (for live speaker management)
        old_speakers = set(self.get_resolved_speakers(session)) if session.is_playing else set()
        speakers_changing = speaker_group_id is not None or adhoc_selection is not None

        # Update fields if provided
        if theme_id is not None:
            session.theme_id = theme_id

        if preset_id is not None:
            session.preset_id = preset_id

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

        if cycle_config is not None:
            session.cycle_config = cycle_config

        # Re-generate auto-name if needed
        if custom_name is None and session.name_source != NameSource.CUSTOM:
            group = None
            if session.speaker_group_id:
                group = self.state.speaker_groups.get(session.speaker_group_id)
            session.name, session.name_source = self.generate_session_name(
                session.adhoc_selection, group
            )

        self.state.save()

        # If session is playing and theme changed, trigger crossfade
        if session.is_playing and theme_changed and session.theme_id:
            # Apply preset before crossfade if session has one
            if session.preset_id:
                self.apply_preset_to_theme(session.theme_id, session.preset_id)

            self._trigger_theme_crossfade(session)

            # Reset cycle timer since theme was manually changed
            if self.cycle_manager:
                self.cycle_manager.reset_cycle(session_id)

        # If session is playing and preset changed (same theme), apply the preset
        elif session.is_playing and preset_changed and session.theme_id:
            if session.preset_id:
                self.apply_preset_to_theme(session.theme_id, session.preset_id)
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
    
    def update_cycle_config(
        self,
        session_id: str,
        enabled: bool = None,
        interval_minutes: int = None,
        randomize: bool = None,
        theme_ids: list[str] = None,
    ) -> Optional[Session]:
        """
        Update just the cycle configuration for a session.
        
        Convenience method for updating cycle settings without
        affecting other session properties.
        """
        session = self.state.sessions.get(session_id)
        if not session:
            return None
        
        if enabled is not None:
            session.cycle_config.enabled = enabled
        
        if interval_minutes is not None:
            session.cycle_config.interval_minutes = max(1, interval_minutes)
        
        if randomize is not None:
            session.cycle_config.randomize = randomize
        
        if theme_ids is not None:
            session.cycle_config.theme_ids = theme_ids
        
        self.state.save()
        
        # Reset cycle timer if cycling was just enabled
        if enabled and self.cycle_manager:
            self.cycle_manager.reset_cycle(session_id)
        
        logger.info(f"  Updated cycle config for '{session.name}': enabled={session.cycle_config.enabled}, interval={session.cycle_config.interval_minutes}m")
        return session
    
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
        channel.set_theme(theme)
    
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
        
        session = self.state.sessions.pop(session_id)
        self.state.save()
        
        logger.info(f"  Deleted session '{session.name}'")
        return True
    
    # --- Speaker Resolution ---
    
    def get_resolved_speakers(self, session: Session) -> list[str]:
        """
        Get the list of speaker entity_ids for a session.
        
        Resolves speaker group or ad-hoc selection to final list.
        """
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
        Also initializes the cycle timer if cycling is enabled.
        
        Returns:
            True if started successfully, False otherwise
        """
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return False
        
        if not session.theme_id:
            logger.warning(f"  Session has no theme selected")
            return False
        
        speakers = self.get_resolved_speakers(session)
        if not speakers:
            logger.warning(f"  Session has no speakers")
            return False
        
        if not self.media_controller:
            logger.warning(f"  No media controller available")
            return False
        
        # Apply session's preset to theme BEFORE assigning to channel
        if session.preset_id:
            self.apply_preset_to_theme(session.theme_id, session.preset_id)

        # Assign channel and set theme
        channel = self._assign_channel(session)
        if channel:
            theme = self.get_theme(session.theme_id)
            if theme:
                channel.set_theme(theme)
                logger.info(f"  Channel {channel.id}: theme '{theme.name}'")
        
        # Build stream URL (channel-based if available)
        stream_url = self.get_stream_url(session)
        logger.info(f"  Stream URL: {stream_url}")
        
        # Mark as playing immediately (optimistic update)
        session.is_playing = True
        session.mark_played()
        self.state.save()
        
        # Initialize cycle timer if enabled
        if session.cycle_config.enabled and self.cycle_manager:
            self.cycle_manager.reset_cycle(session_id)
            logger.info(f"  Cycle enabled: every {session.cycle_config.interval_minutes}m")
        
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
            logger.info(f"  Started playback on {success_count}/{len(speakers)} speakers")
            
        except Exception as e:
            logger.error(f"  Error starting playback: {e}")
    
    @logger.instrument("Pausing session {session_id}...")
    async def pause(self, session_id: str) -> bool:
        """
        Pause playback for a session.
        
        Returns:
            True if paused, False if session not found
        """
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return False
        
        speakers = self.get_resolved_speakers(session)
        
        if self.media_controller and speakers:
            await self.media_controller.pause_multi(speakers)
        
        session.is_playing = False
        self.state.save()
        
        logger.info(f"  Paused session '{session.name}'")
        return True
    
    @logger.instrument("Stopping session {session_id}...")
    async def stop(self, session_id: str) -> bool:
        """
        Stop playback for a session.
        
        Releases the channel and stops speakers.
        
        Returns:
            True if stopped, False if session not found
        """
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return False
        
        speakers = self.get_resolved_speakers(session)
        
        if self.media_controller and speakers:
            await self.media_controller.stop_multi(speakers)
        
        # Release channel
        self._release_channel(session_id)
        
        session.is_playing = False
        self.state.save()
        
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
    
    async def stop_all(self) -> int:
        """
        Stop all playing sessions.
        
        Returns:
            Number of sessions stopped
        """
        count = 0
        for session in self.state.sessions.values():
            if session.is_playing:
                await self.stop(session.id)
                count += 1
        return count