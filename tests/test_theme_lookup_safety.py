"""
A theme id never reaches outside the audio folder: "..", ".", paths, and on
Windows drive ids ("C:", "F:..") that would re-root a path, don't match a
folder. Checked with Windows path rules on every system (PureWindowsPath),
so Linux CI covers the Windows case too.
"""
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

PACKAGE = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"
BAD_IDS = ["", ".", "..", " ", "../x", "a/b", "a\\b", "..\\..", "C:", "c:", "F:..", "F:x", "C:\\Windows",
           "C:/Windows", "\\\\server\\share", "//server/share", "/etc", "\\x", "x\x00", "x:y"]


@pytest.fixture(scope="module")
def paths():
    with addon_modules("sonorium.paths") as mods:
        yield mods.paths


@pytest.mark.parametrize("theme_id", BAD_IDS)
def test_plain_name_refuses_paths_drives_and_parents(paths, theme_id):
    assert not paths.plain_name(theme_id)


@pytest.mark.parametrize("theme_id", ["tavern", "a_rainy_day", "Sleigh Ride", "x.y", "Café 01"])
def test_plain_name_takes_folder_names(paths, theme_id):
    assert paths.plain_name(theme_id)


def test_windows_rules_are_what_the_check_guards_against():
    # On Windows `folder / "C:"` is drive C: the reason ':' and drives are refused
    assert str(PureWindowsPath("F:/audio") / "C:") == "C:"
    assert PureWindowsPath("F:..").drive == "F:"


def test_child_folder_only_finds_folders_directly_inside(paths, tmp_path, monkeypatch):
    root = tmp_path / "audio"
    (root / "tavern").mkdir(parents=True)
    (tmp_path / "secret").mkdir()
    monkeypatch.chdir(tmp_path)  # a drive-relative id would resolve against the working folder
    assert paths.child_folder(root, "tavern") == root / "tavern"
    for bad in BAD_IDS + ["../secret", "secret", "missing"]:
        assert paths.child_folder(root, bad) is None, bad


def test_child_folder_refuses_a_folder_whose_parent_is_elsewhere(paths, tmp_path, monkeypatch):
    """Even a name that passes is checked: the folder's resolved parent must be the audio folder."""
    root = tmp_path / "audio"
    (root / "tavern").mkdir(parents=True)
    monkeypatch.setattr(paths, "plain_name", lambda name: True)  # as if a drive id got through
    assert paths.child_folder(root, "..") is None
    assert paths.child_folder(root, str(tmp_path)) is None


def test_web_app_lookup_refuses_drive_ids(tmp_path, monkeypatch):
    """api.py's _find_theme_folder: a drive id ("C:", "F:..") finds nothing, a folder name still does."""
    pytest.importorskip("fastapi")
    with addon_modules("sonorium.api") as mods:
        api = mods.api
        root = tmp_path / "audio"
        (root / "tavern").mkdir(parents=True)
        web = SimpleNamespace(_theme_metadata_manager=None,
                              backend=SimpleNamespace(device=SimpleNamespace(audio_dir=root)))
        find = api.WebApp._find_theme_folder.__get__(web)
        monkeypatch.chdir(tmp_path)
        assert find("tavern") == root / "tavern"
        for bad in ("C:", "F:..", "..", "../audio", str(tmp_path)):
            found = find(bad)
            assert found is None or found.parent == root, bad


def test_link_keys_with_paths_are_dropped_on_load():
    """metadata.json's links: a key or source track with "..", a drive or a path isn't kept."""
    with addon_modules("sonorium.core.theme_metadata") as mods:
        tm = mods.theme_metadata
        ok = {"theme": "t1", "track": "Rain"}
        links = {"Rain": ok, "Weather/Rain": {"theme": "t1", "track": "Weather/Rain"},
                 "../Rain": ok, "Weather/..": ok, "../../x/y": ok, "C:/Rain": ok, "Weather\\Rain": ok,
                 "./Rain": ok, "Rain2": {"theme": "t1", "track": "../secret"}, "Rain3": {"theme": "t1", "track": "C:"},
                 "": ok, "a/b/c": ok}
        meta = tm.ThemeMetadata(id="x", name="x", links=links)
        assert set(meta.links) == {"Rain", "Weather/Rain"}
        assert tm.link_key_ok("Weather/Rain") and tm.link_key_ok("Rain")
        for bad in ("..", "../Rain", "Weather/..", "C:", "C:/x", "a\\b", "", "a/b/c", "x/\x00"):
            assert not tm.link_key_ok(bad), bad
