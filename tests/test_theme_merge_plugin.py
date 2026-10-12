"""Theme Merge plugin (Themes 2.0): into a new theme or into a source, with groups, links and presets."""

import asyncio
import json
from pathlib import Path

import pytest

from playback_helpers import addon_modules

BUILTIN = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium" / "plugins" / "builtin"
SIX = {"volume", "presence", "playback_mode", "seamless_loop", "exclusive", "muted"}


@pytest.fixture(scope="module")
def m():
    with addon_modules("sonorium.plugins.loader", "sonorium.plugins.theme_api",
                       "sonorium.core.theme_metadata") as mods:
        folder = BUILTIN / "theme_merge"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        mods.Plugin = mods.loader.load_plugin_class(folder, manifest)
        yield mods


def save_theme(m, folder, theme_id, name, files, tracks, groups=None, links=None, presets=None):
    meta = m.theme_metadata
    for rel, content in files.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_bytes(content)
    metadata = meta.ThemeMetadata(
        id=theme_id, name=name, ebb_duration=True, intrusions_retired=True, auto_retired=True,
        tracks={k: meta.TrackSettings(**v) for k, v in tracks.items()},
        groups=groups or {}, links=links or {}, presets=presets or {})
    assert meta.save_theme_folder(folder, metadata)


@pytest.fixture
def audio(m, tmp_path):
    audio = tmp_path / "audio"
    save_theme(m, audio / "Weather", "weather-id", "Weather", {"Thunder.mp3": b"thunder"},
               {"Thunder": {"playback_mode": "sparse", "presence": 0.2}})
    save_theme(m, audio / "Tavern", "tavern-id", "Tavern",
               {"Fireplace.mp3": b"fire", "Rain.mp3": b"tavern rain",
                "Lute/Song 1.mp3": b"song1", "Lute/Song 2.mp3": b"song2"},
               {"Fireplace": {"playback_mode": "continuous", "volume": 0.9},
                "Rain": {"playback_mode": "continuous", "volume": 0.4, "seamless_loop": True},
                "Lute/Song 1": {"playback_mode": "sparse", "presence": 0.5},
                "Lute/Song 2": {"playback_mode": "sparse", "presence": 0.6},
                "Thunder": {"playback_mode": "sparse", "presence": 0.3, "muted": True}},
               groups={"Lute": {"mode": "merry_go_round", "crossfade": 8}},
               links={"Thunder": {"theme": "weather-id", "track": "Thunder"}},
               presets={"calm": {"name": "Calm", "is_default": True,
                                 "tracks": {"Fireplace": {"volume": 0.5}, "Thunder": {"muted": False}},
                                 "groups": {"Lute": {"volume": 0.3}}}})
    save_theme(m, audio / "Forest", "forest-id", "Forest",
               {"Rain.mp3": b"forest rain", "Birds/Robin.mp3": b"robin"},
               {"Rain": {"playback_mode": "continuous", "volume": 1.0},
                "Birds/Robin": {"playback_mode": "sparse", "presence": 0.7},
                "Music/Song 1": {"playback_mode": "sparse", "presence": 0.5}},
               groups={"Birds": {"volume": 0.5, "gap_min": 10, "gap_max": 20}},
               links={"Music/Song 1": {"theme": "tavern-id", "track": "Lute/Song 1"}},
               presets={"calm": {"name": "Calm", "is_default": True,
                                 "tracks": {"Rain": {"volume": 0.2}, "Birds/Robin": {"muted": False},
                                            "Music/Song 1": {"volume": 0.1}},
                                 "groups": {"Birds": {"muted": False}}},
                        "dawn": {"name": "Dawn", "is_default": False,
                                 "tracks": {"Birds/Robin": {"volume": 1.0, "playback_mode": "auto"}}}})
    return audio


def plugin(m, audio):
    p = m.Plugin(plugin_dir=BUILTIN / "theme_merge", settings={}, audio_path=audio)
    p.enabled = True
    return p


def merge(m, audio, **data):
    return asyncio.run(plugin(m, audio).handle_action("merge", data))


def snapshot(folder):
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def playback_modes(data):
    found = []
    if isinstance(data, dict):
        for k, v in data.items():
            found += [v] if k == "playback_mode" else playback_modes(v)
    elif isinstance(data, list):
        for v in data:
            found += playback_modes(v)
    return found


