"""
Plugin API 1.1 end to end: the compatibility check (api_version, requires),
packages and binaries, command maps (learn, run), the MQTT router, the
Announcements plugin's notify entities with Home Assistant's text to speech
(MQTT and HA mocked), and the new routes.
"""
import asyncio
import hashlib
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from playback_helpers import PACKAGE, addon_modules  # noqa: E402
import test_sequences_playlists as runner_tests  # noqa: E402

m = SimpleNamespace()
ANNOUNCEMENTS = PACKAGE / "plugins" / "builtin" / "announcements"


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.session_manager", "sonorium.core.state", "sonorium.core.playback_runner",
                       "sonorium.core.sequences", "sonorium.core.playlists", "sonorium.plugins.events",
                       "sonorium.plugins.host", "sonorium.plugins.manager", "sonorium.plugins.loader",
                       "sonorium.plugins.compat", "sonorium.plugins.packages", "sonorium.plugins.commands",
                       "sonorium.plugins.mqtt", "sonorium.plugins.homeassistant",
                       "sonorium.web.plugin_framework_api", "sonorium.alerts") as mods:
        m.__dict__.update(mods.__dict__)
        runner_tests.m.__dict__.update(mods.__dict__)
        yield


REMOTE = '''
from sonorium.plugins.base import BasePlugin


class Remote(BasePlugin):
    id = "remote"
    name = "Remote"
    version = "1.0.0"

    def get_command_maps(self):
        return [{"id": "ir", "title": "IR remote", "codes": [{"code": "KEY_UP", "label": "Up"}]}]
'''


class FakeOverlay:
    """Records what would play (the mixing has its own tests)."""

    def __init__(self):
        self.played = []
        self.gates = {}

    async def play(self, channels, source=None, **kwargs):
        self.played.append({"channels": list(channels), "source": source, **kwargs})
        return {"sound_id": f"s_{len(self.played)}", "length": 1.0, "capped": False,
                "channels": {c: {"state": "playing", "message": ""} for c in channels}}

    def sounds(self):
        return []

    def cancel(self, sound_id, channel_id=None):
        return False

    def remove_gate(self, owner):
        self.gates.pop(owner, None)


class FakeMqtt:
    def __init__(self):
        self.is_connected = True
        self.published = []
        self.subscribed = []
        self.handler = None

    def set_message_handler(self, handler):
        self.handler = handler

    def subscribe(self, topic):
        self.subscribed.append(topic)

    def publish(self, topic, payload, retain=False):
        self.published.append((topic, payload, retain))


