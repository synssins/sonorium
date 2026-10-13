"""
The cue timeline, service side (core/timeline.py): what the mixer announces
becomes cues with each speaker's audible time and the objects' plugin
attributes, delivered to plugins and outside programs for the channels they
asked for; cues are confirmed, called off (stopped, paused, theme changed,
not played) and ended. Two unrelated plugins (a lamp and a wall panel) use
the same timeline: nothing in it knows what either does.
"""
import asyncio
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.timeline", "sonorium.plugins.events", "sonorium.plugins.host",
                       "sonorium.plugins.base", "sonorium.plugins.compat", "sonorium.plugins.services",
                       "sonorium.core.speaker_settings", "sonorium.web.plugin_framework_api",
                       "sonorium.auth.permissions") as mods:
        m.__dict__.update(mods.__dict__)
        yield


class FakeSessions:
    """Two channels (A on channel 1, B on channel 2), a personal one (3); speakers with and without Sync."""

    def __init__(self, folder):
        self.folder = folder
        speakers = {
            "media_player.living": SimpleNamespace(type="cast", area_id="living_room"),
            "media_player.kitchen": SimpleNamespace(type="cast", area_id="kitchen"),
            "output:host:1": SimpleNamespace(type="output", area_id="office"),
            "media_player.near": SimpleNamespace(type="esphome", area_id="hall"),
        }
        self.registry = SimpleNamespace(get_speaker=speakers.get)
        self.state = SimpleNamespace(settings=SimpleNamespace(speaker_settings={
            "media_player.living": {"sync": 4200}, "media_player.near": {"sync": 0}}))
        self.sessions = {
            "chA": SimpleNamespace(id="chA", name="Storm", theme_id="storm", is_playing=True, speakers_muted=False,
                                   owner=None, sequence_id=None, preset_id="p1",
                                   speakers=["media_player.living", "media_player.kitchen", "output:host:1"]),
            "chB": SimpleNamespace(id="chB", name="Hall", theme_id="storm", is_playing=True, speakers_muted=False,
                                   owner=None, sequence_id=None, preset_id=None, speakers=["media_player.near"]),
            "mine": SimpleNamespace(id="mine", name="Mine", theme_id="storm", is_playing=True, speakers_muted=False,
                                    owner="user:ann", sequence_id=None, preset_id=None, speakers=[]),
        }
        self._session_channels = {"chA": 1, "chB": 2, "mine": 3}
        views = {"Rain": SimpleNamespace(volume=0.4, is_enabled=True),
                 "Thunder/Close": SimpleNamespace(volume=0.8, is_enabled=False)}
        self.channel = SimpleNamespace(_theme_stream=SimpleNamespace(_views=views))

    def get_any(self, session_id):
        return self.sessions.get(session_id)

    def get_resolved_speakers(self, session):
        return list(session.speakers)

    def theme_folder(self, theme_id):
        return self.folder

    def _channel_for(self, session):
        return self.channel

    def is_listened(self, session):
        return False


class FakeAttributes:
    """Two plugins' values on the storm theme's objects."""

    def get(self, ref):
        if ref["kind"] == "track" and ref["id"] == "Thunder/Close":
            return {"lamp": {"cue": "flash"}, "panel": {"caption": "Thunder"}}
        if ref["kind"] == "file" and ref["id"] == "Thunder/Close":
            return {"lamp": {"colour": "#cfe0ff"}}
        if ref["kind"] == "theme":
            return {"panel": {"artwork": "storm.png"}}
        return {}


class FakeOutputs:
    def latency(self, session_id, speaker_id):
        return 0.12 if speaker_id == "output:host:1" else None


def _stream(folder, name="Thunder/Close", group="Thunder", volume=0.8):
    meta = SimpleNamespace(group=group, path=Path(folder) / f"{name}.ogg")
    return SimpleNamespace(instance=SimpleNamespace(name=name, meta=meta, volume=volume))


