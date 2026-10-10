"""REST API for sequences, playlists and running them on a channel: CRUD, validation, status codes."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient

from playback_helpers import addon_modules  # noqa: E402
from test_sequences_playlists import build  # noqa: E402  (same channel set-up)
import test_sequences_playlists as runner_tests  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.session_manager", "sonorium.core.state", "sonorium.core.playback_runner",
                       "sonorium.core.sequences", "sonorium.core.playlists", "sonorium.web.api_v2") as mods:
        m.__dict__.update(mods.__dict__)
        runner_tests.m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture
def api(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    t = build(tmp_path)
    t.manager.registry.get_speaker_name = lambda speaker_id: "Desk"  # for the speaker summary
    app = FastAPI()
    app.include_router(m.api_v2.create_api_router(
        session_manager=t.manager, group_manager=MagicMock(), ha_registry=MagicMock(), state_store=t.store))
    t.client = TestClient(app)
    return t


def presets_doc(t, theme="storm"):
    return json.loads((t.folders[theme] / "presets.json").read_text(encoding="utf-8"))


# --- Sequences ---

def test_sequence_crud(api):
    c = api.client
    r = c.get("/api/themes/tavern/sequences")
    assert r.status_code == 200
    assert r.json()["sequence_defaults"] == {"hold_min": 300, "hold_max": 900, "ramp": 30}
    assert r.json()["sequences"] == []

    r = c.post("/api/themes/tavern/sequences", json={"name": "Night falls", "presets": ["busy", "quiet"], "at_end": "stop"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"] == "night_falls" and body["owner"] is None
    assert body["hold_min"] is None and body["effective"] == {"hold_min": 300, "hold_max": 900, "ramp": 30}

    r = c.put("/api/themes/tavern/sequences/night_falls", json={"ramp": 12, "at_end": "reverse"})
    assert r.status_code == 200, r.text
    assert r.json()["ramp"] == 12 and r.json()["effective"]["ramp"] == 12 and r.json()["presets"] == ["busy", "quiet"]
    assert c.get("/api/themes/tavern/sequences/night_falls").json()["at_end"] == "reverse"

    doc = presets_doc(api, "tavern")
    assert doc["sequences"]["night_falls"]["ramp"] == 12 and "busy" in doc["presets"]  # next to the presets

    assert c.delete("/api/themes/tavern/sequences/night_falls").status_code == 200
    assert c.get("/api/themes/tavern/sequences/night_falls").status_code == 404
    assert c.delete("/api/themes/tavern/sequences/night_falls").status_code == 404


@pytest.mark.parametrize("body, detail", [
    ({"presets": ["busy"]}, "name"),
    ({"name": "X", "presets": []}, "presets"),
    ({"name": "X", "presets": ["nope"]}, "unknown preset"),
    ({"name": "X", "presets": ["busy"], "at_end": "pingpong"}, "at_end"),
    ({"name": "X", "presets": ["busy"], "hold_min": 100, "hold_max": 50}, "hold_min"),
    ({"name": "X", "presets": ["busy"], "ramp": -1}, "ramp"),
    ({"name": "X", "presets": ["busy"], "ramp": "slow"}, "ramp"),
    ({"name": "X", "presets": ["busy"], "owner": "bob"}, "owner"),
])
def test_bad_sequences_are_refused(api, body, detail):
    r = api.client.post("/api/themes/tavern/sequences", json=body)
    assert r.status_code == 400 and detail in r.json()["detail"]


def test_sequence_conflicts_and_unknown_theme(api):
    c = api.client
    assert c.post("/api/themes/storm/sequences", json={"name": "loop", "presets": ["a"]}).status_code == 409
    assert c.post("/api/themes/nope/sequences", json={"name": "X", "presets": ["a"]}).status_code == 404
    assert c.get("/api/themes/nope/sequences").status_code == 404
    assert c.put("/api/themes/storm/sequences/nope", json={"ramp": 1}).status_code == 404
    assert c.post("/api/themes/storm/sequences", content="not json").status_code == 400


def test_sequence_defaults(api):
    c = api.client
    assert c.get("/api/themes/storm/sequence_defaults").json() == {"theme_id": "storm", "hold_min": 10, "hold_max": 10, "ramp": 5}
    r = c.put("/api/themes/tavern/sequence_defaults", json={"hold_min": 60, "hold_max": 120})
    assert r.status_code == 200 and r.json()["ramp"] == 30 and r.json()["hold_max"] == 120
    assert presets_doc(api, "tavern")["sequence_defaults"] == {"hold_min": 60, "hold_max": 120, "ramp": 30}
    assert c.put("/api/themes/tavern/sequence_defaults", json={"hold_min": 500}).status_code == 400  # above hold_max
    assert c.put("/api/themes/tavern/sequence_defaults", json={"speed": 1}).status_code == 400


def test_deleting_a_running_sequence_stops_it(api):
    c = api.client
    assert c.post(f"/api/sessions/{api.session.id}/sequence", json={"sequence_id": "loop"}).status_code == 200
    r = c.delete("/api/themes/storm/sequences/loop")
    assert r.json()["stopped_on"] == [api.session.id] and api.session.sequence_id is None


# --- Playlists ---

PLAYLIST = {"name": "Evening", "entries": [{"theme_id": "tavern", "preset_id": "busy"},
                                           {"theme_id": "forest", "sequence_id": "day"},
                                           {"theme_id": "storm"}],
            "order": "shuffle", "repeat": False, "play_min": 600, "play_max": 1200}


def test_playlist_crud(api):
    c = api.client
    r = c.post("/api/playlists", json=PLAYLIST)
    assert r.status_code == 201, r.text
    playlist = r.json()
    pid = playlist["id"]
    assert playlist["entries"][2] == {"theme_id": "storm", "preset_id": None, "sequence_id": None}
    assert playlist["owner"] is None and playlist["order"] == "shuffle"
    assert [p["id"] for p in c.get("/api/playlists").json()["playlists"]] == [pid]
    r = c.put(f"/api/playlists/{pid}", json={"name": "Late", "repeat": True})
    assert r.status_code == 200 and r.json()["name"] == "Late" and r.json()["play_min"] == 600
    assert c.get(f"/api/playlists/{pid}").json()["repeat"] is True
    assert json.loads(api.store.state_file.read_text())["playlists"][pid]["name"] == "Late"
    assert c.delete(f"/api/playlists/{pid}").status_code == 200
    assert c.get(f"/api/playlists/{pid}").status_code == 404


@pytest.mark.parametrize("change, detail", [
    ({"name": ""}, "name"),
    ({"entries": []}, "entries"),
    ({"entries": [{"theme_id": "nope"}]}, "unknown theme"),
    ({"entries": [{"theme_id": "tavern", "preset_id": "nope"}]}, "unknown preset"),
    ({"entries": [{"theme_id": "tavern", "sequence_id": "nope"}]}, "unknown sequence"),
    ({"entries": [{"theme_id": "forest", "preset_id": "dawn", "sequence_id": "day"}]}, "not both"),
    ({"order": "random"}, "order"),
    ({"repeat": "yes"}, "repeat"),
    ({"play_min": 100, "play_max": 50}, "play_min"),
    ({"play_min": 0}, "play_min"),
])
def test_bad_playlists_are_refused(api, change, detail):
    r = api.client.post("/api/playlists", json={**PLAYLIST, **change})
    assert r.status_code == 400 and detail in r.json()["detail"]


def test_playlist_name_conflict(api):
    assert api.client.post("/api/playlists", json=PLAYLIST).status_code == 201
    assert api.client.post("/api/playlists", json={**PLAYLIST, "name": "evening "}).status_code == 409


# --- On a channel ---

def test_channel_sequence_playlist_and_next(api):
    c = api.client
    sid = api.session.id
    r = c.post(f"/api/sessions/{sid}/sequence", json={"sequence_id": "up_down"})
    assert r.status_code == 200, r.text
    assert r.json()["sequence"]["name"] == "Up and down" and r.json()["sequence"]["running"] is False  # not playing
    assert c.get(f"/api/sessions/{sid}").json()["sequence_id"] == "up_down"
    assert c.post(f"/api/sessions/{sid}/sequence", json={"sequence_id": "nope"}).status_code == 404
    assert c.post(f"/api/sessions/{sid}/sequence", json={}).status_code == 400
    assert c.post("/api/sessions/nope/sequence", json={"sequence_id": "loop"}).status_code == 404
    assert c.delete(f"/api/sessions/{sid}/sequence").json()["sequence"] is None

    pid = c.post("/api/playlists", json=PLAYLIST).json()["id"]
    r = c.put(f"/api/sessions/{sid}/playlist", json={"playlist_id": pid})
    assert r.status_code == 200 and r.json()["playlist"]["name"] == "Evening"
    assert r.json()["playlist"]["theme_id"] == api.session.theme_id
    assert c.put(f"/api/sessions/{sid}/playlist", json={"playlist_id": "nope"}).status_code == 404
    assert c.post(f"/api/sessions/{sid}/next").status_code == 409  # not playing

    runner_tests.run(api.manager.play(sid))
    r = c.post(f"/api/sessions/{sid}/next")
    assert r.status_code == 200 and r.json()["skipped"] == "playlist"
    status = c.get(f"/api/sessions/{sid}/playback").json()
    assert status["playlist"]["running"] and status["playlist"]["position"] == 1
    assert status["playlist"]["next_change_at"]
    session = c.get(f"/api/sessions/{sid}").json()
    assert session["playlist_id"] == pid and session["playlist"]["entries"] == 3

    assert c.delete(f"/api/sessions/{sid}/playlist").json()["playlist"] is None
    c.delete(f"/api/sessions/{sid}/sequence")
    assert c.post(f"/api/sessions/{sid}/next").status_code == 409  # nothing runs


def test_deleting_a_playlist_clears_it_from_channels(api):
    c = api.client
    pid = c.post("/api/playlists", json=PLAYLIST).json()["id"]
    c.put(f"/api/sessions/{api.session.id}/playlist", json={"playlist_id": pid})
    assert c.delete(f"/api/playlists/{pid}").json()["stopped_on"] == [api.session.id]
    assert api.session.playlist_id is None


def test_cycle_endpoints_are_gone(api):
    assert api.client.get(f"/api/sessions/{api.session.id}/cycle").status_code in (404, 405)
    assert "cycle_config" not in api.client.get(f"/api/sessions/{api.session.id}").json()

