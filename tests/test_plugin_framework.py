"""
Plugin framework: the settings schema (validation, defaults, the
on_settings_changed hook and the settings API), bundled plugins updated by
version, the theme rescan after a plugin action and the theme hooks.
"""

import asyncio
import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from playback_helpers import addon_modules

MODULES = (
    "sonorium.core.state",
    "sonorium.plugins.base",
    "sonorium.plugins.loader",
    "sonorium.plugins.manager",
    "sonorium.plugins.settings_schema",
)


@pytest.fixture(scope="module")
def m():
    with addon_modules(*MODULES) as mods:
        yield mods


SCHEMA = {
    "hash_check": {"type": "boolean", "label": "Hash check", "default": True},
    "quality": {"type": "select", "label": "Quality", "default": "high",
                "options": [{"value": "high", "label": "High"}, {"value": "low", "label": "Low"}]},
    "max_tracks": {"type": "number", "label": "Max", "min": 1, "max": 8, "default": 8},
    "api_url": {"type": "url", "label": "API"},
    "note": {"type": "string", "label": "Note", "required": True, "default": "hi"},
}

PLUGIN_SOURCE = '''
from sonorium.plugins.base import BasePlugin


class DemoPlugin(BasePlugin):
    id = "demo"
    name = "Demo"
    version = "{version}"
    changed = []

    def get_settings_schema(self):
        return {schema}

    async def on_settings_changed(self, settings):
        DemoPlugin.changed.append(dict(settings))
'''


def write_plugin(folder: Path, version="1.0.0", schema=None, manifest=True):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "plugin.py").write_text(PLUGIN_SOURCE.format(version=version, schema=repr(schema or SCHEMA)))
    if manifest:
        (folder / "manifest.json").write_text(json.dumps({
            "id": "demo", "name": "Demo", "version": version, "plugin_class": "DemoPlugin"}))
    return folder


def manager_with(m, tmp_path, stored=None, **plugin_kwargs):
    store = m.state.StateStore(tmp_path / "state.json")
    if stored is not None:
        store.settings.plugin_settings["demo"] = stored
    plugins_dir = tmp_path / "plugins"
    write_plugin(plugins_dir / "demo", **plugin_kwargs)
    m.loader.BUILTIN_PLUGINS_DIR = None  # only the test's plugin
    manager = m.manager.PluginManager(store, plugins_dir=plugins_dir, audio_path=tmp_path / "audio")
    asyncio.run(manager.initialize())
    return manager, store


# --- settings schema ----------------------------------------------------------

def test_validate_settings_accepts_matching_values(m):
    s = m.settings_schema
    schema = s.normalize_schema(SCHEMA)
    clean = s.validate_settings(schema, {"hash_check": False, "quality": "low", "max_tracks": 3,
                                         "api_url": " https://example.com/x ", "note": "x"})
    assert clean == {"hash_check": False, "quality": "low", "max_tracks": 3,
                     "api_url": "https://example.com/x", "note": "x"}
    assert s.validate_settings(schema, {"api_url": ""}) == {"api_url": ""}


@pytest.mark.parametrize("values, fragment", [
    ({"nope": 1}, "unknown setting 'nope'"),
    ({"hash_check": "yes"}, "'hash_check' must be true or false"),
    ({"quality": "medium"}, "'quality' must be one of"),
    ({"max_tracks": True}, "'max_tracks' must be a number"),
    ({"max_tracks": 9}, "at most 8"),
    ({"max_tracks": 0}, "at least 1"),
    ({"api_url": "ftp://x"}, "http(s) URL"),
    ({"api_url": 5}, "'api_url' must be text"),
    ({"note": " "}, "'note' is required"),
])
def test_validate_settings_rejects(m, values, fragment):
    s = m.settings_schema
    with pytest.raises(s.SettingsError) as e:
        s.validate_settings(s.normalize_schema(SCHEMA), values)
    assert fragment in str(e.value)


def test_validate_lists_every_problem_and_needs_an_object(m):
    s = m.settings_schema
    with pytest.raises(s.SettingsError) as e:
        s.validate_settings(s.normalize_schema(SCHEMA), {"nope": 1, "hash_check": 2})
    assert "nope" in str(e.value) and "hash_check" in str(e.value)
    with pytest.raises(s.SettingsError):
        s.validate_settings(SCHEMA, ["x"])
    with pytest.raises(s.SettingsError, match="no settings"):
        s.validate_settings({}, {"x": 1})


