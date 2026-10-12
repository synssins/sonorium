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
