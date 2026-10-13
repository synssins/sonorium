"""
Plugin Framework 2, phase 5: what each plugin may do (approved by the user
before it first runs, and again when an update asks for more), a plugin's own
HTTP routes with an ability each, and Home Assistant entities a plugin
declares. Two unrelated plugins: "lights" lists what it uses, "panel" lists
nothing (so it may use everything, once approved).
"""
import asyncio
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()

LIGHTS = '''
from sonorium.plugins.base import BasePlugin


class Lights(BasePlugin):
    id = "lights"
    name = "Lights"
    version = "1.0.0"
    actions_seen = []

    def get_settings_schema(self):
        return {"on": {"type": "boolean", "default": False, "label": "On"},
                "distance": {"type": "number", "default": 1, "min": 0, "max": 5, "label": "Distance"},
                "mode": {"type": "select", "options": ["mood", "storm"], "default": "mood", "label": "Mode"}}

    async def http_request(self, route_id, request):
        if route_id == "status":
            return {"status": 200, "json": {"strip": request["params"].get("strip"), "q": request["query"]}}
        if route_id == "page":
            return {"status": 200, "body": b"<script>steal()</script>", "content_type": "text/html"}
        return {"status": 201, "text": "flashed"}

    async def ha_action(self, action, value=None):
        Lights.actions_seen.append((action, value))
        return {"value": "storm"} if action == "mode" else {}
'''

LIGHTS_MANIFEST = {
    "id": "lights", "name": "Lights", "version": "1.0.0", "api_version": "1.2",
    "permissions": ["devices.light", "timeline", "ui", "http", "ha.entities", "network.lan"],
    "http": [{"id": "status", "path": "strips/{strip}", "methods": ["GET"], "ability": "ch.view"},
             {"id": "flash", "path": "flash", "methods": ["POST"], "ability": "pg.use"},
             {"id": "page", "path": "page", "methods": ["GET"], "ability": "ch.view"},
             {"id": "open", "path": "open", "methods": ["GET"], "ability": "public"}],
    "ha_entities": [
        {"id": "on", "type": "switch", "name": "Storm lights", "icon": "mdi:weather-lightning", "bind": {"setting": "on"}},
        {"id": "distance", "type": "number", "name": "Distance", "unit": "km", "step": 0.5,
         "bind": {"setting": "distance"}},
        {"id": "flash", "type": "button", "name": "Flash now", "bind": {"action": "flash"}},
        {"id": "mode", "type": "select", "name": "Mode", "options": ["mood", "storm"], "bind": {"action": "mode"}},
        {"id": "online", "type": "sensor", "name": "Strips online", "bind": {"live": True}},
        {"id": "bad", "type": "switch", "name": "Bad", "bind": {"setting": "distance"}},
    ],
    "device_kinds": [{"id": "light", "title": "Lights"}, {"id": "fan", "title": "Fans"}],
}

PANEL = '''
from sonorium.plugins.base import BasePlugin


class Panel(BasePlugin):
    id = "panel"
    name = "Panel"
    version = "1.0.0"
'''


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.state", "sonorium.plugins.events", "sonorium.plugins.host",
                       "sonorium.plugins.manager", "sonorium.plugins.loader", "sonorium.plugins.permissions",
                       "sonorium.plugins.actions", "sonorium.plugins.http_routes", "sonorium.plugins.ha_entities",
                       "sonorium.web.plugin_framework_api", "sonorium.auth.permissions") as mods:
        m.__dict__.update(mods.__dict__)
        import sys
        m.pp = sys.modules["sonorium.plugins.permissions"]  # m.permissions is the sign-in's
        yield


class FakeMqtt:
    is_connected = True

    def __init__(self):
        self.published, self.subscribed = [], []

    def set_message_handler(self, handler):
        self.handler = handler

    def subscribe(self, topic):
        self.subscribed.append(topic)

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))


