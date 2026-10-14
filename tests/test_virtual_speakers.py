"""
Virtual speakers (demo branch, sonorium/virtual_speakers.py): one speaker per
account, made, renamed and removed with it; a speaker like any other in
channels and speaker groups; playing -> a stream for that user's listening
client only; the last client to claim wins; nobody else can claim or hear
another user's speaker.
"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from playback_helpers import addon_modules  # noqa: E402
from output_world import MODULES, World, wait_for  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules(*MODULES, "sonorium.virtual_speakers", "sonorium.core.speaker_live",
                       "sonorium.auth.principal", "sonorium.web.api_v2", "sonorium.limits",
                       "sonorium.playback_values", "sonorium.auth.proxies", "sonorium.auth.stream_links") as mods:
        m.__dict__.update(mods.__dict__)
        yield


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def w(tmp_path):
    world = World(m, tmp_path)
    world.users = {"u_ann": {"username": "ann"}, "u_bob": {"username": "bob"}}
    world.clock = Clock()
    world.vs = m.virtual_speakers.VirtualSpeakers(world.sm, lambda: world.users, tmp_path, clock=world.clock)
    world.vs.sync()
    yield world
    m.speaker_live.LOCAL_HANDLERS.clear()
    world.close()


ANN, BOB = "virtual:u_ann", "virtual:u_bob"


def listed(w):
    return {s.entity_id: s for s in w.registry._speakers.values() if s.entity_id.startswith("virtual:")}


# --- One speaker per account ---------------------------------------------------------------

def test_every_account_has_one_speaker_named_after_it(w):
    speakers = listed(w)
    assert set(speakers) == {ANN, BOB}
    assert speakers[ANN].name == "ann" and speakers[ANN].type == "virtual" and speakers[ANN].address == "Virtual"
    assert speakers[ANN].online  # always: pickers leave offline speakers out
    assert not w.vs.status("u_ann")["listening"]  # nobody listens yet
    # switched on in Settings › Speakers (this world lists the speakers that are on)
    assert w.state.settings.speaker_enabled(ANN) and w.state.settings.speaker_enabled(BOB)
    assert w.vs.sync() is False  # nothing changed: nothing done


def test_speakers_follow_accounts_added_renamed_removed(w, tmp_path):
    w.users["u_cy"] = {"username": "cy"}
    assert w.vs.sync()
    assert listed(w)["virtual:u_cy"].name == "cy"
    w.users["u_cy"]["username"] = "cyril"
    w.vs.sync()
    assert listed(w)["virtual:u_cy"].name == "cyril"
    # in a channel and a speaker group, with settings: all of it goes with the account
    channel = w.channel("Den", ["net:a", "virtual:u_cy"])
    group = m.state.SpeakerGroup(id="g1", name="All", include_speakers=["virtual:u_cy", "net:a"])
    w.state.speaker_groups["g1"] = group
    w.state.settings.speaker_settings["virtual:u_cy"] = {"volume_offset": 10}
    del w.users["u_cy"]
    w.vs.sync()
    assert "virtual:u_cy" not in listed(w)
    assert channel.adhoc_selection.include_speakers == ["net:a"] and group.include_speakers == ["net:a"]
    assert "virtual:u_cy" not in w.state.settings.enabled_speakers
    assert "virtual:u_cy" not in w.state.settings.speaker_settings
    # a restart: the speakers are listed at once, though no account changed
    w.registry._speakers.clear()
    m.virtual_speakers.VirtualSpeakers(w.sm, lambda: w.users, tmp_path)
    assert {ANN, BOB} <= set(listed(w))
    # an account removed while Sonorium was off: noticed at the next start (remembered on disk)
    again = m.virtual_speakers.VirtualSpeakers(None, lambda: {"u_ann": {"username": "ann"}}, tmp_path)
    assert set(again._known()) == {"u_ann", "u_bob"}
    again.sync()
    assert set(again._known()) == {"u_ann"}


def test_accounts_not_loaded_removes_nothing(w):
    users = w.users
    w.users = None
    w.vs._accounts = lambda: w.users
    w.vs.sync()
    assert set(listed(w)) == {ANN, BOB}  # remembered ones listed (offline), nothing forgotten
    w.users = users
    assert w.vs.status("u_ann") is not None


# --- A speaker like any other: channels and speaker groups ---------------------------------

def test_plays_in_a_channel_and_a_group_never_through_the_media_controller(w):
    solo = w.channel("Solo", [ANN])
    assert w.play(solo)
    assert w.vs.status("u_ann")["playing"] and w.vs.status("u_ann")["channel"] == {"id": solo.id, "name": "Solo"}
    assert not w.vs.status("u_bob")["playing"]
    assert all(ANN not in call.args[0] for call in w.controller.play_media_multi.call_args_list)
    w.stop(solo)
    assert not w.vs.status("u_ann")["playing"]

    w.state.speaker_groups["g1"] = m.state.SpeakerGroup(id="g1", name="Family", include_speakers=["net:a", BOB])
    family = w.sm.create(theme_id="storm", custom_name="Family", speaker_group_id="g1")
    assert w.play(family)
    assert w.vs.status("u_bob")["playing"] and not w.vs.status("u_ann")["playing"]
    assert w.controller.play_media_multi.call_args_list[-1].args[0] == ["net:a"]


def test_switched_off_speaker_does_not_play(w):
    w.state.settings.enabled_speakers.remove(ANN)
    solo = w.channel("Both", ["net:a", ANN])
    assert w.play(solo)
    assert not w.vs.status("u_ann")["playing"]


def test_volume_offset_and_own_volume_and_mute(w):
    session = w.channel("Den", [ANN], volume=50)
    assert w.vs.gain("u_ann", session) == pytest.approx(0.5)
    w.state.settings.speaker_settings[ANN] = {"volume_offset": 20}
    assert w.vs.gain("u_ann", session) == pytest.approx(0.7)
    live = m.speaker_live.SpeakerLive(lambda: None)
    m.speaker_live.LOCAL_HANDLERS["virtual:"] = w.vs
    assert asyncio.run(live.set_volume(ANN, 50)) and live.state(ANN) == {"volume": 50}
    assert w.vs.gain("u_ann", session) == pytest.approx(0.35)
    assert asyncio.run(live.set_muted(ANN, True))
    assert w.vs.gain("u_ann", session) == 0.0
    assert not asyncio.run(live.set_volume("virtual:u_nobody", 50))


def test_test_sound_is_refused(w):
    with pytest.raises(m.audio_outputs.OutputBusy):
        asyncio.run(w.vs.test(ANN))
    with pytest.raises(LookupError):
        asyncio.run(w.vs.test("virtual:u_nobody"))


# --- Listening: the last client wins --------------------------------------------------------

def test_claim_gives_this_client_the_stream_and_the_last_claim_wins(w):
    first = w.vs.claim("u_ann", "web-aaaaaaaa", "web")
    assert first["this_client"] and first["stream_path"].startswith("/stream/virtual/")
    assert first["listening"] and first["listener_kind"] == "web"
    again = w.vs.claim("u_ann", "web-aaaaaaaa", "web")
    assert again["stream_path"] == first["stream_path"]  # asking again keeps its stream
    second = w.vs.claim("u_ann", "app-device-1", "app")
    assert second["this_client"] and second["stream_path"] != first["stream_path"]
    old = w.vs.status("u_ann", "web-aaaaaaaa")
    assert not old["this_client"] and old["stream_path"] is None and old["listener_kind"] == "app"
    # the earlier client's stream is gone
    solo = w.channel("Solo", [ANN])
    w.play(solo)
    with pytest.raises(LookupError):
        w.vs.open_stream(first["stream_path"].rsplit("/", 1)[1])
    # releasing only works for the one listening
    assert not w.vs.release("u_ann", "web-aaaaaaaa")
    assert w.vs.release("u_ann", "app-device-1")
    assert not w.vs.status("u_ann")["listening"]


def test_offline_when_the_listening_client_goes_quiet(w):
    w.vs.claim("u_ann", "app-device-1", "app")
    w.clock.now += m.virtual_speakers.STALE_SECONDS - 1
    assert w.vs.status("u_ann", "app-device-1")["listening"]  # its poll is its heartbeat
    w.clock.now += m.virtual_speakers.STALE_SECONDS + 1
    assert not w.vs.status("u_ann")["listening"]
    w.vs.sync()
    assert listed(w)[ANN].online  # still offered for channels


def test_bad_claims(w):
    with pytest.raises(ValueError):
        w.vs.claim("u_ann", "short", "web")
    with pytest.raises(LookupError):
        w.vs.claim("u_nobody", "app-device-1", "app")
    assert w.vs.status("u_nobody") is None and w.vs.status(None) is None


def read_some(stream, chunks, done):
    for data in stream:
        chunks.append(data)
    done.set()


def test_stream_plays_while_the_channel_plays_and_ends_when_another_client_claims(w):
    solo = w.channel("Solo", [ANN, "net:a"])
    with pytest.raises(LookupError):  # not claimed
        w.vs.open_stream("0" * 32)
    token = w.vs.claim("u_ann", "web-aaaaaaaa", "web")["stream_path"].rsplit("/", 1)[1]
    with pytest.raises(LookupError):  # not playing
        w.vs.open_stream(token)
    assert w.play(solo)
    stream = w.vs.open_stream(token)
    chunks, done = [], threading.Event()
    threading.Thread(target=read_some, args=(stream, chunks, done), daemon=True).start()
    assert wait_for(lambda: len(chunks) > 2)
    assert w.vs.status("u_ann")["listening"]
    w.vs.claim("u_ann", "app-device-1", "app")  # last one wins: this stream ends
    assert done.wait(5)


def test_stream_ends_when_the_speaker_leaves_the_channel(w):
    solo = w.channel("Solo", [ANN, "net:a"])
    token = w.vs.claim("u_ann", "web-aaaaaaaa", "web")["stream_path"].rsplit("/", 1)[1]
    assert w.play(solo)
    chunks, done = [], threading.Event()
    threading.Thread(target=read_some, args=(w.vs.open_stream(token), chunks, done), daemon=True).start()
    assert wait_for(lambda: len(chunks) > 2)
    solo.adhoc_selection.include_speakers.remove(ANN)
    assert done.wait(5)
    assert w.sm.get(solo.id).is_playing  # the channel plays on to the other speakers


# --- The routes: only the caller's own speaker ---------------------------------------------

def client_for(w):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(m.virtual_speakers.create_router(w.vs))

    @app.middleware("http")
    async def sign_in(request, call_next):
        user = request.headers.get("x-test-user")
        request.state.auth = m.principal.Principal("session", user_id=user or None)
        return await call_next(request)

    return TestClient(app)


def test_routes_reach_only_the_callers_own_speaker(w):
    http = client_for(w)
    ann = {"x-test-user": "u_ann"}
    bob = {"x-test-user": "u_bob"}
    assert http.get("/api/me/speaker").status_code == 404  # nobody signed in
    assert http.get("/api/me/speaker", headers={"x-test-user": "u_nobody"}).status_code == 404
    claimed = http.post("/api/me/speaker/claim", json={"client": "web-aaaaaaaa", "kind": "web"}, headers=ann).json()
    assert claimed["id"] == ANN and claimed["this_client"]
    # Bob sees and claims only his own; Ann keeps hers
    seen = http.get("/api/me/speaker", params={"client": "web-aaaaaaaa"}, headers=bob).json()
    assert seen["id"] == BOB and not seen["this_client"] and seen["stream_path"] is None
    bobs = http.post("/api/me/speaker/claim", json={"client": "web-aaaaaaaa"}, headers=bob).json()
    assert bobs["id"] == BOB and bobs["stream_path"] != claimed["stream_path"]
    assert http.get("/api/me/speaker", params={"client": "web-aaaaaaaa"}, headers=ann).json()["this_client"]
    # Bob can't let Ann's client go
    assert not http.request("DELETE", "/api/me/speaker/claim", params={"client": "web-aaaaaaaa"},
                            headers=bob).json()["listening"]
    assert w.vs.status("u_ann")["listening"]
    # streams: only a current claim's token, and only while playing
    assert http.get("/stream/virtual/" + "0" * 32).status_code == 404
    assert http.get("/stream/virtual/not-a-token").status_code == 404
    assert http.get(claimed["stream_path"]).status_code == 404  # nothing plays
    assert http.post("/api/me/speaker/claim", json={"client": "x"}, headers=ann).status_code == 400


def test_auth_me_field(w):
    me = m.virtual_speakers._Me(w.vs)
    assert me.me(None, m.principal.Principal("session", user_id="u_ann"))["virtual_speaker"]["id"] == ANN
    assert me.me(None, m.principal.Principal("open"))["virtual_speaker"] is None


def test_permission_table_covers_the_routes():
    router = m.virtual_speakers.create_router(SimpleNamespace())
    from sonorium.auth import permissions as P
    table = P.parse(m.virtual_speakers.PERMISSIONS)
    paths = {(meth, r.path) for r in router.routes for meth in r.methods if meth != "HEAD"}
    assert paths == set(table)


# --- Through the web UI's routes: per-speaker settings stay per speaker; pickable for channels ----

def ui_client(w):
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(m.api_v2.create_api_router(session_manager=w.sm, group_manager=MagicMock(),
                                                  ha_registry=w.registry, state_store=w.state))

    @app.middleware("http")
    async def signed_in(request, call_next):
        request.state.auth = m.principal.Principal("local", perms=frozenset({"ch.speakers", "sp.set", "sp.mute"}))
        return await call_next(request)
    return TestClient(app)


def snapshot(http):
    rows = {s["entity_id"]: s for s in http.get("/api/speakers").json() if s["entity_id"].startswith("virtual:")}
    enabled = set(http.get("/api/settings/speakers").json()["enabled_speakers"])
    return {sid: (sid in enabled, r["area_id"], r["volume_offset"], r["name"], r.get("volume"), r.get("muted"))
            for sid, r in rows.items()}


def test_each_speakers_settings_are_its_own(w):
    w.users["u_cy"] = {"username": "cy"}
    w.vs.sync()
    CY = "virtual:u_cy"
    w.registry.set_local_spaces_source(lambda: {"floors": [], "areas": [{"id": "den", "name": "Den"}]})
    w.registry.merge_extra_speakers()
    http = ui_client(w)
    m.speaker_live.LOCAL_HANDLERS["virtual:"] = w.vs
    for sid in (ANN, BOB, CY):  # all off
        assert http.post("/api/settings/speakers/disable", json={"entity_id": sid}).status_code == 200
    before = snapshot(http)
    assert not any(v[0] for v in before.values())

    def only_ann_changed(after, field, value):
        assert after[ANN][field] == value
        for sid in (BOB, CY):
            assert after[sid] == before[sid], (field, sid)

    assert http.post("/api/settings/speakers/enable", json={"entity_id": ANN}).status_code == 200
    w.vs.sync()
    only_ann_changed(snapshot(http), 0, True)
    before = snapshot(http)
    r = http.put(f"/api/speakers/{ANN}/settings", json={"volume_offset": 10})
    assert r.status_code == 200, r.text
    only_ann_changed(snapshot(http), 2, 10)
    before = snapshot(http)
    r = http.put(f"/api/speakers/{ANN}/settings", json={"room": "den"})
    assert r.status_code == 200, r.text
    only_ann_changed(snapshot(http), 1, "den")
    before = snapshot(http)
    assert http.put(f"/api/speakers/{ANN}/settings", json={"name": "Ann's room"}).status_code == 200
    only_ann_changed(snapshot(http), 3, "Ann's room")
    before = snapshot(http)
    assert http.put(f"/api/speakers/{ANN}/volume", json={"volume": 40}).status_code == 200
    only_ann_changed(snapshot(http), 4, 40)
    before = snapshot(http)
    assert http.put(f"/api/speakers/{ANN}/muted", json={"muted": True}).status_code == 200
    only_ann_changed(snapshot(http), 5, True)
    before = snapshot(http)
    assert http.post("/api/settings/speakers/disable", json={"entity_id": ANN}).status_code == 200
    w.vs.sync()
    only_ann_changed(snapshot(http), 0, False)


def test_offered_in_the_pickers_and_plays_in_a_channel_made_through_the_api(w):
    http = ui_client(w)
    hierarchy = http.get("/api/speakers/hierarchy").json()
    listed_ids = {s["entity_id"]: s for s in hierarchy["unassigned_speakers"]}
    assert listed_ids[ANN]["online"] and listed_ids[BOB]["online"]  # the pickers show online speakers
    assert ANN in http.get("/api/settings/speakers").json()["enabled_speakers"]
    r = http.post("/api/sessions", json={"theme_id": "storm", "custom_name": "Den",
                                         "adhoc_selection": {"include_speakers": [ANN]}})
    assert r.status_code in (200, 201), r.text
    sid = r.json()["id"]
    session = w.sm.get(sid)
    assert w.sm.get_resolved_speakers(session) == [ANN]
    assert w.play(session)
    assert w.vs.status("u_ann")["channel"]["id"] == sid
    # changed to Bob's speaker through the same route the channel editor uses
    r = http.put(f"/api/sessions/{sid}", json={"adhoc_selection": {"include_speakers": [BOB]}})
    assert r.status_code == 200, r.text
    assert not w.vs.status("u_ann")["playing"] and w.vs.status("u_bob")["playing"]


# --- Exposure limits (limits.py, auth/proxies.py, auth/stream_links.py) -------------------

def demo_client(w, monkeypatch, peer=("172.18.0.5", 40000)):
    """The demo's layout: Caddy (a trusted proxy on the Docker bridge) in front of Sonorium."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv(m.proxies.ENV, "172.18.0.0/16")
    app = FastAPI()
    app.include_router(m.virtual_speakers.create_router(w.vs))

    @app.middleware("http")
    async def sign_in(request, call_next):
        user = request.headers.get("x-test-user")
        request.state.auth = m.principal.Principal("session", user_id=user or None)
        return await call_next(request)
    app.add_middleware(m.limits.BodyLimitMiddleware)
    app.add_middleware(m.proxies.ForwardedClient)
    return TestClient(app, client=peer)


