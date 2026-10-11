"""
The audio output registry (core/audio_outputs.py): a plugin registers an
output type, a channel is sent to one of its devices, the sink receives the
channel's audio (silenced while the speakers are muted), the choice is
remembered and made again when the channel starts.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

from playback_helpers import addon_modules  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.audio_outputs", "sonorium.core.channel") as mods:
        m.__dict__.update(mods.__dict__)
        yield


class Store:
    def __init__(self):
        self.main = SimpleNamespace(data={}, saves=0)
        self.main.section = lambda name, kind=dict: self.main.data.setdefault(name, kind())

        def save():
            self.main.saves += 1
        self.main.save = save


class Sessions:
    def __init__(self):
        self.session = SimpleNamespace(id="den", name="Den", is_playing=False)
        self.channel = None
        self.holds = []
        self.released = []

    def get(self, sid):
        return self.session if sid == "den" else None

    def channel_of(self, sid):
        return self.channel

    def run_mix(self, sid):
        self.channel = m.channel.Channel(id=1)
        self.channel.run_silent()
        self.channel.fade_in(0)  # the theme's level (silence here, but at full level)
        return self.channel

    def add_mix_hold(self, predicate):
        self.holds.append(predicate)

    async def release_unused(self, sid, why):
        if not any(h(sid) for h in self.holds):
            self.released.append(sid)


def wait_for(predicate, seconds=3.0):
    end = time.time() + seconds
    while not predicate() and time.time() < end:
        time.sleep(0.02)
    return predicate()


def test_register_discover_attach_detach():
    sessions, store = Sessions(), Store()
    outputs = m.audio_outputs.AudioOutputs(sessions, store, rate=44100)
    capture = m.audio_outputs.CaptureOutput()
    outputs.register_type("hostaudio", capture)
    assert outputs.types() == [{"id": "capture", "title": "Capture (test)", "plugin_id": "hostaudio",
                                "available": True, "message": ""}]
    assert asyncio.run(outputs.discover("capture")) == [{"id": "memory", "name": "Memory"}]

    info = outputs.attach("den", "capture", "memory")
    assert info["running"] and sessions.channel is not None   # the mix was started for it
    assert any(h("den") for h in sessions.holds)              # and is held while it plays there
    assert wait_for(lambda: len(capture.blocks["memory"]) > 5)
    assert store.main.data["audio_outputs"]["den"][0]["device"] == "memory"

    assert asyncio.run(outputs.detach("den", info["attachment_id"]))
    assert wait_for(lambda: capture.sinks[0].closed)
    assert sessions.released == ["den"]
    assert "den" not in store.main.data["audio_outputs"]
    sessions.channel.stop()


def test_muted_speakers_silence_the_output_and_restart_reattaches():
    sessions, store = Sessions(), Store()
    outputs = m.audio_outputs.AudioOutputs(sessions, store)
    capture = m.audio_outputs.CaptureOutput()
    outputs.register_type("p", capture)
    outputs.attach("den", "capture", "memory")

    # A sound over the silent mix makes the blocks non-zero, until the speakers are muted
    from sonorium.core.overlay import ChannelOverlay, Sound
    sessions.channel.overlay = ChannelOverlay(44100)
    sessions.channel.overlay.add(Sound("s", np.full(44100 * 5, 5000.0, dtype=np.float32)))
    assert wait_for(lambda: any(np.abs(b).max() > 0 for b in capture.blocks["memory"]))
    sessions.channel.speakers_muted = True
    time.sleep(0.2)
    count = len(capture.blocks["memory"])
    assert wait_for(lambda: len(capture.blocks["memory"]) > count + 5)
    assert np.abs(capture.blocks["memory"][-1]).max() == 0

    # The channel stops: the output stops; it starts again with the channel
    sessions.channel.stop()
    assert wait_for(lambda: capture.sinks[0].closed)
    sessions.run_mix("den")
    outputs.channel_started("den")
    assert len(capture.sinks) == 2 and not capture.sinks[1].closed
    outputs.stop_all()
    sessions.channel.stop()


def test_unavailable_and_unknown_types_refused():
    sessions = Sessions()
    outputs = m.audio_outputs.AudioOutputs(sessions)

    class NoCard(m.audio_outputs.OutputType):
        id = "nocard"

        def available(self):
            return False, "No sound card"
    outputs.register_type("p", NoCard())
    with pytest.raises(m.audio_outputs.OutputError, match="No sound card"):
        outputs.attach("den", "nocard", "x")
    with pytest.raises(LookupError):
        outputs.attach("den", "missing", "x")
    with pytest.raises(m.audio_outputs.OutputError):
        outputs.register_type("other", NoCard())  # another plugin's id
    outputs.unregister_owner("p")
    assert outputs.types() == []
