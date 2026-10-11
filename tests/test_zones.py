"""
Zones (the Users plugin's zones.py): channels owned by a user, Kid mode and
its hours (the end-of-hours fade: what the kid started vs what an admin
started), a speaker in use by one channel at a time, speaker homes (taking a
speaker back, a new home when the home is deleted), links (independent
volume, unlinking puts things back, leaving and rejoining per user, an admin
pulling a channel back), Home Assistant acting as an admin, and the switch
off = Sonorium as before.

No audio: the media controller is a mock and the mixer channels are fakes.
"""
import asyncio
import contextlib
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("argon2")
pytest.importorskip("cryptography")
pytest.importorskip("segno")

from playback_helpers import PACKAGE, addon_modules  # noqa: E402

m = SimpleNamespace()
BUILTIN = PACKAGE / "plugins" / "builtin"


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.auth.service", "sonorium.auth.middleware", "sonorium.auth.routes",
                       "sonorium.auth.principal", "sonorium.auth.config", "sonorium.auth.announce",
                       "sonorium.plugins.loader", "sonorium.plugins.events", "sonorium.runtime",
                       "sonorium.core.session_manager", "sonorium.core.state", "sonorium.core.channel_policy",
                       "sonorium.core.playback_runner", "sonorium.core.sequences", "sonorium.core.playlists",
                       "sonorium.core.outputs") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture
def bus():
    new = m.events.EventBus()
    old = m.events.set_bus(new)
    yield new
    m.events.set_bus(old)


class Channel:
    """A mixer channel that records fades (no audio)."""

    def __init__(self, channel_id):
        self.id = channel_id
        self.calls = []
        self.faded = False
        self.speakers_muted = False

    @property
    def is_faded_out(self):
        return self.faded

    @property
    def stream_path(self):
        return f"/stream/channel{self.id}"

    def set_theme(self, theme, overrides=None, crossfade=3.0, fade_in=0.0):
        self.calls.append(("set_theme", theme.id))
        self.faded = False

    def set_track_overrides(self, overrides, glide_seconds=0.0, fade_seconds=None):
        self.calls.append(("overrides",))

    def fade_in(self, seconds):
        self.calls.append(("fade_in", seconds))
        self.faded = False

    def fade_out(self, seconds):
        self.calls.append(("fade_out", seconds))
        self.faded = True

    def stop(self):
        self.calls.append(("stop",))

    def idle_seconds(self):
        return 0.0

    def local_idle_seconds(self):
        return 0.0


class Channels:
    def __init__(self):
        self.channels = {}

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_available_channel(self):
        channel = Channel(len(self.channels) + 1)
        self.channels[channel.id] = channel
        return channel


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 11, 12, 0)

    def __call__(self):
        return self.now


NAMES = {"media_player.x": "Kitchen speaker", "media_player.y": "Office_C97A", "media_player.z": "Guest room"}


