import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse

from sonorium.theme import ThemeDefinition
from sonorium.version import __version__
from sonorium.obs import logger
from fmtr.tools import api, mqtt

for name in ["uvicorn.access", "uvicorn.error", "uvicorn"]:
    _logger = logging.getLogger(name)
    _logger.handlers.clear()
    _logger.propagate = False


# Template directory
TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"


class ApiSonorium(api.Base):
    TITLE = f'Sonorium {__version__} Streaming API'
    URL_DOCS = '/docs'

    def __init__(self, client: mqtt.Client):
        super().__init__()
        self.client = client
        
        # v2 components (initialized lazily)
        self._v2_initialized = False
        self._state_store = None
        self._ha_registry = None
        self._media_controller = None
        self._session_manager = None
        self._group_manager = None
        self._mqtt_manager = None
        
        # Register startup event to initialize v2
        @self.app.on_event("startup")
        async def startup_event():
            logger.info("FastAPI startup event triggered")
            self.initialize_v2()

    def get_endpoints(self):
        endpoints = [
            # Web UI
            api.Endpoint(method_http=self.app.get, path='/', method=self.web_ui),
            api.Endpoint(method_http=self.app.get, path='/v1', method=self.legacy_ui),
            
            # Streaming
            api.Endpoint(method_http=self.app.get, path='/stream/{id}', method=self.stream),
            
            # Theme API (used by both v1 and v2 UI)
            api.Endpoint(method_http=self.app.get, path='/api/themes', method=self.list_themes),
            api.Endpoint(method_http=self.app.get, path='/api/themes/{theme_id}', method=self.get_theme),
            api.Endpoint(method_http=self.app.post, path='/api/enable_all/{theme_id}', method=self.enable_all),
            api.Endpoint(method_http=self.app.post, path='/api/disable_all/{theme_id}', method=self.disable_all),
            api.Endpoint(method_http=self.app.get, path='/api/status', method=self.status),
        ]
        return endpoints
    
    def initialize_v2(self):
        """
        Initialize v2 components after MQTT client is ready.
        Call this after themes are loaded.
        """
        if self._v2_initialized:
            return
        
        try:
            from sonorium.core.state import StateStore
            from sonorium.core.session_manager import SessionManager
            from sonorium.core.group_manager import GroupManager
            from sonorium.ha.registry import HARegistry
            from sonorium.ha.media_controller import HAMediaController
            from sonorium.web.api_v2 import create_api_router
            from sonorium.settings import settings
            
            logger.info("Initializing Sonorium v2 components...")
            
            # Initialize state store
            self._state_store = StateStore()
            self._state_store.load()
            logger.info(f"  State loaded: {len(self._state_store.sessions)} sessions, {len(self._state_store.speaker_groups)} groups")
            
            # Initialize HA registry
            api_url = f"{settings.ha_supervisor_api.replace('/core', '')}/core/api"
            self._ha_registry = HARegistry(api_url, settings.token)
            try:
                self._ha_registry.refresh()
                logger.info(f"  HA registry loaded: {len(self._ha_registry.hierarchy.floors)} floors")
            except Exception as e:
                logger.warning(f"  Could not load HA registry (floors/areas may not work): {e}")
            
            # Initialize media controller
            self._media_controller = HAMediaController(api_url, settings.token)
            
            # Determine stream base URL
            # In addon context, use ingress URL or localhost
            port = getattr(settings, 'port', 8080)
            stream_base_url = f"http://localhost:{port}"
            
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
            
            self._v2_initialized = True
            logger.info("  Sonorium v2 initialization complete!")
            
        except ImportError as e:
            logger.error(f"  Failed to import v2 modules: {e}")
        except Exception as e:
            logger.error(f"  Failed to initialize v2 components: {e}")
            import traceback
            traceback.print_exc()

    async def web_ui(self):
        """Serve the main web UI (v2 if available, else v1)."""
        template_path = TEMPLATES_DIR / "index.html"
        if template_path.exists() and self._v2_initialized:
            return HTMLResponse(content=template_path.read_text())
        else:
            return await self.legacy_ui()

    async def legacy_ui(self):
        """Serve the legacy v1 web UI."""
        device = self.client.device
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
        
        v2_link = ""
        if self._v2_initialized:
            v2_link = '<p class="version-switch"><a href="/">→ Try the new v2 UI with multi-zone support</a></p>'
        
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
        {v2_link}
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

    async def stream(self, id: str):
        theme_def: ThemeDefinition = self.client.device.themes.id[id]
        stream = theme_def.get_stream()
        response = StreamingResponse(stream, media_type="audio/mpeg")
        return response

    async def list_themes(self):
        """List all available themes."""
        device = self.client.device
        themes = []
        for theme in device.themes:
            enabled_count = sum(1 for i in theme.instances if i.is_enabled)
            themes.append({
                "id": theme.id,
                "name": theme.name,
                "total_tracks": len(theme.instances),
                "enabled_tracks": enabled_count,
                "url": theme.url,
            })
        return themes

    async def get_theme(self, theme_id: str):
        """Get theme details."""
        theme = self.client.device.themes.id.get(theme_id)
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

    async def enable_all(self, theme_id: str):
        """Enable all recordings in a theme"""
        theme_def: ThemeDefinition = self.client.device.themes.id[theme_id]
        for instance in theme_def.instances:
            instance.is_enabled = True
        return {"status": "ok", "theme": theme_id, "enabled": len(theme_def.instances)}

    async def disable_all(self, theme_id: str):
        """Disable all recordings in a theme"""
        theme_def: ThemeDefinition = self.client.device.themes.id[theme_id]
        for instance in theme_def.instances:
            instance.is_enabled = False
        return {"status": "ok", "theme": theme_id, "disabled": len(theme_def.instances)}

    async def status(self):
        """Get current status"""
        device = self.client.device
        themes_data = []
        for theme in device.themes:
            enabled_count = sum(1 for i in theme.instances if i.is_enabled)
            themes_data.append({
                "name": theme.name,
                "id": theme.id,
                "total_tracks": len(theme.instances),
                "enabled_tracks": enabled_count,
                "url": theme.url
            })
        
        status_data = {
            "version": __version__,
            "current_theme": device.themes.current.name if device.themes.current else None,
            "themes": themes_data,
            "v2_enabled": self._v2_initialized,
        }
        
        # Add v2 status if initialized
        if self._v2_initialized:
            status_data["sessions"] = len(self._state_store.sessions)
            status_data["speaker_groups"] = len(self._state_store.speaker_groups)
        
        return status_data


if __name__ == '__main__':
    ApiSonorium.launch()