def write(folder: Path, code: str, manifest: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.py").write_text(code, encoding="utf-8")
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


@pytest.fixture
def world(tmp_path):
    store = m.state.StateStore(tmp_path / "state.json")
    bus = m.events.EventBus()
    registry = m.permissions.PermissionRegistry()
    auth = SimpleNamespace(registry=registry, unregister=lambda pid: registry.set_active(pid, False))
    host = m.host.PluginHost(tmp_path / "config", bus=bus, state_store=store, auth=auth)
    plugins = tmp_path / "plugins"
    write(plugins / "lights", LIGHTS, LIGHTS_MANIFEST)
    write(plugins / "panel", PANEL, {"id": "panel", "name": "Panel", "version": "1.0.0", "api_version": "1.2"})
    m.loader.BUILTIN_PLUGINS_DIR = None
    manager = m.manager.PluginManager(store, plugins_dir=plugins, audio_path=tmp_path / "audio", host=host)
    asyncio.run(manager.initialize())
    # Not approved yet: known from their manifests only (WaitingPlugin), not loaded
    return SimpleNamespace(host=host, manager=manager, store=store, plugins=plugins, registry=registry, tmp=tmp_path,
                           lights=manager.find_plugin("lights"), panel=manager.find_plugin("panel"))


def allow(world, plugin_id):
    """The user approves the plugin and switches it on: it is loaded then. Returns the loaded plugin."""
    world.manager.approve_plugin(plugin_id)
    assert asyncio.run(world.manager.enable_plugin(plugin_id)), world.manager.last_enable_error.get(plugin_id)
    return world.manager.get_plugin(plugin_id)


def test_nothing_runs_before_the_user_approves(world):
    perms = world.host.permissions
    assert [p for p in perms.pending(world.lights)] == LIGHTS_MANIFEST["permissions"]
    assert perms.pending(world.panel) == [m.pp.FULL]
    info = world.lights.to_dict()
    assert [p["label"] for p in info["permissions_pending"]][:2] == ["Add devices: light", "Know when sounds play"]
    assert world.panel.to_dict()["permissions"] is None
    assert world.panel.to_dict()["permissions_pending"] == [{"id": "*", "label": "Everything a plugin can do"}]
    assert world.manager.get_plugin("lights") is None and world.manager.get_plugin("panel") is None  # not loaded
    assert {p["id"] for p in world.manager.list_plugins()} == {"lights", "panel"}
    assert asyncio.run(world.manager.enable_plugin("lights")) is False
    assert "Waiting for your approval" in world.manager.last_enable_error["lights"]
    lights, panel = allow(world, "lights"), allow(world, "panel")
    assert lights.enabled and panel.enabled
    assert lights.to_dict()["permissions_pending"] == []


def test_an_update_that_asks_for_more_waits_for_approval(world):
    allow(world, "lights")
    manifest = dict(LIGHTS_MANIFEST, permissions=LIGHTS_MANIFEST["permissions"] + ["audio.tap"])
    write(world.plugins / "lights", LIGHTS, manifest)
    asyncio.run(world.manager.reload_plugins())
    assert world.manager.get_plugin("lights") is None  # off and not loaded until approved
    waiting = world.manager.find_plugin("lights")
    assert world.host.permissions.pending(waiting) == ["audio.tap"]
    lights = allow(world, "lights")
    world.host.plugin_removed("lights")  # uninstalled: a new install asks again
    assert world.host.permissions.pending(lights)


def test_plugins_that_ran_before_approvals_existed_keep_running(tmp_path, world):
    world.store.settings.enabled_plugins.append("panel")
    world.store.save()
    world.manager._initialized = False
    asyncio.run(world.manager.reload_plugins())
    assert world.manager.get_plugin("panel").enabled  # approved as it was: it already ran


def test_services_refuse_what_a_plugin_did_not_list(world):
    E = m.actions.ActionError
    lights, panel = allow(world, "lights"), allow(world, "panel")
    with pytest.raises(E) as e:
        lights.audio.tap("den", print)
    assert e.value.code == "forbidden" and "audio.tap" in e.value.message
    with pytest.raises(E):
        asyncio.run(lights.actions.start_channel("den"))
    with pytest.raises(E):
        lights.devices.register_kind({"id": "fan", "title": "Fans"})
    with pytest.raises(E):
        lights.attributes.set({"kind": "track", "theme": "t", "id": "Rain"}, {"cue": "flash"})
    with pytest.raises(E):
        lights.new_theme("Mine")
    sub = lights.timeline.subscribe(print)  # listed: allowed
    sub.close()
    assert lights.devices.kinds() and [k["id"] for k in lights.devices.kinds()] == ["light"]
    assert any("devices.fan" in w for w in lights.ui_warnings())  # the kind it didn't list, on its row
    # the panel lists nothing: once approved, nothing is refused by a permission
    with pytest.raises(E) as e:
        asyncio.run(panel.actions.start_channel("den"))
    assert e.value.code != "forbidden"
    panel.timeline.subscribe(print).close()


def test_a_plugins_own_routes_carry_an_ability_each(world):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    world.lights = allow(world, "lights")
    assert any("route open" in w for w in world.lights.ui_warnings())  # "public" isn't an ability
    found = world.registry.match("GET", "/api/plugins/lights/http/strips/porch")
    perms = {str(p.raw) for _, _, p in found}
    assert perms == {"ch.view", "public"}  # the sign-in checks the route's ability; the catch-all alone is public
    assert {str(p.raw) for _, _, p in world.registry.match("POST", "/api/plugins/lights/http/flash")} == {"pg.use", "public"}
    app = FastAPI()
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(world.manager, world.host))
    with TestClient(app) as c:
        r = c.get("/api/plugins/lights/http/strips/porch?x=1")
        assert r.status_code == 200 and r.json() == {"strip": "porch", "q": {"x": "1"}}
        assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
        r = c.post("/api/plugins/lights/http/flash", json={"ms": 60})
        assert r.status_code == 201 and r.text == "flashed"
        assert c.get("/api/plugins/lights/http/page").status_code == 502  # a web page is refused
        assert c.get("/api/plugins/lights/http/nope").status_code == 404
        assert c.delete("/api/plugins/lights/http/flash").status_code == 404
        assert c.get("/api/plugins/panel/http/anything").status_code == 404
    asyncio.run(world.manager.disable_plugin("lights"))
    assert all(str(p.raw) == "public" for _, _, p in world.registry.match("GET", "/api/plugins/lights/http/strips/porch"))


