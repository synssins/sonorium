"""
Theme editing as plain functions (sonorium/core/theme_edit.py): the Theme
Editor routes call them, and the same operations on a theme folder write
the same metadata.json and presets.json as the routes do.

Each route test runs the real API class on a generated theme (tests/engine_theme.py)
with the real metadata manager and theme refresh; the folder test makes the
same changes with theme_edit on a copy of the theme and compares the files.
"""

import asyncio
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")
pytest.importorskip("fastapi")

ROOT = Path(__file__).resolve().parents[1]
ADDON = str(ROOT / "sonorium_addon")
TESTS = str(ROOT / "tests")
if TESTS not in sys.path:
    sys.path.insert(0, TESTS)

from engine_theme import make_theme  # noqa: E402

TID = "engine-test-theme"
m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def addon_package():
    """The add-on's sonorium package, for these tests only."""
    saved = {k: v for k, v in sys.modules.items() if k == "sonorium" or k.startswith("sonorium.")}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, ADDON)
    try:
        import sonorium.api
        import sonorium.core.theme_edit
        import sonorium.core.theme_metadata
        from sonorium.utils import IndexList
        m.api, m.te, m.meta, m.IndexList = sonorium.api, sonorium.core.theme_edit, sonorium.core.theme_metadata, IndexList
        yield
    finally:
        sys.path.remove(ADDON)
        for k in [k for k in sys.modules if k == "sonorium" or k.startswith("sonorium.")]:
            del sys.modules[k]
        sys.modules.update(saved)


class Body:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


class Sessions:
    """What the routes call on the session manager: nothing happens, preset saves are noted."""

    def __init__(self):
        self.saved = []

    def preset_saved(self, theme_ids, preset_id):
        self.saved.append(preset_id)

    def __getattr__(self, name):
        return lambda *a, **k: None


def run(coro):
    return asyncio.run(coro)


def server(root: Path):
    device = SimpleNamespace(audio_dir=root, themes=m.IndexList(), files_by_theme={}, all_files=m.IndexList(),
                             master_volume=1.0)
    api = object.__new__(m.api.WebApp)
    api.backend = SimpleNamespace(device=device)
    api._theme_metadata_manager = m.meta.ThemeMetadataManager(root)
    api._theme_refresh_task = None
    api._session_manager = None
    api._mqtt_manager = None
    run(api.refresh_themes())
    api._session_manager = Sessions()
    return api


@pytest.fixture
def two(tmp_path):
    """The same theme twice: one edited through the routes, one with theme_edit on its folder."""
    one = make_theme(tmp_path / "server")
    other = tmp_path / "folder" / one.name
    shutil.copytree(one, other)
    return SimpleNamespace(api=server(tmp_path / "server"), served=one, folder=other)


def files(folder: Path) -> dict:
    return {name: (folder / name).read_bytes() for name in ("metadata.json", "presets.json")}


def same_files(t):
    a, b = files(t.served), files(t.folder)
    for name in a:
        assert json.loads(a[name]) == json.loads(b[name]), name
        assert a[name] == b[name], name


# --- the routes and the folder functions write the same files ---

def test_track_settings_write_the_same_files(two):
    api, te = two.api, m.te
    run(api.set_track_volume(TID, "Drone", Body({"volume": 7})))
    run(api.set_track_presence(TID, "Chirp", Body({"presence": 0.33})))
    run(api.set_track_muted(TID, "Wind", Body({"muted": True})))
    run(api.set_track_playback_mode(TID, "Chirp", Body({"playback_mode": "presence"})))
    run(api.set_track_seamless_loop(TID, "Drone", Body({"seamless_loop": True})))
    run(api.set_track_exclusive(TID, "Chirp", Body({"exclusive": True})))
    run(api.set_track_ebb(TID, "Wind", Body({"volume_low": 0.25, "pause_between": True, "pause_min": 12.345})))
    run(api.set_track_fades(TID, "Drone", Body({"fade_in": 1.234, "fade_out": ""})))

    assert te.set_track(two.folder, "Drone", volume=7)["volume"] == 1.0
    te.set_track(two.folder, "Chirp", presence=0.33)
    te.set_track(two.folder, "Wind", muted=True)
    te.set_track(two.folder, "Chirp", playback_mode="presence")
    te.set_track(two.folder, "Drone", seamless_loop=True)
    te.set_track(two.folder, "Chirp", exclusive=True)
    saved = te.set_track(two.folder, "Wind", volume_low=0.25, pause_between=True, pause_min=12.345)
    assert saved["pause_min"] == 12.3
    te.set_track(two.folder, "Drone", fade_in=1.234, fade_out="")
    same_files(two)


