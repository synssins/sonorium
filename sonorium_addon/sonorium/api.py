import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from sonorium.theme import ThemeDefinition
from sonorium.version import __version__
from sonorium.obs import logger
from fmtr.tools import api

# Import ClientSonorium for type hints (replaces mqtt.Client)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from sonorium.client import ClientSonorium

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

    def __init__(self, client: "ClientSonorium"):
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
        self._theme_metadata_manager = None
        
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
            api.Endpoint(method_http=self.app.put, path='/api/themes/{theme_id}/tracks/{track_name}/exclusive', method=self.set_track_exclusive),
            api.Endpoint(method_http=self.app.post, path='/api/themes/{theme_id}/tracks/reset', method=self.reset_theme_tracks),

            # Theme rename
            api.Endpoint(method_http=self.app.put, path='/api/themes/{theme_id}/rename', method=self.rename_theme),

            # Preset API
            api.Endpoint(method_http=self.app.get, path='/api/themes/{theme_id}/presets', method=self.list_presets),
            api.Endpoint(method_http=self.app.post, path='/api/themes/{theme_id}/presets', method=self.create_preset),
            api.Endpoint(method_http=self.app.post, path='/api/themes/{theme_id}/presets/{preset_id}/load', method=self.load_preset),
            api.Endpoint(method_http=self.app.put, path='/api/themes/{theme_id}/presets/{preset_id}', method=self.update_preset),
            api.Endpoint(method_http=self.app.delete, path='/api/themes/{theme_id}/presets/{preset_id}', method=self.delete_preset),
            api.Endpoint(method_http=self.app.put, path='/api/themes/{theme_id}/presets/{preset_id}/default', method=self.set_default_preset),
            api.Endpoint(method_http=self.app.put, path='/api/themes/{theme_id}/presets/{preset_id}/rename', method=self.rename_preset),
            api.Endpoint(method_http=self.app.post, path='/api/themes/{theme_id}/presets/import', method=self.import_preset),
            api.Endpoint(method_http=self.app.get, path='/api/themes/{theme_id}/presets/{preset_id}/export', method=self.export_preset),

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

            # Initialize theme metadata manager
            from sonorium.core.theme_metadata import ThemeMetadataManager
            audio_path = self.client.device.path_audio
            self._theme_metadata_manager = ThemeMetadataManager(audio_path)
            theme_metadata = self._theme_metadata_manager.scan_themes()
            logger.info(f"  Theme metadata: {len(theme_metadata)} themes scanned")

            # Migrate any theme data from state.json to metadata.json (one-time migration)
            self._migrate_theme_data_to_metadata()

            # Apply saved track settings to themes (now reads from metadata.json)
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
            
            # Initialize session manager (with cycle manager and metadata manager)
            self._session_manager = SessionManager(
                self._state_store,
                self._ha_registry,
                self._media_controller,
                stream_base_url,
                channel_manager=self._channel_manager,
                cycle_manager=self._cycle_manager,
                themes=self.client.device.themes,
                theme_metadata_manager=self._theme_metadata_manager,
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

            # Initialize MQTT entity manager for Home Assistant integration
            try:
                from sonorium.ha.mqtt_entities import SonoriumMQTTManager
                self._mqtt_manager = SonoriumMQTTManager(
                    state_store=self._state_store,
                    session_manager=self._session_manager,
                    mqtt_client=self.client.mqtt_client,
                    theme_metadata_manager=self._theme_metadata_manager,
                )
                # Set available themes for the theme select entity
                themes = [{"id": t.id, "name": t.name} for t in self.client.device.themes]
                self._mqtt_manager.set_themes(themes)

                # Wire up message handler for incoming MQTT commands
                self.client.mqtt_client.set_message_handler(self._mqtt_manager.handle_command)

                await self._mqtt_manager.initialize()
                logger.info(f"  MQTT entity manager: {len(self._state_store.sessions)} session entities published")
            except Exception as e:
                logger.warning(f"  Failed to initialize MQTT entity manager: {e}")
                import traceback
                traceback.print_exc()
                self._mqtt_manager = None

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
    
    def _migrate_theme_data_to_metadata(self):
        """
        Migrate theme data from state.json to metadata.json (one-time migration).

        This moves favorites, categories, and track settings from the global
        state file to each theme's metadata.json, making themes portable.
        """
        if not self._state_store or not self._theme_metadata_manager:
            return

        settings = self._state_store.settings
        migrated_any = False

        # Get all theme IDs from metadata manager
        for theme_id, metadata in self._theme_metadata_manager._metadata_cache.items():
            # theme_id here is actually folder path, get the actual metadata
            pass

        # Iterate through themes in metadata manager
        for folder, metadata in self._theme_metadata_manager._metadata_cache.items():
            theme_id = metadata.id
            old_theme_id = folder.name.lower().replace(' ', '-').replace('_', '-')
            old_theme_id = ''.join(c for c in old_theme_id if c.isalnum() or c == '-')

            # Also try alphanumeric-only ID (legacy format)
            old_theme_id_alnum = ''.join(c for c in folder.name.lower() if c.isalnum())

            changed = False

            # Migrate favorites
            if old_theme_id in settings.favorite_themes or old_theme_id_alnum in settings.favorite_themes:
                if not metadata.is_favorite:
                    metadata.is_favorite = True
                    changed = True
                    logger.info(f"  Migrated favorite status for '{metadata.name}'")

            # Migrate categories
            old_cats = settings.theme_category_assignments.get(old_theme_id) or \
                       settings.theme_category_assignments.get(old_theme_id_alnum)
            if old_cats and not metadata.categories:
                metadata.categories = old_cats
                changed = True
                logger.info(f"  Migrated categories for '{metadata.name}': {old_cats}")

            # Migrate track settings
            track_fields = [
                ('track_presence', 'presence'),
                ('track_muted', 'muted'),
                ('track_volume', 'volume'),
                ('track_playback_mode', 'playback_mode'),
                ('track_seamless_loop', 'seamless_loop'),
                ('track_exclusive', 'exclusive'),
            ]

            for state_field, track_attr in track_fields:
                state_dict = getattr(settings, state_field, {})
                old_data = state_dict.get(old_theme_id) or state_dict.get(old_theme_id_alnum)
                if old_data:
                    for track_name, value in old_data.items():
                        track_settings = metadata.get_track_settings(track_name)
                        current_value = getattr(track_settings, track_attr)
                        # Only migrate if different from default
                        default_values = {'presence': 1.0, 'muted': False, 'volume': 1.0,
                                         'playback_mode': 'auto', 'seamless_loop': False, 'exclusive': False}
                        if value != default_values.get(track_attr):
                            setattr(track_settings, track_attr, value)
                            changed = True

            if changed:
                self._theme_metadata_manager.save_metadata(theme_id, metadata)
                migrated_any = True

        if migrated_any:
            logger.info("  Theme data migration complete")

    def _apply_saved_track_settings(self):
        """Apply saved track settings from metadata.json to theme instances on startup."""
        if not self._theme_metadata_manager:
            return

        from sonorium.recording import PlaybackMode

        device = self.client.device
        if not device.themes:
            return

        logger.info("  Applying saved track settings to themes...")
        for theme in device.themes:
            if not theme.instances:
                continue

            # Find metadata for this theme by matching folder name
            theme_folder = self._find_theme_folder(theme.id)
            if not theme_folder:
                continue

            metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if not metadata:
                continue

            # Apply short_file_threshold from metadata
            theme.short_file_threshold = metadata.short_file_threshold

            for inst in theme.instances:
                track_settings = metadata.tracks.get(inst.name)
                if not track_settings:
                    # Use defaults
                    inst.presence = 1.0
                    inst.is_enabled = True
                    inst.volume = 1.0
                    inst.playback_mode = PlaybackMode.AUTO
                    inst.crossfade_enabled = True
                    inst.exclusive = False
                    continue

                # Apply settings from metadata
                inst.presence = track_settings.presence
                inst.is_enabled = not track_settings.muted
                inst.volume = track_settings.volume
                try:
                    inst.playback_mode = PlaybackMode(track_settings.playback_mode)
                except ValueError:
                    inst.playback_mode = PlaybackMode.AUTO
                inst.crossfade_enabled = not track_settings.seamless_loop
                inst.exclusive = track_settings.exclusive

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
        """Stream audio by theme ID (supports both UUID and legacy slug IDs)."""
        theme_def, _ = self._get_theme_by_id(id)
        if not theme_def:
            raise HTTPException(status_code=404, detail=f"Theme '{id}' not found")
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
        """
        Find theme folder by ID.

        First checks the metadata manager for UUID-based theme IDs,
        then falls back to legacy folder name matching for backwards compatibility.
        """
        # First, try metadata manager (for UUID-based theme IDs)
        if self._theme_metadata_manager:
            folder = self._theme_metadata_manager.get_folder_for_id(theme_id)
            if folder:
                return folder

            # Also search by folder-based lookup in metadata cache
            for folder_path, metadata in self._theme_metadata_manager._metadata_cache.items():
                # Check if theme_id matches the sanitized folder name (legacy compatibility)
                folder_id_alnum = ''.join(c for c in folder_path.name.lower() if c.isalnum())
                theme_id_alnum = ''.join(c for c in theme_id.lower() if c.isalnum())
                if folder_id_alnum == theme_id_alnum:
                    return folder_path

        # Fallback: Get the actual audio path from device and scan manually
        device = self.client.device
        if device and hasattr(device, 'path_audio'):
            media_paths = [device.path_audio]
        else:
            # Fallback to device path_audio (no hardcoded paths)
            logger.warning("_find_theme_folder: device.path_audio not available")
            return None

        # Create multiple normalized versions of theme_id for matching
        theme_id_lower = theme_id.lower()
        theme_id_no_sep = ''.join(c for c in theme_id_lower if c.isalnum())  # alphanumeric only

        for mp in media_paths:
            if not mp.exists():
                logger.debug(f"_find_theme_folder: path {mp} does not exist")
                continue
            # Try exact match first
            exact_path = mp / theme_id
            if exact_path.exists():
                return exact_path
            # Try scanning folders and comparing normalized names
            for folder in mp.iterdir():
                if folder.is_dir():
                    # Create alphanumeric-only version for comparison
                    folder_no_sep = ''.join(c for c in folder.name.lower() if c.isalnum())

                    if folder_no_sep == theme_id_no_sep:
                        return folder

        logger.warning(f"_find_theme_folder: no folder found for theme_id '{theme_id}'")
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

    def _write_theme_metadata(self, theme_id: str, metadata: dict) -> bool:
        """Write metadata.json to theme folder. Returns True on success."""
        import json
        folder = self._find_theme_folder(theme_id)
        if not folder:
            logger.error(f"Cannot write metadata: theme folder not found for '{theme_id}'")
            return False

        meta_path = folder / "metadata.json"
        try:
            meta_path.write_text(json.dumps(metadata, indent=2))
            logger.info(f"Wrote metadata to {meta_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to write metadata for theme {theme_id}: {e}")
            return False

    def _save_track_setting_to_metadata(self, theme_id: str, track_name: str, **settings) -> bool:
        """Save track settings to metadata.json instead of state.json."""
        if not self._theme_metadata_manager:
            return False

        # Find the metadata for this theme
        theme_folder = self._find_theme_folder(theme_id)
        if not theme_folder:
            logger.error(f"Cannot save track setting: theme folder not found for '{theme_id}'")
            return False

        metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
        if not metadata:
            logger.error(f"Cannot save track setting: metadata not found for '{theme_id}'")
            return False

        # Update track settings
        track_settings = metadata.get_track_settings(track_name)
        for key, value in settings.items():
            if hasattr(track_settings, key):
                setattr(track_settings, key, value)

        # Save back to metadata.json
        return self._theme_metadata_manager.save_metadata(metadata.id, metadata)

    def _get_theme_by_id(self, theme_id: str):
        """
        Get a theme by ID, handling both legacy folder-based IDs and UUID-based IDs.
        Returns (theme, folder) tuple or (None, None) if not found.
        """
        # First try direct lookup by folder-based ID
        theme = self.client.device.themes.id.get(theme_id)
        if theme:
            folder = self._find_theme_folder(theme_id)
            return theme, folder

        # Try finding by metadata ID (UUID-based)
        theme_folder = self._find_theme_folder(theme_id)
        if theme_folder:
            # Find matching theme by folder name
            for t in self.client.device.themes:
                if t.name == theme_folder.name:
                    return t, theme_folder

        return None, None

    async def list_themes(self):
        """List all available themes with full metadata from metadata.json files."""
        device = self.client.device
        themes = []
        seen_folders = set()
        audio_extensions = {'.mp3', '.wav', '.flac', '.ogg'}

        # First, add themes loaded by the device (have audio files)
        for theme in device.themes:
            enabled_count = sum(1 for i in theme.instances if i.is_enabled)

            # Get metadata from metadata manager or read from file
            theme_folder = self._find_theme_folder(theme.id)
            metadata = None
            if theme_folder and self._theme_metadata_manager:
                metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
                seen_folders.add(theme_folder.name)

            # Fall back to reading metadata dict directly
            if not metadata:
                metadata_dict = self._read_theme_metadata(theme.id)
                # Use the persistent theme ID from metadata if available
                theme_id = metadata_dict.get("id", theme.id)
                themes.append({
                    "id": theme_id,
                    "name": metadata_dict.get("name", theme.name),
                    "total_tracks": len(theme.instances),
                    "enabled_tracks": enabled_count,
                    "url": theme.url,
                    "description": metadata_dict.get("description", ""),
                    "icon": metadata_dict.get("icon", ""),
                    "is_favorite": metadata_dict.get("is_favorite", False),
                    "has_audio": True,
                    "categories": metadata_dict.get("categories", []),
                    "short_file_threshold": metadata_dict.get("short_file_threshold", theme.short_file_threshold),
                })
                continue

            # Apply short_file_threshold from metadata
            theme.short_file_threshold = metadata.short_file_threshold

            # Use the persistent UUID from metadata.json as the theme ID
            themes.append({
                "id": metadata.id,
                "name": metadata.name,
                "total_tracks": len(theme.instances),
                "enabled_tracks": enabled_count,
                "url": theme.url,
                "description": metadata.description,
                "icon": metadata.icon,
                "is_favorite": metadata.is_favorite,
                "has_audio": True,
                "categories": metadata.categories,
                "short_file_threshold": metadata.short_file_threshold,
            })

        # Then scan for empty theme folders (using device.path_audio, not hardcoded)
        if device and hasattr(device, 'path_audio') and device.path_audio.exists():
            for folder in device.path_audio.iterdir():
                if not folder.is_dir() or folder.name in seen_folders:
                    continue

                # Count audio files in this folder
                audio_files = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in audio_extensions]

                # Skip if it has audio (already added above)
                if audio_files:
                    continue

                # Get or create metadata for empty folder
                if self._theme_metadata_manager:
                    metadata = self._theme_metadata_manager.get_metadata_by_folder(folder)
                    if not metadata:
                        # Create metadata for this empty folder
                        metadata = self._theme_metadata_manager._load_or_create_metadata(folder)

                    themes.append({
                        "id": metadata.id,
                        "name": metadata.name,
                        "total_tracks": 0,
                        "enabled_tracks": 0,
                        "url": "",
                        "description": metadata.description,
                        "icon": metadata.icon,
                        "is_favorite": metadata.is_favorite,
                        "has_audio": False,
                        "categories": metadata.categories,
                    })

        return themes

    async def refresh_themes(self):
        """Rescan theme folders and reload themes."""
        from sonorium.theme import ThemeDefinition
        from sonorium.recording import RecordingMetadata, PlaybackMode
        from fmtr.tools.iterator_tools import IndexList

        device = self.client.device
        path_audio = device.path_audio
        audio_extensions = ['.mp3', '.wav', '.flac', '.ogg']

        logger.info(f'Rescanning themes in "{path_audio}"...')

        # Rescan metadata manager to pick up any new/changed folders
        if self._theme_metadata_manager:
            self._theme_metadata_manager.scan_themes()

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
            device.themes.current = device.themes[0]

            # Enable all recordings and apply saved track settings from metadata.json
            for theme in device.themes:
                if not theme.instances:
                    continue

                # Find metadata for this theme
                theme_folder = self._find_theme_folder(theme.id)
                metadata = None
                if theme_folder and self._theme_metadata_manager:
                    metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)

                if metadata:
                    # Apply short_file_threshold from metadata
                    theme.short_file_threshold = metadata.short_file_threshold

                for inst in theme.instances:
                    if metadata:
                        track_settings = metadata.tracks.get(inst.name)
                        if track_settings:
                            inst.presence = track_settings.presence
                            inst.is_enabled = not track_settings.muted
                            inst.volume = track_settings.volume
                            try:
                                inst.playback_mode = PlaybackMode(track_settings.playback_mode)
                            except ValueError:
                                inst.playback_mode = PlaybackMode.AUTO
                            inst.crossfade_enabled = not track_settings.seamless_loop
                            inst.exclusive = track_settings.exclusive
                            continue

                    # Use defaults if no metadata
                    inst.presence = 1.0
                    inst.is_enabled = True
                    inst.volume = 1.0
                    inst.playback_mode = PlaybackMode.AUTO
                    inst.crossfade_enabled = True
                    inst.exclusive = False

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
        # Use _get_theme_by_id to handle both UUID-based and folder-based IDs
        theme, _ = self._get_theme_by_id(theme_id)
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
        """Get all tracks for a theme with presence/mute settings from metadata.json."""
        theme, theme_folder = self._get_theme_by_id(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        # Get settings from metadata.json
        metadata = None
        if theme_folder and self._theme_metadata_manager:
            metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)

        tracks = []
        for inst in theme.instances:
            # Get track settings from metadata or use instance values (which were loaded from metadata)
            track_settings = metadata.tracks.get(inst.name) if metadata else None

            tracks.append({
                "name": inst.name,
                "presence": track_settings.presence if track_settings else inst.presence,
                "muted": track_settings.muted if track_settings else not inst.is_enabled,
                "is_enabled": inst.is_enabled,
                "duration_seconds": round(inst.meta.duration_seconds, 1),
                "is_short_file": inst.meta.is_short_file(theme.short_file_threshold),
                "volume": track_settings.volume if track_settings else inst.volume,
                "playback_mode": track_settings.playback_mode if track_settings else (
                    inst.playback_mode.value if hasattr(inst.playback_mode, 'value') else 'auto'
                ),
                "seamless_loop": track_settings.seamless_loop if track_settings else not inst.crossfade_enabled,
                "exclusive": track_settings.exclusive if track_settings else inst.exclusive,
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

        # Use _get_theme_by_id to handle both UUID-based and folder-based IDs
        theme, _ = self._get_theme_by_id(theme_id)
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
        # Use _get_theme_by_id to handle both UUID-based and folder-based IDs
        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return {"error": "Theme not found"}

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

        # Persist to metadata.json
        self._save_track_setting_to_metadata(theme_id, track_name, presence=presence)

        return {"status": "ok", "track": track_name, "presence": presence}

    async def set_track_muted(self, theme_id: str, track_name: str, request: Request):
        """Set muted state for a specific track in a theme."""
        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return {"error": "Theme not found"}

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

        # Persist to metadata.json
        self._save_track_setting_to_metadata(theme_id, track_name, muted=muted)

        return {"status": "ok", "track": track_name, "muted": muted}

    async def set_track_volume(self, theme_id: str, track_name: str, request: Request):
        """Set volume (amplitude) for a specific track in a theme.

        Volume controls how loud a track plays (0.0-1.0), independent of presence.
        """
        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return {"error": "Theme not found"}

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

        # Persist to metadata.json
        self._save_track_setting_to_metadata(theme_id, track_name, volume=volume)

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

        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return {"error": "Theme not found"}

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

        # Persist to metadata.json
        self._save_track_setting_to_metadata(theme_id, track_name, playback_mode=mode_str)

        return {"status": "ok", "track": track_name, "playback_mode": mode_str}

    async def set_track_seamless_loop(self, theme_id: str, track_name: str, request: Request):
        """Set seamless loop (disable crossfade) for a specific track in a theme."""
        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON body"}

        seamless = body.get("seamless_loop")
        if seamless is None:
            return {"error": "seamless_loop is required"}

        seamless = bool(seamless)

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

        # Persist to metadata.json
        self._save_track_setting_to_metadata(theme_id, track_name, seamless_loop=seamless)

        return {"status": "ok", "track": track_name, "seamless_loop": seamless}

    async def set_track_exclusive(self, theme_id: str, track_name: str, request: Request):
        """Set exclusive playback for a specific track in a theme.

        When multiple tracks have exclusive=True, only one can play at a time.
        Other exclusive tracks will wait until the playing track finishes
        before starting their own sparse timer.
        """
        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON body"}

        exclusive = body.get("exclusive")
        if exclusive is None:
            return {"error": "exclusive is required"}

        exclusive = bool(exclusive)

        # Find the track instance
        track_inst = None
        for inst in theme.instances:
            if inst.name == track_name:
                track_inst = inst
                break

        if not track_inst:
            return {"error": "Track not found"}

        # Update the live instance
        track_inst.exclusive = exclusive

        # Persist to metadata.json
        self._save_track_setting_to_metadata(theme_id, track_name, exclusive=exclusive)

        return {"status": "ok", "track": track_name, "exclusive": exclusive}

    async def reset_theme_tracks(self, theme_id: str):
        """Reset all track settings to defaults for a theme."""
        from sonorium.recording import PlaybackMode
        from sonorium.core.theme_metadata import TrackSettings

        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        # Reset live instances
        for inst in theme.instances:
            inst.presence = 1.0
            inst.is_enabled = True
            inst.volume = 1.0
            inst.playback_mode = PlaybackMode.AUTO
            inst.crossfade_enabled = True
            inst.exclusive = False

        # Clear persisted settings in metadata.json
        if self._theme_metadata_manager:
            theme_folder = self._find_theme_folder(theme_id)
            if theme_folder:
                metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
                if metadata:
                    # Reset all tracks to defaults
                    metadata.tracks = {}
                    self._theme_metadata_manager.save_metadata(metadata.id, metadata)

        return {"status": "ok", "theme_id": theme_id}

    # ==================== Preset API ====================

    def _get_current_track_settings(self, theme_id: str) -> dict:
        """Get current track settings for a theme as a preset-compatible dict."""
        # Use _get_theme_by_id to handle both UUID-based and folder-based IDs
        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return {}

        tracks = {}
        for inst in theme.instances:
            tracks[inst.name] = {
                "volume": inst.volume,
                "presence": inst.presence,
                "playback_mode": inst.playback_mode.value if hasattr(inst.playback_mode, 'value') else str(inst.playback_mode),
                "seamless_loop": not inst.crossfade_enabled,
                "exclusive": inst.exclusive,
                "muted": not inst.is_enabled,
            }
        return tracks

    def _apply_preset_to_theme(self, theme_id: str, preset_tracks: dict) -> bool:
        """Apply preset track settings to a theme. Returns True on success."""
        from sonorium.recording import PlaybackMode

        # Use _get_theme_by_id to handle both UUID-based and folder-based IDs
        theme, theme_folder = self._get_theme_by_id(theme_id)
        if not theme:
            return False

        # Apply to live instances
        for inst in theme.instances:
            if inst.name in preset_tracks:
                settings = preset_tracks[inst.name]
                inst.volume = settings.get("volume", 1.0)
                inst.presence = settings.get("presence", 1.0)
                mode_str = settings.get("playback_mode", "auto")
                try:
                    inst.playback_mode = PlaybackMode(mode_str)
                except ValueError:
                    inst.playback_mode = PlaybackMode.AUTO
                inst.crossfade_enabled = not settings.get("seamless_loop", False)
                inst.exclusive = settings.get("exclusive", False)
                inst.is_enabled = not settings.get("muted", False)

        # Persist to metadata.json
        if self._theme_metadata_manager and theme_folder:
            metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata:
                # Update track settings in metadata
                for track_name, settings in preset_tracks.items():
                    track_settings = metadata.get_track_settings(track_name)
                    track_settings.presence = settings.get("presence", 1.0)
                    track_settings.muted = settings.get("muted", False)
                    track_settings.volume = settings.get("volume", 1.0)
                    track_settings.playback_mode = settings.get("playback_mode", "auto")
                    track_settings.seamless_loop = settings.get("seamless_loop", False)
                    track_settings.exclusive = settings.get("exclusive", False)

                self._theme_metadata_manager.save_metadata(metadata.id, metadata)

        return True

    async def list_presets(self, theme_id: str):
        """List all presets for a theme."""
        _, theme_folder = self._get_theme_by_id(theme_id)

        # Use metadata manager if available (preferred path)
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                result = []
                for preset_id, preset_data in metadata_obj.presets.items():
                    result.append({
                        "id": preset_id,
                        "name": preset_data.get("name", preset_id),
                        "is_default": preset_data.get("is_default", False),
                        "track_count": len(preset_data.get("tracks", {})),
                    })
                return {"theme_id": theme_id, "presets": result}

        # Fallback to direct file I/O
        metadata = self._read_theme_metadata(theme_id)
        presets = metadata.get("presets", {})

        result = []
        for preset_id, preset_data in presets.items():
            result.append({
                "id": preset_id,
                "name": preset_data.get("name", preset_id),
                "is_default": preset_data.get("is_default", False),
                "track_count": len(preset_data.get("tracks", {})),
            })

        return {"theme_id": theme_id, "presets": result}

    async def create_preset(self, theme_id: str, request: Request):
        """Create a new preset from current track settings."""
        import re

        # Use _get_theme_by_id to handle both UUID-based and folder-based IDs
        theme, theme_folder = self._get_theme_by_id(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail="Theme not found")

        # Get name from request body
        try:
            body = await request.json()
            name = body.get("name")
        except Exception:
            name = None

        # Generate preset ID from name
        if not name:
            name = "New Preset"
        preset_id = re.sub(r'[^a-z0-9]+', '_', name.lower()).strip('_')
        if not preset_id:
            preset_id = "preset"

        # Capture current settings from live theme instances
        tracks = self._get_current_track_settings(theme_id)

        # Use metadata manager if available (preferred path)
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                # Ensure unique ID
                base_id = preset_id
                counter = 1
                while preset_id in metadata_obj.presets:
                    preset_id = f"{base_id}_{counter}"
                    counter += 1

                # Check if this should be default (first preset)
                is_default = len(metadata_obj.presets) == 0

                metadata_obj.presets[preset_id] = {
                    "name": name,
                    "is_default": is_default,
                    "tracks": tracks,
                }

                # Save via metadata manager (updates cache and file)
                if not self._theme_metadata_manager.save_metadata(metadata_obj.id, metadata_obj):
                    raise HTTPException(status_code=500, detail="Failed to save preset")

                return {
                    "status": "ok",
                    "preset_id": preset_id,
                    "name": name,
                    "is_default": is_default,
                }

        # Fallback to direct file I/O (legacy path)
        metadata = self._read_theme_metadata(theme_id)
        if "presets" not in metadata:
            metadata["presets"] = {}

        # Ensure unique ID
        base_id = preset_id
        counter = 1
        while preset_id in metadata["presets"]:
            preset_id = f"{base_id}_{counter}"
            counter += 1

        # Check if this should be default (first preset)
        is_default = len(metadata["presets"]) == 0

        metadata["presets"][preset_id] = {
            "name": name,
            "is_default": is_default,
            "tracks": tracks,
        }

        if not self._write_theme_metadata(theme_id, metadata):
            raise HTTPException(status_code=500, detail="Failed to save preset")

        return {
            "status": "ok",
            "preset_id": preset_id,
            "name": name,
            "is_default": is_default,
        }

    async def load_preset(self, theme_id: str, preset_id: str):
        """Load a preset and apply its settings to the theme."""
        theme, theme_folder = self._get_theme_by_id(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail="Theme not found")

        # Use metadata manager if available (preferred path)
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                if preset_id not in metadata_obj.presets:
                    raise HTTPException(status_code=404, detail="Preset not found")

                preset = metadata_obj.presets[preset_id]
                tracks = preset.get("tracks", {})

                if not self._apply_preset_to_theme(theme_id, tracks):
                    raise HTTPException(status_code=500, detail="Failed to apply preset")

                return {
                    "status": "ok",
                    "preset_id": preset_id,
                    "name": preset.get("name", preset_id),
                    "tracks_applied": len(tracks),
                }

        # Fallback to direct file I/O
        metadata = self._read_theme_metadata(theme_id)
        presets = metadata.get("presets", {})

        if preset_id not in presets:
            raise HTTPException(status_code=404, detail="Preset not found")

        preset = presets[preset_id]
        tracks = preset.get("tracks", {})

        if not self._apply_preset_to_theme(theme_id, tracks):
            raise HTTPException(status_code=500, detail="Failed to apply preset")

        return {
            "status": "ok",
            "preset_id": preset_id,
            "name": preset.get("name", preset_id),
            "tracks_applied": len(tracks),
        }

    async def update_preset(self, theme_id: str, preset_id: str):
        """Update an existing preset with current track settings."""
        # Use _get_theme_by_id to handle both UUID-based and folder-based IDs
        theme, theme_folder = self._get_theme_by_id(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail="Theme not found")

        # Use metadata manager to get cached metadata (avoids cache inconsistency)
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                if preset_id not in metadata_obj.presets:
                    raise HTTPException(status_code=404, detail="Preset not found")

                # Capture current settings from live theme instances
                tracks = self._get_current_track_settings(theme_id)

                # Update the preset's tracks while preserving name and is_default
                metadata_obj.presets[preset_id]["tracks"] = tracks

                # Save via metadata manager (updates cache and file)
                if not self._theme_metadata_manager.save_metadata(metadata_obj.id, metadata_obj):
                    raise HTTPException(status_code=500, detail="Failed to update preset")

                return {
                    "status": "ok",
                    "preset_id": preset_id,
                    "name": metadata_obj.presets[preset_id].get("name", preset_id),
                    "tracks_updated": len(tracks),
                }

        # Fallback to direct file I/O (legacy path)
        metadata = self._read_theme_metadata(theme_id)
        presets = metadata.get("presets", {})

        if preset_id not in presets:
            raise HTTPException(status_code=404, detail="Preset not found")

        tracks = self._get_current_track_settings(theme_id)
        presets[preset_id]["tracks"] = tracks
        metadata["presets"] = presets

        if not self._write_theme_metadata(theme_id, metadata):
            raise HTTPException(status_code=500, detail="Failed to update preset")

        return {
            "status": "ok",
            "preset_id": preset_id,
            "name": presets[preset_id].get("name", preset_id),
            "tracks_updated": len(tracks),
        }

    async def delete_preset(self, theme_id: str, preset_id: str):
        """Delete a preset."""
        theme, theme_folder = self._get_theme_by_id(theme_id)

        # Use metadata manager if available (preferred path)
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                if preset_id not in metadata_obj.presets:
                    raise HTTPException(status_code=404, detail="Preset not found")

                was_default = metadata_obj.presets[preset_id].get("is_default", False)
                del metadata_obj.presets[preset_id]

                # If we deleted the default, make the first remaining preset default
                if was_default and metadata_obj.presets:
                    first_key = next(iter(metadata_obj.presets))
                    metadata_obj.presets[first_key]["is_default"] = True

                if not self._theme_metadata_manager.save_metadata(metadata_obj.id, metadata_obj):
                    raise HTTPException(status_code=500, detail="Failed to save changes")

                return {"status": "ok", "preset_id": preset_id}

        # Fallback to direct file I/O
        metadata = self._read_theme_metadata(theme_id)
        presets = metadata.get("presets", {})

        if preset_id not in presets:
            raise HTTPException(status_code=404, detail="Preset not found")

        was_default = presets[preset_id].get("is_default", False)
        del presets[preset_id]

        # If we deleted the default, make the first remaining preset default
        if was_default and presets:
            first_key = next(iter(presets))
            presets[first_key]["is_default"] = True

        metadata["presets"] = presets
        if not self._write_theme_metadata(theme_id, metadata):
            raise HTTPException(status_code=500, detail="Failed to save changes")

        return {"status": "ok", "preset_id": preset_id}

    async def set_default_preset(self, theme_id: str, preset_id: str):
        """Set a preset as the default for this theme."""
        theme, theme_folder = self._get_theme_by_id(theme_id)

        # Use metadata manager if available (preferred path)
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                if preset_id not in metadata_obj.presets:
                    raise HTTPException(status_code=404, detail="Preset not found")

                # Clear existing default and set new one
                for pid, pdata in metadata_obj.presets.items():
                    pdata["is_default"] = (pid == preset_id)

                if not self._theme_metadata_manager.save_metadata(metadata_obj.id, metadata_obj):
                    raise HTTPException(status_code=500, detail="Failed to save changes")

                return {"status": "ok", "preset_id": preset_id, "is_default": True}

        # Fallback to direct file I/O
        metadata = self._read_theme_metadata(theme_id)
        presets = metadata.get("presets", {})

        if preset_id not in presets:
            raise HTTPException(status_code=404, detail="Preset not found")

        # Clear existing default
        for pid, pdata in presets.items():
            pdata["is_default"] = (pid == preset_id)

        metadata["presets"] = presets
        if not self._write_theme_metadata(theme_id, metadata):
            raise HTTPException(status_code=500, detail="Failed to save changes")

        return {"status": "ok", "preset_id": preset_id, "is_default": True}

    async def import_preset(self, theme_id: str, request: Request):
        """Import a preset from JSON."""
        import json
        import re

        # Use _get_theme_by_id to handle both UUID-based and folder-based IDs
        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            raise HTTPException(status_code=404, detail="Theme not found")

        # Get preset_json and name from request body
        try:
            body = await request.json()
            preset_json = body.get("preset_json")
            name = body.get("name")
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body")

        if not preset_json:
            raise HTTPException(status_code=400, detail="No preset JSON provided")

        # Parse the JSON
        try:
            preset_data = json.loads(preset_json)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {e}")

        # Validate structure - must have tracks dict
        if not isinstance(preset_data, dict):
            raise HTTPException(status_code=400, detail="Preset must be a JSON object")

        # Support both full preset format and tracks-only format
        if "tracks" in preset_data:
            tracks = preset_data["tracks"]
            imported_name = preset_data.get("name", name or "Imported Preset")
        else:
            # Assume it's a tracks-only format
            tracks = preset_data
            imported_name = name or "Imported Preset"

        if not isinstance(tracks, dict):
            raise HTTPException(status_code=400, detail="Tracks must be a JSON object")

        # Validate track settings and filter to only known tracks
        valid_track_names = {inst.name for inst in theme.instances}
        validated_tracks = {}
        unknown_tracks = []

        for track_name, settings in tracks.items():
            if track_name not in valid_track_names:
                unknown_tracks.append(track_name)
                continue

            if not isinstance(settings, dict):
                continue

            validated_tracks[track_name] = {
                "volume": float(settings.get("volume", 1.0)),
                "presence": float(settings.get("presence", 1.0)),
                "playback_mode": str(settings.get("playback_mode", "auto")),
                "seamless_loop": bool(settings.get("seamless_loop", False)),
                "exclusive": bool(settings.get("exclusive", False)),
                "muted": bool(settings.get("muted", False)),
            }

        if not validated_tracks:
            raise HTTPException(status_code=400, detail="No valid track settings found in preset")

        # Generate preset ID
        preset_id = re.sub(r'[^a-z0-9]+', '_', imported_name.lower()).strip('_')
        if not preset_id:
            preset_id = "imported"

        # Get theme folder for metadata manager
        _, theme_folder = self._get_theme_by_id(theme_id)

        # Use metadata manager if available (preferred path)
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                # Ensure unique ID
                base_id = preset_id
                counter = 1
                while preset_id in metadata_obj.presets:
                    preset_id = f"{base_id}_{counter}"
                    counter += 1

                # Save preset
                metadata_obj.presets[preset_id] = {
                    "name": imported_name,
                    "is_default": False,
                    "tracks": validated_tracks,
                }

                if not self._theme_metadata_manager.save_metadata(metadata_obj.id, metadata_obj):
                    raise HTTPException(status_code=500, detail="Failed to save preset")

                result = {
                    "status": "ok",
                    "preset_id": preset_id,
                    "name": imported_name,
                    "tracks_imported": len(validated_tracks),
                }
                if unknown_tracks:
                    result["unknown_tracks"] = unknown_tracks
                    result["warning"] = f"{len(unknown_tracks)} track(s) not found in theme"

                return result

        # Fallback to direct file I/O
        metadata = self._read_theme_metadata(theme_id)
        if "presets" not in metadata:
            metadata["presets"] = {}

        base_id = preset_id
        counter = 1
        while preset_id in metadata["presets"]:
            preset_id = f"{base_id}_{counter}"
            counter += 1

        # Save preset
        metadata["presets"][preset_id] = {
            "name": imported_name,
            "is_default": False,
            "tracks": validated_tracks,
        }

        if not self._write_theme_metadata(theme_id, metadata):
            raise HTTPException(status_code=500, detail="Failed to save preset")

        result = {
            "status": "ok",
            "preset_id": preset_id,
            "name": imported_name,
            "tracks_imported": len(validated_tracks),
        }
        if unknown_tracks:
            result["unknown_tracks"] = unknown_tracks
            result["warning"] = f"{len(unknown_tracks)} track(s) not found in theme"

        return result

    async def export_preset(self, theme_id: str, preset_id: str):
        """Export a preset as JSON for sharing."""
        _, theme_folder = self._get_theme_by_id(theme_id)

        # Use metadata manager if available (preferred path)
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                if preset_id not in metadata_obj.presets:
                    raise HTTPException(status_code=404, detail="Preset not found")

                preset = metadata_obj.presets[preset_id]
                return {
                    "name": preset.get("name", preset_id),
                    "tracks": preset.get("tracks", {}),
                }

        # Fallback to direct file I/O
        metadata = self._read_theme_metadata(theme_id)
        presets = metadata.get("presets", {})

        if preset_id not in presets:
            raise HTTPException(status_code=404, detail="Preset not found")

        preset = presets[preset_id]

        # Return full preset data for export
        return {
            "name": preset.get("name", preset_id),
            "tracks": preset.get("tracks", {}),
        }

    async def rename_theme(self, theme_id: str, request: Request):
        """Rename a theme (updates display name in metadata.json, not the folder)."""
        # Get new name from request body
        try:
            body = await request.json()
            new_name = body.get("name", "").strip()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body")

        if not new_name:
            raise HTTPException(status_code=400, detail="Name is required")

        # Find current theme folder and metadata
        folder = self._find_theme_folder(theme_id)
        if not folder:
            raise HTTPException(status_code=404, detail="Theme not found")

        if not self._theme_metadata_manager:
            raise HTTPException(status_code=500, detail="Metadata manager not available")

        metadata = self._theme_metadata_manager.get_metadata_by_folder(folder)
        if not metadata:
            raise HTTPException(status_code=404, detail="Theme metadata not found")

        old_name = metadata.name

        # Update the name in metadata.json (folder stays the same)
        metadata.name = new_name
        if not self._theme_metadata_manager.save_metadata(metadata.id, metadata):
            raise HTTPException(status_code=500, detail="Failed to save metadata")

        logger.info(f"Renamed theme '{old_name}' to '{new_name}' (folder: {folder.name})")

        return {
            "status": "ok",
            "old_name": old_name,
            "new_name": new_name,
            "theme_id": metadata.id,  # UUID stays the same
        }

    async def rename_preset(self, theme_id: str, preset_id: str, request: Request):
        """Rename a preset."""
        # Get new name from request body
        try:
            body = await request.json()
            new_name = body.get("name", "").strip()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid request body")

        if not new_name:
            raise HTTPException(status_code=400, detail="Name is required")

        _, theme_folder = self._get_theme_by_id(theme_id)

        # Use metadata manager if available (preferred path)
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                if preset_id not in metadata_obj.presets:
                    raise HTTPException(status_code=404, detail="Preset not found")

                # Update the name
                metadata_obj.presets[preset_id]["name"] = new_name

                if not self._theme_metadata_manager.save_metadata(metadata_obj.id, metadata_obj):
                    raise HTTPException(status_code=500, detail="Failed to save changes")

                return {
                    "status": "ok",
                    "preset_id": preset_id,
                    "name": new_name,
                }

        # Fallback to direct file I/O
        metadata = self._read_theme_metadata(theme_id)
        presets = metadata.get("presets", {})

        if preset_id not in presets:
            raise HTTPException(status_code=404, detail="Preset not found")

        # Update the name
        presets[preset_id]["name"] = new_name
        metadata["presets"] = presets

        if not self._write_theme_metadata(theme_id, metadata):
            raise HTTPException(status_code=500, detail="Failed to save changes")

        return {
            "status": "ok",
            "preset_id": preset_id,
            "name": new_name,
        }

    async def toggle_favorite(self, theme_id: str):
        """Toggle favorite status for a theme (stored in metadata.json)."""
        # Get the theme folder using UUID-aware lookup
        theme_folder = self._find_theme_folder(theme_id)
        if not theme_folder:
            return {"error": "Theme not found"}

        # Read current metadata
        metadata = self._read_theme_metadata(theme_id)
        is_favorite = not metadata.get("is_favorite", False)
        metadata["is_favorite"] = is_favorite

        # Save to metadata.json
        if not self._write_theme_metadata(theme_id, metadata):
            return {"error": "Failed to save favorite status"}

        # Also update the metadata manager cache if available
        if self._theme_metadata_manager:
            cached_metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if cached_metadata:
                cached_metadata.is_favorite = is_favorite

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
        """Set categories for a theme (stored in metadata.json)."""
        try:
            body = await request.json()
        except Exception:
            return {"error": "Invalid JSON body"}

        new_categories = body.get("categories", [])

        # Ensure all categories exist in global list (auto-create if needed)
        if self._state_store:
            existing_categories = self._state_store.settings.theme_categories
            for cat in new_categories:
                if cat not in existing_categories:
                    existing_categories.append(cat)
            self._state_store.save()

        # Get the theme folder using UUID-aware lookup
        theme_folder = self._find_theme_folder(theme_id)
        if not theme_folder:
            return {"error": "Theme not found"}

        # Save categories to metadata.json
        metadata = self._read_theme_metadata(theme_id)
        metadata["categories"] = new_categories

        if not self._write_theme_metadata(theme_id, metadata):
            return {"error": "Failed to save categories"}

        # Also update the metadata manager cache if available
        if self._theme_metadata_manager:
            cached_metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if cached_metadata:
                cached_metadata.categories = new_categories

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