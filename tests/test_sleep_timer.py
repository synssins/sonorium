"""
Sleep timers on channels (core/sleep_timer.py): set, change and cancel over the
API, the channel stops (the usual stop, reason "sleep") when the time runs out,
the timer goes when the channel stops another way, and a restart keeps it only
on the channels that play again.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient

from playback_helpers import addon_modules  # noqa: E402
from test_sequences_playlists import build  # noqa: E402
import test_sequences_playlists as runner_tests  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.session_manager", "sonorium.core.state", "sonorium.core.playback_runner",
                       "sonorium.core.sequences", "sonorium.core.playlists", "sonorium.core.sleep_timer",
                       "sonorium.web.api_v2") as mods:
        m.__dict__.update(mods.__dict__)
        runner_tests.m.__dict__.update(mods.__dict__)
        yield


class Clock:
    def __init__(self):
        self.now = 1_800_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def t(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    t = build(tmp_path, crossfade=0)
    t.manager.registry.get_speaker_name = lambda speaker_id: "Desk"
    t.clock = Clock()
    t.manager.sleep._clock = t.clock
    t.events = []
    t.manager.publish = lambda event_type, session, **data: t.events.append((event_type, data))
    app = FastAPI()
    app.include_router(m.api_v2.create_api_router(
        session_manager=t.manager, group_manager=MagicMock(), ha_registry=MagicMock(), state_store=t.store))
    t.client = TestClient(app)
    t.sid = t.session.id
    return t


def tick(t):
    async def main():
        await t.manager.sleep.tick()
        for _ in range(5):
            await asyncio.sleep(0)  # the stop runs in the background
    asyncio.run(main())


def sleep_events(t):
    return [(d["reason"], d["seconds_left"]) for kind, d in t.events if kind == "channel.sleep_timer"]


def saved(t):
    return json.loads(t.store.state_file.read_text(encoding="utf-8"))["sessions"][t.sid].get("sleep_until")


def test_set_change_and_cancel_over_the_api(t):
    c = t.client
    url = f"/api/sessions/{t.sid}/sleep"
    assert c.put(url, json={"minutes": 30}).status_code == 409  # not playing
    runner_tests.run(t.manager.play(t.sid))
    for bad in ({"minutes": 0}, {"minutes": 241}, {"minutes": -5}, {"minutes": "30"}, {"minutes": True}, {}):
        r = c.put(url, json=bad)
        assert r.status_code == 400 and "minutes" in r.json()["detail"], bad
    assert c.put(url, content="[1]").status_code == 400
    assert c.put("/api/sessions/nope/sleep", json={"minutes": 5}).status_code == 404
    assert c.get(url).json() == {"channel_id": t.sid, "sleep_until": None, "seconds_left": None}

    r = c.put(url, json={"minutes": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["seconds_left"] == 1800 and body["sleep_until"].startswith("2027-01-15T08:")
    assert saved(t) == body["sleep_until"]  # survives a reload: kept with the channel
    channel = c.get(f"/api/sessions/{t.sid}").json()
    assert channel["sleep_until"] == body["sleep_until"] and channel["sleep_seconds_left"] == 1800

    t.clock.now += 600
    assert c.get(url).json()["seconds_left"] == 1200
    assert c.put(url, json={"minutes": 240}).json()["seconds_left"] == 240 * 60  # changed: from now
    assert c.put(url, json={"minutes": 0.5}).json()["seconds_left"] == 30  # custom, any length

    r = c.delete(url)
    assert r.json() == {"cancelled": True, "channel_id": t.sid, "sleep_until": None, "seconds_left": None}
    assert c.delete(url).json()["cancelled"] is False
    assert c.delete("/api/sessions/nope/sleep").status_code == 404
    assert saved(t) is None and t.session.is_playing
    assert sleep_events(t) == [("set", 1800), ("set", 14400), ("set", 30), ("cancelled", None)]


def test_the_channel_stops_when_the_time_runs_out(t):
    runner_tests.run(t.manager.play(t.sid))
    t.manager.sleep.set(t.sid, 45)
    t.clock.now += 45 * 60 - 1
    tick(t)
    assert t.session.is_playing and t.session.sleep_until
    t.clock.now += 1
    tick(t)
    assert not t.session.is_playing and t.session.sleep_until is None
    assert ("channel.stopped", {"reason": "sleep"}) in t.events
    assert sleep_events(t)[-1] == ("ended", None)
    t.controller.stop_multi.assert_awaited()  # the usual stop: the speakers are stopped


def test_stopping_the_channel_drops_its_timer(t):
    runner_tests.run(t.manager.play(t.sid))
    t.manager.sleep.set(t.sid, 10)
    runner_tests.run(t.manager.stop(t.sid))
    tick(t)
    assert t.session.sleep_until is None and sleep_events(t)[-1] == ("stopped", None)
    runner_tests.run(t.manager.play(t.sid))  # played again: no timer
    t.clock.now += 3600
    tick(t)
    assert t.session.is_playing


def test_a_restart_keeps_the_timer_only_on_channels_that_play_again(t):
    sleep = t.manager.sleep
    other = t.manager.create(theme_id="tavern")
    expired = t.manager.create(theme_id="forest")
    t.session.sleep_until = m.sleep_timer._iso(t.clock.now + 600)
    other.sleep_until = m.sleep_timer._iso(t.clock.now + 600)
    expired.sleep_until = m.sleep_timer._iso(t.clock.now - 5)
    gone = sleep.after_start([t.sid, expired.id])
    assert gone == {expired.id}  # its time ran out during the restart: not played again
    assert t.session.sleep_until and other.sleep_until is None and expired.sleep_until is None
    tick(t)  # not playing yet (the resume is still trying): kept
    assert t.session.sleep_until
    runner_tests.run(t.manager.play(t.sid))
    sleep.resume_done()
    assert t.manager.sleep.status(t.session)["seconds_left"] == 600  # the same end time
    t.clock.now += 600
    tick(t)
    assert not t.session.is_playing and t.session.sleep_until is None


def test_a_channel_that_does_not_play_again_loses_its_timer(t):
    t.session.sleep_until = m.sleep_timer._iso(t.clock.now + 600)
    t.manager.sleep.after_start([t.sid])
    t.manager.sleep.resume_done()  # gave up
    tick(t)
    assert t.session.sleep_until is None