def load_clean(m, folder, check_auto=True):
    metadata = m.theme_metadata.load_theme_folder(folder)
    assert metadata.problems == []
    assert [p.name for p in folder.rglob("*") if ".broken" in p.name or p.name.startswith(".sonorium-")] == []
    for name in ("metadata.json", "presets.json"):
        doc = json.loads((folder / name).read_text(encoding="utf-8"))
        assert not check_auto or "auto" not in playback_modes(doc)
    assert json.loads((folder / "metadata.json").read_text(encoding="utf-8"))["spec_version"] == 2
    return metadata


def test_merge_into_new_theme(m, audio):
    before = {name: snapshot(audio / name) for name in ("Tavern", "Forest")}
    result = merge(m, audio, source_theme_1="tavern-id", source_theme_2="forest-id",
                   target="__new__", new_theme_name="", handle_duplicates="skip")
    assert result["success"], result
    assert result["refresh_themes"] is True and result["data"]["is_new_theme"] is True
    folder = Path(result["data"]["theme_path"])
    assert folder.parent == audio and folder.name == "Tavern_+_Forest"

    merged = load_clean(m, folder)
    assert merged.id == result["data"]["theme_id"] not in ("tavern-id", "forest-id")
    assert merged.name == "Tavern + Forest"
    assert set(merged.tracks) == {"Fireplace", "Rain", "Lute/Song 1", "Lute/Song 2", "Thunder", "Birds/Robin"}
    assert (folder / "Rain.mp3").read_bytes() == b"tavern rain"  # duplicate skipped: the first kept
    assert (folder / "Birds" / "Robin.mp3").read_bytes() == b"robin"
    assert merged.tracks["Rain"].seamless_loop is True and merged.tracks["Birds/Robin"].presence == 0.7
    # A link to a third theme stays a link; a link to a merged theme becomes its track
    assert merged.links == {"Thunder": {"theme": "weather-id", "track": "Thunder"}}
    assert merged.groups == {"Lute": {"mode": "merry_go_round", "crossfade": 8},
                             "Birds": {"volume": 0.5, "gap_min": 10, "gap_max": 20}}

    presets = {p["name"]: p for p in merged.presets.values()}
    assert set(presets) == {"Calm", "Calm (Forest)", "Dawn"}
    assert [p["name"] for p in merged.presets.values() if p["is_default"]] == ["Calm"]
    for preset in presets.values():
        assert set(preset["tracks"]) == set(merged.tracks)
        assert all(SIX <= set(entry) for entry in preset["tracks"].values())
    tavern_calm, forest_calm = presets["Calm"], presets["Calm (Forest)"]
    assert tavern_calm["tracks"]["Fireplace"]["volume"] == 0.5
    assert tavern_calm["tracks"]["Thunder"]["muted"] is False
    assert tavern_calm["tracks"]["Birds/Robin"]["muted"] is True  # not in the source preset
    assert tavern_calm["groups"] == {"Lute": {"volume": 0.3}}
    assert forest_calm["tracks"]["Rain"]["volume"] == 0.2
    assert forest_calm["tracks"]["Lute/Song 1"]["volume"] == 0.1  # was the link "Music/Song 1"
    assert forest_calm["tracks"]["Fireplace"]["muted"] is True
    assert forest_calm["groups"] == {"Birds": {"muted": False}}
    assert presets["Dawn"]["tracks"]["Birds/Robin"]["playback_mode"] in ("sparse", "presence")  # "auto" resolved

    # The sources are untouched
    assert {name: snapshot(audio / name) for name in ("Tavern", "Forest")} == before


def test_merge_with_rename_keeps_both_files(m, audio):
    result = merge(m, audio, source_theme_1="tavern-id", source_theme_2="forest-id",
                   target="__new__", new_theme_name="Both", handle_duplicates="rename")
    assert result["success"], result
    folder = Path(result["data"]["theme_path"])
    merged = load_clean(m, folder)
    assert (folder / "Rain (2).mp3").read_bytes() == b"forest rain"
    forest_calm = next(p for p in merged.presets.values() if p["name"] == "Calm (Forest)")
    assert forest_calm["tracks"]["Rain (2)"]["volume"] == 0.2
    assert forest_calm["tracks"]["Rain"]["muted"] is True
    assert result["data"]["stats"]["tracks_renamed"] == 1


