import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, HTMLResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from sonorium import runtime
from sonorium.version import __version__
from sonorium.obs import logger
from sonorium import logbuffer
from sonorium import web_server
from sonorium.web_server import Route

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from sonorium.client import Backend


# Template directory
TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"

# Static files directory (CSS, JS)
STATIC_DIR = Path(__file__).parent / "web" / "static"

# Static assets (relative to package root)
PACKAGE_ROOT = Path(__file__).parent.parent
LOGO_PATH = PACKAGE_ROOT / "logo.png"
ICON_PATH = PACKAGE_ROOT / "icon.png"
FAVICON_PATH = PACKAGE_ROOT / "favicon.png"  # icon without its dark tile
DISPLAY_PATH = PACKAGE_ROOT / "display.png"  # logo without its dark banner, for speaker screens


class _MQTTUnavailable(Exception):
    """Standalone mode without a connected MQTT broker (not an error)."""


class RevalidatingStaticFiles(StaticFiles):
    """
    Static files the browser must revalidate before reuse (a cheap 304 when
    unchanged), so an updated app.js/styles.css isn't served from a stale
    cache after an add-on update (#28).
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


# The web app's main port. Standalone listens on 8008 directly (one port for
# UI and streams); the add-on keeps 8080, which HA ingress and its 8008 port
# mapping point at.
WEB_PORT = 8008 if runtime.STANDALONE else 8080


class WebApp:
    """
    Sonorium's web app: the UI, the REST API and the audio streams, on one
    FastAPI app (`app`). Its own routes are the table in routes(); the v2
    API, sign-in and plugins add theirs as they start. serve() runs it.
    """
    port = WEB_PORT

    def __init__(self, backend: "Backend"):
        self.app = web_server.new_app(f'Sonorium {__version__} Streaming API')
        web_server.add_routes(self.app, self.routes())
        self.backend = backend
        
        # v2 components (initialized lazily)
        self._v2_initialized = False
        self._state_store = None
        self._ha_registry = None
        self._media_controller = None
        self._session_manager = None
        self._group_manager = None
        self._channel_manager = None
        self._background = None
        self._mqtt_manager = None
        self._theme_refresh_task = None
        self._plugin_manager = None
        self._plugin_host = None  # plugins/host.py: events, actions, scheduler, triggers
        self._theme_metadata_manager = None
        self._network_service = None  # standalone only
        self._cards = None  # Home Assistant dashboard cards (ha/cards.py)

        # Home Assistant loads the dashboard cards from Sonorium (Docker /
        # Windows): a module script from another origin needs CORS
        @self.app.middleware("http")
        async def cards_cors(request: Request, call_next):
            response = await call_next(request)
            if request.url.path == "/static/ha/sonorium-cards.js":
                from sonorium.ha.cards import origin_of
                origin = (request.headers.get("origin") or "").lower()
                linked = origin_of(runtime.load_connection().get("ha_url", "")) if runtime.STANDALONE else ""
                if origin and origin == linked:
                    response.headers["Access-Control-Allow-Origin"] = request.headers["origin"]
                    response.headers["Vary"] = "Origin"
            return response

        # A channel's policy (Zones) refused a change: its status and message
        from sonorium.core.channel_policy import ChannelRefused

        @self.app.exception_handler(ChannelRefused)
        async def channel_refused(request: Request, exc: ChannelRefused):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": exc.detail}, status_code=exc.status)

        # Older theme, category and channel routes' 200 {"error"} answers get an error status (G10)
        from sonorium.web.legacy_errors import LegacyErrorStatus
        self.app.add_middleware(LegacyErrorStatus)

        # Register startup event to initialize v2
        @self.app.on_event("startup")
        async def startup_event():
            logger.debug("FastAPI startup event triggered")
            # uvicorn's loggers don't pass messages up, and are set up just before this
            logbuffer.attach("uvicorn")
            await self._start_auth()
            await self.initialize_v2()
        
        # Register shutdown event to stop the background loop
        @self.app.on_event("shutdown")
        async def shutdown_event():
            logger.info("FastAPI shutdown event triggered")
            await self.shutdown_v2()
            await self._stop_auth()

        # Sign-in, users, linked phones and keys: one middleware checks every
        # route (sonorium/auth); it's outermost, so it runs first
        from sonorium.auth.middleware import AuthMiddleware, AuthService
        from sonorium.auth.routes import create_core_auth_router
        self.auth = AuthService()
        self.auth.app = self.app  # sign-in plugins add their routes to it
        self._https_host_port = None  # the HTTPS port as the LAN sees it (add-on: its port mapping)
        self._announcer = None
        self._announce_task = None
        self.app.add_middleware(AuthMiddleware, service=self.auth)
        self.app.include_router(create_core_auth_router(
            self.auth,
            stream_url=self._stream_base_url,
            features=lambda: runtime.features(self._state_store.settings.feature_overrides if self._state_store else None),
            version=__version__,
            https_host_port=self._https_port_outside,
            mdns_host=lambda: self._announcer.hostname if self._announcer is not None else None,
        ))

        # Mount static files (CSS, JS) for the web UI
        if STATIC_DIR.exists():
            self.app.mount("/static", RevalidatingStaticFiles(directory=STATIC_DIR), name="static")
            logger.debug(f"Mounted static files from: {STATIC_DIR}")
        else:
            logger.warning(f"Static directory not found: {STATIC_DIR}")

    def routes(self) -> list[Route]:
        """This app's own routes, in order: /stream/channel{n} must come before the catch-all /stream/{id}."""
        return [
            # Web UI
            Route("GET", "/", self.web_ui),

            # Standalone connection settings (404 in the HA add-on)
            Route("GET", "/api/connection", self.get_connection),
            Route("PUT", "/api/connection", self.put_connection),
            Route("DELETE", "/api/connection/ha", self.delete_connection_ha),
            Route("DELETE", "/api/connection/mqtt", self.delete_connection_mqtt),
            Route("POST", "/api/connection/test", self.test_connection),
            # Home Assistant dashboard cards: status and the file (to add by hand)
            Route("GET", "/api/ha-cards", self.get_ha_cards),
            Route("GET", "/api/ha-cards/download", self.download_ha_cards),
            Route("GET", "/v1", self.legacy_ui),
            Route("GET", "/logo.png", self.serve_logo),
            Route("GET", "/display.png", self.serve_display_image),
            Route("GET", "/favicon.png", self.serve_favicon),

            # Logs: kept apart from the v2 API so they work when it fails to start
            Route("GET", "/logs", self.logs_page),
            Route("GET", "/api/logs", self.get_logs),
            Route("GET", "/api/logs/download", self.download_logs),

            # Install type and the features it shows (Settings > Advanced)
            Route("GET", "/api/install", self.get_install),
            Route("PUT", "/api/install/features", self.put_install_features),
            
            # Streaming - channel-based (new) - MUST come before theme-based!
            Route("GET", "/stream/channel{channel_id:int}", self.stream_channel),
            
            # Streaming - theme-based (legacy, still supported)
            Route("GET", "/stream/{id}", self.stream),
            
            # Theme API
            Route("GET", "/api/themes", self.list_themes),
            Route("POST", "/api/themes/refresh", self.refresh_themes),
            Route("POST", "/api/themes/{theme_id}/favorite", self.toggle_favorite),
            Route("GET", "/api/themes/{theme_id}", self.get_theme),

            # Track Mixer API
            Route("GET", "/api/themes/{theme_id}/tracks", self.get_theme_tracks),
            Route("GET", "/api/themes/{theme_id}/tracks/{track_name:path}/audio", self.get_track_audio),
            Route("PUT", "/api/themes/{theme_id}/tracks/{track_name:path}/presence", self.set_track_presence),
            Route("PUT", "/api/themes/{theme_id}/tracks/{track_name:path}/muted", self.set_track_muted),
            Route("PUT", "/api/themes/{theme_id}/tracks/{track_name:path}/volume", self.set_track_volume),
            Route("PUT", "/api/themes/{theme_id}/tracks/{track_name:path}/playback_mode", self.set_track_playback_mode),
            Route("PUT", "/api/themes/{theme_id}/tracks/{track_name:path}/seamless_loop", self.set_track_seamless_loop),
            Route("PUT", "/api/themes/{theme_id}/tracks/{track_name:path}/fades", self.set_track_fades),
            Route("PUT", "/api/themes/{theme_id}/tracks/{track_name:path}/ebb", self.set_track_ebb),
            Route("PUT", "/api/themes/{theme_id}/tracks/{track_name:path}/exclusive", self.set_track_exclusive),

            # Groups (subfolders of a theme, Themes 2.0)
            Route("GET", "/api/themes/{theme_id}/groups", self.list_groups),
            Route("PUT", "/api/themes/{theme_id}/groups/{group}", self.update_group),
            Route("POST", "/api/themes/{theme_id}/groups", self.create_group_folder),
            Route("POST", "/api/themes/{theme_id}/groups/{group}/rename", self.rename_group_folder),
            Route("DELETE", "/api/themes/{theme_id}/groups/{group}", self.delete_group_folder),
            Route("POST", "/api/themes/{theme_id}/tracks/{track_name:path}/move", self.move_track_to_group),
            Route("POST", "/api/themes/{theme_id}/tracks/reset", self.reset_theme_tracks),

            # Linked files from other themes (docs/THEME_FORMAT.md 2.6)
            Route("GET", "/api/themes/{theme_id}/links/source/{source_id}", self.list_link_source_tracks),
            Route("POST", "/api/themes/{theme_id}/links", self.add_links),
            Route("POST", "/api/themes/{theme_id}/links/{key:path}/copy-in", self.copy_link_in),
            Route("DELETE", "/api/themes/{theme_id}/links/{key:path}", self.delete_link),

            # Theme rename
            Route("PUT", "/api/themes/{theme_id}/rename", self.rename_theme),

            # Preset API
            Route("GET", "/api/themes/{theme_id}/presets", self.list_presets),
            Route("POST", "/api/themes/{theme_id}/presets", self.create_preset),
            Route("POST", "/api/themes/{theme_id}/presets/{preset_id}/load", self.load_preset),
            Route("GET", "/api/themes/{theme_id}/presets/{preset_id}", self.get_preset),
            Route("PUT", "/api/themes/{theme_id}/presets/{preset_id}", self.update_preset),
            Route("DELETE", "/api/themes/{theme_id}/presets/{preset_id}", self.delete_preset),
            Route("PUT", "/api/themes/{theme_id}/presets/{preset_id}/default", self.set_default_preset),
            Route("PUT", "/api/themes/{theme_id}/presets/{preset_id}/rename", self.rename_preset),
            Route("POST", "/api/themes/{theme_id}/presets/import", self.import_preset),
            Route("GET", "/api/themes/{theme_id}/presets/{preset_id}/export", self.export_preset),

            # Category API
            Route("GET", "/api/categories", self.list_categories),
            Route("POST", "/api/categories", self.create_category),
            Route("DELETE", "/api/categories/{category_name}", self.delete_category),
            Route("POST", "/api/themes/{theme_id}/categories", self.set_theme_categories),

            Route("GET", "/api/status", self.status),
            # Where speakers and the app reach the streams: the direct port, never ingress
            Route("GET", "/api/stream/url", self.stream_urls),
            
            # Channel API
            Route("GET", "/api/channels", self.list_channels),
            Route("GET", "/api/channels/{channel_id}", self.get_channel),
        ]

    async def initialize_v2(self):
        """Initialize v2 components after MQTT client is ready."""
        if self._v2_initialized:
            return
        
        try:
            from sonorium.core.state import StateStore
            from sonorium.core.session_manager import SessionManager
            from sonorium.core.group_manager import GroupManager
            from sonorium.core.channel import ChannelManager
            from sonorium.core.background import BackgroundLoop
            from sonorium.ha.registry import HARegistry
            from sonorium.ha.media_controller import HAMediaController
            from sonorium.web.api_v2 import create_api_router
            from sonorium.settings import settings
            
            logger.debug("Initializing Sonorium v2 components...")
            
            # Initialize state store
            self._state_store = StateStore()
            self._state_store.load()
            # Settings > Advanced's playback values: the mixer reads them from the store
            from sonorium import playback_values
            playback_values.set_source(lambda: self._state_store.settings.playback_values)
            logger.debug(f"  State loaded: {len(self._state_store.sessions)} sessions, {len(self._state_store.speaker_groups)} groups")

            # Initialize theme metadata manager
            from sonorium.core.theme_metadata import ThemeMetadataManager
            audio_path = self.backend.device.audio_dir
            self._theme_metadata_manager = ThemeMetadataManager(audio_path)
            theme_metadata = self._theme_metadata_manager.scan_themes()
            logger.debug(f"  Theme metadata: {len(theme_metadata)} themes scanned")

            # Migrate any theme data from state.json to metadata.json (one-time migration)
            self._migrate_theme_data_to_metadata()

            # Linked files (in other themes' folders) join their themes
            self._attach_linked_tracks()

            # Apply saved track settings to themes (now reads from metadata.json)
            self._apply_saved_track_settings()

            # Theme cycling became playlists: convert channels' cycling once
            # (before anything saves state.json, which no longer keeps cycle_config)
            self._migrate_cycling()

            # Settings > Audio Settings > Master output gain (0-100) is the mix's output gain
            self._apply_master_gain()

            # Initialize channel manager
            max_channels = getattr(settings, 'max_channels', 6)
            self._channel_manager = ChannelManager(max_channels=max_channels)
            logger.debug(f"  Channel manager: {max_channels} channels available")
            
            # Initialize HA registry
            api_url = settings.ha_core_api
            self._ha_registry = HARegistry(api_url, settings.token)
            # Per-speaker names, rooms, volume offsets and play-via choices
            self._ha_registry.set_speaker_settings_source(lambda: self._state_store.settings.speaker_settings)
            # Floors and areas made in Sonorium, merged with Home Assistant's by name
            if runtime.feature_enabled("space_editing"):
                self._ha_registry.set_local_spaces_source(lambda: self._state_store.settings.local_spaces)

            # Speakers found on the LAN or added by address, next to any HA speakers
            if runtime.feature_enabled("network_speakers", self._state_store.settings.feature_overrides):
                self._init_network_speakers()

            try:
                self._ha_registry.refresh()
                logger.debug(f"  HA registry loaded: {len(self._ha_registry.hierarchy.floors)} floors")
            except Exception as e:
                logger.warning(f"  Could not load HA registry (floors/areas may not work): {e}")

            # Older settings used "no speakers listed = all enabled"; make the list
            # exact (keeping what was visible) now that the speakers are known
            try:
                if self._state_store.settings.migrate_enabled_speakers(self._ha_registry.get_all_speaker_ids()):
                    self._state_store.save()
                    logger.info(f"  Speaker settings updated: {len(self._state_store.settings.enabled_speakers)} speakers switched on")
            except Exception as e:
                logger.warning(f"  Could not update speaker settings: {e}")

            # Initialize media controller
            self._media_controller = HAMediaController(api_url, settings.token)
            if self._network_service:
                # Route net:* speakers to the network service, the rest to HA
                from sonorium.network.router import SpeakerRouter
                self._media_controller = SpeakerRouter(
                    self._media_controller if runtime.ha_configured() else None,
                    self._network_service,
                )
            # Volume offsets and play-via for every speaker command
            from sonorium.core.speaker_settings import SpeakerOutputs
            self._media_controller = SpeakerOutputs(
                self._media_controller,
                lambda: self._state_store.settings.speaker_settings,
                self._ha_registry.get_play_target,
            )

            # Use configured stream URL (from SONORIUM__STREAM_URL env var)
            stream_base_url = settings.stream_url
            logger.debug(f"  Stream base URL: {stream_base_url}")
            
            # Initialize session manager (with metadata manager)
            self._session_manager = SessionManager(
                self._state_store,
                self._ha_registry,
                self._media_controller,
                stream_base_url,
                channel_manager=self._channel_manager,
                themes=self.backend.device.themes,
                theme_metadata_manager=self._theme_metadata_manager,
            )

            # Background loop: sequence and playlist timers, idle sessions
            self._background = BackgroundLoop(self._session_manager)
            
            self._group_manager = GroupManager(
                self._state_store,
                self._ha_registry,
            )

            # Plugin host: the services plugins use (events, actions, scheduler,
            # triggers), next to state.json
            try:
                from sonorium.plugins.host import PluginHost
                from sonorium.plugins import events as plugin_events
                plugin_events.get_bus().bind_loop()
                self._plugin_host = PluginHost(
                    self._state_store.state_file.parent,
                    auth=self.auth,
                    session_manager=self._session_manager,
                    state_store=self._state_store,
                    registry=self._ha_registry,
                )
            except Exception as e:
                logger.warning(f"  Plugin framework unavailable: {e}")
                self._plugin_host = None

            # The framework's audio and connection services (each one failing only disables itself)
            if self._plugin_host:
                self._init_framework_services()

            # Initialize plugin manager
            try:
                from sonorium.plugins.manager import PluginManager
                audio_path = self.backend.device.audio_dir if self.backend and self.backend.device else None
                self._plugin_manager = PluginManager(
                    self._state_store,
                    audio_path=audio_path,
                    host=self._plugin_host,
                )
                await self._plugin_manager.initialize()
                logger.debug(f"  Plugin manager: {len(self._plugin_manager.plugins)} plugin(s) loaded")
            except Exception as e:
                logger.warning(f"  Failed to initialize plugin manager: {e}")
                self._plugin_manager = None

            # Initialize MQTT entity manager for Home Assistant integration
            # (standalone mode may run without a connected broker)
            try:
                if runtime.STANDALONE and not self.backend.mqtt_client.is_connected:
                    # Optional in standalone; already logged by the MQTT client
                    raise _MQTTUnavailable()
                from sonorium.ha.mqtt_entities import SonoriumMQTTManager
                self._mqtt_manager = SonoriumMQTTManager(
                    state_store=self._state_store,
                    session_manager=self._session_manager,
                    mqtt_client=self.backend.mqtt_client,
                    theme_metadata_manager=self._theme_metadata_manager,
                    version=__version__,
                )
                # Set available themes for the theme select entity
                themes = [{"id": t.id, "name": t.name} for t in self.backend.device.themes]
                self._mqtt_manager.set_themes(themes)

                # Incoming MQTT messages: the entity manager's commands, then plugins' subscriptions
                if self._plugin_host:
                    self._plugin_host.mqtt.prefix = self._mqtt_manager.prefix
                    self._plugin_host.mqtt.bind(self.backend.mqtt_client, self._mqtt_manager.handle_command)
                else:
                    self.backend.mqtt_client.set_message_handler(self._mqtt_manager.handle_command)

                await self._mqtt_manager.initialize()
                logger.debug(f"  MQTT entity manager: {len(self._state_store.sessions)} session entities published")
            except _MQTTUnavailable:
                self._mqtt_manager = None
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
                plugin_manager=self._plugin_manager,
                mqtt_manager=self._mqtt_manager,
                on_themes_changed=self.schedule_theme_refresh,
                network_service=self._network_service,
                on_master_gain=self._apply_master_gain,
            )
            self.app.include_router(api_router)

            # Plugin framework endpoints (events, triggers, schedules, extensions, sounds, announcements)
            try:
                from sonorium.web.plugin_framework_api import create_plugin_framework_router
                self.app.include_router(create_plugin_framework_router(self._plugin_manager, self._plugin_host))
            except Exception as e:
                logger.warning(f"  Plugin framework endpoints unavailable: {e}")
            
            # Keep MQTT entities in sync when idle sessions are stopped, and
            # when a sequence or playlist moves a channel on
            if self._mqtt_manager:
                self._background.on_session_stopped = self._mqtt_manager.update_session_state
                self._session_manager.runner.on_change = self._mqtt_manager.update_session_state
                if self._plugin_host:  # plugins' changes reach Home Assistant like the UI's
                    self._plugin_host.on_change = self._mqtt_manager.update_session_state

            # Home Assistant dashboard cards: installed once both HA and MQTT are linked
            self._init_dashboard_cards(stream_base_url)

            # Start the background loop
            await self._background.start()
            logger.debug("  Background loop started")

            # Plugin scheduler and the alert bar for failures
            if self._plugin_host:
                try:
                    await self._plugin_host.start()
                except Exception as e:
                    logger.warning(f"  Plugin scheduler could not start: {e}")

            # Network speaker discovery runs in the background: the UI is up meanwhile
            if self._network_service:
                self._network_service.start()

            self._v2_initialized = True
            logger.debug("  Sonorium v2 initialization complete!")
            try:
                from sonorium.plugins import events as plugin_events
                plugin_events.publish("sonorium.started", {"version": __version__})
            except Exception:
                pass
            self._log_startup_summary()
            
        except ImportError as e:
            logger.error(f"  Failed to import v2 modules: {e}")
        except Exception as e:
            logger.error(f"  Failed to initialize v2 components: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # Plugins are loaded (or failed): sign-in decides from here (auth/service.py)
            self.auth.mark_ready()
    
    def _init_framework_services(self):
        """
        Sounds over channels, volume ramps, audio outputs, Home Assistant for
        plugins and the Plugin packages switch (docs/PLUGIN_DEVELOPMENT.md).
        """
        host = self._plugin_host
        try:
            from sonorium.core.audio_outputs import AudioOutputs
            from sonorium.core.overlay_service import OverlayService
            from sonorium.core.ramp_service import RampService
            from sonorium.recording import SAMPLE_RATE
            publish = host.bus.publish
            outputs = AudioOutputs(self._session_manager, host.store, rate=SAMPLE_RATE)
            host.set_audio(OverlayService(self._session_manager, publish, rate=SAMPLE_RATE),
                           RampService(self._session_manager, publish, rate=SAMPLE_RATE,
                                       on_change=lambda s: host._changed(s)),
                           outputs)
            # A channel sent to an audio output is sent there again when it starts
            host.bus.subscribe("channel.started", lambda e: outputs.channel_started(e.data.get("channel_id")),
                               owner=None)
        except Exception as e:
            logger.warning(f"  Sounds, ramps and audio outputs unavailable: {e}")
        try:
            from sonorium.plugins.homeassistant import HomeAssistant
            from sonorium.settings import settings
            host.homeassistant = HomeAssistant(lambda: (settings.ha_core_api, settings.token),
                                               configured=runtime.ha_configured)
        except Exception as e:
            logger.warning(f"  Home Assistant for plugins unavailable: {e}")
        host.packages_allowed = lambda: runtime.feature_enabled(
            "plugin_packages", self._state_store.settings.feature_overrides if self._state_store else None)

    def _migrate_cycling(self):
        """Turn channels' theme cycling into playlists, once (core/playlists.migrate_cycling)."""
        from sonorium.core.playlists import migrate_cycling
        state = self._state_store.state
        if state.cycling_migrated:
            return
        try:
            themes = sorted(self.backend.device.themes or [], key=lambda t: (t.name or "").lower())
            migrate_cycling(state, [t.id for t in themes])
            self._state_store.save()
        except Exception as e:
            logger.warning(f"  Could not convert theme cycling to playlists: {e}")

    def _apply_master_gain(self):
        """
        Settings > Audio Settings > Master output gain (0-100 %) as the mix's
        output gain (device.master_volume, 0-10; 60 % = 6.0, the old default),
        the same scale as the Home Assistant "Master Volume" number.
        """
        try:
            gain = int(self._state_store.settings.master_gain)
            self.backend.device.master_volume = max(0, min(100, gain)) / 100 * 10.0
        except Exception as e:
            logger.warning(f"  Could not apply the master output gain: {e}")

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
            logger.debug("  Theme data migration complete")

    def _apply_saved_track_settings(self):
        """Apply saved track settings from metadata.json to theme instances on startup."""
        from sonorium.core.theme_metadata import EBB_TRACK_SETTINGS, reset_ebb_settings
        if not self._theme_metadata_manager:
            return

        from sonorium.recording import PlaybackMode

        device = self.backend.device
        if not device.themes:
            return

        logger.debug("  Applying saved track settings to themes...")
        for theme in device.themes:
            if not theme.tracks:
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
            # Group settings (Themes 2.0), read by the theme's grouped tracks
            theme.groups = dict(getattr(metadata, "groups", None) or {})

            for inst in theme.tracks:
                track_settings = metadata.tracks.get(inst.name)
                if not track_settings:
                    # Use defaults
                    inst.presence = 1.0
                    inst.is_enabled = True
                    inst.volume = 1.0
                    inst.playback_mode = PlaybackMode.AUTO
                    inst.crossfade_enabled = True
                    inst.exclusive = False
                    inst.fade_in = inst.fade_out = 0.0
                    reset_ebb_settings(inst)
                    if getattr(inst.meta, "link", None):
                        inst.is_enabled = False  # a linked file without settings stays off
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
                inst.fade_in, inst.fade_out = track_settings.fade_in, track_settings.fade_out
                for key in EBB_TRACK_SETTINGS:
                    setattr(inst, key, getattr(track_settings, key))

            logger.debug(f"    Applied settings to theme '{theme.name}'")

    async def shutdown_v2(self):
        """Shutdown v2 components gracefully."""
        if self._background:
            await self._background.stop()
        if self._plugin_host:
            try:
                await self._plugin_host.stop()
            except Exception as e:
                logger.warning(f"Plugin framework stop failed: {e}")
        if self._network_service:
            await self._network_service.stop()

    # --- Sign-in (sonorium/auth) ---

    def main_listener(self):
        """The main port (WEB_PORT): it runs the app's start-up and shut-down."""
        return web_server.listener(self.app, self.port)

    def extra_listeners(self) -> list:
        """
        The app's HTTPS port (8808, SONORIUM_HTTPS_PORT) with the pinned certificate,
        and in the add-on the ingress port (8099) HA's sidebar uses, never mapped outside.
        """
        from sonorium.auth import config
        listeners = []
        port = config.https_port()
        if port:
            identity = self.auth.identity
            if identity.tls_ready:
                listeners.append(web_server.listener(self.app, port, lifespan="off",
                                                     ssl_certfile=str(identity.cert_path),
                                                     ssl_keyfile=str(identity.key_path)))
                logger.info(f"HTTPS for the Sonorium app on port {port}")
        ingress = config.ingress_port()
        if ingress:
            listeners.append(web_server.listener(self.app, ingress, lifespan="off"))
        return listeners

    async def serve(self) -> None:
        """Serve the app until shut down: the main port, then the extra ones beside it."""
        main = self.main_listener()
        await web_server.serve(main, self.extra_listeners())

    @staticmethod
    def _stream_base_url() -> str:
        """The direct base URL speakers use (Settings > Connection's stream URL, or the detected address)."""
        from sonorium.settings import settings
        return settings.stream_url

    def _https_port_outside(self) -> int:
        from sonorium.auth import config
        if self._https_host_port is None:
            return config.https_port()
        return self._https_host_port

    async def _start_auth(self):
        """Load accounts, make the server identity and certificate, warn about the bypass, announce on mDNS."""
        import asyncio
        from sonorium.auth import config
        try:
            self.auth.note_install()
            from sonorium.plugins.manager import DEFAULT_PLUGINS_DIR
            self.auth.find_expected_providers(DEFAULT_PLUGINS_DIR)
            identity = await asyncio.to_thread(lambda: self.auth.identity)
            if config.bypass_login():
                logger.warning("BYPASS_LOGIN is on: anyone on your network can use and change Sonorium without signing in. "
                               "Reset your account under Settings > Users & devices, then set it back to false.")
            if config.web_ui_only_through_ha():
                logger.info("The web UI is only served through Home Assistant's sidebar (web_ui_only_through_ha)")
            if not runtime.STANDALONE:
                self._https_host_port = await asyncio.to_thread(config.addon_host_port, config.https_port())
            if runtime.STANDALONE:  # the add-on isn't on the host network: the QR code carries its address
                from sonorium.auth.announce import Announcer
                from sonorium.auth.routes import API_VERSION
                self._announcer = Announcer()
                # in the background: checking that sonorium.local is free takes a moment
                self._announce_task = asyncio.create_task(asyncio.to_thread(
                    self._announcer.start, server_id=identity.server_id, version=__version__, api=API_VERSION,
                    base_url=self._stream_base_url(),
                    https_port=self._https_port_outside() if identity.tls_ready else 0,
                    login_required=self.auth.login_required))  # TXT pair follows Require login
            else:
                logger.info("mDNS: the Home Assistant add-on isn't on the host network, so sonorium.local isn't "
                            "announced; the app links with the QR code, which carries the address")
        except Exception as e:
            logger.error(f"Sign-in setup failed: {e}")

    async def _stop_auth(self):
        import asyncio
        try:
            self.auth.flush()
            if self._announcer is not None:
                if self._announce_task is not None:
                    await asyncio.wait({self._announce_task}, timeout=20)
                await asyncio.to_thread(self._announcer.stop)
        except Exception:
            pass

    async def stream_urls(self):
        """The stream URLs on the direct port (what speakers and the app play), never an ingress URL."""
        base = self._stream_base_url().rstrip("/")
        channels = {}
        if self._session_manager is not None:
            for session in self._session_manager.list():
                try:
                    channels[session.id] = self._session_manager.get_stream_url(session)
                except Exception:
                    pass
        return {"base": base, "channels": channels}

    def _ha_linked(self) -> bool:
        """Home Assistant is linked: always for the add-on; standalone once its registry loaded."""
        registry = self._ha_registry
        if not runtime.ha_configured() or registry is None or registry.ha_removed:
            return False
        return runtime.INSTALL == "addon" or registry._hierarchy is not None

    def _init_dashboard_cards(self, sonorium_url: str):
        """Install the dashboard cards in the background (ha/cards.py); errors only leave a status."""
        import asyncio
        try:
            from sonorium.ha.cards import DashboardCards
            self._cards = DashboardCards(
                self._ha_registry.ws_commands if self._ha_registry else None,
                install=runtime.INSTALL,
                version=__version__,
                ha_linked=self._ha_linked,
                mqtt_manager=self._mqtt_manager,
                ha_url=runtime.load_connection().get("ha_url", "") if runtime.STANDALONE else "",
                sonorium_url=sonorium_url or "",
            )
            if self._mqtt_manager:
                self._mqtt_manager.on_discovery = self._cards.schedule_entity_refresh
            asyncio.get_running_loop().create_task(self._cards.start())
        except Exception as e:
            logger.warning(f"  Dashboard cards unavailable: {e}")

    async def get_ha_cards(self):
        """The dashboard cards' status line for Settings › Connection."""
        if self._cards is None:
            from sonorium.ha.cards import LINES, NEEDS_HA
            return {"state": NEEDS_HA, "line": LINES[NEEDS_HA], "reason": ""}
        return self._cards.status()

    async def download_ha_cards(self):
        """The dashboard cards' file, to add to Home Assistant by hand."""
        from sonorium.ha.cards import CARD_FILE, CARD_NAME
        if not CARD_FILE.exists():
            raise HTTPException(status_code=404, detail="Card file not found")
        return FileResponse(CARD_FILE, media_type="text/javascript", filename=CARD_NAME)

    def _init_network_speakers(self):
        """Network speaker discovery and streaming (only when the network_speakers feature is on)."""
        try:
            from sonorium.network.service import NetworkSpeakerService
            self._network_service = NetworkSpeakerService(config_dir=runtime.CONNECTION_FILE.parent)
            self._ha_registry.set_extra_speaker_source(self._network_service.hierarchy_speakers)
            self._network_service.on_change = self._ha_registry.merge_extra_speakers
            known = len(self._network_service.speakers)
            logger.debug(f"  Network speakers: {known} saved, discovery starts in the background")
        except Exception as e:
            logger.warning(f"Network speakers unavailable: {e}")
            self._network_service = None

    async def get_connection(self):
        """Standalone connection settings, without secrets."""
        from sonorium import runtime
        if not runtime.features()["connection_settings"]["available"]:
            raise HTTPException(status_code=404, detail="Not available in the Home Assistant app")
        conn = runtime.load_connection()
        return {
            "standalone": True,
            "ha_url": conn.get("ha_url", ""),
            "ha_token_set": bool(conn.get("ha_token")),
            "ha_connected": bool(runtime.ha_configured() and self._ha_registry and self._ha_registry._hierarchy is not None),
            "mqtt_host": conn.get("mqtt_host", ""),
            "mqtt_port": conn.get("mqtt_port", 1883),
            "mqtt_username": conn.get("mqtt_username", ""),
            "mqtt_password_set": bool(conn.get("mqtt_password")),
            "mqtt_connected": self.backend.mqtt_client.is_connected,
            "stream_url": conn.get("stream_url", ""),
        }

    async def test_connection(self, request: Request):
        """
        Try Home Assistant or MQTT with the saved values and the form's (unsaved)
        ones on top: {"kind": "ha"|"mqtt", "values": {...}}. Nothing is saved.
        Answers {"success", "message"}.
        """
        from sonorium import runtime
        if not runtime.features()["connection_settings"]["available"]:
            raise HTTPException(status_code=404, detail="Not available in the Home Assistant app")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(body, dict) or body.get("kind") not in ("ha", "mqtt"):
            raise HTTPException(status_code=400, detail="'kind' must be ha or mqtt")
        values = {**runtime.load_connection(),
                  **{k: v for k, v in (body.get("values") or {}).items()
                     if k in runtime.CONNECTION_FIELDS and v not in (None, "")}}
        from sonorium.connection_test import test_ha, test_mqtt
        if body["kind"] == "ha":
            return await test_ha(values.get("ha_url", ""), values.get("ha_token", ""))
        return await test_mqtt(values.get("mqtt_host", ""), values.get("mqtt_port") or 1883,
                               values.get("mqtt_username") or None, values.get("mqtt_password") or None)

    async def put_connection(self, request: Request):
        """
        Save standalone connection settings, then restart Sonorium so every
        component picks them up. Empty token/password fields keep the saved value.
        """
        from sonorium import runtime
        if not runtime.features()["connection_settings"]["available"]:
            raise HTTPException(status_code=404, detail="Not available in the Home Assistant app")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Expected a JSON object")
        updates = {k: body[k] for k in runtime.CONNECTION_FIELDS if k in body}
        if "mqtt_port" in updates and updates["mqtt_port"] not in (None, ""):
            try:
                updates["mqtt_port"] = int(updates["mqtt_port"])
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="MQTT port must be a number")
        for key in ("ha_url", "stream_url"):
            value = (updates.get(key) or "").strip()
            if value and not value.startswith(("http://", "https://")):
                raise HTTPException(status_code=400, detail=f"{key} must start with http:// or https://")
        runtime.save_connection(updates)
        logger.info("Connection settings saved; restarting Sonorium to apply them")
        self._restart_soon()
        return {"status": "ok", "restarting": True}

    async def delete_connection_ha(self):
        """
        Remove the Home Assistant connection without a restart. Its floors,
        areas and speakers leave every list at once; Sonorium's own floors and
        areas and the network speakers stay (one merged into an HA speaker is
        listed on its own again). Channels keep playing on their remaining
        speakers. Speaker settings and groups are kept as they are.
        """
        if not runtime.features()["connection_settings"]["available"]:
            raise HTTPException(status_code=404, detail="Not available in the Home Assistant app")
        runtime.clear_connection(runtime.HA_FIELDS)
        runtime.clear_ha_env()

        targets = {}
        if self._ha_registry is not None:
            targets = self._ha_registry.remove_home_assistant()
        self._carry_over_enabled(targets)

        # No Home Assistant to send commands to: HA entity IDs fail quietly
        # in the router, and saved selections no longer resolve to them
        controller = getattr(self._media_controller, "controller", None)
        if controller is not None and hasattr(controller, "ha_controller"):
            controller.ha_controller = None
        device = getattr(self.backend, "device", None)
        if device is not None and hasattr(device, "media_players"):
            from sonorium.utils import IndexList
            device.media_players = IndexList()

        logger.info("Home Assistant connection removed")
        return {"status": "ok", "connection": await self.get_connection()}

    def _carry_over_enabled(self, targets: dict):
        """A network speaker that was merged into a switched-on HA speaker is switched on too."""
        if not targets or self._state_store is None:
            return
        settings = self._state_store.settings
        if not settings.enabled_speakers_exact:
            return
        changed = False
        for ha_id, network_id in targets.items():
            if ha_id in settings.enabled_speakers and network_id not in settings.enabled_speakers:
                settings.enabled_speakers.append(network_id)
                changed = True
        if changed:
            self._state_store.save()

    async def delete_connection_mqtt(self):
        """
        Remove the MQTT connection without a restart: Sonorium's Home
        Assistant entities are removed first (empty retained discovery
        configs) while the broker is still connected, then MQTT disconnects.
        """
        if not runtime.features()["connection_settings"]["available"]:
            raise HTTPException(status_code=404, detail="Not available in the Home Assistant app")
        runtime.clear_connection(runtime.MQTT_FIELDS)
        runtime.clear_mqtt_env()

        mqtt_client = self.backend.mqtt_client
        if self._mqtt_manager is not None:
            try:
                if mqtt_client.is_connected:
                    await self._mqtt_manager.remove_all_entities()
            except Exception as e:
                logger.warning(f"Could not remove Sonorium's MQTT entities: {e}")
            self._mqtt_manager.stop_publishing()

        restart_required = False
        try:
            await mqtt_client.close()
        except Exception as e:
            logger.warning(f"Could not disconnect MQTT cleanly; a restart finishes removing it: {e}")
            restart_required = True

        logger.info("MQTT connection removed")
        return {
            "status": "ok",
            "connection": await self.get_connection(),
            "restart_required": restart_required,
        }

    def _restart_soon(self):
        """Restart Sonorium in place a moment after the current response is sent."""
        import asyncio
        import os
        import sys

        def restart():
            os.execv(sys.executable, [sys.executable, "-m", "sonorium.entrypoint"])

        asyncio.get_running_loop().call_later(1.0, restart)

    def _feature_overrides(self) -> dict:
        return dict(self._state_store.settings.feature_overrides) if self._state_store else {}

    async def get_install(self):
        """How Sonorium is installed, and which features that install shows and runs."""
        return {
            "install": runtime.INSTALL,
            "label": runtime.INSTALL_LABEL,
            "version": __version__,
            "features": runtime.features(self._feature_overrides()),
        }

    async def put_install_features(self, request: Request):
        """
        Settings > Advanced: switch on (or back off) features that are off by
        default for this install. Takes effect after a restart.
        Body: {"network_speakers": true, "restart": true}
        """
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(body, dict) or not self._state_store:
            raise HTTPException(status_code=400, detail="Expected a JSON object")
        overrides = self._feature_overrides()
        for name, value in body.items():
            if name == "restart":
                continue
            if name not in runtime.FEATURES or runtime.feature_state(name) != "off":
                raise HTTPException(status_code=400, detail=f"'{name}' can't be changed here")
            if value:
                overrides[name] = True
            else:
                overrides.pop(name, None)
        self._state_store.settings.feature_overrides = overrides
        self._state_store.save()
        logger.info(f"Advanced settings changed: {overrides or 'all defaults'}")
        try:
            from sonorium.plugins import events
            events.publish("settings.changed", {"section": "features", "keys": sorted(k for k in body if k != "restart")})
        except Exception:
            pass
        restarting = bool(body.get("restart"))
        if restarting:
            self._restart_soon()
        return {**(await self.get_install()), "restarting": restarting}

    def _log_startup_summary(self):
        """One-line summary at normal log level; details are at debug level."""
        try:
            themes = len(self.backend.device.themes)
            speakers = len(self._ha_registry.hierarchy.get_all_speakers()) if self._ha_registry and self._ha_registry.hierarchy else 0
            sessions = len(self._state_store.sessions) if self._state_store else 0
            mqtt = "connected" if self._mqtt_manager else "not available"
            logger.info(f"Sonorium {__version__} ({runtime.INSTALL_LABEL}) ready: {themes} themes, {speakers} speakers detected, {sessions} channels, MQTT {mqtt}")
        except Exception as e:
            logger.warning(f"Could not build startup summary: {e}")

    async def web_ui(self):
        """Serve the main web UI (v2 if available, else v1)."""
        template_path = TEMPLATES_DIR / "index.html"
        if template_path.exists() and self._v2_initialized:
            return HTMLResponse(content=template_path.read_text(), headers={"Cache-Control": "no-cache"})
        if (TEMPLATES_DIR / "logs.html").exists():
            return await self.logs_page()  # startup failed: show why
        return await self.legacy_ui()

    async def logs_page(self):
        """Standalone log viewer, independent of the main web UI."""
        html = (TEMPLATES_DIR / "logs.html").read_text()
        if not self._v2_initialized:
            html = html.replace("<!--STARTUP_NOTICE-->", '<p class="notice">Sonorium didn&#39;t finish starting. The logs below show why.</p>')
        return HTMLResponse(content=html, headers={"Cache-Control": "no-cache"})

    def _log_info(self) -> dict:
        return {"version": __version__, "install": runtime.INSTALL_LABEL,
                "log_level": logging.getLevelName(logger.getEffectiveLevel()).lower()}

    async def get_logs(self, after: int = 0):
        """Recent log messages, oldest first; pass the last seq seen as `after` to get only newer ones."""
        return {"entries": logbuffer.recent(after), **self._log_info()}

    async def download_logs(self):
        """Recent log messages as a text file."""
        info = self._log_info()
        text = f"Sonorium {info['version']}, {info['install']} (log level: {info['log_level']})\n\n" + logbuffer.as_text(logbuffer.recent())
        name = f"sonorium-logs-{info['version']}.txt"
        return PlainTextResponse(text, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def serve_favicon(self):
        """Serve the browser tab icon."""
        for path in (FAVICON_PATH, ICON_PATH):
            if path.exists():
                return FileResponse(path, media_type="image/png")
        raise HTTPException(status_code=404, detail="Icon not found")

    async def serve_display_image(self):
        """Serve the logo shown on speakers with a screen."""
        for path in (DISPLAY_PATH, LOGO_PATH):
            if path.exists():
                return FileResponse(path, media_type="image/png")
        raise HTTPException(status_code=404, detail="Logo not found")

    async def serve_logo(self):
        """Serve the logo.png file."""
        if LOGO_PATH.exists():
            return FileResponse(LOGO_PATH, media_type="image/png")
        raise HTTPException(status_code=404, detail="Logo not found")

    async def legacy_ui(self):
        """Serve the legacy v1 web UI."""
        device = self.backend.device
        themes = device.themes
        
        # Build theme cards
        theme_cards = ""
        for theme in themes:
            total = len(theme.tracks)
            is_current = theme == themes.current
            current_class = "current" if is_current else ""
            
            recordings_list = ""
            for inst in theme.tracks:
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
                <div class="stream-url">Stream: {theme.listen_url}</div>
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
        # A theme on its own, outside any channel: a fresh mix, as MP3, for this listener only
        theme, _ = self._get_theme_by_id(id)
        if not theme:
            raise HTTPException(status_code=404, detail=f"Theme '{id}' not found")
        return StreamingResponse(theme.new_mix(), media_type="audio/mpeg")

    async def stream_channel(self, channel_id: int, local: bool = False, listener: str = ""):
        """
        Stream audio from a channel (new endpoint). ?local=1: a browser listening here (Listen here);
        &listener=<tag>: a personal channel's device (its listen_path), so each device is counted on its own.
        """
        if not self._channel_manager:
            return HTMLResponse(content="Channel system not initialized", status_code=503)

        channel = self._channel_manager.get_channel(channel_id)
        if not channel:
            return HTMLResponse(content=f"Channel {channel_id} not found", status_code=404)

        # Every listener gets the channel's one broadcast, encoded for it alone
        import re
        tag = listener if local and re.fullmatch(r"[0-9a-f]{8,32}", listener or "") else None
        return StreamingResponse(channel.get_stream(local, tag), media_type="audio/mpeg")

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
        device = self.backend.device
        if device and hasattr(device, 'audio_dir'):
            media_paths = [device.audio_dir]
        else:
            # Fallback to the library's audio folder (no hardcoded paths)
            logger.warning("_find_theme_folder: device.audio_dir not available")
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
        """Read metadata.json from theme folder, with its presets (from presets.json) under "presets"."""
        from sonorium.core import theme_presets
        folder = self._find_theme_folder(theme_id)
        if folder:
            try:
                metadata = theme_presets.read_json(folder / theme_presets.METADATA_FILE)
            except theme_presets.BrokenJsonError:
                metadata = None
            if metadata is not None:
                metadata["presets"] = theme_presets.load_presets(folder)
                return metadata
        return {}

    def _write_theme_metadata(self, theme_id: str, metadata: dict) -> bool:
        """Write metadata.json (and presets.json if "presets" is given) to theme folder. Returns True on success."""
        from sonorium.core import theme_presets
        folder = self._find_theme_folder(theme_id)
        if not folder:
            logger.error(f"Cannot write metadata: theme folder not found for '{theme_id}'")
            return False

        meta_path = folder / theme_presets.METADATA_FILE
        metadata = dict(metadata)
        presets = metadata.pop("presets", None)
        try:
            # Presets first, so a failed write never loses them
            if presets is not None:
                theme_presets.save_presets(folder, presets)
            theme_presets.write_json_atomic(meta_path, metadata)
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

    # ==================== Linked files ====================

    def _folder_for_theme_id(self, theme_id) -> Path | None:
        """A theme's folder by its id (metadata.json), for resolving links."""
        if not self._theme_metadata_manager or not isinstance(theme_id, str):
            return None
        return self._theme_metadata_manager.get_folder_for_id(theme_id)

    def _linked_recordings(self, metadata) -> list:
        """AudioFile for a theme's linked files that can be found (missing ones are left out)."""
        from sonorium.core.intrusions import link_group, resolve_link
        from sonorium.recording import AudioFile
        found = []
        for key, link in (getattr(metadata, "links", None) or {}).items():
            path = resolve_link(link, self._folder_for_theme_id)
            if path is None:
                logger.warning(f'Theme "{metadata.name}": linked track "{key}" is missing '
                               f'(theme {link.get("theme")}, track "{link.get("track")}"); it is skipped')
                continue
            found.append(AudioFile(path, key=key, group=link_group(key), link=dict(link)))
        return found

    def _attach_linked_tracks(self):
        """At start-up: each theme's linked files join its tracks (refresh_themes does this on a rescan)."""
        if not self._theme_metadata_manager:
            return
        from sonorium.recording import ThemeTrack
        device = self.backend.device
        for theme in device.themes or []:
            folder = self._find_theme_folder(theme.id)
            metadata = self._theme_metadata_manager.get_metadata_by_folder(folder) if folder else None
            if not metadata or not metadata.links:
                continue
            linked = self._linked_recordings(metadata)
            for meta in linked:
                if theme.name in device.files_by_theme:
                    device.files_by_theme[theme.name].append(meta)
                theme.tracks.append(ThemeTrack(meta, theme=theme))
            logger.debug(f'Theme "{theme.name}": {len(linked)} linked track(s)')

    def _link_info(self, link: dict) -> dict:
        """A link as the API shows it: source theme id and name, track key, and whether it is missing."""
        from sonorium.core.intrusions import resolve_link
        theme_id = link.get("theme")
        folder = self._folder_for_theme_id(theme_id)
        source = self._theme_metadata_manager.get_metadata_by_folder(folder) if folder else None
        return {
            "theme_id": theme_id,
            "theme_name": source.name if source else None,
            "track": link.get("track"),
            "missing": resolve_link(link, self._folder_for_theme_id) is None,
        }

    def _linked_keys(self, theme, metadata) -> list:
        """A theme's playable linked files (their source is there): what the ⚭ preset mark looks at."""
        from sonorium.core.intrusions import linked_track_keys
        instances = list(getattr(theme, "tracks", None) or []) if theme else []
        playable = {i.name for i in instances if getattr(i.meta, "link", None)}
        return linked_track_keys(metadata, playable)

    def _get_theme_by_id(self, theme_id: str):
        """
        Get a theme by ID, handling both legacy folder-based IDs and UUID-based IDs.
        Returns (theme, folder) tuple or (None, None) if not found.
        """
        # First try direct lookup by folder-based ID
        theme = self.backend.device.themes.id.get(theme_id)
        if theme:
            folder = self._find_theme_folder(theme_id)
            return theme, folder

        # Try finding by metadata ID (UUID-based)
        theme_folder = self._find_theme_folder(theme_id)
        if theme_folder:
            # Find matching theme by folder name
            for t in self.backend.device.themes:
                if t.name == theme_folder.name:
                    return t, theme_folder

        return None, None

    async def list_themes(self):
        """List all available themes with full metadata from metadata.json files."""
        device = self.backend.device
        themes = []
        seen_folders = set()
        audio_extensions = {'.mp3', '.wav', '.flac', '.ogg'}

        # First, add themes loaded by the device (have audio files)
        for theme in device.themes:
            enabled_count = sum(1 for i in theme.tracks if i.is_enabled)

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
                    "total_tracks": len(theme.tracks),
                    "enabled_tracks": enabled_count,
                    "url": theme.listen_url,
                    "description": metadata_dict.get("description", ""),
                    "icon": metadata_dict.get("icon", ""),
                    "is_favorite": metadata_dict.get("is_favorite", False),
                    "has_audio": True,
                    "categories": metadata_dict.get("categories", []),
                    "short_file_threshold": metadata_dict.get("short_file_threshold", theme.short_file_threshold),
                    "problems": [],
                })
                continue

            # Apply short_file_threshold from metadata
            theme.short_file_threshold = metadata.short_file_threshold

            # Use the persistent UUID from metadata.json as the theme ID
            themes.append({
                "id": metadata.id,
                "name": metadata.name,
                "total_tracks": len(theme.tracks),
                "enabled_tracks": enabled_count,
                "url": theme.listen_url,
                "description": metadata.description,
                "icon": metadata.icon,
                "is_favorite": metadata.is_favorite,
                "has_audio": True,
                "categories": metadata.categories,
                "short_file_threshold": metadata.short_file_threshold,
                "problems": list(metadata.problems),
            })

        # Then scan for empty theme folders (the library's audio folder, not hardcoded)
        if device and hasattr(device, 'audio_dir') and device.audio_dir.exists():
            for folder in device.audio_dir.iterdir():
                if not folder.is_dir() or folder.name in seen_folders or folder.name.startswith("."):
                    continue

                # Count audio files in this folder (top level and group folders)
                from sonorium.theme_files import theme_audio_files
                audio_files = theme_audio_files(folder)

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
                        "problems": list(metadata.problems),
                    })

        return themes

    def schedule_theme_refresh(self, delay: float = 2.0):
        """
        Rescan themes shortly after a change. Uploads arrive one file at a
        time, so each call restarts the delay and only one rescan runs.
        """
        import asyncio

        if self._theme_refresh_task and not self._theme_refresh_task.done():
            self._theme_refresh_task.cancel()

        async def refresh_later():
            await asyncio.sleep(delay)
            try:
                await self.refresh_themes()
            except Exception as e:
                logger.error(f"Theme refresh after change failed: {e}")

        self._theme_refresh_task = asyncio.get_running_loop().create_task(refresh_later())
        return self._theme_refresh_task

    async def refresh_themes(self):
        """Rescan theme folders and reload themes."""
        from sonorium.core.theme_metadata import EBB_TRACK_SETTINGS, reset_ebb_settings
        from sonorium.theme import LoadedTheme
        from sonorium.recording import AudioFile, PlaybackMode
        from sonorium.utils import IndexList

        device = self.backend.device
        path_audio = device.audio_dir
        audio_extensions = ['.mp3', '.wav', '.flac', '.ogg']

        logger.info(f'Rescanning themes in "{path_audio}"...')

        # Rescan metadata manager to pick up any new/changed folders
        if self._theme_metadata_manager:
            self._theme_metadata_manager.scan_themes()

        # Scan for theme folders
        theme_folders = [folder for folder in path_audio.iterdir() if folder.is_dir() and not folder.name.startswith(".")]
        logger.debug(f'Found {len(theme_folders)} theme folder(s)')

        # Step 1: each theme's files, before any Theme is made (it looks its files up)
        new_theme_metas = {}
        theme_names_with_audio = []

        from sonorium.theme_files import theme_audio_files

        for folder in theme_folders:
            # Top-level files and group folders (sonorium/theme_files.py)
            audio_files = theme_audio_files(folder)

            # A theme made in Sonorium has its metadata.json before it has any
            # audio: load it empty, so it can be edited, saved and given presets
            if audio_files or (folder / "metadata.json").is_file():
                theme_name = folder.name
                new_theme_metas[theme_name] = IndexList(AudioFile(path, folder) for path in audio_files)
                # The theme's linked files, found by their source theme's
                # id (so a renamed or rescanned source is followed; a missing one is skipped)
                folder_metadata = (self._theme_metadata_manager.get_metadata_by_folder(folder)
                                   if self._theme_metadata_manager else None)
                if folder_metadata is not None and folder_metadata.links:
                    new_theme_metas[theme_name].extend(self._linked_recordings(folder_metadata))
                theme_names_with_audio.append(theme_name)
                logger.debug(f'Found theme "{theme_name}" with {len(audio_files)} audio files')

        # Step 2: the library's file lists, which LoadedTheme() reads
        device.files_by_theme = new_theme_metas

        # Rebuild metas list
        device.all_files = IndexList()
        for theme_recordings in device.files_by_theme.values():
            device.all_files.extend(theme_recordings)

        # Step 3: NOW create Theme objects (they will find their metas correctly)
        new_themes = IndexList()
        for theme_name in theme_names_with_audio:
            # Read UUID from metadata.json if it exists
            theme_id = None
            metadata_path = path_audio / theme_name / "metadata.json"
            if metadata_path.exists():
                try:
                    import json
                    metadata = json.loads(metadata_path.read_text())
                    theme_id = metadata.get("id")
                except Exception:
                    pass  # Fall back to sanitized folder name

            theme_def = LoadedTheme(library=device, name=theme_name, theme_id=theme_id)
            new_themes.append(theme_def)
            logger.debug(f'Created Theme "{theme_name}" with {len(theme_def.tracks)} instances')

        # Step 4: Update device.themes
        previous = {t.id: t for t in (device.themes or [])}
        device.themes = new_themes

        # Set current theme if we have themes
        if device.themes:
            device.themes.current = device.themes[0]

            # Enable all recordings and apply saved track settings from metadata.json
            for theme in device.themes:
                if not theme.tracks:
                    continue

                # Find metadata for this theme
                theme_folder = self._find_theme_folder(theme.id)
                metadata = None
                if theme_folder and self._theme_metadata_manager:
                    metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)

                if metadata:
                    # metadata.json and the presets follow the files: new tracks
                    # get an entry, removed tracks and groups lose theirs
                    from sonorium.core.theme_metadata import sync_entries_with_files
                    changes = sync_entries_with_files(theme_folder, metadata)
                    if changes:
                        self._theme_metadata_manager.save_metadata(theme.id, metadata)
                        logger.info(
                            f'Theme "{theme.name}": {len(changes["added"])} track(s) added to metadata.json, '
                            f'{len(changes["removed"])} removed, {len(changes["groups_removed"])} group(s) removed, '
                            f'{changes["preset_entries_removed"]} preset entr(y/ies) removed')

                    # Apply short_file_threshold and group settings from metadata
                    theme.short_file_threshold = metadata.short_file_threshold
                    theme.groups = dict(getattr(metadata, "groups", None) or {})

                for inst in theme.tracks:
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
                            inst.fade_in, inst.fade_out = track_settings.fade_in, track_settings.fade_out
                            for key in EBB_TRACK_SETTINGS:
                                setattr(inst, key, getattr(track_settings, key))
                            continue

                    # Use defaults if no metadata (a linked file stays off)
                    inst.presence = 1.0
                    inst.is_enabled = not getattr(inst.meta, "link", None)
                    inst.volume = 1.0
                    inst.playback_mode = PlaybackMode.AUTO
                    inst.crossfade_enabled = True
                    inst.exclusive = False
                    inst.fade_in = inst.fade_out = 0.0
                    reset_ebb_settings(inst)

        logger.info(f'Theme refresh complete: {len(device.themes)} themes loaded')

        # Channels and previews playing a theme follow its new files at once:
        # added tracks join the mix, removed ones leave, nothing restarts
        for theme in device.themes:
            old = previous.get(theme.id)
            if old is None or old is theme:
                continue
            for stream in list(getattr(old, "live_mixes", ())):
                try:
                    stream.adopt(theme)
                    theme.live_mixes.add(stream)
                except Exception as e:
                    logger.warning(f'Could not update a playing stream of "{theme.name}": {e}')

        # Update session manager's theme reference
        if self._session_manager:
            self._session_manager.set_themes(device.themes)

        # Update theme options on the MQTT select entities (#33)
        if self._mqtt_manager:
            try:
                await self._mqtt_manager.refresh_themes([{"id": t.id, "name": t.name} for t in device.themes])
            except Exception as e:
                logger.warning(f"Failed to refresh MQTT theme options: {e}")

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
            "track_count": len(theme.tracks),
            "url": theme.listen_url,
            "tracks": [{"name": i.name} for i in theme.tracks],
        }

    async def get_theme_tracks(self, theme_id: str):
        """
        Get all tracks for a theme with presence/mute settings from metadata.json.
        Each has its "group" (null when ungrouped) and "intrusion_group" (its
        group is still an old Intrusion group: only until the theme is
        converted); a linked file has "linked": {theme_id, theme_name, track,
        missing}, and a linked file whose source is gone is listed too
        (missing, not played).
        """
        from sonorium.core.theme_metadata import EBB_TRACK_SETTINGS, TrackSettings
        from sonorium.core.intrusions import is_intrusion_group, link_group
        theme, theme_folder = self._get_theme_by_id(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        # Get settings from metadata.json
        metadata = None
        if theme_folder and self._theme_metadata_manager:
            metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)

        tracks = []
        for inst in theme.tracks:
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
                "fade_in": track_settings.fade_in if track_settings else getattr(inst, "fade_in", 0.0),
                "fade_out": track_settings.fade_out if track_settings else getattr(inst, "fade_out", 0.0),
                **{key: getattr(track_settings, key) if track_settings else getattr(inst, key) for key in EBB_TRACK_SETTINGS},
                "group": getattr(inst.meta, "group", None),
                "intrusion_group": is_intrusion_group((getattr(theme, "groups", None) or {}).get(getattr(inst.meta, "group", None))),
            })
            if getattr(inst.meta, "link", None):
                tracks[-1]["linked"] = self._link_info(inst.meta.link)

        # Linked files whose source is gone: listed (missing), not played
        if metadata and metadata.links:
            listed = {t["name"] for t in tracks}
            for key, link in metadata.links.items():
                if key in listed:
                    continue
                track_settings = metadata.tracks.get(key)
                group = link_group(key)
                tracks.append({
                    "name": key,
                    "presence": track_settings.presence if track_settings else 1.0,
                    "muted": track_settings.muted if track_settings else True,
                    "is_enabled": False,
                    "duration_seconds": 0.0,
                    "is_short_file": False,
                    "volume": track_settings.volume if track_settings else 1.0,
                    "playback_mode": track_settings.playback_mode if track_settings else "auto",
                    "seamless_loop": track_settings.seamless_loop if track_settings else False,
                    "exclusive": track_settings.exclusive if track_settings else False,
                    "fade_in": track_settings.fade_in if track_settings else 0.0,
                    "fade_out": track_settings.fade_out if track_settings else 0.0,
                    **{key: getattr(track_settings or TrackSettings(), key) for key in EBB_TRACK_SETTINGS},
                    "group": group,
                    "intrusion_group": is_intrusion_group((metadata.groups or {}).get(group)),
                    "linked": self._link_info(link),
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
        for inst in theme.tracks:
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
        for inst in theme.tracks:
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
        for inst in theme.tracks:
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
        for inst in theme.tracks:
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
        - unassigned: not placed in a section yet, doesn't play
        - continuous: Loop continuously with crossfade (Background)
        - sparse: Play once, then silence for interval based on presence (Intermittent)
        - presence: Ebb & Flow (presence is its Duration)
        - auto: choose by file length and presence at play time (still
          accepted; only a track without saved settings has it now)
        A track in a group plays its group's way whatever its own mode.
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
        for inst in theme.tracks:
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
        for inst in theme.tracks:
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

    async def set_track_ebb(self, theme_id: str, track_name: str, request: Request):
        """
        A track's Ebb & Flow settings. Body, any of: {"volume_low": 0..1 (the
        Volume slider's lower knob), "pause_between": bool, "pause_min": s,
        "pause_max": s}. Playing channels follow at once.
        """
        from sonorium.core.theme_metadata import EBB_TRACK_SETTINGS
        from fastapi.responses import JSONResponse
        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return JSONResponse({"error": "Theme not found"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict) or not body:
            return JSONResponse({"error": "Expected volume_low, pause_between, pause_min or pause_max"}, status_code=400)
        track_inst = next((inst for inst in theme.tracks if inst.name == track_name), None)
        if track_inst is None:
            return JSONResponse({"error": "Track not found"}, status_code=404)
        values = {}
        for key, raw in body.items():
            if key not in EBB_TRACK_SETTINGS:
                return JSONResponse({"error": f"Unknown setting '{key}'"}, status_code=400)
            if key == "pause_between":
                if not isinstance(raw, bool):
                    return JSONResponse({"error": "pause_between must be true or false"}, status_code=400)
                values[key] = raw
                continue
            try:
                number = float(raw)
            except (TypeError, ValueError):
                return JSONResponse({"error": f"{key} must be a number"}, status_code=400)
            if number != number or isinstance(raw, bool):
                return JSONResponse({"error": f"{key} must be a number"}, status_code=400)
            values[key] = max(0.0, min(1.0, number)) if key in ("volume_low", "rise") else round(max(0.0, min(86400.0, number)), 1)
        for key, value in values.items():
            setattr(track_inst, key, value)
        self._save_track_setting_to_metadata(theme_id, track_name, **values)
        return {"status": "ok", "track": track_name, **{key: getattr(track_inst, key) for key in EBB_TRACK_SETTINGS}}

    async def set_track_fades(self, theme_id: str, track_name: str, request: Request):
        """
        A file's own Fade in / Fade out, seconds (0 or empty = off, at most 30).
        Body: {"fade_in": s, "fade_out": s}; either may be left out. They apply
        from the file's next start or loop.
        """
        from fastapi.responses import JSONResponse
        from sonorium.recording import FILE_FADE_MAX_SECONDS
        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return JSONResponse({"error": "Theme not found"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        track_inst = next((inst for inst in theme.tracks if inst.name == track_name), None)
        if track_inst is None:
            return JSONResponse({"error": "Track not found"}, status_code=404)
        values = {}
        for key in ("fade_in", "fade_out"):
            if key not in body:
                continue
            raw = body[key]
            try:
                seconds = float(raw) if raw not in (None, "") else 0.0
            except (TypeError, ValueError):
                return JSONResponse({"error": f"{key} must be a number of seconds"}, status_code=400)
            if seconds != seconds or seconds < 0:
                return JSONResponse({"error": f"{key} must be 0 or more"}, status_code=400)
            values[key] = round(min(FILE_FADE_MAX_SECONDS, seconds), 2)
        if not values:
            return JSONResponse({"error": "fade_in or fade_out is required"}, status_code=400)
        for key, seconds in values.items():
            setattr(track_inst, key, seconds)
        self._save_track_setting_to_metadata(theme_id, track_name, **values)
        return {"status": "ok", "track": track_name, "fade_in": track_inst.fade_in, "fade_out": track_inst.fade_out}

    # --- Groups ---

    # Master controls: volume and presence multiply each track's own value; the
    # gap between plays (seconds) is a real interval, never scaled. mode:
    # "intermittent" (default) or "merry_go_round"; crossfade: a merry-go-round's
    # overlap in seconds. Mode and crossfade are theme settings, not in presets.
    # Mode "intrusion" is retired (read from old files, converted on load,
    # refused here); a linked file can be in any group.
    GROUP_SETTING_KEYS = ("presence", "volume", "muted", "gap_min", "gap_max", "mode", "crossfade", "pause_between", "rise")

    def _theme_group_folder(self, theme_id: str):
        """(theme, folder, metadata) for a theme, or raise 404."""
        theme, folder = self._get_theme_by_id(theme_id)
        metadata = self._theme_metadata_manager.get_metadata_by_folder(folder) if (folder and self._theme_metadata_manager) else None
        if not theme or not metadata:
            raise HTTPException(status_code=404, detail="Theme not found")
        return theme, folder, metadata

    def _theme_groups(self, theme_id: str):
        """
        (theme, metadata, {group: [track keys]}) for a theme, or raise 404.
        Read from the folder, so empty groups and just-moved tracks show
        before the theme refresh has run.
        """
        from sonorium.core import theme_groups
        theme, folder, metadata = self._theme_group_folder(theme_id)
        members = {name: theme_groups.group_track_keys(folder, name)
                   for name in theme_groups.group_folder_names(folder)}
        # A group's linked files are its tracks too
        from sonorium.core.intrusions import link_group
        for key in (getattr(metadata, "links", None) or {}):
            if link_group(key) is not None:
                members.setdefault(link_group(key), []).append(key)
        return theme, metadata, members

    @staticmethod
    async def _json_object(request: Request) -> dict:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Expected a JSON object")
        return body

    def _save_group_change(self, folder, metadata, change) -> None:
        """
        After the files moved (or a linked file was re-keyed): carry the track
        keys along in metadata.json and presets.json (presets and sequences),
        save both, and rebuild the live themes.
        """
        from sonorium.core import sequences, theme_groups, theme_presets
        presets = theme_presets.load_presets(folder)
        migrated = theme_groups.migrate_keys(metadata, presets, change)
        # TODO: per-user preset files (planned, docs/THEME_FORMAT.md 1.5) store
        # track keys too; migrate them here with theme_groups.migrate_presets
        # once they exist.
        try:
            if migrated != presets:
                theme_presets.save_presets(folder, migrated)
            sequences.migrate_track_keys(folder, change.track_keys, change.group_renames)
        except OSError as e:
            logger.error(f"Theme '{metadata.name}': files moved but presets.json couldn't be saved ({e})")
            self.schedule_theme_refresh()
            raise HTTPException(status_code=500, detail="Files were moved, but presets.json couldn't be saved")
        metadata.presets = migrated
        if not self._theme_metadata_manager.save_metadata(metadata.id, metadata):
            self.schedule_theme_refresh()
            raise HTTPException(status_code=500, detail="Files were moved, but metadata.json couldn't be saved")

    async def _refresh_now(self):
        """Rebuild the themes before answering, so the editor shows the change at once."""
        if self._theme_refresh_task and not self._theme_refresh_task.done():
            self._theme_refresh_task.cancel()
        try:
            await self.refresh_themes()
        except Exception as e:
            logger.error(f"Theme refresh after a group change failed: {e}")

    @staticmethod
    def _group_http_error(error):
        return HTTPException(status_code=getattr(error, "status", 400), detail=str(error))

    async def create_group_folder(self, theme_id: str, request: Request):
        """Create an empty group (a subfolder). Body: {"name": "Lute"}. 201 {"name": ...}."""
        from fastapi.responses import JSONResponse
        from sonorium.core import theme_groups
        theme, folder, metadata = self._theme_group_folder(theme_id)
        body = await self._json_object(request)
        try:
            name = theme_groups.create_group(folder, body.get("name"))
        except theme_groups.GroupError as e:
            raise self._group_http_error(e)
        logger.info(f"Theme '{metadata.name}': group '{name}' created")
        return JSONResponse(status_code=201, content={"name": name})

    async def rename_group_folder(self, theme_id: str, group: str, request: Request):
        """Rename a group. Body: {"name": "New"}. Track keys follow in metadata and presets."""
        from sonorium.core import theme_groups
        theme, folder, metadata = self._theme_group_folder(theme_id)
        body = await self._json_object(request)
        try:
            change = theme_groups.rename_group(folder, group, body.get("name"))
        except theme_groups.GroupError as e:
            raise self._group_http_error(e)
        new = change.group_renames.get(group, group)
        if change.group_renames:
            self._save_group_change(folder, metadata, change)
            await self._refresh_now()
            logger.info(f"Theme '{metadata.name}': group '{group}' renamed to '{new}'")
        return {"name": new, "tracks": change.track_keys}

    async def delete_group_folder(self, theme_id: str, group: str):
        """
        Delete a group: its audio files move up to the theme folder (renamed
        "x (2)" on a clash) and keep their settings; its linked files become
        ungrouped the same way (re-keyed, nothing moves). Audio files are never deleted.
        """
        from sonorium.core import intrusions, theme_groups
        theme, folder, metadata = self._theme_group_folder(theme_id)
        links = intrusions.group_link_keys(metadata, group)
        try:
            change = theme_groups.delete_group(folder, group, reserved=intrusions.link_names(metadata, None))
        except theme_groups.NotFound as e:
            if not links:
                raise self._group_http_error(e)
            change = theme_groups.Change(group_renames={group: None})  # only links, no folder
        except theme_groups.GroupError as e:
            raise self._group_http_error(e)
        change.track_keys.update(intrusions.regroup_link_keys(metadata, folder, links, None,
                                                              taken=change.track_keys.values()))
        self._save_group_change(folder, metadata, change)
        await self._refresh_now()
        result = {"name": group, "tracks": change.track_keys, "folder_removed": change.folder_removed}
        if change.folder_removed:
            logger.info(f"Theme '{metadata.name}': group '{group}' deleted, {len(change.track_keys)} tracks moved to the top level")
        else:
            result["left_behind"] = change.left_behind
            result["message"] = (f"The tracks moved to the top level; the folder '{group}' was kept "
                                 f"because it still holds other files")
            logger.info(f"Theme '{metadata.name}': group '{group}' deleted, {len(change.track_keys)} tracks moved "
                        f"to the top level; folder kept for {len(change.left_behind)} other files")
        return result

    async def move_track_to_group(self, theme_id: str, track_name: str, request: Request):
        """
        Move a track into a group (created if missing) or, with {"group": null}, to the top level.
        A linked file moves the same way, but only its key changes (no file moves). Its
        settings, preset values and sequence entries follow the new key. 200 {"track": new key}.
        """
        from sonorium.core import intrusions, theme_groups
        from sonorium.theme_files import track_display_name
        theme, folder, metadata = self._theme_group_folder(theme_id)
        body = await self._json_object(request)
        if "group" not in body:
            raise HTTPException(status_code=400, detail="'group' is required (a group name, or null for the top level)")
        group = body["group"]
        if track_name in (getattr(metadata, "links", None) or {}):
            return await self._move_link(theme_id, metadata, folder, track_name, group)
        try:
            change = theme_groups.move_track(folder, track_name, group,
                                             reserved=intrusions.link_names(metadata, group))
        except theme_groups.GroupError as e:
            raise self._group_http_error(e)
        new_key = change.track_keys.get(track_name, track_name)
        if change.track_keys:
            self._save_group_change(folder, metadata, change)
            await self._refresh_now()
            where = f"into group '{new_key.rsplit('/', 1)[0]}'" if "/" in new_key else "to the top level"
            renamed = f" as '{track_display_name(new_key)}'" if track_display_name(new_key) != track_display_name(track_name) else ""
            logger.info(f"Theme '{metadata.name}': track '{track_display_name(track_name)}' moved {where}{renamed}")
        return {"track": new_key}

    async def _move_link(self, theme_id: str, metadata, folder, key: str, group):
        """
        A linked file into a group (its folder created if missing, as for a
        file) or ungrouped: re-keyed "<group>/<name>" or "<name>" (" (2)" on a
        clash), like a moved file (see move_track_to_group). No file moves.
        """
        from sonorium.core import intrusions, theme_groups
        if group is not None:
            if not isinstance(group, str):
                raise HTTPException(status_code=400, detail="'group' must be a group name or null")
            try:
                group = theme_groups.group_target(folder, group).name
            except theme_groups.GroupError as e:
                raise self._group_http_error(e)
        moves = intrusions.regroup_link_keys(metadata, folder, [key], group)
        new_key = moves.get(key, key)
        if moves:
            self._save_group_change(folder, metadata, theme_groups.Change(track_keys=moves))
            await self._refresh_now()
            logger.info(f"Theme '{metadata.name}': linked track '{key}' moved to '{new_key}'")
        return {"track": new_key}

    async def list_groups(self, theme_id: str):
        """A theme's groups: their master settings and their tracks."""
        from sonorium.core.intrusions import link_group
        theme, metadata, members = self._theme_groups(theme_id)
        groups = []
        for name, keys in sorted(members.items()):
            settings = dict((metadata.groups or {}).get(name) or {})
            links = getattr(metadata, "links", None) or {}
            groups.append({
                "name": name,
                "settings": {k: v for k, v in settings.items() if k in self.GROUP_SETTING_KEYS},
                "tracks": keys,
                "intrusion": settings.get("mode") == "intrusion",
                "links": [{"key": key, **self._link_info(link)}
                          for key, link in links.items() if link_group(key) == name],
            })
        return {"groups": groups}

    async def update_group(self, theme_id: str, group: str, request: Request):
        """
        Change a group's master settings. Body: any of presence, volume (0-1,
        multiplying each track's own value), muted, gap_min, gap_max (seconds
        between plays), mode ("intermittent", "merry_go_round" or "ebb_flow";
        "intrusion" is retired: 400), crossfade (seconds, within Settings > Advanced's range, by default 1-60, for a
        merry-go-round), pause_between and rise (Ebb & Flow). null removes a
        setting (back to 100% / no mute / default gap / Intermittent / 10 s
        crossfade). A group's linked files stay in it whatever its mode.
        "remove_links" (from when intrusion groups held them) is accepted and ignored.
        """
        from sonorium.recording import GROUP_MODE_INTRUSION, GROUP_MODES, group_crossfade_range
        theme, metadata, members = self._theme_groups(theme_id)
        if group not in members:
            raise HTTPException(status_code=404, detail="Group not found")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Expected a JSON object")
        body = dict(body)
        remove_links = body.pop("remove_links", False)
        if not isinstance(remove_links, bool):
            raise HTTPException(status_code=400, detail="Invalid value for 'remove_links'")

        settings = dict((metadata.groups or {}).get(group) or {})
        for key, value in body.items():
            if key not in self.GROUP_SETTING_KEYS:
                raise HTTPException(status_code=400, detail=f"Unknown group setting '{key}'")
            if value is None:
                settings.pop(key, None)
                continue
            try:
                if key in ("presence", "volume", "rise"):
                    if isinstance(value, bool):
                        raise ValueError(value)
                    value = max(0.0, min(1.0, float(value)))
                elif key in ("gap_min", "gap_max"):
                    value = max(0.0, float(value))
                elif key in ("muted", "pause_between"):
                    if not isinstance(value, bool):
                        raise ValueError(value)
                elif key == "mode":
                    if value not in GROUP_MODES or value == GROUP_MODE_INTRUSION:
                        raise ValueError(value)
                elif key == "crossfade":
                    if isinstance(value, bool):
                        raise ValueError(value)
                    value = float(value)
                    if value != value:  # NaN
                        raise ValueError(value)
                    low, high = group_crossfade_range()  # Settings > Advanced, by default 1-60
                    value = max(low, min(high, value))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"Invalid value for '{key}'")
            settings[key] = value
        if "gap_min" in settings and "gap_max" in settings and settings["gap_min"] > settings["gap_max"]:
            settings["gap_min"], settings["gap_max"] = settings["gap_max"], settings["gap_min"]

        metadata.groups = {**(metadata.groups or {}), group: settings}
        self._theme_metadata_manager.save_metadata(metadata.id, metadata)
        theme.groups = dict(metadata.groups)  # live: playing channels follow (a mode change rebuilds the group's streams)
        logger.info(f"Theme '{theme.name}': group '{group}' settings changed")
        return {"name": group, "settings": settings}

    async def list_link_source_tracks(self, theme_id: str, source_id: str):
        """
        Another theme's tracks, read-only, for picking files to link into
        theme_id: each with its key ("id"), group, display
        name, duration, a preview URL (the source theme's own track audio,
        relative to the app's base path) and the key it is already linked as
        in theme_id, if it is. Linked files of the source are not offered.
        """
        from urllib.parse import quote
        from sonorium.theme_files import track_display_name
        _, _, metadata = self._theme_group_folder(theme_id)
        source, source_folder = self._get_theme_by_id(source_id)
        source_metadata = (self._theme_metadata_manager.get_metadata_by_folder(source_folder)
                           if (source_folder and self._theme_metadata_manager) else None)
        if not source or not source_metadata:
            raise HTTPException(status_code=404, detail="Source theme not found")
        linked_as = {(link["theme"], link["track"]): key for key, link in (metadata.links or {}).items()}
        tracks = []
        for inst in source.tracks:
            if getattr(inst.meta, "link", None):
                continue
            try:
                duration = round(inst.meta.duration_seconds, 1)
            except Exception:
                duration = None
            tracks.append({
                "id": inst.name,
                "name": inst.name,
                "display_name": track_display_name(inst.name),
                "group": getattr(inst.meta, "group", None),
                "duration_seconds": duration,
                "preview_url": f"/api/themes/{quote(source_metadata.id, safe='')}/tracks/{quote(inst.name, safe='')}/audio",
                "linked_as": linked_as.get((source_metadata.id, inst.name)),
            })
        tracks.sort(key=lambda t: t["name"].lower())
        return {
            "theme_id": metadata.id,
            "source": {"id": source_metadata.id, "name": source_metadata.name},
            "same_theme": source_metadata.id == metadata.id,
            "tracks": tracks,
        }

    async def add_links(self, theme_id: str, request: Request):
        """
        Link files of another theme into this theme. Body:
        {"theme": source theme id, "tracks": [source track keys], "group": a group (optional)}.
        Nothing is copied. Each new linked track starts with the source theme's
        settings for it, muted. Without "group" (or null) they are ungrouped
        and Unassigned (playback_mode "unassigned": not played until placed);
        with one, they go in that group (any mode). A file already linked
        there is not linked again ("added": false).
        201 {"links": [{"key", "theme", "track", "added"}]}.
        """
        from fastapi.responses import JSONResponse
        from sonorium.core import intrusions, theme_groups
        theme, folder, metadata = self._theme_group_folder(theme_id)
        body = await self._json_object(request)
        source_id, keys, group = body.get("theme"), body.get("tracks"), body.get("group")
        if not isinstance(source_id, str) or not source_id:
            raise HTTPException(status_code=400, detail="'theme' (the source theme id) is required")
        if group is not None and (not isinstance(group, str) or not group):
            raise HTTPException(status_code=400, detail="'group' must be a group name or null")
        if (not isinstance(keys, list) or not keys or len(keys) > 500
                or not all(isinstance(k, str) and k for k in keys)):
            raise HTTPException(status_code=400, detail="'tracks' must be a list of track keys")
        keys = list(dict.fromkeys(keys))

        _, _, members = self._theme_groups(theme_id)
        if group is not None and group not in members:
            raise HTTPException(status_code=404, detail="Group not found")

        source, source_folder = self._get_theme_by_id(source_id)
        source_metadata = (self._theme_metadata_manager.get_metadata_by_folder(source_folder)
                           if (source_folder and self._theme_metadata_manager) else None)
        if not source or not source_metadata:
            raise HTTPException(status_code=404, detail="Source theme not found")
        if source_metadata.id == metadata.id:
            raise HTTPException(status_code=400, detail="A theme's own files can't be linked; move them into the group instead")
        unknown = []
        for key in keys:
            try:
                theme_groups.find_track_file(source_folder, key)
            except theme_groups.GroupError:
                unknown.append(key)
        if unknown:
            raise HTTPException(status_code=404, detail=f"Not a track of the source theme: {', '.join(unknown)}")

        if group is not None:
            try:
                theme_groups.group_target(folder, group)  # the group's folder exists, even with only links in it
            except theme_groups.GroupError as e:
                raise self._group_http_error(e)
        results = intrusions.add_links(metadata, folder, group, source_metadata.id, source_metadata, keys)
        if not self._theme_metadata_manager.save_metadata(metadata.id, metadata):
            raise HTTPException(status_code=500, detail="metadata.json couldn't be saved")
        added = [r for r in results if r["added"]]
        where = f"into group '{group}'" if group is not None else "as Unassigned"
        logger.info(f"Theme '{metadata.name}': {len(added)} file(s) of '{source_metadata.name}' linked {where}")
        if added:
            await self._refresh_now()
        return JSONResponse(status_code=201, content={"links": results})

    async def copy_link_in(self, theme_id: str, key: str, request: Request):
        """
        Copy a linked file into this theme: the source file is copied (never
        moved) into group's folder, or the theme folder with {"group": null}
        (renamed "x (2)" etc. on a clash), the link is removed and the track's
        settings and preset values move to its new key. 404 if it is not a
        link or the group is unknown; 409 when the source file is missing.
        """
        import shutil
        from sonorium.core import intrusions, theme_groups
        from sonorium.theme_files import track_display_name, track_key
        theme, folder, metadata = self._theme_group_folder(theme_id)
        body = await self._json_object(request)
        if "group" not in body:
            raise HTTPException(status_code=400, detail="'group' is required (a group name, or null for the top level)")
        group = body["group"]
        link = (getattr(metadata, "links", None) or {}).get(key)
        if link is None:
            raise HTTPException(status_code=404, detail="Linked track not found")
        _, _, members = self._theme_groups(theme_id)
        if group is not None and (not isinstance(group, str) or group not in members):
            raise HTTPException(status_code=404, detail="Group not found")
        source = intrusions.resolve_link(link, self._folder_for_theme_id)
        if source is None:
            raise HTTPException(status_code=409, detail="The linked file is missing; it can't be copied")
        try:
            target_folder = folder if group is None else theme_groups.group_target(folder, group)
        except theme_groups.GroupError as e:
            raise self._group_http_error(e)
        # Another linked file there keeps its name; this one's key is free once it is copied in
        reserved = intrusions.link_names(metadata, group) - (
            {track_display_name(key)} if intrusions.link_group(key) == group else set())
        target = target_folder / theme_groups.free_file_name(target_folder, source.name, reserved=reserved)
        try:
            shutil.copy2(source, target)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Couldn't copy the file: {e}")
        new_key = track_key(folder, target)
        intrusions.remove_link_keep_settings(metadata, key)
        # Its settings, preset values and sequence entries move to the new key
        self._save_group_change(folder, metadata, theme_groups.Change(
            track_keys={key: new_key} if new_key != key else {}))
        await self._refresh_now()
        logger.info(f"Theme '{metadata.name}': linked track '{key}' copied in as '{new_key}' (from {source})")
        return {"track": new_key}

    async def delete_link(self, theme_id: str, key: str):
        """Remove a linked file from a theme: its link, settings and preset entries. Never a file."""
        from sonorium.core import intrusions
        theme, folder, metadata = self._theme_group_folder(theme_id)
        if not intrusions.remove_link(metadata, key):
            raise HTTPException(status_code=404, detail="Linked track not found")
        if not self._theme_metadata_manager.save_metadata(metadata.id, metadata):
            raise HTTPException(status_code=500, detail="metadata.json couldn't be saved")
        logger.info(f"Theme '{metadata.name}': linked track '{key}' removed")
        await self._refresh_now()
        return {"removed": key}

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
        for inst in theme.tracks:
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
        """Reset all track settings to defaults for a theme (linked files stay muted)."""
        from sonorium.recording import PlaybackMode
        from sonorium.core.theme_metadata import TrackSettings, reset_ebb_settings

        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return {"error": "Theme not found"}

        # Reset live instances (linked files stay off, as when they were added)
        for inst in theme.tracks:
            inst.presence = 1.0
            inst.is_enabled = not getattr(inst.meta, "link", None)
            inst.volume = 1.0
            inst.playback_mode = PlaybackMode.AUTO
            inst.crossfade_enabled = True
            inst.exclusive = False
            inst.fade_in = inst.fade_out = 0.0
            reset_ebb_settings(inst)

        # Clear persisted settings in metadata.json
        if self._theme_metadata_manager:
            theme_folder = self._find_theme_folder(theme_id)
            if theme_folder:
                metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
                if metadata:
                    # Reset all tracks to defaults
                    metadata.tracks = {key: TrackSettings(muted=True) for key in (metadata.links or {})}
                    self._theme_metadata_manager.save_metadata(metadata.id, metadata)

        return {"status": "ok", "theme_id": theme_id}

    # ==================== Preset API ====================

    def _get_current_group_settings(self, theme_id: str) -> dict:
        """The theme's group settings as a preset saves them ({group: {volume, presence, muted, gap_min, gap_max}})."""
        from sonorium.recording import GROUP_PRESET_SETTINGS
        theme, _ = self._get_theme_by_id(theme_id)
        groups = getattr(theme, "groups", None) or {} if theme else {}
        return {
            name: {k: v for k, v in (settings or {}).items() if k in GROUP_PRESET_SETTINGS}
            for name, settings in groups.items()
            if not (settings or {}).get("legacy_exclusive")
        }

    def _get_current_track_settings(self, theme_id: str) -> dict:
        """Get current track settings for a theme as a preset-compatible dict."""
        from sonorium.core.theme_metadata import EBB_TRACK_SETTINGS
        # Use _get_theme_by_id to handle both UUID-based and folder-based IDs
        theme, _ = self._get_theme_by_id(theme_id)
        if not theme:
            return {}

        tracks = {}
        for inst in theme.tracks:
            tracks[inst.name] = {
                "volume": inst.volume,
                "presence": inst.presence,
                "playback_mode": inst.playback_mode.value if hasattr(inst.playback_mode, 'value') else str(inst.playback_mode),
                "seamless_loop": not inst.crossfade_enabled,
                "exclusive": inst.exclusive,
                "muted": not inst.is_enabled,
                **{key: getattr(inst, key) for key in EBB_TRACK_SETTINGS},
            }
        return tracks

    def _apply_preset_to_theme(self, theme_id: str, preset_tracks: dict, preset_groups: dict | None = None) -> bool:
        """Apply preset track settings (and group master settings) to a theme. Returns True on success."""
        from sonorium.core.theme_metadata import EBB_TRACK_SETTINGS
        from sonorium.recording import PlaybackMode

        # Use _get_theme_by_id to handle both UUID-based and folder-based IDs
        theme, theme_folder = self._get_theme_by_id(theme_id)
        if not theme:
            return False

        # Apply to live instances
        for inst in theme.tracks:
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
                for key in EBB_TRACK_SETTINGS:
                    if key in settings:
                        setattr(inst, key, settings[key])

        # Group master settings from the preset
        if preset_groups:
            from sonorium.recording import GROUP_PRESET_SETTINGS
            merged = dict(getattr(theme, "groups", None) or {})
            for name, values in preset_groups.items():
                merged[name] = {**(merged.get(name) or {}),
                                **{k: v for k, v in (values or {}).items() if k in GROUP_PRESET_SETTINGS}}
            theme.groups = merged

        # Persist to metadata.json
        if self._theme_metadata_manager and theme_folder:
            metadata = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata:
                if preset_groups:
                    metadata.groups = dict(theme.groups)
                # Update track settings in metadata
                for track_name, settings in preset_tracks.items():
                    track_settings = metadata.get_track_settings(track_name)
                    track_settings.presence = settings.get("presence", 1.0)
                    track_settings.muted = settings.get("muted", False)
                    track_settings.volume = settings.get("volume", 1.0)
                    track_settings.playback_mode = settings.get("playback_mode", "auto")
                    track_settings.seamless_loop = settings.get("seamless_loop", False)
                    track_settings.exclusive = settings.get("exclusive", False)
                    for key in EBB_TRACK_SETTINGS:
                        if key in settings:
                            setattr(track_settings, key, settings[key])

                self._theme_metadata_manager.save_metadata(metadata.id, metadata)

        return True

    @staticmethod
    def _sequence_choices(folder, preset_intrusion: dict) -> list:
        """
        A theme's sequences as choices next to its presets (core/sequences.py
        to_choice): id, name, presets, at_end, effective timing, pass_min /
        pass_max, "choice" ("seq:<id>") and has_intrusion.
        """
        from sonorium.core import sequences as seq
        if not folder:
            return []
        try:
            defaults = seq.load_defaults(folder)
            return [seq.to_choice(sid, data, defaults, preset_intrusion)
                    for sid, data in seq.load_sequences(folder).items()]
        except Exception as e:
            logger.warning(f"Could not read the sequences in {folder}: {e}")
            return []

    async def list_presets(self, theme_id: str):
        """
        List all presets for a theme. "has_intrusion" (the ⚭ mark): the preset
        turns on (unmutes) at least one linked file, wherever it is (its
        settings over the theme's); "default_has_intrusion": the theme's own
        settings do (no preset).
        "sequences": the theme's sequences, which a channel can pick like a
        preset (preset_id "seq:<id>"); has_intrusion if any of its presets has.
        """
        from sonorium.core.intrusions import enables_intrusion
        theme, theme_folder = self._get_theme_by_id(theme_id)

        # Use metadata manager if available (preferred path)
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                linked_keys = self._linked_keys(theme, metadata_obj)
                result = []
                for preset_id, preset_data in metadata_obj.presets.items():
                    result.append({
                        "id": preset_id,
                        "name": preset_data.get("name", preset_id),
                        "is_default": preset_data.get("is_default", False),
                        "track_count": len(preset_data.get("tracks", {})),
                        "has_intrusion": enables_intrusion(metadata_obj, linked_keys, preset_data),
                    })
                intrusion = {p["id"]: p["has_intrusion"] for p in result}
                return {"theme_id": theme_id, "presets": result,
                        "default_has_intrusion": enables_intrusion(metadata_obj, linked_keys),
                        "sequences": self._sequence_choices(theme_folder, intrusion)}

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

        return {"theme_id": theme_id, "presets": result,
                "sequences": self._sequence_choices(self._find_theme_folder(theme_id), {})}

    async def get_preset(self, theme_id: str, preset_id: str):
        """One preset's settings: {"id", "name", "tracks": {key: {...}}, "groups": {name: {...}}}."""
        _, theme_folder = self._get_theme_by_id(theme_id)
        presets = None
        if self._theme_metadata_manager and theme_folder:
            metadata_obj = self._theme_metadata_manager.get_metadata_by_folder(theme_folder)
            if metadata_obj:
                presets = metadata_obj.presets
        if presets is None:  # fallback: direct file I/O
            presets = self._read_theme_metadata(theme_id).get("presets", {})
        if preset_id not in presets:
            raise HTTPException(status_code=404, detail="Preset not found")
        preset = presets[preset_id]
        return {
            "id": preset_id,
            "name": preset.get("name", preset_id),
            "tracks": preset.get("tracks") or {},
            "groups": preset.get("groups") or {},
        }

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
                    "groups": self._get_current_group_settings(theme_id),
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

                if not self._apply_preset_to_theme(theme_id, tracks, preset.get("groups")):
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

        if not self._apply_preset_to_theme(theme_id, tracks, preset.get("groups")):
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

                # Update the preset's tracks and groups while preserving name and is_default
                metadata_obj.presets[preset_id]["tracks"] = tracks
                metadata_obj.presets[preset_id]["groups"] = self._get_current_group_settings(theme_id)

                # Save via metadata manager (updates cache and file)
                if not self._theme_metadata_manager.save_metadata(metadata_obj.id, metadata_obj):
                    raise HTTPException(status_code=500, detail="Failed to update preset")
                self._preset_saved(theme_id, theme, preset_id)

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
        presets[preset_id]["groups"] = self._get_current_group_settings(theme_id)
        metadata["presets"] = presets

        if not self._write_theme_metadata(theme_id, metadata):
            raise HTTPException(status_code=500, detail="Failed to update preset")
        self._preset_saved(theme_id, theme, preset_id)

        return {
            "status": "ok",
            "preset_id": preset_id,
            "name": presets[preset_id].get("name", preset_id),
            "tracks_updated": len(tracks),
        }

    def _preset_saved(self, theme_id: str, theme, preset_id: str) -> None:
        """Channels playing the saved preset follow its new values."""
        if self._session_manager is None:
            return
        try:
            self._session_manager.preset_saved({theme_id, getattr(theme, "id", None)}, preset_id)
        except Exception as e:
            logger.warning(f"Preset '{preset_id}' saved, but playing channels could not follow it: {e}")

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

                await self._preset_removed(theme_id, theme_folder, preset_id)
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

        await self._preset_removed(theme_id, theme_folder or self._find_theme_folder(theme_id), preset_id)
        return {"status": "ok", "preset_id": preset_id}

    async def _preset_removed(self, theme_id: str, folder, preset_id: str) -> None:
        """A preset was deleted: take it out of the theme's sequences, and update the MQTT selects."""
        if folder:
            try:
                from sonorium.core.sequences import drop_preset
                changed = drop_preset(folder, preset_id)
                if changed:
                    logger.info(f"  Preset '{preset_id}' removed from sequence(s): {', '.join(changed)}")
            except Exception as e:
                logger.warning(f"  Could not remove preset '{preset_id}' from the theme's sequences: {e}")
        if self._mqtt_manager:
            try:
                await self._mqtt_manager.refresh_sequences(theme_id)
            except Exception as e:
                logger.warning(f"  Could not update MQTT selects: {e}")

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
        valid_track_names = {inst.name for inst in theme.tracks}
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

        # Themes keep their categories in metadata.json (bundled and imported
        # themes arrive with them), so list those too, or the Themes page shows
        # those themes as Uncategorized
        categories = self._state_store.settings.theme_categories
        if self._theme_metadata_manager:
            missing = [c for c in self._theme_metadata_manager.all_categories() if c not in categories]
            if missing:
                categories.extend(missing)
                self._state_store.save()
        return {
            "categories": categories
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

        # Take it off the themes too, or it comes back with them
        if self._theme_metadata_manager:
            self._theme_metadata_manager.remove_category(category_name)

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
        device = self.backend.device
        themes_data = []
        for theme in device.themes:
            themes_data.append({
                "name": theme.name,
                "id": theme.id,
                "track_count": len(theme.tracks),
                "url": theme.listen_url
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
            
            # Channels playing a playlist (theme cycling became playlists)
            status_data["playlist_sessions"] = sum(
                1 for s in self._state_store.sessions.values() if s.playlist_id
            )
        
        return status_data