class World:
    def __init__(self, tmp_path):
        self.folder = tmp_path / "Storm"
        self.bus = m.events.EventBus()
        self.timeline = m.timeline.Timeline()
        m.timeline.set_timeline(self.timeline)
        self.sessions = FakeSessions(self.folder)
        self.host = m.host.PluginHost(tmp_path / "config", bus=self.bus, session_manager=self.sessions)
        self.got = {"lamp": [], "panel": []}

        def plugin(pid):
            cls = type(pid.title(), (m.base.BasePlugin,), {"id": pid, "name": pid.title()})
            p = cls(tmp_path / pid, {})
            p._host, p._manifest = self.host, {"api_version": "1.2"}
            p.enabled = True
            return p
        self.lamp, self.panel = plugin("lamp"), plugin("panel")

    async def start(self):
        await self.host.start()
        self.timeline.attach(attributes=FakeAttributes(), outputs=FakeOutputs())
        # The lamp follows channel A only; the panel every channel. Neither is known to core.
        self.lamp.timeline.subscribe(lambda e: self.got["lamp"].append(e), channels=["chA"])

        async def panel(event):
            self.got["panel"].append(event)
        self.panel.timeline.subscribe(panel)

    def from_mixer(self, work):
        """Run as the mixing thread does: off the event loop."""
        out = {}
        thread = threading.Thread(target=lambda: out.setdefault("result", work()))
        thread.start()
        thread.join()
        return out.get("result")

    async def settle(self, seconds=0.05):
        await asyncio.sleep(seconds)
        await self.timeline.bus.drain()
        await self.bus.drain()

    def types(self, who):
        return [(e.type, e.data["cue_id"]) for e in self.got[who]]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    yield w
    m.timeline.set_timeline(m.timeline.Timeline())


def test_a_cue_carries_audible_times_attributes_and_reaches_who_asked(world):
    async def main():
        await world.start()
        a = world.timeline.for_channel(1, "storm")
        b = world.timeline.for_channel(2, "storm")
        before = time.time()
        world.from_mixer(lambda: a.announce(_stream(world.folder), "intermittent", "Thunder/Close", 44100 * 2, 0))
        world.from_mixer(lambda: b.announce(_stream(world.folder, "Rain", None, 0.4), "loop", "Rain", 44100, 0))
        await world.settle()
        assert [t for t, _ in world.types("lamp")] == ["cue.scheduled"]  # channel A only
        assert [e.data["channel_id"] for e in world.got["panel"]] == ["chA", "chB"]
        cue = world.got["lamp"][0].data
        assert cue["start"] == "intermittent" and cue["track"] == "Thunder/Close" and cue["group"] == "Thunder"
        assert cue["file"] == "Thunder/Close.ogg" and cue["duration_ms"] == 2000 and cue["gain"] == 0.8
        assert cue["confirmed"] is True and cue["lead_ms"] == 0 and before - 0.001 <= cue["mixer_at"] <= time.time() + 0.001  # milliseconds
        speakers = {s["speaker_id"]: s for s in cue["speakers"]}
        assert speakers["media_player.living"]["delay_source"] == "setting"
        assert speakers["media_player.living"]["audible_at"] == pytest.approx(cue["mixer_at"] + 4.2, abs=0.002)
        assert speakers["media_player.kitchen"]["delay_source"] == "estimate"
        assert speakers["media_player.kitchen"]["sync_ms"] == 5000 and speakers["media_player.kitchen"]["area"] == "kitchen"
        assert speakers["output:host:1"]["delay_source"] == "device" and speakers["output:host:1"]["sync_ms"] == 120
        assert cue["attributes"] == {"theme": {"panel": {"artwork": "storm.png"}},
                                     "track": {"lamp": {"cue": "flash"}, "panel": {"caption": "Thunder"}},
                                     "file": {"lamp": {"colour": "#cfe0ff"}}}
        assert world.got["panel"][1].data["file"] == "Rain.ogg" and world.got["panel"][1].data["group"] is None
        # Pending until heard and ended
        assert {c["cue_id"] for c in world.lamp.timeline.pending()} == {e.data["cue_id"] for e in world.got["panel"]}
    run(main())


