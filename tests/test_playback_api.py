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
    assert (body["pass_min"], body["pass_max"]) == (2 * 300 + 30, 2 * 900 + 30)  # one pass: each preset once

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


def test_pass_length_in_the_listing(api):
    listed = {s["id"]: s for s in api.client.get("/api/themes/storm/sequences").json()["sequences"]}
    assert (listed["up_down"]["pass_min"], listed["up_down"]["pass_max"]) == (40, 40)  # 3 x 10 s + 2 x 5 s
    assert (listed["random_hold"]["pass_min"], listed["random_hold"]["pass_max"]) == (22, 42)


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
    entry = dict(playlist["entries"][2])
    assert entry.pop("id")  # an id of its own: plugin attributes address the entry by it
    assert entry == {"theme_id": "storm", "preset_id": None, "sequence_id": None}
    assert playlist["owner"] is None and playlist["order"] == "shuffle"
    assert [p["id"] for p in c.get("/api/playlists").json()["playlists"]] == [pid]
    assert playlist["fit_sequences"] is False  # default: a sequence entry plays one full pass
    r = c.put(f"/api/playlists/{pid}", json={"name": "Late", "repeat": True})
    assert r.status_code == 200 and r.json()["name"] == "Late" and r.json()["play_min"] == 600
    r = c.put(f"/api/playlists/{pid}", json={"fit_sequences": True})
    assert r.status_code == 200 and r.json()["fit_sequences"] is True and r.json()["name"] == "Late"
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
    ({"fit_sequences": "yes"}, "fit_sequences"),
    ({"fit_sequences": 1}, "fit_sequences"),
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

def test_channels_list_their_recent_changes_newest_first(api):
    c = api.client
    sid = api.session.id
    assert c.get(f"/api/sessions/{sid}").json()["recent"] == []
    runner_tests.run(api.manager.play(sid))
    assert c.put(f"/api/sessions/{sid}", json={"preset_id": "c"}).status_code == 200
    recent = c.get("/api/sessions").json()[0]["recent"]
    assert [e["text"] for e in recent] == ["Preset: Downpour", "Started"]
    assert all(e["at"] for e in recent)


def test_listen_here_starts_and_ends_a_browser_listener(api):
    c = api.client
    sid = api.session.id
    r = c.post(f"/api/sessions/{sid}/listen")
    assert r.status_code == 200, r.text
    assert r.json() == {"channel_id": 1, "stream_path": "/stream/channel1", "hold_seconds": 15}
    assert c.get(f"/api/sessions/{sid}").json()["is_playing"] is False  # the speakers stay off
    assert c.get(f"/api/sessions/{sid}").json()["listening"] is True
    assert c.delete(f"/api/sessions/{sid}/listen").status_code == 200
    assert c.post("/api/sessions/nope/listen").status_code == 404
    assert c.delete("/api/sessions/nope/listen").status_code == 404


def test_reported_outputs(api):
    """G2: POST/PUT/DELETE /api/sessions/{id}/outputs, and the channel lists them."""
    c = api.client
    sid = api.session.id
    r = c.post(f"/api/sessions/{sid}/outputs", json={"kind": "cast", "name": "Office display",
                                                     "address": "192.168.10.249", "receiver_id": "a944"})
    assert r.status_code == 201, r.text
    body = r.json()
    oid = body["output_id"]
    assert body["channel_id"] == 1 and body["stream_path"] == "/stream/channel1"
    assert body["stream_url"] == "http://test/stream/channel1"  # the plain stream: speakers-muted applies
    assert body["heartbeat_seconds"] == 60 and body["expire_seconds"] == 180 and body["expires_in"] == 180
    assert body["by_device_id"] is None and body["since"]
    channel = c.get(f"/api/sessions/{sid}").json()
    assert channel["outputs"][0]["output_id"] == oid and channel["outputs"][0]["name"] == "Office display"
    assert channel["is_playing"] is False and channel["listening"] is False
    assert c.get(f"/api/sessions/{sid}/outputs").json()["outputs"][0]["kind"] == "cast"
    assert c.put(f"/api/sessions/{sid}/outputs/{oid}").json()["output_id"] == oid  # heartbeat
    assert c.put(f"/api/sessions/{sid}/outputs/o_nope").status_code == 404
    assert c.delete(f"/api/sessions/{sid}/outputs/{oid}").json() == {"removed": True}
    assert c.delete(f"/api/sessions/{sid}/outputs/{oid}").status_code == 404
    assert c.get(f"/api/sessions/{sid}").json()["outputs"] == []
    assert c.post(f"/api/sessions/{sid}/outputs", json={"kind": "??"}).status_code == 400
    assert c.post(f"/api/sessions/{sid}/outputs", content="nope").status_code == 400
    assert c.post("/api/sessions/nope/outputs", json={}).status_code == 404
    assert c.get("/api/sessions/nope/outputs").status_code == 404


def test_listen_hold_seconds(api):
    """G3: {"hold_seconds": 0-300} in the body or the query."""
    c = api.client
    sid = api.session.id
    assert c.post(f"/api/sessions/{sid}/listen", json={"hold_seconds": 120}).json()["hold_seconds"] == 120
    assert c.post(f"/api/sessions/{sid}/listen?hold_seconds=200").json()["hold_seconds"] == 200
    for bad in ({"hold_seconds": 301}, {"hold_seconds": -5}, {"hold_seconds": "long"}, {"hold_seconds": True}):
        r = c.post(f"/api/sessions/{sid}/listen", json=bad)
        assert r.status_code == 400 and "hold_seconds" in r.json()["detail"], bad
    assert c.post(f"/api/sessions/{sid}/listen", content="[1]").status_code == 400


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


