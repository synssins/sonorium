"""
G6: a speaker's own volume and mute, live (core/speaker_live.py and
PUT /api/speakers/{id}/volume, /muted).
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.speaker_live", "sonorium.core.speaker_settings") as mods:
        m.__dict__.update(mods.__dict__)
        yield


class FakeHA:
    def __init__(self, ok=True):
        self.ok = ok
        self.calls = []

    async def set_volume_multi(self, ids, level):
        self.calls.append(("volume", list(ids), level))
        return {i: self.ok for i in ids}

    async def mute(self, entity_id, mute=True):
        self.calls.append(("mute", entity_id, mute))
        return self.ok


class FakeRouter:
    """Like network/router.SpeakerRouter: net:* to the network, the rest to Home Assistant."""

    def __init__(self, ha):
        self.ha_controller = ha
        self.net_calls = []

    async def set_volume_multi(self, ids, level):
        net = [i for i in ids if i.startswith("net:")]
        out = {}
        if net:
            self.net_calls.append((net, level))
            out.update({i: True for i in net})
        rest = [i for i in ids if not i.startswith("net:")]
        if rest:
            out.update(await self.ha_controller.set_volume_multi(rest, level))
        return out


def world(play_via=None, offset=0, ha_ok=True):
    ha = FakeHA(ha_ok)
    router = FakeRouter(ha)
    settings = {"media_player.office": {"volume_offset": offset}}
    outputs = m.speaker_settings.SpeakerOutputs(router, lambda: settings,
                                                lambda sid: (play_via or {}).get(sid, sid))
    return SimpleNamespace(ha=ha, router=router, live=m.speaker_live.SpeakerLive(lambda: outputs))


def run(coro):
    return asyncio.run(coro)


def test_volume_goes_to_the_speaker_without_the_offset():
    w = world(offset=-10)
    assert run(w.live.set_volume("media_player.office", 40)) is True
    assert w.ha.calls == [("volume", ["media_player.office"], 0.4)]  # the speaker's own level, no offset
    assert w.live.state("media_player.office") == {"volume": 40}
    assert w.live.state("media_player.other") == {}


def test_home_assistant_mute():
    w = world()
    assert run(w.live.set_muted("media_player.office", True)) is True
    assert w.ha.calls == [("mute", "media_player.office", True)]
    assert w.live.state("media_player.office") == {"muted": True}
    assert run(w.live.set_muted("media_player.office", False)) is True
    assert w.live.state("media_player.office") == {"muted": False}


def test_a_failed_command_is_reported_and_not_remembered():
    w = world(ha_ok=False)
    assert run(w.live.set_volume("media_player.office", 40)) is False
    assert run(w.live.set_muted("media_player.office", True)) is False
    assert w.live.state("media_player.office") == {}
    assert run(m.speaker_live.SpeakerLive(lambda: None).set_volume("media_player.office", 5)) is False


def test_network_speakers_mute_by_volume_and_get_it_back():
    w = world()
    sid = "net:dlna:abc"
    run(w.live.set_volume(sid, 35))
    run(w.live.set_muted(sid, True))
    assert w.router.net_calls == [([sid], 0.35), ([sid], 0.0)]
    run(w.live.set_volume(sid, 60))  # while muted: kept for unmuting, nothing sent
    assert len(w.router.net_calls) == 2 and w.live.state(sid) == {"volume": 60, "muted": True}
    run(w.live.set_muted(sid, False))
    assert w.router.net_calls[-1] == ([sid], 0.6) and w.live.state(sid)["muted"] is False


def test_a_merged_speaker_follows_play_via():
    w = world(play_via={"media_player.office": "net:sonos:xyz"})
    run(w.live.set_muted("media_player.office", True))  # played through the network: mute by volume
    assert w.router.net_calls == [(["net:sonos:xyz"], 0.0)] and w.ha.calls == []


# --- The routes ---

@pytest.fixture
def api():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    with addon_modules("sonorium.web.api_v2", "sonorium.core.speaker_settings") as mods:
        w = world()
        outputs = w.live._controller()
        manager = MagicMock()
        manager.media_controller = outputs
        registry = MagicMock()
        registry.get_speaker.side_effect = lambda sid: None if sid == "media_player.gone" else object()
        speaker = MagicMock(entity_id="media_player.office")
        speaker.to_dict.return_value = {"entity_id": "media_player.office", "name": "Office"}
        registry.hierarchy.get_all_speakers.return_value = [speaker]
        app = FastAPI()
        app.include_router(mods.api_v2.create_api_router(session_manager=manager, group_manager=MagicMock(),
                                                         ha_registry=registry, state_store=MagicMock()))
        w.client = TestClient(app)
        yield w


def test_speaker_volume_and_mute_routes(api):
    c = api.client
    assert c.get("/api/speakers").json() == [{"entity_id": "media_player.office", "name": "Office"}]
    r = c.put("/api/speakers/media_player.office/volume", json={"volume": 25})
    assert r.status_code == 200 and r.json() == {"speaker_id": "media_player.office", "volume": 25}
    r = c.put("/api/speakers/media_player.office/muted", json={"muted": True})
    assert r.json() == {"speaker_id": "media_player.office", "muted": True}
    assert c.get("/api/speakers").json()[0] == {"entity_id": "media_player.office", "name": "Office",
                                                 "volume": 25, "muted": True}
    assert c.put("/api/speakers/media_player.office/volume", json={"volume": 101}).status_code == 422
    assert c.put("/api/speakers/media_player.office/muted", json={}).status_code == 422
    assert c.put("/api/speakers/media_player.gone/volume", json={"volume": 5}).status_code == 404
    api.ha.ok = False
    assert c.put("/api/speakers/media_player.office/muted", json={"muted": False}).status_code == 502
