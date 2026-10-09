"""Group folders: create, rename and delete groups, move tracks, upload into a group, and key migration."""

import importlib.util
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"


def _exec(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mods():
    """theme_files, theme_presets, theme_metadata and theme_groups without the app's heavy imports."""
    obs = types.ModuleType("sonorium.obs")
    obs.logger = Mock()
    core = types.ModuleType("sonorium.core")
    stubs = {"sonorium": types.ModuleType("sonorium"), "sonorium.obs": obs, "sonorium.core": core}
    with patch.dict(sys.modules, stubs):
        files = _exec("sonorium.theme_files", "theme_files.py")
        presets = _exec("sonorium.core.theme_presets", "core/theme_presets.py")
        core.theme_presets = presets
        meta = _exec("sonorium.core.theme_metadata", "core/theme_metadata.py")
        groups = _exec("sonorium.core.theme_groups", "core/theme_groups.py")
        yield SimpleNamespace(files=files, presets=presets, meta=meta, groups=groups)


def make_theme(tmp_path):
    theme = tmp_path / "Tavern"
    (theme / "Lute").mkdir(parents=True)
    for f in ("Fireplace.wav", "Mug clink.mp3", "Lute/Lute song 1.wav", "Lute/Lute song 2.wav"):
        (theme / f).write_bytes(f.encode())
    return theme


def keys(mods, theme):
    return [mods.files.track_key(theme, p) for p in mods.files.theme_audio_files(theme)]


# --- names --------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", ".", "..", ".hidden", "_drafts", "a/b", "a\\b", "a:b", "a*b",
                                 "a?b", 'a"b', "a<b", "a>b", "a|b", "x" * 61, None, 5])
def test_bad_group_names_are_refused(mods, bad):
    with pytest.raises(mods.groups.InvalidName):
        mods.groups.validate_group_name(bad)


def test_group_names_are_trimmed(mods):
    assert mods.groups.validate_group_name("  Bar chatter ") == "Bar chatter"
    assert mods.groups.validate_group_name("x" * 60) == "x" * 60


# --- create -------------------------------------------------------------------

def test_create_group_makes_an_empty_folder_listed_as_a_group(mods, tmp_path):
    theme = make_theme(tmp_path)
    (theme / "_drafts").mkdir()
    assert mods.groups.create_group(theme, " Bar chatter ") == "Bar chatter"
    assert (theme / "Bar chatter").is_dir()
    assert mods.groups.group_folder_names(theme) == ["Bar chatter", "Lute"]
    assert mods.groups.group_track_keys(theme, "Bar chatter") == []
    # Playback scanner unchanged: an empty folder is no group there
    assert [g.name for g in mods.files.group_folders(theme)] == ["Lute"]


def test_create_group_refuses_an_existing_name_in_any_case(mods, tmp_path):
    theme = make_theme(tmp_path)
    for name in ("Lute", "lute", "LUTE"):
        with pytest.raises(mods.groups.Conflict):
            mods.groups.create_group(theme, name)
    (theme / "notes").write_text("x")
    with pytest.raises(mods.groups.Conflict):
        mods.groups.create_group(theme, "Notes")


# --- rename -------------------------------------------------------------------

def test_rename_group_moves_the_folder_and_reports_new_keys(mods, tmp_path):
    theme = make_theme(tmp_path)
    change = mods.groups.rename_group(theme, "Lute", "Strings")
    assert not (theme / "Lute").exists()
    assert keys(mods, theme) == ["Fireplace", "Mug clink", "Strings/Lute song 1", "Strings/Lute song 2"]
    assert change.track_keys == {"Lute/Lute song 1": "Strings/Lute song 1", "Lute/Lute song 2": "Strings/Lute song 2"}
    assert change.group_renames == {"Lute": "Strings"}


def test_rename_group_errors(mods, tmp_path):
    theme = make_theme(tmp_path)
    (theme / "Harp").mkdir()
    with pytest.raises(mods.groups.NotFound):
        mods.groups.rename_group(theme, "Drums", "Percussion")
    with pytest.raises(mods.groups.Conflict):
        mods.groups.rename_group(theme, "Lute", "harp")
    with pytest.raises(mods.groups.InvalidName):
        mods.groups.rename_group(theme, "Lute", "_hidden")


def test_rename_group_case_only(mods, tmp_path):
    theme = make_theme(tmp_path)
    change = mods.groups.rename_group(theme, "Lute", "LUTE")
    assert mods.groups.group_folder_names(theme) == ["LUTE"]
    assert change.track_keys["Lute/Lute song 1"] == "LUTE/Lute song 1"


# --- delete -------------------------------------------------------------------

def test_delete_group_moves_tracks_up_with_suffix_on_clash_and_removes_folder(mods, tmp_path):
    theme = make_theme(tmp_path)
    (theme / "Lute song 1.mp3").write_bytes(b"top")  # same track name, other extension
    change = mods.groups.delete_group(theme, "Lute")
    assert not (theme / "Lute").exists()
    assert change.folder_removed
    assert change.track_keys == {"Lute/Lute song 1": "Lute song 1 (2)", "Lute/Lute song 2": "Lute song 2"}
    assert (theme / "Lute song 1 (2).wav").read_bytes() == b"Lute/Lute song 1.wav"
    assert (theme / "Lute song 1.mp3").read_bytes() == b"top"
    assert change.group_renames == {"Lute": None}


