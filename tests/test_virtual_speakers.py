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
                       "sonorium.auth.principal") as mods:
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
    assert not speakers[ANN].online  # nobody listens yet
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
    w.vs.sync()
    assert listed(w)[ANN].online
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
    assert not listed(w)[ANN].online


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
