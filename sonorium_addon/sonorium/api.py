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
        self._channel_manager = None
        self._cycle_manager = None
        self._mqtt_manager = None
        
        # Register startup event to initialize v2
        @self.app.on_event("startup")
        async def startup_event():
            logger.info("FastAPI startup event triggered")
            await self.initialize_v2()
        
        # Register shutdown event to stop cycle manager
        @self.app.on_event("shutdown")
        async def shutdown_event():
            logger.info("FastAPI shutdown event triggered")
            await self.shutdown_v2()

    def get_endpoints(self):
        # IMPORTANT: More specific routes must come BEFORE catch-all routes!
        # /stream/channel{n} must be registered before /stream/{id}
        endpoints = [
            # Web UI
            api.Endpoint(method_http=self.app.get, path='/', method=self.web_ui),
            api.Endpoint(method_http=self.app.get, path='/v1', method=self.legacy_ui),
            
            # Streaming - channel-based (new) - MUST come before theme-based!
            api.Endpoint(method_http=self.app.get, path='/stream/channel{channel_id:int}', method=self.stream_channel),
            
            # Streaming - theme-based (legacy, still supported)
            api.Endpoint(method_http=self.app.get, path='/stream/{id}', method=self.stream),
            
            # Theme API
            api.Endpoint(method_http=self.app.get, path='/api/themes', method=self.list_themes),
            api.Endpoint(method_http=self.app.get, path='/api/themes/{theme_id}', method=self.get_theme),
            api.Endpoint(method_http=self.app.get, path='/api/status', method=self.status),
            
            # Channel API
            api.Endpoint(method_http=self.app.get, path='/api/channels', method=self.list_channels),
            api.Endpoint(method_http=self.app.get, path='/api/channels/{channel_id}', method=self.get_channel),
        ]
        return endpoints
    
    async def initialize_v2(self):
        """Initialize v2 components after MQTT client is ready."""
        if self._v2_initialized:
            return
        
        try:
            from sonorium.core.state import StateStore
            from sonorium.core.session_manager import SessionManager
            from sonorium.core.group_manager import GroupManager
            from sonorium.core.channel import ChannelManager
            from sonorium.core.cycle_manager import CycleManager
            from sonorium.ha.registry import HARegistry
            from sonorium.ha.media_controller import HAMediaController
            from sonorium.web.api_v2 import create_api_router
            from sonorium.settings import settings
            
            logger.info("Initializing Sonorium v2 components...")
            
            # Initialize state store
            self._state_store = StateStore()
            self._state_store.load()
            logger.info(f"  State loaded: {len(self._state_store.sessions)} sessions, {len(self._state_store.speaker_groups)} groups")
            
            # Initialize channel manager
            max_channels = getattr(settings, 'max_channels', 6)
            self._channel_manager = ChannelManager(max_channels=max_channels)
            logger.info(f"  Channel manager: {max_channels} channels available")
            
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
            
            # Use configured stream URL (from SONORIUM__STREAM_URL env var)
            stream_base_url = settings.stream_url
            logger.info(f"  Stream base URL: {stream_base_url}")
            
            # Initialize cycle manager
            self._cycle_manager = CycleManager(
                session_manager=None,  # Will set after session_manager is created
                themes=self.client.device.themes,
                check_interval=10.0,  # Check every 10 seconds
            )
            
            # Initialize session manager (with cycle manager)
            self._session_manager = SessionManager(
                self._state_store,
                self._ha_registry,
                self._media_controller,
                stream_base_url,
                channel_manager=self._channel_manager,
                cycle_manager=self._cycle_manager,
                themes=self.client.device.themes,
            )
            
            # Connect cycle manager to session manager
            self._cycle_manager.set_session_manager(self._session_manager)
            
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
                channel_manager=self._channel_manager,
                cycle_manager=self._cycle_manager,
            )
            self.app.include_router(api_router)
            
            # Start cycle manager background task
            await self._cycle_manager.start()
            logger.info("  CycleManager started")
            
            self._v2_initialized = True
            logger.info("  Sonorium v2 initialization complete!")
            
        except ImportError as e:
            logger.error(f"  Failed to import v2 modules: {e}")
        except Exception as e:
            logger.error(f"  Failed to initialize v2 components: {e}")
            import traceback
            traceback.print_exc()
    
    async def shutdown_v2(self):
        """Shutdown v2 components gracefully."""
        if self._cycle_manager:
            await self._cycle_manager.stop()
            logger.info("CycleManager stopped")

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
            total = len(theme.instances)
            is_current = theme == themes.current
            current_class = "current" if is_current else ""
            
            recordings_list = ""
            for inst in theme.instances:
                recordings_list += f'<div class="rec"><span class="status">✓</span> {inst.name}</div>'
            
            theme_cards += f'''
            <div class="theme-card {current_class}">
                <div class="theme-header">
                    <h3>{theme.name}</h3>
                    <span class="track-count">{total} tracks</span>
                </div>
                <div class="recordings">{recordings_list}</div>
                <div class="theme-actions">
                    <button onclick="playTheme('{theme.id}')" class="play">▶ Play in Browser</button>
                </div>
                <div class="stream-url">Stream: {theme.url}</div>
            </div>
            '''
        
        v2_link = ""
        if self._v2_initialized:
            v2_link = '<p class="version-switch"><a href="/">→ Use the v2 UI with multi-zone support</a></p>'
        
        html = f'''<!DOCTYPE html>
<html>
<head>
    <title>Sonorium {__version__}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
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
            color: #00ff88;
        }}
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
        <p class="subtitle">Ambient Soundscape Mixer</p>
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
        
        function playTheme(themeId) {{
            window.open('/stream/' + themeId, '_blank');
            showStatus('Opening audio stream...');
        }}
    </script>
</body>
</html>'''
        return HTMLResponse(content=html)

    async def stream(self, id: str):
        """Stream audio by theme ID (legacy endpoint)."""
        theme_def: ThemeDefinition = self.client.device.themes.id[id]
        stream = theme_def.get_stream()
        response = StreamingResponse(stream, media_type="audio/mpeg")
        return response

    async def stream_channel(self, channel_id: int):
        """Stream audio from a channel (new endpoint)."""
        if not self._channel_manager:
            return HTMLResponse(content="Channel system not initialized", status_code=503)
        
        channel = self._channel_manager.get_channel(channel_id)
        if not channel:
            return HTMLResponse(content=f"Channel {channel_id} not found", status_code=404)
        
        stream = channel.get_stream()
        response = StreamingResponse(stream, media_type="audio/mpeg")
        return response

    async def list_themes(self):
        """List all available themes."""
        device = self.client.device
        themes = []
        for theme in device.themes:
            themes.append({
                "id": theme.id,
                "name": theme.name,
                "track_count": len(theme.instances),
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
            "track_count": len(theme.instances),
            "url": theme.url,
            "tracks": [{"name": i.name} for i in theme.instances],
        }

    async def list_channels(self):
        """List all available channels."""
        if not self._channel_manager:
            return {"error": "Channel system not initialized"}
        return self._channel_manager.list_channels()

    async def get_channel(self, channel_id: int):
        """Get channel details."""
        if not self._channel_manager:
            return {"error": "Channel system not initialized"}
        
        channel = self._channel_manager.get_channel(channel_id)
        if not channel:
            return {"error": f"Channel {channel_id} not found"}
        
        return channel.to_dict()

    async def status(self):
        """Get current status"""
        device = self.client.device
        themes_data = []
        for theme in device.themes:
            themes_data.append({
                "name": theme.name,
                "id": theme.id,
                "track_count": len(theme.instances),
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
            if self._channel_manager:
                status_data["channels"] = self._channel_manager.max_channels
                status_data["active_channels"] = self._channel_manager.get_active_count()
            
            # Count sessions with cycling enabled
            cycling_count = sum(
                1 for s in self._state_store.sessions.values() 
                if s.cycle_config and s.cycle_config.enabled
            )
            status_data["cycling_sessions"] = cycling_count
        
        return status_data


if __name__ == '__main__':
    ApiSonorium.launch()