def test_an_announced_play_is_confirmed_or_called_off(world, monkeypatch):
    monkeypatch.setattr(m.timeline, "CONFIRM_GRACE_SECONDS", 0.05)

    async def main():
        await world.start()
        a = world.timeline.for_channel(1, "storm")
        stream = _stream(world.folder)
        kept = world.from_mixer(lambda: a.announce(stream, "intermittent", "Thunder/Close", 44100, 2205))
        dropped = world.from_mixer(lambda: a.announce(stream, "intermittent", "Thunder/Close", 44100, 2205))
        skipped = world.from_mixer(lambda: a.announce(stream, "intermittent", "Thunder/Close", 44100, 2205))
        await world.settle(0.01)
        first = world.got["lamp"][0].data
        assert first["confirmed"] is False and first["lead_ms"] == 50
        world.from_mixer(lambda: a.confirm(kept))
        world.from_mixer(lambda: a.cancel(skipped, "skipped"))
        await world.settle(0.2)
        events = world.types("lamp")
        assert ("cue.cancelled", dropped) in events and ("cue.cancelled", skipped) in events
        assert ("cue.cancelled", kept) not in events
        reasons = {e.data["cue_id"]: e.data["reason"] for e in world.got["lamp"] if e.type == "cue.cancelled"}
        assert reasons == {dropped: "not_played", skipped: "skipped"}
        # Called off as late, and it starts after all: a new cue, confirmed, as it starts
        world.from_mixer(lambda: a.confirm(dropped))
        await world.settle()
        late = world.got["lamp"][-1]
        assert late.type == "cue.scheduled" and late.data["cue_id"] != dropped and late.data["confirmed"] is True
    run(main())


def test_stopping_cancels_what_isnt_heard_yet_and_ends_what_is(world):
    async def main():
        await world.start()
        a = world.timeline.for_channel(1, "storm")
        b = world.timeline.for_channel(2, "storm")
        # channel B's speaker plays at once (Sync 0): heard, and it ends by itself after its 50 ms
        short = world.from_mixer(lambda: b.announce(_stream(world.folder, "Rain", None), "loop", "Rain", 2205, 0))
        await world.settle(0.2)
        assert ("cue.ended", short) in world.types("panel")
        # A's first speaker hears it in 120 ms: paused before that, it is called off
        coming = world.from_mixer(lambda: a.announce(_stream(world.folder), "intermittent", "Thunder/Close", 44100, 0))
        world.bus.publish("channel.stopped", {"channel_id": "chA", "name": "Storm", "reason": "paused"})
        await world.settle()
        cancelled = [e.data for e in world.got["lamp"] if e.type == "cue.cancelled"]
        assert cancelled == [{"cue_id": coming, "channel_id": "chA", "reason": "paused"}]
        assert world.lamp.timeline.pending() == []
        # already heard on one speaker when it stops: it ended there
        heard = world.from_mixer(lambda: a.announce(_stream(world.folder), "intermittent", "Thunder/Close", 44100, 0))
        await world.settle(0.2)
        world.bus.publish("channel.stopped", {"channel_id": "chA", "name": "Storm", "reason": "stopped"})
        await world.settle()
        assert world.got["lamp"][-1].type == "cue.ended" and world.got["lamp"][-1].data == {
            "cue_id": heard, "channel_id": "chA", "reason": "stopped"}
    run(main())