def test_home_assistant_entities_follow_settings_and_actions(world):
    mqtt = FakeMqtt()
    world.host.mqtt.bind(mqtt)
    world.manager.approve_plugin("lights")

    async def main():
        await world.manager.enable_plugin("lights")
        world.lights = world.manager.get_plugin("lights")
        await asyncio.sleep(0.05)
        configs = {t: json.loads(p) for t, p, r in mqtt.published if t.endswith("/config") and p}
        assert set(configs) == {f"homeassistant/{k}/sonorium_plugin_lights_{e}/config" for k, e in
                                [("switch", "on"), ("number", "distance"), ("button", "flash"), ("select", "mode"),
                                 ("sensor", "online")]}
        switch = configs["homeassistant/switch/sonorium_plugin_lights_on/config"]
        assert switch["device"]["name"] == "Sonorium" and switch["icon"] == "mdi:weather-lightning"
        assert switch["command_topic"] == "sonorium/plugin/lights/on/set"
        assert "command_topic" not in configs["homeassistant/sensor/sonorium_plugin_lights_online/config"]
        assert configs["homeassistant/number/sonorium_plugin_lights_distance/config"]["max"] == 5
        assert any("'bad'" in w for w in world.lights.ui_warnings())  # a switch can't show a number setting
        states = {t: p for t, p, r in mqtt.published if t.endswith("/state")}
        assert states["sonorium/plugin/lights/on/state"] == "OFF" and states["sonorium/plugin/lights/distance/state"] == "1"
        # Home Assistant changes a setting: checked, saved, shown
        await world.host.mqtt.dispatch("sonorium/plugin/lights/on/set", "ON")
        assert world.lights.settings["on"] is True and world.store.settings.plugin_settings["lights"]["on"] is True
        await world.host.mqtt.dispatch("sonorium/plugin/lights/distance/set", "9")  # out of range: refused
        assert world.lights.settings["distance"] == 1
        # actions reach the plugin; its answer becomes the state
        await world.host.mqtt.dispatch("sonorium/plugin/lights/flash/set", "PRESS")
        await world.host.mqtt.dispatch("sonorium/plugin/lights/mode/set", "storm")
        assert type(world.lights).actions_seen[-2:] == [("flash", None), ("mode", "storm")]
        assert mqtt.published[-1][:2] == ("sonorium/plugin/lights/mode/state", "storm")
        await world.lights.ha_set_state("online", 2)
        assert mqtt.published[-1][:2] == ("sonorium/plugin/lights/online/state", "2")
        # switched off: the entities leave Home Assistant
        await world.manager.disable_plugin("lights")
        await asyncio.sleep(0.05)
        removed = {t for t, p, r in mqtt.published if t.endswith("/config") and p == "" and r}
        assert len(removed) == 5
    asyncio.run(main())


