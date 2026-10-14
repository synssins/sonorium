"""
Automation pieces (plugins/automation.py): command maps grow into "when /
only if / do". Two unrelated plugins add pieces: "weather" an "only if"
condition, "lights" two "do" actions; a remote's map uses them next to
Sonorium's own actions and conditions. No rules screen: the map is the rule.
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.plugins.events", "sonorium.plugins.host", "sonorium.plugins.base",
                       "sonorium.plugins.commands", "sonorium.plugins.automation", "sonorium.plugins.compat",
                       "sonorium.web.plugin_framework_api", "sonorium.auth.permissions") as mods:
        m.__dict__.update(mods.__dict__)
        yield


class FakeActions:
    """What a command map's actions run through: channel A plays, B doesn't."""

    def __init__(self):
        self.done = []

    async def get_channel(self, channel):
        return {"is_playing": channel == "a", "volume": 40, "speakers_muted": False}

    async def stop_channel(self, channel):
        self.done.append(("stop", channel))


@pytest.fixture
def world(tmp_path):
    host = m.host.PluginHost(tmp_path / "config", bus=m.events.EventBus())
    host.automation.now = lambda: datetime(2026, 10, 12, 23, 30)

    class Weather(m.base.BasePlugin):
        id, name = "weather", "Weather"
        stormy = True

        def get_automation(self):
            return {"conditions": [
                {"id": "stormy", "title": "It's stormy outside",
                 "args": {"level": {"type": "select", "label": "At least", "options": ["rain", "storm"]}}},
                {"id": "Bad Id", "title": "x"}]}

        async def automation_condition(self, condition, channel, args):
            return Weather.stormy and args.get("level") in ("rain", "storm")

    class Lights(m.base.BasePlugin):
        id, name = "lights", "Lights"
        ran = []

        def get_automation(self):
            return {"actions": [
                {"id": "flash", "title": "Flash the lights",
                 "args": {"ms": {"type": "number", "label": "Length", "min": 20, "max": 2000, "default": 60}}},
                {"id": "dim", "title": "Dim the channel's lights", "channel": True, "args": {}}]}

        async def automation_action(self, action, channel, args, value=None):
            Lights.ran.append((action, channel, args, value))
            return {"success": action != "dim" or channel == "a", "message": "No lights there"}

    plugins = {}
    for cls in (Weather, Lights):
        p = cls(tmp_path / cls.id, {})
        p._host, p._manifest, p.enabled = host, {"api_version": "1.2"}, True
        plugins[p.id] = p
    host.manager = SimpleNamespace(get_plugin=plugins.get)
    for p in plugins.values():
        host.plugin_enabled(p)
    return SimpleNamespace(host=host, weather=plugins["weather"], lights=plugins["lights"], actions=FakeActions(),
                           W=Weather, L=Lights)


def run(coro):
    return asyncio.run(coro)


def test_pieces_join_the_catalogues_and_bad_ones_are_listed(world):
    actions = m.commands.catalogue(world.host.automation)
    assert actions[0]["id"] == "channel.play"  # Sonorium's first
    mine = [a for a in actions if a.get("plugin_id")]
    assert [(a["id"], a["channel"]) for a in mine] == [("lights:flash", False), ("lights:dim", True)]
    conditions = m.commands.conditions(world.host.automation)
    assert [c["id"] for c in conditions] == ["channel.playing", "channel.stopped", "time.between", "weather:stormy"]
    assert any("Bad Id" in w for w in world.weather.ui_warnings())
    assert "automation" in m.compat.CAPABILITIES and "automation" in world.host.capabilities()


def test_entries_are_checked_against_the_pieces(world):
    maps = world.host.commands
    good = {"code": "KEY_1", "action": "lights:flash", "args": {"ms": 80},
            "only_if": [{"condition": "weather:stormy", "args": {"level": "storm"}},
                        {"condition": "channel.playing", "channel": "a"},
                        {"condition": "time.between", "args": {"from": "22:00", "to": "06:00"}}]}
    saved = maps.set_entries("remote", "main", [good])
    assert saved[0]["only_if"][1] == {"condition": "channel.playing", "channel": "a", "args": {}}
    E = m.commands.CommandError
    for bad in ({**good, "args": {"ms": 5}},                                   # out of range
                {**good, "action": "lights:dim"},                              # needs a channel
                {**good, "only_if": [{"condition": "nope"}]},                  # unknown condition
                {**good, "only_if": [{"condition": "channel.playing"}]},       # needs a channel
                {**good, "only_if": [{"condition": "time.between", "args": {"from": "25:00", "to": "06:00"}}]},
                {**good, "only_if": [{"condition": "weather:stormy", "args": {"level": "hail"}}]}):
        with pytest.raises(E):
            maps.set_entries("remote", "main", [bad])
    # a switched-off plugin's pieces stay in the map as they are
    world.host.plugin_disabled(world.lights)
    kept = maps.set_entries("remote", "main", [good])
    assert kept[0]["action"] == "lights:flash" and kept[0]["args"] == {"ms": 80}


def test_when_only_if_do(world):
    maps = world.host.commands
    maps.set_entries("remote", "main", [
        {"code": "KEY_1", "action": "lights:flash", "args": {"ms": 80},
         "only_if": [{"condition": "weather:stormy", "args": {"level": "storm"}},
                     {"condition": "channel.playing", "channel": "a"}]},
        {"code": "KEY_1", "action": "channel.stop", "channel": "b",
         "only_if": [{"condition": "channel.playing", "channel": "b"}]},           # B isn't playing: skipped
        {"code": "KEY_2", "action": "lights:dim", "channel": "b"},                 # the plugin says it can't
        {"code": "KEY_3", "action": "channel.stop", "channel": "a",
         "only_if": [{"condition": "time.between", "args": {"from": "22:00", "to": "06:00"}}]},
    ])
    result = run(maps.received("remote", "main", "KEY_1", world.actions, value=7))
    assert result["ran"] == ["lights:flash"] and result["errors"] == []
    assert world.L.ran[-1] == ("flash", None, {"ms": 80}, 7)
    world.W.stormy = False
    assert run(maps.received("remote", "main", "KEY_1", world.actions))["ran"] == []  # not stormy: skipped
    result = run(maps.received("remote", "main", "KEY_2", world.actions))
    assert result["ran"] == [] and result["errors"] == ["No lights there"]
    assert run(maps.received("remote", "main", "KEY_3", world.actions))["ran"] == ["channel.stop"]  # 23:30
    world.host.automation.now = lambda: datetime(2026, 10, 12, 12, 0)
    assert run(maps.received("remote", "main", "KEY_3", world.actions))["ran"] == []
    # a plugin switched off: its actions don't run and its conditions don't hold
    world.W.stormy = True
    world.host.plugin_disabled(world.weather)
    assert run(maps.received("remote", "main", "KEY_1", world.actions))["ran"] == []
    world.host.plugin_disabled(world.lights)
    maps.set_entries("remote", "main", [{"code": "KEY_9", "action": "lights:flash", "args": {"ms": 80}}])
    result = run(maps.received("remote", "main", "KEY_9", world.actions))
    assert result["ran"] == [] and "switched off" in result["errors"][0]


def test_catalogue_routes(world):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    manager = SimpleNamespace(get_plugin=lambda pid: None)
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(manager, world.host))
    with TestClient(app) as c:
        assert "lights:flash" in {a["id"] for a in c.get("/api/command-actions").json()["actions"]}
        assert "weather:stormy" in {x["id"] for x in c.get("/api/command-conditions").json()["conditions"]}
    assert m.permissions.ROUTES[("GET", "/api/command-conditions")].any_of == {"admin.plugins"}
