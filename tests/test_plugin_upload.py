"""
Plugin upload (POST /api/plugins/upload, plugins/upload.py): the zip is
checked in full before the installed plugin is replaced, so a refused
re-upload keeps the installed one.
"""

import asyncio
import importlib
import io
import json
import zipfile
from unittest.mock import MagicMock

import pytest

from playback_helpers import addon_modules

MODULES = (
    "sonorium.core.state",
    "sonorium.plugins.base",
    "sonorium.plugins.loader",
    "sonorium.plugins.manager",
    "sonorium.plugins.upload",
)

SOURCE = '''
from sonorium.plugins.base import BasePlugin


class DemoPlugin(BasePlugin):
    id = "demo"
    name = "Demo"
    version = "{version}"
'''


@pytest.fixture(scope="module")
def m():
    with addon_modules(*MODULES) as mods:
        yield mods


def make_zip(files: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in files.items():
            zf.writestr(name, text)
    return buffer.getvalue()


def demo_files(version="1.0.0", prefix="demo/", manifest_version=None):
    return {
        f"{prefix}plugin.py": SOURCE.format(version=version),
        f"{prefix}manifest.json": json.dumps({"id": "demo", "name": "Demo",
                                              "version": manifest_version or version}),
    }


@pytest.fixture
def api(m, tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    api_v2 = importlib.import_module("sonorium.web.api_v2")  # inside m's modules

    store = m.state.StateStore(tmp_path / "state.json")
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    m.loader.BUILTIN_PLUGINS_DIR = None
    manager = m.manager.PluginManager(store, plugins_dir=plugins_dir, audio_path=tmp_path / "audio")
    asyncio.run(manager.initialize())
    app = FastAPI()
    app.include_router(api_v2.create_api_router(
        session_manager=MagicMock(), group_manager=MagicMock(), ha_registry=MagicMock(),
        state_store=store, plugin_manager=manager))
    client = TestClient(app)

    def upload(files, name="demo.zip"):
        return client.post("/api/plugins/upload", files={"file": (name, make_zip(files), "application/zip")})
    return MagicMock(upload=upload, plugins_dir=plugins_dir, manager=manager)


def installed_version(api):
    return json.loads((api.plugins_dir / "demo" / "manifest.json").read_text())["version"]


def test_upload_installs_then_replaces(api):
    r = api.upload(demo_files("1.0.0"))
    assert r.status_code == 200, r.text
    assert installed_version(api) == "1.0.0"
    r = api.upload(demo_files("1.1.0"))
    assert r.status_code == 200, r.text
    assert installed_version(api) == "1.1.0"
    assert sorted(p.name for p in api.plugins_dir.iterdir()) == ["demo"]  # no .updating / .old left


@pytest.mark.parametrize("files, fragment", [
    (demo_files("one"), "Invalid version format 'one'"),
    ({"demo/plugin.py": "x = 1\n", "demo/manifest.json": json.dumps({"id": "demo"})}, "'version'"),
    ({"demo/manifest.json": json.dumps({"id": "demo", "version": "2.0.0"})}, "No plugin.py"),
    ({**demo_files("2.0.0"), "demo/manifest.json": "{not json"}, "not valid JSON"),
    ({**demo_files("2.0.0"), "demo/manifest.json": "[1]"}, "JSON object"),
    ({**demo_files("2.0.0"), "../evil.py": "x"}, "Invalid path in zip"),
])
def test_refused_reupload_keeps_installed_plugin(api, files, fragment):
    assert api.upload(demo_files("1.0.0")).status_code == 200
    r = api.upload(files)
    assert r.status_code == 400, r.text
    assert fragment in r.json()["detail"]
    assert installed_version(api) == "1.0.0"
    assert (api.plugins_dir / "demo" / "plugin.py").exists()
    assert sorted(p.name for p in api.plugins_dir.iterdir()) == ["demo"]


def test_failed_swap_puts_installed_plugin_back(m, tmp_path, monkeypatch):
    target = tmp_path / "plugins" / "demo"
    target.mkdir(parents=True)
    (target / "plugin.py").write_text("old")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "plugin.py").write_text("new")
    real_replace = m.loader.os.replace

    def failing_replace(src, dst):
        if str(src).endswith(".demo.updating"):
            raise OSError("disk full")
        return real_replace(src, dst)
    monkeypatch.setattr(m.loader.os, "replace", failing_replace)
    with pytest.raises(OSError):
        m.upload.install(staged, target)
    assert (target / "plugin.py").read_text() == "old"
    assert sorted(p.name for p in target.parent.iterdir()) == ["demo"]
