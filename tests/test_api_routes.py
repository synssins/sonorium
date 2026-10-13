"""
The web app's routes (api.WebApp's table, registered through
sonorium/web_server.py) and the v2 router's: every route is there, in the
same order, with the same method, path and handler.

APP_ROUTES and V2_ROUTES were recorded from origin/fixes (api.py on the real
fmtr.tools 1.5.4 api_tools.Base) before the change.
"""
import importlib.util
import logging
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

PACKAGE = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"

# (route type, methods, path, handler name)
APP_ROUTES = [
    ('Route', 'GET,HEAD', '/openapi.json', 'openapi'),
    ('Route', 'GET,HEAD', '/docs', 'swagger_ui_html'),
    ('Route', 'GET,HEAD', '/docs/oauth2-redirect', 'swagger_ui_redirect'),
    ('Route', 'GET,HEAD', '/redoc', 'redoc_html'),
    ('APIRoute', 'GET', '/', 'web_ui'),
    ('APIRoute', 'GET', '/api/connection', 'get_connection'),
    ('APIRoute', 'PUT', '/api/connection', 'put_connection'),
    ('APIRoute', 'DELETE', '/api/connection/ha', 'delete_connection_ha'),
    ('APIRoute', 'DELETE', '/api/connection/mqtt', 'delete_connection_mqtt'),
    ('APIRoute', 'POST', '/api/connection/test', 'test_connection'),
    ('APIRoute', 'GET', '/api/ha-cards', 'get_ha_cards'),
    ('APIRoute', 'GET', '/api/ha-cards/download', 'download_ha_cards'),
    ('APIRoute', 'GET', '/v1', 'legacy_ui'),
    ('APIRoute', 'GET', '/logo.png', 'serve_logo'),
    ('APIRoute', 'GET', '/display.png', 'serve_display_image'),
    ('APIRoute', 'GET', '/favicon.png', 'serve_favicon'),
    ('APIRoute', 'GET', '/logs', 'logs_page'),
    ('APIRoute', 'GET', '/api/logs', 'get_logs'),
    ('APIRoute', 'GET', '/api/logs/download', 'download_logs'),
    ('APIRoute', 'GET', '/api/install', 'get_install'),
    ('APIRoute', 'PUT', '/api/install/features', 'put_install_features'),
    ('APIRoute', 'GET', '/stream/channel{channel_id:int}', 'stream_channel'),
    ('APIRoute', 'GET', '/stream/{id}', 'stream'),
    ('APIRoute', 'GET', '/api/themes', 'list_themes'),
    ('APIRoute', 'POST', '/api/themes/refresh', 'refresh_themes'),
    ('APIRoute', 'GET', '/api/themes/packages', 'list_theme_packages'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/favorite', 'toggle_favorite'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}', 'get_theme'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/tracks', 'get_theme_tracks'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/tracks/{track_name:path}/audio', 'get_track_audio'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/tracks/{track_name:path}/presence', 'set_track_presence'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/tracks/{track_name:path}/muted', 'set_track_muted'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/tracks/{track_name:path}/volume', 'set_track_volume'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/tracks/{track_name:path}/playback_mode', 'set_track_playback_mode'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/tracks/{track_name:path}/seamless_loop', 'set_track_seamless_loop'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/tracks/{track_name:path}/fades', 'set_track_fades'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/tracks/{track_name:path}/ebb', 'set_track_ebb'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/tracks/{track_name:path}/exclusive', 'set_track_exclusive'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/groups', 'list_groups'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/groups/{group}', 'update_group'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/groups', 'create_group_folder'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/groups/{group}/rename', 'rename_group_folder'),
    ('APIRoute', 'DELETE', '/api/themes/{theme_id}/groups/{group}', 'delete_group_folder'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/tracks/{track_name:path}/move', 'move_track_to_group'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/tracks/reset', 'reset_theme_tracks'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/links/source/{source_id}', 'list_link_source_tracks'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/links', 'add_links'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/links/{key:path}/copy-in', 'copy_link_in'),
    ('APIRoute', 'DELETE', '/api/themes/{theme_id}/links/{key:path}', 'delete_link'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/rename', 'rename_theme'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/presets', 'list_presets'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/presets', 'create_preset'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/presets/{preset_id}/load', 'load_preset'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/presets/{preset_id}', 'get_preset'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/presets/{preset_id}', 'update_preset'),
    ('APIRoute', 'DELETE', '/api/themes/{theme_id}/presets/{preset_id}', 'delete_preset'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/presets/{preset_id}/default', 'set_default_preset'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/presets/{preset_id}/rename', 'rename_preset'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/presets/import', 'import_preset'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/presets/{preset_id}/export', 'export_preset'),
    ('APIRoute', 'GET', '/api/categories', 'list_categories'),
    ('APIRoute', 'POST', '/api/categories', 'create_category'),
    ('APIRoute', 'DELETE', '/api/categories/{category_name}', 'delete_category'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/categories', 'set_theme_categories'),
    ('APIRoute', 'GET', '/api/status', 'status'),
    ('APIRoute', 'GET', '/api/stream/url', 'stream_urls'),
    ('APIRoute', 'GET', '/api/channels', 'list_channels'),
    ('APIRoute', 'GET', '/api/channels/{channel_id}', 'get_channel'),
    ('Mount', '', '/static', 'static'),
]

V2_ROUTES = [
    ('APIRoute', 'GET', '/api/debug/speakers', 'debug_speakers'),
    ('APIRoute', 'GET', '/api/sessions', 'list_sessions'),
    ('APIRoute', 'GET', '/api/now-playing', 'now_playing'),
    ('APIRoute', 'POST', '/api/sessions', 'create_session'),
    ('APIRoute', 'GET', '/api/sessions/{session_id}', 'get_session'),
    ('APIRoute', 'PUT', '/api/sessions/{session_id}', 'update_session'),
    ('APIRoute', 'DELETE', '/api/sessions/{session_id}', 'delete_session'),
    ('APIRoute', 'POST', '/api/sessions/{session_id}/play', 'play_session'),
    ('APIRoute', 'POST', '/api/sessions/{session_id}/pause', 'pause_session'),
    ('APIRoute', 'POST', '/api/sessions/{session_id}/stop', 'stop_session'),
    ('APIRoute', 'POST', '/api/sessions/{session_id}/volume', 'set_session_volume'),
    ('APIRoute', 'PUT', '/api/sessions/{session_id}/announcements', 'set_session_announcements'),
    ('APIRoute', 'PUT', '/api/sessions/{session_id}/speakers-muted', 'mute_session_speakers'),
    ('APIRoute', 'GET', '/api/sessions/{session_id}/sleep', 'get_session_sleep'),
    ('APIRoute', 'PUT', '/api/sessions/{session_id}/sleep', 'set_session_sleep'),
    ('APIRoute', 'DELETE', '/api/sessions/{session_id}/sleep', 'cancel_session_sleep'),
    ('APIRoute', 'POST', '/api/sessions/{session_id}/listen', 'listen_session'),
    ('APIRoute', 'DELETE', '/api/sessions/{session_id}/listen', 'unlisten_session'),
    ('APIRoute', 'POST', '/api/sessions/stop-all', 'stop_all_sessions'),
    ('APIRoute', 'GET', '/api/sessions/{session_id}/playback', 'get_session_playback'),
    ('APIRoute', 'POST', '/api/sessions/{session_id}/sequence', 'start_session_sequence'),
    ('APIRoute', 'DELETE', '/api/sessions/{session_id}/sequence', 'stop_session_sequence'),
    ('APIRoute', 'PUT', '/api/sessions/{session_id}/playlist', 'set_session_playlist'),
    ('APIRoute', 'DELETE', '/api/sessions/{session_id}/playlist', 'clear_session_playlist'),
    ('APIRoute', 'POST', '/api/sessions/{session_id}/next', 'skip_session'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/sequences', 'list_theme_sequences'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/sequences', 'create_theme_sequence'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/sequences/{sequence_id}', 'get_theme_sequence'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/sequences/{sequence_id}', 'update_theme_sequence'),
    ('APIRoute', 'DELETE', '/api/themes/{theme_id}/sequences/{sequence_id}', 'delete_theme_sequence'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/sequence_defaults', 'get_sequence_defaults'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/sequence_defaults', 'update_sequence_defaults'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/sequences/{sequence_id}/transitions/{preset_id}', 'get_step_transitions'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/sequences/{sequence_id}/transitions/{preset_id}', 'set_step_transitions'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/sequences/{sequence_id}/while/{preset_id}', 'get_step_while'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/sequences/{sequence_id}/while/{preset_id}', 'set_step_while'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/transition_tracks', 'get_transition_tracks'),
    ('APIRoute', 'GET', '/api/playlists', 'list_playlists'),
    ('APIRoute', 'POST', '/api/playlists', 'create_playlist'),
    ('APIRoute', 'GET', '/api/playlists/{playlist_id}', 'get_playlist'),
    ('APIRoute', 'PUT', '/api/playlists/{playlist_id}', 'update_playlist'),
    ('APIRoute', 'DELETE', '/api/playlists/{playlist_id}', 'delete_playlist'),
    ('APIRoute', 'GET', '/api/channels', 'list_channels'),
    ('APIRoute', 'GET', '/api/channels/{channel_id}', 'get_channel'),
    ('APIRoute', 'POST', '/api/channels/{channel_id}/play', 'play_channel'),
    ('APIRoute', 'POST', '/api/channels/{channel_id}/stop', 'stop_channel'),
    ('APIRoute', 'POST', '/api/channels/{channel_id}/volume', 'set_channel_volume'),
    ('APIRoute', 'GET', '/api/groups', 'list_groups'),
    ('APIRoute', 'POST', '/api/groups', 'create_group'),
    ('APIRoute', 'GET', '/api/groups/{group_id}', 'get_group'),
    ('APIRoute', 'PUT', '/api/groups/{group_id}', 'update_group'),
    ('APIRoute', 'DELETE', '/api/groups/{group_id}', 'delete_group'),
    ('APIRoute', 'GET', '/api/groups/{group_id}/resolve', 'resolve_group'),
    ('APIRoute', 'GET', '/api/speakers', 'list_speakers'),
    ('APIRoute', 'GET', '/api/speakers/hierarchy', 'get_speaker_hierarchy'),
    ('APIRoute', 'POST', '/api/speakers/refresh', 'refresh_speakers'),
    ('APIRoute', 'GET', '/api/network-speakers', 'list_network_speakers'),
    ('APIRoute', 'POST', '/api/network-speakers/refresh', 'refresh_network_speakers'),
    ('APIRoute', 'GET', '/api/speakers/{speaker_id}/settings', 'get_one_speaker_settings'),
    ('APIRoute', 'PUT', '/api/speakers/{speaker_id}/settings', 'update_one_speaker_settings'),
    ('APIRoute', 'GET,HEAD', '/api/test-tone.mp3', 'test_tone'),
    ('APIRoute', 'POST', '/api/speakers/{speaker_id}/test', 'test_speaker'),
    ('APIRoute', 'GET', '/api/spaces', 'list_spaces'),
    ('APIRoute', 'POST', '/api/spaces/floors', 'add_floor'),
    ('APIRoute', 'POST', '/api/spaces/areas', 'add_area'),
    ('APIRoute', 'PUT', '/api/spaces/floors/{floor_id}', 'update_floor'),
    ('APIRoute', 'PUT', '/api/spaces/areas/{area_id}', 'update_area'),
    ('APIRoute', 'DELETE', '/api/spaces/floors/{floor_id}', 'delete_floor'),
    ('APIRoute', 'DELETE', '/api/spaces/areas/{area_id}', 'delete_area'),
    ('APIRoute', 'POST', '/api/speakers/manual/check', 'check_manual_speaker'),
    ('APIRoute', 'POST', '/api/speakers/manual', 'add_manual_speaker'),
    ('APIRoute', 'DELETE', '/api/speakers/manual/{speaker_id}', 'remove_manual_speaker'),
    ('APIRoute', 'POST', '/api/speakers/resolve', 'resolve_selection'),
    ('APIRoute', 'GET', '/api/settings', 'get_settings'),
    ('APIRoute', 'PUT', '/api/settings', 'update_settings'),
    ('APIRoute', 'GET', '/api/settings/playback-values', 'get_playback_values'),
    ('APIRoute', 'PUT', '/api/settings/playback-values', 'update_playback_values'),
    ('APIRoute', 'GET', '/api/settings/speakers', 'get_speaker_settings'),
    ('APIRoute', 'PUT', '/api/settings/speakers', 'update_speaker_settings'),
    ('APIRoute', 'POST', '/api/settings/speakers/enable', 'enable_speaker'),
    ('APIRoute', 'POST', '/api/settings/speakers/disable', 'disable_speaker'),
    ('APIRoute', 'POST', '/api/settings/speakers/enable-all', 'enable_all_speakers'),
    ('APIRoute', 'POST', '/api/settings/speakers/disable-all', 'disable_all_speakers'),
    ('APIRoute', 'GET', '/api/settings/speaker-areas', 'get_custom_speaker_areas'),
    ('APIRoute', 'PUT', '/api/settings/speaker-areas', 'update_custom_speaker_areas'),
    ('APIRoute', 'POST', '/api/settings/speaker-areas/create', 'create_custom_area'),
    ('APIRoute', 'PUT', '/api/settings/speaker-areas/{area_name}', 'update_custom_area'),
    ('APIRoute', 'DELETE', '/api/settings/speaker-areas/{area_name}', 'delete_custom_area'),
    ('APIRoute', 'POST', '/api/settings/speaker-areas/{area_name}/add-speaker', 'add_speaker_to_area'),
    ('APIRoute', 'POST', '/api/settings/speaker-areas/{area_name}/remove-speaker', 'remove_speaker_from_area'),
    ('APIRoute', 'POST', '/api/themes/create', 'create_theme'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/upload', 'upload_theme_file'),
    ('APIRoute', 'PUT', '/api/themes/{theme_id}/metadata', 'update_theme_metadata'),
    ('APIRoute', 'DELETE', '/api/themes/{theme_id}', 'delete_theme'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/export', 'export_theme'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/duplicate', 'duplicate_theme'),
    ('APIRoute', 'POST', '/api/themes/import', 'import_theme'),
    ('APIRoute', 'GET', '/api/plugins', 'list_plugins'),
    ('APIRoute', 'GET', '/api/plugins/catalog', 'get_plugin_catalog'),
    ('APIRoute', 'POST', '/api/plugins/install-from-catalog', 'install_plugin_from_catalog'),
    ('APIRoute', 'GET', '/api/plugins/{plugin_id}', 'get_plugin'),
    ('APIRoute', 'PUT', '/api/plugins/{plugin_id}/enable', 'enable_plugin'),
    ('APIRoute', 'PUT', '/api/plugins/{plugin_id}/disable', 'disable_plugin'),
    ('APIRoute', 'GET', '/api/plugins/{plugin_id}/settings', 'get_plugin_settings'),
    ('APIRoute', 'PUT', '/api/plugins/{plugin_id}/settings', 'update_plugin_settings'),
    ('APIRoute', 'POST', '/api/plugins/{plugin_id}/action', 'execute_plugin_action'),
    ('APIRoute', 'POST', '/api/plugins/reload', 'reload_plugins'),
    ('APIRoute', 'POST', '/api/plugins/upload', 'upload_plugin'),
    ('APIRoute', 'DELETE', '/api/plugins/{plugin_id}', 'uninstall_plugin'),
    # The app's routes (web/app_routes.py)
    ('APIRoute', 'GET', '/api/sessions/{session_id}/outputs', 'list_session_outputs'),
    ('APIRoute', 'POST', '/api/sessions/{session_id}/outputs', 'add_session_output'),
    ('APIRoute', 'PUT', '/api/sessions/{session_id}/outputs/{output_id}', 'heartbeat_session_output'),
    ('APIRoute', 'DELETE', '/api/sessions/{session_id}/outputs/{output_id}', 'remove_session_output'),
    ('APIRoute', 'PUT', '/api/speakers/{speaker_id}/volume', 'set_speaker_volume'),
    ('APIRoute', 'PUT', '/api/speakers/{speaker_id}/muted', 'set_speaker_muted'),
    # Theme packages for offline playback (web/theme_package_routes.py)
    ('APIRoute', 'GET', '/api/themes/{theme_id}/package', 'get_theme_package'),
    ('APIRoute', 'GET', '/api/themes/{theme_id}/files/{path:path}', 'get_theme_file'),
    ('APIRoute', 'POST', '/api/themes/{theme_id}/render', 'render_theme'),
    # The personal channel (web/personal_routes.py)
    ('APIRoute', 'GET', '/api/me/channel', 'get_personal_channel'),
    ('APIRoute', 'PUT', '/api/me/channel', 'update_personal_channel'),
    ('APIRoute', 'POST', '/api/me/channel/listen', 'listen_personal_channel'),
    ('APIRoute', 'DELETE', '/api/me/channel/listen', 'unlisten_personal_channel'),
    # Theme backups from the app (web/backup_routes.py)
    ('APIRoute', 'GET', '/api/me/backups', 'list_backups'),
    ('APIRoute', 'GET', '/api/me/backups/settings', 'get_backup_settings'),
    ('APIRoute', 'PUT', '/api/me/backups/settings', 'put_backup_settings'),
    ('APIRoute', 'GET', '/api/me/backups/{device_id}/playlists', 'get_backup_playlists'),
    ('APIRoute', 'PUT', '/api/me/backups/{device_id}/playlists', 'put_backup_playlists'),
    ('APIRoute', 'GET', '/api/me/backups/{device_id}/themes/{key}', 'get_backup_theme'),
    ('APIRoute', 'PUT', '/api/me/backups/{device_id}/themes/{key}', 'put_backup_mix'),
    ('APIRoute', 'DELETE', '/api/me/backups/{device_id}/themes/{key}', 'delete_backup_theme'),
    ('APIRoute', 'DELETE', '/api/me/backups/{device_id}', 'delete_backup_device'),
    ('APIRoute', 'GET', '/api/me/backups/{device_id}/themes/{key}/package', 'get_backup_package'),
    ('APIRoute', 'PUT', '/api/me/backups/{device_id}/themes/{key}/package', 'put_backup_package'),
    ('APIRoute', 'GET', '/api/me/backups/{device_id}/themes/{key}/upload', 'get_backup_upload'),
    ('APIRoute', 'PUT', '/api/me/backups/{device_id}/themes/{key}/files/{path:path}', 'put_backup_file'),
    ('APIRoute', 'GET', '/api/me/backups/{device_id}/themes/{key}/files/{path:path}', 'get_backup_file'),
]


def _exec(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def m():
    """api.py, web_server.py and web/api_v2.py with Sonorium's logger, themes and version stubbed."""
    package = types.ModuleType("sonorium")
    package.__path__ = [str(PACKAGE)]
    obs = types.ModuleType("sonorium.obs")
    obs.logger = logging.getLogger("sonorium-routes-test")
    obs.logger.instrument = lambda *a, **k: (lambda f: f)
    theme = types.ModuleType("sonorium.theme")
    theme.LoadedTheme = object
    version = types.ModuleType("sonorium.version")
    version.__version__ = "test"
    logbuffer = types.ModuleType("sonorium.logbuffer")
    logbuffer.attach = lambda *a: None
    stubs = {"sonorium": package, "sonorium.obs": obs, "sonorium.theme": theme,
             "sonorium.version": version, "sonorium.logbuffer": logbuffer}
    for name in ("core", "web", "ha"):
        sub = types.ModuleType(f"sonorium.{name}")
        sub.__path__ = [str(PACKAGE / name)]
        stubs[f"sonorium.{name}"] = sub
    with patch.dict(sys.modules, stubs):
        package.runtime = _exec("sonorium.runtime", PACKAGE / "runtime.py")
        api = _exec("sonorium.api", PACKAGE / "api.py")
        v2 = _exec("sonorium.web.api_v2", PACKAGE / "web" / "api_v2.py")
        yield types.SimpleNamespace(api=api, v2=v2)


def _rows(routes):
    # Included routers (the sign-in router, sonorium/auth) are checked in test_auth_*.py
    return [(type(r).__name__, ",".join(sorted(getattr(r, "methods", None) or [])), r.path, r.name)
            for r in routes if hasattr(r, "path")]


def test_app_routes_unchanged(m):
    app = m.api.WebApp(MagicMock()).app
    assert _rows(app.routes) == APP_ROUTES


def test_app_setup_unchanged(m):
    api = m.api.WebApp(MagicMock())
    app = api.app
    assert app.title == "Sonorium test Streaming API"
    assert app.docs_url == "/docs"
    assert app.swagger_ui_parameters == {"tryItOutEnabled": True}
    assert api.port == (8008 if m.api.runtime.STANDALONE else 8080)
    assert len(app.router.on_startup) == 1 and len(app.router.on_shutdown) == 1
    assert len(app.user_middleware) == 3  # cards CORS, legacy error statuses (web/legacy_errors.py), sign-in
    config = api.main_listener()
    assert (config.host, config.port, config.access_log, config.proxy_headers) == ("0.0.0.0", api.port, False, False)
    # Handler docstrings are the summaries in the API docs, as before
    route = next(r for r in app.routes if getattr(r, "path", None) == "/api/status")
    assert route.summary == route.description == ((api.status.__doc__ or "").strip() or None)


def test_route_docs_unchanged(m):
    """Every route's API docs entry: its handler's docstring as summary and description, in the untagged section."""
    app = m.api.WebApp(MagicMock()).app
    routes = [r for r in app.routes if type(r).__name__ == "APIRoute"]
    assert len(routes) == sum(1 for row in APP_ROUTES if row[0] == "APIRoute")
    for route in routes:
        doc = (route.endpoint.__doc__ or "").strip() or None
        assert route.summary == route.description == doc, route.path
        assert route.tags == [None], route.path


def test_v2_routes_unchanged(m):
    router = m.v2.create_api_router(
        session_manager=MagicMock(), group_manager=MagicMock(), ha_registry=MagicMock(),
        state_store=MagicMock(), channel_manager=MagicMock(), plugin_manager=MagicMock(),
        mqtt_manager=MagicMock(),
    )
    assert _rows(router.routes) == V2_ROUTES
