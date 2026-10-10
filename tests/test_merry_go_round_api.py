"""Group mode and crossfade through the groups API, saved in metadata.json and read back."""

import asyncio
import importlib.util
import json
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

pytest.importorskip("fastapi")  # api.py needs it; the CI runner installs only the audio stack
pytest.importorskip("numpy")  # imported here, before the module stubs below come and go
pytest.importorskip("av")

PACKAGE = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"


def _exec(name, relative):
    spec = importlib.util.spec_from_file_location(name, PACKAGE / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def m():
    """api.py and theme_metadata.py without fmtr, HA, MQTT or the web app."""
    package = types.ModuleType("sonorium")
    package.__path__ = [str(PACKAGE)]
    obs = types.ModuleType("sonorium.obs")
    obs.logger = logging.getLogger("sonorium-mgr-api-test")
    theme = types.ModuleType("sonorium.theme")
    theme.ThemeDefinition = object
    version = types.ModuleType("sonorium.version")
    version.__version__ = "test"
    logbuffer = types.ModuleType("sonorium.logbuffer")
    fmtr = types.ModuleType("fmtr")
    fmtr_tools = types.ModuleType("fmtr.tools")
    fmtr_tools.http = MagicMock()
    fmtr_tools.api = SimpleNamespace(Base=object, Endpoint=MagicMock())
    fmtr.tools = fmtr_tools
    core = types.ModuleType("sonorium.core")
    core.__path__ = [str(PACKAGE / "core")]
    stubs = {
        "sonorium": package, "sonorium.obs": obs, "sonorium.theme": theme, "sonorium.version": version,
        "sonorium.logbuffer": logbuffer, "fmtr": fmtr, "fmtr.tools": fmtr_tools, "sonorium.core": core,
    }
    with patch.dict(sys.modules, stubs):
        package.runtime = _exec("sonorium.runtime", "runtime.py")
        _exec("sonorium.theme_files", "theme_files.py")
        core.theme_presets = _exec("sonorium.core.theme_presets", "core/theme_presets.py")
        meta = _exec("sonorium.core.theme_metadata", "core/theme_metadata.py")
        api = _exec("sonorium.api_under_test", "api.py")
        yield SimpleNamespace(api=api, meta=meta)


class Body:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


def make_api(m, metadata, theme):
    api = object.__new__(m.api.ApiSonorium)
    api._theme_metadata_manager = Mock()
    api._theme_metadata_manager.save_metadata.return_value = True
    api._theme_groups = lambda theme_id: (theme, metadata, {"Bed": ["Bed/Wind A", "Bed/Wind B"], "Birds": []})
    return api


def run(coro):
    return asyncio.run(coro)


def test_mode_and_crossfade_round_trip(m):
    metadata = m.meta.ThemeMetadata(id="t1", name="Forest", groups={"Bed": {"volume": 0.5}})
    theme = SimpleNamespace(name="Forest", groups=dict(metadata.groups))
    api = make_api(m, metadata, theme)

    # No mode yet: the group is Intermittent (nothing stored, nothing returned)
    listed = run(api.list_groups("t1"))["groups"]
    assert listed[0]["name"] == "Bed" and "mode" not in listed[0]["settings"]

    result = run(api.update_group("t1", "Bed", Body({"mode": "merry_go_round", "crossfade": 12.5})))
    assert result["settings"] == {"volume": 0.5, "mode": "merry_go_round", "crossfade": 12.5}
    assert theme.groups["Bed"]["mode"] == "merry_go_round"  # playing channels follow
    api._theme_metadata_manager.save_metadata.assert_called()

    # Saved in metadata.json and read back
    reloaded = m.meta.ThemeMetadata.from_dict(json.loads(json.dumps(metadata.to_dict())))
    assert reloaded.groups["Bed"] == {"volume": 0.5, "mode": "merry_go_round", "crossfade": 12.5}
    listed = run(api.list_groups("t1"))["groups"]
    assert listed[0]["settings"]["mode"] == "merry_go_round" and listed[0]["settings"]["crossfade"] == 12.5

    # Clamped to 1-60 s; null goes back to the defaults (Intermittent, 10 s)
    assert run(api.update_group("t1", "Bed", Body({"crossfade": 500})))["settings"]["crossfade"] == 60.0
    assert run(api.update_group("t1", "Bed", Body({"crossfade": 0})))["settings"]["crossfade"] == 1.0
    settings = run(api.update_group("t1", "Bed", Body({"mode": "intermittent", "crossfade": None})))["settings"]
    assert settings == {"volume": 0.5, "mode": "intermittent"}
    settings = run(api.update_group("t1", "Bed", Body({"mode": None})))["settings"]
    assert settings == {"volume": 0.5}


@pytest.mark.parametrize("body", [{"mode": "carousel"}, {"mode": 1}, {"crossfade": "long"}, {"crossfade": True},
                                  {"crossfade": float("nan")}])
def test_bad_mode_or_crossfade_is_refused(m, body):
    from fastapi import HTTPException
    metadata = m.meta.ThemeMetadata(id="t1", name="Forest")
    api = make_api(m, metadata, SimpleNamespace(name="Forest", groups={}))
    with pytest.raises(HTTPException) as error:
        run(api.update_group("t1", "Bed", Body(body)))
    assert error.value.status_code == 400
    assert metadata.groups == {}


def test_mode_and_crossfade_stay_out_of_presets(m):
    api = object.__new__(m.api.ApiSonorium)
    theme = SimpleNamespace(groups={"Bed": {"volume": 0.5, "mode": "merry_go_round", "crossfade": 8.0, "gap_min": 30}})
    api._get_theme_by_id = lambda theme_id: (theme, None)
    assert api._get_current_group_settings("t1") == {"Bed": {"volume": 0.5}}