def test_normalize_schema_drops_malformed_fields(m):
    s = m.settings_schema
    schema = s.normalize_schema({"ok": {"type": "string"}, "bad": {"type": "color"},
                                 "empty_select": {"type": "select", "options": []}, "x": "string"})
    assert list(schema) == ["ok"]
    assert s.normalize_schema(None) == {}


def test_effective_settings_applies_defaults_and_drops_stale_values(m):
    s = m.settings_schema
    schema = s.normalize_schema(SCHEMA)
    result = s.effective_settings(schema, {"hash_check": False, "quality": "gone", "old_setting": 1})
    assert result == {"hash_check": False, "quality": "high", "max_tracks": 8, "note": "hi"}
    # Without a schema whatever is stored is kept
    assert s.effective_settings({}, {"a": 1}) == {"a": 1}


def test_manager_loads_plugin_with_defaults(m, tmp_path):
    manager, _ = manager_with(m, tmp_path, stored={"quality": "low", "old": True})
    plugin = manager.get_plugin("demo")
    assert plugin.settings == {"hash_check": True, "quality": "low", "max_tracks": 8, "note": "hi"}
    info = plugin.to_dict()
    assert info["settings"] == plugin.settings
    assert set(info["settings_schema"]) == set(SCHEMA)


def test_manager_update_merges_saves_and_calls_hook(m, tmp_path):
    manager, store = manager_with(m, tmp_path)
    plugin = manager.get_plugin("demo")
    type(plugin).changed.clear()
    updated = asyncio.run(manager.update_plugin_settings("demo", {"max_tracks": 2}))
    assert updated["max_tracks"] == 2 and updated["quality"] == "high"
    assert plugin.settings == updated
    assert store.settings.plugin_settings["demo"] == updated
    assert type(plugin).changed == [updated]
    saved = json.loads((tmp_path / "state.json").read_text())
    assert "demo" in json.dumps(saved)

    with pytest.raises(m.settings_schema.SettingsError):
        asyncio.run(manager.update_plugin_settings("demo", {"max_tracks": "2"}))
    assert plugin.settings["max_tracks"] == 2  # nothing changed
    assert len(type(plugin).changed) == 1
    with pytest.raises(KeyError):
        asyncio.run(manager.update_plugin_settings("missing", {}))


def test_manifest_settings_schema_is_the_default(m, tmp_path):
    store = m.state.StateStore(tmp_path / "state.json")
    folder = tmp_path / "plugins" / "plain"
    folder.mkdir(parents=True)
    (folder / "plugin.py").write_text(
        "from sonorium.plugins.base import BasePlugin\n\n"
        "class Plain(BasePlugin):\n    id = 'plain'\n    name = 'Plain'\n    version = '1.0.0'\n")
    (folder / "manifest.json").write_text(json.dumps({
        "id": "plain", "version": "1.0.0", "plugin_class": "Plain",
        "settings_schema": {"flag": {"type": "boolean", "default": False, "label": "Flag"}}}))
    m.loader.BUILTIN_PLUGINS_DIR = None
    manager = m.manager.PluginManager(store, plugins_dir=tmp_path / "plugins")
    asyncio.run(manager.initialize())
    plugin = manager.get_plugin("plain")
    assert plugin.settings == {"flag": False}
    assert asyncio.run(manager.update_plugin_settings("plain", {"flag": True})) == {"flag": True}


# --- bundled plugins updated by version -----------------------------------------

def test_parse_version_orders_semver(m):
    v = m.loader.parse_version
    assert v("2.0.0") > v("1.10.0") > v("1.9.9")
    assert v("2.0.0") > v("2.0.0-beta.2") > v("2.0.0-beta.1") > v("2.0.0-alpha")
    assert v("not a version") is None and v(None) is None