class FakeHA:
    """An httpx.AsyncClient stand-in answering tts_get_url and the proxy."""
    calls = []

    def __init__(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def request(self, method, url, json=None, headers=None):
        FakeHA.calls.append((method, url, json, headers))
        if url.endswith("/api/states"):
            return SimpleNamespace(status_code=200, json=lambda: [{"entity_id": "tts.piper"}], text="")
        return SimpleNamespace(status_code=200, json=lambda: {"url": "http://ha.local:8123/api/tts_proxy/abc.mp3",
                                                              "path": "/api/tts_proxy/abc.mp3"}, text="")

    async def get(self, url, headers=None):
        FakeHA.calls.append(("GET", url, None, headers))
        return SimpleNamespace(status_code=200, content=b"ID3-fake-mp3")


@pytest.fixture
def w(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    bus = m.events.EventBus()
    old_bus = m.events.set_bus(bus)
    t = runner_tests.build(tmp_path)
    host = m.host.PluginHost(tmp_path / "config", bus=bus, session_manager=t.manager, state_store=t.store)
    overlay = FakeOverlay()
    host.set_audio(overlay, None, None)
    FakeHA.calls = []
    host.homeassistant = m.homeassistant.HomeAssistant(lambda: ("http://supervisor/core/api", "tok"),
                                                       client_factory=FakeHA)
    plugins_dir = tmp_path / "plugins"
    shutil.copytree(ANNOUNCEMENTS, plugins_dir / "announcements")
    (plugins_dir / "remote").mkdir()
    (plugins_dir / "remote" / "plugin.py").write_text(REMOTE)
    m.loader.BUILTIN_PLUGINS_DIR = None
    manager = m.manager.PluginManager(t.store, plugins_dir=plugins_dir, audio_path=tmp_path / "audio", host=host)
    app = FastAPI()
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(manager, host))
    with TestClient(app) as client:
        call = client.portal.call
        call(manager.initialize)
        call(host.start)
        for plugin_id in ("announcements", "remote"):
            assert call(manager.enable_plugin, plugin_id), plugin_id
        yield SimpleNamespace(t=t, bus=bus, host=host, manager=manager, client=client, call=call, overlay=overlay,
                              plugins_dir=plugins_dir)
        call(host.stop)
    m.events.set_bus(old_bus)


def wait_for(condition, seconds=3.0):
    give_up = time.monotonic() + seconds
    while time.monotonic() < give_up:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


# --- Compatibility ---

def test_compat_check_messages():
    check = m.compat.check
    assert check({"name": "Old"}) is None                                  # no api_version: 1.0
    assert check({"name": "X", "api_version": "1.1"}) is None
    assert "needs plugin API 1.9; this Sonorium has 1.1" in check({"name": "X", "api_version": "1.9"})
    assert "needs plugin API 2.0" in check({"name": "X", "api_version": "2.0"})
    assert "plugin API 0.x" in check({"name": "X", "api_version": "0.3"})
    assert "can't read" in check({"name": "X", "api_version": "one"})
    assert "teleport" in check({"name": "X", "requires": ["teleport"]})
    assert "volume ramps" in check({"name": "X", "requires": ["audio.ramps"]}, available={"events"})


def test_refused_plugin_is_listed_with_its_reason_and_its_code_never_runs(w, tmp_path):
    folder = w.plugins_dir / "future"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"id": "future", "name": "Future", "version": "1.0.0",
                                                      "api_version": "1.7"}))
    (folder / "plugin.py").write_text("raise SystemExit('this must never run')\n")
    ramps = w.plugins_dir / "ramper"
    ramps.mkdir()
    (ramps / "manifest.json").write_text(json.dumps({"id": "ramper", "name": "Ramper", "version": "1.0.0",
                                                     "api_version": "1.1", "requires": ["audio.ramps"]}))
    (ramps / "plugin.py").write_text("raise SystemExit('nor this')\n")
    w.call(w.manager.reload_plugins)
    listed = {p["id"]: p for p in w.manager.list_plugins()}
    assert listed["future"]["compatible"] is False and "1.7" in listed["future"]["problem"]
    assert "volume ramps" in listed["ramper"]["problem"]          # host has no ramp service here
    assert listed["announcements"]["compatible"] is True and listed["announcements"]["api_version"] == "1.1"
    assert any("Future" in a["message"] for a in m.alerts.active())  # and the alert bar says so


# --- Packages and binaries ---

def test_requirements_are_checked_and_off_by_default(tmp_path):
    good = m.packages.check_requirements(["pyserial==3.5", "requests[socks]>=2.31,<3", "numpy"])
    assert good[0] == "pyserial==3.5"
    for bad in (["-e git+https://x"], ["https://evil/pkg.whl"], ["../local"], ["pkg; os_name=='nt'"],
                ["--index-url=x"], "pyserial"):
        with pytest.raises(m.packages.PackageError):
            m.packages.check_requirements(bad)
    packages = m.packages.Packages(tmp_path / "pp", allowed=lambda: False)
    with pytest.raises(m.packages.PackageError, match="Plugin packages in Settings"):
        packages.ensure("p", {"name": "P", "requirements": ["pyserial==3.5"]})
    assert packages.ensure("p", {"name": "P"}) is None