def test_group_settings_write_the_same_files(two):
    api, te = two.api, m.te
    run(api.update_group(TID, "Birds", Body({"volume": 0.5, "gap_min": 9, "gap_max": 2, "muted": True})))
    run(api.update_group(TID, "Birds", Body({"mode": "merry_go_round", "crossfade": 500})))
    te.set_group(two.folder, "Birds", volume=0.5, gap_min=9, gap_max=2, muted=True)
    settings = te.set_group(two.folder, "Birds", mode="merry_go_round", crossfade=500)
    assert settings == {"mode": "merry_go_round", "gap_min": 2.0, "gap_max": 9.0, "volume": 0.5, "muted": True,
                        "crossfade": 60.0}
    assert te.list_groups(two.folder)["Birds"]["tracks"] == ["Birds/Bird 1", "Birds/Bird 2", "Birds/Bird 3"]
    same_files(two)


def test_presets_write_the_same_files(two):
    api, te = two.api, m.te
    # The mix changes, then is saved as a preset, into a preset, loaded, renamed ...
    run(api.set_track_volume(TID, "Drone", Body({"volume": 0.11})))
    run(api.update_group(TID, "Birds", Body({"volume": 0.4})))
    te.set_track(two.folder, "Drone", volume=0.11)
    te.set_group(two.folder, "Birds", volume=0.4)
    assert te.current_mix(two.folder) == {"tracks": api._get_current_track_settings(TID),
                                          "groups": api._get_current_group_settings(TID)}

    created = run(api.create_preset(TID, Body({"name": "My Mix!"})))
    assert te.new_preset(two.folder, "My Mix!") == {k: created[k] for k in ("preset_id", "name", "is_default")}
    run(api.create_preset(TID, Body({})))
    assert te.new_preset(two.folder)["preset_id"] == "new_preset"
    run(api.update_preset(TID, "calm"))
    te.save_preset(two.folder, "calm")
    loaded = run(api.load_preset(TID, "quiet"))
    assert te.load_preset(two.folder, "quiet")["name"] == loaded["name"] == "Quiet"
    run(api.set_default_preset(TID, "quiet"))
    te.set_default_preset(two.folder, "quiet")
    run(api.rename_preset(TID, "quiet", Body({"name": "  Hush  "})))
    assert te.rename_preset(two.folder, "quiet", "  Hush  ") == "Hush"
    same_files(two)

    run(api.delete_preset(TID, "quiet"))  # the default: the first one left takes over
    te.delete_preset(two.folder, "quiet")
    run(api.delete_preset(TID, "busy"))  # in sequences: taken out of them
    assert te.delete_preset(two.folder, "busy") == ["walk", "bounce", "once"]
    same_files(two)
    assert te.get_preset(two.folder, "calm") == run(api.get_preset(TID, "calm"))
    listed = run(api.list_presets(TID))["presets"]
    assert te.list_presets(two.folder) == [{k: p[k] for k in ("id", "name", "is_default", "track_count")} for p in listed]


def test_moving_a_track_writes_the_same_files(two):
    api, te = two.api, m.te
    run(api.create_preset(TID, Body({"name": "Snap"})))
    te.new_preset(two.folder, "Snap")
    assert run(api.move_track_to_group(TID, "Chirp", Body({"group": "Small"}))) == {"track": "Small/Chirp"}
    assert te.move_track(two.folder, "Chirp", "Small") == "Small/Chirp"
    same_files(two)
    assert (two.folder / "Small" / "Chirp.wav").is_file()
    assert te.create_group(two.folder, "Fresh") == "Fresh"
    assert (two.folder / "Fresh").is_dir()
    assert te.move_track(two.folder, "Small/Chirp", None) == "Chirp"


