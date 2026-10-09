"""Themes 2.0: presets.json, converting 1.0 themes, and recovering broken theme files."""

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
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def mods():
    """theme_presets, theme_metadata, state and session_manager, loaded without the app's heavy imports."""
    obs = types.ModuleType("sonorium.obs")
    obs.logger = Mock()
    obs.logger.instrument.side_effect = lambda *a, **k: (lambda f: f)
    core = types.ModuleType("sonorium.core")
    stubs = {"sonorium": types.ModuleType("sonorium"), "sonorium.obs": obs, "sonorium.core": core}
    with patch.dict(sys.modules, stubs):
        _exec("sonorium.theme_files", "theme_files.py")
        presets = _exec("sonorium.core.theme_presets", "core/theme_presets.py")
        core.theme_presets = presets
        metadata = _exec("sonorium.core.theme_metadata", "core/theme_metadata.py")
        _exec("sonorium.core.state", "core/state.py")
        session = _exec("session_manager_under_test", "core/session_manager.py")
        metadata._warned_read_only.clear()
        yield SimpleNamespace(presets=presets, meta=metadata, session=session, logger=obs.logger)


PRESETS = {
    "calm": {"name": "Calm", "is_default": True, "tracks": {"Rain": {"volume": 0.3}}},
    "storm": {"name": "Storm", "is_default": False, "tracks": {"Rain": {"volume": 1.0}}},
}


