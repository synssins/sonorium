"""
Max channels: a channel that would go over the limit is refused (it never
shows Playing without a channel of its own), from the API (409), MQTT/Home
Assistant, plugins and anything else that starts a channel.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from playback_helpers import FakeChannel, addon_modules
from test_sequences_playlists import build  # noqa: E402  (same channel set-up)
import test_sequences_playlists as runner_tests  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.session_manager", "sonorium.core.state", "sonorium.core.playback_runner",
                       "sonorium.core.sequences", "sonorium.core.playlists", "sonorium.plugins.events",
                       "sonorium.plugins.actions") as mods:
        m.__dict__.update(mods.__dict__)
        runner_tests.m.__dict__.update(mods.__dict__)
        yield


class LimitedChannels:
    """max_channels channels; a free one is one nothing plays on."""

    def __init__(self, max_channels):
        self.max_channels = max_channels
        self.channels = [FakeChannel(i + 1) for i in range(max_channels)]

    def get_channel(self, channel_id):
        return next((c for c in self.channels if c.id == channel_id), None)

    def get_available_channel(self):
        return next((c for c in self.channels if not c.playing), None)


def run(coro):
    async def main():
        result = await coro
        await asyncio.sleep(0)
        return result
    return asyncio.run(main())


def limited(tmp_path, max_channels):
    t = build(tmp_path)
    t.manager.channel_manager = LimitedChannels(max_channels)
    t.second = t.manager.create(theme_id="tavern",
                                adhoc_selection=m.state.SpeakerSelection(include_speakers=["media_player.x"]))
    return t


MESSAGE = "1 channel is already playing (the most at once is 1). Stop it first."


def test_starting_over_the_limit_is_refused(tmp_path):
    """What MQTT, Home Assistant, Zones and the playlists' timers call: play() says no and nothing plays."""
    t = limited(tmp_path, 1)
    assert run(t.manager.play(t.session.id)) is True
    assert t.manager.channel_limit_refusal(t.second) == MESSAGE
    t.second.is_playing = True  # e.g. marked optimistically by a caller
    assert run(t.manager.play(t.second.id)) is False
    assert t.second.is_playing is False and t.manager.get_session_channel(t.second.id) is None
    assert t.session.is_playing is True
    # the channel already playing may play again (it has its channel)
    assert t.manager.channel_limit_refusal(t.session) is None
    # once one stops, the other starts
    run(t.manager.stop(t.session.id))
    assert run(t.manager.play(t.second.id)) is True and t.second.is_playing


def test_message_names_the_limit(tmp_path):
    t = limited(tmp_path, 2)
    third = t.manager.create(theme_id="storm",
                             adhoc_selection=m.state.SpeakerSelection(include_speakers=["media_player.x"]))
    run(t.manager.play(t.session.id))
    run(t.manager.play(t.second.id))
    assert t.manager.channel_limit_refusal(third) == \
        "2 channels are already playing (the most at once is 2). Stop one first."


def test_play_route_answers_409_and_the_channel_stays_stopped(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import importlib
    api_v2 = importlib.import_module("sonorium.web.api_v2")
    t = limited(tmp_path, 1)
    t.manager.registry.get_speaker_name = lambda speaker_id: "Desk"
    app = FastAPI()
    app.include_router(api_v2.create_api_router(
        session_manager=t.manager, group_manager=MagicMock(), ha_registry=MagicMock(), state_store=t.store))
    client = TestClient(app)
    assert client.post(f"/api/sessions/{t.session.id}/play").status_code == 200
    r = client.post(f"/api/sessions/{t.second.id}/play")
    assert r.status_code == 409 and r.json()["detail"] == MESSAGE
    assert t.second.is_playing is False


def test_plugin_start_channel_is_refused(tmp_path):
    t = limited(tmp_path, 1)
    actions = m.actions.Actions(t.manager, t.store)
    run(t.manager.play(t.session.id))
    with pytest.raises(m.actions.ActionError) as refused:
        run(actions.start_channel(t.second.id))
    assert refused.value.code == "conflict" and str(refused.value.message) == MESSAGE
    assert t.second.is_playing is False