def test_permission_routes(world):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(world.manager, world.host))
    with TestClient(app) as c:
        assert "timeline" in {p["id"] for p in c.get("/api/plugin-permissions").json()["permissions"]}
        body = c.get("/api/plugins/lights/permissions").json()
        assert len(body["permissions_pending"]) == 6 and body["granted"] == []
        assert c.put("/api/plugins/lights/permissions", json={}).status_code == 400
        body = c.put("/api/plugins/lights/permissions", json={"approve": True}).json()
        assert body["permissions_pending"] == [] and "timeline" in body["granted"]
    routes = m.permissions.ROUTES
    assert routes[("PUT", "/api/plugins/{plugin_id}/permissions")].any_of == {"admin.plugins"}
    assert routes[("GET", "/api/plugins/{plugin_id}/http/{path:path}")].public


# --- Packages and code only after approval ---------------------------------------------------

SERIAL = '''
from pathlib import Path
Path(__file__).with_name("imported.txt").write_text("plugin.py ran")  # top-level code: must wait for approval

from sonorium.plugins.base import BasePlugin


class Serial(BasePlugin):
    id = "serial"
    name = "Serial"
    version = "1.0.0"
    enabled_calls = []

    async def on_enable(self):
        Serial.enabled_calls.append(True)
'''


@pytest.fixture
def serial(world):
    """A plugin with a Python package; the installer is a fake that records what it installs."""
    import sys
    marker = sys.modules["sonorium.plugins.packages"].MARKER
    installs = []
    packages = world.host.packages

    def fake_install(plugin_id, requirements):
        installs.append((plugin_id, list(requirements)))
        folder = packages.folder(plugin_id)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / marker).write_text(json.dumps(packages._wanted(requirements)), encoding="utf-8")

    packages.install = fake_install
    world.host.packages_allowed = lambda: True  # Settings > Advanced > Plugin packages
    manifest = {"id": "serial", "name": "Serial", "version": "1.0.0", "api_version": "1.2",
                "permissions": ["devices.serial"], "requirements": ["pyserial==3.5"]}
    write(world.plugins / "serial", SERIAL, manifest)
    sys.modules.pop("sonorium_plugin_serial", None)
    asyncio.run(world.manager.reload_plugins())
    yield SimpleNamespace(installs=installs, manifest=manifest, imported=world.plugins / "serial" / "imported.txt")
    sys.modules.pop("sonorium_plugin_serial", None)


def test_packages_are_not_installed_and_code_not_run_before_approval(world, serial):
    import sys
    assert serial.installs == [] and not serial.imported.exists()  # read from the manifest only
    assert world.manager.get_plugin("serial") is None and "sonorium_plugin_serial" not in sys.modules
    row = next(p for p in world.manager.list_plugins() if p["id"] == "serial")
    assert row["enabled"] is False and row["requirements"] == ["pyserial==3.5"]
    assert [p["label"] for p in row["permissions_pending"]] == [
        "Add devices: serial", "Install the Python package pyserial==3.5"]
    assert row["permissions_pending"][-1]["id"] == "package:pyserial==3.5"
    # Switching it on without the approval: still nothing installed or run
    assert asyncio.run(world.manager.enable_plugin("serial")) is False
    assert serial.installs == [] and not serial.imported.exists()


