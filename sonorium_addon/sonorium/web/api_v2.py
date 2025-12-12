"""Sonorium REST API - Sessions, Groups, Cycling, Speakers."""

from __future__ import annotations
import asyncio
from typing import Optional
from dataclasses import asdict
from fastapi import APIRouter, HTTPException, status, BackgroundTasks
from pydantic import BaseModel, Field
from sonorium.core.state import SpeakerSelection, CycleConfig, NameSource
from sonorium.obs import logger

class SpeakerSelectionModel(BaseModel):
    include_floors: list[str] = Field(default_factory=list)
    include_areas: list[str] = Field(default_factory=list)
    include_speakers: list[str] = Field(default_factory=list)
    exclude_areas: list[str] = Field(default_factory=list)
    exclude_speakers: list[str] = Field(default_factory=list)
    def to_selection(self) -> SpeakerSelection:
        return SpeakerSelection(**self.dict())

class CycleConfigModel(BaseModel):
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=1, le=1440)
    randomize: bool = False
    theme_ids: list[str] = Field(default_factory=list)
    def to_config(self) -> CycleConfig:
        return CycleConfig(**self.dict())

class CycleConfigResponse(BaseModel):
    enabled: bool
    interval_minutes: int
    randomize: bool
    theme_ids: list[str]

class CycleStatusResponse(BaseModel):
    enabled: bool
    interval_minutes: int
    randomize: bool
    theme_ids: list[str]
    next_change: Optional[str] = None
    seconds_until_change: Optional[int] = None
    themes_in_rotation: int = 0

class CreateSessionRequest(BaseModel):
    theme_id: Optional[str] = None
    speaker_group_id: Optional[str] = None
    adhoc_selection: Optional[SpeakerSelectionModel] = None
    custom_name: Optional[str] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)
    cycle_config: Optional[CycleConfigModel] = None

class UpdateSessionRequest(BaseModel):
    theme_id: Optional[str] = None
    speaker_group_id: Optional[str] = None
    adhoc_selection: Optional[SpeakerSelectionModel] = None
    custom_name: Optional[str] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)
    cycle_config: Optional[CycleConfigModel] = None