@pytest.fixture
def values():
    stored = {}
    m.playback_values.set_source(lambda: stored)
    yield stored
    m.playback_values.set_source(None)


def test_claim_and_status_have_the_request_body_cap(w, monkeypatch, values):
    http = demo_client(w, monkeypatch)
    ann = {"x-test-user": "u_ann", "Content-Type": "application/json"}
    big = b'{"client": "web-aaaaaaaa", "pad": "' + b"a" * (1100 * 1024) + b'"}'
    assert http.post("/api/me/speaker/claim", content=big, headers=ann).status_code == 413
    assert http.request("DELETE", "/api/me/speaker/claim", content=big, headers=ann).status_code == 413
    assert http.post("/api/me/speaker/claim", json={"client": "web-aaaaaaaa"}, headers=ann).status_code == 200


def test_stream_counts_against_the_stream_limits_by_the_forwarded_address(w, monkeypatch, values):
    http = demo_client(w, monkeypatch)
    slots = m.stream_links.STREAMS
    solo = w.channel("Solo", [ANN, "net:a"])
    path = w.vs.claim("u_ann", "web-aaaaaaaa", "web")["stream_path"]
    assert "?t=" not in path  # its own secret: no stream link
    assert w.play(solo)
    values.update(streams_per_address=1, streams_limit=0)
    assert slots.take("198.51.100.7", 1)
    try:
        # the caller behind Caddy is counted, not Caddy
        assert http.get(path, headers={"X-Forwarded-For": "198.51.100.7"}).status_code == 429
        missing = http.get("/stream/virtual/" + "0" * 32, headers={"X-Forwarded-For": "198.51.100.8"})
        assert missing.status_code == 404 and slots.held("198.51.100.8") == 0  # a refused stream gives its slot back
        values.update(streams_per_address=0, streams_limit=1)
        assert http.get(path, headers={"X-Forwarded-For": "198.51.100.9"}).status_code == 429  # in all
    finally:
        slots.give("198.51.100.7")
    assert slots.held() == 0


