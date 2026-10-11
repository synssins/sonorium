"""The theme helper for plugins (plugins/theme_api.py): new themes, adding to themes, presets, rollback."""

import json
from pathlib import Path

import pytest

from playback_helpers import addon_modules


@pytest.fixture(scope="module")
def m():
    with addon_modules("sonorium.plugins.theme_api", "sonorium.core.theme_metadata",
                       "sonorium.core.theme_presets", "sonorium.theme_files") as mods:
        yield mods


def all_values(data, name):
    """Every value of a key anywhere in a JSON structure."""
    found = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k == name:
                found.append(v)
            found += all_values(v, name)
    elif isinstance(data, list):
        for v in data:
            found += all_values(v, name)
    return found


def read(folder, name):
    return json.loads((folder / name).read_text(encoding="utf-8"))


def check_clean(m, folder):
    """The folder loads with no problems, nothing broken or left over, never "auto"."""
    metadata = m.theme_metadata.load_theme_folder(folder)
    assert metadata.problems == []
    leftovers = [p.name for p in folder.rglob("*") if ".broken" in p.name or p.name.startswith(".sonorium-")]
    assert leftovers == []
    assert read(folder, "metadata.json")["spec_version"] == 2
    assert "auto" not in all_values(read(folder, "metadata.json"), "playback_mode")
    assert "auto" not in all_values(read(folder, "presets.json"), "playback_mode")
    return metadata


def make_existing(tmp_path, m):
    folder = tmp_path / "Forest"
    (folder / "Birds").mkdir(parents=True)
    (folder / "Rain.mp3").write_bytes(b"rain")
    (folder / "Birds" / "Robin.mp3").write_bytes(b"robin")
    metadata = m.theme_metadata.ThemeMetadata(
        id="forest-id", name="Forest", ebb_duration=True, intrusions_retired=True, auto_retired=True,
        tracks={"Rain": m.theme_metadata.TrackSettings(playback_mode="continuous", volume=0.8),
                "Birds/Robin": m.theme_metadata.TrackSettings(playback_mode="sparse", presence=0.5)},
        groups={"Birds": {"volume": 0.7}},
        presets={"calm": {"name": "Calm", "is_default": True, "tracks": {"Rain": {"volume": 0.3}}}})
    assert m.theme_metadata.save_theme_folder(folder, metadata)
    return folder


def test_new_theme_with_groups_and_presets(m, tmp_path):
    api = m.theme_api
    source = tmp_path / "src"
    source.mkdir()
    (source / "door.wav").write_bytes(b"door")
    audio = tmp_path / "audio"
    audio.mkdir()

    edit = api.ThemeEdit.new(audio, "Rainy: Tavern", description="test", attribution={"source": "x"})
    fire = edit.add_file(b"fire", "Fireplace.mp3", settings={"playback_mode": "continuous", "seamless_loop": True})
    door = edit.add_file(source / "door.wav", group="Doors", settings={"playback_mode": "sparse", "presence": 0.4})
    door2 = edit.add_file(b"door2", "door.mp3", group="doors")  # same group (any case), same name
    edit.set_group("Doors", {"volume": 0.8, "mode": "intermittent"})
    calm = edit.add_preset("Quiet night", {fire: {"volume": 0.5}}, groups={"Doors": {"muted": True}})
    loud = edit.add_preset("Quiet night", {door: {"volume": 1.0}})
    result = edit.commit()

    folder = result["folder"]
    assert result["is_new"] and folder.parent == audio and folder.name == "Rainy_Tavern"
    assert (door, door2) == ("Doors/door", "Doors/door (2)")
    assert (folder / "Doors" / "door.wav").read_bytes() == b"door"
    assert (folder / "Doors" / "door (2).mp3").read_bytes() == b"door2"
    assert result["added"] == [fire, door, door2]
    assert (calm, loud) == ("quiet_night", "quiet_night_1")

    metadata = check_clean(m, folder)
    assert metadata.id == result["theme_id"] and metadata.name == "Rainy: Tavern"
    assert metadata.attribution == {"source": "x"}
    assert metadata.tracks[fire].seamless_loop is True
    assert metadata.tracks[door2].playback_mode == "continuous"
    assert metadata.groups == {"Doors": {"volume": 0.8, "mode": "intermittent"}}
    presets = metadata.presets
    assert presets[calm]["is_default"] is True and presets[loud]["is_default"] is False
    # Every track in every preset with all six fields; tracks not named are muted
    for preset in presets.values():
        assert set(preset["tracks"]) == {fire, door, door2}
        for entry in preset["tracks"].values():
            assert {"volume", "presence", "playback_mode", "seamless_loop", "exclusive", "muted"} <= set(entry)
    assert presets[calm]["tracks"][fire]["volume"] == 0.5 and presets[calm]["tracks"][fire]["muted"] is False
    assert presets[calm]["tracks"][door]["muted"] is True
    assert presets[calm]["groups"] == {"Doors": {"muted": True}}
    # A new theme needs no load-time conversion: values are kept as written
    assert metadata.tracks[door].presence == 0.4
    assert sorted(p.name for p in audio.iterdir()) == ["Rainy_Tavern"]