def test_channel_previous(api):
    c = api.client
    sid = api.session.id
    assert c.post(f"/api/sessions/{sid}/previous").status_code == 409  # not playing
    assert c.post("/api/sessions/nope/previous").status_code == 404
    pid = c.post("/api/playlists", json=PLAYLIST).json()["id"]
    c.put(f"/api/sessions/{sid}/playlist", json={"playlist_id": pid})
    runner_tests.run(api.manager.play(sid))
    c.post(f"/api/sessions/{sid}/next")
    r = c.post(f"/api/sessions/{sid}/previous")
    assert r.status_code == 200 and r.json()["skipped"] == "playlist"
    assert r.json()["playlist"]["position"] == 0

    c.delete(f"/api/sessions/{sid}/playlist")
    c.delete(f"/api/sessions/{sid}/sequence")
    c.put(f"/api/sessions/{sid}", json={"theme_id": "storm", "preset_id": "seq:loop"})
    r = c.post(f"/api/sessions/{sid}/previous")
    assert r.status_code == 200 and r.json()["skipped"] == "sequence"
    assert c.get(f"/api/sessions/{sid}").json()["preset_id"] == "c"  # restart: wraps to the last step

    c.delete(f"/api/sessions/{sid}/sequence")
    r = c.post(f"/api/sessions/{sid}/previous")
    assert r.status_code == 200 and r.json()["skipped"] == "preset"
    assert c.get(f"/api/sessions/{sid}").json()["preset_id"] == "b"


def test_picking_a_sequence_as_the_channel_preset(api):
    c = api.client
    sid = api.session.id
    r = c.put(f"/api/sessions/{sid}", json={"preset_id": "seq:up_down"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["preset_choice"], body["sequence_id"], body["preset_id"]) == ("seq:up_down", "up_down", "a")
    assert body["sequence"]["name"] == "Up and down"
    assert c.get(f"/api/sessions/{sid}/playback").json()["preset_choice"] == "seq:up_down"

    runner_tests.run(api.manager.play(sid))
    assert api.manager.runner.running_sequence(sid) is not None
    r = c.put(f"/api/sessions/{sid}", json={"preset_id": "b"})  # a normal preset stops it
    assert (r.json()["preset_choice"], r.json()["sequence_id"], r.json()["sequence"]) == ("b", None, None)
    assert api.manager.runner.running_sequence(sid) is None

    c.put(f"/api/sessions/{sid}", json={"preset_id": "seq:loop"})
    r = c.put(f"/api/sessions/{sid}", json={"theme_id": "tavern"})  # a new theme stops it
    assert r.json()["sequence_id"] is None and r.json()["preset_choice"] == "quiet"

    r = c.put(f"/api/sessions/{sid}", json={"theme_id": "forest", "preset_id": "seq:day"})
    assert (r.json()["theme_id"], r.json()["preset_choice"], r.json()["preset_id"]) == ("forest", "seq:day", "dawn")
    runner_tests.run(api.manager.stop(sid))  # stopping the channel stops its timer
    assert api.manager.runner.running_sequence(sid) is None

    assert c.put(f"/api/sessions/{sid}", json={"preset_id": "seq:nope"}).status_code == 404
    assert c.get(f"/api/sessions/{sid}").json()["theme_id"] == "forest"  # nothing changed


def test_a_sequence_with_no_presets_is_a_conflict(api):
    doc = presets_doc(api)
    doc["sequences"]["empty"] = {"name": "Empty", "presets": ["gone"]}
    (api.folders["storm"] / "presets.json").write_text(json.dumps(doc), encoding="utf-8")
    assert api.client.put(f"/api/sessions/{api.session.id}", json={"preset_id": "seq:empty"}).status_code == 409


def test_creating_a_channel_on_a_sequence(api):
    r = api.client.post("/api/sessions", json={"theme_id": "storm", "preset_id": "seq:loop"})
    assert r.status_code == 201, r.text
    assert (r.json()["preset_choice"], r.json()["preset_id"]) == ("seq:loop", "a")
    assert api.client.post("/api/sessions", json={"theme_id": "storm", "preset_id": "seq:nope"}).status_code == 404


def test_cycling_settings_are_gone(api):
    settings = api.client.get("/api/settings").json()
    assert "default_cycle_interval" not in settings and "default_cycle_randomize" not in settings
    r = api.client.put("/api/settings", json={"default_cycle_interval": 30, "default_volume": 40})
    assert r.status_code == 200 and r.json()["default_volume"] == 40 and "default_cycle_interval" not in r.json()
    # Older state files with the fields still load
    loaded = m.state.SonoriumSettings.from_dict({"default_cycle_interval": 30, "default_cycle_randomize": True})
    assert not hasattr(loaded, "default_cycle_interval")


def test_deleting_a_playlist_clears_it_from_channels(api):
    c = api.client
    pid = c.post("/api/playlists", json=PLAYLIST).json()["id"]
    c.put(f"/api/sessions/{api.session.id}/playlist", json={"playlist_id": pid})
    assert c.delete(f"/api/playlists/{pid}").json()["stopped_on"] == [api.session.id]
    assert api.session.playlist_id is None


def test_cycle_endpoints_are_gone(api):
    assert api.client.get(f"/api/sessions/{api.session.id}/cycle").status_code in (404, 405)
    assert "cycle_config" not in api.client.get(f"/api/sessions/{api.session.id}").json()