def test_a_new_stream_on_the_same_claim_replaces_the_old_one(w):
    solo = w.channel("Solo", [ANN, "net:a"])
    token = w.vs.claim("u_ann", "web-aaaaaaaa", "web")["stream_path"].rsplit("/", 1)[1]
    assert w.play(solo)
    released = []
    first, done1 = [], threading.Event()
    threading.Thread(target=read_some, args=(w.vs.open_stream(token, release=lambda: released.append(1)), first, done1),
                     daemon=True).start()
    assert wait_for(lambda: len(first) > 2)
    second, done2 = [], threading.Event()
    threading.Thread(target=read_some, args=(w.vs.open_stream(token), second, done2), daemon=True).start()
    assert done1.wait(5) and released == [1]  # a reconnect: the old stream ends and gives its slot back
    assert wait_for(lambda: len(second) > 2) and not done2.is_set()
    w.vs.release("u_ann", "web-aaaaaaaa")
    assert done2.wait(5)


def test_log_lines_naming_users_are_private(w, caplog):
    import logging
    w.users["u_dee"] = {"username": "dee"}
    with caplog.at_level(logging.INFO):
        w.vs.sync()
        del w.users["u_dee"]
        w.vs.sync()
    named = [r for r in caplog.records if "dee" in r.getMessage()]
    assert len(named) == 2
    # logbuffer.is_private: the "sonorium.auth" logger or a child of an "auth" logger tree
    assert all(r.name.endswith(".auth") or r.name == "sonorium.auth" for r in named)