def test_delete_group_keeps_folder_with_other_files(mods, tmp_path):
    theme = make_theme(tmp_path)
    (theme / "Lute" / "cover.png").write_bytes(b"png")
    change = mods.groups.delete_group(theme, "Lute")
    assert not change.folder_removed
    assert change.left_behind == ["cover.png"]
    assert (theme / "Lute" / "cover.png").exists()
    assert keys(mods, theme) == ["Fireplace", "Lute song 1", "Lute song 2", "Mug clink"]


def test_delete_unknown_group(mods, tmp_path):
    with pytest.raises(mods.groups.NotFound):
        mods.groups.delete_group(make_theme(tmp_path), "Drums")


def test_failed_move_undoes_the_moves_already_done(mods, tmp_path):
    theme = make_theme(tmp_path)
    real_rename = mods.groups.os.rename
    calls = []

    def flaky(src, dst):
        calls.append(src)
        if len(calls) == 2:
            raise OSError("disk full")
        return real_rename(src, dst)

    with patch.object(mods.groups.os, "rename", side_effect=flaky):
        with pytest.raises(mods.groups.GroupError):
            mods.groups.delete_group(theme, "Lute")
    assert keys(mods, theme) == ["Fireplace", "Mug clink", "Lute/Lute song 1", "Lute/Lute song 2"]


# --- move ---------------------------------------------------------------------

def test_move_track_into_a_new_group_and_back_with_a_clash(mods, tmp_path):
    theme = make_theme(tmp_path)
    change = mods.groups.move_track(theme, "Mug clink", "Bar")
    assert change.track_keys == {"Mug clink": "Bar/Mug clink"}
    assert (theme / "Bar" / "Mug clink.mp3").exists()

    (theme / "Mug clink.wav").write_bytes(b"new")
    change = mods.groups.move_track(theme, "Bar/Mug clink", None)
    assert change.track_keys == {"Bar/Mug clink": "Mug clink (2)"}
    assert (theme / "Mug clink (2).mp3").exists()
    assert (theme / "Bar").is_dir()  # the group stays, empty


def test_move_track_between_groups_uses_existing_folder_case(mods, tmp_path):
    theme = make_theme(tmp_path)
    change = mods.groups.move_track(theme, "Fireplace", "lute")
    assert change.track_keys == {"Fireplace": "Lute/Fireplace"}
    assert mods.groups.group_folder_names(theme) == ["Lute"]


def test_move_track_to_where_it_is_changes_nothing(mods, tmp_path):
    theme = make_theme(tmp_path)
    assert mods.groups.move_track(theme, "Lute/Lute song 1", "Lute").track_keys == {}
    assert mods.groups.move_track(theme, "Fireplace", None).track_keys == {}


def test_move_track_errors(mods, tmp_path):
    theme = make_theme(tmp_path)
    with pytest.raises(mods.groups.NotFound):
        mods.groups.move_track(theme, "Nope", "Lute")
    with pytest.raises(mods.groups.NotFound):
        mods.groups.move_track(theme, "../Tavern/Fireplace", None)
    with pytest.raises(mods.groups.InvalidName):
        mods.groups.move_track(theme, "Fireplace", "../elsewhere")
    assert (theme / "Fireplace.wav").exists()


# --- upload -------------------------------------------------------------------

def test_upload_into_a_group(mods, tmp_path):
    theme = make_theme(tmp_path)
    path = mods.groups.upload_path(theme, "Harp 1.wav", "Harp")
    assert path == theme / "Harp" / "Harp 1.wav"
    assert (theme / "Harp").is_dir()
    assert mods.groups.upload_path(theme, "Rain.wav") == theme / "Rain.wav"
    assert mods.groups.upload_path(theme, "x.wav", "lute") == theme / "Lute" / "x.wav"


@pytest.mark.parametrize("name, expected", [
    ("../../etc/evil.wav", "evil.wav"), ("C:\\Users\\a\\song.mp3", "song.mp3"), ("Lute/a.wav", "a.wav"),
])
def test_upload_keeps_only_the_base_name(mods, tmp_path, name, expected):
    theme = make_theme(tmp_path)
    assert mods.groups.upload_path(theme, name) == theme / expected


@pytest.mark.parametrize("name", ["", "..", "a/..", ".hidden.wav", None])
def test_upload_refuses_bad_names(mods, tmp_path, name):
    with pytest.raises(mods.groups.InvalidName):
        mods.groups.upload_path(make_theme(tmp_path), name)


def test_upload_refuses_bad_group(mods, tmp_path):
    with pytest.raises(mods.groups.InvalidName):
        mods.groups.upload_path(make_theme(tmp_path), "a.wav", "../x")


# --- key migration ------------------------------------------------------------