@pytest.mark.parametrize("installed, bundled, replaced", [
    ("1.0.0", "2.0.0", True),
    ("2.0.0", "2.0.0", False),
    ("3.0.0", "2.0.0", False),   # e.g. a newer one from the catalog
    ("2.0.0-beta", "2.0.0", True),
])
def test_bundled_plugin_replaces_older_install(m, tmp_path, installed, bundled, replaced):
    bundled_dir = tmp_path / "bundled"
    write_plugin(bundled_dir / "demo", version=bundled)
    (bundled_dir / "demo" / "__pycache__").mkdir()
    (bundled_dir / "demo" / "__pycache__" / "plugin.cpython.pyc").write_bytes(b"x")
    plugins_dir = tmp_path / "plugins"
    write_plugin(plugins_dir / "demo", version=installed)
    (plugins_dir / "demo" / "user_file.txt").write_text("old")

    m.loader.BUILTIN_PLUGINS_DIR = bundled_dir
    m.loader.copy_builtin_plugins(plugins_dir)

    assert m.loader.plugin_version(plugins_dir / "demo") == (bundled if replaced else installed)
    assert (plugins_dir / "demo" / "user_file.txt").exists() is (not replaced)
    assert not (plugins_dir / "demo" / "__pycache__").exists()
    assert sorted(p.name for p in plugins_dir.iterdir()) == ["demo"]  # no leftovers


def test_bundled_plugin_version_from_plugin_py_and_skips(m, tmp_path):
    bundled_dir = tmp_path / "bundled"
    write_plugin(bundled_dir / "demo", version="2.0.0")
    plugins_dir = tmp_path / "plugins"
    write_plugin(plugins_dir / "demo", version="1.5.0", manifest=False)
    m.loader.BUILTIN_PLUGINS_DIR = bundled_dir
    assert m.loader.plugin_version(plugins_dir / "demo") == "1.5.0"

    m.loader.copy_builtin_plugins(plugins_dir, skip_plugins=["demo"])
    assert m.loader.plugin_version(plugins_dir / "demo") == "1.5.0"  # deleted by the user: left alone
    m.loader.copy_builtin_plugins(plugins_dir)
    assert m.loader.plugin_version(plugins_dir / "demo") == "2.0.0"


def test_removed_bundled_plugin_stays_removed(m, tmp_path):
    bundled_dir = tmp_path / "bundled"
    write_plugin(bundled_dir / "demo", version="9.0.0")
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    m.loader.BUILTIN_PLUGINS_DIR = bundled_dir

    m.loader.copy_builtin_plugins(plugins_dir, skip_plugins=["demo"])
    assert not (plugins_dir / "demo").exists()  # uninstalled by the user: not put back, even when newer


