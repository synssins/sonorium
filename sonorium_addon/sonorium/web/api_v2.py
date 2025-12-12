"""
Sonorium REST API

Provides endpoints for the web UI to manage sessions, speaker groups,
theme cycling, and retrieve speaker hierarchy from Home Assistant.
"""

from __future__ import annotations

import asyncio
from typing import Optional
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status, BackgroundTasks
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
    speaker_group_id: Optional[str] = None
    adhoc_selection: Optional[SpeakerSelectionModel] = None
    custom_name: Optional[str] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)
    cycle_config: Optional[CycleConfigModel] = None


class UpdateSessionRequest(BaseModel):
    """Request to update an existing session."""
    theme_id: Optional[str] = None
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
    max_sessions: int
    max_groups: int
    entity_prefix: str
    show_in_sidebar: bool
    auto_create_quick_play: bool
    default_cycle_interval: int
    default_cycle_randomize: bool


class UpdateSettingsRequest(BaseModel):
    """Request to update settings."""
    default_volume: Optional[int] = Field(default=None, ge=0, le=100)
    crossfade_duration: Optional[float] = Field(default=None, ge=0.5, le=10.0)
    max_sessions: Optional[int] = Field(default=None, ge=1, le=20)
    max_groups: Optional[int] = Field(default=None, ge=1, le=50)
    entity_prefix: Optional[str] = None
    show_in_sidebar: Optional[bool] = None
    auto_create_quick_play: Optional[bool] = None
    default_cycle_interval: Optional[int] = Field(default=None, ge=1, le=1440)
    default_cycle_randomize: Optional[bool] = None


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
        session = session_manager.update(
            session_id=session_id,
            theme_id=request.theme_id,
            speaker_group_id=request.speaker_group_id,
            adhoc_selection=request.adhoc_selection.to_selection() if request.adhoc_selection else None,
            custom_name=request.custom_name,
            volume=request.volume,
            cycle_config=request.cycle_config.to_config() if request.cycle_config else None,
        )
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
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
            max_sessions=settings.max_sessions,
            max_groups=settings.max_groups,
            entity_prefix=settings.entity_prefix,
            show_in_sidebar=settings.show_in_sidebar,
            auto_create_quick_play=settings.auto_create_quick_play,
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
        if request.max_sessions is not None:
            settings.max_sessions = request.max_sessions
        if request.max_groups is not None:
            settings.max_groups = request.max_groups
        if request.entity_prefix is not None:
            settings.entity_prefix = request.entity_prefix
        if request.show_in_sidebar is not None:
            settings.show_in_sidebar = request.show_in_sidebar
        if request.auto_create_quick_play is not None:
            settings.auto_create_quick_play = request.auto_create_quick_play
        if request.default_cycle_interval is not None:
            settings.default_cycle_interval = request.default_cycle_interval
        if request.default_cycle_randomize is not None:
            settings.default_cycle_randomize = request.default_cycle_randomize
        
        state_store.save()
        
        return SettingsResponse(
            default_volume=settings.default_volume,
            crossfade_duration=settings.crossfade_duration,
            max_sessions=settings.max_sessions,
            max_groups=settings.max_groups,
            entity_prefix=settings.entity_prefix,
            show_in_sidebar=settings.show_in_sidebar,
            auto_create_quick_play=settings.auto_create_quick_play,
            default_cycle_interval=settings.default_cycle_interval,
            default_cycle_randomize=settings.default_cycle_randomize,
        )
    
    # --- Theme Endpoints (placeholder) ---
    
    @router.get("/themes")
    async def list_themes() -> list[dict]:
        """List all available themes."""
        # TODO: Integrate with existing theme scanning
        # For now, return empty list - will be connected to existing theme logic
        if theme_manager:
            return theme_manager.list_themes()
        return []
    
    return router