def test_binaries_are_checked_by_sha256(tmp_path):
    key = m.packages.platform_key()
    plugin = tmp_path / "editor"
    (plugin / "bin").mkdir(parents=True)
    tool = plugin / "bin" / "ffmpeg"
    tool.write_bytes(b"#!/bin/sh\necho ffmpeg\n")
    digest = hashlib.sha256(tool.read_bytes()).hexdigest()
    manifest = {"binaries": {"ffmpeg": {key: {"path": "bin/ffmpeg", "sha256": digest},
                                        "plan9-mips": {"path": "bin/none", "sha256": "0" * 64}}}}
    assert m.packages.check_binaries(plugin, manifest) == {"ffmpeg": tool.resolve()}
    manifest["binaries"]["ffmpeg"][key]["sha256"] = "1" * 64
    with pytest.raises(m.packages.PackageError, match="doesn't match"):
        m.packages.check_binaries(plugin, manifest)
    manifest["binaries"]["ffmpeg"][key] = {"path": "../../etc/passwd", "sha256": digest}
    with pytest.raises(m.packages.PackageError, match="outside"):
        m.packages.check_binaries(plugin, manifest)


# --- Command maps ---

def test_command_map_learns_and_runs_actions(w):
    c, session = w.client, w.t.session
    actions = c.get("/api/command-actions").json()["actions"]
    assert any(a["id"] == "channel.volume_up" for a in actions)
    maps = c.get("/api/plugins/remote/command-maps").json()["maps"]
    assert maps[0]["id"] == "ir" and maps[0]["entries"] == []

    bad = c.put("/api/plugins/remote/command-maps/ir", json={"entries": [{"code": "X", "action": "channel.play"}]})
    assert bad.status_code == 400 and "needs a channel" in bad.json()["detail"]
    entries = [{"code": "KEY_UP", "label": "Up", "action": "channel.volume_up", "channel": session.id,
                "args": {"step": 10}},
               {"code": "KEY_SET", "action": "channel.volume", "channel": session.id}]
    assert c.put("/api/plugins/remote/command-maps/ir", json={"entries": entries}).status_code == 200

    before = session.volume
    result = c.post("/api/plugins/remote/command-maps/ir/test", json={"code": "KEY_UP"}).json()
    assert result["ran"] == ["channel.volume_up"] and session.volume == min(100, before + 10)
    c.post("/api/plugins/remote/command-maps/ir/test", json={"code": "KEY_SET", "value": "33"})
    assert session.volume == 33

    # Learning: the next code the plugin receives is the answer
    remote = w.manager.get_plugin("remote")

    async def learn_and_press():
        waiting = asyncio.ensure_future(w.host.commands.learn("remote", "ir", 5))
        await asyncio.sleep(0.05)
        pressed = await remote.commands.received("ir", "0x20DF10EF")
        return await waiting, pressed
    code, pressed = w.call(learn_and_press)
    assert code == "0x20DF10EF" and pressed["learned"]
    assert any(e.type == "command.received" for e in w.bus.history())


# --- MQTT router ---

def test_topic_filters():
    tm = m.mqtt.topic_matches
    assert tm("sonorium/announce/+/set", "sonorium/announce/abc/set")
    assert not tm("sonorium/announce/+/set", "sonorium/announce/abc/def/set")
    assert tm("zigbee2mqtt/#", "zigbee2mqtt/door/contact") and tm("#", "a")
    for bad in ("a/#/b", "a/b+", ""):
        with pytest.raises(ValueError):
            m.mqtt.check_filter(bad)


def test_router_sends_messages_to_core_then_plugins():
    async def main():
        router = m.mqtt.MqttRouter()
        seen = []

        async def core(topic, payload):
            seen.append(("core", topic))
        router.subscribe("p", "ir/+/code", lambda t, p: seen.append(("p", t, p)))
        client = FakeMqtt()
        router.bind(client, core)
        assert client.subscribed == ["ir/+/code"]
        await client.handler("ir/livingroom/code", "KEY_UP")
        assert seen == [("core", "ir/livingroom/code"), ("p", "ir/livingroom/code", "KEY_UP")]
        assert await router.publish("x/y", {"a": 1})
        assert client.published[-1] == ("x/y", '{"a": 1}', False)
        router.unsubscribe_owner("p")
        await client.handler("ir/livingroom/code", "KEY_DOWN")
        assert len(seen) == 3
    asyncio.run(main())


