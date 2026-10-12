"""
Keys and Announcements: core features with a switch in Settings > Advanced
(PUT /api/install/features). Both are on out of the box, switch at once (no
restart), and GET /api/install says so for the web UI and the app.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")

from test_api_routes import m  # noqa: E402,F401  (m: the app modules, stubbed)


class FakeAnnouncements:
    def __init__(self):
        self.calls = []

    async def set_enabled(self, on):
        self.calls.append(on)


class FakeRequest:
    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


def make_app(m):
    app = m.api.WebApp(MagicMock())
    saved = []
    app._state_store = SimpleNamespace(settings=SimpleNamespace(feature_overrides={}), save=lambda: saved.append(1))
    app._announcements = FakeAnnouncements()
    app._restart_soon = MagicMock()
    return app


def test_both_are_on_out_of_the_box_and_switch_at_once(m):
    app = make_app(m)
    from sonorium.auth import keys as core_keys
    features = asyncio.run(app.get_install())["features"]
    for name in ("keys", "announcements"):
        assert features[name] == {"available": True, "enabled": True, "overridable": True, "switchable": True,
                                  "default": True, "live": True}
    assert core_keys.enabled(app.auth)

    body = asyncio.run(app.put_install_features(FakeRequest({"keys": False, "announcements": False})))
    assert body["features"]["keys"]["enabled"] is False and body["features"]["announcements"]["enabled"] is False
    assert body["restarting"] is False and not app._restart_soon.called       # live: no restart
    assert app._state_store.settings.feature_overrides == {"keys": False, "announcements": False}
    assert not core_keys.enabled(app.auth) and app._announcements.calls == [False]

    asyncio.run(app.put_install_features(FakeRequest({"keys": True, "announcements": True})))
    assert app._state_store.settings.feature_overrides == {}                  # back to the default: nothing stored
    assert core_keys.enabled(app.auth) and app._announcements.calls == [False, True]


def test_features_that_cant_be_switched_are_refused(m):
    from fastapi import HTTPException
    app = make_app(m)
    with pytest.raises(HTTPException) as refused:
        asyncio.run(app.put_install_features(FakeRequest({"space_editing": False})))
    assert refused.value.status_code == 400