def test_packages_are_installed_after_approval_then_the_plugin_loads(world, serial):
    plugin = allow(world, "serial")
    assert serial.installs == [("serial", ["pyserial==3.5"])]
    assert serial.imported.exists() and plugin.enabled and type(plugin).enabled_calls == [True]
    assert world.host.permissions.approved_requirements("serial") == ["pyserial==3.5"]
    # A restart: approved, so it loads again with no question and no second install
    asyncio.run(world.manager.reload_plugins())
    assert world.manager.get_plugin("serial").enabled and len(serial.installs) == 1


def test_packages_switched_off_refuse_the_plugin_after_approval(world, serial):
    world.host.packages_allowed = lambda: False
    world.manager.approve_plugin("serial")
    assert asyncio.run(world.manager.enable_plugin("serial")) is False
    assert "Plugin packages" in world.manager.last_enable_error["serial"]
    assert serial.installs == [] and not serial.imported.exists()


def test_an_update_asking_for_more_packages_stays_off_until_approved(world, serial):
    allow(world, "serial")
    serial.imported.unlink()
    manifest = dict(serial.manifest, requirements=["pyserial==3.5", "crcmod==1.7"])
    write(world.plugins / "serial", SERIAL, manifest)
    asyncio.run(world.manager.reload_plugins())
    assert world.manager.get_plugin("serial") is None and not serial.imported.exists()
    assert world.host.permissions.pending(world.manager.find_plugin("serial")) == ["package:crcmod==1.7"]
    assert len(serial.installs) == 1  # the new package isn't installed
    plugin = allow(world, "serial")
    assert serial.installs[-1] == ("serial", ["pyserial==3.5", "crcmod==1.7"]) and plugin.enabled


def test_an_approval_from_before_packages_counts_for_the_installed_ones(world, serial):
    allow(world, "serial")
    del world.host.permissions._table()["serial"]["requirements"]  # as saved before packages were approved
    asyncio.run(world.manager.reload_plugins())
    assert world.manager.get_plugin("serial").enabled  # no new question: it ran with them
    assert world.host.permissions.approved_requirements("serial") == ["pyserial==3.5"]


def test_packages_that_are_not_approved_are_never_installed(world, serial):
    with pytest.raises(Exception, match="aren't approved"):
        world.host.packages.ensure("serial", serial.manifest, approved=[])
    assert serial.installs == []


def test_a_manifest_saying_builtin_does_not_skip_the_approval(world):
    write(world.plugins / "fake_builtin", PANEL.replace('"panel"', '"fake_builtin"'),
          {"id": "fake_builtin", "name": "Fake", "version": "1.0.0", "api_version": "1.2", "builtin": True})
    asyncio.run(world.manager.reload_plugins())
    assert world.manager.get_plugin("fake_builtin") is None
    assert world.host.permissions.pending(world.manager.find_plugin("fake_builtin")) == [m.pp.FULL]


def test_the_bundled_copy_of_a_builtin_plugin_needs_no_approval(world, tmp_path):
    bundled = tmp_path / "bundled"
    write(bundled / "panel", PANEL, {"id": "panel", "name": "Panel", "version": "1.0.0", "api_version": "1.2"})
    m.loader.BUILTIN_PLUGINS_DIR = bundled
    try:
        asyncio.run(world.manager.reload_plugins())
        panel = world.manager.get_plugin("panel")  # the same files as bundled: loaded, no question
        assert panel is not None and panel._bundled and world.host.permissions.pending(panel) == []
        assert world.manager.get_plugin("lights") is None  # not bundled: waits
        (world.plugins / "panel" / "plugin.py").write_text(PANEL + "\n# changed\n", encoding="utf-8")
        asyncio.run(world.manager.reload_plugins())
        assert world.manager.get_plugin("panel") is None  # a different copy asks like any plugin
    finally:
        m.loader.BUILTIN_PLUGINS_DIR = None