def test_reset_writes_the_same_files(two):
    run(two.api.reset_theme_tracks(TID))
    m.te.reset_theme_tracks(two.folder)
    same_files(two)


# --- the folder functions on their own ---

def test_folder_functions_refuse_as_the_editor_does(tmp_path):
    te = m.te
    folder = make_theme(tmp_path)
    with pytest.raises(te.NotFound, match="Track not found"):
        te.set_track(folder, "Nope", volume=0.5)
    with pytest.raises(te.EditError, match="Invalid playback mode"):
        te.set_track(folder, "Drone", playback_mode="loud")
    with pytest.raises(te.EditError, match="Unknown setting 'loud'"):
        te.set_track(folder, "Drone", loud=1)
    with pytest.raises(te.EditError, match="pause_between must be true or false"):
        te.set_track(folder, "Wind", pause_between=1)
    with pytest.raises(te.NotFound, match="Group not found"):
        te.set_group(folder, "Nope", volume=1)
    with pytest.raises(te.EditError, match="Invalid value for 'mode'") as error:
        te.set_group(folder, "Birds", mode="intrusion")
    assert error.value.status == 400
    with pytest.raises(te.NotFound) as error:
        te.load_preset(folder, "nope")
    assert error.value.status == 404 and str(error.value) == "Preset not found"
    with pytest.raises(te.EditError, match="Name is required"):
        te.rename_preset(folder, "calm", "   ")
    assert te.track_settings(folder, "Chirp")["presence"] == 0.9
    assert te.track_settings(folder)["Drone"]["volume"] == 0.8


def test_sequences_on_a_folder(tmp_path):
    te = m.te
    from sonorium.core import sequences
    folder = make_theme(tmp_path)
    sequence_id, saved, defaults = te.create_sequence(folder, {"name": "Night", "presets": ["calm", "quiet"]})
    assert sequence_id == "night" and saved["presets"] == ["calm", "quiet"]
    assert "night" in sequences.load_sequences(folder)
    with pytest.raises(te.Conflict) as error:
        te.create_sequence(folder, {"name": " night ", "presets": ["calm"]})
    assert error.value.status == 409
    with pytest.raises(sequences.SequenceError):
        te.create_sequence(folder, {"name": "Bad", "presets": ["nope"]})
    changed, _ = te.update_sequence(folder, "night", {"at_end": "stop"})
    assert changed["at_end"] == "stop" and changed["name"] == "Night"
    te.delete_sequence(folder, "night")
    with pytest.raises(te.NotFound, match="Sequence not found"):
        te.delete_sequence(folder, "night")


# --- details, imported presets, groups, linked files, uploads (core gaps 2) ---

PLUGINS_THEME = {"wled": {"scene": "fire"}}
PLUGINS_TRACK = {"wled": {"cue": "flash"}}
PLUGINS_PRESET = {"wled": {"mood": "calm"}}