def test_bundled_update_keeps_install_when_copy_fails(m, tmp_path, monkeypatch):
    bundled_dir = tmp_path / "bundled"
    write_plugin(bundled_dir / "demo", version="2.0.0")
    plugins_dir = tmp_path / "plugins"
    write_plugin(plugins_dir / "demo", version="1.0.0")
    m.loader.BUILTIN_PLUGINS_DIR = bundled_dir

    def broken_copy(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(m.loader.shutil, "copytree", broken_copy)
    m.loader.copy_builtin_plugins(plugins_dir)
    assert m.loader.plugin_version(plugins_dir / "demo") == "1.0.0"


def test_bundled_plugins_declare_builtin_and_matching_versions(m):
    from sonorium.plugins.builtin import BUILTIN_PLUGINS_DIR
    for folder in [p for p in BUILTIN_PLUGINS_DIR.iterdir() if (p / "plugin.py").exists()]:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["builtin"] is True, folder.name
        cls = m.loader.load_plugin_class(folder, manifest)
        assert cls.version == manifest["version"], folder.name
        assert cls.id == manifest["id"] == folder.name


# --- API: plugin list, settings, actions -----------------------------------------

class FakePlugin:
    def __init__(self, result):
        self.result = result


@pytest.fixture
def api(m, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api_v2 = importlib.import_module("sonorium.web.api_v2")  # inside m's modules

    manager, store = manager_with(m, tmp_path)
    asyncio.run(manager.enable_plugin("demo"))
    rescans = []

    def on_themes_changed(delay=2.0):
        rescans.append(delay)

        async def done():
            return None
        return asyncio.get_running_loop().create_task(done())

    app = FastAPI()
    app.include_router(api_v2.create_api_router(
        session_manager=MagicMock(), group_manager=MagicMock(), ha_registry=MagicMock(),
        state_store=store, plugin_manager=manager, on_themes_changed=on_themes_changed))
    return MagicMock(client=TestClient(app), manager=manager, rescans=rescans)


def test_plugin_list_has_builtin_settings_and_schema(api):
    plugin = api.manager.get_plugin("demo")
    plugin._builtin = True
    listed = api.client.get("/api/plugins").json()
    assert listed[0]["builtin"] is True
    assert listed[0]["settings"]["quality"] == "high"
    assert listed[0]["settings_schema"]["quality"]["type"] == "select"
    one = api.client.get("/api/plugins/demo").json()
    assert one["builtin"] is True and one["settings"] == listed[0]["settings"]


def test_settings_api_validates(api):
    r = api.client.put("/api/plugins/demo/settings", json={"settings": {"quality": "low"}})
    assert r.status_code == 200, r.text
    assert r.json()["settings"]["quality"] == "low" and r.json()["settings"]["hash_check"] is True
    r = api.client.put("/api/plugins/demo/settings", json={"settings": {"quality": "ultra", "x": 1}})
    assert r.status_code == 400
    assert "unknown setting 'x'" in r.json()["detail"] and "'quality' must be one of" in r.json()["detail"]
    assert api.client.get("/api/plugins/demo/settings").json()["settings"]["quality"] == "low"
    assert api.client.put("/api/plugins/nope/settings", json={"settings": {}}).status_code == 404


def test_action_that_changed_themes_rescans_and_notifies(api, tmp_path):
    plugin = api.manager.get_plugin("demo")
    plugin.handle_action = AsyncMock(return_value={
        "success": True, "message": "ok", "refresh_themes": True,
        "data": {"is_new_theme": True, "theme_id": "abc", "theme_path": str(tmp_path / "T")}})
    plugin.on_theme_created = AsyncMock()
    r = api.client.post("/api/plugins/demo/action", json={"action": "import", "data": {}})
    assert r.status_code == 200
    assert r.json()["themes_changed"] is True
    assert api.rescans == [0.0]
    plugin.on_theme_created.assert_awaited_once_with("abc", tmp_path / "T")


def test_action_without_theme_changes_does_not_rescan(api):
    plugin = api.manager.get_plugin("demo")
    plugin.on_theme_created = AsyncMock()
    for result in ({"success": True, "message": "listed"},
                   {"success": False, "message": "failed", "refresh_themes": True}):
        plugin.handle_action = AsyncMock(return_value=result)
        body = api.client.post("/api/plugins/demo/action", json={"action": "x", "data": {}}).json()
        assert "themes_changed" not in body
    assert api.rescans == []
    plugin.on_theme_created.assert_not_awaited()


def test_theme_hook_errors_never_fail_the_operation(api, tmp_path):
    plugin = api.manager.get_plugin("demo")
    plugin.on_theme_created = AsyncMock(side_effect=RuntimeError("plugin bug"))
    plugin.handle_action = AsyncMock(return_value={
        "success": True, "refresh_themes": True,
        "data": {"is_new_theme": True, "theme_id": "abc", "theme_path": str(tmp_path)}})
    r = api.client.post("/api/plugins/demo/action", json={"action": "import", "data": {}})
    assert r.status_code == 200 and r.json()["success"] is True


def test_catalog_zips_match_the_bundled_plugins():
    import zipfile
    root = Path(__file__).resolve().parents[1]
    bundled = root / "sonorium_addon" / "sonorium" / "plugins" / "builtin"
    catalog = json.loads((root / "plugins" / "catalog.json").read_text(encoding="utf-8"))
    for entry in catalog["plugins"]:
        pid = entry["id"]
        assert entry["zip_file"] == f"{pid}_v{entry['version']}.zip"
        with zipfile.ZipFile(root / "plugins" / entry["zip_file"]) as z:
            manifest = json.loads(z.read(f"{pid}/manifest.json"))
            code = z.read(f"{pid}/plugin.py").replace(b"\r\n", b"\n")
        assert manifest["version"] == entry["version"]
        assert manifest == json.loads((bundled / pid / "manifest.json").read_text(encoding="utf-8"))
        assert code == (bundled / pid / "plugin.py").read_bytes().replace(b"\r\n", b"\n")