# --- Announcements (HA notify entities, HA TTS) ---

def test_notify_entity_per_channel_and_spoken_message(w):
    client = FakeMqtt()
    w.host.mqtt.bind(client, None)
    w.bus.publish("sonorium.started", {"version": "t"})
    session = w.t.session
    topic = f"homeassistant/notify/sonorium_{session.id}_announce/config"
    assert wait_for(lambda: any(p[0] == topic for p in client.published))
    config = json.loads(next(p[1] for p in client.published if p[0] == topic))
    assert config["command_topic"] == f"sonorium/announce/{session.id}/set"
    assert config["name"].endswith("Announce") and config["device"]["identifiers"] == ["sonorium_device"]
    assert f"sonorium/announce/+/set" in client.subscribed

    # Home Assistant's notify.send_message publishes the message to the command topic
    w.call(client.handler, f"sonorium/announce/{session.id}/set", "Dinner is ready")
    assert wait_for(lambda: w.overlay.played)
    played = w.overlay.played[-1]
    assert played["channels"] == [session.id] and played["source"] == b"ID3-fake-mp3"
    assert played["override"] is True and played["start_stopped"] is True and played["duck"] is True
    tts_call = next(cl for cl in FakeHA.calls if cl[1].endswith("/api/tts_get_url"))
    assert tts_call[1] == "http://supervisor/core/api/tts_get_url"
    assert tts_call[2] == {"message": "Dinner is ready", "engine_id": "tts.piper"}
    assert ("GET", "http://supervisor/core/api/tts_proxy/abc.mp3") == next(
        (cl[0], cl[1]) for cl in FakeHA.calls if cl[0] == "GET" and "tts_proxy" in cl[1])

    # The MQTT command: a chime then speech, on every channel
    w.overlay.played.clear()
    w.call(client.handler, "sonorium/announce/set",
           json.dumps({"channels": "all", "url": "http://x/chime.mp3", "text": "Hello", "volume": 70}))
    assert wait_for(lambda: len(w.overlay.played) == 2)
    assert [p["source"] for p in w.overlay.played] == ["http://x/chime.mp3", b"ID3-fake-mp3"]
    assert all(p["volume"] == 70 for p in w.overlay.played)

    # Disabled: the entities are removed
    w.call(w.manager.disable_plugin, "announcements")
    assert (topic, "", True) in client.published


def test_announce_route(w):
    c = w.client
    r = c.post("/api/announce", json={"channels": [w.t.session.id], "text": "Doorbell"})
    assert r.status_code == 200 and r.json()["speech"]["channels"][w.t.session.id]["state"] == "playing"
    assert w.overlay.played[-1]["origin"] == "api"
    assert c.post("/api/announce", json={"channels": [w.t.session.id]}).status_code == 400
    assert c.post("/api/announce", json={"channels": 5, "text": "x"}).status_code == 400
    assert c.post("/api/announce", json={"channels": "all", "url": "ftp://x"}).status_code == 400
    info = c.get("/api/plugin-framework").json()
    assert info["api_version"] == "1.1" and "audio.sounds" in info["offered"]
    assert info["tts_providers"] == [{"id": "homeassistant", "title": "Home Assistant", "plugin_id": "announcements"}]


def test_alerts_route():
    m.alerts.report("Something broke", "test", log=False)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(SimpleNamespace(), SimpleNamespace()))
    with TestClient(app) as client:
        alerts = client.get("/api/alerts").json()["alerts"]
        mine = next(a for a in alerts if a["message"] == "Something broke")
        assert client.post(f"/api/alerts/{mine['id']}/dismiss").json() == {"dismissed": True}
        assert client.post(f"/api/alerts/{mine['id']}/dismiss").status_code == 404
