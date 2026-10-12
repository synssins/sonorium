"""
The entity_prefix setting was never used (every entity id starts with
"sonorium_"), so it's gone; old state files and clients that still send it
keep working.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.state", "sonorium.core.session_manager", "sonorium.web.api_v2") as mods:
        m.__dict__.update(mods.__dict__)
        yield


def test_old_state_file_with_entity_prefix_loads(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "settings": {"entity_prefix": "custom", "default_volume": 35}}))
    store = m.state.StateStore(path)
    store.load()
    assert store.settings.default_volume == 35
    assert not hasattr(store.settings, "entity_prefix")
    store.save()
    assert "entity_prefix" not in json.loads(path.read_text())["settings"]


def test_settings_route_no_longer_offers_it_but_accepts_it(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    store = m.state.StateStore(tmp_path / "state.json")
    app = FastAPI()
    app.include_router(m.api_v2.create_api_router(
        session_manager=MagicMock(), group_manager=MagicMock(), ha_registry=MagicMock(), state_store=store))
    client = TestClient(app)
    assert "entity_prefix" not in client.get("/api/settings").json()
    r = client.put("/api/settings", json={"entity_prefix": "x", "default_volume": 40})
    assert r.status_code == 200 and r.json()["default_volume"] == 40 and "entity_prefix" not in r.json()