def make_theme(tmp_path, name="Forest", metadata=None, presets_json=None, raw_metadata=None):
    folder = tmp_path / name
    folder.mkdir()
    (folder / "Rain.mp3").write_bytes(b"x")
    (folder / "Birds.mp3").write_bytes(b"x")
    if raw_metadata is not None:
        (folder / "metadata.json").write_text(raw_metadata, encoding="utf-8")
    elif metadata is not None:
        (folder / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    if presets_json is not None:
        text = presets_json if isinstance(presets_json, str) else json.dumps(presets_json)
        (folder / "presets.json").write_text(text, encoding="utf-8")
    return folder


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def legacy_metadata():
    return {
        "id": "theme-1", "name": "Forest",
        "tracks": {"Rain": {"volume": 0.5, "exclusive": True}, "Birds": {"volume": 1.0}},
        "presets": PRESETS,
    }


def test_converts_a_1_0_theme(mods, tmp_path):
    folder = make_theme(tmp_path, metadata=legacy_metadata())
    original = (folder / "metadata.json").read_text(encoding="utf-8")

    loaded = mods.meta.load_theme_folder(folder)

    assert loaded.id == "theme-1" and loaded.presets == PRESETS and loaded.problems == []
    assert read(folder / "presets.json") == {"presets": PRESETS}
    meta = read(folder / "metadata.json")
    assert "presets" not in meta
    assert meta["spec_version"] == 2
    assert meta["groups"] == {"Exclusive": {"legacy_exclusive": True}}
    assert meta["tracks"]["Rain"]["exclusive"] is True  # the flag stays, playback is unchanged
    assert (folder / "metadata.json.pre-presets.bak").read_text(encoding="utf-8") == original
    mods.logger.info.assert_any_call("Theme 'Forest' converted to the 2.0 format")

    # A second load changes nothing and doesn't log a conversion again
    mods.logger.info.reset_mock()
    files = {p.name: p.read_bytes() for p in folder.iterdir()}
    again = mods.meta.load_theme_folder(folder)
    assert again.presets == PRESETS and again.id == "theme-1"
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == files
    mods.logger.info.assert_not_called()


def test_backup_is_written_only_once(mods, tmp_path):
    folder = make_theme(tmp_path, metadata=legacy_metadata())
    (folder / "metadata.json.pre-presets.bak").write_text("first", encoding="utf-8")
    mods.meta.load_theme_folder(folder)
    assert (folder / "metadata.json.pre-presets.bak").read_text(encoding="utf-8") == "first"


def test_presets_json_wins_on_the_same_id(mods, tmp_path):
    newer = {"calm": {"name": "Calm (new)", "is_default": True, "tracks": {}}}
    folder = make_theme(tmp_path, metadata=legacy_metadata(),
                        presets_json={"presets": newer, "sequences": {"night": [1, 2]}})

    loaded = mods.meta.load_theme_folder(folder)

    assert loaded.presets["calm"]["name"] == "Calm (new)"
    assert loaded.presets["storm"] == PRESETS["storm"]  # only in metadata.json: added
    doc = read(folder / "presets.json")
    assert doc["sequences"] == {"night": [1, 2]}  # other keys are kept untouched
    assert doc["presets"] == loaded.presets


def test_unwritable_folder_falls_back_to_metadata_presets(mods, tmp_path, monkeypatch):
    folder = make_theme(tmp_path, metadata=legacy_metadata())
    before = (folder / "metadata.json").read_text(encoding="utf-8")

    def read_only(*args, **kwargs):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(mods.presets, "save_presets", read_only)
    loaded = mods.meta.load_theme_folder(folder)
    mods.meta.load_theme_folder(folder)

    assert loaded.presets == PRESETS
    assert (folder / "metadata.json").read_text(encoding="utf-8") == before
    assert not (folder / "presets.json").exists()
    warnings = [c for c in mods.logger.warning.call_args_list if "can't write" in str(c)]
    assert len(warnings) == 1  # once per theme
    assert mods.presets.load_presets(folder) == PRESETS


def test_broken_metadata_is_kept_and_rebuilt(mods, tmp_path):
    folder = make_theme(tmp_path, raw_metadata='{\n  "id": "theme-7",\n  "name": "Forest",,\n}',
                        presets_json={"presets": PRESETS})

    loaded = mods.meta.load_theme_folder(folder)

    broken = list(folder.glob("metadata.json.broken-*"))
    assert len(broken) == 1
    assert loaded.id == "theme-7"  # the id was still readable
    assert set(loaded.tracks) == {"Rain", "Birds"}
    assert loaded.presets == PRESETS
    assert len(loaded.problems) == 1 and "metadata.json (line 3)" in loaded.problems[0]
    assert read(folder / "metadata.json")["id"] == "theme-7"


def test_broken_presets_json_is_kept_and_emptied(mods, tmp_path):
    meta = legacy_metadata()
    del meta["presets"]
    meta["spec_version"] = 2
    folder = make_theme(tmp_path, metadata=meta, presets_json="{ not json")

    loaded = mods.meta.load_theme_folder(folder)

    assert len(list(folder.glob("presets.json.broken-*"))) == 1
    assert loaded.presets == {} and loaded.id == "theme-1"
    assert read(folder / "presets.json") == {"presets": {}}
    assert "presets.json" in loaded.problems[0]


def test_broken_presets_json_restored_from_conversion_backup(mods, tmp_path):
    folder = make_theme(tmp_path, metadata=legacy_metadata())
    mods.meta.load_theme_folder(folder)  # converts: presets.json + metadata.json.pre-presets.bak
    (folder / "presets.json").write_text("{ broken", encoding="utf-8")

    loaded = mods.meta.load_theme_folder(folder)

    assert set(loaded.presets) == set(legacy_metadata()["presets"])
    assert read(folder / "presets.json")["presets"] == legacy_metadata()["presets"]
    assert len(list(folder.glob("presets.json.broken-*"))) == 1
    assert any("restored" in p for p in loaded.problems)


def test_one_broken_theme_does_not_stop_the_others(mods, tmp_path):
    make_theme(tmp_path, name="Broken", raw_metadata="[1, 2")
    make_theme(tmp_path, name="Fine", metadata=legacy_metadata())
    themes = mods.meta.ThemeMetadataManager(tmp_path).scan_themes()
    by_name = {m.name: m for m in themes.values()}
    assert set(by_name) == {"Broken", "Forest"}
    assert by_name["Broken"].problems and not by_name["Forest"].problems


def test_manager_saves_presets_to_presets_json(mods, tmp_path):
    folder = make_theme(tmp_path, metadata=legacy_metadata())
    manager = mods.meta.ThemeMetadataManager(tmp_path)
    manager.scan_themes()
    metadata = manager.get_metadata("theme-1")
    metadata.presets["new"] = {"name": "New", "is_default": False, "tracks": {}}
    assert manager.save_metadata("theme-1", metadata)
    assert "new" in read(folder / "presets.json")["presets"]
    assert "presets" not in read(folder / "metadata.json")


def test_new_theme_gets_2_0_files(mods, tmp_path):
    folder = make_theme(tmp_path)
    loaded = mods.meta.load_theme_folder(folder)
    assert read(folder / "metadata.json")["spec_version"] == 2
    assert read(folder / "presets.json") == {"presets": {}}
    assert loaded.groups == {}


def test_export_documents_use_the_2_0_layout(mods, tmp_path):
    folder = make_theme(tmp_path, metadata=legacy_metadata())
    meta, doc = mods.meta.theme_documents_for_export(folder)
    assert "presets" not in meta and meta["spec_version"] == 2
    assert doc == {"presets": PRESETS}
    assert not (folder / "presets.json").exists()  # export writes nothing


def test_session_manager_reads_presets_json(mods, tmp_path):
    meta = legacy_metadata()
    del meta["presets"]
    meta["spec_version"] = 2
    folder = make_theme(tmp_path, metadata=meta, presets_json={"presets": PRESETS})

    manager = mods.session.SessionManager.__new__(mods.session.SessionManager)
    manager.theme_metadata_manager = None
    manager.themes = [SimpleNamespace(sonorium=SimpleNamespace(path_audio=tmp_path))]
    assert manager._theme_presets("theme-1") == PRESETS
    assert manager.preset_for_theme("theme-1") == "calm"
    assert manager.preset_for_theme("theme-1", "storm") == "storm"

    # Through the metadata manager's cache as well
    manager.theme_metadata_manager = mods.meta.ThemeMetadataManager(tmp_path)
    manager.theme_metadata_manager.scan_themes()
    assert manager._theme_presets("theme-1") == PRESETS