def write_settings(theme):
    (theme / "metadata.json").write_text(json.dumps({
        "spec_version": 2, "id": "tavern-id", "name": "Tavern",
        "tracks": {
            "Fireplace": {"volume": 0.9},
            "Mug clink": {"volume": 0.2},
            "Lute/Lute song 1": {"volume": 0.5},
            "Lute/Lute song 2": {"presence": 0.4},
        },
        "groups": {"Lute": {"volume": 0.8, "gap_min": 60}, "Other": {"muted": True}},
    }))
    (theme / "presets.json").write_text(json.dumps({
        "presets": {
            "night": {"name": "Night", "is_default": True,
                      "tracks": {"Fireplace": {"volume": 1.0}, "Lute/Lute song 1": {"volume": 0.3},
                                 "Lute/Lute song 2": {"volume": 0.1}},
                      "groups": {"Lute": {"volume": 0.5}}},
            "day": {"name": "Day", "tracks": {"Mug clink": {"volume": 0.6}}},
        },
        "sequences": {"keep": "me"},
    }))


def apply(mods, theme, change):
    """What the API does after the files moved: migrate, save presets, save metadata."""
    metadata = mods.meta.load_theme_folder(theme)
    presets = mods.presets.load_presets(theme)
    migrated = mods.groups.migrate_keys(metadata, presets, change)
    mods.presets.save_presets(theme, migrated)
    metadata.presets = migrated
    assert mods.meta.save_theme_folder(theme, metadata)
    return (json.loads((theme / "metadata.json").read_text()),
            json.loads((theme / "presets.json").read_text()))


def test_rename_migrates_metadata_and_presets(mods, tmp_path):
    theme = make_theme(tmp_path)
    write_settings(theme)
    meta, presets = apply(mods, theme, mods.groups.rename_group(theme, "Lute", "Strings"))
    assert list(meta["tracks"]) == ["Fireplace", "Mug clink", "Strings/Lute song 1", "Strings/Lute song 2"]
    assert meta["tracks"]["Strings/Lute song 1"]["volume"] == 0.5
    assert meta["groups"] == {"Strings": {"volume": 0.8, "gap_min": 60}, "Other": {"muted": True}}
    night = presets["presets"]["night"]
    assert night["tracks"] == {"Fireplace": {"volume": 1.0}, "Strings/Lute song 1": {"volume": 0.3},
                               "Strings/Lute song 2": {"volume": 0.1}}
    assert night["groups"] == {"Strings": {"volume": 0.5}}
    assert presets["sequences"] == {"keep": "me"}
    # The migrated keys match the tracks on disk
    assert set(keys(mods, theme)) <= set(meta["tracks"])


def test_delete_migrates_metadata_and_presets(mods, tmp_path):
    theme = make_theme(tmp_path)
    write_settings(theme)
    (theme / "Lute song 2.ogg").write_bytes(b"")
    meta, presets = apply(mods, theme, mods.groups.delete_group(theme, "Lute"))
    assert meta["tracks"]["Lute song 1"]["volume"] == 0.5
    assert meta["tracks"]["Lute song 2 (2)"]["presence"] == 0.4
    assert not any(k.startswith("Lute/") for k in meta["tracks"])
    assert meta["groups"] == {"Other": {"muted": True}}
    night = presets["presets"]["night"]
    assert night["tracks"] == {"Fireplace": {"volume": 1.0}, "Lute song 1": {"volume": 0.3},
                               "Lute song 2 (2)": {"volume": 0.1}}
    assert night["groups"] == {}


def test_move_migrates_metadata_and_presets(mods, tmp_path):
    theme = make_theme(tmp_path)
    write_settings(theme)
    meta, presets = apply(mods, theme, mods.groups.move_track(theme, "Mug clink", "Lute"))
    assert "Mug clink" not in meta["tracks"]
    assert meta["tracks"]["Lute/Mug clink"]["volume"] == 0.2
    assert presets["presets"]["day"]["tracks"] == {"Lute/Mug clink": {"volume": 0.6}}
    assert meta["groups"]["Lute"] == {"volume": 0.8, "gap_min": 60}  # untouched

    meta, presets = apply(mods, theme, mods.groups.move_track(theme, "Lute/Mug clink", None))
    assert meta["tracks"]["Mug clink"]["volume"] == 0.2
    assert presets["presets"]["day"]["tracks"] == {"Mug clink": {"volume": 0.6}}


def test_moved_track_wins_over_a_stale_key(mods):
    tracks = {"Rain": {"volume": 0.1}, "G/Rain": {"volume": 0.9}, "Wind": {"volume": 0.5}}
    assert mods.groups.remap_tracks(tracks, {"G/Rain": "Rain"}) == {"Rain": {"volume": 0.9}, "Wind": {"volume": 0.5}}


def test_migrate_presets_does_not_change_the_input(mods):
    presets = {"p": {"tracks": {"A/x": {"volume": 1}}, "groups": {"A": {"volume": 0.5}}}}
    out = mods.groups.migrate_presets(presets, {}, {"A": "B"})
    assert out == {"p": {"tracks": {"B/x": {"volume": 1}}, "groups": {"B": {"volume": 0.5}}}}
    assert presets == {"p": {"tracks": {"A/x": {"volume": 1}}, "groups": {"A": {"volume": 0.5}}}}
