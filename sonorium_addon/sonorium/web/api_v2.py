"""
Sonorium REST API

Provides endpoints for the web UI to manage sessions, speaker groups,
theme cycling, and retrieve speaker hierarchy from Home Assistant.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status, BackgroundTasks, Request, UploadFile, File
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
    enabled_speakers: list[str]  # Empty = all enabled
    hierarchy: Optional[dict] = None  # Full speaker hierarchy


class UpdateSpeakerSettingsRequest(BaseModel):
    """Request to update enabled speakers."""
    enabled_speakers: list[str]


class SingleSpeakerRequest(BaseModel):
    """Request to enable/disable a single speaker."""
    entity_id: str


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
            "token_preview": ha_registry.token[:20] + "..." if ha_registry.token else None,
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
            return _session_to_response(session, session_manager)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    
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

        # Refresh MQTT discovery if session name changed (Issue #16)
        if mqtt_manager and old_name and session.name != old_name:
            try:
                await mqtt_manager.refresh_session_discovery(session)
            except Exception as e:
                logger.warning(f"Failed to refresh MQTT discovery for renamed session: {e}")

        return _session_to_response(session, session_manager)
    
    @router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(session_id: str):
        """Delete a session."""
        if not session_manager.delete(session_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    
    @router.post("/sessions/{session_id}/play")
    async def play_session(session_id: str) -> dict:
        """Start playback for a session (fire-and-forget, returns immediately)."""
        session = session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        
        if not session.theme_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No theme selected")
        
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
                detail=f"Group is used by {len(session_ids)} session(s). Delete or update those sessions first."
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
        """Refresh speaker hierarchy from Home Assistant."""
        hierarchy = ha_registry.refresh()
        return {
            "floors": len(hierarchy.floors),
            "unassigned_areas": len(hierarchy.unassigned_areas),
            "unassigned_speakers": len(hierarchy.unassigned_speakers),
            "total_speakers": len(hierarchy.get_all_speakers()),
        }
    
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
        settings = state_store.settings
        hierarchy = None
        if ha_registry:
            hierarchy = ha_registry.get_hierarchy_dict()
        return SpeakerSettingsResponse(
            enabled_speakers=settings.enabled_speakers,
            hierarchy=hierarchy,
        )

    @router.put("/settings/speakers")
    async def update_speaker_settings(request: UpdateSpeakerSettingsRequest) -> SpeakerSettingsResponse:
        """Update enabled speakers list."""
        settings = state_store.settings
        settings.enabled_speakers = request.enabled_speakers
        state_store.save()

        hierarchy = None
        if ha_registry:
            hierarchy = ha_registry.get_hierarchy_dict()
        return SpeakerSettingsResponse(
            enabled_speakers=settings.enabled_speakers,
            hierarchy=hierarchy,
        )

    @router.post("/settings/speakers/enable")
    async def enable_speaker(request: SingleSpeakerRequest) -> SpeakerSettingsResponse:
        """Enable a single speaker."""
        settings = state_store.settings
        entity_id = request.entity_id

        # If enabled_speakers is empty, all are enabled - nothing to do
        if not settings.enabled_speakers:
            # Need to switch to explicit mode: add all speakers except this one... wait no
            # Actually if empty = all enabled, then enabling one speaker doesn't change anything
            pass
        else:
            # Add to enabled list if not already there
            if entity_id not in settings.enabled_speakers:
                settings.enabled_speakers.append(entity_id)
                state_store.save()

        hierarchy = None
        if ha_registry:
            hierarchy = ha_registry.get_hierarchy_dict()
        return SpeakerSettingsResponse(
            enabled_speakers=settings.enabled_speakers,
            hierarchy=hierarchy,
        )

    @router.post("/settings/speakers/disable")
    async def disable_speaker(request: SingleSpeakerRequest) -> SpeakerSettingsResponse:
        """Disable a single speaker."""
        settings = state_store.settings
        entity_id = request.entity_id

        # If enabled_speakers is empty, all are enabled - need to switch to explicit mode
        if not settings.enabled_speakers:
            # Get all speakers and add all except the one being disabled
            if ha_registry:
                all_speakers = ha_registry.get_all_speaker_ids()
                settings.enabled_speakers = [s for s in all_speakers if s != entity_id]
            else:
                # Can't disable without knowing all speakers
                raise HTTPException(status_code=400, detail="Cannot disable speaker: speaker list not available")
        else:
            # Remove from enabled list
            if entity_id in settings.enabled_speakers:
                settings.enabled_speakers.remove(entity_id)

        state_store.save()

        hierarchy = None
        if ha_registry:
            hierarchy = ha_registry.get_hierarchy_dict()
        return SpeakerSettingsResponse(
            enabled_speakers=settings.enabled_speakers,
            hierarchy=hierarchy,
        )

    @router.post("/settings/speakers/enable-all")
    async def enable_all_speakers() -> SpeakerSettingsResponse:
        """Enable all speakers (clear the enabled list)."""
        settings = state_store.settings
        settings.enabled_speakers = []  # Empty = all enabled
        state_store.save()

        hierarchy = None
        if ha_registry:
            hierarchy = ha_registry.get_hierarchy_dict()
        return SpeakerSettingsResponse(
            enabled_speakers=settings.enabled_speakers,
            hierarchy=hierarchy,
        )

    @router.post("/settings/speakers/disable-all")
    async def disable_all_speakers() -> SpeakerSettingsResponse:
        """Disable all speakers (set to special sentinel value)."""
        settings = state_store.settings
        settings.enabled_speakers = ["__none__"]  # Special value = no speakers enabled
        state_store.save()

        hierarchy = None
        if ha_registry:
            hierarchy = ha_registry.get_hierarchy_dict()
        return SpeakerSettingsResponse(
            enabled_speakers=settings.enabled_speakers,
            hierarchy=hierarchy,
        )

    # --- Custom Speaker Areas (fallback when HA areas unavailable) ---

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
        """Upload an audio file to a theme folder."""
        theme_path = _find_theme_folder(theme_id)
        if not theme_path:
            raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")

        # Parse multipart form data
        try:
            form = await request.form()
            file = form.get("file")
            if not file:
                raise HTTPException(status_code=400, detail="No file provided")

            # Validate file extension
            valid_extensions = ['.mp3', '.wav', '.flac', '.ogg']
            filename = file.filename
            ext = '.' + filename.split('.')[-1].lower() if '.' in filename else ''

            if ext not in valid_extensions:
                raise HTTPException(status_code=400, detail=f"Invalid file type. Supported: {', '.join(valid_extensions)}")

            # Save the file
            file_path = theme_path / filename

            # Read and write the file content
            content = await file.read()
            file_path.write_bytes(content)

            logger.info(f"Uploaded file to theme '{theme_id}': {filename} ({len(content)} bytes)")

            return {
                "status": "ok",
                "filename": filename,
                "size": len(content),
                "theme_id": theme_id
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

        # Read existing metadata and merge
        metadata_path = theme_path / "metadata.json"
        metadata = {}
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text())
            except Exception:
                pass

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

        # Write back
        try:
            metadata_path.write_text(json.dumps(metadata, indent=2))
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

            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
                # Walk through all files in the theme folder
                for file_path in theme_path.rglob('*'):
                    if file_path.is_file():
                        # Use relative path within the theme folder
                        arcname = f"{theme_name}/{file_path.relative_to(theme_path)}"
                        zip_file.write(file_path, arcname)
                        logger.debug(f"Added to zip: {arcname}")

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

                # Generate new UUID for imported theme if metadata.json exists
                metadata_path = target_path / "metadata.json"
                if metadata_path.exists():
                    try:
                        metadata = json.loads(metadata_path.read_text())
                        # Generate new UUID to avoid conflicts
                        import uuid
                        metadata["id"] = str(uuid.uuid4())
                        metadata_path.write_text(json.dumps(metadata, indent=2))
                    except Exception as e:
                        logger.warning(f"Could not update metadata UUID: {e}")
                else:
                    # Create basic metadata
                    import uuid
                    metadata = {
                        "id": str(uuid.uuid4()),
                        "name": theme_folder_name
                    }
                    metadata_path.write_text(json.dumps(metadata, indent=2))

                logger.info(f"Imported theme '{theme_folder_name}' with {files_extracted} files")

                return {
                    "status": "ok",
                    "theme_folder": theme_folder_name,
                    "files_extracted": files_extracted,
                    "path": str(target_path)
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
        """
        import shutil

        if not plugin_manager:
            raise HTTPException(status_code=503, detail="Plugin system not available")

        plugin = plugin_manager.get_plugin(plugin_id)
        plugin_dir = plugin_manager.plugins_dir / plugin_id

        # Check if plugin exists (either loaded or as directory)
        if not plugin and not plugin_dir.exists():
            raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")

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
            plugin_manager.state_store.save()

            return {
                "status": "ok",
                "message": f"Plugin '{plugin_id}' uninstalled successfully"
            }

        except Exception as e:
            logger.error(f"Failed to uninstall plugin {plugin_id}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to uninstall plugin: {str(e)}")

    return router