def test_merge_into_source(m, audio):
    forest_before = snapshot(audio / "Forest")
    result = merge(m, audio, source_theme_1="tavern-id", source_theme_2="forest-id",
                   target="__source1__", handle_duplicates="skip")
    assert result["success"], result
    assert result["data"]["is_new_theme"] is False and result["data"]["theme_id"] == "tavern-id"
    folder = audio / "Tavern"
    merged = load_clean(m, folder)
    assert merged.id == "tavern-id"
    assert set(merged.tracks) == {"Fireplace", "Rain", "Lute/Song 1", "Lute/Song 2", "Thunder", "Birds/Robin"}
    assert (folder / "Rain.mp3").read_bytes() == b"tavern rain"
    assert merged.groups["Birds"] == {"volume": 0.5, "gap_min": 10, "gap_max": 20}
    presets = {p["name"]: p for p in merged.presets.values()}
    # The target's preset keeps its entries and gets the new track muted
    assert presets["Calm"]["tracks"]["Fireplace"] == {"volume": 0.5}
    assert presets["Calm"]["tracks"]["Birds/Robin"]["muted"] is True
    assert presets["Calm"]["is_default"] is True
    assert presets["Calm (Forest)"]["is_default"] is False
    # Forest's link to Tavern's song is Tavern's own track now
    assert presets["Calm (Forest)"]["tracks"]["Lute/Song 1"]["volume"] == 0.1
    assert merged.links == {"Thunder": {"theme": "weather-id", "track": "Thunder"}}
    assert snapshot(audio / "Forest") == forest_before


def test_merge_into_second_source(m, audio):
    result = merge(m, audio, source_theme_1="tavern-id", source_theme_2="Forest",
                   target="__source2__", handle_duplicates="skip")
    assert result["success"], result
    # (Forest's own "Dawn" preset is the target's and keeps what it has)
    merged = load_clean(m, audio / "Forest", check_auto=False)
    added = {"Fireplace", "Lute/Song 1", "Lute/Song 2", "Thunder"}
    assert added <= set(merged.tracks)
    for preset in merged.presets.values():
        assert all(preset["tracks"][key].get("playback_mode") != "auto" for key in added)
    assert merged.groups["Lute"] == {"mode": "merry_go_round", "crossfade": 8}
    assert (audio / "Forest" / "Lute" / "Song 2.mp3").read_bytes() == b"song2"


def test_failed_merge_changes_nothing(m, audio, monkeypatch):
    before = {p.name: snapshot(p) for p in audio.iterdir()}
    monkeypatch.setattr(m.theme_api, "save_theme_folder", lambda folder, metadata: False)
    for target in ("__new__", "__source1__"):
        result = merge(m, audio, source_theme_1="tavern-id", source_theme_2="forest-id",
                       target=target, handle_duplicates="rename")
        assert result["success"] is False and result["message"].startswith("Nothing was merged")
        assert {p.name: snapshot(p) for p in audio.iterdir()} == before


@pytest.mark.parametrize("data, message", [
    ({"source_theme_1": "", "source_theme_2": "forest-id"}, "Source Theme 1"),
    ({"source_theme_1": "tavern-id", "source_theme_2": "Tavern"}, "two different"),
    ({"source_theme_1": "tavern-id", "source_theme_2": "../Forest"}, "not found"),
    ({"source_theme_1": "tavern-id", "source_theme_2": "forest-id", "target": "/tmp"}, "Invalid target"),
    ({"source_theme_1": "tavern-id", "source_theme_2": "forest-id", "handle_duplicates": "overwrite"},
     "duplicate handling"),
])
def test_bad_input_is_refused(m, audio, data, message):
    before = {p.name: snapshot(p) for p in audio.iterdir()}
    result = merge(m, audio, **data)
    assert result["success"] is False and message in result["message"]
    assert {p.name: snapshot(p) for p in audio.iterdir()} == before


def test_ui_lists_themes_by_id(m, audio):
    schema = plugin(m, audio).get_ui_schema()
    options = schema["fields"][0]["options"]
    assert [o["value"] for o in options] == ["", "forest-id", "tavern-id", "weather-id"]