def with_plugins(folder: Path) -> Path:
    """Plugin attributes on the theme, a grouped track and a preset (they must ride along unchanged)."""
    meta = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    meta["plugins"] = PLUGINS_THEME
    meta["tracks"]["Birds/Bird 1"]["plugins"] = PLUGINS_TRACK
    (folder / "metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    presets = json.loads((folder / "presets.json").read_text(encoding="utf-8"))
    presets["presets"]["calm"]["plugins"] = PLUGINS_PRESET
    (folder / "presets.json").write_text(json.dumps(presets, indent=2), encoding="utf-8")
    return folder


@pytest.fixture
def three(tmp_path):
    """Two themes (the second is a source for linked files), served and as plain folders."""
    from engine_theme import make_second_theme
    one = with_plugins(make_theme(tmp_path / "server"))
    second = make_second_theme(tmp_path / "server")
    other, other_second = tmp_path / "folder" / one.name, tmp_path / "folder" / second.name
    shutil.copytree(one, other)
    shutil.copytree(second, other_second)
    api = server(tmp_path / "server")
    api._state_store = None
    return SimpleNamespace(api=api, served=one, folder=other, source=other_second, root=tmp_path)


def tree(folder: Path) -> dict:
    """Every file (relative path -> bytes) and folder (relative path -> None) under folder."""
    return {p.relative_to(folder).as_posix(): (p.read_bytes() if p.is_file() else None)
            for p in sorted(folder.rglob("*"))}


def repeat(ops, edited: Path, target: Path, source: Optional[Path] = None) -> None:
    """
    Do the file operations on another copy, as an app does in storage it can't
    write by path (a copy's src relative to `source`, when given).
    """
    import os
    for op in ops:
        if op["op"] == "mkdir":
            (target / op["dst"]).mkdir()
        elif op["op"] == "move":
            os.rename(target / op["src"], target / op["dst"])
        elif op["op"] == "copy":
            shutil.copyfile(Path(source or "") / op["src"], target / op["dst"])
        elif op["op"] == "write":
            shutil.copyfile(edited / op["dst"], target / op["dst"])
        elif op["op"] == "delete":
            (target / op["src"]).rmdir()
        else:
            raise AssertionError(op)


def test_details_write_the_same_files(three):
    api, te = three.api, m.te
    run(api.rename_theme(TID, Body({"name": "  Night Garden  "})))
    run(api.set_theme_categories(TID, Body({"categories": ["Nature", "Night"]})))
    saved = te.set_details(three.folder, name="  Night Garden  ", categories=["Nature", "Night"])
    assert {k: saved[k] for k in ("folder", "name", "categories")} == \
        {"folder": str(three.folder), "name": "Night Garden", "categories": ["Nature", "Night"]}
    same_files(three)
    assert json.loads((three.folder / "metadata.json").read_text(encoding="utf-8"))["plugins"] == PLUGINS_THEME
    with pytest.raises(te.EditError, match="Name is required"):
        te.set_details(three.folder, name="  ")
    with pytest.raises(te.EditError, match="short_file_threshold must be >= 0"):
        te.set_details(three.folder, short_file_threshold=-1)


def test_details_route_of_the_web_api_writes_the_same_files(three, monkeypatch):
    pytest.importorskip("httpx")
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import sonorium.paths
    import sonorium.web.api_v2 as api_v2
    monkeypatch.setattr(sonorium.paths, "audio_dir", lambda: three.root / "server")
    app = FastAPI()
    sessions = SimpleNamespace(theme_metadata_manager=three.api._theme_metadata_manager)
    app.include_router(api_v2.create_api_router(session_manager=sessions, group_manager=MagicMock(),
                                                ha_registry=MagicMock(), state_store=MagicMock()))
    client = TestClient(app)
    body ={"description": "Crickets after dark", "icon": "🌙", "short_file_threshold": 12}
    r = client.put(f"/api/themes/{TID}/metadata", json=body)
    assert r.status_code == 200, r.text
    te = m.te
    saved = te.set_details(three.folder, description="Crickets after dark", icon="🌙", short_file_threshold=12)
    assert saved["icon"] == "🌙" and r.json()["metadata"] == json.loads((three.folder / "metadata.json").read_bytes())
    same_files(three)
    client.put(f"/api/themes/{TID}/metadata", json={"icon": ""})
    assert "icon" not in te.set_metadata_fields(three.folder, {"icon": ""})
    same_files(three)
    r = client.put(f"/api/themes/{TID}/metadata", json={"short_file_threshold": -2})
    assert r.status_code == 400 and r.json()["detail"] == "short_file_threshold must be >= 0"

    # Upload: a file into a new group, through the route and with theme_edit
    run(three.api.refresh_themes())  # as the server's on_themes_changed does after a details change
    wav = (three.served / "Drone.wav").read_bytes()
    r = client.post(f"/api/themes/{TID}/upload", files={"file": ("Hum.wav", wav)}, data={"group": "Hums"})
    assert r.status_code == 200, r.text
    before = tree(three.folder)
    phone = three.root / "phone"
    shutil.copytree(three.folder, phone)
    result = te.upload(three.folder, "Hum.wav", wav, "Hums")
    assert {k: r.json()[k] for k in ("filename", "size", "group", "track")} == \
        {k: result[k] for k in ("filename", "size", "group", "track")} == \
        {"filename": "Hum.wav", "size": len(wav), "group": "Hums", "track": "Hums/Hum"}
    assert result["ops"] == [{"op": "mkdir", "src": None, "dst": "Hums"},
                             {"op": "write", "src": None, "dst": "Hums/Hum.wav"},
                             {"op": "write", "src": None, "dst": "metadata.json"}]
    same_files(three)
    assert (three.served / "Hums" / "Hum.wav").read_bytes() == (three.folder / "Hums" / "Hum.wav").read_bytes()
    repeat(result["ops"], three.folder, phone)
    assert tree(phone) == tree(three.folder) != before
    # Copied from a path; a bad type is refused as the route refuses it
    copied = te.upload(three.folder, "Hum 2.wav", three.served / "Wind.wav")
    assert copied["ops"][0] == {"op": "copy", "src": str(three.served / "Wind.wav"), "dst": "Hum 2.wav"}
    r = client.post(f"/api/themes/{TID}/upload", files={"file": ("x.txt", b"x")})
    with pytest.raises(te.EditError) as error:
        te.upload(three.folder, "x.txt", b"x")
    assert r.status_code == error.value.status == 400 and r.json()["detail"] == str(error.value)


def test_import_preset_writes_the_same_files(three):
    api, te = three.api, m.te
    text = json.dumps({"name": "Shared Mix", "tracks": {"Drone": {"volume": 0.25, "plugins": {"x": 1}},
                                                        "Nope": {"volume": 1}}})
    routed = run(api.import_preset(TID, Body({"preset_json": text})))
    result = te.import_preset(three.folder, text)
    assert routed == result == {"status": "ok", "preset_id": "shared_mix", "name": "Shared Mix", "tracks_imported": 1,
                                "unknown_tracks": ["Nope"], "warning": "1 track(s) not found in theme"}
    run(api.import_preset(TID, Body({"preset_json": json.dumps({"Wind": {"muted": True}}), "name": "Shared Mix"})))
    assert te.import_preset(three.folder, json.dumps({"Wind": {"muted": True}}), "Shared Mix")["preset_id"] == "shared_mix_1"
    same_files(three)
    presets = json.loads((three.folder / "presets.json").read_text(encoding="utf-8"))["presets"]
    assert presets["calm"]["plugins"] == PLUGINS_PRESET
    for bad, message in ((None, "No preset JSON provided"), ("{", "Invalid JSON"), ("[1]", "Preset must be a JSON object"),
                         ('{"tracks": 3}', "Tracks must be a JSON object"),
                         ('{"Nope": {}}', "No valid track settings found in preset")):
        with pytest.raises(te.EditError, match=message):
            te.import_preset(three.folder, bad)
        with pytest.raises(m.api.HTTPException) as error:
            run(api.import_preset(TID, Body({"preset_json": bad})))
        assert error.value.status_code == 400 and message in error.value.detail


def test_group_rename_and_delete_write_the_same_files_and_list_their_file_operations(three):
    api, te = three.api, m.te
    phone = three.root / "phone"
    shutil.copytree(three.folder, phone)

    assert run(api.rename_group_folder(TID, "Birds", Body({"name": "Flock"}))) == \
        {"name": "Flock", "tracks": {f"Birds/Bird {i}": f"Flock/Bird {i}" for i in (1, 2, 3)}}
    renamed = te.rename_group(three.folder, "Birds", "Flock")
    assert renamed["name"] == "Flock" and renamed["ops"][0] == {"op": "move", "src": "Birds", "dst": "Flock"}
    assert {op["dst"] for op in renamed["ops"][1:]} <= {"metadata.json", "presets.json"}
    same_files(three)
    meta = json.loads((three.folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["tracks"]["Flock/Bird 1"]["plugins"] == PLUGINS_TRACK and meta["plugins"] == PLUGINS_THEME
    repeat(renamed["ops"], three.folder, phone)
    assert tree(phone) == tree(three.folder)

    # A clash at the top level: " (2)"
    shutil.copy(three.served / "Drone.wav", three.served / "Flock" / "Drone.wav")
    shutil.copy(three.folder / "Drone.wav", three.folder / "Flock" / "Drone.wav")
    shutil.copy(three.folder / "Drone.wav", phone / "Flock" / "Drone.wav")
    run(api.refresh_themes())  # the server gives the new file its entry: the same in the other copies
    for target in (three.folder, phone):
        shutil.copy(three.served / "metadata.json", target / "metadata.json")
    routed = run(api.delete_group_folder(TID, "Flock"))
    deleted = te.delete_group(three.folder, "Flock")
    assert {k: v for k, v in deleted.items() if k != "ops"} == routed
    assert routed["tracks"]["Flock/Drone"] == "Drone (2)" and routed["folder_removed"] is True
    assert {"op": "move", "src": "Flock/Drone.wav", "dst": "Drone (2).wav"} in deleted["ops"]
    assert {"op": "delete", "src": "Flock", "dst": None} in deleted["ops"]
    same_files(three)
    meta = json.loads((three.folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["tracks"]["Bird 1"]["plugins"] == PLUGINS_TRACK
    repeat(deleted["ops"], three.folder, phone)
    assert tree(phone) == tree(three.folder)

    from sonorium.core import theme_groups
    with pytest.raises(theme_groups.NotFound) as error:
        te.rename_group(three.folder, "Nope", "X")
    assert error.value.status == 404


def test_linked_files_write_the_same_files_and_list_their_file_operations(three):
    from fastapi.responses import JSONResponse
    api, te = three.api, m.te
    phone = three.root / "phone"
    shutil.copytree(three.folder, phone)
    routed = run(api.add_links(TID, Body({"theme": "engine-second-theme", "tracks": ["Tone", "Tone"], "group": "Birds"})))
    assert isinstance(routed, JSONResponse)
    linked = te.add_links(three.folder, three.source, ["Tone", "Tone"], "Birds")
    assert json.loads(routed.body)["links"] == linked["links"] == [
        {"key": "Birds/Tone", "theme": "engine-second-theme", "track": "Tone", "added": True}]
    assert linked["ops"] == [{"op": "write", "src": None, "dst": "metadata.json"}]
    same_files(three)
    repeat(linked["ops"], three.folder, phone)
    assert tree(phone) == tree(three.folder)

    # Into another (empty) group
    run(api.create_group_folder(TID, Body({"name": "Echo"})))
    te.create_group(three.folder, "Echo")
    (phone / "Echo").mkdir()
    run(api.add_links(TID, Body({"theme": "engine-second-theme", "tracks": ["Tone"], "group": "Echo"})))
    echoed = te.add_links(three.folder, three.source, ["Tone"], "Echo")
    assert echoed["links"][0]["key"] == "Echo/Tone"
    same_files(three)
    repeat(echoed["ops"], three.folder, phone)

    assert run(api.delete_link(TID, "Birds/Tone")) == {"removed": "Birds/Tone"}
    removed = te.remove_link(three.folder, "Birds/Tone")
    assert removed["removed"] == "Birds/Tone" and removed["ops"] == [{"op": "write", "src": None, "dst": "metadata.json"}]
    same_files(three)
    repeat(removed["ops"], three.folder, phone)
    assert tree(phone) == tree(three.folder)

    # Refused as the route refuses
    for args, status, message in (((three.source, ["Tone"], "Nope"), 404, "Group not found"),
                                  ((three.folder, ["Drone"], None), 400, "A theme's own files can't be linked"),
                                  ((three.source, ["Gone"], None), 404, "Not a track of the source theme: Gone"),
                                  ((three.source, [], None), 400, "'tracks' must be a list of track keys")):
        with pytest.raises(te.EditError, match=message) as error:
            te.add_links(three.folder, *args)
        assert error.value.status == status
    with pytest.raises(te.NotFound, match="Linked track not found"):
        te.remove_link(three.folder, "Birds/Tone")


def test_copying_a_link_in_writes_the_same_files_and_lists_its_file_operations(three):
    api, te = three.api, m.te
    phone = three.root / "phone"
    shutil.copytree(three.folder, phone)
    second = three.root / "server" / three.source.name

    def link(group):
        run(api.add_links(TID, Body({"theme": "engine-second-theme", "tracks": ["Tone"], "group": group})))
        linked = te.add_links(three.folder, three.source, ["Tone"], group)
        repeat(linked["ops"], three.folder, phone)

    def copy_in(key, group):
        routed = run(api.copy_link_in(TID, key, Body({"group": group})))
        copied = te.copy_link_in(three.folder, key, three.source, group)
        assert {k: v for k, v in copied.items() if k != "ops"} == routed
        same_files(three)
        repeat(copied["ops"], three.folder, phone, three.source)
        assert tree(phone) == tree(three.folder)
        return copied

    # A linked file with plugin attributes, copied to the top level: settings and plugins follow its new key
    link("Birds")
    metadata = api._theme_metadata_manager.get_metadata_by_folder(three.served)
    metadata.tracks["Birds/Tone"].plugins = dict(PLUGINS_TRACK)
    api._theme_metadata_manager.save_metadata(metadata.id, metadata)
    for target in (three.folder, phone):
        shutil.copy(three.served / "metadata.json", target / "metadata.json")
    copied = copy_in("Birds/Tone", None)
    assert copied["track"] == "Tone"
    assert copied["ops"][0] == {"op": "copy", "src": "Tone.wav", "dst": "Tone.wav"}
    assert {op["op"] for op in copied["ops"][1:]} == {"write"}
    assert (three.served / "Tone.wav").read_bytes() == (second / "Tone.wav").read_bytes()
    meta = json.loads((three.folder / "metadata.json").read_text(encoding="utf-8"))
    assert "links" not in meta or "Birds/Tone" not in meta["links"]
    assert meta["tracks"]["Tone"]["plugins"] == PLUGINS_TRACK and "Birds/Tone" not in meta["tracks"]
    assert meta["plugins"] == PLUGINS_THEME

    # Into a group of links only whose folder is gone: the folder is made
    run(api.create_group_folder(TID, Body({"name": "Echo"})))
    te.create_group(three.folder, "Echo")
    (phone / "Echo").mkdir()
    link("Echo")
    for target in (three.served, three.folder, phone):
        (target / "Echo").rmdir()
    copied = copy_in("Echo/Tone", "Echo")
    assert copied["track"] == "Echo/Tone"
    assert copied["ops"][:2] == [{"op": "mkdir", "src": None, "dst": "Echo"},
                                 {"op": "copy", "src": "Tone.wav", "dst": "Echo/Tone.wav"}]

    # Again to the top level: the name is taken, so "Tone (2)"
    link("Birds")
    assert copy_in("Birds/Tone", None)["track"] == "Tone (2)"

    # Refused as the route refuses
    link("Birds")
    for args, error, status, message in (
            (("Drone", three.source, None), te.NotFound, 404, "Linked track not found"),
            (("Birds/Tone", three.source, "Nope"), te.NotFound, 404, "Group not found"),
            (("Birds/Tone", three.source, 5), te.NotFound, 404, "Group not found"),
            (("Birds/Tone", None, None), te.Conflict, 409, "The linked file is missing")):
        with pytest.raises(error, match=message) as raised:
            te.copy_link_in(three.folder, *args)
        assert raised.value.status == status
        if args[1] is not None:  # the server finds the source itself
            with pytest.raises(m.api.HTTPException) as refused:
                run(api.copy_link_in(TID, args[0], Body({"group": args[2]})))
            assert refused.value.status_code == status and refused.value.detail == message
    same_files(three)


def test_transition_tracks_on_a_folder_match_the_route(three):
    pytest.importorskip("httpx")
    import types
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import sonorium.web.api_v2 as api_v2
    from sonorium.core.session_manager import SessionManager
    from sonorium.engine.theme_loader import load_theme
    loaded = load_theme(three.folder)
    sessions = SimpleNamespace(get_theme=lambda theme_id: loaded.theme if theme_id == TID else None,
                               _theme_presets=lambda theme_id: dict(loaded.presets))
    sessions.preset_overrides = types.MethodType(SessionManager.preset_overrides, sessions)
    app = FastAPI()
    app.include_router(api_v2.create_api_router(session_manager=sessions, group_manager=MagicMock(),
                                                ha_registry=MagicMock(), state_store=MagicMock()))
    client = TestClient(app)
    for params in ({"preset": "busy", "prev": "calm", "next": "quiet"}, {"preset": "calm", "prev": "busy"},
                   {"preset": "busy"}):
        r = client.get(f"/api/themes/{TID}/transition_tracks", params=params)
        assert r.status_code == 200, r.text
        assert m.te.transition_tracks(three.folder, **params) == r.json()
    assert r.json()["in"] == [] and r.json()["out"] == []
    coming = m.te.transition_tracks(three.folder, "busy", "calm", "quiet")
    assert {row["key"] for row in coming["in"]} >= {"Drone", "Wind"} and coming["out"]
    r = client.get(f"/api/themes/{TID}/transition_tracks", params={"preset": "zz"})
    with pytest.raises(m.te.NotFound) as error:
        m.te.transition_tracks(three.folder, "zz")
    assert r.status_code == error.value.status == 404 and r.json()["detail"] == str(error.value) == "Preset 'zz' not found"


def test_loading_without_conversions_writes_nothing(tmp_path, monkeypatch):
    """An older theme with empty stand-ins for its audio: read, edited, nothing converted wrongly."""
    te, meta = m.te, m.meta
    folder = tmp_path / "Old"
    folder.mkdir()
    for name in ("Rain.mp3", "Bell.mp3"):
        (folder / name).write_bytes(b"")
    (folder / "metadata.json").write_text(json.dumps({
        "name": "Old", "id": "old-id",
        "tracks": {"Rain": {"presence": 0.4, "playback_mode": "auto"}, "Bell": {"volume": 0.5}},
        "presets": {"p": {"name": "P", "tracks": {"Rain": {"volume": 0.2}}}}}), encoding="utf-8")
    before = tree(folder)

    loaded = te.load(folder, convert=False)
    assert tree(folder) == before  # nothing written, no backup
    assert loaded.tracks["Rain"].playback_mode == "auto" and not loaded.auto_retired
    assert loaded.spec_version == 2 and loaded.presets["p"]["name"] == "P"

    monkeypatch.setattr(te, "convert_on_load", False)
    te.set_track(folder, "Bell", volume=0.6)
    saved = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    assert saved["tracks"]["Rain"]["playback_mode"] == "auto"  # left for the server, which has the audio
    assert saved["tracks"]["Bell"]["volume"] == 0.6 and not saved.get("auto_retired")
    assert json.loads((folder / "presets.json").read_text(encoding="utf-8"))["presets"]["p"]["name"] == "P"
    assert not list(folder.glob("*.bak"))

    # Lengths: None for an empty or unreadable file; playback still counts it as a minute
    (folder / "Junk.mp3").write_bytes(b"not audio at all")
    assert meta.track_seconds(folder / "Rain.mp3") is None and meta.track_seconds(folder / "Junk.mp3") is None
    assert meta.played_seconds(folder / "Junk.mp3") == 60.0
    shutil.copy(make_theme(tmp_path / "t") / "Drone.wav", folder / "Drone.wav")
    assert meta.track_seconds(folder / "Drone.wav") == pytest.approx(6.0, abs=0.01)
