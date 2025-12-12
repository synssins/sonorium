"""
Session Manager

Handles CRUD operations for playback sessions, including:
- Creating sessions with auto-naming
- Updating session configuration
- Play/pause/stop control with channel-based streaming
- Volume management
- Seamless theme transitions via channel crossfading
"""

from __future__ import annotations

import uuid
from typing import Optional, TYPE_CHECKING

from sonorium.core.state import (
    Session,
    SpeakerSelection,
    SpeakerGroup,
    NameSource,
    StateStore,
)
from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.ha.registry import HARegistry
    from sonorium.ha.media_controller import HAMediaController
    from sonorium.core.channel import ChannelManager, Channel
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
    ):
        """
        Initialize SessionManager.
        
        Args:
            state_store: StateStore for persistence
            ha_registry: HARegistry for speaker resolution
            media_controller: HAMediaController for playback (optional, for testing)
            stream_base_url: Base URL for audio streams (e.g., "http://192.168.1.104:8008")
            channel_manager: ChannelManager for channel-based streaming
            themes: List of available themes
        """
        self.state = state_store
        self.registry = ha_registry
        self.media_controller = media_controller
        self.stream_base_url = stream_base_url or "http://localhost:8080"
        self.channel_manager = channel_manager
        self.themes = themes
        
        # Track which session is using which channel: session_id -> channel_id
        self._session_channels: dict[str, int] = {}
    
    def set_media_controller(self, controller: HAMediaController):
        """Set the media controller (for deferred initialization)."""
        self.media_controller = controller
    
    def set_stream_base_url(self, url: str):
        """Set the stream base URL."""
        self.stream_base_url = url.rstrip("/")
    
    def get_theme(self, theme_id: str) -> Optional[ThemeDefinition]:
        """Get a theme by ID."""
        if not self.themes:
            return None
        return self.themes.id.get(theme_id)
    
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
    
    @logger.instrument("Creating new session...")
    def create(
        self,
        theme_id: str = None,
        speaker_group_id: str = None,
        adhoc_selection: SpeakerSelection = None,
        custom_name: str = None,
        volume: int = None,
    ) -> Session:
        """
        Create a new session.
        
        Args:
            theme_id: Theme to play (optional, can set later)
            speaker_group_id: Saved speaker group to use
            adhoc_selection: Ad-hoc speaker selection (if not using group)
            custom_name: Custom name (overrides auto-naming)
            volume: Initial volume (uses default if not specified)
        
        Returns:
            Created session
        
        Raises:
            ValueError: If max sessions exceeded
        """
        # Check limits
        max_sessions = self.state.settings.max_sessions
        if len(self.state.sessions) >= max_sessions:
            raise ValueError(f"Maximum of {max_sessions} sessions allowed")
        
        # Generate ID
        session_id = str(uuid.uuid4())[:8]
        
        # Determine name
        if custom_name:
            name = custom_name
            name_source = NameSource.CUSTOM
        else:
            group = None
            if speaker_group_id:
                group = self.state.speaker_groups.get(speaker_group_id)
            name, name_source = self.generate_session_name(adhoc_selection, group)
        
        # Use default volume if not specified
        if volume is None:
            volume = self.state.settings.default_volume
        
        # Create session
        session = Session(
            id=session_id,
            name=name,
            name_source=name_source,
            theme_id=theme_id,
            speaker_group_id=speaker_group_id,
            adhoc_selection=adhoc_selection,
            volume=volume,
            is_playing=False,
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
        speaker_group_id: str = None,
        adhoc_selection: SpeakerSelection = None,
        custom_name: str = None,
        volume: int = None,
    ) -> Optional[Session]:
        """
        Update an existing session.
        
        Only provided fields are updated.
        If theme changes on a playing session, triggers seamless crossfade.
        
        Returns:
            Updated session, or None if not found
        """
        session = self.state.sessions.get(session_id)
        if not session:
            logger.warning(f"  Session {session_id} not found")
            return None
        
        # Track if theme is changing
        theme_changed = theme_id is not None and theme_id != session.theme_id
        
        # Update fields if provided
        if theme_id is not None:
            session.theme_id = theme_id
        
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
        
        self.state.save()
        
        # If session is playing and theme changed, trigger crossfade
        if session.is_playing and theme_changed and session.theme_id:
            self._trigger_theme_crossfade(session)
        
        logger.info(f"  Updated session '{session.name}'")
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