def test_new_theme_folder_name_is_unique(m, tmp_path):
    api = m.theme_api
    (tmp_path / "Rain").mkdir()
    edit = api.ThemeEdit.new(tmp_path, "Rain")
    edit.add_file(b"x", "a.mp3")
    assert edit.commit()["folder"].name == "Rain_2"


@pytest.mark.parametrize("call, message", [
    (lambda e: e.add_file(b"x", "a.mp3", settings={"playback_mode": "auto"}), "retired"),
    (lambda e: e.add_file(b"x", "a.mp3", settings={"volume": "loud"}), "must be a number"),
    (lambda e: e.add_file(b"x", "a.mp3", settings={"colour": 1}), "Unknown track setting"),
    (lambda e: e.add_file(b"x", "a.txt"), "not an audio file"),
    (lambda e: e.add_file(b"x", "a.mp3", group="_hidden"), "can't start"),
    (lambda e: e.add_file(Path("missing.mp3")), "File not found"),
    (lambda e: e.add_preset("P", {"Nope": {}}), "no track"),
    (lambda e: e.set_group("G", {"mode": "intrusion"}), "retired"),
])
def test_bad_requests_are_refused(m, tmp_path, call, message):
    edit = m.theme_api.ThemeEdit.new(tmp_path, "T")
    with pytest.raises(m.theme_api.ThemeEditError, match=message):
        call(edit)
    assert list(tmp_path.iterdir()) == []


def test_add_to_existing_theme_keeps_presets_sounding_the_same(m, tmp_path):
    api = m.theme_api
    folder = make_existing(tmp_path, m)
    edit = api.ThemeEdit.open(tmp_path, "forest-id")
    rain2 = edit.add_file(b"rain2", "rain.ogg", settings={"playback_mode": "presence", "presence": 0.3})
    robin2 = edit.add_file(b"robin2", "Robin.wav", group="BIRDS", settings={"playback_mode": "sparse"})
    storm = edit.add_preset("Storm", {rain2: {"volume": 1.0}})
    result = edit.commit()

    assert (rain2, robin2) == ("rain (2)", "Birds/Robin (2)")
    assert not result["is_new"] and result["folder"] == folder
    metadata = check_clean(m, folder)
    calm = metadata.presets["calm"]
    assert calm["tracks"]["Rain"] == {"volume": 0.3}  # untouched
    assert calm["tracks"][rain2]["muted"] is True and calm["tracks"][robin2]["muted"] is True
    assert calm["is_default"] is True and metadata.presets[storm]["is_default"] is False
    assert metadata.presets[storm]["tracks"]["Rain"]["muted"] is True
    assert metadata.presets[storm]["tracks"]["Birds/Robin"]["playback_mode"] == "sparse"
    assert metadata.groups == {"Birds": {"volume": 0.7}}
    assert (folder / "Birds" / "Robin (2).wav").read_bytes() == b"robin2"


def test_failed_save_rolls_back_an_existing_theme(m, tmp_path, monkeypatch):
    api = m.theme_api
    folder = make_existing(tmp_path, m)
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    edit = api.ThemeEdit.open(tmp_path, "Forest")  # by folder name
    edit.add_file(b"x", "New.mp3", group="Wind")
    edit.add_file(b"y", "Other.mp3")
    edit.add_preset("P", {})
    monkeypatch.setattr(api, "save_theme_folder", lambda folder, metadata: False)
    with pytest.raises(api.ThemeEditError):
        edit.commit()
    after = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert after == before
    assert not (folder / "Wind").exists()


def test_failed_json_write_is_restored(m, tmp_path, monkeypatch):
    api = m.theme_api
    folder = make_existing(tmp_path, m)
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    edit = api.ThemeEdit.open(tmp_path, "forest-id")
    edit.add_file(b"x", "New.mp3")
    real_write = m.theme_presets.write_json_atomic

    def write_presets_then_fail(path, data):
        if Path(path).name == "metadata.json":
            raise OSError("disk full")
        real_write(path, data)
    monkeypatch.setattr(m.theme_presets, "write_json_atomic", write_presets_then_fail)
    with pytest.raises(api.ThemeEditError):
        edit.commit()
    monkeypatch.undo()
    after = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert after == before


