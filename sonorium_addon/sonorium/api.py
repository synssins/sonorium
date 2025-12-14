import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

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

# Static files directory (CSS, JS)
STATIC_DIR = Path(__file__).parent / "web" / "static"

# Static assets (relative to package root)
PACKAGE_ROOT = Path(__file__).parent.parent
LOGO_PATH = PACKAGE_ROOT / "logo.png"


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
        self._plugin_manager = None
        
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

        # Mount static files (CSS, JS) for the web UI
        if STATIC_DIR.exists():
            self.app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
            logger.info(f"Mounted static files from: {STATIC_DIR}")
        else:
            logger.warning(f"Static directory not found: {STATIC_DIR}")

    def get_endpoints(self):
        # IMPORTANT: More specific routes must come BEFORE catch-all routes!
        # /stream/channel{n} must be registered before /stream/{id}
        endpoints = [
            # Web UI
            api.Endpoint(method_http=self.app.get, path='/', method=self.web_ui),
            api.Endpoint(method_http=self.app.get, path='/v1', method=self.legacy_ui),
            api.Endpoint(method_http=self.app.get, path='/logo.png', method=self.serve_logo),
            
            # Streaming - channel-based (new) - MUST come before theme-based!
            api.Endpoint(method_http=self.app.get, path='/stream/channel{channel_id:int}', method=self.stream_channel),
            
            # Streaming - theme-based (legacy, still supported)
            api.Endpoint(method_http=self.app.get, path='/stream/{id}', method=self.stream),
            
            # Theme API
            api.Endpoint(method_http=self.app.get, path='/api/themes', method=self.list_themes),
            api.Endpoint(method_http=self.app.post, path='/api/themes/refresh', method=self.refresh_themes),
            api.Endpoint(method_http=self.app.post, path='/api/themes/{theme_id}/favorite', method=self.toggle_favorite),
            api.Endpoint(method_http=self.app.get, path='/api/themes/{theme_id}', method=self.get_theme),

            # Track Mixer API
            api.Endpoint(method_http=self.app.get, path='/api/themes/{theme_id}/tracks', method=self.get_theme_tracks),
            api.Endpoint(method_http=self.app.get, path='/api/themes/{theme_id}/tracks/{track_name}/audio', method=self.get_track_audio),
            api.Endpoint(method_http=self.app.put, path='/api/themes/{theme_id}/tracks/{track_name}/presence', method=self.set_track_presence),
            api.Endpoint(method_http=self.app.put, path='/api/themes/{theme_id}/tracks/{track_name}/muted', method=self.set_track_muted),
            api.Endpoint(method_http=self.app.put, path='/api/themes/{theme_id}/tracks/{track_name}/volume', method=self.set_track_volume),
            api.Endpoint(method_http=self.app.put, path='/api/themes/{theme_id}/tracks/{track_name}/playback_mode', method=self.set_track_playback_mode),
            api.Endpoint(method_http=self.app.put, path='/api/themes/{theme_id}/tracks/{track_name}/seamless_loop', method=self.set_track_seamless_loop),
            api.Endpoint(method_http=self.app.post, path='/api/themes/{theme_id}/tracks/reset', method=self.reset_theme_tracks),

            # Category API
            api.Endpoint(method_http=self.app.get, path='/api/categories', method=self.list_categories),
            api.Endpoint(method_http=self.app.post, path='/api/categories', method=self.create_category),
            api.Endpoint(method_http=self.app.delete, path='/api/categories/{category_name}', method=self.delete_category),
            api.Endpoint(method_http=self.app.post, path='/api/themes/{theme_id}/categories', method=self.set_theme_categories),

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

            # Apply saved track settings to themes
            self._apply_saved_track_settings()

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

            # Initialize plugin manager
            try:
                from sonorium.plugins.manager import PluginManager
                audio_path = self.client.device.path_audio if self.client and self.client.device else None
                self._plugin_manager = PluginManager(
                    self._state_store,
                    audio_path=audio_path,
                )
                await self._plugin_manager.initialize()
                logger.info(f"  Plugin manager: {len(self._plugin_manager.plugins)} plugin(s) loaded")
            except Exception as e:
                logger.warning(f"  Failed to initialize plugin manager: {e}")
                self._plugin_manager = None

            # Create and mount v2 API router
            api_router = create_api_router(
                session_manager=self._session_manager,
                group_manager=self._group_manager,
                ha_registry=self._ha_registry,
                state_store=self._state_store,
                channel_manager=self._channel_manager,
                cycle_manager=self._cycle_manager,
                plugin_manager=self._plugin_manager,
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
    
    def _apply_saved_track_settings(self):
        """Apply saved track settings (mute, volume, presence, etc.) to theme instances on startup."""
        if not self._state_store:
            return

        from sonorium.recording import PlaybackMode

        device = self.client.device
        if not device.themes:
            return

        logger.info("  Applying saved track settings to themes...")
        for theme in device.themes:
            if not theme.instances:
                continue

            # Get saved settings for this theme
            saved_presence = self._state_store.settings.track_presence.get(theme.id, {})
            saved_muted = self._state_store.settings.track_muted.get(theme.id, {})
            saved_volume = self._state_store.settings.track_volume.get(theme.id, {})
            saved_playback_mode = self._state_store.settings.track_playback_mode.get(theme.id, {})
            saved_seamless_loop = self._state_store.settings.track_seamless_loop.get(theme.id, {})

            # Skip themes with no saved settings
            if not any([saved_presence, saved_muted, saved_volume, saved_playback_mode, saved_seamless_loop]):
                continue

            for inst in theme.instances:
                # Apply saved presence or default to 1.0 (always playing)
                inst.presence = saved_presence.get(inst.name, 1.0)
                # Apply saved muted state (default to enabled/not muted)
                inst.is_enabled = not saved_muted.get(inst.name, False)
                # Apply saved volume (default to 1.0)
                inst.volume = saved_volume.get(inst.name, 1.0)
                # Apply saved playback mode (default to auto)
                mode_str = saved_playback_mode.get(inst.name, "auto")
                try:
                    inst.playback_mode = PlaybackMode(mode_str)
                except ValueError:
                    inst.playback_mode = PlaybackMode.AUTO
                # Apply saved seamless loop (default to False, i.e., crossfade enabled)
                inst.crossfade_enabled = not saved_seamless_loop.get(inst.name, False)

            logger.info(f"    Applied settings to theme '{theme.name}'")

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

    async def serve_logo(self):
        """Serve the logo.png file."""
        if LOGO_PATH.exists():
            return FileResponse(LOGO_PATH, media_type="image/png")
        raise HTTPException(status_code=404, detail="Logo not found")

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

    def _find_theme_folder(self, theme_id: str) -> Path | None:
        """Find theme folder by ID, handling sanitized names."""
        media_paths = [Path("/media/sonorium"), Path("/share/sonorium")]
        for mp in media_paths:
            if not mp.exists():
                continue
            # Try exact match first
            exact_path = mp / theme_id
            if exact_path.exists():
                return exact_path
            # Try scanning folders and comparing sanitized names
            for folder in mp.iterdir():
                if folder.is_dir():
                    sanitized = folder.name.lower().replace(' ', '-').replace('_', '-')
                    sanitized = ''.join(c for c in sanitized if c.isalnum() or c == '-')
                    while '--' in sanitized:
                        sanitized = sanitized.replace('--', '-')
                    sanitized = sanitized.strip('-')
                    if sanitized == theme_id or sanitized == theme_id.replace('_', '-'):
                        return folder
        return None

    def _read_theme_metadata(self, theme_id: str) -> dict:
        """Read metadata.json from theme folder."""
        import json
        folder = self._find_theme_folder(theme_id)
        if folder:
            meta_path = folder / "metadata.json"
            if meta_path.exists():
                try:
                    return json.loads(meta_path.read_text())
                except Exception:
                    pass
        return {}

    async def list_themes(self):
        """List all available themes with full metadata."""
        import json
        device = self.client.device

        # Get favorites and categories from state if available
        favorites = []
        category_assignments = {}
        if self._state_store:
            favorites = self._state_store.settings.favorite_themes
            category_assignments = self._state_store.settings.theme_category_assignments

        themes = []
        seen_folders = set()
        audio_extensions = {'.mp3', '.wav', '.flac', '.ogg'}

        # First, add themes loaded by the device (have audio files)
        for theme in device.themes:
            enabled_count = sum(1 for i in theme.instances if i.is_enabled)

            # Try to read metadata.json from theme folder
            metadata = self._read_theme_metadata(theme.id)

            # Apply short_file_threshold from metadata if present
            if "short_file_threshold" in metadata:
                theme.short_file_threshold = float(metadata["short_file_threshold"])

            # Track the folder name
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
                "categories": category_assignments.get(theme.id, []),
                "short_file_threshold": theme.short_file_threshold,
            })

        # Then scan for empty theme folders
        media_paths = [
            Path("/media/sonorium"),
            Path("/share/sonorium"),
        ]

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

                # Generate sanitized ID
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
                    "categories": category_assignments.get(sanitized_id, []),
                })

        return themes

    async def refresh_themes(self):
        """Rescan theme folders and reload themes."""
        from sonorium.theme import ThemeDefinition
        from sonorium.recording import RecordingMetadata
        from fmtr.tools.iterator_tools import IndexList

        device = self.client.device
        path_audio = device.path_audio
        audio_extensions = ['.mp3', '.wav', '.flac', '.ogg']

        logger.info(f'Rescanning themes in "{path_audio}"...')

        # Scan for theme folders
        theme_folders = [folder for folder in path_audio.iterdir() if folder.is_dir()]
        logger.info(f'Found {len(theme_folders)} theme folder(s)')

        # Step 1: Build theme_metas FIRST (before creating ThemeDefinitions)
        new_theme_metas = {}
        theme_names_with_audio = []

        for folder in theme_folders:
            audio_files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in audio_extensions]

            if audio_files:
                theme_name = folder.name
                new_theme_metas[theme_name] = IndexList(RecordingMetadata(path) for path in audio_files)
                theme_names_with_audio.append(theme_name)
                logger.info(f'Found theme "{theme_name}" with {len(audio_files)} audio files')

        # Step 2: Update device.theme_metas BEFORE creating ThemeDefinitions
        # This is critical because ThemeDefinition.__init__ looks up theme_metas[name]
        device.theme_metas = new_theme_metas

        # Rebuild metas list
        device.metas = IndexList()
        for theme_recordings in device.theme_metas.values():
            device.metas.extend(theme_recordings)

        # Step 3: NOW create ThemeDefinition objects (they will find their metas correctly)
        new_themes = IndexList()
        for theme_name in theme_names_with_audio:
            theme_def = ThemeDefinition(sonorium=device, name=theme_name)
            new_themes.append(theme_def)
            logger.info(f'Created ThemeDefinition "{theme_name}" with {len(theme_def.instances)} instances')

        # Step 4: Update device.themes
        device.themes = new_themes

        # Set current theme if we have themes
        if device.themes:
            from sonorium.recording import PlaybackMode

            device.themes.current = device.themes[0]
            # Enable all recordings and apply saved track settings
            for theme in device.themes:
                if theme.instances:
                    # Get saved settings for this theme
                    saved_presence = {}
                    saved_muted = {}
                    saved_volume = {}
                    saved_playback_mode = {}
                    saved_seamless_loop = {}
                    if self._state_store:
                        saved_presence = self._state_store.settings.track_presence.get(theme.id, {})
                        saved_muted = self._state_store.settings.track_muted.get(theme.id, {})
                        saved_volume = self._state_store.settings.track_volume.get(theme.id, {})
                        saved_playback_mode = self._state_store.settings.track_playback_mode.get(theme.id, {})
                        saved_seamless_loop = self._state_store.settings.track_seamless_loop.get(theme.id, {})

                    for inst in theme.instances:
                        # Apply saved presence or default to 1.0 (always playing)
                        inst.presence = saved_presence.get(inst.name, 1.0)
                        # Apply saved muted state (default to enabled/not muted)
                        inst.is_enabled = not saved_muted.get(inst.name, False)
                        # Apply saved volume (default to 1.0)
                        inst.volume = saved_volume.get(inst.name, 1.0)
                        # Apply saved playback mode (default to auto)
                        mode_str = saved_playback_mode.get(inst.name, "auto")
                        try:
                            inst.playback_mode = PlaybackMode(mode_str)
                        except ValueError:
                            inst.playback_mode = PlaybackMode.AUTO
                        # Apply saved seamless loop (default to False, i.e., crossfade enabled)
                        inst.crossfade_enabled = not saved_seamless_loop.get(inst.name, False)

        logger.info(f'Theme refresh complete: {len(device.themes)} themes loaded')

        # Update session manager's theme reference
        if self._session_manager:
            self._session_manager.set_themes(device.themes)

        return {
            "status": "ok",
            "themes_count": len(device.themes),
            "message": f"Refreshed {len(device.themes)} themes"
        }

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

    async def get_theme_tracks(self, theme_id: str):
        """Get all tracks for a theme with presence/mute settings."""
        theme = self.client.device.themes.id.get(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        if not self._state_store:
            return {"error": "State not available"}

        # Get saved settings
        track_presence = self._state_store.settings.track_presence.get(theme_id, {})
        track_muted = self._state_store.settings.track_muted.get(theme_id, {})
        track_volume = self._state_store.settings.track_volume.get(theme_id, {})
        track_playback_mode = self._state_store.settings.track_playback_mode.get(theme_id, {})
        track_seamless_loop = self._state_store.settings.track_seamless_loop.get(theme_id, {})

        tracks = []
        for inst in theme.instances:
            tracks.append({
                "name": inst.name,
                "presence": track_presence.get(inst.name, 1.0),
                "muted": track_muted.get(inst.name, False),
                "is_enabled": inst.is_enabled,
                "duration_seconds": round(inst.meta.duration_seconds, 1),
                "is_short_file": inst.meta.is_short_file(theme.short_file_threshold),
                "volume": track_volume.get(inst.name, 1.0),
                "playback_mode": track_playback_mode.get(inst.name, "auto"),
                "seamless_loop": track_seamless_loop.get(inst.name, False),
            })

        # Sort tracks alphabetically by name
        tracks.sort(key=lambda t: t["name"].lower())

        return {
            "theme_id": theme_id,
            "theme_name": theme.name,
            "short_file_threshold": theme.short_file_threshold,
            "tracks": tracks,
        }

    async def get_track_audio(self, theme_id: str, track_name: str):
        """Serve an individual track audio file for browser preview playback."""
        from fastapi.responses import FileResponse
        from fastapi import HTTPException
        from urllib.parse import unquote

        # URL decode the track name
        track_name = unquote(track_name)

        theme = self.client.device.themes.id.get(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail="Theme not found")

        # Find the track instance
        track_inst = None
        for inst in theme.instances:
            if inst.name == track_name:
                track_inst = inst
                break

        if not track_inst:
            raise HTTPException(status_code=404, detail=f"Track not found: {track_name}")

        # Get the file path from the metadata
        audio_path = track_inst.meta.path
        if not audio_path or not audio_path.exists():
            raise HTTPException(status_code=404, detail="Audio file not found")

        # Determine media type based on extension
        suffix = audio_path.suffix.lower()
        media_types = {
            '.mp3': 'audio/mpeg',
            '.wav': 'audio/wav',
            '.ogg': 'audio/ogg',
            '.flac': 'audio/flac',
            '.m4a': 'audio/mp4',
            '.aac': 'audio/aac',
        }
        media_type = media_types.get(suffix, 'audio/mpeg')

        return FileResponse(
            path=str(audio_path),
            media_type=media_type,
            filename=audio_path.name,
        )

    async def set_track_presence(self, theme_id: str, track_name: str, request: Request):
        """Set presence (frequency) for a specific track in a theme.

        Presence controls how often a track plays in the mix:
        - 1.0 = always playing (100% of the time)
        - 0.5 = plays about half the time
        - 0.0 = never plays (disabled)
        """
        theme = self.client.device.themes.id.get(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        if not self._state_store:
            return {"error": "State not available"}

        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON body"}

        presence = body.get("presence")
        if presence is None:
            return {"error": "Presence is required"}

        # Clamp presence to 0.0 - 1.0 range
        presence = max(0.0, min(1.0, float(presence)))

        # Find the track instance
        track_inst = None
        for inst in theme.instances:
            if inst.name == track_name:
                track_inst = inst
                break

        if not track_inst:
            return {"error": "Track not found"}

        # Update the live instance presence
        track_inst.presence = presence

        # Persist to settings
        if theme_id not in self._state_store.settings.track_presence:
            self._state_store.settings.track_presence[theme_id] = {}
        self._state_store.settings.track_presence[theme_id][track_name] = presence
        self._state_store.save()

        return {"status": "ok", "track": track_name, "presence": presence}

    async def set_track_muted(self, theme_id: str, track_name: str, request: Request):
        """Set muted state for a specific track in a theme."""
        theme = self.client.device.themes.id.get(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        if not self._state_store:
            return {"error": "State not available"}

        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON body"}

        muted = body.get("muted")
        if muted is None:
            return {"error": "Muted state is required"}

        muted = bool(muted)

        # Find the track instance
        track_inst = None
        for inst in theme.instances:
            if inst.name == track_name:
                track_inst = inst
                break

        if not track_inst:
            return {"error": "Track not found"}

        # Update the live instance
        track_inst.is_enabled = not muted

        # Persist to settings
        if theme_id not in self._state_store.settings.track_muted:
            self._state_store.settings.track_muted[theme_id] = {}
        self._state_store.settings.track_muted[theme_id][track_name] = muted
        self._state_store.save()

        return {"status": "ok", "track": track_name, "muted": muted}

    async def set_track_volume(self, theme_id: str, track_name: str, request: Request):
        """Set volume (amplitude) for a specific track in a theme.

        Volume controls how loud a track plays (0.0-1.0), independent of presence.
        """
        theme = self.client.device.themes.id.get(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        if not self._state_store:
            return {"error": "State not available"}

        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON body"}

        volume = body.get("volume")
        if volume is None:
            return {"error": "Volume is required"}

        # Clamp volume to 0.0 - 1.0 range
        volume = max(0.0, min(1.0, float(volume)))

        # Find the track instance
        track_inst = None
        for inst in theme.instances:
            if inst.name == track_name:
                track_inst = inst
                break

        if not track_inst:
            return {"error": "Track not found"}

        # Update the live instance volume
        track_inst.volume = volume

        # Persist to settings
        if theme_id not in self._state_store.settings.track_volume:
            self._state_store.settings.track_volume[theme_id] = {}
        self._state_store.settings.track_volume[theme_id][track_name] = volume
        self._state_store.save()

        return {"status": "ok", "track": track_name, "volume": volume}

    async def set_track_playback_mode(self, theme_id: str, track_name: str, request: Request):
        """Set playback mode for a specific track in a theme.

        Playback modes:
        - auto: Automatically choose based on file length and presence
        - continuous: Loop continuously with crossfade
        - sparse: Play once, then silence for interval based on presence
        - presence: Fade in/out based on presence value
        """
        from sonorium.recording import PlaybackMode

        theme = self.client.device.themes.id.get(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        if not self._state_store:
            return {"error": "State not available"}

        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON body"}

        mode_str = body.get("playback_mode")
        if mode_str is None:
            return {"error": "Playback mode is required"}

        # Validate mode
        valid_modes = [m.value for m in PlaybackMode]
        if mode_str not in valid_modes:
            return {"error": f"Invalid playback mode. Must be one of: {valid_modes}"}

        mode = PlaybackMode(mode_str)

        # Find the track instance
        track_inst = None
        for inst in theme.instances:
            if inst.name == track_name:
                track_inst = inst
                break

        if not track_inst:
            return {"error": "Track not found"}

        # Update the live instance
        track_inst.playback_mode = mode

        # Persist to settings
        if theme_id not in self._state_store.settings.track_playback_mode:
            self._state_store.settings.track_playback_mode[theme_id] = {}
        self._state_store.settings.track_playback_mode[theme_id][track_name] = mode_str
        self._state_store.save()

        return {"status": "ok", "track": track_name, "playback_mode": mode_str}

    async def set_track_seamless_loop(self, theme_id: str, track_name: str, request: Request):
        """Set seamless loop (disable crossfade) for a specific track in a theme."""
        theme = self.client.device.themes.id.get(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        if not self._state_store:
            return {"error": "State not available"}

        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON body"}

        seamless = body.get("seamless_loop")
        if seamless is None:
            return {"error": "seamless_loop is required"}

        # Find the track instance
        track_inst = None
        for inst in theme.instances:
            if inst.name == track_name:
                track_inst = inst
                break

        if not track_inst:
            return {"error": "Track not found"}

        # Update the live instance
        track_inst.crossfade_enabled = not seamless

        # Persist to settings
        if theme_id not in self._state_store.settings.track_seamless_loop:
            self._state_store.settings.track_seamless_loop[theme_id] = {}

        if seamless:
            self._state_store.settings.track_seamless_loop[theme_id][track_name] = True
        else:
            # Remove from dict if disabling seamless
            self._state_store.settings.track_seamless_loop[theme_id].pop(track_name, None)

        self._state_store.save()

        return {"status": "ok", "track": track_name, "seamless_loop": seamless}

    async def reset_theme_tracks(self, theme_id: str):
        """Reset all track settings to defaults for a theme."""
        from sonorium.recording import PlaybackMode

        theme = self.client.device.themes.id.get(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        if not self._state_store:
            return {"error": "State not available"}

        # Reset live instances
        for inst in theme.instances:
            inst.presence = 1.0
            inst.is_enabled = True
            inst.volume = 1.0
            inst.playback_mode = PlaybackMode.AUTO
            inst.crossfade_enabled = True

        # Clear persisted settings
        if theme_id in self._state_store.settings.track_presence:
            del self._state_store.settings.track_presence[theme_id]
        if theme_id in self._state_store.settings.track_muted:
            del self._state_store.settings.track_muted[theme_id]
        if theme_id in self._state_store.settings.track_volume:
            del self._state_store.settings.track_volume[theme_id]
        if theme_id in self._state_store.settings.track_playback_mode:
            del self._state_store.settings.track_playback_mode[theme_id]
        if theme_id in self._state_store.settings.track_seamless_loop:
            del self._state_store.settings.track_seamless_loop[theme_id]
        self._state_store.save()

        return {"status": "ok", "theme_id": theme_id}

    async def toggle_favorite(self, theme_id: str):
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

    async def list_categories(self):
        """List all theme categories."""
        if not self._state_store:
            return {"error": "State not available"}

        return {
            "categories": self._state_store.settings.theme_categories
        }

    async def create_category(self, request: Request):
        """Create a new theme category."""
        if not self._state_store:
            return {"error": "State not available"}

        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON body"}

        name = body.get("name", "").strip()
        if not name:
            return {"error": "Category name is required"}

        categories = self._state_store.settings.theme_categories
        if name in categories:
            return {"error": "Category already exists"}

        categories.append(name)
        self._state_store.save()

        return {"status": "ok", "category": name, "categories": categories}

    async def delete_category(self, category_name: str):
        """Delete a theme category."""
        if not self._state_store:
            return {"error": "State not available"}

        import urllib.parse
        category_name = urllib.parse.unquote(category_name)

        categories = self._state_store.settings.theme_categories
        if category_name not in categories:
            return {"error": "Category not found"}

        categories.remove(category_name)

        # Also remove from all theme assignments
        assignments = self._state_store.settings.theme_category_assignments
        for theme_id in assignments:
            if category_name in assignments[theme_id]:
                assignments[theme_id].remove(category_name)

        self._state_store.save()

        return {"status": "ok", "deleted": category_name, "categories": categories}

    async def set_theme_categories(self, theme_id: str, request: Request):
        """Set categories for a theme."""
        if not self._state_store:
            return {"error": "State not available"}

        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON body"}

        new_categories = body.get("categories", [])

        # Ensure all categories exist (auto-create if needed)
        existing_categories = self._state_store.settings.theme_categories
        for cat in new_categories:
            if cat not in existing_categories:
                existing_categories.append(cat)

        # Update theme's category assignments
        self._state_store.settings.theme_category_assignments[theme_id] = new_categories
        self._state_store.save()

        return {"theme_id": theme_id, "categories": new_categories}

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