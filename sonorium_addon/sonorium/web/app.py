"""
Sonorium v2 Application Factory

Creates and configures the FastAPI application with:
- Session and group management
- Speaker hierarchy from Home Assistant
- Media player control
- Streaming endpoints
- Web UI
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from sonorium.version import __version__
from sonorium.obs import logger

if TYPE_CHECKING:
    from fmtr.tools import mqtt


# Suppress uvicorn access logs
for name in ["uvicorn.access", "uvicorn.error", "uvicorn"]:
    _logger = logging.getLogger(name)
    _logger.handlers.clear()
    _logger.propagate = False


# Template directory
TEMPLATES_DIR = Path(__file__).parent / "templates"


class SonoriumApp:
    """
    Sonorium v2 FastAPI Application.
    
    Integrates:
    - Session management (multi-zone playback)
    - Speaker groups
    - Home Assistant registry
    - Audio streaming
    - Web UI
    """
    
    def __init__(self, mqtt_client: mqtt.Client = None):
        """
        Initialize the Sonorium application.
        
        Args:
            mqtt_client: MQTT client for legacy v1 functionality
        """
        self.mqtt_client = mqtt_client
        self.app = FastAPI(
            title=f"Sonorium {__version__}",
            version=__version__,
            docs_url="/docs",
        )
        
        # Components (initialized lazily)
        self._state_store = None
        self._ha_registry = None
        self._media_controller = None
        self._session_manager = None
        self._group_manager = None
        self._initialized = False
        
        # Setup routes
        self._setup_routes()
    
    def _setup_routes(self):
        """Configure all routes."""
        
        # --- Web UI ---
        
        @self.app.get("/", response_class=HTMLResponse)
        async def index():
            """Serve the main web UI."""
            template_path = TEMPLATES_DIR / "index.html"
            if template_path.exists():
                return HTMLResponse(content=template_path.read_text())
            else:
                # Fallback to legacy UI
                return await self._legacy_ui()
        
        @self.app.get("/v1", response_class=HTMLResponse)
        async def legacy_index():
            """Serve the legacy v1 web UI."""
            return await self._legacy_ui()
        
        # --- Streaming (unchanged from v1) ---
        
        @self.app.get("/stream/{theme_id}")
        async def stream(theme_id: str):
            """Stream audio for a theme."""
            if not self.mqtt_client:
                return {"error": "Streaming not available"}
            
            theme_def = self.mqtt_client.device.themes.id.get(theme_id)
            if not theme_def:
                return {"error": f"Theme '{theme_id}' not found"}
            
            audio_stream = theme_def.get_stream()
            return StreamingResponse(audio_stream, media_type="audio/mpeg")
        
        # --- Theme API (for web UI) ---
        
        @self.app.get("/api/themes")
        async def list_themes():
            """List all available themes."""
            if not self.mqtt_client:
                return []
            
            themes = []
            for theme in self.mqtt_client.device.themes:
                enabled_count = sum(1 for i in theme.instances if i.is_enabled)
                themes.append({
                    "id": theme.id,
                    "name": theme.name,
                    "total_tracks": len(theme.instances),
                    "enabled_tracks": enabled_count,
                    "url": theme.url,
                })
            return themes
        
        @self.app.get("/api/themes/{theme_id}")
        async def get_theme(theme_id: str):
            """Get theme details."""
            if not self.mqtt_client:
                return {"error": "Not available"}
            
            theme = self.mqtt_client.device.themes.id.get(theme_id)
            if not theme:
                return {"error": "Theme not found"}
            
            return {
                "id": theme.id,
                "name": theme.name,
                "total_tracks": len(theme.instances),
                "enabled_tracks": sum(1 for i in theme.instances if i.is_enabled),
                "url": theme.url,
                "tracks": [
                    {"name": i.name, "enabled": i.is_enabled}
                    for i in theme.instances
                ],
            }
        
        # --- Legacy v1 API endpoints ---
        
        @self.app.post("/api/enable_all/{theme_id}")
        async def enable_all(theme_id: str):
            """Enable all recordings in a theme."""
            if not self.mqtt_client:
                return {"error": "Not available"}
            
            theme = self.mqtt_client.device.themes.id.get(theme_id)
            if not theme:
                return {"error": "Theme not found"}
            
            for instance in theme.instances:
                instance.is_enabled = True
            return {"status": "ok", "theme": theme_id, "enabled": len(theme.instances)}
        
        @self.app.post("/api/disable_all/{theme_id}")
        async def disable_all(theme_id: str):
            """Disable all recordings in a theme."""
            if not self.mqtt_client:
                return {"error": "Not available"}
            
            theme = self.mqtt_client.device.themes.id.get(theme_id)
            if not theme:
                return {"error": "Theme not found"}
            
            for instance in theme.instances:
                instance.is_enabled = False
            return {"status": "ok", "theme": theme_id, "disabled": len(theme.instances)}
        
        @self.app.get("/api/status")
        async def status():
            """Get current status."""
            if not self.mqtt_client:
                return {"version": __version__, "themes": []}
            
            device = self.mqtt_client.device
            themes_data = []
            for theme in device.themes:
                enabled_count = sum(1 for i in theme.instances if i.is_enabled)
                themes_data.append({
                    "name": theme.name,
                    "id": theme.id,
                    "total_tracks": len(theme.instances),
                    "enabled_tracks": enabled_count,
                    "url": theme.url,
                })
            return {
                "version": __version__,
                "current_theme": device.themes.current.name if device.themes.current else None,
                "themes": themes_data,
            }
    
    def initialize_v2(self, settings=None):
        """
        Initialize v2 components (sessions, groups, HA integration).
        
        Call this after the MQTT client is connected and HA is available.
        """
        if self._initialized:
            return
        
        try:
            from sonorium.core.state import StateStore
            from sonorium.core.session_manager import SessionManager
            from sonorium.core.group_manager import GroupManager
            from sonorium.ha.registry import HARegistry
            from sonorium.ha.media_controller import HAMediaController
            from sonorium.web.api_v2 import create_api_router
            from sonorium.settings import settings as app_settings
            
            settings = settings or app_settings
            
            # Initialize state store
            self._state_store = StateStore()
            self._state_store.load()
            
            # Initialize HA registry
            api_url = f"{settings.ha_supervisor_api.replace('/core', '')}/core/api"
            self._ha_registry = HARegistry(api_url, settings.token)
            self._ha_registry.refresh()
            
            # Initialize media controller
            self._media_controller = HAMediaController(api_url, settings.token)
            
            # Determine stream base URL
            # In addon context, this should be the addon's external URL
            stream_base_url = f"http://localhost:{settings.port if hasattr(settings, 'port') else 8080}"
            
            # Initialize managers
            self._session_manager = SessionManager(
                self._state_store,
                self._ha_registry,
                self._media_controller,
                stream_base_url,
            )
            
            self._group_manager = GroupManager(
                self._state_store,
                self._ha_registry,
            )
            
            # Create and mount v2 API router
            api_router = create_api_router(
                session_manager=self._session_manager,
                group_manager=self._group_manager,
                ha_registry=self._ha_registry,
                state_store=self._state_store,
            )
            self.app.include_router(api_router)
            
            self._initialized = True
            logger.info("Sonorium v2 components initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize v2 components: {e}")
            # Continue with v1 functionality only
    
    async def _legacy_ui(self):
        """Generate the legacy v1 web UI."""
        if not self.mqtt_client:
            return HTMLResponse("<h1>Sonorium</h1><p>Initializing...</p>")
        
        device = self.mqtt_client.device
        themes = device.themes
        
        # Build theme cards
        theme_cards = ""
        for theme in themes:
            enabled_count = sum(1 for inst in theme.instances if inst.is_enabled)
            total = len(theme.instances)
            is_current = theme == themes.current
            current_class = "current" if is_current else ""
            
            recordings_list = ""
            for inst in theme.instances:
                status = "✓" if inst.is_enabled else "○"
                status_class = "enabled" if inst.is_enabled else "disabled"
                recordings_list += f'<div class="rec {status_class}"><span class="status">{status}</span> {inst.name}</div>'
            
            theme_cards += f'''
            <div class="theme-card {current_class}">
                <div class="theme-header">
                    <h3>{theme.name}</h3>
                    <span class="track-count">{enabled_count}/{total} enabled</span>
                </div>
                <div class="recordings">{recordings_list}</div>
                <div class="theme-actions">
                    <button onclick="enableAll('{theme.id}')">Enable All</button>
                    <button onclick="disableAll('{theme.id}')" class="secondary">Disable All</button>
                    <button onclick="playTheme('{theme.id}')" class="play">▶ Play</button>
                </div>
                <div class="stream-url">Stream: {theme.url}</div>
            </div>
            '''
        
        html = f'''<!DOCTYPE html>
<html>
<head>
    <title>Sonorium {__version__}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="10">
    <style>
        * {{ box-sizing: border-box; }}
        body {{ 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            margin: 0;
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{ max-width: 800px; margin: 0 auto; }}
        h1 {{ 
            color: #00d4ff;
            text-align: center;
            margin-bottom: 10px;
        }}
        .subtitle {{
            text-align: center;
            color: #888;
            margin-bottom: 30px;
        }}
        .version-switch {{
            text-align: center;
            margin-bottom: 20px;
        }}
        .version-switch a {{
            color: #00d4ff;
            text-decoration: none;
        }}
        .theme-card {{
            background: rgba(255,255,255,0.08);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
            border: 2px solid transparent;
        }}
        .theme-card.current {{
            border-color: #00d4ff;
        }}
        .theme-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }}
        .theme-header h3 {{
            margin: 0;
            color: #fff;
        }}
        .track-count {{
            color: #00d4ff;
            font-size: 14px;
        }}
        .recordings {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
            gap: 8px;
            margin-bottom: 15px;
            max-height: 200px;
            overflow-y: auto;
        }}
        .rec {{
            padding: 6px 10px;
            background: rgba(0,0,0,0.3);
            border-radius: 6px;
            font-size: 12px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .rec.enabled {{ color: #00ff88; }}
        .rec.disabled {{ color: #666; }}
        .rec .status {{ margin-right: 5px; }}
        .theme-actions {{
            display: flex;
            gap: 10px;
        }}
        button {{
            padding: 10px 16px;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            cursor: pointer;
            background: #00d4ff;
            color: #000;
            font-weight: bold;
        }}
        button:hover {{ opacity: 0.9; }}
        button.secondary {{
            background: rgba(255,255,255,0.2);
            color: #fff;
        }}
        button.play {{
            background: #00ff88;
            flex: 1;
        }}
        .stream-url {{
            margin-top: 10px;
            font-size: 11px;
            color: #666;
            font-family: monospace;
        }}
        .status-msg {{
            position: fixed;
            bottom: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: #00d4ff;
            color: #000;
            padding: 10px 20px;
            border-radius: 8px;
            display: none;
        }}
        .status-msg.show {{ display: block; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🎵 Sonorium {__version__}</h1>
        <p class="subtitle">Ambient Soundscape Mixer • Auto-refreshes every 10s</p>
        <p class="version-switch"><a href="/">→ Try the new v2 UI with multi-zone support</a></p>
        {theme_cards}
    </div>
    <div class="status-msg" id="status"></div>
    
    <script>
        function showStatus(msg) {{
            const el = document.getElementById('status');
            el.textContent = msg;
            el.classList.add('show');
            setTimeout(() => el.classList.remove('show'), 2000);
        }}
        
        async function enableAll(themeId) {{
            await fetch('/api/enable_all/' + themeId, {{method: 'POST'}});
            showStatus('All recordings enabled!');
            setTimeout(() => location.reload(), 500);
        }}
        
        async function disableAll(themeId) {{
            await fetch('/api/disable_all/' + themeId, {{method: 'POST'}});
            showStatus('All recordings disabled');
            setTimeout(() => location.reload(), 500);
        }}
        
        function playTheme(themeId) {{
            window.open('/stream/' + themeId, '_blank');
            showStatus('Opening audio stream...');
        }}
    </script>
</body>
</html>'''
        return HTMLResponse(content=html)
    
    # Properties for accessing components
    
    @property
    def state_store(self):
        return self._state_store
    
    @property
    def session_manager(self):
        return self._session_manager
    
    @property
    def group_manager(self):
        return self._group_manager
    
    @property
    def ha_registry(self):
        return self._ha_registry


def create_app(mqtt_client=None) -> FastAPI:
    """
    Create the Sonorium FastAPI application.
    
    Args:
        mqtt_client: Optional MQTT client for v1 functionality
    
    Returns:
        Configured FastAPI application
    """
    sonorium = SonoriumApp(mqtt_client)
    return sonorium.app


# For direct running
if __name__ == "__main__":
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8080)