class UpdateCycleRequest(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = Field(default=None, ge=1, le=1440)
    randomize: Optional[bool] = None
    theme_ids: Optional[list[str]] = None

class SessionResponse(BaseModel):
    id: str
    name: str
    name_source: str
    theme_id: Optional[str]
    speaker_group_id: Optional[str]
    adhoc_selection: Optional[dict]
    volume: int
    is_playing: bool
    speakers: list[str]
    speaker_summary: str
    channel_id: Optional[int] = None
    cycle_config: CycleConfigResponse
    created_at: str
    last_played_at: Optional[str]

class CreateGroupRequest(BaseModel):
    name: str
    icon: str = "mdi:speaker-group"
    include_floors: list[str] = Field(default_factory=list)
    include_areas: list[str] = Field(default_factory=list)
    include_speakers: list[str] = Field(default_factory=list)
    exclude_areas: list[str] = Field(default_factory=list)
    exclude_speakers: list[str] = Field(default_factory=list)

class UpdateGroupRequest(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    include_floors: Optional[list[str]] = None
    include_areas: Optional[list[str]] = None
    include_speakers: Optional[list[str]] = None
    exclude_areas: Optional[list[str]] = None
    exclude_speakers: Optional[list[str]] = None

class GroupResponse(BaseModel):
    id: str
    name: str
    icon: str
    include_floors: list[str]
    include_areas: list[str]
    include_speakers: list[str]
    exclude_areas: list[str]
    exclude_speakers: list[str]
    speakers: list[str]
    speaker_count: int
    summary: str
    created_at: str
    updated_at: str

class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)

class SettingsResponse(BaseModel):
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
    id: int
    name: str
    state: str
    current_theme: Optional[str]
    current_theme_name: Optional[str]
    client_count: int
    stream_path: str

def _session_to_response(session, session_manager) -> SessionResponse:
    cycle_config = session.cycle_config or CycleConfig()
    return SessionResponse(
        id=session.id, name=session.name, name_source=session.name_source.value,
        theme_id=session.theme_id, speaker_group_id=session.speaker_group_id,
        adhoc_selection=asdict(session.adhoc_selection) if session.adhoc_selection else None,
        volume=session.volume, is_playing=session.is_playing,
        speakers=session_manager.get_resolved_speakers(session),
        speaker_summary=session_manager.get_speaker_summary(session),
        channel_id=session_manager.get_session_channel(session.id),
        cycle_config=CycleConfigResponse(enabled=cycle_config.enabled, interval_minutes=cycle_config.interval_minutes, randomize=cycle_config.randomize, theme_ids=cycle_config.theme_ids),
        created_at=session.created_at, last_played_at=session.last_played_at)

def create_api_router(session_manager, group_manager, ha_registry, state_store, theme_manager=None, channel_manager=None, cycle_manager=None) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["api"])
    
    @router.get("/sessions")
    async def list_sessions() -> list[SessionResponse]:
        return [_session_to_response(s, session_manager) for s in session_manager.list()]
    
    @router.post("/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(request: CreateSessionRequest) -> SessionResponse:
        try:
            session = session_manager.create(theme_id=request.theme_id, speaker_group_id=request.speaker_group_id, adhoc_selection=request.adhoc_selection.to_selection() if request.adhoc_selection else None, custom_name=request.custom_name, volume=request.volume, cycle_config=request.cycle_config.to_config() if request.cycle_config else None)
            return _session_to_response(session, session_manager)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    
    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str) -> SessionResponse:
        session = session_manager.get(session_id)
        if not session: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return _session_to_response(session, session_manager)
    
    @router.put("/sessions/{session_id}")
    async def update_session(session_id: str, request: UpdateSessionRequest) -> SessionResponse:
        session = session_manager.update(session_id=session_id, theme_id=request.theme_id, speaker_group_id=request.speaker_group_id, adhoc_selection=request.adhoc_selection.to_selection() if request.adhoc_selection else None, custom_name=request.custom_name, volume=request.volume, cycle_config=request.cycle_config.to_config() if request.cycle_config else None)
        if not session: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return _session_to_response(session, session_manager)
    
    @router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(session_id: str):
        if not session_manager.delete(session_id): raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    
    @router.post("/sessions/{session_id}/play")
    async def play_session(session_id: str) -> dict:
        session = session_manager.get(session_id)
        if not session: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if not session.theme_id: raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No theme selected")
        if not session_manager.get_resolved_speakers(session): raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No speakers selected")
        session.is_playing = True
        session.mark_played()
        state_store.save()
        asyncio.create_task(session_manager.play(session_id))
        return {"status": "playing", "channel_id": session_manager.get_session_channel(session_id), "cycling": session.cycle_config.enabled if session.cycle_config else False}
    
    @router.post("/sessions/{session_id}/pause")
    async def pause_session(session_id: str) -> dict:
        if not await session_manager.pause(session_id): raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return {"status": "paused"}
    
    @router.post("/sessions/{session_id}/stop")
    async def stop_session(session_id: str) -> dict:
        if not await session_manager.stop(session_id): raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return {"status": "stopped"}
    
    @router.post("/sessions/{session_id}/volume")
    async def set_session_volume(session_id: str, request: VolumeRequest) -> dict:
        if not await session_manager.set_volume(session_id, request.volume): raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return {"volume": request.volume}
    
    @router.post("/sessions/stop-all")
    async def stop_all_sessions() -> dict:
        return {"stopped": await session_manager.stop_all()}
    
    @router.get("/sessions/{session_id}/cycle")
    async def get_cycle_status(session_id: str) -> CycleStatusResponse:
        session = session_manager.get(session_id)
        if not session: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        cycle_config = session.cycle_config or CycleConfig()
        status_data = cycle_manager.get_cycle_status(session_id) if cycle_manager and session.is_playing else None
        return CycleStatusResponse(enabled=cycle_config.enabled, interval_minutes=cycle_config.interval_minutes, randomize=cycle_config.randomize, theme_ids=cycle_config.theme_ids, next_change=status_data.get("next_change") if status_data else None, seconds_until_change=status_data.get("seconds_until_change") if status_data else None, themes_in_rotation=status_data.get("themes_in_rotation", 0) if status_data else 0)
    
    @router.put("/sessions/{session_id}/cycle")
    async def update_cycle_config(session_id: str, request: UpdateCycleRequest) -> CycleStatusResponse:
        session = session_manager.update_cycle_config(session_id=session_id, enabled=request.enabled, interval_minutes=request.interval_minutes, randomize=request.randomize, theme_ids=request.theme_ids)
        if not session: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        cycle_config = session.cycle_config
        status_data = cycle_manager.get_cycle_status(session_id) if cycle_manager and session.is_playing else None
        return CycleStatusResponse(enabled=cycle_config.enabled, interval_minutes=cycle_config.interval_minutes, randomize=cycle_config.randomize, theme_ids=cycle_config.theme_ids, next_change=status_data.get("next_change") if status_data else None, seconds_until_change=status_data.get("seconds_until_change") if status_data else None, themes_in_rotation=status_data.get("themes_in_rotation", 0) if status_data else 0)
    
    @router.post("/sessions/{session_id}/cycle/skip")
    async def skip_to_next_theme(session_id: str) -> dict:
        session = session_manager.get(session_id)
        if not session: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if not session.is_playing: raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session is not playing")
        if not cycle_manager: raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Cycling not available")
        await cycle_manager._cycle_theme(session)
        return {"status": "skipped", "new_theme_id": session.theme_id}
    
    @router.get("/channels")
    async def list_channels() -> list[ChannelResponse]:
        if not channel_manager: return []
        return [ChannelResponse(**ch) for ch in channel_manager.list_channels()]
    
    @router.get("/groups")
    async def list_groups() -> list[GroupResponse]:
        return [GroupResponse(id=g.id, name=g.name, icon=g.icon, include_floors=g.include_floors, include_areas=g.include_areas, include_speakers=g.include_speakers, exclude_areas=g.exclude_areas, exclude_speakers=g.exclude_speakers, speakers=group_manager.resolve(g), speaker_count=group_manager.get_speaker_count(g), summary=group_manager.get_summary(g), created_at=g.created_at, updated_at=g.updated_at) for g in group_manager.list()]
    
    @router.post("/groups", status_code=status.HTTP_201_CREATED)
    async def create_group(request: CreateGroupRequest) -> GroupResponse:
        try:
            g = group_manager.create(name=request.name, icon=request.icon, include_floors=request.include_floors, include_areas=request.include_areas, include_speakers=request.include_speakers, exclude_areas=request.exclude_areas, exclude_speakers=request.exclude_speakers)
            return GroupResponse(id=g.id, name=g.name, icon=g.icon, include_floors=g.include_floors, include_areas=g.include_areas, include_speakers=g.include_speakers, exclude_areas=g.exclude_areas, exclude_speakers=g.exclude_speakers, speakers=group_manager.resolve(g), speaker_count=group_manager.get_speaker_count(g), summary=group_manager.get_summary(g), created_at=g.created_at, updated_at=g.updated_at)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    
    @router.get("/groups/{group_id}")
    async def get_group(group_id: str) -> GroupResponse:
        g = group_manager.get(group_id)
        if not g: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        return GroupResponse(id=g.id, name=g.name, icon=g.icon, include_floors=g.include_floors, include_areas=g.include_areas, include_speakers=g.include_speakers, exclude_areas=g.exclude_areas, exclude_speakers=g.exclude_speakers, speakers=group_manager.resolve(g), speaker_count=group_manager.get_speaker_count(g), summary=group_manager.get_summary(g), created_at=g.created_at, updated_at=g.updated_at)
    
    @router.put("/groups/{group_id}")
    async def update_group(group_id: str, request: UpdateGroupRequest) -> GroupResponse:
        try:
            g = group_manager.update(group_id=group_id, name=request.name, icon=request.icon, include_floors=request.include_floors, include_areas=request.include_areas, include_speakers=request.include_speakers, exclude_areas=request.exclude_areas, exclude_speakers=request.exclude_speakers)
            if not g: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
            return GroupResponse(id=g.id, name=g.name, icon=g.icon, include_floors=g.include_floors, include_areas=g.include_areas, include_speakers=g.include_speakers, exclude_areas=g.exclude_areas, exclude_speakers=g.exclude_speakers, speakers=group_manager.resolve(g), speaker_count=group_manager.get_speaker_count(g), summary=group_manager.get_summary(g), created_at=g.created_at, updated_at=g.updated_at)
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    
    @router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_group(group_id: str):
        session_ids = group_manager.get_sessions_using_group(group_id)
        if session_ids: raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Group is used by {len(session_ids)} session(s)")
        if not group_manager.delete(group_id): raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
    
    @router.get("/groups/{group_id}/resolve")
    async def resolve_group(group_id: str) -> dict:
        g = group_manager.get(group_id)
        if not g: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Group not found")
        speakers = group_manager.resolve(g)
        return {"speakers": speakers, "count": len(speakers), "summary": group_manager.get_summary(g)}
    
    @router.get("/speakers")
    async def list_speakers() -> list[dict]:
        return [s.to_dict() for s in ha_registry.hierarchy.get_all_speakers()]
    
    @router.get("/speakers/hierarchy")
    async def get_speaker_hierarchy() -> dict:
        return ha_registry.hierarchy.to_dict()
    
    @router.post("/speakers/refresh")
    async def refresh_speakers() -> dict:
        h = ha_registry.refresh()
        return {"floors": len(h.floors), "unassigned_areas": len(h.unassigned_areas), "unassigned_speakers": len(h.unassigned_speakers), "total_speakers": len(h.get_all_speakers())}
    
    @router.post("/speakers/resolve")
    async def resolve_selection(request: SpeakerSelectionModel) -> dict:
        speakers = ha_registry.resolve_selection(**request.dict())
        return {"speakers": speakers, "count": len(speakers)}
    
    @router.get("/settings")
    async def get_settings() -> SettingsResponse:
        s = state_store.settings
        return SettingsResponse(default_volume=s.default_volume, crossfade_duration=s.crossfade_duration, max_sessions=s.max_sessions, max_groups=s.max_groups, entity_prefix=s.entity_prefix, show_in_sidebar=s.show_in_sidebar, auto_create_quick_play=s.auto_create_quick_play, default_cycle_interval=s.default_cycle_interval, default_cycle_randomize=s.default_cycle_randomize)
    
    @router.put("/settings")
    async def update_settings(request: UpdateSettingsRequest) -> SettingsResponse:
        s = state_store.settings
        if request.default_volume is not None: s.default_volume = request.default_volume
        if request.crossfade_duration is not None: s.crossfade_duration = request.crossfade_duration
        if request.max_sessions is not None: s.max_sessions = request.max_sessions
        if request.max_groups is not None: s.max_groups = request.max_groups
        if request.entity_prefix is not None: s.entity_prefix = request.entity_prefix
        if request.show_in_sidebar is not None: s.show_in_sidebar = request.show_in_sidebar
        if request.auto_create_quick_play is not None: s.auto_create_quick_play = request.auto_create_quick_play
        if request.default_cycle_interval is not None: s.default_cycle_interval = request.default_cycle_interval
        if request.default_cycle_randomize is not None: s.default_cycle_randomize = request.default_cycle_randomize
        state_store.save()
        return SettingsResponse(default_volume=s.default_volume, crossfade_duration=s.crossfade_duration, max_sessions=s.max_sessions, max_groups=s.max_groups, entity_prefix=s.entity_prefix, show_in_sidebar=s.show_in_sidebar, auto_create_quick_play=s.auto_create_quick_play, default_cycle_interval=s.default_cycle_interval, default_cycle_randomize=s.default_cycle_randomize)
    
    @router.get("/themes")
    async def list_themes() -> list[dict]:
        return theme_manager.list_themes() if theme_manager else []
    
    return router