def test_failed_new_theme_leaves_nothing(m, tmp_path, monkeypatch):
    api = m.theme_api
    edit = api.ThemeEdit.new(tmp_path, "T")
    edit.add_file(b"x", "a.mp3", group="G")

    def no_rename(*a):
        raise OSError("busy")
    monkeypatch.setattr(api.os, "rename", no_rename)
    with pytest.raises(OSError):
        edit.commit()
    assert list(tmp_path.iterdir()) == []


def test_open_refuses_broken_or_missing_themes(m, tmp_path):
    api = m.theme_api
    folder = make_existing(tmp_path, m)
    with pytest.raises(api.ThemeEditError, match="not found"):
        api.ThemeEdit.open(tmp_path, "nope")
    (folder / "presets.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(api.ThemeEditError, match="problems"):
        api.ThemeEdit.open(tmp_path, "forest-id")


def test_find_and_list_themes_stay_inside_audio_path(m, tmp_path):
    api = m.theme_api
    audio = tmp_path / "audio"
    audio.mkdir()
    folder = make_existing(audio, m)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "metadata.json").write_text(json.dumps({"id": "out", "name": "Out"}))
    (audio / ".hidden").mkdir()
    (audio / "empty").mkdir()

    assert api.find_theme(audio, "forest-id") == folder
    assert api.find_theme(audio, "Forest") == folder
    for bad in ("out", "../outside", str(outside), "", None, ".hidden"):
        assert api.find_theme(audio, bad) is None
    assert api.list_themes(audio) == [{"id": "forest-id", "name": "Forest", "folder": "Forest",
                                       "track_count": 2, "preset_count": 1}]


def test_ids_and_names(m):
    api = m.theme_api
    assert api.preset_id_for("Quiet Night!", {}) == "quiet_night"
    assert api.preset_id_for("Quiet Night", {"quiet_night": {}, "quiet_night_1": {}}) == "quiet_night_2"
    assert api.preset_id_for("***", {}) == "preset"
    assert api.folder_name_for(' My: "Theme" ') == "My_Theme"
    assert api.folder_name_for("...") == "Theme"


def test_resolve_auto(m):
    api, TS = m.theme_api, m.theme_metadata.TrackSettings
    assert api.resolve_auto("A", TS(), None, 15.0).playback_mode == "continuous"
    assert api.resolve_auto("A", TS(presence=0.3), None, 15.0).playback_mode == "sparse"
    assert api.resolve_auto("A", TS(playback_mode="presence", presence=0.3), None, 15.0).presence == 0.3


def test_leftovers_of_an_interrupted_commit(m, tmp_path):
    api = m.theme_api
    folder = make_existing(tmp_path, m)
    staging = tmp_path / ".sonorium-new-abc123"
    staging.mkdir()
    (staging / "Rain.mp3").write_bytes(b"x")
    (staging / "metadata.json").write_text(json.dumps({"id": "half", "name": "Half"}))
    (folder / ".sonorium-partial-1.mp3").write_bytes(b"x")
    (folder / "Birds" / ".sonorium-partial-2.mp3").write_bytes(b"x")
    (tmp_path / ".other").mkdir()

    # Rescans never take a hidden folder for a theme
    themes = m.theme_metadata.ThemeMetadataManager(tmp_path).scan_themes()
    assert set(themes) == {"forest-id"}

    # At start-up they are removed; everything else stays
    removed = api.remove_stale_staging(tmp_path)
    assert len(removed) == 3
    assert not staging.exists() and (tmp_path / ".other").exists()
    assert not list(folder.rglob(".sonorium-partial-*"))
    assert (folder / "Rain.mp3").exists() and (folder / "Birds" / "Robin.mp3").exists()
    assert api.remove_stale_staging(tmp_path / "missing") == []


def test_commit_refuses_when_the_theme_changed_after_open(m, tmp_path):
    api = m.theme_api
    folder = make_existing(tmp_path, m)
    edit = api.ThemeEdit.open(tmp_path, "forest-id")
    edit.add_file(b"x", "New.mp3")
    metadata = m.theme_metadata.load_theme_folder(folder)
    metadata.description = "saved by someone else"
    assert m.theme_metadata.save_theme_folder(folder, metadata)
    before = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    with pytest.raises(api.ThemeEditError, match="changed meanwhile"):
        edit.commit()
    assert {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file()} == before
