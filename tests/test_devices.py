"""
Devices of any kind a plugin brings (core/devices.py): two unrelated plugins
(lights and wall panels) register their kinds; core lists, remembers, names,
places and assigns their devices, runs their actions through their owner,
and forgets them with their plugin. Nothing in core knows what a light or a
panel is.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.devices", "sonorium.core.plugin_data", "sonorium.plugins.events",
                       "sonorium.plugins.host", "sonorium.plugins.base", "sonorium.plugins.compat",
                       "sonorium.plugins.services", "sonorium.plugins.actions", "sonorium.plugins.ui_contract",
                       "sonorium.web.plugin_framework_api", "sonorium.auth.permissions") as mods:
        m.__dict__.update(mods.__dict__)
        yield


class Sessions:
    """Two channels: Storm plays on the living-room speaker, Hall on the hall one."""

    def __init__(self):
        self.sessions = {"storm": SimpleNamespace(id="storm", name="Storm", speakers=["media_player.living"]),
                         "hall": SimpleNamespace(id="hall", name="Hall", speakers=["media_player.hall"])}
        speakers = {"media_player.living": SimpleNamespace(area_id="living_room"),
                    "media_player.hall": SimpleNamespace(area_id="hall")}
        self.registry = SimpleNamespace(get_speaker=speakers.get)

    def list(self):
        return list(self.sessions.values())

    def get_any(self, sid):
        return self.sessions.get(sid)

    def get_resolved_speakers(self, session):
        return session.speakers


def make_plugins(host, tmp_path):
    class Lights(m.base.BasePlugin):
        id, name = "lights", "Lights"
        found = [{"id": "10.0.0.40", "name": "Porch strip", "model": "Strip 2"},
                 {"id": "10.0.0.41", "name": "Lounge lamp"}]
        calls = []

        def get_device_kinds(self):
            return [{"id": "light", "title": "Lights", "icon": "lightbulb", "latency_ms": 20,
                     "actions": [{"id": "test", "label": "Flash"}]}]

        async def discover_devices(self, kind):
            return list(self.found)

        async def device_action(self, kind, device, action, params):
            self.calls.append((kind, device["device"], action, params))
            return {"success": True, "message": f"{device['name']}: {action}"}

    class Panels(m.base.BasePlugin):
        id, name = "panels", "Panels"

        def get_device_kinds(self):
            return [{"id": "display", "title": "Displays", "one_channel": True,
                     "actions": [{"id": "pattern", "label": "Test pattern"}]},
                    {"id": "speaker", "title": "Not allowed"},              # refused: speakers are core's
                    {"id": "Bad Id", "title": "x"}]                         # refused: the id

        async def discover_devices(self, kind):
            return [{"id": "bedside", "name": "Bedside panel", "area": "bedroom"}]

    out = []
    for cls in (Lights, Panels):
        p = cls(tmp_path / cls.id, {})
        p._host, p._manifest, p.enabled = host, {"api_version": "1.2"}, True
        host.manager = SimpleNamespace(get_plugin={"lights": None, "panels": None}.get)
        out.append(p)
    plugins = {p.id: p for p in out}
    host.manager = SimpleNamespace(get_plugin=plugins.get)
    return out


@pytest.fixture
def world(tmp_path):
    bus = m.events.EventBus()
    host = m.host.PluginHost(tmp_path / "config", bus=bus, session_manager=Sessions())
    lights, panels = make_plugins(host, tmp_path)
    return SimpleNamespace(host=host, bus=bus, lights=lights, panels=panels, devices=host.devices, tmp=tmp_path)


def run(coro):
    return asyncio.run(coro)


def test_kinds_are_registered_and_out_of_contract_ones_refused_on_the_plugins_row(world):
    world.host.plugin_enabled(world.lights)
    world.host.plugin_enabled(world.panels)
    assert [k["id"] for k in world.devices.kinds()] == ["light", "display"]
    light = world.devices.kind("light")
    assert light["plugin_id"] == "lights" and light["latency_ms"] == 20 and light["icon"]["value"] == "lightbulb"
    warnings = world.panels.ui_warnings()
    assert len(warnings) == 2 and any("audio output" in w for w in warnings)
    assert world.lights.ui_warnings() == []
    with pytest.raises(m.devices.DeviceError):  # one kind, one owner
        world.devices.register_kind("panels", {"id": "light", "title": "Mine"})


def test_devices_are_listed_remembered_named_and_placed(world):
    async def main():
        world.host.plugin_enabled(world.lights)
        world.host.plugin_enabled(world.panels)
        found = await world.devices.discover()
        assert sorted(d["name"] for d in found) == ["Bedside panel", "Lounge lamp", "Porch strip"]
        porch = next(d for d in found if d["name"] == "Porch strip")
        assert porch["id"].startswith("dev:light:lights:") and porch["device"] == "10.0.0.40"
        assert porch["online"] and porch["use"] and porch["model"] == "Strip 2" and porch["actions"][0]["label"] == "Flash"
        assert next(d for d in found if d["kind"] == "display")["area"] == "bedroom"  # the plugin's suggestion
        # the user's choices stay through a new listing
        world.devices.update(porch["id"], {"name": "Porch", "area": "living_room"})
        Lights = type(world.lights)
        Lights.found = Lights.found[:1] + [{"id": "10.0.0.40", "name": "Strip renamed by the plugin"}][:0]
        await world.devices.discover("light")
        again = world.devices.get(porch["id"])
        assert again["name"] == "Porch" and again["renamed"] and again["area"] == "living_room"
        lounge = next(d for d in world.devices.list("light") if d["device"] == "10.0.0.41")
        assert lounge["online"] is False  # not in the last listing: offline, still remembered
        world.devices.update(porch["id"], {"name": None})
        assert world.devices.get(porch["id"])["name"] == "Porch strip"
        with pytest.raises(m.devices.DeviceError):
            world.devices.update(porch["id"], {"colour": "red"})
        with pytest.raises(m.devices.DeviceError):
            world.devices.remove(porch["id"])  # online: it would come straight back
        world.devices.remove(lounge["id"])
        assert [d["device"] for d in world.devices.list("light")] == ["10.0.0.40"]
        types = [e.type for e in world.bus.history()]
        assert types.count("device.added") == 3 and "device.changed" in types and "device.removed" in types
        assert "device.unavailable" in types
    run(main())


def test_assignment_to_channels_and_areas(world):
    async def main():
        world.host.plugin_enabled(world.lights)
        world.host.plugin_enabled(world.panels)
        await world.devices.discover()
        porch = next(d for d in world.devices.list("light") if d["device"] == "10.0.0.40")
        lounge = next(d for d in world.devices.list("light") if d["device"] == "10.0.0.41")
        panel = world.devices.list("display")[0]
        world.devices.assign(porch["id"], channels=["storm"])
        world.devices.assign(lounge["id"], areas=["hall"])
        assert [d["name"] for d in world.devices.assigned("storm")] == ["Porch strip"]
        assert [(d["name"], d["follows_by"]) for d in world.devices.assigned("hall")] == [("Lounge lamp", "area")]
        assert world.devices.following(lounge["id"]) == ["hall"]
        world.devices.update(porch["id"], {"use": False})
        assert world.devices.assigned("storm") == []  # switched off: follows nothing
        with pytest.raises(m.devices.DeviceError):
            world.devices.assign(panel["id"], channels=["storm", "hall"])  # one channel at a time
        with pytest.raises(m.devices.DeviceError):
            world.devices.assign(panel["id"], channels=["nope"])
        world.devices.assign(panel["id"], channels=["hall"], areas=["living_room"])
        assert [d["name"] for d in world.devices.assigned("storm", "display")] == []  # assigned to Hall by name
        assert [d["name"] for d in world.devices.assigned("hall", "display")] == ["Bedside panel"]
    run(main())


def test_actions_go_through_the_owner_and_plugins_drive_each_others_devices(world):
    async def main():
        world.host.plugin_enabled(world.lights)
        world.host.plugin_enabled(world.panels)
        await world.devices.discover()
        porch = next(d for d in world.devices.list("light") if d["device"] == "10.0.0.40")
        # the panels plugin, through its own facade, flashes the lights plugin's strip
        result = await world.panels.devices.invoke(porch["id"], "flash", {"ms": 60})
        assert result == {"success": True, "message": "Porch strip: flash"}
        assert type(world.lights).calls[-1] == ("light", "10.0.0.40", "flash", {"ms": 60})
        assert [d["id"] for d in world.panels.devices.list("light")] == [d["id"] for d in world.devices.list("light")]
        panel = world.devices.list("display")[0]
        refused = await world.lights.devices.invoke(panel["id"], "pattern")  # Panels has no device_action
        assert refused["success"] is False and "Unknown action" in refused["message"]
        with pytest.raises(m.actions.ActionError):
            await world.lights.devices.invoke(panel["id"], "Bad Action!")
        # a plugin reports its own device's state
        world.lights.devices.set_status("10.0.0.40", online=True, error="Too hot")
        assert world.devices.get(porch["id"])["error"] == "Too hot"
        with pytest.raises(m.actions.ActionError):
            world.panels.devices.set_status("10.0.0.40", error="not mine")
        with pytest.raises(m.actions.ActionError):
            await world.panels.devices.discover("light")  # only its own kinds
    run(main())


def test_switched_off_hides_and_keeps_uninstalled_forgets(world):
    async def main():
        world.host.plugin_enabled(world.lights)
        await world.devices.discover("light")
        porch = next(d for d in world.devices.list("light") if d["device"] == "10.0.0.40")
        world.devices.update(porch["id"], {"name": "Porch"})
        world.host.plugin_disabled(world.lights)
        assert world.devices.kinds() == [] and world.devices.list() == []
        assert world.devices.get(porch["id"])["name"] == "Porch" and not world.devices.get(porch["id"])["online"]
        world.host.plugin_enabled(world.lights)
        assert world.devices.get(porch["id"])["name"] == "Porch"  # back as it was
        world.host.plugin_removed("lights")
        assert world.devices.list(active_only=False) == []
    run(main())


def test_devices_are_kept_in_the_framework_store(world, tmp_path):
    async def main():
        world.host.plugin_enabled(world.lights)
        await world.devices.discover("light")
        porch = next(d for d in world.devices.list("light") if d["device"] == "10.0.0.40")
        world.devices.assign(porch["id"], channels=["storm"])
        saved = json.loads((tmp_path / "config" / "plugin_framework.json").read_text(encoding="utf-8"))
        assert saved["devices"][porch["id"]]["channels"] == ["storm"]
        fresh = m.devices.Devices(world.host.store)
        assert fresh.get(porch["id"])["channels"] == ["storm"]
    run(main())


def test_device_is_an_attribute_class_and_a_ui_target():
    assert "device" in m.plugin_data.OBJECT_CLASSES and "device" in m.plugin_data.SIDE_TABLE_CLASSES
    target = m.ui_contract.target("device.row")
    assert target["object"] == "device" and set(target["spots"]) == {"badges", "columns", "menu", "settings"}
    assert "devices" in m.compat.CAPABILITIES and m.compat.API_VERSION == "1.2"


def test_http_routes(world):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    manager = SimpleNamespace(get_plugin=lambda pid: None)
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(manager, world.host))
    world.host.plugin_enabled(world.lights)
    with TestClient(app) as c:
        assert [k["id"] for k in c.get("/api/plugin-devices/kinds").json()["kinds"]] == ["light"]
        found = c.post("/api/plugin-devices/discover?kind=light").json()["devices"]
        porch = next(d for d in found if d["device"] == "10.0.0.40")
        r = c.put(f"/api/plugin-devices/{porch['id']}", json={"name": "Porch", "area": "living_room", "channels": ["storm"]})
        assert r.status_code == 200 and r.json()["name"] == "Porch" and r.json()["channels"] == ["storm"]
        assert c.put(f"/api/plugin-devices/{porch['id']}", json={"nope": 1}).status_code == 400
        assert c.put("/api/plugin-devices/dev:x", json={"name": "a"}).status_code == 404
        assert c.post("/api/plugin-devices/discover?kind=nope").status_code == 404
        r = c.post(f"/api/plugin-devices/{porch['id']}/actions/test", json={})
        assert r.json() == {"success": True, "message": "Porch: test"}
        assert [d["name"] for d in c.get("/api/sessions/storm/plugin-devices").json()["devices"]] == ["Porch"]
        assert c.delete(f"/api/plugin-devices/{porch['id']}").status_code == 400  # online
        assert c.get("/api/plugin-devices?plugin=lights").json()["devices"]
    routes = m.permissions.ROUTES
    for method, path, perm in [("GET", "/api/plugin-devices", "sp.view"), ("PUT", "/api/plugin-devices/{device_id}", "sp.set"),
                               ("POST", "/api/plugin-devices/{device_id}/actions/{action}", "sp.set"),
                               ("GET", "/api/sessions/{session_id}/plugin-devices", "ch.view")]:
        assert routes[(method, path)].any_of == {perm}
