"""
A Listener on the real channel routes (web/api_v2.py, web/personal_routes.py):
choosing theme, preset, sequence and playlist works without "Change theme,
preset and volume"; the channel's volume, name and announcements don't;
listings show only the channels in its scope; several browsers of one account
listen to one channel at once.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient

from playback_helpers import addon_modules  # noqa: E402
from test_sequences_playlists import build  # noqa: E402
import test_sequences_playlists as runner_tests  # noqa: E402

m = SimpleNamespace()
LISTENER = frozenset({"ch.view", "ch.play", "ch.select", "pl.run", "ev.read"})


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.plugins.events", "sonorium.core.session_manager", "sonorium.core.state",
                       "sonorium.core.playback_runner", "sonorium.core.sequences", "sonorium.core.playlists",
                       "sonorium.core.outputs", "sonorium.core.announce", "sonorium.core.spaces",
                       "sonorium.ha.registry", "sonorium.web.api_v2", "sonorium.web.personal_routes",
                       "sonorium.auth.principal") as mods:
        m.__dict__.update(mods.__dict__)
        runner_tests.m.__dict__.update(mods.__dict__)
        yield


class As:
    """The caller from X-Test-* headers: a Listener's session (instead of the auth middleware)."""

    def __init__(self, app, scoped=None):
        self.app = app
        self.scoped = scoped
        self.service = SimpleNamespace(authenticators={"users": object()})

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode(): v.decode() for k, v in scope["headers"]}
            principal = m.principal.Principal("session", perms=LISTENER, user_id="demo", role="listener",
                                              session_id=headers.get("x-test-session") or "s1",
                                              channels=self.scoped)
            scope.setdefault("state", {})["auth"] = principal
            scope["state"]["auth_service"] = self.service
            token = m.principal.CALLER.set(principal)
            try:
                return await self.app(scope, receive, send)
            finally:
                m.principal.CALLER.reset(token)
        return await self.app(scope, receive, send)


def client(tmp_path, scoped=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    t = build(tmp_path)
    t.manager.registry.get_speaker_name = lambda speaker_id: "Desk"
    registry = MagicMock()
    registry.get_hierarchy_dict.return_value = {}
    app = FastAPI()
    app.include_router(m.api_v2.create_api_router(
        session_manager=t.manager, group_manager=MagicMock(), ha_registry=registry, state_store=t.store))
    t.app = app
    t.client = TestClient(As(app, scoped))
    return t


def test_choosing_theme_and_preset_needs_no_edit_ability(tmp_path):
    t = client(tmp_path)
    sid = t.session.id
    r = t.client.put(f"/api/sessions/{sid}", json={"theme_id": "tavern"})
    assert r.status_code == 200, r.text
    assert r.json()["theme_id"] == "tavern"
    r = t.client.put(f"/api/sessions/{sid}", json={"preset_id": "busy"})
    assert r.status_code == 200 and r.json()["preset_id"] == "busy"
    # a client sending the whole channel back unchanged is fine
    volume = t.session.volume
    assert t.client.put(f"/api/sessions/{sid}", json={"preset_id": "", "volume": volume}).status_code == 200
    # what a Listener may not change
    for change in ({"volume": volume - 1}, {"custom_name": "Mine now"}, {"announcements": False}, {"take_over": True},
                   {"speaker_group_id": "g"}):
        assert t.client.put(f"/api/sessions/{sid}", json=change).status_code == 403, change
    assert t.session.volume == volume and t.session.announcements is True


def test_sequence_and_playlist_on_a_channel(tmp_path):
    t = client(tmp_path)
    sid = t.session.id
    r = t.client.post(f"/api/sessions/{sid}/sequence", json={"sequence_id": "loop"})
    assert r.status_code == 200, r.text
    assert t.session.sequence_id == "loop"
    assert t.client.delete(f"/api/sessions/{sid}/sequence").status_code == 200
    assert t.session.sequence_id is None
    # a sequence through the preset field, as the channel card picks it
    assert t.client.put(f"/api/sessions/{sid}", json={"preset_id": "seq:up_down"}).status_code == 200
    assert t.session.sequence_id == "up_down"
    runner_tests.add_playlist(t, runner_tests.ENTRIES)
    r = t.client.put(f"/api/sessions/{sid}/playlist", json={"playlist_id": "p1"})
    assert r.status_code == 200, r.text
    assert t.session.playlist_id == "p1"
    assert t.client.delete(f"/api/sessions/{sid}/playlist").status_code == 200
    assert t.session.playlist_id is None


def test_my_channel_choose_but_not_edit(tmp_path):
    t = client(tmp_path)
    r = t.client.put("/api/me/channel", json={"theme_id": "tavern", "preset_id": "busy"})
    assert r.status_code == 200, r.text
    assert (r.json()["theme_id"], r.json()["preset_id"]) == ("tavern", "busy")
    for change in ({"volume": 5}, {"custom_name": "Ours"}, {"announcements": False}):
        assert t.client.put("/api/me/channel", json=change).status_code == 403, change


def test_two_browsers_of_one_account_listen_at_once(tmp_path):
    t = client(tmp_path)
    sid = t.session.id
    first = t.client.post(f"/api/sessions/{sid}/listen", headers={"X-Test-Session": "a"})
    second = t.client.post(f"/api/sessions/{sid}/listen", headers={"X-Test-Session": "b"})
    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert first.json()["stream_path"] == second.json()["stream_path"]  # each opens it: one stream each
    assert t.manager.listener_count(sid) == 2
    assert t.client.delete(f"/api/sessions/{sid}/listen", headers={"X-Test-Session": "a"}).status_code == 200
    assert t.manager.listener_count(sid) == 1 and t.manager.is_listened(t.session)  # the other keeps listening
    # My Channel: each browser counts on its own too
    t.client.put("/api/me/channel", json={"theme_id": "storm"})
    a = t.client.post("/api/me/channel/listen", headers={"X-Test-Session": "a"}).json()
    b = t.client.post("/api/me/channel/listen", headers={"X-Test-Session": "b"}).json()
    assert (a["devices"], b["devices"]) == (1, 2)


def test_listings_show_only_its_channels(tmp_path):
    t = client(tmp_path)
    other = t.manager.create(theme_id="storm",
                             adhoc_selection=m.state.SpeakerSelection(include_speakers=["media_player.x"]))
    assert {s["id"] for s in t.client.get("/api/sessions").json()} == {t.session.id, other.id}
    t.client = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(As(t.app, [t.session.id]))
    assert [s["id"] for s in t.client.get("/api/sessions").json()] == [t.session.id]
    t.client.post(f"/api/sessions/{t.session.id}/listen")
    playing = t.client.get("/api/now-playing").json()["channels"]
    assert all(c.get("id", c.get("session_id", t.session.id)) == t.session.id for c in playing)
