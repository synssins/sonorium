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

from fastapi import FastAPI, Request
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

# Static files directory (CSS, JS)
# Try multiple possible locations
_static_candidates = [
    Path(__file__).parent / "static",  # Relative to app.py
    Path("/app/sonorium/web/static"),  # Docker container path
]
STATIC_DIR = next((p for p in _static_candidates if p.exists()), _static_candidates[0])

# Static files (logo, etc.)
# In Docker container, files are at /app; in development, use relative path
ADDON_DIR = Path("/app") if Path("/app/logo.png").exists() else Path(__file__).parent.parent.parent

# Log paths at module load time
logger.info(f"TEMPLATES_DIR: {TEMPLATES_DIR} (exists: {TEMPLATES_DIR.exists()})")
logger.info(f"STATIC_DIR: {STATIC_DIR} (exists: {STATIC_DIR.exists()})")
logger.info(f"ADDON_DIR: {ADDON_DIR} (exists: {ADDON_DIR.exists()})")


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
        self._plugin_manager = None
        self._initialized = False
        
        # Setup routes
        self._setup_routes()
    
    def _setup_routes(self):
        """Configure all routes."""
        print("[ROUTES] _setup_routes() called", flush=True)

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

        # Explicit route for logo.png - more reliable than StaticFiles mount
        @self.app.get("/logo.png")
        async def serve_logo():
            """Serve the logo file."""
            logo_paths = [
                Path("/app/logo.png"),
                Path(__file__).parent.parent.parent / "logo.png",
            ]
            for logo_path in logo_paths:
                if logo_path.exists():
                    logger.info(f"Serving logo from: {logo_path}")
                    return FileResponse(logo_path, media_type="image/png")
            logger.warning(f"Logo not found. Checked: {logo_paths}")
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Logo not found")

        @self.app.get("/static/logo.png", response_class=FileResponse)
        async def serve_static_logo():
            """Serve logo from /static/logo.png path."""
            return await serve_logo()

        @self.app.get("/icon.png")
        async def serve_icon():
            """Serve the icon file."""
            icon_paths = [
                Path("/app/icon.png"),
                Path(__file__).parent.parent.parent / "icon.png",
            ]
            for icon_path in icon_paths:
                if icon_path.exists():
                    return FileResponse(icon_path, media_type="image/png")
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail="Icon not found")

        print("[ROUTES] After icon route, before debug endpoint", flush=True)

        # Debug endpoint to check static files
        @self.app.get("/debug/static")
        async def debug_static():
            """Debug endpoint to check static file paths."""
            result = {
                "STATIC_DIR": str(STATIC_DIR),
                "exists": STATIC_DIR.exists(),
                "is_dir": STATIC_DIR.is_dir() if STATIC_DIR.exists() else False,
                "contents": [],
                "candidates_checked": [str(p) for p in _static_candidates],
            }
            if STATIC_DIR.exists():
                try:
                    result["contents"] = [str(p.relative_to(STATIC_DIR)) for p in STATIC_DIR.rglob("*") if p.is_file()]
                except Exception as e:
                    result["error"] = str(e)
            return result

        # Mount static files (CSS, JS)
        print(f"[STATIC] About to mount. STATIC_DIR={STATIC_DIR}, exists={STATIC_DIR.exists()}", flush=True)
        logger.info(f"About to mount static files. STATIC_DIR={STATIC_DIR}, exists={STATIC_DIR.exists()}")
        if STATIC_DIR.exists():
            # List contents for debugging
            try:
                contents = list(STATIC_DIR.rglob("*"))
                file_list = [str(p.relative_to(STATIC_DIR)) for p in contents if p.is_file()]
                print(f"[STATIC] Dir contains {len(file_list)} files: {file_list}", flush=True)
                logger.info(f"Static dir contains {len(file_list)} files: {file_list}")
            except Exception as e:
                print(f"[STATIC] Error listing dir: {e}", flush=True)
                logger.warning(f"Could not list static dir: {e}")

            try:
                self.app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
                print(f"[STATIC] Successfully mounted from: {STATIC_DIR}", flush=True)
                logger.info(f"Successfully mounted static files from: {STATIC_DIR}")
            except Exception as e:
                print(f"[STATIC] Mount failed: {e}", flush=True)
                logger.error(f"Failed to mount static files: {e}")
        else:
            print(f"[STATIC] Directory not found: {STATIC_DIR}", flush=True)
            logger.error(f"Static directory not found: {STATIC_DIR}")
            logger.error(f"Checked candidates: {_static_candidates}")

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
            """List all available themes with metadata, including empty folders."""
            import json

            # Get favorites from state if available
            favorites = []
            if self._state_store:
                favorites = self._state_store.settings.favorite_themes

            themes = []
            seen_folders = set()

            # First, add themes loaded by the device (have audio files)
            if self.mqtt_client:
                for theme in self.mqtt_client.device.themes:
                    enabled_count = sum(1 for i in theme.instances if i.is_enabled)

                    # Try to read metadata.json from theme folder
                    metadata = self._read_theme_metadata(theme.id)

                    # Track the actual folder name
                    theme_folder = self._find_theme_folder(theme.id)
                    if theme_folder:
                        seen_folders.add(theme_folder.name)

                    themes.append({
                        "id": theme.id,
                        "name": theme.name,
                        "total_tracks": len(theme.instances),
                        "enabled_tracks": enabled_count,
                        "url": theme.url,
                        "description": metadata.get("description", ""),
                        "icon": metadata.get("icon", ""),
                        "is_favorite": theme.id in favorites,
                        "has_audio": True,
                    })

            # Then scan for empty theme folders
            media_paths = [
                Path("/media/sonorium"),
                Path("/share/sonorium"),
            ]

            audio_extensions = {'.mp3', '.wav', '.flac', '.ogg'}

            for mp in media_paths:
                if not mp.exists():
                    continue

                for folder in mp.iterdir():
                    if not folder.is_dir() or folder.name in seen_folders:
                        continue

                    # Count audio files in this folder
                    audio_files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in audio_extensions]

                    # Skip if it has audio (already added above)
                    if audio_files:
                        continue

                    # Generate sanitized ID like ThemeDefinition does
                    sanitized_id = folder.name.lower().replace(' ', '-').replace('_', '-')
                    sanitized_id = ''.join(c for c in sanitized_id if c.isalnum() or c == '-')
                    while '--' in sanitized_id:
                        sanitized_id = sanitized_id.replace('--', '-')
                    sanitized_id = sanitized_id.strip('-')

                    # Read metadata if exists
                    metadata = {}
                    meta_path = folder / "metadata.json"
                    if meta_path.exists():
                        try:
                            metadata = json.loads(meta_path.read_text())
                        except Exception:
                            pass

                    themes.append({
                        "id": sanitized_id,
                        "name": folder.name,
                        "total_tracks": 0,
                        "enabled_tracks": 0,
                        "url": "",
                        "description": metadata.get("description", ""),
                        "icon": metadata.get("icon", ""),
                        "is_favorite": sanitized_id in favorites,
                        "has_audio": False,
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

        @self.app.post("/api/themes/{theme_id}/favorite")
        async def toggle_favorite(theme_id: str):
            """Toggle favorite status for a theme."""
            if not self._state_store:
                return {"error": "State not available"}

            favorites = self._state_store.settings.favorite_themes
            if theme_id in favorites:
                favorites.remove(theme_id)
                is_favorite = False
            else:
                favorites.append(theme_id)
                is_favorite = True

            self._state_store.save()
            return {"theme_id": theme_id, "is_favorite": is_favorite}

        @self.app.put("/api/themes/{theme_id}/metadata")
        async def update_metadata(theme_id: str, request: Request):
            """Update theme metadata (description, etc.)."""
            if not self.mqtt_client:
                return {"error": "Not available"}

            theme = self.mqtt_client.device.themes.id.get(theme_id)
            if not theme:
                return {"error": "Theme not found"}

            # Parse JSON body
            try:
                body = await request.json()
            except Exception:
                return {"error": "Invalid JSON body"}

            # Read existing metadata and merge
            metadata = self._read_theme_metadata(theme_id)
            if "description" in body:
                metadata["description"] = body["description"]

            # Write back
            if self._write_theme_metadata(theme_id, metadata):
                return {"status": "ok", "metadata": metadata}
            return {"error": "Could not write metadata"}

        # Note: Theme create, upload, delete, and metadata update are now in api_v2.py

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
            
            # Use the resolved stream URL from settings (has correct IP, not localhost)
            # Cast devices pull the stream, so they need a reachable IP address
            stream_base_url = settings.stream_url
            logger.info(f"Using stream base URL: {stream_base_url}")
            
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

            # Initialize plugin manager
            try:
                from sonorium.plugins.manager import PluginManager
                # Get audio path from device if available
                audio_path = None
                if hasattr(self, 'mqtt_client') and self.mqtt_client and hasattr(self.mqtt_client, 'device'):
                    audio_path = self.mqtt_client.device.path_audio
                if audio_path is None:
                    audio_path = Path("/media/sonorium")

                self._plugin_manager = PluginManager(self._state_store, audio_path=audio_path)
                # Note: Plugin initialization is async, so we start it in background
                import asyncio
                asyncio.create_task(self._plugin_manager.initialize())
                logger.info(f"Plugin manager created with audio_path={audio_path}, initialization started")
            except Exception as e:
                logger.warning(f"Failed to initialize plugin manager: {e}")
                self._plugin_manager = None

            # Create and mount v2 API router
            api_router = create_api_router(
                session_manager=self._session_manager,
                group_manager=self._group_manager,
                ha_registry=self._ha_registry,
                state_store=self._state_store,
                plugin_manager=self._plugin_manager,
            )
            self.app.include_router(api_router)

            self._initialized = True
            logger.info("Sonorium v2 components initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize v2 components: {e}")
            # Continue with v1 functionality only

    def _find_theme_folder(self, theme_id: str) -> Path | None:
        """Find theme folder by ID, handling sanitized names."""
        media_paths = [
            Path("/media/sonorium"),
            Path("/share/sonorium"),
        ]

        for mp in media_paths:
            if not mp.exists():
                continue

            # Try exact match first
            exact_path = mp / theme_id
            if exact_path.exists():
                return exact_path

            # Try to find by comparing sanitized folder names
            for folder in mp.iterdir():
                if folder.is_dir():
                    # Sanitize the folder name the same way ThemeDefinition does
                    sanitized = folder.name.lower().replace(' ', '-').replace('_', '-')
                    # Remove non-alphanumeric except dashes
                    sanitized = ''.join(c for c in sanitized if c.isalnum() or c == '-')
                    # Collapse multiple dashes
                    while '--' in sanitized:
                        sanitized = sanitized.replace('--', '-')
                    sanitized = sanitized.strip('-')

                    if sanitized == theme_id or sanitized == theme_id.replace('_', '-'):
                        return folder

        return None

    def _read_theme_metadata(self, theme_id: str) -> dict:
        """Read metadata.json from a theme folder."""
        import json
        theme_folder = self._find_theme_folder(theme_id)
        if theme_folder:
            meta_path = theme_folder / "metadata.json"
            if meta_path.exists():
                try:
                    return json.loads(meta_path.read_text())
                except Exception as e:
                    logger.debug(f"Failed to read metadata from {meta_path}: {e}")
        return {}

    def _write_theme_metadata(self, theme_id: str, metadata: dict) -> bool:
        """Write metadata.json to a theme folder."""
        import json
        theme_folder = self._find_theme_folder(theme_id)
        if theme_folder:
            try:
                meta_path = theme_folder / "metadata.json"
                meta_path.write_text(json.dumps(metadata, indent=2))
                return True
            except Exception as e:
                logger.error(f"Failed to write metadata to {meta_path}: {e}")
        return False

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

    @property
    def plugin_manager(self):
        return self._plugin_manager


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
