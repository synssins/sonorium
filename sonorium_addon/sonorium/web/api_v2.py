"""
Sonorium REST API

Provides endpoints for the web UI to manage sessions, speaker groups,
and retrieve speaker hierarchy from Home Assistant.
"""

from __future__ import annotations

from typing import Optional
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from sonorium.core.state import SpeakerSelection, NameSource
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


class CreateSessionRequest(BaseModel):
    """Request to create a new session."""
    theme_id: Optional[str] = None
    speaker_group_id: Optional[str] = None
    adhoc_selection: Optional[SpeakerSelectionModel] = None
    custom_name: Optional[str] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)


class UpdateSessionRequest(BaseModel):
    """Request to update an existing session."""
    theme_id: Optional[str] = None
    speaker_group_id: Optional[str] = None
    adhoc_selection: Optional[SpeakerSelectionModel] = None
    custom_name: Optional[str] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)


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


class UpdateSettingsRequest(BaseModel):
    """Request to update settings."""
    default_volume: Optional[int] = Field(default=None, ge=0, le=100)
    crossfade_duration: Optional[float] = Field(default=None, ge=0.5, le=10.0)
    max_sessions: Optional[int] = Field(default=None, ge=1, le=20)
    max_groups: Optional[int] = Field(default=None, ge=1, le=50)
    entity_prefix: Optional[str] = None
    show_in_sidebar: Optional[bool] = None
    auto_create_quick_play: Optional[bool] = None


# --- API Router Factory ---

def create_api_router(session_manager, group_manager, ha_registry, state_store, theme_manager=None) -> APIRouter:
    """
    Create the API router with all endpoints.
    
    Args:
        session_manager: SessionManager instance
        group_manager: GroupManager instance
        ha_registry: HARegistry instance
        state_store: StateStore instance
        theme_manager: Optional theme manager for theme endpoints
    
    Returns:
        Configured APIRouter
    """
    router = APIRouter(prefix="/api", tags=["api"])
    
    # --- Session Endpoints ---
    
    @router.get("/sessions")
    async def list_sessions() -> list[SessionResponse]:
        """List all sessions."""
        sessions = session_manager.list()
        return [
            SessionResponse(
                id=s.id,
                name=s.name,
                name_source=s.name_source.value,
                theme_id=s.theme_id,
                speaker_group_id=s.speaker_group_id,
                adhoc_selection=asdict(s.adhoc_selection) if s.adhoc_selection else None,
                volume=s.volume,
                is_playing=s.is_playing,
                speakers=session_manager.get_resolved_speakers(s),
                speaker_summary=session_manager.get_speaker_summary(s),
                created_at=s.created_at,
                last_played_at=s.last_played_at,
            )
            for s in sessions
        ]
    
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
            )
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
                created_at=session.created_at,
                last_played_at=session.last_played_at,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    
    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> SessionResponse:
        """Get a session by ID."""
        session = session_manager.get(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
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
            created_at=session.created_at,
            last_played_at=session.last_played_at,
        )
    
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
        )
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
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
            created_at=session.created_at,
            last_played_at=session.last_played_at,
        )
    
    @router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(session_id: str):
        """Delete a session."""
        if not session_manager.delete(session_id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    
    @router.post("/sessions/{session_id}/play")
    async def play_session(session_id: str) -> dict:
        """Start playback for a session."""
        success = await session_manager.play(session_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Could not start playback")
        return {"status": "playing"}
    
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
            "speakers": len(hierarchy.get_all_speakers()),
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
        
        state_store.save()
        
        return SettingsResponse(
            default_volume=settings.default_volume,
            crossfade_duration=settings.crossfade_duration,
            max_sessions=settings.max_sessions,
            max_groups=settings.max_groups,
            entity_prefix=settings.entity_prefix,
            show_in_sidebar=settings.show_in_sidebar,
            auto_create_quick_play=settings.auto_create_quick_play,
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
