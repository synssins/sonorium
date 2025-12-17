"""
Standalone Web API for Sonorium.

Full API implementation compatible with the Sonorium web UI,
adapted for standalone desktop use with local audio output.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional
from dataclasses import dataclass, field, asdict

from fastapi import FastAPI, HTTPException, UploadFile, File, Request
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sonorium.obs import logger

if TYPE_CHECKING:
    from sonorium.app_device import SonoriumApp
    from sonorium.plugins.manager import PluginManager
from sonorium.config import get_config, save_config


# --- Data Models ---

@dataclass
class Session:
    """A playback session."""
    id: str
    name: str
    theme_id: Optional[str] = None
    preset_id: Optional[str] = None
    volume: int = 80
    is_playing: bool = False
    device_id: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    last_played_at: Optional[str] = None
    # Speaker selection - list of speaker IDs
    # 'local' for local audio, 'network_speaker.{id}' for network speakers
    speakers: list = field(default_factory=lambda: ['local'])
    use_local_speaker: bool = True  # Whether to play through local audio output


# --- Request Models ---

class AdhocSelection(BaseModel):
    """Speaker selection from the UI tree."""
    include_floors: list = []
    include_areas: list = []
    include_speakers: list = []  # e.g. ['audio_device.1', 'network_speaker.abc']
    exclude_areas: list = []
    exclude_speakers: list = []


class CreateSessionRequest(BaseModel):
    theme_id: Optional[str] = None
    preset_id: Optional[str] = None
    custom_name: Optional[str] = None
    volume: Optional[int] = Field(default=80, ge=0, le=100)
    speakers: Optional[list] = None  # List of speaker IDs (legacy)
    adhoc_selection: Optional[AdhocSelection] = None  # From UI speaker tree


class UpdateSessionRequest(BaseModel):
    theme_id: Optional[str] = None
    preset_id: Optional[str] = None
    custom_name: Optional[str] = None
    volume: Optional[int] = Field(default=None, ge=0, le=100)
    speakers: Optional[list] = None  # List of speaker IDs (legacy)
    adhoc_selection: Optional[AdhocSelection] = None  # From UI speaker tree


class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)


class TrackSettingRequest(BaseModel):
    value: float | bool | str = None
    # Allow named fields for backwards compatibility with web UI
    presence: float = None
    volume: float = None
    playback_mode: str = None
    seamless_loop: bool = None
    exclusive: bool = None
    muted: bool = None

    def get_value(self, field_name: str):
        """Get the value from either named field or generic value."""
        named = getattr(self, field_name, None)
        if named is not None:
            return named
        return self.value


class PlayRequest(BaseModel):
    theme: str | None = None


class SettingsUpdate(BaseModel):
    audio_path: str | None = None
    auto_play_on_start: bool | None = None
    default_volume: int | None = Field(default=None, ge=0, le=100)
    master_gain: int | None = Field(default=None, ge=0, le=100)


class DeviceSelectRequest(BaseModel):
    device_id: int


class ThemeMetadataUpdate(BaseModel):
    description: str | None = None
    icon: str | None = None


class ThemeCategoriesUpdate(BaseModel):
    categories: list[str] = []


class PresetCreate(BaseModel):
    name: str
    description: str | None = ''
    track_settings: dict | None = None


class PresetRename(BaseModel):
    name: str


class PresetImport(BaseModel):
    preset_json: str
    name: str | None = None


class CategoryCreate(BaseModel):
    name: str


class PluginActionRequest(BaseModel):
    action: str
    data: dict = {}


class PluginSettingsUpdate(BaseModel):
    settings: dict


# --- Global State ---

_app_instance: 'SonoriumApp | None' = None
_sessions: dict[str, Session] = {}
_plugin_manager: 'PluginManager | None' = None

# Browser connection tracking
_last_heartbeat: float = 0.0
_heartbeat_timeout: float = 10.0  # Stop playback if no heartbeat for 10 seconds
_heartbeat_check_thread = None
_stop_on_disconnect: bool = False  # Disabled by default - systray provides stop control


def _has_local_speaker(speakers: list) -> bool:
    """Check if speakers list includes local audio (handles 'local' and 'local_audio' formats)."""
    return 'local' in speakers or 'local_audio' in speakers


def _is_local_speaker_ref(speaker_ref: str) -> bool:
    """Check if a speaker reference is a local speaker (not network)."""
    return speaker_ref in ('local', 'local_audio')


def _load_sessions_from_config():
    """Load saved sessions from config file."""
    global _sessions
    try:
        config = get_config()
        for session_data in config.sessions:
            if isinstance(session_data, dict):
                # Parse speakers list, defaulting to ['local'] for backward compat
                speakers = session_data.get('speakers', ['local'])
                use_local = _has_local_speaker(speakers)

                session = Session(
                    id=session_data.get('id', str(uuid.uuid4())[:8]),
                    name=session_data.get('name', 'Session'),
                    theme_id=session_data.get('theme_id', ''),
                    preset_id=session_data.get('preset_id', ''),
                    volume=session_data.get('volume', 80),
                    created_at=session_data.get('created_at', datetime.now().isoformat()),
                    speakers=speakers,
                    use_local_speaker=use_local
                )
                _sessions[session.id] = session
        logger.info(f'Loaded {len(_sessions)} sessions from config')
    except Exception as e:
        logger.error(f'Failed to load sessions from config: {e}')


def _save_sessions_to_config():
    """Save sessions to config file."""
    try:
        config = get_config()
        config.sessions = [
            {
                'id': s.id,
                'name': s.name,
                'theme_id': s.theme_id,
                'preset_id': s.preset_id,
                'volume': s.volume,
                'created_at': s.created_at,
                'speakers': s.speakers
            }
            for s in _sessions.values()
        ]
        config.save()
        logger.info(f'Saved {len(_sessions)} sessions to config')
    except PermissionError as e:
        logger.error(f'Permission denied saving sessions: {e}. Check that the config directory is writable.')
    except Exception as e:
        logger.error(f'Failed to save sessions to config: {e}')


def set_plugin_manager(plugin_manager: 'PluginManager'):
    """Set the global plugin manager instance."""
    global _plugin_manager
    _plugin_manager = plugin_manager

    # Configure speaker plugins with stream URL provider
    _configure_speaker_plugins()


def _configure_speaker_plugins():
    """Configure all speaker plugins with the stream URL callback."""
    from sonorium.plugins.speaker_base import SpeakerPlugin

    if not _plugin_manager:
        return

    def get_stream_url(theme_id: str) -> str:
        """Generate stream URL for a theme using detected local IP."""
        from sonorium.config import get_config, get_stream_base_url
        config = get_config()
        port = config.server_port if hasattr(config, 'server_port') else 8008
        base_url = get_stream_base_url(port)
        return f'{base_url}/stream/{theme_id}'

    for plugin in _plugin_manager.list_plugins():
        if isinstance(plugin, SpeakerPlugin):
            plugin.set_stream_url_provider(get_stream_url)


def get_plugin_manager() -> 'PluginManager | None':
    """Get the global plugin manager instance."""
    return _plugin_manager


def _get_version() -> str:
    """Get version from version file."""
    try:
        import sys
        if getattr(sys, 'frozen', False):
            version_path = Path(sys._MEIPASS) / 'sonorium' / 'version'
        else:
            version_path = Path(__file__).parent / 'version'
        if version_path.exists():
            return version_path.read_text().strip()
    except Exception:
        pass
    return '1.0.0'


def _convert_adhoc_to_speakers(adhoc: 'AdhocSelection') -> list:
    """
    Convert adhoc_selection from UI to speakers list.

    The UI sends entity_ids like:
    - 'audio_device.1' for local audio devices
    - 'network_speaker.abc123' for network speakers

    We convert to:
    - 'local' if any audio_device is included
    - 'network_speaker.{id}' for network speakers
    """
    speakers = []

    # Check if any local audio device is selected
    has_local = False
    for entity_id in adhoc.include_speakers:
        if entity_id.startswith('audio_device.'):
            has_local = True
        elif entity_id.startswith('network_speaker.'):
            # Keep network speaker IDs as-is
            speakers.append(entity_id)

    # Check areas - 'local_audio' area means local speaker
    if 'local_audio' in adhoc.include_areas:
        has_local = True

    # Add 'local' once if any local device is selected
    if has_local:
        speakers.insert(0, 'local')

    return speakers if speakers else ['local']  # Default to local if nothing selected


async def _start_session_speakers(session: 'Session'):
    """Start streaming to all network speakers in a session."""
    from sonorium.streaming import get_streaming_manager
    from sonorium.network_speakers import get_speaker, SpeakerStatus

    if not session.theme_id:
        return

    manager = get_streaming_manager()

    for speaker_ref in session.speakers:
        # Skip local speaker (handles both 'local' and 'local_audio')
        if _is_local_speaker_ref(speaker_ref):
            continue

        # Extract speaker ID from 'network_speaker.{id}' format
        if speaker_ref.startswith('network_speaker.'):
            speaker_id = speaker_ref.replace('network_speaker.', '')
        else:
            speaker_id = speaker_ref

        speaker = get_speaker(speaker_id)
        if not speaker:
            logger.warning(f'Speaker {speaker_id} not found, skipping')
            continue

        if speaker.status == SpeakerStatus.UNAVAILABLE:
            logger.warning(f'Speaker {speaker.name} is unavailable, skipping')
            continue

        # Start streaming to this speaker
        logger.info(f'Starting stream to network speaker: {speaker.name}')
        try:
            success = await manager.start_streaming(
                speaker_id=speaker_id,
                speaker_type=speaker.speaker_type.value,
                speaker_info=speaker.to_dict(),
                theme_id=session.theme_id
            )
            if success:
                logger.info(f'Streaming started to {speaker.name}')
            else:
                logger.error(f'Failed to start streaming to {speaker.name}')
        except Exception as e:
            logger.error(f'Error starting stream to {speaker.name}: {e}')


async def _stop_session_speakers(session: 'Session'):
    """Stop streaming to all network speakers in a session."""
    from sonorium.streaming import get_streaming_manager

    manager = get_streaming_manager()

    for speaker_ref in session.speakers:
        # Skip local speaker (handles both 'local' and 'local_audio')
        if _is_local_speaker_ref(speaker_ref):
            continue

        # Extract speaker ID
        if speaker_ref.startswith('network_speaker.'):
            speaker_id = speaker_ref.replace('network_speaker.', '')
        else:
            speaker_id = speaker_ref

        try:
            await manager.stop_streaming(speaker_id)
            logger.info(f'Stopped streaming to speaker: {speaker_id}')
        except Exception as e:
            logger.error(f'Error stopping stream to {speaker_id}: {e}')


async def _update_session_speakers(session: 'Session'):
    """Update speakers for a playing session (add new, remove old)."""
    from sonorium.streaming import get_streaming_manager

    manager = get_streaming_manager()

    # Get currently streaming speakers
    active_sessions = manager.get_active_sessions()
    active_speaker_ids = {s.speaker_id for s in active_sessions}

    # Get target speakers (excluding local)
    target_speaker_ids = set()
    for speaker_ref in session.speakers:
        # Skip local speaker (handles both 'local' and 'local_audio')
        if _is_local_speaker_ref(speaker_ref):
            continue
        if speaker_ref.startswith('network_speaker.'):
            target_speaker_ids.add(speaker_ref.replace('network_speaker.', ''))
        else:
            target_speaker_ids.add(speaker_ref)

    # Stop removed speakers
    for speaker_id in active_speaker_ids - target_speaker_ids:
        try:
            await manager.stop_streaming(speaker_id)
            logger.info(f'Stopped streaming to removed speaker: {speaker_id}')
        except Exception as e:
            logger.error(f'Error stopping stream to {speaker_id}: {e}')

    # Start new speakers
    from sonorium.network_speakers import get_speaker, SpeakerStatus

    for speaker_id in target_speaker_ids - active_speaker_ids:
        speaker = get_speaker(speaker_id)
        if not speaker:
            logger.warning(f'Speaker {speaker_id} not found')
            continue

        if speaker.status == SpeakerStatus.UNAVAILABLE:
            logger.warning(f'Speaker {speaker.name} is unavailable')
            continue

        try:
            success = await manager.start_streaming(
                speaker_id=speaker_id,
                speaker_type=speaker.speaker_type.value,
                speaker_info=speaker.to_dict(),
                theme_id=session.theme_id
            )
            if success:
                logger.info(f'Started streaming to new speaker: {speaker.name}')
        except Exception as e:
            logger.error(f'Error starting stream to {speaker.name}: {e}')

    # Handle local speaker toggle
    if session.use_local_speaker and _app_instance.playback_state != 'playing':
        _app_instance.play(session.theme_id, preset_id=session.preset_id)
        _app_instance.set_volume(session.volume / 100.0)
    elif not session.use_local_speaker and _app_instance.playback_state == 'playing':
        _app_instance.stop()


def _session_to_dict(session: Session) -> dict:
    """Convert session to API response dict."""
    theme_name = None
    if session.theme_id and _app_instance:
        theme = _app_instance.get_theme(session.theme_id)
        if theme:
            theme_name = theme.name

    # Build speaker summary
    speaker_names = []
    if session.use_local_speaker:
        speaker_names.append('Local Audio')
    # Count network speakers
    network_count = sum(1 for s in session.speakers if s.startswith('network_speaker.'))
    if network_count > 0:
        speaker_names.append(f'{network_count} Network Speaker{"s" if network_count > 1 else ""}')

    speaker_summary = ', '.join(speaker_names) if speaker_names else 'No speakers selected'

    # Convert speakers list back to adhoc_selection format for UI
    # The UI expects entity_ids like 'audio_device.X' and 'network_speaker.abc'
    include_speakers = []
    include_areas = []

    for speaker_ref in session.speakers:
        if speaker_ref == 'local' or speaker_ref == 'local_audio':
            # Local speaker selected - use area selection to indicate local audio
            include_areas.append('local_audio')
        elif speaker_ref.startswith('network_speaker.'):
            # Network speaker - keep the full entity_id
            include_speakers.append(speaker_ref)

    adhoc_selection = {
        'include_floors': [],
        'include_areas': include_areas,
        'include_speakers': include_speakers,
        'exclude_areas': [],
        'exclude_speakers': []
    }

    return {
        'id': session.id,
        'name': session.name,
        'name_source': 'custom' if session.name != theme_name else 'theme',
        'theme_id': session.theme_id,
        'preset_id': session.preset_id,
        'speaker_group_id': None,
        'adhoc_selection': adhoc_selection,
        'volume': session.volume,
        'is_playing': session.is_playing,
        'speakers': session.speakers,
        'speaker_summary': speaker_summary,
        'channel_id': 0 if session.is_playing else None,
        'cycle_config': {
            'enabled': False,
            'interval_minutes': 60,
            'randomize': False,
            'theme_ids': []
        },
        'created_at': session.created_at,
        'last_played_at': session.last_played_at
    }


def create_app(app_instance: 'SonoriumApp') -> FastAPI:
    """Create the FastAPI application."""
    global _app_instance, _last_heartbeat, _heartbeat_check_thread
    _app_instance = app_instance

    # Load saved sessions from config
    _load_sessions_from_config()

    # Initialize heartbeat timestamp
    import time
    _last_heartbeat = time.time()

    # Start heartbeat check thread
    def heartbeat_checker():
        import time
        global _last_heartbeat
        while True:
            time.sleep(2)  # Check every 2 seconds
            if _stop_on_disconnect and _app_instance and _app_instance.playback_state == 'playing':
                elapsed = time.time() - _last_heartbeat
                if elapsed > _heartbeat_timeout:
                    logger.info(f'Browser disconnected (no heartbeat for {elapsed:.1f}s). Stopping playback.')
                    _app_instance.stop()
                    # Stop all sessions
                    for session in _sessions.values():
                        session.is_playing = False

    import threading
    _heartbeat_check_thread = threading.Thread(target=heartbeat_checker, daemon=True)
    _heartbeat_check_thread.start()

    fastapi_app = FastAPI(title='Sonorium', version='1.0.0')

    # Add exception handler for validation errors to log details
    from fastapi.exceptions import RequestValidationError
    from starlette.requests import Request

    @fastapi_app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        body = await request.body()
        logger.error(f'Validation error on {request.method} {request.url.path}')
        logger.error(f'Request body: {body.decode("utf-8", errors="replace")}')
        logger.error(f'Validation errors: {exc.errors()}')
        return JSONResponse(
            status_code=422,
            content={'detail': exc.errors()}
        )

    # Serve static files (web UI)
    # Web folder is at app/core/web/, this file is at app/core/sonorium/
    web_dir = Path(__file__).parent.parent / 'web'
    templates_dir = web_dir / 'templates'

    if (web_dir / 'static').exists():
        fastapi_app.mount('/static', StaticFiles(directory=str(web_dir / 'static')), name='static')

    # --- Startup Event: Validate Network Speakers ---
    @fastapi_app.on_event('startup')
    async def validate_saved_speakers():
        """Validate saved network speakers on startup."""
        try:
            from sonorium.network_speakers import validate_network_speakers, get_discovered_speakers
            speakers = get_discovered_speakers()
            if speakers:
                logger.info(f"Validating {len(speakers)} saved network speakers...")
                results = await validate_network_speakers()
                available = sum(1 for v in results.values() if v)
                logger.info(f"Network speaker validation: {available}/{len(results)} available")
            else:
                logger.info("No saved network speakers to validate")

            # Load enabled network speakers from config
            config = get_config()
            if config.enabled_network_speakers:
                logger.info(f"Restoring {len(config.enabled_network_speakers)} enabled network speakers")
                _app_instance.set_enabled_network_speakers(config.enabled_network_speakers)
        except Exception as e:
            logger.error(f"Failed to validate network speakers on startup: {e}")

    # --- Root / UI ---

    @fastapi_app.get('/', response_class=HTMLResponse)
    async def root():
        index_path = templates_dir / 'index.html'
        if index_path.exists():
            return HTMLResponse(content=index_path.read_text(encoding='utf-8'))
        return HTMLResponse(content='<h1>Sonorium</h1><p>Web UI not found</p>')

    @fastapi_app.get('/logo.png')
    async def serve_logo():
        import sys
        if getattr(sys, 'frozen', False):
            logo_path = Path(sys._MEIPASS) / 'logo.png'
        else:
            logo_path = Path(__file__).parent.parent / 'logo.png'
        if logo_path.exists():
            return FileResponse(logo_path)
        raise HTTPException(status_code=404, detail='Logo not found')

    # --- Status ---

    @fastapi_app.get('/api/status')
    async def get_status():
        playing_count = sum(1 for s in _sessions.values() if s.is_playing)
        return {
            'version': _get_version(),
            'playback_state': _app_instance.playback_state,
            'current_theme': _app_instance.current_theme,
            'current_preset': _app_instance.current_preset,
            'master_volume': _app_instance.master_volume,
            'playing_sessions': playing_count,
            'total_sessions': len(_sessions),
            'mode': 'standalone'
        }

    # --- Heartbeat (browser connection tracking) ---

    @fastapi_app.post('/api/heartbeat')
    async def heartbeat():
        """Receive heartbeat from browser to track connection."""
        global _last_heartbeat
        import time
        _last_heartbeat = time.time()
        return {'status': 'ok'}

    @fastapi_app.get('/api/heartbeat/settings')
    async def get_heartbeat_settings():
        """Get heartbeat settings."""
        return {
            'enabled': _stop_on_disconnect,
            'timeout': _heartbeat_timeout
        }

    @fastapi_app.put('/api/heartbeat/settings')
    async def set_heartbeat_settings(enabled: bool = True):
        """Enable/disable stop on disconnect."""
        global _stop_on_disconnect
        _stop_on_disconnect = enabled
        return {'enabled': _stop_on_disconnect}

    # --- Sessions ---

    @fastapi_app.get('/api/sessions')
    async def get_sessions():
        return [_session_to_dict(s) for s in _sessions.values()]

    @fastapi_app.post('/api/sessions')
    async def create_session(request: CreateSessionRequest):
        session_id = str(uuid.uuid4())[:8]

        # Determine name
        name = request.custom_name
        if not name and request.theme_id:
            theme = _app_instance.get_theme(request.theme_id)
            if theme:
                name = theme.name
        if not name:
            name = f'Session {len(_sessions) + 1}'

        # Parse speakers from adhoc_selection or direct speakers list
        if request.adhoc_selection:
            speakers = _convert_adhoc_to_speakers(request.adhoc_selection)
            logger.info(f'Converted adhoc_selection to speakers: {speakers}')
        elif request.speakers:
            speakers = request.speakers
        else:
            speakers = ['local']  # Default to local only

        use_local = _has_local_speaker(speakers)

        session = Session(
            id=session_id,
            name=name,
            theme_id=request.theme_id,
            preset_id=request.preset_id,
            volume=request.volume or 80,
            speakers=speakers,
            use_local_speaker=use_local
        )
        _sessions[session_id] = session

        # Save sessions to config
        _save_sessions_to_config()

        return _session_to_dict(session)

    @fastapi_app.get('/api/sessions/{session_id}')
    async def get_session(session_id: str):
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail='Session not found')
        return _session_to_dict(_sessions[session_id])

    @fastapi_app.put('/api/sessions/{session_id}')
    async def update_session(session_id: str, request: UpdateSessionRequest):
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail='Session not found')

        session = _sessions[session_id]
        needs_restart = False
        speakers_changed = False

        if request.theme_id is not None and request.theme_id != session.theme_id:
            session.theme_id = request.theme_id
            needs_restart = True
        if request.preset_id is not None and request.preset_id != session.preset_id:
            session.preset_id = request.preset_id
            needs_restart = True
        if request.custom_name is not None:
            session.name = request.custom_name
        if request.volume is not None:
            session.volume = request.volume
            # Apply volume immediately if this session is playing
            if session.is_playing:
                _app_instance.set_volume(request.volume / 100.0)

        # Handle speaker selection changes (from adhoc_selection or direct speakers list)
        new_speakers_list = None
        if request.adhoc_selection is not None:
            new_speakers_list = _convert_adhoc_to_speakers(request.adhoc_selection)
            logger.info(f'Converted adhoc_selection to speakers: {new_speakers_list}')
        elif request.speakers is not None:
            new_speakers_list = request.speakers

        old_use_local = session.use_local_speaker
        if new_speakers_list is not None:
            old_speakers = set(session.speakers)
            new_speakers = set(new_speakers_list)
            logger.info(f'Session {session_id} speaker comparison: old={old_speakers}, new={new_speakers}, equal={old_speakers == new_speakers}')
            if old_speakers != new_speakers:
                session.speakers = new_speakers_list
                session.use_local_speaker = _has_local_speaker(new_speakers_list)
                speakers_changed = True
                logger.info(f'Session {session_id} speakers changed: {new_speakers_list}, '
                           f'old_use_local={old_use_local}, new_use_local={session.use_local_speaker}, '
                           f'is_playing={session.is_playing}')

        # Crossfade to new theme/preset if changed while playing
        if needs_restart and session.is_playing:
            logger.info(f'Crossfading for session {session_id} due to theme/preset change')
            _app_instance.crossfade_to(session.theme_id, preset_id=session.preset_id)

        # Handle speaker changes while playing
        if speakers_changed and session.is_playing:
            # Handle local playback start/stop based on local speaker selection change
            if old_use_local and not session.use_local_speaker:
                # Local speaker was removed - stop local playback
                logger.info(f'Session {session_id}: stopping local playback (local speaker unchecked)')
                _app_instance.stop()
            elif not old_use_local and session.use_local_speaker:
                # Local speaker was added - start local playback
                logger.info(f'Session {session_id}: starting local playback (local speaker checked)')
                if session.theme_id:
                    _app_instance.play(session.theme_id, preset_id=session.preset_id)
                    _app_instance.set_volume(session.volume / 100.0)

            # Update network speakers
            await _update_session_speakers(session)

        # Save sessions to config
        _save_sessions_to_config()

        return _session_to_dict(session)

    @fastapi_app.delete('/api/sessions/{session_id}')
    async def delete_session(session_id: str):
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail='Session not found')

        session = _sessions[session_id]
        if session.is_playing:
            _app_instance.stop()
            session.is_playing = False

        del _sessions[session_id]

        # Save sessions to config
        _save_sessions_to_config()

        return {'status': 'ok'}

    @fastapi_app.post('/api/sessions/{session_id}/play')
    async def play_session(session_id: str):
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail='Session not found')

        session = _sessions[session_id]

        # Stop any other playing sessions and their network speakers
        for s in _sessions.values():
            if s.is_playing and s.id != session_id:
                await _stop_session_speakers(s)
                s.is_playing = False

        # Play this session's theme
        if session.theme_id:
            # Only play to local speaker if enabled
            if session.use_local_speaker:
                _app_instance.play(session.theme_id, preset_id=session.preset_id)
                _app_instance.set_volume(session.volume / 100.0)
            else:
                # No local playback - just set the theme for streaming
                _app_instance.current_theme = session.theme_id
                _app_instance.current_preset = session.preset_id
                _app_instance.playback_state = 'playing'

            session.is_playing = True
            session.last_played_at = datetime.now().isoformat()

            # Start streaming to network speakers
            await _start_session_speakers(session)

        return _session_to_dict(session)

    @fastapi_app.post('/api/sessions/{session_id}/stop')
    async def stop_session(session_id: str):
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail='Session not found')

        session = _sessions[session_id]

        # Stop local playback
        if session.use_local_speaker:
            _app_instance.stop()

        # Stop network speakers
        await _stop_session_speakers(session)

        session.is_playing = False

        return _session_to_dict(session)

    @fastapi_app.post('/api/sessions/{session_id}/volume')
    async def set_session_volume(session_id: str, request: VolumeRequest):
        if session_id not in _sessions:
            raise HTTPException(status_code=404, detail='Session not found')

        session = _sessions[session_id]
        session.volume = request.volume

        if session.is_playing:
            _app_instance.set_volume(request.volume / 100.0)

        return _session_to_dict(session)

    # --- Themes ---

    @fastapi_app.get('/api/themes')
    async def get_themes():
        themes = []
        for theme in _app_instance.themes:
            track_count = len(theme.instances) if theme.instances else 0
            theme_data = {
                'id': theme.name,
                'name': theme.name,
                'track_count': track_count,
                'total_tracks': track_count,  # UI expects this field
                'has_audio': track_count > 0,  # UI expects this field
                'is_current': theme.name == _app_instance.current_theme,
                'description': '',
                'icon': '',
                'categories': [],
                'is_favorite': False
            }

            # Get metadata if exists
            meta_path = _app_instance.path_audio / theme.name / 'metadata.json'
            if meta_path.exists():
                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    theme_data.update({
                        'description': meta.get('description', ''),
                        'icon': meta.get('icon', ''),
                        'categories': meta.get('categories', []),
                        'is_favorite': meta.get('is_favorite', False)
                    })
                except Exception:
                    pass

            themes.append(theme_data)

        return themes

    @fastapi_app.post('/api/themes/refresh')
    async def refresh_themes():
        _app_instance.refresh_themes()
        return {'status': 'ok', 'count': len(_app_instance.themes)}

    # --- HTTP Audio Streaming ---

    @fastapi_app.get('/stream/{theme_id}')
    async def stream_theme(theme_id: str, preset_id: str = None):
        """
        Stream audio for a theme as MP3.

        This endpoint provides an infinite MP3 audio stream that can be consumed by:
        - Network speakers (Chromecast, Sonos, etc.)
        - Media players
        - Web browsers

        The stream mixes all enabled tracks in real-time with crossfading.

        Args:
            theme_id: The theme ID to stream
            preset_id: Optional preset ID to apply before streaming
        """
        from fastapi.responses import StreamingResponse

        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail=f'Theme "{theme_id}" not found')

        # Apply preset if specified
        if preset_id:
            presets = theme.get_presets()
            preset = next((p for p in presets if p['id'] == preset_id), None)
            if preset:
                # Apply preset track settings
                for instance in theme.instances:
                    track_settings = preset.get('track_settings', {}).get(instance.name, {})
                    if track_settings:
                        if 'volume' in track_settings:
                            instance.volume = float(track_settings['volume'])
                        if 'presence' in track_settings:
                            instance.presence = float(track_settings['presence'])
                        if 'muted' in track_settings:
                            instance.is_enabled = not track_settings['muted']

        # Create a new stream for this client
        audio_stream = theme.get_stream()
        logger.info(f'Starting HTTP stream for theme "{theme_id}" (preset: {preset_id or "none"})')

        return StreamingResponse(
            audio_stream,
            media_type='audio/mpeg',
            headers={
                'Cache-Control': 'no-cache, no-store',
                'Connection': 'keep-alive',
                'X-Content-Type-Options': 'nosniff',
            }
        )

    @fastapi_app.get('/api/stream/url')
    async def get_stream_url_endpoint(request: Request):
        """
        Get the base URL for streaming.

        Network speaker plugins use this to construct stream URLs for devices.
        Returns the server's base URL that external devices can connect to.
        """
        from sonorium.config import get_stream_base_url

        # Use detected local IP for the stream URL
        config = get_config()
        port = config.server_port if hasattr(config, 'server_port') else 8008
        base_url = get_stream_base_url(port)

        return {
            'base_url': base_url,
            'stream_path': '/stream/{theme_id}',
            'example': f'{base_url}/stream/example_theme'
        }

    @fastapi_app.get('/api/themes/{theme_id}')
    async def get_theme(theme_id: str):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        return {
            'id': theme.name,
            'name': theme.name,
            'tracks': [
                {
                    'name': inst.name,
                    'volume': inst.volume,
                    'presence': inst.presence,
                    'is_enabled': inst.is_enabled,
                    'playback_mode': inst.playback_mode.value,
                    'exclusive': inst.exclusive,
                    'crossfade_enabled': inst.crossfade_enabled,
                    'duration_seconds': inst.meta.duration_seconds if inst.meta else 0
                }
                for inst in theme.instances
            ] if theme.instances else []
        }

    @fastapi_app.delete('/api/themes/{theme_id}')
    async def delete_theme(theme_id: str):
        """Delete a theme and all its files."""
        import shutil
        import time
        import gc

        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        # Stop playback if this theme is currently playing
        if _app_instance.current_theme == theme_id:
            _app_instance.stop()

        # Stop any sessions using this theme
        sessions_stopped = []
        for session_id, session in _sessions.items():
            if session.theme_id == theme_id and session.is_playing:
                # Stop this session's playback
                _app_instance.stop()
                session.is_playing = False
                sessions_stopped.append(session_id)
                logger.info(f'Stopped session "{session.name}" for theme deletion')

        # Clear the theme's streams to release file handles
        if theme.streams:
            theme.streams.clear()

        # Remove theme from app's themes dictionary to fully release references
        if theme_id in _app_instance.themes:
            del _app_instance.themes[theme_id]

        # Force garbage collection to release file handles
        gc.collect()

        # Brief delay to allow file handles to be released
        time.sleep(0.2)

        # Get the theme folder path - use theme.name for the folder
        theme_path = _app_instance.path_audio / theme.name

        if not theme_path.exists():
            # Also try theme_id directly
            theme_path = _app_instance.path_audio / theme_id
            if not theme_path.exists():
                raise HTTPException(status_code=404, detail='Theme folder not found')

        try:
            # Delete the entire theme folder with retry logic
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    shutil.rmtree(theme_path)
                    break
                except PermissionError:
                    if attempt < max_retries - 1:
                        gc.collect()
                        time.sleep(0.5)
                    else:
                        raise

            # Refresh themes to update the list
            _app_instance.refresh_themes()

            return {'status': 'ok', 'message': f'Theme "{theme_id}" deleted', 'sessions_stopped': sessions_stopped}
        except PermissionError as e:
            raise HTTPException(status_code=500, detail=f'Permission denied: {e}. Stop any sessions using this theme first.')
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Failed to delete theme: {e}')

    @fastapi_app.put('/api/themes/{theme_id}/metadata')
    async def update_theme_metadata(theme_id: str, request: ThemeMetadataUpdate):
        """Update theme description and icon."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        # Update the theme's metadata
        if request.description is not None:
            theme._metadata['description'] = request.description
        if request.icon is not None:
            theme._metadata['icon'] = request.icon

        try:
            theme.save_metadata()
            return {'status': 'ok'}
        except PermissionError as e:
            raise HTTPException(status_code=500, detail=f'Permission denied: {e}')
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Failed to save: {e}')

    @fastapi_app.post('/api/themes/{theme_id}/categories')
    async def update_theme_categories(theme_id: str, request: ThemeCategoriesUpdate):
        """Update theme categories."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        theme._metadata['categories'] = request.categories

        try:
            theme.save_metadata()
            return {'status': 'ok', 'categories': request.categories}
        except PermissionError as e:
            raise HTTPException(status_code=500, detail=f'Permission denied: {e}')
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Failed to save: {e}')

    @fastapi_app.post('/api/themes/{theme_id}/favorite')
    async def toggle_theme_favorite(theme_id: str):
        """Toggle theme favorite status."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        # Toggle the is_favorite flag
        current = theme._metadata.get('is_favorite', False)
        theme._metadata['is_favorite'] = not current

        try:
            theme.save_metadata()
            return {'status': 'ok', 'is_favorite': theme._metadata['is_favorite']}
        except PermissionError as e:
            raise HTTPException(status_code=500, detail=f'Permission denied: {e}')
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Failed to save: {e}')

    @fastapi_app.get('/api/themes/{theme_id}/export')
    async def export_theme(theme_id: str):
        """Export a theme as a ZIP file."""
        import zipfile
        import io
        from fastapi.responses import StreamingResponse

        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        theme_path = _app_instance.path_audio / theme.name
        if not theme_path.exists():
            raise HTTPException(status_code=404, detail='Theme directory not found')

        # Create ZIP in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for file_path in theme_path.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(theme_path)
                    zf.write(file_path, arcname)

        zip_buffer.seek(0)
        filename = f'{theme.name}.zip'

        return StreamingResponse(
            zip_buffer,
            media_type='application/zip',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'}
        )

    @fastapi_app.post('/api/themes/import')
    async def import_theme_zip(file: UploadFile = File(...)):
        """Import a theme from a ZIP file."""
        import zipfile
        import io
        import shutil

        if not file.filename.endswith('.zip'):
            raise HTTPException(status_code=400, detail='File must be a ZIP archive')

        try:
            # Read the zip file
            contents = await file.read()
            zip_buffer = io.BytesIO(contents)

            with zipfile.ZipFile(zip_buffer, 'r') as zf:
                # Check for valid theme structure
                namelist = zf.namelist()
                if not namelist:
                    raise HTTPException(status_code=400, detail='ZIP file is empty')

                # Determine the theme folder name
                # If all files are in a subdirectory, use that as the theme name
                # Otherwise use the zip filename without extension
                first_path = namelist[0]
                if '/' in first_path:
                    theme_folder = first_path.split('/')[0]
                    # Verify all files are in this folder
                    all_in_folder = all(n.startswith(theme_folder + '/') or n == theme_folder + '/' for n in namelist)
                else:
                    all_in_folder = False
                    theme_folder = Path(file.filename).stem

                # Create theme directory
                theme_path = _app_instance.path_audio / theme_folder
                if theme_path.exists():
                    # Add suffix to avoid overwriting
                    i = 1
                    while (_app_instance.path_audio / f'{theme_folder}_{i}').exists():
                        i += 1
                    theme_folder = f'{theme_folder}_{i}'
                    theme_path = _app_instance.path_audio / theme_folder

                theme_path.mkdir(parents=True, exist_ok=True)

                # Extract files
                files_extracted = 0
                for name in namelist:
                    if name.endswith('/'):
                        continue  # Skip directories

                    # Determine target path
                    if all_in_folder:
                        # Remove the folder prefix
                        relative_name = name[len(first_path.split('/')[0]) + 1:]
                    else:
                        relative_name = name

                    if not relative_name:
                        continue

                    target_path = theme_path / relative_name
                    target_path.parent.mkdir(parents=True, exist_ok=True)

                    # Extract the file
                    with zf.open(name) as src, open(target_path, 'wb') as dst:
                        shutil.copyfileobj(src, dst)
                    files_extracted += 1

                logger.info(f'Imported theme "{theme_folder}" with {files_extracted} files')

                return {
                    'status': 'ok',
                    'theme_folder': theme_folder,
                    'files_extracted': files_extracted
                }

        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail='Invalid ZIP file')
        except Exception as e:
            logger.error(f'Failed to import theme: {e}')
            raise HTTPException(status_code=500, detail=f'Import failed: {str(e)}')

    @fastapi_app.post('/api/themes/{theme_id}/upload')
    async def upload_theme_file(theme_id: str, file: UploadFile = File(...)):
        """Upload an audio file to a theme."""
        import shutil

        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        theme_path = _app_instance.path_audio / theme.name
        if not theme_path.exists():
            raise HTTPException(status_code=404, detail='Theme directory not found')

        # Validate file type
        allowed_extensions = {'.mp3', '.wav', '.ogg', '.flac', '.m4a', '.aac'}
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f'Invalid file type. Allowed: {", ".join(allowed_extensions)}'
            )

        # Save file
        target_path = theme_path / file.filename
        try:
            with open(target_path, 'wb') as f:
                shutil.copyfileobj(file.file, f)

            logger.info(f'Uploaded file "{file.filename}" to theme "{theme.name}"')
            return {
                'status': 'ok',
                'filename': file.filename,
                'size': target_path.stat().st_size
            }
        except Exception as e:
            logger.error(f'Failed to upload file: {e}')
            raise HTTPException(status_code=500, detail=f'Upload failed: {str(e)}')

    @fastapi_app.get('/api/themes/{theme_id}/tracks')
    async def get_theme_tracks(theme_id: str):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        return {
            'tracks': [
                {
                    'name': inst.name,
                    'volume': inst.volume,
                    'presence': inst.presence,
                    'muted': not inst.is_enabled,  # UI expects muted, not is_enabled
                    'playback_mode': inst.playback_mode.value,
                    'exclusive': inst.exclusive,
                    'seamless_loop': inst.crossfade_enabled  # UI expects seamless_loop
                }
                for inst in theme.instances
            ] if theme.instances else []
        }

    @fastapi_app.put('/api/themes/{theme_id}/tracks/{track_name}/presence')
    async def set_track_presence(theme_id: str, track_name: str, request: TrackSettingRequest):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        track = next((t for t in theme.instances if t.name == track_name), None)
        if not track:
            raise HTTPException(status_code=404, detail='Track not found')

        value = request.get_value('presence')
        if value is None:
            raise HTTPException(status_code=400, detail='Missing presence value')
        track.presence = float(value)
        theme.save_metadata()
        return {'status': 'ok', 'presence': track.presence}

    @fastapi_app.put('/api/themes/{theme_id}/tracks/{track_name}/volume')
    async def set_track_volume(theme_id: str, track_name: str, request: TrackSettingRequest):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        track = next((t for t in theme.instances if t.name == track_name), None)
        if not track:
            raise HTTPException(status_code=404, detail='Track not found')

        value = request.get_value('volume')
        if value is None:
            raise HTTPException(status_code=400, detail='Missing volume value')
        track.volume = float(value)
        theme.save_metadata()
        return {'status': 'ok', 'volume': track.volume}

    @fastapi_app.put('/api/themes/{theme_id}/tracks/{track_name}/muted')
    async def set_track_muted(theme_id: str, track_name: str, request: TrackSettingRequest):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        track = next((t for t in theme.instances if t.name == track_name), None)
        if not track:
            raise HTTPException(status_code=404, detail='Track not found')

        value = request.get_value('muted')
        if value is None:
            value = request.value
        track.is_enabled = not bool(value)
        theme.save_metadata()
        return {'status': 'ok', 'muted': not track.is_enabled}

    @fastapi_app.put('/api/themes/{theme_id}/tracks/{track_name}/playback_mode')
    async def set_track_playback_mode(theme_id: str, track_name: str, request: TrackSettingRequest):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        track = next((t for t in theme.instances if t.name == track_name), None)
        if not track:
            raise HTTPException(status_code=404, detail='Track not found')

        value = request.get_value('playback_mode')
        if value is None:
            raise HTTPException(status_code=400, detail='Missing playback_mode value')

        from sonorium.recording import PlaybackMode
        mode_str = str(value).lower()
        try:
            track.playback_mode = PlaybackMode(mode_str)
        except ValueError:
            track.playback_mode = PlaybackMode.AUTO

        theme.save_metadata()
        return {'status': 'ok', 'playback_mode': track.playback_mode.value}

    @fastapi_app.put('/api/themes/{theme_id}/tracks/{track_name}/exclusive')
    async def set_track_exclusive(theme_id: str, track_name: str, request: TrackSettingRequest):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        track = next((t for t in theme.instances if t.name == track_name), None)
        if not track:
            raise HTTPException(status_code=404, detail='Track not found')

        value = request.get_value('exclusive')
        if value is None:
            value = request.value
        track.exclusive = bool(value)
        theme.save_metadata()
        return {'status': 'ok', 'exclusive': track.exclusive}

    @fastapi_app.put('/api/themes/{theme_id}/tracks/{track_name}/crossfade')
    async def set_track_crossfade(theme_id: str, track_name: str, request: TrackSettingRequest):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        track = next((t for t in theme.instances if t.name == track_name), None)
        if not track:
            raise HTTPException(status_code=404, detail='Track not found')

        value = request.value
        track.crossfade_enabled = bool(value) if value is not None else True
        theme.save_metadata()
        return {'status': 'ok', 'crossfade_enabled': track.crossfade_enabled}

    @fastapi_app.put('/api/themes/{theme_id}/tracks/{track_name}/seamless_loop')
    async def set_track_seamless_loop(theme_id: str, track_name: str, request: TrackSettingRequest):
        """Set seamless loop (same as crossfade) - alias for UI compatibility."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        track = next((t for t in theme.instances if t.name == track_name), None)
        if not track:
            raise HTTPException(status_code=404, detail='Track not found')

        value = request.get_value('seamless_loop')
        if value is None:
            value = request.value
        track.crossfade_enabled = bool(value) if value is not None else True
        theme.save_metadata()
        return {'status': 'ok', 'seamless_loop': track.crossfade_enabled}

    # --- Track Audio Preview ---

    @fastapi_app.get('/api/themes/{theme_id}/tracks/{track_name}/audio')
    async def get_track_audio(theme_id: str, track_name: str):
        """Stream the audio file for preview."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        track = next((t for t in theme.instances if t.name == track_name), None)
        if not track:
            raise HTTPException(status_code=404, detail='Track not found')

        audio_path = track.meta.path
        if not audio_path.exists():
            raise HTTPException(status_code=404, detail='Audio file not found')

        return FileResponse(
            audio_path,
            media_type='audio/mpeg',
            filename=audio_path.name
        )

    # --- Presets ---

    @fastapi_app.get('/api/themes/{theme_id}/presets')
    async def get_theme_presets(theme_id: str):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        return {'presets': theme.get_presets()}

    @fastapi_app.post('/api/themes/{theme_id}/presets')
    async def create_preset(theme_id: str, request: PresetCreate):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        # Generate preset ID
        preset_id = str(uuid.uuid4())[:8]

        try:
            preset_data = theme.save_preset(
                preset_id,
                request.name,
                request.track_settings if request.track_settings else None
            )
            return {
                'id': preset_id,
                'preset_id': preset_id,  # UI expects this field
                'name': request.name,
                'description': request.description or '',
                'track_settings': preset_data.get('tracks', {})
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Failed to save preset: {e}')

    @fastapi_app.post('/api/themes/{theme_id}/presets/{preset_id}/load')
    async def load_preset(theme_id: str, preset_id: str):
        """Load a preset - apply its track settings to the theme."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        # Get preset from metadata
        presets = theme._metadata.get('presets', {})
        if preset_id not in presets:
            raise HTTPException(status_code=404, detail='Preset not found')

        preset_data = presets[preset_id]
        track_settings = preset_data.get('tracks', {})

        # Apply track settings to theme instances
        from sonorium.recording import PlaybackMode
        for instance in theme.instances:
            settings = track_settings.get(instance.name) or track_settings.get(f'{instance.name}.mp3', {})
            if settings:
                if 'volume' in settings:
                    instance.volume = float(settings['volume'])
                if 'presence' in settings:
                    instance.presence = float(settings['presence'])
                if 'muted' in settings:
                    instance.is_enabled = not settings['muted']
                if 'playback_mode' in settings:
                    try:
                        instance.playback_mode = PlaybackMode(settings['playback_mode'])
                    except ValueError:
                        instance.playback_mode = PlaybackMode.AUTO
                if 'exclusive' in settings:
                    instance.exclusive = bool(settings['exclusive'])
                if 'seamless_loop' in settings:
                    instance.crossfade_enabled = bool(settings['seamless_loop'])

        logger.info(f'Loaded preset {preset_id} for theme {theme_id}')

        # If this theme is currently playing, crossfade to apply changes smoothly
        if _app_instance.playback_state == 'playing' and _app_instance.current_theme == theme_id:
            logger.info(f'Crossfading to apply preset changes')
            _app_instance.crossfade_to(theme_id, preset_id=preset_id)

        # Return the updated track list so UI can refresh
        return {
            'status': 'ok',
            'preset_id': preset_id,
            'name': preset_data.get('name', preset_id),  # JS expects 'name'
            'tracks': [
                {
                    'name': inst.name,
                    'volume': inst.volume,
                    'presence': inst.presence,
                    'muted': not inst.is_enabled,
                    'playback_mode': inst.playback_mode.value,
                    'exclusive': inst.exclusive,
                    'seamless_loop': inst.crossfade_enabled
                }
                for inst in theme.instances
            ]
        }

    @fastapi_app.put('/api/themes/{theme_id}/presets/{preset_id}/default')
    async def set_preset_default(theme_id: str, preset_id: str):
        """Set a preset as the default for this theme."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        presets = theme._metadata.get('presets', {})
        if preset_id not in presets:
            raise HTTPException(status_code=404, detail='Preset not found')

        # Clear default from all presets
        for pid, pdata in presets.items():
            pdata['is_default'] = (pid == preset_id)

        try:
            theme.save_metadata()
            return {'status': 'ok'}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Failed to set default: {e}')

    @fastapi_app.put('/api/themes/{theme_id}/presets/{preset_id}')
    async def update_preset(theme_id: str, preset_id: str):
        """Update an existing preset with current track settings."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        presets = theme._metadata.get('presets', {})
        if preset_id not in presets:
            raise HTTPException(status_code=404, detail='Preset not found')

        # Capture current track settings
        track_settings = {}
        for instance in theme.instances:
            track_settings[instance.name] = {
                'volume': instance.volume,
                'presence': instance.presence,
                'muted': not instance.is_enabled,
                'playback_mode': instance.playback_mode.value,
                'exclusive': instance.exclusive,
                'seamless_loop': instance.crossfade_enabled
            }

        # Update preset tracks, preserve name and is_default
        presets[preset_id]['tracks'] = track_settings

        try:
            theme.save_metadata()
            return {
                'status': 'ok',
                'name': presets[preset_id].get('name', preset_id),
                'tracks_updated': len(track_settings)
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Failed to update preset: {e}')

    @fastapi_app.put('/api/themes/{theme_id}/presets/{preset_id}/rename')
    async def rename_preset(theme_id: str, preset_id: str, request: PresetRename):
        """Rename a preset."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        presets = theme._metadata.get('presets', {})
        if preset_id not in presets:
            raise HTTPException(status_code=404, detail='Preset not found')

        presets[preset_id]['name'] = request.name

        try:
            theme.save_metadata()
            return {'status': 'ok', 'name': request.name}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Failed to rename preset: {e}')

    @fastapi_app.get('/api/themes/{theme_id}/presets/{preset_id}/export')
    async def export_preset(theme_id: str, preset_id: str):
        """Export a preset as JSON."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        presets = theme._metadata.get('presets', {})
        if preset_id not in presets:
            raise HTTPException(status_code=404, detail='Preset not found')

        preset_data = presets[preset_id]
        return {
            'name': preset_data.get('name', preset_id),
            'tracks': preset_data.get('tracks', {})
        }

    @fastapi_app.post('/api/themes/{theme_id}/presets/import')
    async def import_preset(theme_id: str, request: PresetImport):
        """Import a preset from JSON."""
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        try:
            import_data = json.loads(request.preset_json)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f'Invalid JSON: {e}')

        # Get name from request or imported data
        preset_name = request.name or import_data.get('name', 'Imported Preset')
        track_settings = import_data.get('tracks', {})

        if not track_settings:
            raise HTTPException(status_code=400, detail='No track settings in preset JSON')

        # Generate preset ID
        preset_id = str(uuid.uuid4())[:8]

        # Validate track names exist in theme
        theme_track_names = {inst.name for inst in theme.instances}
        unknown_tracks = set(track_settings.keys()) - theme_track_names
        warning = None
        if unknown_tracks:
            warning = f'{len(unknown_tracks)} track(s) not found in theme'

        try:
            preset_data = theme.save_preset(preset_id, preset_name, track_settings)
            result = {
                'status': 'ok',
                'preset_id': preset_id,
                'name': preset_name
            }
            if warning:
                result['warning'] = warning
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Failed to import preset: {e}')

    @fastapi_app.delete('/api/themes/{theme_id}/presets/{preset_id}')
    async def delete_preset(theme_id: str, preset_id: str):
        theme = _app_instance.get_theme(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail='Theme not found')

        try:
            theme.delete_preset(preset_id)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'Failed to delete preset: {e}')

        return {'status': 'ok'}

    # --- Categories ---

    @fastapi_app.get('/api/categories')
    async def get_categories():
        # Collect unique categories from all themes
        categories = set()
        for theme in _app_instance.themes:
            meta_path = _app_instance.path_audio / theme.name / 'metadata.json'
            if meta_path.exists():
                try:
                    with open(meta_path, 'r', encoding='utf-8') as f:
                        meta = json.load(f)
                    for cat in meta.get('categories', []):
                        categories.add(cat)
                except Exception:
                    pass
        return {'categories': list(categories)}

    @fastapi_app.post('/api/categories')
    async def create_category(request: CategoryCreate):
        """Create a new category. Categories are just strings stored in theme metadata."""
        # Categories don't have their own storage - they exist when at least one theme uses them
        # This endpoint just validates the name and returns success
        # The actual assignment happens when themes are updated with the category
        if not request.name or not request.name.strip():
            raise HTTPException(status_code=400, detail='Category name is required')
        return {'status': 'ok', 'name': request.name.strip()}

    # --- Speaker Hierarchy (adapted for audio devices) ---

    @fastapi_app.get('/api/speakers/hierarchy')
    async def get_speaker_hierarchy():
        """Return audio devices and network speakers as a hierarchy."""
        from sonorium.network_speakers import get_discovered_speakers

        devices = _app_instance.list_audio_devices()
        current_device = _app_instance._current_device

        # Build hierarchy with local audio devices as "speakers"
        local_speakers = []
        for d in devices:
            local_speakers.append({
                'entity_id': f'audio_device.{d.id}',
                'name': d.name,
                'friendly_name': d.name,
                'state': 'on',
                'is_default': d.is_default,
                'is_current': d.id == (current_device.id if current_device else None),
                'channels': d.channels,
                'speaker_type': 'local'
            })

        # Get enabled network speakers
        enabled_network_ids = _app_instance.get_enabled_network_speakers()
        discovered = get_discovered_speakers()

        network_speakers = []
        for speaker in discovered:
            if speaker['id'] in enabled_network_ids:
                # Include availability status for UI display
                is_available = speaker.get('available', speaker.get('status') == 'available')
                network_speakers.append({
                    'entity_id': f'network_speaker.{speaker["id"]}',
                    'name': speaker['name'],
                    'friendly_name': speaker['name'],
                    'state': 'on' if is_available else 'unavailable',
                    'model': speaker.get('model'),
                    'host': speaker.get('host'),
                    'speaker_type': speaker.get('type', 'dlna'),
                    'network_speaker_id': speaker['id'],
                    'available': is_available,
                    'status': speaker.get('status', 'unknown'),
                    'last_seen': speaker.get('last_seen')
                })

        areas = []

        # Local audio devices area (if any)
        if local_speakers:
            areas.append({
                'area_id': 'local_audio',
                'name': 'Local Audio',
                'speakers': local_speakers
            })

        # Network speakers area (if any enabled)
        if network_speakers:
            areas.append({
                'area_id': 'network_speakers',
                'name': 'Network Speakers',
                'speakers': network_speakers
            })

        return {
            'floors': [],
            'unassigned_areas': areas,
            'unassigned_speakers': [],
            'custom_areas': {}
        }

    @fastapi_app.post('/api/speakers/refresh')
    async def refresh_speakers():
        """Refresh audio device list."""
        # Re-enumerate devices
        devices = _app_instance.list_audio_devices()
        return {'status': 'ok', 'count': len(devices)}

    @fastapi_app.post('/api/speakers/network/validate')
    async def validate_network_speakers_endpoint():
        """Validate all saved network speakers are still reachable."""
        try:
            from sonorium.network_speakers import validate_network_speakers, get_discovered_speakers
            results = await validate_network_speakers()
            speakers = get_discovered_speakers()
            return {
                'status': 'ok',
                'results': results,
                'speakers': speakers,
                'available_count': sum(1 for v in results.values() if v),
                'total_count': len(results)
            }
        except Exception as e:
            logger.error(f"Network speaker validation failed: {e}")
            return {'status': 'error', 'message': str(e)}

    # --- Speaker Groups (simplified for standalone) ---
    # In standalone mode, speaker groups are stored in memory/config
    # They can be used to organize audio output presets for future multi-device support

    _speaker_groups: list[dict] = []

    @fastapi_app.get('/api/groups')
    async def get_groups():
        """Return speaker groups list."""
        return _speaker_groups

    @fastapi_app.post('/api/groups')
    async def create_group(request: dict):
        """Create a new speaker group."""
        import uuid

        group = {
            'id': str(uuid.uuid4())[:8],
            'name': request.get('name', 'Unnamed Group'),
            'include_floors': request.get('include_floors', []),
            'include_areas': request.get('include_areas', []),
            'include_speakers': request.get('include_speakers', []),
        }
        _speaker_groups.append(group)
        logger.info(f"Created speaker group: {group['name']}")
        return group

    @fastapi_app.put('/api/groups/{group_id}')
    async def update_group(group_id: str, request: dict):
        """Update an existing speaker group."""
        for i, group in enumerate(_speaker_groups):
            if group['id'] == group_id:
                _speaker_groups[i] = {
                    'id': group_id,
                    'name': request.get('name', group['name']),
                    'include_floors': request.get('include_floors', []),
                    'include_areas': request.get('include_areas', []),
                    'include_speakers': request.get('include_speakers', []),
                }
                logger.info(f"Updated speaker group: {_speaker_groups[i]['name']}")
                return _speaker_groups[i]

        raise HTTPException(status_code=404, detail='Group not found')

    @fastapi_app.delete('/api/groups/{group_id}')
    async def delete_group(group_id: str):
        """Delete a speaker group."""
        global _speaker_groups
        original_count = len(_speaker_groups)
        _speaker_groups = [g for g in _speaker_groups if g['id'] != group_id]

        if len(_speaker_groups) == original_count:
            raise HTTPException(status_code=404, detail='Group not found')

        logger.info(f"Deleted speaker group: {group_id}")
        return {'status': 'ok'}

    # --- Channels (simplified for standalone) ---

    @fastapi_app.get('/api/channels')
    async def get_channels():
        """Return single local channel."""
        return [{
            'id': 0,
            'name': 'Local Audio',
            'state': _app_instance.playback_state,
            'current_theme': _app_instance.current_theme,
            'current_theme_name': _app_instance.current_theme,
            'client_count': 1,
            'stream_path': '/local'
        }]

    # --- Settings ---

    @fastapi_app.get('/api/settings')
    async def get_settings():
        from sonorium.config import get_config
        config = get_config()
        return {
            'default_volume': int(config.master_volume * 100),
            'crossfade_duration': 2.0,
            'max_groups': 10,
            'entity_prefix': 'sonorium',
            'show_in_sidebar': True,
            'auto_create_quick_play': True,
            'master_gain': int(config.master_volume * 100),
            'default_cycle_interval': 60,
            'default_cycle_randomize': False,
            'audio_path': config.audio_path
        }

    @fastapi_app.put('/api/settings')
    async def update_settings(request: SettingsUpdate):
        from sonorium.config import get_config
        config = get_config()

        if request.audio_path is not None:
            config.audio_path = request.audio_path
            _app_instance.path_audio = Path(request.audio_path)
            _app_instance.refresh_themes()

        if request.default_volume is not None:
            config.master_volume = request.default_volume / 100.0

        if request.master_gain is not None:
            config.master_volume = request.master_gain / 100.0
            _app_instance.set_volume(config.master_volume)

        if request.auto_play_on_start is not None:
            config.auto_play_on_start = request.auto_play_on_start

        config.save()
        return {'status': 'ok'}

    @fastapi_app.get('/api/settings/audio-devices')
    async def get_audio_devices():
        """Return list of available audio output devices."""
        import sounddevice as sd

        devices = sd.query_devices()
        output_devices = []

        current_device = _app_instance._current_device
        current_index = current_device.id if current_device else sd.default.device[1]

        for i, dev in enumerate(devices):
            # Only output devices (max_output_channels > 0)
            if dev['max_output_channels'] > 0:
                output_devices.append({
                    'index': i,
                    'name': dev['name'],
                    'channels': dev['max_output_channels'],
                    'sample_rate': int(dev['default_samplerate']),
                    'hostapi': dev['hostapi']
                })

        return {
            'devices': output_devices,
            'selected': current_index
        }

    @fastapi_app.put('/api/settings/audio-device')
    async def set_audio_device_endpoint(request: dict):
        """Set the active audio output device. Use -1 for no local device."""
        device_index = request.get('device_index')
        if device_index is None:
            raise HTTPException(status_code=400, detail='device_index is required')

        try:
            if device_index == -1:
                # Disable local audio output
                _app_instance.disable_local_audio()
                return {'status': 'ok', 'device_index': -1, 'message': 'Local audio disabled'}
            else:
                _app_instance.set_audio_device(device_index)
                return {'status': 'ok', 'device_index': device_index}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @fastapi_app.get('/api/settings/speakers')
    async def get_speaker_settings():
        """Return enabled speakers (audio devices + network speakers)."""
        from sonorium.network_speakers import get_discovered_speakers

        devices = _app_instance.list_audio_devices()
        current = _app_instance._current_device

        # Current local audio device
        enabled = []
        if current:
            enabled.append(f'audio_device.{current.id}')

        # Enabled network speakers
        enabled_network_ids = _app_instance.get_enabled_network_speakers()
        for nid in enabled_network_ids:
            enabled.append(f'network_speaker.{nid}')

        # All available speakers
        all_speakers = [f'audio_device.{d.id}' for d in devices]
        for speaker in get_discovered_speakers():
            all_speakers.append(f'network_speaker.{speaker["id"]}')

        return {
            'enabled_speakers': enabled,
            'all_speakers': all_speakers
        }

    @fastapi_app.put('/api/settings/speakers')
    async def update_speaker_settings(request: dict):
        """Update selected audio device."""
        enabled = request.get('enabled_speakers', [])
        if enabled:
            # Extract device ID from entity_id format
            entity_id = enabled[0]
            if entity_id.startswith('audio_device.'):
                device_id = int(entity_id.split('.')[1])
                _app_instance.set_audio_device(device_id)
        return {'status': 'ok'}

    @fastapi_app.post('/api/settings/speakers/enable')
    async def enable_speaker(request: dict):
        """Enable (select) an audio device."""
        entity_id = request.get('entity_id', '')
        if entity_id.startswith('audio_device.'):
            device_id = int(entity_id.split('.')[1])
            _app_instance.set_audio_device(device_id)
        return {'status': 'ok'}

    @fastapi_app.post('/api/settings/speakers/disable')
    async def disable_speaker(request: dict):
        """No-op for standalone - can't disable the only output."""
        return {'status': 'ok'}

    # --- Network Speakers ---
    # Native network speaker discovery (Chromecast, Sonos, DLNA)

    @fastapi_app.get('/api/network-speakers')
    async def get_network_speakers():
        """
        Get all previously discovered network speakers.

        Returns speakers organized by type, plus list of enabled speaker IDs.
        """
        from sonorium.network_speakers import get_discovered_speakers

        speakers = get_discovered_speakers()

        # Group by type
        by_type = {
            'chromecast': {'name': 'Chromecast', 'speakers': []},
            'sonos': {'name': 'Sonos', 'speakers': []},
            'dlna': {'name': 'DLNA/UPnP', 'speakers': []},
        }

        for speaker in speakers:
            speaker_type = speaker.get('type', 'dlna')
            if speaker_type in by_type:
                by_type[speaker_type]['speakers'].append(speaker)

        # Get enabled speakers from app settings
        enabled = _app_instance.get_enabled_network_speakers() if hasattr(_app_instance, 'get_enabled_network_speakers') else []

        return {
            'speakers': speakers,
            'by_type': by_type,
            'enabled': enabled
        }

    @fastapi_app.put('/api/network-speakers/enabled')
    async def set_enabled_network_speakers(request: dict):
        """Set which network speakers are enabled for streaming."""
        speaker_ids = request.get('speaker_ids', [])

        if hasattr(_app_instance, 'set_enabled_network_speakers'):
            _app_instance.set_enabled_network_speakers(speaker_ids)

            # Persist to config
            config = get_config()
            config.enabled_network_speakers = speaker_ids
            save_config(config)

            return {'status': 'ok', 'enabled': speaker_ids}
        else:
            raise HTTPException(status_code=501, detail='Network speaker management not implemented')

    @fastapi_app.post('/api/network-speakers/refresh')
    async def refresh_network_speakers():
        """Scan network for speakers (Chromecast, Sonos, DLNA)."""
        from sonorium.network_speakers import discover_network_speakers

        try:
            speakers = await discover_network_speakers(timeout=10.0)

            # Count by type
            counts = {'chromecast': 0, 'sonos': 0, 'dlna': 0}
            for speaker in speakers:
                speaker_type = speaker.get('type', 'dlna')
                if speaker_type in counts:
                    counts[speaker_type] += 1

            return {
                'status': 'ok',
                'total_speakers': len(speakers),
                'counts': counts,
                'speakers': speakers
            }
        except Exception as e:
            logger.error(f"Network speaker discovery failed: {e}")
            return {
                'status': 'error',
                'message': str(e),
                'speakers': []
            }

    @fastapi_app.post('/api/network-speakers/{speaker_id}/play')
    async def play_on_network_speaker(speaker_id: str, request: dict):
        """
        Start streaming to a network speaker.

        Args:
            speaker_id: The speaker's unique ID
            request: {
                "theme_id": "theme_name (optional, uses current theme if not specified)"
            }
        """
        from sonorium.streaming import get_streaming_manager
        from sonorium.network_speakers import get_speaker, is_speaker_available, SpeakerStatus

        # Get the speaker info
        speaker = get_speaker(speaker_id)
        if not speaker:
            raise HTTPException(status_code=404, detail=f'Speaker {speaker_id} not found')

        # Check if speaker is available (skip unavailable speakers gracefully)
        if speaker.status == SpeakerStatus.UNAVAILABLE:
            logger.warning(f'Speaker {speaker.name} ({speaker_id}) is unavailable, skipping playback')
            return {
                'status': 'skipped',
                'message': f'Speaker {speaker.name} is currently unavailable',
                'speaker': speaker.to_dict()
            }

        # Get theme (use current if not specified)
        theme_id = request.get('theme_id') or _app_instance.current_theme
        if not theme_id:
            raise HTTPException(status_code=400, detail='No theme specified and no theme currently playing')

        # Get streaming manager and start
        manager = get_streaming_manager()

        speaker_info = speaker.to_dict()
        success = await manager.start_streaming(
            speaker_id=speaker_id,
            speaker_type=speaker.speaker_type.value,
            speaker_info=speaker_info,
            theme_id=theme_id
        )

        if success:
            return {'status': 'ok', 'message': 'Streaming started', 'speaker': speaker_info}
        else:
            session = manager.get_session(speaker_id)
            error = session.error_message if session else 'Unknown error'
            raise HTTPException(status_code=500, detail=f'Failed to start streaming: {error}')

    @fastapi_app.post('/api/network-speakers/{speaker_id}/stop')
    async def stop_network_speaker(speaker_id: str, request: dict = None):
        """Stop streaming to a network speaker."""
        from sonorium.streaming import get_streaming_manager

        manager = get_streaming_manager()
        success = await manager.stop_streaming(speaker_id)

        if success:
            return {'status': 'ok'}
        else:
            raise HTTPException(status_code=500, detail='Failed to stop streaming')

    @fastapi_app.post('/api/network-speakers/{speaker_id}/volume')
    async def set_network_speaker_volume(speaker_id: str, request: dict):
        """Set volume on a network speaker (not yet implemented for streaming)."""
        level = request.get('level')
        if level is None:
            raise HTTPException(status_code=400, detail='level is required')

        # TODO: Implement volume control for streaming sessions
        # This would require sending volume commands to the specific device
        return {'status': 'ok', 'message': 'Volume control not yet implemented for streaming'}

    @fastapi_app.post('/api/network-speakers/stop-all')
    async def stop_all_network_speakers():
        """Stop streaming to all network speakers."""
        from sonorium.streaming import get_streaming_manager

        manager = get_streaming_manager()
        sessions = manager.get_active_sessions()
        await manager.stop_all()

        return {'status': 'ok', 'stopped': len(sessions)}

    @fastapi_app.get('/api/network-speakers/sessions')
    async def get_streaming_sessions():
        """Get all active streaming sessions."""
        from sonorium.streaming import get_streaming_manager

        manager = get_streaming_manager()
        sessions = manager.get_active_sessions()

        return {
            'sessions': [
                {
                    'speaker_id': s.speaker_id,
                    'speaker_type': s.speaker_type,
                    'stream_url': s.stream_url,
                    'state': s.state.value,
                    'error': s.error_message
                }
                for s in sessions
            ]
        }

    # --- Audio Settings ---

    @fastapi_app.get('/api/settings/audio')
    async def get_audio_settings():
        from sonorium.config import get_config
        config = get_config()
        return {
            'short_file_threshold': 15.0,
            'crossfade_duration': 2.0,
            'master_volume': config.master_volume,
            'audio_path': config.audio_path
        }

    @fastapi_app.put('/api/settings/audio')
    async def update_audio_settings(request: dict):
        from sonorium.config import get_config
        config = get_config()

        if 'master_volume' in request:
            config.master_volume = float(request['master_volume'])
            _app_instance.set_volume(config.master_volume)

        if 'audio_path' in request:
            config.audio_path = request['audio_path']
            _app_instance.path_audio = Path(request['audio_path'])
            _app_instance.refresh_themes()

        config.save()
        return {'status': 'ok'}

    # --- Plugins ---

    @fastapi_app.get('/api/plugins')
    async def get_plugins():
        """List all loaded plugins."""
        if _plugin_manager is None:
            return []
        return _plugin_manager.list_plugins()

    @fastapi_app.get('/api/plugins/{plugin_id}')
    async def get_plugin(plugin_id: str):
        """Get plugin details."""
        if _plugin_manager is None:
            raise HTTPException(status_code=503, detail='Plugin system not initialized')

        plugin = _plugin_manager.get_plugin(plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail='Plugin not found')

        return plugin.to_dict()

    @fastapi_app.put('/api/plugins/{plugin_id}/enable')
    async def enable_plugin(plugin_id: str):
        """Enable a plugin."""
        if _plugin_manager is None:
            raise HTTPException(status_code=503, detail='Plugin system not initialized')

        success = await _plugin_manager.enable_plugin(plugin_id)
        if not success:
            raise HTTPException(status_code=400, detail='Failed to enable plugin')

        return {'status': 'ok', 'enabled': True}

    @fastapi_app.put('/api/plugins/{plugin_id}/disable')
    async def disable_plugin(plugin_id: str):
        """Disable a plugin."""
        if _plugin_manager is None:
            raise HTTPException(status_code=503, detail='Plugin system not initialized')

        success = await _plugin_manager.disable_plugin(plugin_id)
        if not success:
            raise HTTPException(status_code=400, detail='Failed to disable plugin')

        return {'status': 'ok', 'enabled': False}

    @fastapi_app.get('/api/plugins/{plugin_id}/settings')
    async def get_plugin_settings(plugin_id: str):
        """Get plugin settings."""
        if _plugin_manager is None:
            raise HTTPException(status_code=503, detail='Plugin system not initialized')

        plugin = _plugin_manager.get_plugin(plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail='Plugin not found')

        return {
            'settings': plugin.settings,
            'schema': plugin.get_settings_schema()
        }

    @fastapi_app.put('/api/plugins/{plugin_id}/settings')
    async def update_plugin_settings(plugin_id: str, request: PluginSettingsUpdate):
        """Update plugin settings."""
        if _plugin_manager is None:
            raise HTTPException(status_code=503, detail='Plugin system not initialized')

        success = _plugin_manager.update_plugin_settings(plugin_id, request.settings)
        if not success:
            raise HTTPException(status_code=404, detail='Plugin not found')

        return {'status': 'ok'}

    @fastapi_app.post('/api/plugins/{plugin_id}/action')
    async def call_plugin_action(plugin_id: str, request: PluginActionRequest):
        """Execute a plugin action."""
        if _plugin_manager is None:
            raise HTTPException(status_code=503, detail='Plugin system not initialized')

        result = await _plugin_manager.call_action(plugin_id, request.action, request.data)
        return result

    @fastapi_app.post('/api/plugins/reload')
    async def reload_plugins():
        """Reload all plugins."""
        if _plugin_manager is None:
            raise HTTPException(status_code=503, detail='Plugin system not initialized')

        await _plugin_manager.reload_plugins()
        return {'status': 'ok', 'count': len(_plugin_manager.plugins)}

    @fastapi_app.delete('/api/plugins/{plugin_id}')
    async def delete_plugin(plugin_id: str):
        """Delete a plugin."""
        import shutil

        if _plugin_manager is None:
            raise HTTPException(status_code=503, detail='Plugin system not initialized')

        plugin = _plugin_manager.get_plugin(plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail='Plugin not found')

        # Get the actual plugin directory from the plugin instance
        plugin_dir = plugin.plugin_dir

        # Disable the plugin first if enabled
        if plugin.enabled:
            await _plugin_manager.disable_plugin(plugin_id)

        # Unload the plugin
        await _plugin_manager._unload_plugin(plugin_id)

        # Remove from plugins dict
        if plugin_id in _plugin_manager.plugins:
            del _plugin_manager.plugins[plugin_id]

        # Delete the plugin directory
        if plugin_dir and plugin_dir.exists():
            try:
                shutil.rmtree(plugin_dir)
                logger.info(f'Deleted plugin directory: {plugin_dir}')
            except Exception as e:
                logger.error(f'Failed to delete plugin directory: {e}')
                raise HTTPException(status_code=500, detail=f'Failed to delete plugin files: {e}')

        # Remove from enabled list in config if present
        config = get_config()
        if plugin_id in config.enabled_plugins:
            config.enabled_plugins.remove(plugin_id)
        if plugin_id in config.plugin_settings:
            del config.plugin_settings[plugin_id]
        config.save()

        return {'status': 'ok', 'message': f'Plugin "{plugin_id}" deleted successfully'}

    @fastapi_app.post('/plugins/upload')
    async def upload_plugin(file: UploadFile = File(...)):
        """Upload and install a plugin."""
        import zipfile
        import io
        import shutil

        if _plugin_manager is None:
            raise HTTPException(status_code=503, detail='Plugin system not initialized')

        if not file.filename or not file.filename.endswith('.zip'):
            raise HTTPException(status_code=400, detail='Plugin must be a ZIP file')

        try:
            content = await file.read()
            zip_buffer = io.BytesIO(content)

            with zipfile.ZipFile(zip_buffer, 'r') as zf:
                # Check for plugin.py in the archive
                file_list = zf.namelist()

                # Find the plugin directory (may be nested)
                plugin_py_paths = [f for f in file_list if f.endswith('plugin.py')]
                if not plugin_py_paths:
                    raise HTTPException(status_code=400, detail='No plugin.py found in ZIP')

                # Use the first plugin.py found
                plugin_py = plugin_py_paths[0]
                plugin_dir_name = plugin_py.rsplit('/', 1)[0] if '/' in plugin_py else ''

                # Determine extraction target
                if plugin_dir_name:
                    target_name = plugin_dir_name.split('/')[0]
                else:
                    # Use filename without .zip
                    target_name = file.filename[:-4]

                target_dir = _plugin_manager.plugins_dir / target_name

                # Remove existing if present
                if target_dir.exists():
                    shutil.rmtree(target_dir)

                # Extract
                target_dir.mkdir(parents=True, exist_ok=True)
                for member in zf.namelist():
                    # Remove top-level directory prefix if present
                    if plugin_dir_name and member.startswith(plugin_dir_name + '/'):
                        target_path = target_dir / member[len(plugin_dir_name) + 1:]
                    elif plugin_dir_name and member == plugin_dir_name:
                        continue  # Skip the directory entry itself
                    else:
                        target_path = target_dir / member

                    if member.endswith('/'):
                        target_path.mkdir(parents=True, exist_ok=True)
                    else:
                        target_path.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, open(target_path, 'wb') as dst:
                            dst.write(src.read())

            # Read the manifest to get the actual plugin ID before reloading
            # (the folder name may differ from the plugin ID in manifest)
            manifest_path = target_dir / 'manifest.json'
            plugin_id = target_name  # Default to folder name
            plugin_name = target_name

            if manifest_path.exists():
                try:
                    import json
                    manifest = json.loads(manifest_path.read_text())
                    plugin_id = manifest.get('id', target_name)
                    plugin_name = manifest.get('name', plugin_id)
                except Exception as e:
                    logger.warning(f'Failed to read manifest: {e}')

            # Reload plugins to pick up the new one
            await _plugin_manager.reload_plugins()

            # Get the actual plugin name from the loaded plugin (may have been updated)
            plugin = _plugin_manager.get_plugin(plugin_id)
            if plugin:
                plugin_name = plugin.name

            return {
                'status': 'ok',
                'plugin_id': plugin_id,
                'name': plugin_name,
                'message': f'Plugin "{plugin_name}" installed successfully'
            }

        except zipfile.BadZipFile:
            raise HTTPException(status_code=400, detail='Invalid ZIP file')
        except Exception as e:
            logger.error(f'Error installing plugin: {e}')
            raise HTTPException(status_code=500, detail=f'Failed to install plugin: {e}')

    # --- Legacy endpoints for compatibility ---

    @fastapi_app.post('/api/play')
    async def legacy_play(request: PlayRequest = None):
        theme_name = request.theme if request else None
        _app_instance.play(theme_name)
        return {'status': 'ok', 'playing': _app_instance.current_theme}

    @fastapi_app.post('/api/stop')
    async def legacy_stop():
        _app_instance.stop()
        # Mark all sessions as stopped
        for session in _sessions.values():
            session.is_playing = False
        return {'status': 'ok'}

    @fastapi_app.post('/api/pause')
    async def legacy_pause():
        _app_instance.pause()
        return {'status': 'ok'}

    @fastapi_app.post('/api/volume')
    async def legacy_set_volume(request: VolumeRequest):
        _app_instance.set_volume(request.volume / 100.0)
        return {'status': 'ok', 'volume': _app_instance.master_volume}

    @fastapi_app.get('/api/devices')
    async def get_devices():
        devices = _app_instance.list_audio_devices()
        current = _app_instance._current_device
        return {
            'devices': [
                {
                    'id': d.id,
                    'name': d.name,
                    'channels': d.channels,
                    'is_default': d.is_default,
                    'is_current': d.id == (current.id if current else None)
                }
                for d in devices
            ]
        }

    @fastapi_app.post('/api/devices/{device_id}')
    async def set_device(device_id: int):
        try:
            _app_instance.set_audio_device(device_id)
            return {'status': 'ok'}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    # --- Update System Endpoints ---

    @fastapi_app.get('/api/update/check')
    async def check_for_update():
        """Check for available updates."""
        from sonorium.update import check_for_updates, get_current_version

        release = check_for_updates()
        if release:
            return {
                'update_available': True,
                'current_version': get_current_version(),
                'latest_version': release.version,
                'release_name': release.name,
                'release_notes': release.body,
                'download_url': release.download_url,
                'download_size': release.download_size,
                'html_url': release.html_url,
            }
        else:
            return {
                'update_available': False,
                'current_version': get_current_version(),
            }

    @fastapi_app.post('/api/update/install')
    async def install_update():
        """Download and install an update."""
        from sonorium.update import check_for_updates, launch_updater

        release = check_for_updates()
        if not release:
            raise HTTPException(status_code=404, detail='No update available')

        if launch_updater(release, launch_after=True):
            # App will exit - frontend should handle this
            return {
                'status': 'ok',
                'message': 'Update started. The application will restart.',
                'version': release.version,
            }
        else:
            raise HTTPException(status_code=500, detail='Failed to launch updater')

    @fastapi_app.post('/api/update/ignore')
    async def ignore_update(version: str = None):
        """Ignore a specific version (don't prompt again)."""
        from sonorium.update import UpdateChecker, check_for_updates

        config = get_config()
        checker = UpdateChecker(Path(config.audio_path).parent)

        if not version:
            release = check_for_updates()
            if release:
                version = release.version

        if version:
            checker.ignore_version(version)
            return {'status': 'ok', 'ignored_version': version}
        else:
            raise HTTPException(status_code=400, detail='No version to ignore')

    @fastapi_app.post('/api/update/remind-later')
    async def remind_later():
        """Remind about update later."""
        from sonorium.update import UpdateChecker

        config = get_config()
        checker = UpdateChecker(Path(config.audio_path).parent)
        checker.remind_later()

        return {'status': 'ok', 'message': 'Will remind in 24 hours'}

    @fastapi_app.get('/api/version')
    async def get_version():
        """Get current application version."""
        from sonorium.update import get_current_version
        return {'version': get_current_version()}

    return fastapi_app
