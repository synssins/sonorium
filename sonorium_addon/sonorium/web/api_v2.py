"""
Sonorium REST API

Provides endpoints for the web UI to manage sessions, speaker groups,
theme cycling, and retrieve speaker hierarchy from Home Assistant.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status, BackgroundTasks, Request, UploadFile, File, Response
from pydantic import BaseModel, Field

from sonorium.core.state import SpeakerSelection, CycleConfig, NameSource
from sonorium.obs import logger


# --- Request/Response Models ---

class SpeakerSelectionModel(BaseModel):
    """Speaker selection for creating/updating sessions or groups."""
    include_floors: list[str] = Field(default_factory=list)
    include_areas: list[str] = Field(default_factory=list)
    include_speakers: list[str] = Field(default_factory=list)
    exclude_areas: list[str] = Field(default_factory=list)
    exclude_speakers: list[str] = Field(default_factory=list)
    
    def to_selection(self) -> SpeakerSelection:
        return SpeakerSelection(
            include_floors=self.include_floors,
            include_areas=self.include_areas,
            include_speakers=self.include_speakers,
            exclude_areas=self.exclude_areas,
            exclude_speakers=self.exclude_speakers,
        )


class CycleConfigModel(BaseModel):
    """Theme cycling configuration."""
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=1, le=1440)  # 1 min to 24 hours
    randomize: bool = False
    theme_ids: list[str] = Field(default_factory=list)  # Empty = all themes
    
    def to_config(self) -> CycleConfig:
        return CycleConfig(
            enabled=self.enabled,
            interval_minutes=self.interval_minutes,
            randomize=self.randomize,
            theme_ids=self.theme_ids,
        )


class CycleConfigResponse(BaseModel):
    """Theme cycling configuration response."""
    enabled: bool
    interval_minutes: int
    randomize: bool
    theme_ids: list[str]


class CycleStatusResponse(BaseModel):
    """Current cycling status for a session."""
    enabled: bool
    interval_minutes: int
    randomize: bool
    theme_ids: list[str]
    next_change: Optional[str] = None  # ISO timestamp
    seconds_until_change: Optional[int] = None
    themes_in_rotation: int = 0


class SpaceRequest(BaseModel):
    """Add or change a floor or area (Settings > Floors & Areas)."""
    name: Optional[str] = None
    floor_id: Optional[str] = None


class CreateSessionRequest(BaseModel):
    """Request to create a new session."""
    theme_id: Optional[str] = None
    preset_id: Optional[str] = None
    speaker_group_id: Optional[str] = None
    adhoc_selection: Optional[SpeakerSelectionModel] = None
    custom_name: Optional[str] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)
    cycle_config: Optional[CycleConfigModel] = None


class UpdateSessionRequest(BaseModel):
    """Request to update an existing session."""
    theme_id: Optional[str] = None
    preset_id: Optional[str] = None
    speaker_group_id: Optional[str] = None
    adhoc_selection: Optional[SpeakerSelectionModel] = None
    custom_name: Optional[str] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)
    cycle_config: Optional[CycleConfigModel] = None


class UpdateCycleRequest(BaseModel):
    """Request to update cycling configuration."""
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    randomize: Optional[bool] = None
    theme_ids: Optional[list[str]] = None


class SessionResponse(BaseModel):
    """Session details response."""
    id: str
    name: str
    name_source: str
    theme_id: Optional[str]
    preset_id: Optional[str] = None
    speaker_group_id: Optional[str]
    adhoc_selection: Optional[dict]
    volume: int
    is_playing: bool
    speakers: list[str]  # Resolved speaker list
    speaker_summary: str  # Human-readable summary
    channel_id: Optional[int] = None  # Assigned channel (if playing)
    cycle_config: CycleConfigResponse
    created_at: str
    last_played_at: Optional[str]


class CreateGroupRequest(BaseModel):
    """Request to create a new speaker group."""
    name: str
    icon: str = "mdi:speaker-group"
    include_floors: list[str] = Field(default_factory=list)
    include_areas: list[str] = Field(default_factory=list)
    include_speakers: list[str] = Field(default_factory=list)
    exclude_areas: list[str] = Field(default_factory=list)
    exclude_speakers: list[str] = Field(default_factory=list)


class UpdateGroupRequest(BaseModel):
    """Request to update an existing speaker group."""
    name: Optional[str] = None
    icon: Optional[str] = None
    include_floors: Optional[list[str]] = None
    include_areas: Optional[list[str]] = None
    include_speakers: Optional[list[str]] = None
    exclude_areas: Optional[list[str]] = None
    exclude_speakers: Optional[list[str]] = None


class GroupResponse(BaseModel):
    """Speaker group details response."""
    id: str
    name: str
    icon: str
    include_floors: list[str]
    include_areas: list[str]
    include_speakers: list[str]
    exclude_areas: list[str]
    exclude_speakers: list[str]
    speakers: list[str]  # Resolved speaker list
    speaker_count: int
    summary: str  # Human-readable summary
    created_at: str
    updated_at: str


class VolumeRequest(BaseModel):
    """Request to set volume."""
    volume: int = Field(ge=0, le=100)


class SettingsResponse(BaseModel):
    """Settings response."""
    default_volume: int
    crossfade_duration: float
    max_groups: int
    entity_prefix: str
    show_in_sidebar: bool
    auto_create_quick_play: bool
    master_gain: int
    default_cycle_interval: int
    default_cycle_randomize: bool


class UpdateSettingsRequest(BaseModel):
    """Request to update settings."""
    default_volume: Optional[int] = Field(default=None, ge=0, le=100)
    crossfade_duration: Optional[float] = Field(default=None, ge=0, le=10.0)
    max_groups: Optional[int] = Field(default=None, ge=1, le=50)
    entity_prefix: Optional[str] = None
    show_in_sidebar: Optional[bool] = None
    auto_create_quick_play: Optional[bool] = None
    master_gain: Optional[int] = Field(default=None, ge=0, le=100)
    default_cycle_interval: Optional[int] = Field(default=None, ge=1, le=1440)
    default_cycle_randomize: Optional[bool] = None


class SpeakerSettingsResponse(BaseModel):
    """Speaker settings response with hierarchy."""
    enabled_speakers: list[str]  # Exact: only these are switched on
    hierarchy: Optional[dict] = None  # Full speaker hierarchy


class UpdateSpeakerSettingsRequest(BaseModel):
    """Request to update enabled speakers."""
    enabled_speakers: list[str]


class SingleSpeakerRequest(BaseModel):
    """Request to enable/disable a single speaker."""
    entity_id: str


class SpeakerSettingsUpdateRequest(BaseModel):
    """Per-speaker settings; only the fields sent change, and null resets one to its default."""
    name: Optional[str] = None  # "" or null = the original name
    room: Optional[str] = None  # area ID, "" = no room, null = the speaker's own HA area
    volume_offset: Optional[int] = None  # percent, -20..+20
    play_via: Optional[str] = None  # "ha" or a merged network speaker ID


class ManualSpeakerRequest(BaseModel):
    """A speaker added by address (standalone mode)."""
    address: str
    type: str = "auto"  # auto, cast, sonos, dlna, airplay, linkplay, heos
    name: Optional[str] = None
    room: Optional[str] = None  # area ID
    port: Optional[int] = None


class CustomAreasRequest(BaseModel):
    """Request to update all custom speaker areas."""
    custom_areas: dict[str, list[str]] = Field(default_factory=dict)


class CreateCustomAreaRequest(BaseModel):
    """Request to create a custom speaker area."""
    name: str
    speakers: list[str] = Field(default_factory=list)


class UpdateCustomAreaRequest(BaseModel):
    """Request to update a custom speaker area."""
    name: Optional[str] = None
    speakers: Optional[list[str]] = None


# --- Plugin Models ---

class PluginResponse(BaseModel):
    """Plugin details response."""
    id: str
    name: str
    version: str
    description: str
    author: str
    enabled: bool
    settings: dict
    ui_schema: dict
    settings_schema: dict


class PluginActionRequest(BaseModel):
    """Request to execute a plugin action."""
    action: str
    data: dict = Field(default_factory=dict)


class PluginSettingsRequest(BaseModel):
    """Request to update plugin settings."""
    settings: dict


class ChannelResponse(BaseModel):
    """Channel status response."""
    id: int
    name: str
    state: str
    current_theme: Optional[str]
    current_theme_name: Optional[str]
    client_count: int
    stream_path: str


# --- Helper Functions ---

def _session_to_response(session, session_manager) -> SessionResponse:
    """Convert a Session to SessionResponse."""
    cycle_config = session.cycle_config or CycleConfig()
    return SessionResponse(
        id=session.id,
        name=session.name,
        name_source=session.name_source.value,
        theme_id=session.theme_id,
        preset_id=getattr(session, 'preset_id', None),
        speaker_group_id=session.speaker_group_id,
        adhoc_selection=asdict(session.adhoc_selection) if session.adhoc_selection else None,
        volume=session.volume,
        is_playing=session.is_playing,
        speakers=session_manager.get_resolved_speakers(session),
        speaker_summary=session_manager.get_speaker_summary(session),
        channel_id=session_manager.get_session_channel(session.id),
        cycle_config=CycleConfigResponse(
            enabled=cycle_config.enabled,
            interval_minutes=cycle_config.interval_minutes,
            randomize=cycle_config.randomize,
            theme_ids=cycle_config.theme_ids,
        ),
        created_at=session.created_at,
        last_played_at=session.last_played_at,
    )


# --- API Router Factory ---

def create_api_router(
    session_manager,
    group_manager,
    ha_registry,
    state_store,
    theme_manager=None,
    channel_manager=None,
    cycle_manager=None,
    plugin_manager=None,
    mqtt_manager=None,
    on_themes_changed=None,
    network_service=None,
) -> APIRouter:
    """
    Create the API router with all endpoints.

    Args:
        session_manager: SessionManager instance
        group_manager: GroupManager instance
        ha_registry: HARegistry instance
        state_store: StateStore instance
        theme_manager: Optional theme manager for theme endpoints
        channel_manager: Optional ChannelManager for channel-based streaming
        cycle_manager: Optional CycleManager for theme cycling
        plugin_manager: Optional PluginManager for plugin endpoints
        mqtt_manager: Optional MQTT manager for HA entity updates
        on_themes_changed: Optional callback after themes are added to or deleted,
            so the theme list (and MQTT theme selects) gets rescanned
        network_service: Optional NetworkSpeakerService (standalone mode only)

    Returns:
        Configured APIRouter
    """
    router = APIRouter(prefix="/api", tags=["api"])
    
    # --- Debug Endpoint ---
    
    @router.get("/debug/speakers")
    async def debug_speakers() -> dict:
        """Debug endpoint to show raw speaker discovery data."""
        from fmtr.tools import http
        from sonorium.settings import settings
        
        debug_info = {
            "api_url": ha_registry.api_url,
            "token_present": bool(ha_registry.token),
            "cached_floors": len(ha_registry._floors),
            "cached_areas": len(ha_registry._areas),
            "cached_speakers": len(ha_registry._speakers),
            "hierarchy": None,
            "errors": [],
            "raw_states_sample": [],
        }
        
        # Try to get raw states
        try:
            url = f"{ha_registry.api_url}/states"
            with http.Client() as client:
                response = client.get(url, headers=ha_registry.headers)
                states = response.json()
                
                # Filter to media_player entities
                media_players = [
                    {
                        "entity_id": s.get("entity_id"),
                        "state": s.get("state"),
                        "friendly_name": s.get("attributes", {}).get("friendly_name"),
                    }
                    for s in states
                    if s.get("entity_id", "").startswith("media_player.")
                ]
                debug_info["raw_states_sample"] = media_players[:20]  # Limit to 20
                debug_info["total_media_players_in_states"] = len(media_players)
        except Exception as e:
            debug_info["errors"].append(f"Failed to fetch states: {str(e)}")
        
        # Get hierarchy
        try:
            hierarchy = ha_registry.hierarchy
            debug_info["hierarchy"] = {
                "floors": len(hierarchy.floors),
                "unassigned_areas": len(hierarchy.unassigned_areas),
                "unassigned_speakers": len(hierarchy.unassigned_speakers),
                "total_speakers": len(hierarchy.get_all_speakers()),
                "floor_details": [
                    {
                        "name": f.name,
                        "floor_id": f.floor_id,
                        "areas": [
                            {
                                "name": a.name,
                                "area_id": a.area_id,
                                "speakers": [s.entity_id for s in a.speakers]
                            }
                            for a in f.areas
                        ]
                    }
                    for f in hierarchy.floors
                ],
                "unassigned_area_details": [
                    {
                        "name": a.name,
                        "area_id": a.area_id,
                        "speakers": [s.entity_id for s in a.speakers]
                    }
                    for a in hierarchy.unassigned_areas
                ],
                "unassigned_speaker_details": [
                    {"entity_id": s.entity_id, "name": s.name}
                    for s in hierarchy.unassigned_speakers
                ],
            }
        except Exception as e:
            debug_info["errors"].append(f"Failed to get hierarchy: {str(e)}")
        
        return debug_info
    
    # --- Session Endpoints ---
    
    @router.get("/sessions")
    async def list_sessions() -> list[SessionResponse]:
        """List all sessions."""
        sessions = session_manager.list()
        return [_session_to_response(s, session_manager) for s in sessions]
    
    @router.post("/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(request: CreateSessionRequest) -> SessionResponse:
        """Create a new session."""
        try:
            session = session_manager.create(
                theme_id=request.theme_id,
                preset_id=request.preset_id,
                speaker_group_id=request.speaker_group_id,
                adhoc_selection=request.adhoc_selection.to_selection() if request.adhoc_selection else None,
                custom_name=request.custom_name,
                volume=request.volume,
                cycle_config=request.cycle_config.to_config() if request.cycle_config else None,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

        if mqtt_manager:
            try:
                await mqtt_manager.add_session_entities(session)
                await mqtt_manager.sync_all_states()
            except Exception as e:
                logger.warning(f"Failed to publish MQTT entities for new session: {e}")

        return _session_to_response(session, session_manager)
    
    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> SessionResponse:
        """Get a session by ID."""
        session = session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return _session_to_response(session, session_manager)
    
    @router.put("/sessions/{session_id}")
    async def update_session(session_id: str, request: UpdateSessionRequest) -> SessionResponse:
        """Update an existing session."""
        # Get old session name to detect changes for MQTT discovery refresh
        old_session = session_manager.get(session_id)
        old_name = old_session.name if old_session else None

        session, added_speakers, removed_speakers = session_manager.update(
            session_id=session_id,
            theme_id=request.theme_id,
            preset_id=request.preset_id,
            speaker_group_id=request.speaker_group_id,
            adhoc_selection=request.adhoc_selection.to_selection() if request.adhoc_selection else None,
            custom_name=request.custom_name,
            volume=request.volume,
            cycle_config=request.cycle_config.to_config() if request.cycle_config else None,
        )
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        # Apply live speaker changes if session is playing
        if added_speakers or removed_speakers:
            await session_manager.apply_speaker_changes(session, added_speakers, removed_speakers)

        # Apply volume to speakers if changed and session is playing
        if request.volume is not None and session.is_playing:
            speakers = session_manager.get_resolved_speakers(session)
            if speakers and session_manager.media_controller:
                volume_level = session.volume / 100.0
                await session_manager.media_controller.set_volume_multi(speakers, volume_level)
                logger.info(f"Applied volume {session.volume}% to {len(speakers)} speaker(s)")

        if mqtt_manager:
            try:
                if session.name != old_name:
                    await mqtt_manager.refresh_session_discovery(session)
                await mqtt_manager.sync_all_states()
            except Exception as e:
                logger.warning(f"Failed to refresh MQTT entities for updated session: {e}")

        return _session_to_response(session, session_manager)
    
    @router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(session_id: str):
        """Delete a session."""
        if not session_manager.delete(session_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if mqtt_manager:
            try:
                await mqtt_manager.remove_session_entities(session_id)
                await mqtt_manager.sync_all_states()
            except Exception as e:
                logger.warning(f"Failed to remove MQTT entities for deleted session: {e}")
    
    @router.post("/sessions/{session_id}/play")
    async def play_session(session_id: str) -> dict:
        """Start playback for a session (fire-and-forget, returns immediately)."""
        session = session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        
        if not session.theme_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No theme selected")
        if not session_manager.get_theme(session.theme_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Theme not found")
        
        speakers = session_manager.get_resolved_speakers(session)
        if not speakers:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No speakers selected")
        
        # Mark as playing immediately (optimistic update)
        session.is_playing = True
        session.mark_played()
        state_store.save()
        
        # Fire the play command in the background - don't wait for it
        asyncio.create_task(session_manager.play(session_id))
        
        return {
            "status": "playing", 
            "channel_id": session_manager.get_session_channel(session_id),
            "cycling": session.cycle_config.enabled if session.cycle_config else False,
        }
    
    @router.post("/sessions/{session_id}/pause")
    async def pause_session(session_id: str) -> dict:
        """Pause playback for a session."""
        success = await session_manager.pause(session_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return {"status": "paused"}
    
    @router.post("/sessions/{session_id}/stop")
    async def stop_session(session_id: str) -> dict:
        """Stop playback for a session."""
        success = await session_manager.stop(session_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return {"status": "stopped"}
    
    @router.post("/sessions/{session_id}/volume")
    async def set_session_volume(session_id: str, request: VolumeRequest) -> dict:
        """Set volume for a session."""
        success = await session_manager.set_volume(session_id, request.volume)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return {"volume": request.volume}
    
    @router.post("/sessions/stop-all")
    async def stop_all_sessions() -> dict:
        """Stop all playing sessions."""
        count = await session_manager.stop_all()
        return {"stopped": count}
    
    # --- Theme Cycling Endpoints ---
    
    @router.get("/sessions/{session_id}/cycle")
    async def get_cycle_status(session_id: str) -> CycleStatusResponse:
        """Get cycling status for a session."""
        session = session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        
        cycle_config = session.cycle_config or CycleConfig()
        
        # Get runtime status from cycle manager
        status_data = None
        if cycle_manager and session.is_playing:
            status_data = cycle_manager.get_cycle_status(session_id)
        
        return CycleStatusResponse(
            enabled=cycle_config.enabled,
            interval_minutes=cycle_config.interval_minutes,
            randomize=cycle_config.randomize,
            theme_ids=cycle_config.theme_ids,
            next_change=status_data.get("next_change") if status_data else None,
            seconds_until_change=status_data.get("seconds_until_change") if status_data else None,
            themes_in_rotation=status_data.get("themes_in_rotation", 0) if status_data else 0,
        )
    
    @router.put("/sessions/{session_id}/cycle")
    async def update_cycle_config(session_id: str, request: UpdateCycleRequest) -> CycleStatusResponse:
        """Update cycling configuration for a session."""
        session = session_manager.update_cycle_config(
            session_id=session_id,
            enabled=request.enabled,
            interval_minutes=request.interval_minutes,
            randomize=request.randomize,
            theme_ids=request.theme_ids,
        )
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        
        cycle_config = session.cycle_config
        
        # Get runtime status from cycle manager
        status_data = None
        if cycle_manager and session.is_playing:
            status_data = cycle_manager.get_cycle_status(session_id)
        
        return CycleStatusResponse(
            enabled=cycle_config.enabled,
            interval_minutes=cycle_config.interval_minutes,
            randomize=cycle_config.randomize,
            theme_ids=cycle_config.theme_ids,
            next_change=status_data.get("next_change") if status_data else None,
            seconds_until_change=status_data.get("seconds_until_change") if status_data else None,
            themes_in_rotation=status_data.get("themes_in_rotation", 0) if status_data else 0,
        )
    
    @router.post("/sessions/{session_id}/cycle/skip")
    async def skip_to_next_theme(session_id: str) -> dict:
        """Skip to the next theme in the cycle."""
        session = session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        
        if not session.is_playing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session is not playing")
        
        if not cycle_manager:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cycling not available")
        
        # Manually trigger a cycle
        await cycle_manager._cycle_theme(session)
        
        return {
            "status": "skipped",
            "new_theme_id": session.theme_id,
        }
    
    # --- Channel Endpoints ---
    
    @router.get("/channels")
    async def list_channels() -> list[ChannelResponse]:
        """List all channels."""
        if not channel_manager:
            return []
        return [
            ChannelResponse(**ch)
            for ch in channel_manager.list_channels()
        ]

    @router.get("/channels/{channel_id}")
    async def get_channel(channel_id: int) -> ChannelResponse:
        """Get a specific channel."""
        if not channel_manager:
            raise HTTPException(status_code=503, detail="Channel system not initialized")
        channel = channel_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail=f"Channel {channel_id} not found")
        return ChannelResponse(**channel.to_dict())

    @router.post("/channels/{channel_id}/play")
    async def play_channel(channel_id: int, request: dict = None):
        """Play a theme on a specific channel."""
        if not channel_manager:
            raise HTTPException(status_code=503, detail="Channel system not initialized")

        channel = channel_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail=f"Channel {channel_id} not found")

        # Get theme_id from request body
        theme_id = None
        if request:
            theme_id = request.get("theme_id")

        if not theme_id:
            raise HTTPException(status_code=400, detail="theme_id is required")

        # Get the theme from session_manager's theme registry
        theme = session_manager.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")

        # Set theme on channel
        channel.set_theme(theme)

        return {
            "status": "playing",
            "channel_id": channel_id,
            "theme_id": theme_id,
        }

    @router.post("/channels/{channel_id}/stop")
    async def stop_channel(channel_id: int):
        """Stop playback on a specific channel."""
        if not channel_manager:
            raise HTTPException(status_code=503, detail="Channel system not initialized")

        channel = channel_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail=f"Channel {channel_id} not found")

        channel.stop()

        return {
            "status": "stopped",
            "channel_id": channel_id,
        }

    @router.post("/channels/{channel_id}/volume")
    async def set_channel_volume(channel_id: int, request: dict):
        """Set volume for a channel (placeholder - channels don't have individual volume yet)."""
        if not channel_manager:
            raise HTTPException(status_code=503, detail="Channel system not initialized")

        channel = channel_manager.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail=f"Channel {channel_id} not found")

        volume = request.get("volume", 50)

        # Note: Channel volume is not yet implemented in the core
        # This is a placeholder that acknowledges the request
        return {
            "status": "acknowledged",
            "channel_id": channel_id,
            "volume": volume,
            "note": "Channel-level volume control not yet implemented",
        }

    # --- Speaker Group Endpoints ---
    
    @router.get("/groups")
    async def list_groups() -> list[GroupResponse]:
        """List all speaker groups."""
        groups = group_manager.list()
        return [
            GroupResponse(
                id=g.id,
                name=g.name,
                icon=g.icon,
                include_floors=g.include_floors,
                include_areas=g.include_areas,
                include_speakers=g.include_speakers,
                exclude_areas=g.exclude_areas,
                exclude_speakers=g.exclude_speakers,
                speakers=group_manager.resolve(g),
                speaker_count=group_manager.get_speaker_count(g),
                summary=group_manager.get_summary(g),
                created_at=g.created_at,
                updated_at=g.updated_at,
            )
            for g in groups
        ]
    
    @router.post("/groups", status_code=status.HTTP_201_CREATED)
    async def create_group(request: CreateGroupRequest) -> GroupResponse:
        """Create a new speaker group."""
        try:
            group = group_manager.create(
                name=request.name,
                icon=request.icon,
                include_floors=request.include_floors,
                include_areas=request.include_areas,
                include_speakers=request.include_speakers,
                exclude_areas=request.exclude_areas,
                exclude_speakers=request.exclude_speakers,
            )
            return GroupResponse(
                id=group.id,
                name=group.name,
                icon=group.icon,
                include_floors=group.include_floors,
                include_areas=group.include_areas,
                include_speakers=group.include_speakers,
                exclude_areas=group.exclude_areas,
                exclude_speakers=group.exclude_speakers,
                speakers=group_manager.resolve(group),
                speaker_count=group_manager.get_speaker_count(group),
                summary=group_manager.get_summary(group),
                created_at=group.created_at,
                updated_at=group.updated_at,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    
    @router.get("/groups/{group_id}")
    async def get_group(group_id: str) -> GroupResponse:
        """Get a speaker group by ID."""
        group = group_manager.get(group_id)
        if not group:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        return GroupResponse(
            id=group.id,
            name=group.name,
            icon=group.icon,
            include_floors=group.include_floors,
            include_areas=group.include_areas,
            include_speakers=group.include_speakers,
            exclude_areas=group.exclude_areas,
            exclude_speakers=group.exclude_speakers,
            speakers=group_manager.resolve(group),
            speaker_count=group_manager.get_speaker_count(group),
            summary=group_manager.get_summary(group),
            created_at=group.created_at,
            updated_at=group.updated_at,
        )
    
    @router.put("/groups/{group_id}")
    async def update_group(group_id: str, request: UpdateGroupRequest) -> GroupResponse:
        """Update an existing speaker group."""
        try:
            group = group_manager.update(
                group_id=group_id,
                name=request.name,
                icon=request.icon,
                include_floors=request.include_floors,
                include_areas=request.include_areas,
                include_speakers=request.include_speakers,
                exclude_areas=request.exclude_areas,
                exclude_speakers=request.exclude_speakers,
            )
            if not group:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
            return GroupResponse(
                id=group.id,
                name=group.name,
                icon=group.icon,
                include_floors=group.include_floors,
                include_areas=group.include_areas,
                include_speakers=group.include_speakers,
                exclude_areas=group.exclude_areas,
                exclude_speakers=group.exclude_speakers,
                speakers=group_manager.resolve(group),
                speaker_count=group_manager.get_speaker_count(group),
                summary=group_manager.get_summary(group),
                created_at=group.created_at,
                updated_at=group.updated_at,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    
    @router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_group(group_id: str):
        """Delete a speaker group."""
        # Check if any sessions use this group
        session_ids = group_manager.get_sessions_using_group(group_id)
        if session_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Group is used by {len(session_ids)} channel(s). Delete or update those channels first."
            )
        if not group_manager.delete(group_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    
    @router.get("/groups/{group_id}/resolve")
    async def resolve_group(group_id: str) -> dict:
        """Get resolved speaker list for a group."""
        group = group_manager.get(group_id)
        if not group:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        speakers = group_manager.resolve(group)
        return {
            "speakers": speakers,
            "count": len(speakers),
            "summary": group_manager.get_summary(group),
        }
    
    # --- Speaker Hierarchy Endpoints ---
    
    @router.get("/speakers")
    async def list_speakers() -> list[dict]:
        """List all available speakers (flat list)."""
        hierarchy = ha_registry.hierarchy
        speakers = hierarchy.get_all_speakers()
        return [s.to_dict() for s in speakers]
    
    @router.get("/speakers/hierarchy")
    async def get_speaker_hierarchy() -> dict:
        """Get full floor/area/speaker hierarchy."""
        hierarchy = ha_registry.hierarchy
        return hierarchy.to_dict()
    
    @router.post("/speakers/refresh")
    async def refresh_speakers() -> dict:
        """Refresh speaker hierarchy from Home Assistant (and, standalone, rescan the network)."""
        hierarchy = ha_registry.refresh()
        if network_service is not None:
            await network_service.discover(full=True)
            hierarchy = ha_registry.hierarchy
        return {
            "floors": len(hierarchy.floors),
            "unassigned_areas": len(hierarchy.unassigned_areas),
            "unassigned_speakers": len(hierarchy.unassigned_speakers),
            "total_speakers": len(hierarchy.get_all_speakers()),
        }
    
    # --- Network Speakers (standalone mode; 404 in the HA add-on) ---

    def _require_network_service():
        if network_service is None:
            raise HTTPException(status_code=404, detail="Network speakers are only available in standalone mode")
        return network_service

    @router.get("/network-speakers")
    async def list_network_speakers() -> dict:
        """Network speakers found by discovery, with protocol details."""
        service = _require_network_service()
        speakers = sorted(service.speakers.values(), key=lambda s: s.name.lower())
        result = []
        for speaker in speakers:
            item = speaker.to_dict()
            item["playing"] = service.streaming.is_playing(speaker.id)
            item["manual"] = bool(speaker.extra.get("manual"))
            result.append(item)
        return {
            "speakers": result,
            "total_speakers": len(result),
            "last_scan": service.last_scan,  # ISO time (UTC), None before the first scan finishes
            "found": service.last_scan_found,  # speakers the last scan found
        }

    @router.post("/network-speakers/refresh")
    async def refresh_network_speakers() -> dict:
        """Rescan the network for speakers now."""
        service = _require_network_service()
        total = await service.discover(full=True)
        return {"total_speakers": total, "last_scan": service.last_scan, "found": service.last_scan_found}

    # --- Per-speaker settings (both modes) ---

    def _speaker_or_404(speaker_id: str):
        speaker = ha_registry.get_speaker(speaker_id)
        if speaker is None:
            raise HTTPException(status_code=404, detail="Speaker not found")
        return speaker

    def _speaker_settings_response(speaker_id: str) -> dict:
        speaker = _speaker_or_404(speaker_id)
        return {
            "speaker_id": speaker_id,
            "settings": dict(state_store.settings.speaker_settings.get(speaker_id) or {}),
            "speaker": speaker.to_dict(),
        }

    @router.get("/speakers/{speaker_id}/settings")
    async def get_one_speaker_settings(speaker_id: str) -> dict:
        """A speaker's saved settings (name, room, volume_offset, play_via) and how it looks with them."""
        return _speaker_settings_response(speaker_id)

    @router.put("/speakers/{speaker_id}/settings")
    async def update_one_speaker_settings(speaker_id: str, request: SpeakerSettingsUpdateRequest) -> dict:
        """Change a speaker's settings. Fields left out stay; null resets one."""
        from sonorium.core.speaker_settings import SpeakerSettingsError, clean_speaker_settings

        speaker = _speaker_or_404(speaker_id)
        updates = {key: getattr(request, key) for key in request.model_fields_set}
        room = updates.get("room")
        if room and ha_registry.get_area(room) is None:
            raise HTTPException(status_code=400, detail="Unknown room")
        via = updates.get("play_via")
        if via and via != "ha" and via not in [m["id"] for m in speaker.merged]:
            raise HTTPException(status_code=400, detail="This speaker can't play that way")

        all_settings = state_store.settings.speaker_settings
        try:
            cleaned = clean_speaker_settings(all_settings.get(speaker_id) or {}, updates)
        except SpeakerSettingsError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if cleaned:
            all_settings[speaker_id] = cleaned
        else:
            all_settings.pop(speaker_id, None)
        state_store.save()
        ha_registry.apply_speaker_settings()
        return _speaker_settings_response(speaker_id)

    # --- Test sound (user-triggered, one speaker) ---

    TEST_VOLUME = 0.2
    TEST_SECONDS = 4.0  # the chime is 3 s; stop shortly after it ends
    tests_running: set[str] = set()

    @router.api_route("/test-tone.mp3", methods=["GET", "HEAD"])
    async def test_tone() -> Response:
        """The short chime the speaker test plays."""
        from sonorium.core.speaker_test_tone import tone_mp3

        data = await asyncio.get_running_loop().run_in_executor(None, tone_mp3)
        return Response(content=data, media_type="audio/mpeg", headers={"Cache-Control": "no-cache"})

    @router.post("/speakers/{speaker_id}/test")
    async def test_speaker(speaker_id: str) -> dict:
        """Play a short, quiet chime on one speaker, then stop it."""
        _speaker_or_404(speaker_id)
        if speaker_id in tests_running:
            raise HTTPException(status_code=409, detail="A test sound is already playing on this speaker")
        for session in session_manager.list():
            if session.is_playing and speaker_id in session_manager.get_resolved_speakers(session):
                raise HTTPException(status_code=409, detail=f"This speaker is playing '{session.name}'. Stop it first.")

        controller = session_manager.media_controller
        raw_controller = getattr(controller, "controller", controller)  # without volume offsets
        url = f"{session_manager.stream_base_url.rstrip('/')}/api/test-tone.mp3"
        target = controller.target_for(speaker_id) if hasattr(controller, "target_for") else speaker_id

        # Home Assistant speakers get their volume back afterwards
        previous_volume = None
        if not target.startswith("net:"):
            try:
                state = await controller.get_state(target)
                previous_volume = (state or {}).get("attributes", {}).get("volume_level")
            except Exception:
                previous_volume = None

        tests_running.add(speaker_id)
        try:
            await controller.set_volume_multi([speaker_id], TEST_VOLUME)
            result = await controller.play_media_multi([speaker_id], url)
        except Exception as e:
            logger.warning(f"Speaker test on {speaker_id} failed: {e}")
            result = None
        if not (result or {}).get(speaker_id):
            tests_running.discard(speaker_id)
            raise HTTPException(status_code=502, detail="Couldn't play on this speaker")

        async def finish():
            try:
                await asyncio.sleep(TEST_SECONDS)
                await controller.stop_multi([speaker_id])
                if previous_volume is not None:
                    await raw_controller.set_volume_multi([target], float(previous_volume))
            except Exception as e:
                logger.debug(f"Speaker test on {speaker_id}: cleanup failed: {e}")
            finally:
                tests_running.discard(speaker_id)

        asyncio.get_running_loop().create_task(finish())
        return {"speaker_id": speaker_id, "playing": True, "seconds": TEST_SECONDS}

    # --- Floors & Areas: Home Assistant's, plus Sonorium's own where editing is on ---

    def _spaces_editable() -> bool:
        from sonorium import runtime
        return runtime.feature_enabled("space_editing")

    def _space_dict(space, kind: str) -> dict:
        speakers = space.speakers if kind == "area" else [s for a in space.areas for s in a.speakers]
        return {
            "id": space.area_id if kind == "area" else space.floor_id,
            "name": space.name,
            "source": list(space.source),
            # The ID to edit it by: Sonorium's own, also when merged into HA's
            "local_id": space.local_id or (space.area_id if kind == "area" else space.floor_id) if "local" in space.source else None,
            "speakers": len(speakers),
        }

    def _spaces_response() -> dict:
        hierarchy = ha_registry.hierarchy
        return {
            "editable": _spaces_editable(),
            "floors": [
                {**_space_dict(f, "floor"), "areas": [_space_dict(a, "area") for a in f.areas]}
                for f in hierarchy.floors
            ],
            "unassigned_areas": [_space_dict(a, "area") for a in hierarchy.unassigned_areas],
        }

    def _change_spaces(change) -> dict:
        from sonorium.core.spaces import SpaceError
        if not _spaces_editable():
            raise HTTPException(status_code=404, detail="Floors and areas are managed in Home Assistant")
        local = state_store.settings.local_spaces
        try:
            change(local)
        except SpaceError as e:
            raise HTTPException(status_code=400, detail=str(e))
        state_store.save()
        ha_registry.apply_speaker_settings()  # rebuild with the new spaces
        return _spaces_response()

    def _local_floor_id(floor_id: Optional[str]) -> Optional[str]:
        """A floor picked in the UI (listed ID) as the ID to store, so it survives merges."""
        if not floor_id:
            return None
        floor = ha_registry.get_floor(floor_id)
        if floor is None:
            raise HTTPException(status_code=400, detail="Unknown floor")
        return floor.local_id or floor.floor_id

    @router.get("/spaces")
    async def list_spaces() -> dict:
        """Floors and their areas, where each came from, and whether this install can edit them."""
        return _spaces_response()

    @router.post("/spaces/floors", status_code=status.HTTP_201_CREATED)
    async def add_floor(request: SpaceRequest) -> dict:
        from sonorium.core import spaces
        return _change_spaces(lambda local: spaces.add_floor(local, request.name))

    @router.post("/spaces/areas", status_code=status.HTTP_201_CREATED)
    async def add_area(request: SpaceRequest) -> dict:
        from sonorium.core import spaces
        floor_id = _local_floor_id(request.floor_id)
        return _change_spaces(lambda local: spaces.add_area(local, request.name, floor_id))

    @router.put("/spaces/floors/{floor_id}")
    async def update_floor(floor_id: str, request: SpaceRequest) -> dict:
        from sonorium.core import spaces
        return _change_spaces(lambda local: spaces.rename(local, "floor", floor_id, request.name))

    @router.put("/spaces/areas/{area_id}")
    async def update_area(area_id: str, request: SpaceRequest) -> dict:
        from sonorium.core import spaces
        fields = request.model_fields_set
        floor_id = _local_floor_id(request.floor_id) if "floor_id" in fields else None

        def change(local):
            if "name" in fields:
                spaces.rename(local, "area", area_id, request.name)
            if "floor_id" in fields:
                spaces.move_area(local, area_id, floor_id)
        return _change_spaces(change)

    @router.delete("/spaces/floors/{floor_id}")
    async def delete_floor(floor_id: str) -> dict:
        from sonorium.core import spaces
        return _change_spaces(lambda local: spaces.delete_floor(local, floor_id))

    @router.delete("/spaces/areas/{area_id}")
    async def delete_area(area_id: str) -> dict:
        from sonorium.core import spaces
        return _change_spaces(lambda local: spaces.delete_area(local, area_id, state_store.settings.speaker_settings))

    # --- Manual speakers (standalone mode; 404 in the HA add-on) ---

    def _manual_result(speaker) -> dict:
        from sonorium.network.manual import TYPE_LABELS, manual_kind

        kind = manual_kind(speaker)
        return {
            "id": speaker.id,
            "name": speaker.name,
            "type": kind,
            "type_label": TYPE_LABELS.get(kind, kind),
            "host": speaker.host,
            "address": speaker.extra.get("address") or speaker.host,
            "port": speaker.port,
        }

    @router.post("/speakers/manual/check")
    async def check_manual_speaker(request: ManualSpeakerRequest) -> dict:
        """What answers at an address, without adding it ("Check connection")."""
        service = _require_network_service()
        from sonorium.network.manual import ProbeError

        try:
            speaker = await service.check_manual(request.address, request.type, request.port, request.name)
        except ProbeError as e:
            return {"found": False, "message": str(e)}
        result = _manual_result(speaker)
        result.update(found=True, message=f"Found {result['type_label']} speaker \"{speaker.name}\" at {speaker.host}.")
        return result

    @router.post("/speakers/manual", status_code=status.HTTP_201_CREATED)
    async def add_manual_speaker(request: ManualSpeakerRequest) -> dict:
        """Add a speaker by address. It's checked first: 400 if nothing answers."""
        service = _require_network_service()
        from sonorium.network.manual import ProbeError

        if request.room and ha_registry.get_area(request.room) is None:
            raise HTTPException(status_code=400, detail="Unknown room")
        try:
            speaker = await service.add_manual(request.address, request.type, request.port, request.name)
        except ProbeError as e:
            raise HTTPException(status_code=400, detail=str(e))

        settings = state_store.settings
        if request.room:
            settings.speaker_settings[speaker.id] = {"room": request.room}
        # Added on purpose, so switch it on
        _set_speaker_enabled(speaker.id, True)
        state_store.save()
        ha_registry.merge_extra_speakers()

        result = _manual_result(speaker)
        merged_into = ha_registry.get_merged_owner(speaker.id)
        if merged_into:
            result["merged_into"] = merged_into
        return result

    @router.delete("/speakers/manual/{speaker_id}")
    async def remove_manual_speaker(speaker_id: str) -> dict:
        """Remove a manually added speaker."""
        service = _require_network_service()
        if not await service.remove_manual(speaker_id):
            raise HTTPException(status_code=404, detail="Not a manually added speaker")
        settings = state_store.settings
        settings.speaker_settings.pop(speaker_id, None)
        for other in settings.speaker_settings.values():
            if other.get("play_via") == speaker_id:
                other.pop("play_via", None)
        _set_speaker_enabled(speaker_id, False)
        state_store.save()
        ha_registry.merge_extra_speakers()
        return {"removed": speaker_id}

    @router.post("/speakers/resolve")
    async def resolve_selection(request: SpeakerSelectionModel) -> dict:
        """Resolve a speaker selection to a list of entity_ids."""
        speakers = ha_registry.resolve_selection(
            include_floors=request.include_floors,
            include_areas=request.include_areas,
            include_speakers=request.include_speakers,
            exclude_areas=request.exclude_areas,
            exclude_speakers=request.exclude_speakers,
        )
        return {
            "speakers": speakers,
            "count": len(speakers),
        }
    
    # --- Enabled speakers (Settings > Speakers): an exact list ---

    def _migrate_enabled_speakers():
        """Convert an older "empty = all" list once the speaker list is known."""
        if ha_registry and state_store.settings.migrate_enabled_speakers(ha_registry.get_all_speaker_ids()):
            state_store.save()

    def _set_speaker_enabled(speaker_id: str, enabled: bool):
        _migrate_enabled_speakers()
        settings = state_store.settings
        if not settings.enabled_speakers_exact:
            # Speaker list still unknown: start an exact list from what we know
            settings.enabled_speakers = [s for s in settings.enabled_speakers if s != "__none__"]
            settings.enabled_speakers_exact = True
        if enabled and speaker_id not in settings.enabled_speakers:
            settings.enabled_speakers.append(speaker_id)
        elif not enabled and speaker_id in settings.enabled_speakers:
            settings.enabled_speakers.remove(speaker_id)

    def _enabled_speakers_response() -> SpeakerSettingsResponse:
        _migrate_enabled_speakers()
        settings = state_store.settings
        all_ids = ha_registry.get_all_speaker_ids() if ha_registry else []
        return SpeakerSettingsResponse(
            enabled_speakers=settings.effective_enabled_speakers(all_ids),
            hierarchy=ha_registry.get_hierarchy_dict() if ha_registry else None,
        )

    # --- Settings Endpoints ---
    
    @router.get("/settings")
    async def get_settings() -> SettingsResponse:
        """Get current settings."""
        settings = state_store.settings
        return SettingsResponse(
            default_volume=settings.default_volume,
            crossfade_duration=settings.crossfade_duration,
            max_groups=settings.max_groups,
            entity_prefix=settings.entity_prefix,
            show_in_sidebar=settings.show_in_sidebar,
            auto_create_quick_play=settings.auto_create_quick_play,
            master_gain=settings.master_gain,
            default_cycle_interval=settings.default_cycle_interval,
            default_cycle_randomize=settings.default_cycle_randomize,
        )

    @router.put("/settings")
    async def update_settings(request: UpdateSettingsRequest) -> SettingsResponse:
        """Update settings."""
        settings = state_store.settings

        if request.default_volume is not None:
            settings.default_volume = request.default_volume
        if request.crossfade_duration is not None:
            settings.crossfade_duration = request.crossfade_duration
        if request.max_groups is not None:
            settings.max_groups = request.max_groups
        if request.entity_prefix is not None:
            settings.entity_prefix = request.entity_prefix
        if request.show_in_sidebar is not None:
            settings.show_in_sidebar = request.show_in_sidebar
        if request.auto_create_quick_play is not None:
            settings.auto_create_quick_play = request.auto_create_quick_play
        if request.master_gain is not None:
            settings.master_gain = request.master_gain
        if request.default_cycle_interval is not None:
            settings.default_cycle_interval = request.default_cycle_interval
        if request.default_cycle_randomize is not None:
            settings.default_cycle_randomize = request.default_cycle_randomize

        state_store.save()

        return SettingsResponse(
            default_volume=settings.default_volume,
            crossfade_duration=settings.crossfade_duration,
            max_groups=settings.max_groups,
            entity_prefix=settings.entity_prefix,
            show_in_sidebar=settings.show_in_sidebar,
            auto_create_quick_play=settings.auto_create_quick_play,
            master_gain=settings.master_gain,
            default_cycle_interval=settings.default_cycle_interval,
            default_cycle_randomize=settings.default_cycle_randomize,
        )

    # --- Speaker Settings Endpoints ---

    @router.get("/settings/speakers")
    async def get_speaker_settings() -> SpeakerSettingsResponse:
        """Get enabled speakers and full hierarchy."""
        return _enabled_speakers_response()

    @router.put("/settings/speakers")
    async def update_speaker_settings(request: UpdateSpeakerSettingsRequest) -> SpeakerSettingsResponse:
        """Update enabled speakers list."""
        settings = state_store.settings
        settings.enabled_speakers = [s for s in request.enabled_speakers if s != "__none__"]
        settings.enabled_speakers_exact = True
        state_store.save()
        return _enabled_speakers_response()

    @router.post("/settings/speakers/enable")
    async def enable_speaker(request: SingleSpeakerRequest) -> SpeakerSettingsResponse:
        """Switch a speaker on."""
        _set_speaker_enabled(request.entity_id, True)
        state_store.save()
        return _enabled_speakers_response()

    @router.post("/settings/speakers/disable")
    async def disable_speaker(request: SingleSpeakerRequest) -> SpeakerSettingsResponse:
        """Switch a speaker off."""
        _set_speaker_enabled(request.entity_id, False)
        state_store.save()
        return _enabled_speakers_response()

    @router.post("/settings/speakers/enable-all")
    async def enable_all_speakers() -> SpeakerSettingsResponse:
        """Switch every known speaker on."""
        settings = state_store.settings
        settings.enabled_speakers = ha_registry.get_all_speaker_ids() if ha_registry else []
        settings.enabled_speakers_exact = True
        state_store.save()
        return _enabled_speakers_response()

    @router.post("/settings/speakers/disable-all")
    async def disable_all_speakers() -> SpeakerSettingsResponse:
        """Switch every speaker off."""
        settings = state_store.settings
        settings.enabled_speakers = []
        settings.enabled_speakers_exact = True
        state_store.save()
        return _enabled_speakers_response()

    @router.get("/settings/speaker-areas")
    async def get_custom_speaker_areas() -> dict:
        """Get custom speaker area assignments."""
        settings = state_store.settings
        return {
            "custom_areas": settings.custom_speaker_areas,
        }

    @router.put("/settings/speaker-areas")
    async def update_custom_speaker_areas(request: CustomAreasRequest) -> dict:
        """Update all custom speaker area assignments."""
        settings = state_store.settings
        settings.custom_speaker_areas = request.custom_areas
        state_store.save()
        return {
            "custom_areas": settings.custom_speaker_areas,
        }

    @router.post("/settings/speaker-areas/create")
    async def create_custom_area(request: CreateCustomAreaRequest) -> dict:
        """Create a new custom speaker area."""
        settings = state_store.settings
        area_name = request.name.strip()
        if not area_name:
            raise HTTPException(status_code=400, detail="Area name is required")
        if area_name in settings.custom_speaker_areas:
            raise HTTPException(status_code=400, detail="Area already exists")

        settings.custom_speaker_areas[area_name] = request.speakers
        state_store.save()
        return {
            "name": area_name,
            "speakers": settings.custom_speaker_areas[area_name],
        }

    @router.put("/settings/speaker-areas/{area_name}")
    async def update_custom_area(area_name: str, request: UpdateCustomAreaRequest) -> dict:
        """Update a custom speaker area."""
        settings = state_store.settings
        if area_name not in settings.custom_speaker_areas:
            raise HTTPException(status_code=404, detail="Area not found")

        # Handle rename
        new_name = (request.name or area_name).strip()
        speakers = request.speakers if request.speakers is not None else settings.custom_speaker_areas[area_name]

        if new_name != area_name:
            # Rename: delete old, create new
            del settings.custom_speaker_areas[area_name]
            settings.custom_speaker_areas[new_name] = speakers
        else:
            settings.custom_speaker_areas[area_name] = speakers

        state_store.save()
        return {
            "name": new_name,
            "speakers": speakers,
        }

    @router.delete("/settings/speaker-areas/{area_name}")
    async def delete_custom_area(area_name: str) -> dict:
        """Delete a custom speaker area."""
        settings = state_store.settings
        if area_name not in settings.custom_speaker_areas:
            raise HTTPException(status_code=404, detail="Area not found")

        del settings.custom_speaker_areas[area_name]
        state_store.save()
        return {"deleted": area_name}

    @router.post("/settings/speaker-areas/{area_name}/add-speaker")
    async def add_speaker_to_area(area_name: str, request: SingleSpeakerRequest) -> dict:
        """Add a speaker to a custom area."""
        settings = state_store.settings
        if area_name not in settings.custom_speaker_areas:
            raise HTTPException(status_code=404, detail="Area not found")

        if request.entity_id not in settings.custom_speaker_areas[area_name]:
            settings.custom_speaker_areas[area_name].append(request.entity_id)
            state_store.save()

        return {
            "name": area_name,
            "speakers": settings.custom_speaker_areas[area_name],
        }

    @router.post("/settings/speaker-areas/{area_name}/remove-speaker")
    async def remove_speaker_from_area(area_name: str, request: SingleSpeakerRequest) -> dict:
        """Remove a speaker from a custom area."""
        settings = state_store.settings
        if area_name not in settings.custom_speaker_areas:
            raise HTTPException(status_code=404, detail="Area not found")

        if request.entity_id in settings.custom_speaker_areas[area_name]:
            settings.custom_speaker_areas[area_name].remove(request.entity_id)
            state_store.save()

        return {
            "name": area_name,
            "speakers": settings.custom_speaker_areas[area_name],
        }

    # --- Theme Endpoints ---
    # NOTE: GET /themes is handled by app.py with full metadata support
    # api_v2.py only handles theme management (create, upload, delete, metadata)

    @router.post("/themes/create")
    async def create_theme(request: Request):
        """Create a new theme folder."""
        from pathlib import Path
        import re
        import json

        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        name = body.get("name", "").strip()
        description = body.get("description", "").strip()
        icon = body.get("icon", "🎵").strip()

        if not name:
            raise HTTPException(status_code=400, detail="Theme name is required")

        # Generate a safe folder name from the theme name
        folder_name = re.sub(r'[^\w\s-]', '', name.lower())
        folder_name = re.sub(r'[-\s]+', '_', folder_name).strip('_')

        if not folder_name:
            raise HTTPException(status_code=400, detail="Invalid theme name - could not generate folder name")

        # Find the media path
        media_paths = [
            Path("/media/sonorium"),
            Path("/share/sonorium"),
        ]

        media_path = None
        for mp in media_paths:
            if mp.exists():
                media_path = mp
                break

        if not media_path:
            # Try to create the default media path
            media_path = Path("/media/sonorium")
            try:
                media_path.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.error(f"Failed to create media path: {e}")
                raise HTTPException(status_code=500, detail="Media path not available")

        # Create the theme folder
        theme_path = media_path / folder_name
        if theme_path.exists():
            raise HTTPException(status_code=409, detail=f"Theme folder '{folder_name}' already exists")

        try:
            theme_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"Created theme folder: {theme_path}")

            # Write metadata.json with description and icon
            metadata = {}
            if description:
                metadata["description"] = description
            if icon and icon != "🎵":  # Only store non-default icons
                metadata["icon"] = icon
            if metadata:
                metadata["spec_version"] = 2
                metadata_path = theme_path / "metadata.json"
                metadata_path.write_text(json.dumps(metadata, indent=2))

            return {
                "status": "ok",
                "theme_id": folder_name,
                "path": str(theme_path),
                "message": f"Theme '{name}' created successfully"
            }
        except Exception as e:
            logger.error(f"Failed to create theme folder: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/themes/{theme_id}/upload")
    async def upload_theme_file(theme_id: str, request: Request):
        """
        Upload an audio file to a theme folder. An optional "group" (form field
        or query parameter) puts it in that group's folder, created if missing.
        """
        from sonorium.core import theme_groups

        theme_path = _find_theme_folder(theme_id)
        if not theme_path:
            raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")

        # Parse multipart form data
        try:
            form = await request.form()
            file = form.get("file")
            if not file:
                raise HTTPException(status_code=400, detail="No file provided")

            # Only the file's own name: no folders, no path tricks
            try:
                filename = theme_groups.safe_upload_name(file.filename)
            except theme_groups.GroupError as e:
                raise HTTPException(status_code=400, detail=str(e))

            # Validate file extension
            valid_extensions = ['.mp3', '.wav', '.flac', '.ogg']
            ext = '.' + filename.split('.')[-1].lower() if '.' in filename else ''

            if ext not in valid_extensions:
                raise HTTPException(status_code=400, detail=f"Invalid file type. Supported: {', '.join(valid_extensions)}")

            # Optional group (created if missing)
            group = form.get("group") or request.query_params.get("group")
            try:
                file_path = theme_groups.upload_path(theme_path, filename, group)
            except theme_groups.GroupError as e:
                raise HTTPException(status_code=e.status, detail=str(e))
            target_folder = file_path.parent

            # Read and write the file content
            content = await file.read()
            file_path.write_bytes(content)

            group_name = target_folder.name if target_folder != theme_path else None
            where = f" in group '{group_name}'" if group_name else ""
            logger.info(f"Uploaded file to theme '{theme_id}'{where}: {filename} ({len(content)} bytes)")
            if on_themes_changed:
                on_themes_changed()

            return {
                "status": "ok",
                "filename": filename,
                "size": len(content),
                "theme_id": theme_id,
                "group": group_name,
                "track": f"{group_name}/{file_path.stem}" if group_name else file_path.stem,
            }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to upload file: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    def _find_theme_folder(theme_id: str):
        """Find theme folder by ID, handling sanitized names and UUID-based IDs."""
        import json
        from pathlib import Path
        from fmtr.tools.string_tools import sanitize

        media_paths = [
            Path("/media/sonorium"),
            Path("/share/sonorium"),
        ]

        for mp in media_paths:
            if not mp.exists():
                continue

            # Try exact match first (folder name = theme_id)
            exact_path = mp / theme_id
            if exact_path.exists():
                return exact_path

            # Scan folders for UUID match in metadata.json or sanitized name match
            for folder in mp.iterdir():
                if folder.is_dir():
                    # Check metadata.json for UUID match
                    metadata_path = folder / "metadata.json"
                    if metadata_path.exists():
                        try:
                            metadata = json.loads(metadata_path.read_text())
                            if metadata.get("id") == theme_id:
                                return folder
                        except Exception:
                            pass

                    # Try sanitized folder name match
                    sanitized = sanitize(folder.name)
                    if sanitized == theme_id:
                        return folder

        return None

    @router.put("/themes/{theme_id}/metadata")
    async def update_theme_metadata(theme_id: str, request: Request):
        """Update theme metadata (description, etc.)."""
        import json

        theme_path = _find_theme_folder(theme_id)
        if not theme_path:
            raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")

        # Parse JSON body
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")

        # Read existing metadata and merge. If it can't be read, refuse: writing
        # only the edited fields would lose the theme's id and track settings.
        from sonorium.core.theme_presets import BrokenJsonError, read_json, write_json_atomic
        metadata_path = theme_path / "metadata.json"
        try:
            metadata = read_json(metadata_path) or {}
        except BrokenJsonError as e:
            raise HTTPException(status_code=409, detail=f"metadata.json can't be read ({e}); refresh themes to repair it first")

        if "description" in body:
            metadata["description"] = body["description"]

        if "icon" in body:
            # Allow setting icon to empty string to clear it (use auto-detection)
            icon_value = body["icon"]
            if icon_value:
                metadata["icon"] = icon_value
            elif "icon" in metadata:
                del metadata["icon"]  # Remove icon to use auto-detection

        if "short_file_threshold" in body:
            threshold = float(body["short_file_threshold"])
            if threshold < 0:
                raise HTTPException(status_code=400, detail="short_file_threshold must be >= 0")
            metadata["short_file_threshold"] = threshold

            # Also update the live theme object if it exists
            if themes:
                theme = themes.id.get(theme_id)
                if theme:
                    theme.short_file_threshold = threshold
                    logger.info(f"Updated short_file_threshold for '{theme_id}' to {threshold}s")

        # Write back (temporary file, then rename), and reload so the theme
        # manager's copy can't overwrite the change later
        try:
            write_json_atomic(metadata_path, metadata)
            if on_themes_changed:
                on_themes_changed()
            return {"status": "ok", "metadata": metadata}
        except Exception as e:
            logger.error(f"Failed to write metadata: {e}")
            raise HTTPException(status_code=500, detail="Could not write metadata")

    @router.delete("/themes/{theme_id}")
    async def delete_theme(theme_id: str):
        """Delete a theme folder and all its contents."""
        import shutil

        theme_path = _find_theme_folder(theme_id)
        if not theme_path:
            raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")

        try:
            shutil.rmtree(theme_path)
            logger.info(f"Deleted theme folder: {theme_path}")

            # Remove from favorites if present
            if state_store:
                favorites = state_store.settings.favorite_themes
                if theme_id in favorites:
                    favorites.remove(theme_id)
                    state_store.save()

            if on_themes_changed:
                on_themes_changed()

            return {"status": "ok", "theme_id": theme_id, "message": "Theme deleted"}
        except Exception as e:
            logger.error(f"Failed to delete theme: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/themes/{theme_id}/export")
    async def export_theme(theme_id: str):
        """Export a theme as a zip file containing all audio files and metadata."""
        import zipfile
        import io
        from fastapi.responses import StreamingResponse

        theme_path = _find_theme_folder(theme_id)
        if not theme_path:
            raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")

        try:
            # Create zip in memory
            zip_buffer = io.BytesIO()
            theme_name = theme_path.name

            import json
            from sonorium.core.theme_metadata import theme_documents_for_export
            from sonorium.core.theme_presets import METADATA_FILE, PRESETS_FILE

            def is_theme_json(path):
                # The theme's own JSON files, their backups and broken copies
                if path.parent != theme_path:
                    return False
                return any(path.name == base or path.name.startswith(base + ".")
                           for base in (METADATA_FILE, PRESETS_FILE))

            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # Walk through all files in the theme folder
                for file_path in theme_path.rglob('*'):
                    if file_path.is_file() and not is_theme_json(file_path):
                        # Use relative path within the theme folder
                        arcname = f"{theme_name}/{file_path.relative_to(theme_path)}"
                        zip_file.write(file_path, arcname)
                        logger.debug(f"Added to zip: {arcname}")

                # Always the 2.0 layout: metadata.json without presets, plus presets.json
                metadata_doc, presets_doc = theme_documents_for_export(theme_path)

                # Self-contained: an intrusion group's linked files (they live in
                # other themes' folders) go in as normal files of their group,
                # the links are dropped and their settings kept under the same keys
                from sonorium.core.intrusions import export_entries
                for relative, source in export_entries(theme_path, metadata_doc):
                    zip_file.write(source, f"{theme_name}/{relative}")
                    logger.debug(f"Added linked file to zip: {relative} (from {source})")

                zip_file.writestr(f"{theme_name}/{METADATA_FILE}",
                                  json.dumps(metadata_doc, indent=2, ensure_ascii=False))
                zip_file.writestr(f"{theme_name}/{PRESETS_FILE}",
                                  json.dumps(presets_doc, indent=2, ensure_ascii=False))

            zip_buffer.seek(0)

            # Generate filename
            safe_name = "".join(c if c.isalnum() or c in "._- " else "_" for c in theme_name)
            filename = f"{safe_name}.zip"

            logger.info(f"Exported theme '{theme_name}' as {filename}")

            return StreamingResponse(
                zip_buffer,
                media_type="application/zip",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"'
                }
            )

        except Exception as e:
            logger.error(f"Failed to export theme: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/themes/import")
    async def import_theme(request: Request):
        """Import a theme from a zip file."""
        import zipfile
        import io
        import json
        from pathlib import Path

        # Get the audio path
        audio_path = Path("/media/sonorium")
        if not audio_path.exists():
            audio_path = Path("/share/sonorium")
        if not audio_path.exists():
            raise HTTPException(status_code=500, detail="No valid audio path found")

        try:
            # Parse multipart form data
            form = await request.form()
            file = form.get("file")
            if not file:
                raise HTTPException(status_code=400, detail="No file provided")

            filename = file.filename
            if not filename.lower().endswith('.zip'):
                raise HTTPException(status_code=400, detail="File must be a .zip archive")

            # Read zip content
            content = await file.read()
            zip_buffer = io.BytesIO(content)

            # Validate and extract
            with zipfile.ZipFile(zip_buffer, 'r') as zip_file:
                # Get the list of files
                namelist = zip_file.namelist()
                if not namelist:
                    raise HTTPException(status_code=400, detail="Zip file is empty")

                # Determine theme folder name from zip structure
                # Expected: theme_name/file1.mp3, theme_name/metadata.json, etc.
                first_entry = namelist[0]
                if '/' in first_entry:
                    theme_folder_name = first_entry.split('/')[0]
                else:
                    # No folder structure, use zip filename without extension
                    theme_folder_name = filename.rsplit('.', 1)[0]

                # Check if theme already exists
                target_path = audio_path / theme_folder_name
                if target_path.exists():
                    # Generate unique name
                    counter = 1
                    while target_path.exists():
                        target_path = audio_path / f"{theme_folder_name}_{counter}"
                        counter += 1
                    theme_folder_name = target_path.name

                # Extract files
                target_path.mkdir(parents=True, exist_ok=True)
                files_extracted = 0
                audio_extensions = {'.mp3', '.wav', '.flac', '.ogg'}

                for zip_info in zip_file.infolist():
                    if zip_info.is_dir():
                        continue

                    # Get the path relative to the theme folder in the zip
                    parts = zip_info.filename.split('/')
                    if len(parts) > 1:
                        # Remove the original theme folder prefix
                        relative_path = '/'.join(parts[1:])
                    else:
                        relative_path = zip_info.filename

                    if not relative_path:
                        continue

                    # Determine output path
                    output_path = target_path / relative_path

                    # Create parent directories if needed
                    output_path.parent.mkdir(parents=True, exist_ok=True)

                    # Extract file
                    with zip_file.open(zip_info) as src:
                        output_path.write_bytes(src.read())
                    files_extracted += 1
                    logger.debug(f"Extracted: {relative_path}")

                # Convert 1.0 themes and recover broken JSON files, then give
                # the imported theme a new UUID to avoid conflicts
                from sonorium.core.theme_metadata import load_theme_folder, save_theme_folder
                import uuid
                problems = []
                try:
                    metadata = load_theme_folder(target_path)
                    problems = list(metadata.problems)
                    metadata.id = str(uuid.uuid4())
                    if not save_theme_folder(target_path, metadata):
                        problems.append("Couldn't save the theme files")
                except Exception as e:
                    logger.warning(f"Could not prepare imported theme files: {e}")
                    problems.append(f"Couldn't prepare the theme files: {e}")

                logger.info(f"Imported theme '{theme_folder_name}' with {files_extracted} files")

                return {
                    "status": "ok",
                    "theme_folder": theme_folder_name,
                    "files_extracted": files_extracted,
                    "path": str(target_path),
                    "problems": problems,
                }

        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid zip file")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to import theme: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # --- Plugin Endpoints ---

    @router.get("/plugins", response_model=list[PluginResponse])
    async def list_plugins():
        """List all available plugins."""
        if not plugin_manager:
            return []
        return plugin_manager.list_plugins()

    # --- Plugin Catalog (Browse & Install from GitHub) ---
    # NOTE: These routes MUST come before /plugins/{plugin_id} to avoid route conflict

    _catalog_cache: dict = {'data': None, 'timestamp': 0}
    CATALOG_CACHE_TTL = 3600  # 1 hour

    @router.get("/plugins/catalog")
    async def get_plugin_catalog():
        """Fetch available plugins from the GitHub catalog."""
        import time
        import aiohttp

        now = time.time()

        if _catalog_cache['data'] and (now - _catalog_cache['timestamp']) < CATALOG_CACHE_TTL:
            catalog = _catalog_cache['data']
        else:
            catalog_url = 'https://raw.githubusercontent.com/synssins/sonorium/main/plugins/catalog.json'
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(catalog_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status != 200:
                            raise HTTPException(status_code=502, detail=f'Failed to fetch catalog: HTTP {resp.status}')
                        catalog = await resp.json(content_type=None)
                        _catalog_cache['data'] = catalog
                        _catalog_cache['timestamp'] = now
            except Exception as e:
                logger.error(f'Failed to fetch plugin catalog: {e}')
                if _catalog_cache['data']:
                    catalog = _catalog_cache['data']
                else:
                    raise HTTPException(status_code=502, detail=f'Failed to fetch catalog: {e}')

        # Enrich with installed status
        installed_plugins = {}
        if plugin_manager:
            for plugin in plugin_manager.plugins.values():
                installed_plugins[plugin.id] = plugin.version

        enriched_plugins = []
        for plugin in catalog.get('plugins', []):
            plugin_copy = dict(plugin)
            pid = plugin.get('id')
            if pid in installed_plugins:
                plugin_copy['installed'] = True
                plugin_copy['installed_version'] = installed_plugins[pid]
                plugin_copy['update_available'] = plugin.get('version') != installed_plugins[pid]
            else:
                plugin_copy['installed'] = False
                plugin_copy['installed_version'] = None
                plugin_copy['update_available'] = False
            enriched_plugins.append(plugin_copy)

        return {
            'version': catalog.get('version', 1),
            'updated': catalog.get('updated'),
            'plugins': enriched_plugins
        }

    @router.post("/plugins/install-from-catalog")
    async def install_plugin_from_catalog(request: Request):
        """Download and install a plugin from the GitHub catalog."""
        import aiohttp
        import zipfile
        import io
        import shutil

        if not plugin_manager:
            raise HTTPException(status_code=503, detail='Plugin system not initialized')

        body = await request.json()
        plugin_id = body.get('plugin_id')
        if not plugin_id:
            raise HTTPException(status_code=400, detail='plugin_id is required')

        # Fetch catalog
        catalog_url = 'https://raw.githubusercontent.com/synssins/sonorium/main/plugins/catalog.json'
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(catalog_url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        raise HTTPException(status_code=502, detail='Failed to fetch catalog')
                    catalog = await resp.json(content_type=None)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f'Failed to fetch catalog: {e}')

        # Find plugin
        plugin_info = None
        for p in catalog.get('plugins', []):
            if p.get('id') == plugin_id:
                plugin_info = p
                break

        if not plugin_info:
            raise HTTPException(status_code=404, detail=f'Plugin "{plugin_id}" not found in catalog')

        # Download ZIP
        zip_filename = plugin_info.get('zip_file')
        if not zip_filename:
            raise HTTPException(status_code=500, detail='Plugin has no zip_file specified')

        zip_url = f'https://raw.githubusercontent.com/synssins/sonorium/main/plugins/{zip_filename}'
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(zip_url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                    if resp.status != 200:
                        raise HTTPException(status_code=502, detail=f'Failed to download plugin: HTTP {resp.status}')
                    content = await resp.read()
        except Exception as e:
            raise HTTPException(status_code=502, detail=f'Failed to download plugin: {e}')

        # Install
        try:
            zip_buffer = io.BytesIO(content)
            with zipfile.ZipFile(zip_buffer, 'r') as zf:
                file_list = zf.namelist()
                plugin_py_paths = [f for f in file_list if f.endswith('plugin.py')]
                if not plugin_py_paths:
                    raise HTTPException(status_code=400, detail='No plugin.py found in ZIP')

                plugin_py = plugin_py_paths[0]
                plugin_dir_name = plugin_py.rsplit('/', 1)[0] if '/' in plugin_py else ''
                target_name = plugin_dir_name.split('/')[0] if plugin_dir_name else plugin_id
                target_dir = plugin_manager.plugins_dir / target_name

                if target_dir.exists():
                    shutil.rmtree(target_dir)

                target_dir.mkdir(parents=True, exist_ok=True)
                for member in zf.namelist():
                    if plugin_dir_name and member.startswith(plugin_dir_name + '/'):
                        target_path = target_dir / member[len(plugin_dir_name) + 1:]
                    elif plugin_dir_name and member == plugin_dir_name:
                        continue
                    else:
                        target_path = target_dir / member

                    if member.endswith('/'):
                        target_path.mkdir(parents=True, exist_ok=True)
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(target_path, 'wb') as dst:
                            dst.write(src.read())

            # Remove from deleted_builtin_plugins if reinstalling a previously deleted builtin
            # Check both plugin_id and target_name since they might differ
            deleted_list = plugin_manager.state_store.settings.deleted_builtin_plugins
            removed_from_deleted = False
            for name_to_check in [plugin_id, target_name]:
                if name_to_check in deleted_list:
                    deleted_list.remove(name_to_check)
                    removed_from_deleted = True
                    logger.info(f"Removed '{name_to_check}' from deleted builtins list")
            if removed_from_deleted:
                plugin_manager.state_store.save()

            await plugin_manager.reload_plugins()

            return {
                'status': 'ok',
                'plugin_id': plugin_id,
                'name': plugin_info.get('name', plugin_id),
                'version': plugin_info.get('version'),
                'message': f'Plugin "{plugin_info.get("name", plugin_id)}" installed successfully'
            }

        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail='Invalid ZIP file from catalog')
        except Exception as e:
            logger.error(f'Error installing plugin from catalog: {e}')
            raise HTTPException(status_code=500, detail=f'Failed to install plugin: {e}')

    # --- Individual Plugin Routes ---

    @router.get("/plugins/{plugin_id}", response_model=PluginResponse)
    async def get_plugin(plugin_id: str):
        """Get details for a specific plugin."""
        if not plugin_manager:
            raise HTTPException(status_code=503, detail="Plugin system not available")

        plugin = plugin_manager.get_plugin(plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")

        return plugin.to_dict()

    @router.put("/plugins/{plugin_id}/enable")
    async def enable_plugin(plugin_id: str):
        """Enable a plugin."""
        if not plugin_manager:
            raise HTTPException(status_code=503, detail="Plugin system not available")

        success = await plugin_manager.enable_plugin(plugin_id)
        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to enable plugin: {plugin_id}")

        return {"status": "ok", "plugin_id": plugin_id, "enabled": True}

    @router.put("/plugins/{plugin_id}/disable")
    async def disable_plugin(plugin_id: str):
        """Disable a plugin."""
        if not plugin_manager:
            raise HTTPException(status_code=503, detail="Plugin system not available")

        success = await plugin_manager.disable_plugin(plugin_id)
        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to disable plugin: {plugin_id}")

        return {"status": "ok", "plugin_id": plugin_id, "enabled": False}

    @router.get("/plugins/{plugin_id}/settings")
    async def get_plugin_settings(plugin_id: str):
        """Get settings for a plugin."""
        if not plugin_manager:
            raise HTTPException(status_code=503, detail="Plugin system not available")

        plugin = plugin_manager.get_plugin(plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")

        return {
            "plugin_id": plugin_id,
            "settings": plugin.settings,
            "schema": plugin.get_settings_schema(),
        }

    @router.put("/plugins/{plugin_id}/settings")
    async def update_plugin_settings(plugin_id: str, request: PluginSettingsRequest):
        """Update settings for a plugin."""
        if not plugin_manager:
            raise HTTPException(status_code=503, detail="Plugin system not available")

        success = plugin_manager.update_plugin_settings(plugin_id, request.settings)
        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to update plugin settings: {plugin_id}")

        return {"status": "ok", "plugin_id": plugin_id, "settings": request.settings}

    @router.post("/plugins/{plugin_id}/action")
    async def execute_plugin_action(plugin_id: str, request: PluginActionRequest):
        """Execute an action on a plugin."""
        if not plugin_manager:
            raise HTTPException(status_code=503, detail="Plugin system not available")

        result = await plugin_manager.call_action(plugin_id, request.action, request.data)
        return result

    @router.post("/plugins/reload")
    async def reload_plugins():
        """Reload all plugins."""
        if not plugin_manager:
            raise HTTPException(status_code=503, detail="Plugin system not available")

        await plugin_manager.reload_plugins()
        return {"status": "ok", "message": "Plugins reloaded", "count": len(plugin_manager.plugins)}

    @router.post("/plugins/upload")
    async def upload_plugin(file: UploadFile = File(...)):
        """
        Upload and install a plugin from a ZIP file.

        The ZIP file should contain either:
        - A single plugin.py file (minimal plugin)
        - A plugin.py file with optional manifest.json
        - A directory containing plugin.py (and optionally manifest.json)

        Returns the installed plugin info.
        """
        import zipfile
        import tempfile
        import shutil
        from pathlib import Path

        if not plugin_manager:
            raise HTTPException(status_code=503, detail="Plugin system not available")

        # Validate file type
        if not file.filename or not file.filename.lower().endswith('.zip'):
            raise HTTPException(status_code=400, detail="File must be a .zip archive")

        try:
            # Save uploaded file to temp location
            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = Path(tmp.name)

            # Extract and validate
            with tempfile.TemporaryDirectory() as extract_dir:
                extract_path = Path(extract_dir)

                with zipfile.ZipFile(tmp_path, 'r') as zf:
                    # Security: Check for path traversal
                    for member in zf.namelist():
                        if '..' in member or member.startswith('/'):
                            raise HTTPException(status_code=400, detail=f"Invalid path in zip: {member}")
                    zf.extractall(extract_path)

                # Find plugin.py - could be at root or in a subdirectory
                plugin_files = list(extract_path.rglob('plugin.py'))

                if not plugin_files:
                    raise HTTPException(status_code=400, detail="No plugin.py found in archive")

                # Use the first plugin.py found
                plugin_file = plugin_files[0]
                plugin_source_dir = plugin_file.parent

                # Get plugin ID from manifest or directory name
                manifest_file = plugin_source_dir / 'manifest.json'
                manifest = {}
                if manifest_file.exists():
                    import json
                    manifest = json.loads(manifest_file.read_text())
                    plugin_id = manifest.get('id', plugin_source_dir.name)
                else:
                    # Generate ID from directory name or zip filename
                    plugin_id = plugin_source_dir.name
                    if plugin_id in ('.', extract_path.name):
                        # Plugin files at root of zip - use zip filename
                        plugin_id = Path(file.filename).stem.lower().replace(' ', '_').replace('-', '_')

                # Sanitize plugin ID
                plugin_id = ''.join(c for c in plugin_id if c.isalnum() or c == '_').lower()
                if not plugin_id:
                    plugin_id = 'imported_plugin'

                # Check if plugin already exists
                target_dir = plugin_manager.plugins_dir / plugin_id
                if target_dir.exists():
                    # Remove old version
                    shutil.rmtree(target_dir)
                    logger.info(f"Replacing existing plugin: {plugin_id}")

                # Validate required plugin attributes by parsing the plugin.py file
                plugin_py_content = plugin_file.read_text()

                # Check for version attribute (required)
                import re
                version_match = re.search(r'^\s*version\s*[=:]\s*["\']([^"\']+)["\']', plugin_py_content, re.MULTILINE)
                manifest_version = manifest.get('version')

                if not version_match and not manifest_version:
                    raise HTTPException(
                        status_code=400,
                        detail="Plugin must define a 'version' attribute (e.g., version = \"1.0.0\"). "
                               "Use semantic versioning: MAJOR.MINOR.PATCH"
                    )

                # Validate semantic versioning format
                version_str = version_match.group(1) if version_match else manifest_version
                if version_str and not re.match(r'^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$', version_str):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid version format '{version_str}'. "
                               f"Use semantic versioning: MAJOR.MINOR.PATCH (e.g., 1.0.0, 2.1.3-beta)"
                    )

                # Copy plugin files to plugins directory
                if plugin_source_dir == extract_path:
                    # Files at root - create directory
                    target_dir.mkdir(parents=True, exist_ok=True)
                    for item in extract_path.iterdir():
                        if item.is_file():
                            shutil.copy2(item, target_dir / item.name)
                else:
                    # Copy the directory
                    shutil.copytree(plugin_source_dir, target_dir)

                logger.info(f"Installed plugin to: {target_dir}")

            # Clean up temp file
            tmp_path.unlink()

            # Reload plugins to pick up the new one
            await plugin_manager.reload_plugins()

            # Get the newly installed plugin info
            plugin = plugin_manager.get_plugin(plugin_id)
            if plugin:
                return {
                    "status": "ok",
                    "message": f"Plugin '{plugin.name}' installed successfully",
                    "plugin": plugin.to_dict()
                }
            else:
                return {
                    "status": "warning",
                    "message": f"Plugin files installed but failed to load. Check logs for errors.",
                    "plugin_id": plugin_id
                }

        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail="Invalid or corrupted ZIP file")
        except Exception as e:
            logger.error(f"Failed to install plugin: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to install plugin: {str(e)}")

    @router.delete("/plugins/{plugin_id}")
    async def uninstall_plugin(plugin_id: str):
        """
        Uninstall a plugin by removing its directory.

        This will:
        1. Disable the plugin if enabled
        2. Unload the plugin
        3. Delete the plugin directory
        4. Remove plugin settings from state
        5. If builtin, track as deleted to prevent auto-reinstall
        """
        import shutil
        from sonorium.plugins.loader import get_builtin_plugin_ids

        if not plugin_manager:
            raise HTTPException(status_code=503, detail="Plugin system not available")

        plugin = plugin_manager.get_plugin(plugin_id)
        plugin_dir = plugin_manager.plugins_dir / plugin_id

        # Check if plugin exists (either loaded or as directory)
        if not plugin and not plugin_dir.exists():
            raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")

        # Check if this is a builtin plugin
        builtin_ids = get_builtin_plugin_ids()
        is_builtin = plugin_id in builtin_ids

        try:
            # Disable and unload if loaded
            if plugin:
                if plugin.enabled:
                    await plugin_manager.disable_plugin(plugin_id)
                await plugin_manager._unload_plugin(plugin_id)
                del plugin_manager.plugins[plugin_id]

            # Remove plugin directory
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir)
                logger.info(f"Removed plugin directory: {plugin_dir}")

            # Remove plugin settings from state
            if plugin_id in plugin_manager.state_store.settings.plugin_settings:
                del plugin_manager.state_store.settings.plugin_settings[plugin_id]
            if plugin_id in plugin_manager.state_store.settings.enabled_plugins:
                plugin_manager.state_store.settings.enabled_plugins.remove(plugin_id)

            # If this was a builtin plugin, track it as deleted to prevent auto-reinstall
            if is_builtin:
                deleted_list = plugin_manager.state_store.settings.deleted_builtin_plugins
                if plugin_id not in deleted_list:
                    deleted_list.append(plugin_id)
                    logger.info(f"Marked builtin plugin '{plugin_id}' as deleted")

            plugin_manager.state_store.save()

            return {
                "status": "ok",
                "message": f"Plugin '{plugin_id}' uninstalled successfully"
            }

        except Exception as e:
            logger.error(f"Failed to uninstall plugin {plugin_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to uninstall plugin: {str(e)}")

    return router
