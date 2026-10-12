"""
The audio-output routes run a plugin's code (available, discover, open): an
ActionError it raises answers with its matching status and message, any
other exception a 502 "<type title>: <message>" (logged with its
traceback), never a bare 500.
"""
import logging
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")
pytest.importorskip("av")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient

from playback_helpers import addon_modules  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.audio_outputs", "sonorium.core.channel", "sonorium.plugins.actions",
                       "sonorium.web.plugin_framework_api") as mods:
        m.__dict__.update(mods.__dict__)
        yield


class Store:
    def __init__(self):
        self.main = SimpleNamespace(data={})
        self.main.section = lambda name, kind=dict: self.main.data.setdefault(name, kind())
        self.main.save = lambda: None


class Sessions:
    def __init__(self):
        self.session = SimpleNamespace(id="den", name="Den", is_playing=False)
        self.channel = None

    def get(self, sid):
        return self.session if sid == "den" else None

    def channel_of(self, sid):
        return self.channel

    def run_mix(self, sid):
        self.channel = m.channel.Channel(id=1)
        self.channel.run_silent()
        return self.channel

    def add_mix_hold(self, predicate):
        pass

    async def release_unused(self, sid, why):
        pass


class Broken:
    """An output type whose open() / discover() / available() raise what it is told to."""
    id = "broken"
    title = "Broken Speakers"

    def __init__(self):
        self.error = None
        self.where = "open"

    def _maybe_raise(self, where):
        if self.where == where and self.error is not None:
            raise self.error

    def available(self):
        self._maybe_raise("available")
        return True, ""

    async def discover(self):
        self._maybe_raise("discover")
        return [{"id": "d1", "name": "Device"}]

    def open(self, device_id, sample_rate, options):
        self._maybe_raise("open")
        return m.audio_outputs.CaptureSink([])


@pytest.fixture
def w():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    sessions = Sessions()
    outputs = m.audio_outputs.AudioOutputs(sessions, Store(), rate=44100)
    broken = Broken()
    outputs.register_type("brokenplugin", broken)
    host = SimpleNamespace(outputs=outputs)
    app = FastAPI()
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(SimpleNamespace(), host))
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(client=client, broken=broken, outputs=outputs, sessions=sessions)
    outputs.stop_all()
    if sessions.channel is not None:
        sessions.channel.stop()


def attach(w):
    return w.client.post("/api/sessions/den/audio-outputs", json={"type": "broken", "device": "d1"})


@pytest.mark.parametrize("code, status", [
    ("not_found", 404), ("invalid", 400), ("conflict", 409), ("unavailable", 503)])
@pytest.mark.parametrize("where", ["open", "available"])
def test_action_error_from_the_plugin_keeps_its_status(w, code, status, where):
    w.broken.where = where
    w.broken.error = m.actions.ActionError(code, "The amplifier is busy")
    r = attach(w)
    assert r.status_code == status, r.text
    assert r.json()["detail"] == "The amplifier is busy"
    assert not any(a["running"] for a in w.outputs.attachments("den"))


@pytest.mark.parametrize("where", ["open", "available"])
def test_other_exception_is_502_with_type_title(w, where, caplog):
    w.broken.where = where
    w.broken.error = RuntimeError("no such ALSA device")
    with caplog.at_level(logging.ERROR):
        r = attach(w)
    assert r.status_code == 502, r.text
    assert r.json()["detail"] == "Broken Speakers: no such ALSA device"
    assert any(rec.exc_info for rec in caplog.records if "broken" in rec.getMessage())


def test_output_error_is_still_400(w):
    w.broken.error = m.audio_outputs.OutputError("Pick a device first")
    r = attach(w)
    assert r.status_code == 400 and r.json()["detail"] == "Pick a device first"


def test_discover_errors(w):
    w.broken.where = "discover"
    w.broken.error = m.actions.ActionError("unavailable", "The hub is offline")
    r = w.client.get("/api/audio-outputs/broken/devices")
    assert r.status_code == 503 and r.json()["detail"] == "The hub is offline"
    w.broken.error = KeyError("hub")  # a LookupError from the plugin is its failure, not a 404
    r = w.client.get("/api/audio-outputs/broken/devices")
    assert r.status_code == 502 and r.json()["detail"] == "Broken Speakers: 'hub'"
    w.broken.error = None
    assert w.client.get("/api/audio-outputs/broken/devices").json() == {"devices": [{"id": "d1", "name": "Device"}]}
    assert w.client.get("/api/audio-outputs/nope/devices").status_code == 404


def test_attach_works_when_open_succeeds(w):
    r = attach(w)
    assert r.status_code == 201, r.text
    assert r.json()["running"] is True