def test_changes_of_theme_preset_and_step_are_cues_with_the_new_levels(world):
    async def main():
        await world.start()
        a = world.timeline.for_channel(1, "storm")
        ahead = world.from_mixer(lambda: a.announce(_stream(world.folder), "intermittent", "Thunder/Close",
                                                    44100, 44100 * 3))
        await world.settle()
        world.sessions.sessions["chA"].theme_id = "rain"
        world.bus.publish("channel.changed", {"channel_id": "chA", "name": "Storm", "changes": {"theme_id": "rain"}})
        world.bus.publish("channel.changed", {"channel_id": "chA", "name": "Storm", "changes": {"preset_id": "p2"}})
        world.bus.publish("sequence.step", {"channel_id": "chA", "name": "Storm", "theme_id": "rain",
                                            "sequence_id": "s", "step": 2, "steps": 3, "preset_id": "p3"})
        await world.settle()
        data = [e.data for e in world.got["lamp"]]
        assert {"cue_id": ahead, "channel_id": "chA", "reason": "theme_changed"} in data
        changes = [d for d in data if d.get("start") in ("theme", "preset", "step")]
        assert [d["start"] for d in changes] == ["theme", "preset", "step"]
        assert changes[0]["theme_id"] == "rain"
        assert changes[0]["levels"] == {"Rain": {"volume": 0.4, "on": True}, "Thunder/Close": {"volume": 0.8, "on": False}}
        assert changes[1]["preset_id"] == "p2" and changes[2]["step"] == 2 and changes[2]["preset_id"] == "p3"
        world.bus.publish("sound.started", {"channel_id": "chB", "name": "Hall", "sound_id": "s1", "label": "Bell"})
        await world.settle()
        sound = [e.data for e in world.got["panel"] if e.type == "cue.scheduled"][-1]
        assert sound["start"] == "sound" and sound["sound_id"] == "s1" and sound["label"] == "Bell"
        assert any(c["cue_id"] == sound["cue_id"] for c in world.panel.timeline.pending())  # until the sound ends
        world.bus.publish("sound.ended", {"channel_id": "chB", "name": "Hall", "sound_id": "s1", "reason": "finished"})
        await world.settle()
        assert world.got["panel"][-1].type == "cue.ended" and world.got["panel"][-1].data["cue_id"] == sound["cue_id"]
    run(main())


def test_a_personal_channels_cues_reach_no_plugin(world):
    async def main():
        await world.start()
        mine = world.timeline.for_channel(3, "storm")
        got = []
        world.timeline.subscribe(lambda e: got.append(e), audience="user:ann")
        world.from_mixer(lambda: mine.announce(_stream(world.folder), "loop", "Rain", 44100, 0))
        await world.settle()
        assert world.got["panel"] == [] and len(got) == 1  # only its account's stream
        assert world.panel.timeline.pending() == []
    run(main())


def test_disabling_a_plugin_ends_its_subscriptions_and_nobody_listening_costs_nothing(world):
    async def main():
        await world.start()
        owners = lambda: sorted(s.owner for s in world.timeline.bus.subscriptions())  # noqa: E731
        assert owners() == ["lamp", "panel"]
        world.host.plugin_disabled(world.lamp)
        assert owners() == ["panel"]
        world.host.plugin_removed("panel")
        assert owners() == [] and world.timeline.listening is False
        a = world.timeline.for_channel(1, "storm")
        assert world.from_mixer(lambda: a.announce(_stream(world.folder), "loop", "Rain", 44100, 0)) is None
    run(main())


def test_the_timeline_is_a_capability_with_a_documented_catalogue():
    assert "timeline" in m.compat.CAPABILITIES and m.compat.CAPABILITIES["timeline"][0] == "1.2"
    assert m.compat.API_VERSION == "1.2"
    for name, entry in m.timeline.CATALOGUE.items():
        assert name.startswith("cue.") and entry["description"] and entry["fields"]
    assert "timeline_lookahead" in __import__("sonorium.playback_values", fromlist=["DEFAULTS"]).DEFAULTS


def test_http_timeline_needs_the_event_streams_sign_in(world):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    manager = SimpleNamespace(get_plugin=lambda pid: None)
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(manager, world.host))
    with TestClient(app) as client:
        client.portal.call(world.start)
        assert client.get("/api/timeline").status_code == 401
        assert client.get("/api/timeline/stream").status_code == 401
        token = client.post("/api/events/token").json()["token"]
        body = client.get("/api/timeline", headers={"Authorization": f"Bearer {token}"}).json()
        assert set(body["events"]) == {"cue.scheduled", "cue.cancelled", "cue.ended"}
        assert body["look_ahead_ms"] == 500 and body["pending"] == []
    routes = m.permissions.ROUTES if hasattr(m.permissions, "ROUTES") else None
    if routes is not None:
        assert any("/api/timeline/stream" in str(r) for r in routes)
