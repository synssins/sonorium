"""Settings > Advanced > Server name: the default, saving and reloading, the checks, and GET /api/info."""

import pytest

from playback_helpers import addon_modules


def test_default_without_a_store():
    with addon_modules("sonorium.servername", "sonorium.core.state") as mods:
        assert mods.servername.get() == "Sonorium"
        assert mods.state.SonoriumSettings().server_name == ""
        mods.servername.set_source(lambda: "")
        assert mods.servername.get() == "Sonorium"
        mods.servername.set_source(lambda: "   ")
        assert mods.servername.get() == "Sonorium"


def test_unreadable_or_odd_stored_names():
    with addon_modules("sonorium.servername") as mods:
        sn = mods.servername

        def broken():
            raise RuntimeError("settings unreadable")
        sn.set_source(broken)
        assert sn.get() == "Sonorium"
        sn.set_source(lambda: 42)
        assert sn.get() == "Sonorium"
        sn.set_source(lambda: " Den\n")  # a hand-edited state.json
        assert sn.get() == "Den"
        sn.set_source(lambda: "x" * (sn.MAX_LENGTH + 5))  # longer than today's limit: kept, never cut
        assert sn.get() == "x" * (sn.MAX_LENGTH + 5)


def test_clean():
    with addon_modules("sonorium.servername") as mods:
        sn = mods.servername
        assert sn.clean("  Living Room  ") == "Living Room"
        assert sn.clean("") == sn.clean("   ") == sn.clean(None) == ""
        assert sn.clean("é" * sn.MAX_LENGTH) == "é" * sn.MAX_LENGTH
        assert sn.clean("Küche · Haus 2") == "Küche · Haus 2"
        for bad in ("x" * (sn.MAX_LENGTH + 1), "Living\nRoom", "a\tb", "a\x00b", "a\x7fb", "a\x85b", 12, ["x"]):
            with pytest.raises(ValueError):
                sn.clean(bad)


def test_saved_and_loaded_with_the_settings(tmp_path):
    with addon_modules("sonorium.core.state", "sonorium.servername") as mods:
        store = mods.state.StateStore(tmp_path / "state.json")
        store.load()
        store.settings.server_name = "Living Room"
        store.save()
        again = mods.state.StateStore(tmp_path / "state.json")
        again.load()
        assert again.settings.server_name == "Living Room"
        mods.servername.set_source(lambda: again.settings.server_name)
        assert mods.servername.get() == "Living Room"


def _settings_api(tmp_path, mods):
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    store = mods.state.StateStore(tmp_path / "state.json")
    store.load()
    app = FastAPI()
    app.include_router(mods.api_v2.create_api_router(
        session_manager=MagicMock(), group_manager=MagicMock(), ha_registry=MagicMock(), state_store=store))
    return store, TestClient(app)


def test_api(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    with addon_modules("sonorium.web.api_v2", "sonorium.core.state", "sonorium.servername") as mods:
        store, c = _settings_api(tmp_path, mods)
        sn = mods.servername
        r = c.get("/api/settings")
        assert r.status_code == 200 and r.json()["server_name"] == ""
        assert sn.get() == "Sonorium"

        r = c.put("/api/settings", json={"server_name": "  Living Room "})
        assert r.status_code == 200, r.text
        assert r.json()["server_name"] == "Living Room" == store.settings.server_name
        assert sn.get() == "Living Room"  # the router points the name at its store
        assert c.get("/api/settings").json()["server_name"] == "Living Room"

        # saved: a new store reads it back
        again = mods.state.StateStore(tmp_path / "state.json")
        again.load()
        assert again.settings.server_name == "Living Room"

        # other fields leave it alone
        assert c.put("/api/settings", json={"default_volume": 40}).json()["server_name"] == "Living Room"

        # refused: nothing changes, not even the other fields sent with it
        for bad in ("x" * (sn.MAX_LENGTH + 1), "Living\nRoom", "tab\there", "bell\x07"):
            r = c.put("/api/settings", json={"server_name": bad, "default_volume": 10})
            assert r.status_code == 400, bad
            assert "Server name" in r.json()["detail"]
        assert c.put("/api/settings", json={"server_name": 5}).status_code in (400, 422)
        assert store.settings.server_name == "Living Room" and store.settings.default_volume == 40

        # exactly the longest name is fine
        longest = "n" * sn.MAX_LENGTH
        assert c.put("/api/settings", json={"server_name": longest}).json()["server_name"] == longest

        # empty: back to "Sonorium"
        r = c.put("/api/settings", json={"server_name": "   "})
        assert r.status_code == 200 and r.json()["server_name"] == "" and sn.get() == "Sonorium"
        sn.set_source(None)


def test_api_info_reports_it(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    pytest.importorskip("cryptography")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("SONORIUM_CONFIG_DIR", str(tmp_path))
    with addon_modules("sonorium.auth.service", "sonorium.auth.routes", "sonorium.servername",
                       "sonorium.core.state") as mods:
        store = mods.state.StateStore(tmp_path / "state.json")
        store.load()
        mods.servername.set_source(lambda: store.settings.server_name)
        service = mods.service.AuthService(tmp_path)
        app = FastAPI()
        app.include_router(mods.routes.create_core_auth_router(
            service, stream_url=lambda: "http://192.0.2.5:8008", features=lambda: {}, version="test",
            https_host_port=lambda: 8808))
        c = TestClient(app)
        assert c.get("/api/info").json()["name"] == "Sonorium"
        store.settings.server_name = "Kitchen"
        assert c.get("/api/info").json()["name"] == "Kitchen" == mods.routes.server_name()
        store.settings.server_name = ""
        assert c.get("/api/info").json()["name"] == "Sonorium"
        mods.servername.set_source(None)