class World:
    def __init__(self, tmp_path, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        monkeypatch.setenv("SONORIUM_CONFIG_DIR", str(tmp_path))
        for name in ("BYPASS_LOGIN", "SONORIUM__BYPASS_LOGIN", "SONORIUM__WEB_UI_ONLY_THROUGH_HA", "SUPERVISOR_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(m.runtime, "STANDALONE", True)
        monkeypatch.setattr(m.announce, "interface_addresses", lambda: [("eth0", "192.168.1.5")])

        # Channels, with fake speakers and no audio
        store = m.state.StateStore(tmp_path / "state.json")
        store.settings.crossfade_duration = 0
        store.settings.enabled_speakers = list(NAMES)
        registry = MagicMock()
        registry.resolve_selection.side_effect = lambda **kw: list(kw.get("include_speakers") or [])
        registry.get_speaker_name.side_effect = lambda sp: NAMES.get(sp, sp)
        controller = MagicMock()
        for name in ("play_media_multi", "set_volume_multi", "stop_multi", "pause_multi"):
            setattr(controller, name, AsyncMock(side_effect=lambda ids, *a: {i: True for i in ids}))
        self.controller = controller
        self.channels = Channels()
        sm = self.sm = m.session_manager.SessionManager(store, registry, controller, "http://test",
                                                        channel_manager=self.channels)
        themes = {t: SimpleNamespace(id=t, name=t.title()) for t in ("storm", "sleigh", "forest")}
        sm.get_theme = themes.get
        sm._theme_presets = lambda theme_id: {"quiet": {"name": "Quiet night"}} if theme_id == "sleigh" else {}
        store.playlists["pl"] = {"name": "Mix", "order": "in_order", "repeat": True, "play_min": 60,
                                 "play_max": 60, "entries": [{"theme_id": "forest"}]}

        # Sign-in with the Users plugin
        self.service = s = m.service.AuthService(tmp_path)
        self.app = app = FastAPI()
        s.app = app
        app.add_middleware(m.middleware.AuthMiddleware, service=s)
        app.include_router(m.routes.create_core_auth_router(
            s, stream_url=lambda: "http://192.168.1.5:8008", features=lambda: {}, version="test",
            https_host_port=lambda: 8808))
        s.note_install()
        folder = BUILTIN / "users"
        manifest = json.loads((folder / "manifest.json").read_text())
        plugin = m.loader.load_plugin_class(folder, manifest)(plugin_dir=folder, settings={}, audio_path=None)
        plugin._host = SimpleNamespace(auth=s, session_manager=sm)
        asyncio.run(plugin.on_enable())
        s.mark_ready()
        self.plugin = plugin
        self.zones = s.plugin_state["users.zones"]
        self.clock = Clock()
        self.zones.now = self.clock
        self.accounts = s.plugin_state["users"]
        self.client = TestClient(app)

        # An admin (chris), and two users: alex (a kid) and sam
        r = self.client.post("/api/auth/setup", json={"username": "chris", "password": "correct horse"})
        assert r.status_code == 200, r.text
        self.csrf = self.client.get("/api/auth/me").json()["csrf"]
        self.ids = {"chris": self.client.get("/api/auth/me").json()["user"]["id"]}
        for name in ("alex", "sam"):
            r = self.api("post", "/api/users", {"username": name, "password": f"{name} password", "role": "user"})
            self.ids[name] = r.json()["user"]["id"]

    def api(self, method, path, body=None, client=None, csrf=None, status=200):
        c = client or self.client
        kwargs = {"headers": {"X-CSRF-Token": csrf or self.csrf}}
        if body is not None:
            kwargs["json"] = body
        r = getattr(c, method)(path, **kwargs)
        assert r.status_code == status, (path, r.status_code, r.text)
        return r

    def sign_in(self, name):
        from fastapi.testclient import TestClient
        c = TestClient(self.app)
        assert c.post("/api/auth/login", json={"username": name, "password": f"{name} password"}).status_code == 200
        return c, c.get("/api/auth/me").json()["csrf"]

    @contextlib.contextmanager
    def acting(self, name):
        """Calls to the session manager as this user (as the middleware sets it for a request)."""
        role = "admin" if name == "chris" else "user"
        p = m.principal.Principal("session", frozenset(), user_id=self.ids[name], role=role)
        token = m.principal.CALLER.set(p)
        try:
            yield
        finally:
            m.principal.CALLER.reset(token)

    def channel(self, name, speakers, theme="storm"):
        return self.sm.create(theme_id=theme, custom_name=name,
                              adhoc_selection=m.state.SpeakerSelection(include_speakers=list(speakers)))

    def switch(self, on=True):
        self.api("put", "/api/zones/switch", {"on": on})

    def own(self, session, owner, **extra):
        body = {"owner": self.ids[owner] if owner else None, **extra}
        return self.api("put", f"/api/zones/{session.id}", body)

    def play(self, session, who=None):
        async def go():
            ok = await self.sm.play(session.id)
            await asyncio.sleep(0)
            return ok
        with self.acting(who) if who else contextlib.nullcontext():
            return asyncio.run(go())

    def stop(self, session, who=None):
        with self.acting(who) if who else contextlib.nullcontext():
            return asyncio.run(self.sm.stop(session.id))

    def refused(self, fn, status=403):
        with pytest.raises(m.channel_policy.ChannelRefused) as e:
            fn()
        assert e.value.status == status
        return e.value.detail


@pytest.fixture
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def sel(*speakers):
    return m.state.SpeakerSelection(include_speakers=list(speakers))


# --- The switch -----------------------------------------------------------------------

def test_switch_off_changes_nothing(w):
    """Off (the default): no policy, every channel open, a shared speaker plays for every channel that has it."""
    a = w.channel("Office", ["media_player.x", "media_player.y"])
    b = w.channel("Kitchen", ["media_player.x"])
    assert w.zones.enabled is False and w.sm.policy is None
    assert w.client.get("/api/zones").json() == {"on": False, "admin": True, "me": w.ids["chris"],
                                                  "can_leave_link": True}
    assert w.client.get("/api/auth/me").json()["zones"] is False
    assert w.play(a, "sam") and w.play(b, "sam")
    assert w.sm.get_resolved_speakers(a) == ["media_player.x", "media_player.y"]
    assert w.sm.get_resolved_speakers(b) == ["media_player.x"]
    with w.acting("sam"):
        w.sm.update(b.id, theme_id="forest", adhoc_selection=sel("media_player.x", "media_player.y"))
        assert w.sm.delete(a.id)
    # Zones can't be set while off
    w.api("put", f"/api/zones/{b.id}", {"owner": w.ids["alex"]}, status=409)


def test_switch_is_for_admins_and_existing_channels_stay_open(w, bus):
    kitchen = w.channel("Kitchen", ["media_player.x"])
    sam, scsrf = w.sign_in("sam")
    w.api("put", "/api/zones/switch", {"on": True}, client=sam, csrf=scsrf, status=403)
    w.switch(True)
    assert w.sm.policy is w.zones and w.client.get("/api/auth/me").json()["zones"] is True
    assert "zone.switched" in [e.type for e in bus.history()]
    view = w.client.get("/api/zones").json()
    assert view["zones"] == {} and view["speakers"]["media_player.x"]["home"] == kitchen.id
    assert w.play(kitchen, "sam")  # nobody owns it: open
    # Off again: as before
    w.switch(False)
    assert w.sm.policy is None and w.sm.get_resolved_speakers(kitchen) == ["media_player.x"]


# --- Owners ---------------------------------------------------------------------------

def test_only_the_owner_and_admins_change_a_zone(w):
    w.switch()
    room = w.channel("Kid's Room", ["media_player.z"])
    sam, scsrf = w.sign_in("sam")
    w.api("put", f"/api/zones/{room.id}", {"owner": w.ids["alex"]}, client=sam, csrf=scsrf, status=403)
    w.own(room, "alex")
    assert w.client.get("/api/zones").json()["zones"][room.id]["owner_name"] == "alex"
    with w.acting("sam"):
        assert "belongs to alex" in w.refused(lambda: w.sm.update(room.id, theme_id="forest"))
        w.refused(lambda: w.sm.update(room.id, custom_name="Mine"))
        w.refused(lambda: w.sm.guard("play", room))
        w.refused(lambda: w.sm.guard("stop", room))
        w.refused(lambda: w.sm.delete(room.id))
        w.refused(lambda: w.sm.start_sequence(room.id, "any"))
        w.refused(lambda: w.sm.set_speakers_muted(room.id, True))
        w.refused(lambda: asyncio.run(w.sm.set_volume(room.id, 10)))
        w.sm.update(room.id, theme_id=room.theme_id, volume=room.volume)  # nothing changes: fine
    with w.acting("alex"):
        w.sm.update(room.id, theme_id="forest", adhoc_selection=sel("media_player.z", "media_player.y"))
        assert room.theme_id == "forest"
    assert w.play(room, "alex")
    with w.acting("chris"):
        w.sm.update(room.id, theme_id="storm")
    # Removing alex opens the zone again
    w.api("delete", f"/api/users/{w.ids['alex']}")
    with w.acting("sam"):
        w.sm.update(room.id, theme_id="sleigh")


# --- Kid mode -------------------------------------------------------------------------

def test_kid_mode_only_an_admin_changes_the_speakers(w, bus):
    w.switch()
    room = w.channel("Kid's Room", ["media_player.z"])
    w.own(room, "alex", kid_mode=True, schedule={"start": "07:00", "end": "20:00"})
    changed = [e.data["changes"] for e in bus.history() if e.type == "zone.changed"]
    assert changed[-1] == {"owner": w.ids["alex"], "kid_mode": True, "schedule": {"start": "07:00", "end": "20:00"}}
    with w.acting("alex"):
        assert w.refused(lambda: w.sm.update(room.id, adhoc_selection=sel("media_player.y"))) == \
            "Only an admin can change the speakers of Kid's Room."
        w.sm.update(room.id, theme_id="forest", volume=30)  # what plays: yes, within the hours
    with w.acting("chris"):
        w.sm.update(room.id, adhoc_selection=sel("media_player.y"))
    assert w.sm.selected_speakers(room) == ["media_player.y"]
    w.api("put", f"/api/zones/{room.id}", {"schedule": {"start": "25:00", "end": "x"}}, status=400)


def test_kid_mode_hours(w):
    w.switch()
    room = w.channel("Kid's Room", ["media_player.z"])
    w.own(room, "alex", kid_mode=True, schedule={"start": "07:00", "end": "20:00"})
    w.clock.now = datetime(2026, 10, 11, 21, 0)
    with w.acting("alex"):
        assert w.refused(lambda: w.sm.guard("play", room)) == "Kid's Room plays from 07:00 to 20:00."
        w.refused(lambda: w.sm.update(room.id, theme_id="forest"))
    assert not w.play(room, "alex")
    assert w.play(room, "chris")  # an admin may, any time
    w.clock.now = datetime(2026, 10, 11, 6, 59)
    with w.acting("alex"):
        w.refused(lambda: w.sm.guard("play", room))
    # Over midnight
    w.own(room, "alex", schedule={"start": "22:00", "end": "06:00"})
    w.clock.now = datetime(2026, 10, 11, 23, 30)
    with w.acting("alex"):
        w.sm.guard("play", room)


def test_end_of_hours_fades_out_what_the_kid_started(w, bus):
    w.switch()
    room = w.channel("Kid's Room", ["media_player.z"])
    w.own(room, "alex", kid_mode=True, schedule={"start": "07:00", "end": "20:00"})
    w.clock.now = datetime(2026, 10, 11, 19, 50)
    assert w.play(room, "alex")
    asyncio.run(w.zones.tick())
    assert room.is_playing  # still within the hours
    w.clock.now = datetime(2026, 10, 11, 20, 0)
    asyncio.run(w.zones.tick())
    channel = w.channels.get_channel(1)
    assert ("fade_out", 30.0) in channel.calls
    assert not room.is_playing
    assert "zone.schedule_ended" in [e.type for e in bus.history()]
    assert [e.data["reason"] for e in bus.history() if e.type == "channel.stopped"] == ["schedule"]
    w.controller.stop_multi.assert_awaited()


def test_end_of_hours_keeps_what_an_admin_started(w):
    w.switch()
    room = w.channel("Kid's Room", ["media_player.z"])
    w.own(room, "alex", kid_mode=True, schedule={"start": "07:00", "end": "20:00"})
    w.clock.now = datetime(2026, 10, 11, 19, 0)
    assert w.play(room, "chris")
    w.clock.now = datetime(2026, 10, 11, 22, 0)
    asyncio.run(w.zones.tick())
    assert room.is_playing
    assert not [c for c in w.channels.get_channel(1).calls if c[0] == "fade_out"]
    assert w.client.get("/api/zones").json()["zones"][room.id]["started_by_admin"] is True


def test_home_assistant_acts_as_an_admin(w):
    """No caller (MQTT, timers, plugins): an admin. It plays a kid's zone after hours and takes speakers."""
    w.switch()
    office = w.channel("Office", ["media_player.x"])
    room = w.channel("Kid's Room", ["media_player.x"])
    w.own(room, "alex", kid_mode=True, schedule={"start": "07:00", "end": "20:00"})
    assert w.play(office, "sam")
    w.clock.now = datetime(2026, 10, 11, 23, 0)
    assert w.play(room)  # no caller
    assert w.sm.get_resolved_speakers(room) == ["media_player.x"] and w.sm.get_resolved_speakers(office) == []
    w.sm.update(room.id, adhoc_selection=sel("media_player.y"))


# --- Speakers in use and homes ----------------------------------------------------------

def test_a_speaker_in_use_cant_be_pulled_into_another_channel(w):
    w.switch()
    office = w.channel("Office", ["media_player.x", "media_player.y"])   # home of x and y
    kitchen = w.channel("Kitchen", ["media_player.x"])
    nap = w.channel("Nap Room", ["media_player.z"])
    assert w.play(office, "sam")
    # Kitchen's only speaker is Office's: refused, with who has it
    with w.acting("alex"):
        assert w.refused(lambda: w.sm.guard("play", kitchen), 409) == "Kitchen speaker is in use by Office."
        # Not added to another channel either
        assert w.refused(lambda: w.sm.update(nap.id, adhoc_selection=sel("media_player.z", "media_player.y")),
                         409) == "Office_C97A is in use by Office."
    # With a free speaker too, it plays on that one only
    with w.acting("chris"):
        w.sm.update(kitchen.id, adhoc_selection=sel("media_player.x", "media_player.z"))
    w.stop(nap)
    assert w.play(kitchen, "alex")
    assert w.sm.get_resolved_speakers(kitchen) == ["media_player.z"]
    assert w.sm.get_resolved_speakers(office) == ["media_player.x", "media_player.y"]
    view = w.client.get("/api/zones").json()["speakers"]
    assert view["media_player.x"]["in_use"] == office.id and view["media_player.z"]["in_use"] == kitchen.id
    # Free again once Office stops
    w.stop(office, "sam")
    assert w.sm.get_resolved_speakers(kitchen) == ["media_player.x", "media_player.z"]


def test_the_home_channel_takes_its_speaker_back(w, bus):
    w.switch()
    kitchen = w.channel("Kitchen", ["media_player.x"])                    # x's home
    office = w.channel("Office", ["media_player.x", "media_player.y"])
    assert w.play(office, "sam")
    assert w.sm.get_resolved_speakers(office) == ["media_player.x", "media_player.y"]
    assert w.play(kitchen, "alex")
    assert w.sm.get_resolved_speakers(kitchen) == ["media_player.x"]
    assert office.is_playing and w.sm.get_resolved_speakers(office) == ["media_player.y"]  # plays on
    taken = [e.data for e in bus.history() if e.type == "zone.speaker_taken"]
    assert taken == [{"channel_id": kitchen.id, "name": "Kitchen", "speaker_id": "media_player.x",
                      "from_channel_id": office.id, "by": "home"}]
    # Office (not x's home) can't take it back as a user; an admin can
    with w.acting("sam"):
        w.refused(lambda: w.sm.update(w.channel("Den", []).id, adhoc_selection=sel("media_player.x")), 409)
    assert w.play(office, "chris")
    assert w.sm.get_resolved_speakers(office) == ["media_player.x", "media_player.y"]


def test_the_next_channel_becomes_home_when_the_home_is_deleted(w, bus):
    w.switch()
    kitchen = w.channel("Kitchen", ["media_player.x"])
    office = w.channel("Office", ["media_player.x"])
    den = w.channel("Den", ["media_player.x"])
    w.client.get("/api/zones")
    assert w.zones.home("media_player.x") == kitchen.id
    with w.acting("sam"):
        w.sm.delete(kitchen.id)
    assert w.zones.home("media_player.x") == office.id
    homes = [e.data for e in bus.history() if e.type == "zone.home_changed"]
    assert homes[-1] == {"speaker_id": "media_player.x", "channel_id": office.id, "name": "Office", "reason": "deleted"}
    # An admin chooses another home
    sam, scsrf = w.sign_in("sam")
    w.api("put", "/api/zones/homes/media_player.x", {"channel_id": den.id}, client=sam, csrf=scsrf, status=403)
    view = w.api("put", "/api/zones/homes/media_player.x", {"channel_id": den.id}).json()
    assert view["speakers"]["media_player.x"]["home"] == den.id
    w.api("put", "/api/zones/homes/media_player.x", {"channel_id": "nope"}, status=409)


# --- Links ---------------------------------------------------------------------------

def test_link_plays_one_thing_with_independent_volume_and_unlink_puts_it_back(w, bus):
    w.switch()
    kitchen = w.channel("Kitchen", ["media_player.x"], theme="storm")
    office = w.channel("Office", ["media_player.y"], theme="forest")
    w.sm.update(kitchen.id, volume=20)
    w.sm.update(office.id, volume=70)
    assert w.play(office, "sam")
    sam, scsrf = w.sign_in("sam")
    body = {"name": "Morning", "channels": [kitchen.id, office.id], "theme_id": "sleigh", "preset_id": "quiet"}
    w.api("post", "/api/links", body, client=sam, csrf=scsrf, status=403)
    link = w.api("post", "/api/links", body).json()
    assert link["name"] == "Morning" and link["channels"] == [kitchen.id, office.id]
    for s in (kitchen, office):
        assert s.theme_id == "sleigh" and s.preset_id == "quiet" and s.is_playing
    assert (kitchen.volume, office.volume) == (20, 70)
    created = [e.data for e in bus.history() if e.type == "link.created"][0]
    assert created["channels"] == [kitchen.id, office.id] and created["theme_id"] == "sleigh"
    # While linked, a user can't change what it plays (an admin can); volume is each channel's own
    with w.acting("sam"):
        assert w.refused(lambda: w.sm.update(office.id, theme_id="storm"), 409) == "Office is linked: Morning."
        w.refused(lambda: w.sm.set_playlist(office.id, "pl"), 409)
        asyncio.run(w.sm.set_volume(office.id, 40))
    assert office.volume == 40 and kitchen.volume == 20
    # Already linked channels can't be in a second link
    w.api("post", "/api/links", {**body, "name": "Again"}, status=409)
    # Change: both follow
    w.api("put", f"/api/links/{link['id']}", {"playlist_id": "pl"})
    assert kitchen.playlist_id == office.playlist_id == "pl"
    # Unlink: back to what each played (Kitchen was stopped, Office played forest)
    w.api("delete", f"/api/links/{link['id']}")
    assert (kitchen.theme_id, kitchen.is_playing, kitchen.playlist_id) == ("storm", False, None)
    assert (office.theme_id, office.is_playing, office.playlist_id) == ("forest", True, None)
    assert "link.removed" in [e.type for e in bus.history()]
    assert w.client.get("/api/zones").json()["links"] == []


def test_leaving_a_link_is_a_per_user_permission(w, bus):
    w.switch()
    kitchen = w.channel("Kitchen", ["media_player.x"])
    room = w.channel("Kid's Room", ["media_player.z"], theme="forest")
    w.own(room, "alex")
    link = w.api("post", "/api/links", {"name": "Morning", "channels": [kitchen.id, room.id],
                                        "theme_id": "sleigh"}).json()
    alex, acsrf = w.sign_in("alex")
    leave = f"/api/links/{link['id']}/leave"
    w.api("post", leave, {"channel_id": room.id}, client=alex, csrf=acsrf, status=403)
    users = {u["username"]: u for u in w.client.get("/api/users").json()["users"]}
    assert users["alex"]["can_leave_link"] is False and users["chris"]["can_leave_link"] is True
    # An admin lets alex leave links (Users & devices: Can leave a link)
    r = w.api("put", f"/api/users/{w.ids['alex']}", {"can_leave_link": True})
    assert r.json()["user"]["can_leave_link"] is True
    w.api("post", leave, {"channel_id": kitchen.id}, client=alex, csrf=acsrf, status=403)  # not alex's
    view = w.api("post", leave, {"channel_id": room.id}, client=alex, csrf=acsrf).json()
    assert view["left"] == [room.id]
    assert room.theme_id == "forest" and not room.is_playing  # back to what it played
    assert kitchen.theme_id == "sleigh" and kitchen.is_playing
    left = [e.data for e in bus.history() if e.type == "link.left"]
    assert left == [{"link_id": link["id"], "name": "Morning", "channel_id": room.id}]
    # ... and rejoin
    w.api("post", f"/api/links/{link['id']}/rejoin", {"channel_id": room.id}, client=alex, csrf=acsrf)
    assert room.theme_id == "sleigh" and room.is_playing
    assert [e.data["by"] for e in bus.history() if e.type == "link.rejoined"] == ["owner"]


def test_an_admin_pulls_a_channel_back_into_its_link(w, bus):
    w.switch()
    kitchen = w.channel("Kitchen", ["media_player.x"])
    room = w.channel("Kid's Room", ["media_player.z"], theme="forest")
    w.own(room, "alex")
    w.api("put", f"/api/users/{w.ids['alex']}", {"can_leave_link": True})
    link = w.api("post", "/api/links", {"name": "Morning", "channels": [kitchen.id, room.id],
                                        "theme_id": "sleigh"}).json()
    alex, acsrf = w.sign_in("alex")
    w.api("post", f"/api/links/{link['id']}/leave", {"channel_id": room.id}, client=alex, csrf=acsrf)
    w.api("put", f"/api/users/{w.ids['alex']}", {"can_leave_link": False})
    w.api("post", f"/api/links/{link['id']}/rejoin", {"channel_id": room.id}, client=alex, csrf=acsrf, status=403)
    view = w.api("post", f"/api/links/{link['id']}/rejoin", {"channel_id": room.id}).json()
    assert view["left"] == [] and room.theme_id == "sleigh" and room.is_playing
    assert [e.data["by"] for e in bus.history() if e.type == "link.rejoined"] == ["admin"]


def test_a_deleted_channel_leaves_its_link(w):
    w.switch()
    a, b, c = (w.channel(n, ["media_player.x"]) for n in ("A", "B", "C"))
    link = w.api("post", "/api/links", {"name": "All", "channels": [a.id, b.id, c.id], "theme_id": "storm"}).json()
    w.sm.delete(b.id)
    assert w.client.get("/api/zones").json()["links"][0]["channels"] == [a.id, c.id]
    w.api("post", "/api/links", {"name": "One", "channels": [a.id], "theme_id": "storm"}, status=409)
    assert link["id"] in w.zones.data["links"]
