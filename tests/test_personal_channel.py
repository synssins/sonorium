"""
The personal channel (web/personal_routes.py, SessionManager's personal
channels): one hidden, local-only channel per account; devices counted one by
one; hidden from everything that lists channels; its events only for its
account. Also the live events for playlists, speaker groups, floors and areas
and settings (G5).
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient

from playback_helpers import addon_modules  # noqa: E402
from test_sequences_playlists import build, run  # noqa: E402
import test_sequences_playlists as runner_tests  # noqa: E402

m = SimpleNamespace()


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


@pytest.fixture
def bus():
    new = m.events.EventBus()
    old = m.events.set_bus(new)
    yield new
    m.events.set_bus(old)


def published(bus, prefix=""):
    return [(e.type, e.data, e.audience) for e in list(bus._history) if e.type.startswith(prefix)]


# --- The session manager ---------------------------------------------------------------

def test_made_on_first_use_one_per_account_and_hidden(tmp_path):
    t = build(tmp_path)
    a = t.manager.personal_channel("user:a")
    assert a.owner == "user:a" and t.manager.personal_channel("user:a") is a
    b = t.manager.personal_channel("user:b")
    assert b.id != a.id
    assert t.manager.personal_channel("user:c", create=False) is None
    # never listed or addressed as a channel; not where HA/MQTT entities and Status look
    assert [s.id for s in t.manager.list()] == [t.session.id]
    assert t.manager.get(a.id) is None and a.id not in t.store.sessions
    assert t.manager.get_any(a.id) is a and t.manager.is_personal(a.id)


def test_persists_apart_from_the_channels(tmp_path):
    t = build(tmp_path)
    a = t.manager.personal_channel("user:a")
    t.manager.update(a.id, theme_id="tavern", volume=33)
    t.manager.set_announcements(a.id, False)
    saved = json.loads(t.store.state_file.read_text())
    assert a.id not in saved["sessions"] and saved["personal_channels"][a.id]["owner"] == "user:a"
    again = m.state.StateStore(t.store.state_file)
    again.load()
    loaded = again.personal_sessions[a.id]
    assert (loaded.theme_id, loaded.volume, loaded.announcements, loaded.owner) == ("tavern", 33, False, "user:a")
    assert a.id not in again.sessions


def test_refuses_speakers(tmp_path):
    t = build(tmp_path)
    a = t.manager.personal_channel("user:a")
    with pytest.raises(m.session_manager.PersonalSpeakersRefused):
        t.manager.update(a.id, adhoc_selection=m.state.SpeakerSelection(include_speakers=["media_player.x"]))
    with pytest.raises(m.session_manager.PersonalSpeakersRefused):
        t.manager.update(a.id, speaker_group_id="g1")
    a.adhoc_selection = m.state.SpeakerSelection(include_speakers=["media_player.x"])  # even a hand-edited file
    assert t.manager.get_resolved_speakers(a) == [] and t.manager.selected_speakers(a) == []
    assert not run(t.manager.play(a.id))  # play() is the speakers': not for a personal channel


def personal(tmp_path, theme="storm"):
    t = build(tmp_path)
    t.personal = t.manager.personal_channel("user:a")
    t.manager.update(t.personal.id, theme_id=theme)
    t.now = [5000.0]
    t.manager._clock = lambda: t.now[0]
    t.tags = {}  # the devices' own streams: tag -> seconds since closed (0: open)
    t.channel.tag_idle_seconds = lambda tag: t.tags.get(tag)
    t.channel.untagged = 10_000.0
    t.channel.untagged_local_idle_seconds = lambda: t.channel.untagged
    return t


def test_devices_are_counted_one_by_one(tmp_path):
    t = personal(tmp_path)
    pid = t.personal.id
    channel, tag_a = t.manager.listen_personal(pid, "device:a")
    assert channel is t.channel and t.manager.is_listened(t.personal)
    again, same = t.manager.listen_personal(pid, "device:a")  # asking twice: still one device
    assert same == tag_a and len(t.manager.personal_listeners(pid)) == 1
    _, tag_b = t.manager.listen_personal(pid, "device:b")
    assert tag_b != tag_a and len(t.manager.personal_listeners(pid)) == 2
    # DELETE removes only the caller; the mix runs on
    assert t.manager.unlisten_personal(pid, "device:a") is True
    assert t.manager.unlisten_personal(pid, "device:a") is False
    assert t.manager.is_listened(t.personal) and t.channel.playing
    assert list(t.manager.personal_listeners(pid)) == ["device:b"]
    # the last one leaves: the mix stops and its channel is freed
    assert t.manager.unlisten_personal(pid, "device:b") is True
    assert not t.manager.is_listened(t.personal)
    assert ("stop",) in t.channel.calls and t.manager.get_session_channel(pid) is None


def test_no_theme_is_refused(tmp_path):
    t = build(tmp_path)
    a = t.manager.personal_channel("user:a")
    with pytest.raises(ValueError):
        t.manager.listen_personal(a.id, "device:a")
    with pytest.raises(KeyError):
        t.manager.listen_personal(t.session.id, "device:a")  # an ordinary channel isn't personal
    with pytest.raises(ValueError):
        t.manager.update(a.id, theme_id="storm") and t.manager.listen_personal(a.id, "device:a", hold_seconds=301)


def test_a_closed_stream_lets_its_device_go_after_its_hold(tmp_path):
    t = personal(tmp_path)
    pid = t.personal.id
    _, tag_a = t.manager.listen_personal(pid, "device:a")
    _, tag_b = t.manager.listen_personal(pid, "device:b", hold_seconds=120)
    t.tags[tag_a] = t.tags[tag_b] = 0.0  # both streams open
    t.manager.release_idle_sessions()
    assert len(t.manager.personal_listeners(pid)) == 2
    t.tags[tag_a] = 16.0  # a's stream closed 16 s ago (the 15 s default hold)
    t.tags[tag_b] = 60.0  # b's 60 s ago, it asked for 120
    t.manager.release_idle_sessions()
    assert list(t.manager.personal_listeners(pid)) == ["device:b"] and t.channel.playing
    t.tags[tag_b] = 121.0
    t.manager.release_idle_sessions()
    assert not t.manager.is_listened(t.personal) and not t.channel.playing  # idle stop


def test_a_device_whose_stream_never_came_counts_from_when_it_asked(tmp_path):
    t = personal(tmp_path)
    pid = t.personal.id
    t.manager.listen_personal(pid, "device:a")
    t.now[0] += 10
    t.manager.release_idle_sessions()
    assert t.manager.is_listened(t.personal)
    t.channel.untagged = 0.0  # it plays the stream without its tag: kept while that plays
    t.now[0] += 100
    t.manager.release_idle_sessions()
    assert t.manager.is_listened(t.personal)
    t.channel.untagged = 20.0
    t.manager.release_idle_sessions()
    assert not t.manager.is_listened(t.personal)


def test_settings_change_live_and_the_ordinary_channels_are_untouched(tmp_path):
    t = personal(tmp_path)
    pid = t.personal.id
    t.manager.listen_personal(pid, "device:a")
    t.manager.update(pid, preset_id="c")
    assert t.channel.glides()[-1] == 3.0  # glides to the new preset at once
    t.manager.update(pid, theme_id="tavern")
    assert t.channel.calls[-1][:2] == ("set_theme", "tavern")
    assert t.session.theme_id == "storm" and t.session.preset_id == "a"


def test_its_events_go_only_to_its_account(bus, tmp_path):
    t = personal(tmp_path)
    pid = t.personal.id
    t.manager.listen_personal(pid, "device:a")
    t.manager.listen_personal(pid, "device:b")
    t.manager.unlisten_personal(pid, "device:a")
    t.manager.unlisten_personal(pid, "device:b")
    listening = [(d["listening"], d["devices"], d["playing"], d["personal"], who)
                 for _, d, who in published(bus, "channel.listening")]
    assert listening == [(True, 1, True, True, "user:a"), (True, 2, True, True, "user:a"),
                         (True, 1, True, True, "user:a"), (False, 0, False, True, "user:a")]
    assert all(d["channel_id"] == pid for _, d, _ in published(bus, "channel.listening"))
    seen = {"user:a": [], "user:b": [], None: []}
    for who in seen:
        bus.subscribe("*", lambda e, who=who: seen[who].append(e.type), audience=who)

    async def go():
        t.manager.update(pid, volume=20)
        t.manager.update(t.session.id, volume=21)
        await bus.drain()
    asyncio.run(go())
    assert seen["user:a"] == ["channel.changed", "channel.changed"]
    assert seen["user:b"] == seen[None] == ["channel.changed"]  # only the ordinary channel's
    assert len(bus.history(audience="user:a")) > len(bus.history(audience="user:b")) == len(bus.history())


def test_announcements_reach_a_listened_personal_channel_with_announcements_on(tmp_path):
    t = personal(tmp_path)
    pid = t.personal.id
    resolve = m.announce.resolve_channels
    assert resolve(t.manager, "all", personal=True) == [t.session.id]  # nobody listens
    t.manager.listen_personal(pid, "device:a")
    assert resolve(t.manager, "all", personal=True) == [t.session.id, pid]
    assert resolve(t.manager, "all") == [t.session.id]  # sounds, not announcements: never
    with pytest.raises(LookupError):
        resolve(t.manager, pid, personal=True)  # never by id
    with pytest.raises(LookupError):
        resolve(t.manager, "Personal", personal=True)  # or by name
    t.manager.set_announcements(pid, False)
    assert resolve(t.manager, "all", personal=True) == [t.session.id]


def test_zones_never_refuse_or_track_a_personal_channel(tmp_path):
    t = personal(tmp_path)
    policy = MagicMock()
    policy.check.side_effect = AssertionError("asked")
    t.manager.policy = policy
    t.manager.update(t.personal.id, volume=10)
    assert t.manager.allowed("update", t.personal)
    policy.changed.assert_not_called()


# --- The routes --------------------------------------------------------------------------

class Who:
    """Sets the caller from X-Test-* headers (instead of the auth middleware)."""

    def __init__(self, app, accounts_on=False):
        self.app = app
        self.service = SimpleNamespace(authenticators={"users": object()} if accounts_on else {})

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = {k.decode(): v.decode() for k, v in scope["headers"]}
            principal = m.principal.Principal(headers.get("x-test-kind", "device"),
                                              user_id=headers.get("x-test-user") or None,
                                              device_id=headers.get("x-test-device") or None)
            scope.setdefault("state", {})["auth"] = principal
            scope["state"]["auth_service"] = self.service
        return await self.app(scope, receive, send)


def client(tmp_path, accounts_on=True):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    t = build(tmp_path)
    t.manager.registry.get_speaker_name = lambda speaker_id: "Desk"
    t.groups = MagicMock()
    registry = MagicMock()
    registry.get_hierarchy_dict.return_value = {}
    app = FastAPI()
    app.include_router(m.api_v2.create_api_router(
        session_manager=t.manager, group_manager=t.groups, ha_registry=registry, state_store=t.store))
    t.client = TestClient(Who(app, accounts_on))
    return t


def as_(user, device):
    return {"X-Test-User": user, "X-Test-Device": device}


A1, A2, B1 = as_("alice", "pa1"), as_("alice", "pa2"), as_("bob", "pb1")


def test_get_makes_it_with_the_channel_shape(tmp_path):
    t = client(tmp_path)
    body = t.client.get("/api/me/channel", headers=A1).json()
    ordinary = t.client.get(f"/api/sessions/{t.session.id}", headers=A1).json()
    assert set(ordinary) < set(body)
    assert body["personal"] is True and body["devices"] == 0 and body["this_device"] is False
    assert body["speakers"] == [] and body["announcements"] is True and body["listening"] is False
    assert t.client.get("/api/me/channel", headers=A2).json()["id"] == body["id"]  # the same account
    assert t.client.get("/api/me/channel", headers=B1).json()["id"] != body["id"]


def test_hidden_from_the_channel_routes(tmp_path):
    t = client(tmp_path)
    pid = t.client.get("/api/me/channel", headers=A1).json()["id"]
    assert [s["id"] for s in t.client.get("/api/sessions", headers=A1).json()] == [t.session.id]
    for method, path, body in (("GET", "", None), ("PUT", "", {"volume": 5}), ("DELETE", "", None),
                               ("POST", "/listen", None), ("DELETE", "/listen", None), ("POST", "/play", None),
                               ("PUT", "/announcements", {"on": False}), ("POST", "/volume", {"volume": 5}),
                               ("GET", "/playback", None), ("PUT", "/playlist", {"playlist_id": "x"}),
                               ("GET", "/outputs", None), ("POST", "/outputs", {"kind": "cast"})):
        r = t.client.request(method, f"/api/sessions/{pid}{path}", headers=A1, json=body)
        assert r.status_code == 404, (method, path, r.status_code)
    assert t.manager.get_any(pid).volume == t.store.settings.default_volume
    assert t.client.get("/api/now-playing", headers=A1).json()["channels"] == []


def test_put_changes_only_the_callers_own(tmp_path):
    t = client(tmp_path)
    r = t.client.put("/api/me/channel", headers=A1,
                     json={"theme_id": "tavern", "preset_id": "busy", "volume": 25, "announcements": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["theme_id"], body["preset_id"], body["volume"], body["announcements"]) == ("tavern", "busy", 25, False)
    bob = t.client.get("/api/me/channel", headers=B1).json()
    assert (bob["theme_id"], bob["volume"], bob["announcements"]) == (None, t.store.settings.default_volume, True)
    r = t.client.put("/api/me/channel", headers=A1, json={"theme_id": "storm", "sequence_id": "loop"})
    assert r.json()["sequence_id"] == "loop" and r.json()["preset_choice"] == "seq:loop"
    assert t.client.put("/api/me/channel", headers=A1, json={"sequence_id": None}).json()["sequence_id"] is None
    assert t.client.put("/api/me/channel", headers=A1, json={"sequence_id": "nope"}).status_code == 404
    assert t.client.put("/api/me/channel", headers=A1, json={"playlist_id": "nope"}).status_code == 404
    assert t.client.put("/api/me/channel", headers=A1, json={"volume": 101}).status_code == 422
    assert t.client.put("/api/me/channel", headers=A1, content="[1]").status_code == 400


def test_put_refuses_speakers(tmp_path):
    t = client(tmp_path)
    for change in ({"adhoc_selection": {"include_speakers": ["media_player.x"]}}, {"speaker_group_id": "g"}):
        r = t.client.put("/api/me/channel", headers=A1, json=change)
        assert r.status_code == 400 and "speakers" in r.json()["detail"]
    assert t.client.put("/api/me/channel", headers=A1, json={"speaker_group_id": None, "volume": 7}).status_code == 200


def test_listen_counts_each_device_and_delete_removes_only_the_caller(tmp_path):
    t = client(tmp_path)
    assert t.client.post("/api/me/channel/listen", headers=A1).status_code == 400  # no theme yet
    t.client.put("/api/me/channel", headers=A1, json={"theme_id": "storm"})
    r = t.client.post("/api/me/channel/listen", headers=A1, json={"hold_seconds": 120})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["stream_path"] == "/stream/channel1" and first["personal"] is True
    assert first["listen_path"].startswith("/stream/channel1?local=1&listener=")
    assert (first["hold_seconds"], first["devices"]) == (120, 1)
    second = t.client.post("/api/me/channel/listen", headers=A2).json()
    assert second["devices"] == 2 and second["listen_path"] != first["listen_path"]
    assert second["stream_path"] == first["stream_path"]  # one shared mix
    assert t.client.post("/api/me/channel/listen", headers=A2).json()["devices"] == 2
    got = t.client.get("/api/me/channel", headers=A1).json()
    assert got["listening"] is True and got["devices"] == 2 and got["this_device"] is True
    r = t.client.delete("/api/me/channel/listen", headers=A1)
    assert r.json() == {"status": "ok", "removed": True, "devices": 1}
    assert t.client.delete("/api/me/channel/listen", headers=A1).json()["removed"] is False
    assert t.client.get("/api/me/channel", headers=A2).json()["this_device"] is True
    assert t.client.delete("/api/me/channel/listen", headers=A2).json()["devices"] == 0
    assert t.client.post("/api/me/channel/listen", headers=A1, json={"hold_seconds": 301}).status_code == 400


def test_who_gets_a_personal_channel(tmp_path):
    t = client(tmp_path, accounts_on=True)
    assert t.client.get("/api/me/channel", headers={"X-Test-Device": "p"}).status_code == 401  # no user
    assert t.client.get("/api/me/channel", headers={"X-Test-Kind": "open"}).status_code == 401
    assert t.client.get("/api/me/channel", headers={"X-Test-Kind": "bypass"}).status_code == 401
    (tmp_path / "noaccounts").mkdir(exist_ok=True)
    t = client(tmp_path / "noaccounts", accounts_on=False)
    local = t.client.get("/api/me/channel", headers={"X-Test-Kind": "local"}).json()
    phone = t.client.get("/api/me/channel", headers={"X-Test-Device": "p"}).json()
    assert local["id"] == phone["id"]  # no accounts: the server's one personal channel
    assert t.client.get("/api/me/channel", headers={"X-Test-Kind": "anonymous"}).status_code == 401


def test_account_and_device_of():
    P = m.principal.Principal
    pr = m.personal_routes
    assert pr.account_of(P("session", user_id="u1"), True) == "user:u1"
    assert pr.account_of(P("ingress", ha_user={"id": "h1"}), False) == "ha:h1"
    assert pr.account_of(P("ingress", ha_user={"id": "h1"}), True) is None
    assert pr.account_of(P("key", key_id="k"), False) == "local"
    assert pr.account_of(P("key", key_id="k"), True) is None
    request = SimpleNamespace(client=SimpleNamespace(host="10.0.0.9"))
    assert pr.device_of(P("device", device_id="d1", session_id="s"), request) == "device:d1"
    assert pr.device_of(P("session", session_id="s1"), request) == "session:s1"
    assert pr.device_of(P("local"), request) == "local:10.0.0.9"


# --- Live events (G5) --------------------------------------------------------------------

def test_playlist_events(bus, tmp_path):
    t = client(tmp_path)
    body = {"name": "Evening", "entries": [{"theme_id": "tavern"}, {"theme_id": "storm"}]}
    pid = t.client.post("/api/playlists", headers=A1, json=body).json()["id"]
    t.client.put(f"/api/playlists/{pid}", headers=A1, json={"repeat": True})
    t.client.put(f"/api/sessions/{t.session.id}/playlist", headers=A1, json={"playlist_id": pid})
    t.client.delete(f"/api/playlists/{pid}", headers=A1)
    events = [(k, d) for k, d, _ in published(bus, "playlist.")]
    kinds = [k for k, _ in events]
    assert kinds == ["playlist.created", "playlist.changed", "playlist.started", "playlist.entry",
                     "playlist.stopped", "playlist.deleted"]
    assert events[0][1] == {"playlist_id": pid, "playlist_name": "Evening"}
    assert events[1][1]["changes"] == ["repeat"]
    assert events[2][1]["channel_id"] == t.session.id and events[2][1]["playlist_name"] == "Evening"
    assert events[5][1] == {"playlist_id": pid, "playlist_name": "Evening", "stopped_on": [t.session.id]}


def test_group_events(bus, tmp_path):
    t = client(tmp_path)
    group = SimpleNamespace(id="g1", name="Upstairs", icon="mdi:x", include_floors=[], include_areas=[],
                            include_speakers=[], exclude_areas=[], exclude_speakers=[], created_at="", updated_at="")
    t.groups.create.return_value = t.groups.update.return_value = t.groups.get.return_value = group
    t.groups.resolve.return_value, t.groups.get_speaker_count.return_value = [], 0
    t.groups.get_summary.return_value = ""
    t.groups.get_sessions_using_group.return_value = []
    assert t.client.post("/api/groups", headers=A1, json={"name": "Upstairs"}).status_code == 201
    assert t.client.put("/api/groups/g1", headers=A1, json={"name": "Upstairs"}).status_code == 200
    assert t.client.delete("/api/groups/g1", headers=A1).status_code == 204
    assert [d for _, d, _ in published(bus, "group.")] == [
        {"group_id": "g1", "group_name": "Upstairs", "change": c} for c in ("created", "changed", "deleted")]


def test_settings_events(bus, tmp_path):
    t = client(tmp_path)
    t.client.put("/api/settings", headers=A1, json={"default_volume": 30, "crossfade_duration": 2})
    t.client.post("/api/settings/speakers/disable", headers=A1, json={"entity_id": "media_player.x"})
    changed = [d for _, d, _ in published(bus, "settings.")]
    assert changed[0]["section"] == "general"
    assert sorted(changed[0]["keys"]) == ["crossfade_duration", "default_volume"]
    assert changed[1] == {"section": "speakers", "keys": ["enabled_speakers"]}


def test_custom_area_events(bus, tmp_path):
    t = client(tmp_path)
    t.client.post("/api/settings/speaker-areas/create", headers=A1, json={"name": "Den", "speakers": []})
    t.client.delete("/api/settings/speaker-areas/Den", headers=A1)
    assert [d for _, d, _ in published(bus, "spaces.")] == [{"source": "custom_areas"}] * 2


def test_spaces_changed_from_the_registry(bus):
    registry = m.registry.HARegistry("http://ha.invalid/api", "token")
    local = {}
    registry.set_local_spaces_source(lambda: local)
    registry._rebuild()
    registry._rebuild()
    assert published(bus, "spaces.") == []  # first list, then nothing changed
    m.spaces.add_floor(local, "Upstairs")
    registry._rebuild()
    assert published(bus, "spaces.") == [("spaces.changed", {"source": "registry"}, None)]
    m.spaces.add_area(local, "Den", "floor_upstairs")
    registry._rebuild()
    assert len(published(bus, "spaces.")) == 2


def test_the_catalogue_lists_the_new_events():
    for name in ("playlist.created", "playlist.changed", "playlist.deleted", "playlist.started", "playlist.stopped",
                 "group.changed", "spaces.changed", "settings.changed"):
        assert m.events.is_known_type(name) and m.events.CATALOGUE[name]["fields"]
    assert {"devices", "playing", "personal"} <= set(m.events.CATALOGUE["channel.listening"]["fields"])
