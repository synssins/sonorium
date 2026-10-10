"""Removing the Home Assistant or MQTT connection in standalone Settings > Connection (no restart)."""

import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"


def _exec(name, relative):
    spec = importlib.util.spec_from_file_location(name, PACKAGE / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def app(monkeypatch, tmp_path):
    """Standalone runtime, the HA registry and ApiSonorium, without fmtr, HA, MQTT or the audio engine."""
    monkeypatch.setenv("SONORIUM_STANDALONE", "1")
    monkeypatch.setenv("SONORIUM_CONNECTION_FILE", str(tmp_path / "sonorium" / "connection.json"))
    monkeypatch.setenv("SONORIUM__HA_CORE_API", "http://ha.invalid:8123/api")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")
    for key in ("SONORIUM__MQTT_HOST", "SONORIUM__MQTT_PORT", "SONORIUM__MQTT_USERNAME", "SONORIUM__MQTT_PASSWORD"):
        monkeypatch.delenv(key, raising=False)

    package = types.ModuleType("sonorium")
    package.__path__ = [str(PACKAGE)]
    obs = types.ModuleType("sonorium.obs")
    obs.logger = logging.getLogger("sonorium-connection-test")
    theme = types.ModuleType("sonorium.theme")
    theme.ThemeDefinition = object
    version = types.ModuleType("sonorium.version")
    version.__version__ = "test"
    logbuffer = types.ModuleType("sonorium.logbuffer")
    fmtr = types.ModuleType("fmtr")
    fmtr_tools = types.ModuleType("fmtr.tools")
    fmtr_tools.http = MagicMock()
    fmtr_tools.api = SimpleNamespace(Base=object, Endpoint=MagicMock())
    fmtr.tools = fmtr_tools
    stubs = {
        "sonorium": package, "sonorium.obs": obs, "sonorium.theme": theme, "sonorium.version": version,
        "sonorium.logbuffer": logbuffer, "fmtr": fmtr, "fmtr.tools": fmtr_tools,
    }
    with patch.dict(sys.modules, stubs):
        runtime = _exec("sonorium.runtime", "runtime.py")
        package.runtime = runtime
        registry = _exec("sonorium.ha.registry_under_test", "ha/registry.py")
        api = _exec("sonorium.api_under_test", "api.py")
        yield SimpleNamespace(runtime=runtime, registry=registry, api=api)


def make_registry(m):
    """HA floor/area/speakers, Sonorium's own spaces and two network speakers (one at an HA speaker's IP)."""
    r = m.registry
    registry = r.HARegistry("http://ha.invalid:8123/api", "token")
    registry._ha_floors = {"ground": r.Floor("ground", "Ground")}
    registry._ha_areas = {"kitchen": r.Area("kitchen", "Kitchen", floor_id="ground")}
    registry._ha_speakers = {
        "media_player.kitchen": r.Speaker("media_player.kitchen", "Kitchen Sonos", area_id="kitchen",
                                          ip_address="10.0.0.5", source=["ha"]),
        "media_player.tv": r.Speaker("media_player.tv", "TV", source=["ha"]),
    }
    local = {"floors": [{"id": "floor_upstairs", "name": "Upstairs"}],
             "areas": [{"id": "area_den", "name": "Den", "floor_id": "floor_upstairs"},
                       {"id": "area_kitchen", "name": "Kitchen"}]}
    registry.set_local_spaces_source(lambda: local)
    registry.set_extra_speaker_source(lambda: [
        {"entity_id": "net:sonos:A", "name": "Kitchen (Sonos)", "ip_address": "10.0.0.5"},
        {"entity_id": "net:dlna:B", "name": "Den (DLNA)", "ip_address": "10.0.0.9"},
    ])
    registry.set_speaker_settings_source(lambda: {"net:dlna:B": {"room": "area_den"}})
    registry._rebuild()
    return registry


def make_api(m, registry=None, mqtt_connected=True):
    api = object.__new__(m.api.ApiSonorium)
    mqtt_client = SimpleNamespace(is_connected=mqtt_connected)

    async def close():
        mqtt_client.is_connected = False
    mqtt_client.close = AsyncMock(side_effect=close)

    settings = SimpleNamespace(enabled_speakers_exact=True, enabled_speakers=["media_player.kitchen", "net:dlna:B"])
    api.client = SimpleNamespace(mqtt_client=mqtt_client, device=SimpleNamespace())
    api._ha_registry = registry
    api._state_store = SimpleNamespace(settings=settings, save=Mock())
    api._media_controller = SimpleNamespace(controller=SimpleNamespace(ha_controller=object()))
    api._mqtt_manager = None
    return api


def all_ids(hierarchy):
    return sorted(s.entity_id for s in hierarchy.get_all_speakers())


def space_names(hierarchy):
    floors = [f.name for f in hierarchy.floors]
    areas = [a.name for f in hierarchy.floors for a in f.areas] + [a.name for a in hierarchy.unassigned_areas]
    return floors, sorted(areas)


# --- runtime helpers -----------------------------------------------------------

def test_clear_connection_removes_keys_and_keeps_others(app):
    rt = app.runtime
    rt.save_connection({"ha_url": "http://ha:8123", "ha_token": "secret", "mqtt_host": "broker",
                        "mqtt_password": "pw", "stream_url": "http://me:8008"})
    left = rt.clear_connection(rt.HA_FIELDS)
    assert left == {"mqtt_host": "broker", "mqtt_password": "pw", "stream_url": "http://me:8008"}
    assert json.loads(rt.CONNECTION_FILE.read_text()) == left
    rt.clear_connection(rt.MQTT_FIELDS)
    assert rt.load_connection() == {"stream_url": "http://me:8008"}


def test_clear_ha_env_makes_ha_unconfigured(app):
    rt = app.runtime
    assert rt.ha_configured()
    rt.clear_ha_env()
    assert not rt.ha_configured()


# --- DELETE /api/connection/ha ---------------------------------------------------

def test_delete_ha_leaves_only_sonorium_spaces_and_network_speakers(app):
    rt = app.runtime
    rt.save_connection({"ha_url": "http://ha.invalid:8123", "ha_token": "secret", "mqtt_host": "broker"})
    registry = make_registry(app)
    before = registry.hierarchy
    assert all_ids(before) == ["media_player.kitchen", "media_player.tv", "net:dlna:B"]  # A merged into kitchen
    assert space_names(before) == (["Ground", "Upstairs"], ["Den", "Kitchen"])

    api = make_api(app, registry)
    result = asyncio.run(api.delete_connection_ha())

    assert result["status"] == "ok"
    assert result["connection"]["ha_connected"] is False
    assert result["connection"]["ha_url"] == "" and result["connection"]["ha_token_set"] is False
    assert rt.load_connection() == {"mqtt_host": "broker"}
    assert not rt.ha_configured()

    after = registry.hierarchy
    assert all_ids(after) == ["net:dlna:B", "net:sonos:A"]  # A is listed on its own again
    assert space_names(after) == (["Upstairs"], ["Den", "Kitchen"])
    assert all("ha" not in f.source for f in after.floors)
    assert registry.get_area("kitchen") is None and registry.get_area("area_kitchen").source == ["local"]
    assert [s.entity_id for s in registry.get_area("area_den").speakers] == ["net:dlna:B"]

    # Commands no longer go to Home Assistant; A takes over kitchen's "switched on"
    assert api._media_controller.controller.ha_controller is None
    assert api._state_store.settings.enabled_speakers == ["media_player.kitchen", "net:dlna:B", "net:sonos:A"]
    api._state_store.save.assert_called_once()


def test_refresh_after_removal_never_fetches_from_ha(app, monkeypatch):
    registry = make_registry(app)
    asyncio.run(make_api(app, registry).delete_connection_ha())
    # Even if the variables came back, the registry stays away from Home Assistant
    monkeypatch.setenv("SONORIUM__HA_CORE_API", "http://ha.invalid:8123/api")
    registry._fetch_registries_via_websocket = Mock(side_effect=AssertionError("websocket fetch"))
    registry._fetch_speakers = Mock(side_effect=AssertionError("states fetch"))
    hierarchy = registry.refresh()
    assert all_ids(hierarchy) == ["net:dlna:B", "net:sonos:A"]
    assert all_ids(registry.merge_extra_speakers()) == ["net:dlna:B", "net:sonos:A"]


def test_saved_selections_keep_their_network_speakers(app):
    registry = make_registry(app)
    asyncio.run(make_api(app, registry).delete_connection_ha())
    # kitchen had A merged into it: A plays instead; tv is gone
    assert registry.resolve_selection(include_speakers=["media_player.kitchen", "media_player.tv", "net:dlna:B"]) \
        == ["net:dlna:B", "net:sonos:A"]
    assert registry.resolve_selection(include_areas=["kitchen"], include_floors=["ground"]) == []
    assert registry.resolve_selection(include_speakers=["net:dlna:B", "media_player.kitchen"],
                                      exclude_speakers=["media_player.kitchen"]) == ["net:dlna:B"]


def test_delete_ha_404_in_the_addon(app, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(app.runtime, "features", lambda: {"connection_settings": {"available": False}})
    api = make_api(app, make_registry(app))
    for call in (api.delete_connection_ha, api.delete_connection_mqtt):
        with pytest.raises(HTTPException) as e:
            asyncio.run(call())
        assert e.value.status_code == 404


# --- DELETE /api/connection/mqtt -------------------------------------------------

def test_delete_mqtt_clears_fields_and_disconnects(app):
    rt = app.runtime
    rt.save_connection({"ha_url": "http://ha.invalid:8123", "mqtt_host": "broker", "mqtt_port": 1884,
                        "mqtt_username": "u", "mqtt_password": "pw", "stream_url": "http://me:8008"})
    api = make_api(app, make_registry(app))
    manager = SimpleNamespace(remove_all_entities=AsyncMock(), stop_publishing=Mock())
    api._mqtt_manager = manager

    result = asyncio.run(api.delete_connection_mqtt())

    assert result["status"] == "ok" and result["restart_required"] is False
    conn = result["connection"]
    assert conn["mqtt_connected"] is False
    assert (conn["mqtt_host"], conn["mqtt_port"], conn["mqtt_username"], conn["mqtt_password_set"]) == ("", 1883, "", False)
    assert rt.load_connection() == {"ha_url": "http://ha.invalid:8123", "stream_url": "http://me:8008"}
    manager.remove_all_entities.assert_awaited_once()  # while still connected
    manager.stop_publishing.assert_called_once()
    api.client.mqtt_client.close.assert_awaited_once()


def test_delete_mqtt_when_disconnect_fails_asks_for_restart(app):
    app.runtime.save_connection({"mqtt_host": "broker"})
    api = make_api(app, make_registry(app), mqtt_connected=False)
    api.client.mqtt_client.close = AsyncMock(side_effect=RuntimeError("stuck"))
    result = asyncio.run(api.delete_connection_mqtt())
    assert result["restart_required"] is True
    assert app.runtime.load_connection() == {}


def test_mqtt_manager_removes_every_entity_then_goes_quiet():
    obs = types.ModuleType("sonorium.obs")
    obs.logger = Mock()
    with patch.dict(sys.modules, {"sonorium.obs": obs}):
        state = _exec("connection_test_state", "core/state.py")
        entities = _exec("connection_test_entities", "ha/mqtt_entities.py")
    sessions = {"s1": state.Session(id="s1", name="Bedroom")}
    client = Mock()
    manager = entities.SonoriumMQTTManager(
        state_store=SimpleNamespace(sessions=sessions), session_manager=Mock(), mqtt_client=client,
    )

    asyncio.run(manager.remove_all_entities())

    published = {c.args[0]: (c.args[1], c.kwargs.get("retain")) for c in client.publish.call_args_list}
    assert all(payload == "" and retain is True for payload, retain in published.values())
    assert "homeassistant/switch/sonorium_bedroom_play/config" in published
    assert "homeassistant/select/sonorium_session/config" in published
    assert "homeassistant/sensor/sonorium_global_active_sessions/config" in published

    client.publish.reset_mock()
    asyncio.run(manager.update_session_state(sessions["s1"]))
    asyncio.run(manager.handle_command("sonorium/play/set", "ON"))
    client.publish.assert_not_called()
