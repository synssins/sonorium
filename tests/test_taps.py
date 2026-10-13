"""
Listening to the audio (core/taps.py): two unrelated plugins (a level meter
and a light show) tap a channel's mix, read-only, each in its own thread; a
slow one falls behind alone; taps never keep a channel running; a track tap
gets one track's blocks before mixing; a theme's files are decoded by core.
"""
import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "sonorium_addon", ROOT / "tests"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from engine_theme import FAST_VALUES, RATE, make_theme  # noqa: E402


def wait_for(cond, seconds=3.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class World:
    def __init__(self):
        from sonorium.core.channel import Channel
        from sonorium.core.taps import Taps
        self.channel = Channel(id=3)
        self.sm = SimpleNamespace(channel_of=lambda sid: self.channel if sid == "den" else None,
                                  get=lambda sid: SimpleNamespace(id=sid) if sid == "den" else None)
        self.taps = Taps(self.sm)

    def play(self, count, start=0):
        for i in range(count):
            self.channel._add_to_buffer(np.full((1, 1024), start + i, dtype=np.int16))


@pytest.fixture
def world():
    w = World()
    yield w
    for tap in list(w.taps._taps):
        tap.close()


def test_two_plugins_tap_a_channel_read_only_in_order(world):
    meter, lights = [], []
    world.taps.tap("den", meter.append, owner="meter")
    world.taps.tap("den", lights.append, owner="lights")
    time.sleep(0.05)
    world.play(5)
    assert wait_for(lambda: len(meter) == 5 and len(lights) == 5)
    assert [b.mixer_sample for b in meter] == [0, 1024, 2048, 3072, 4096]
    assert [int(b.samples[0, 0]) for b in lights] == [0, 1, 2, 3, 4]
    block = meter[0]
    assert block.samples.shape == (1, 1024) and block.samples.dtype == np.int16 and block.channel_id == "den"
    assert not block.samples.flags.writeable
    assert np.shares_memory(meter[2].samples, lights[2].samples)  # the same array for every listener
    assert abs(block.mixer_at - time.time()) < 2
    assert world.channel._client_count == 0  # a tap is never a listener: it keeps no channel running


def test_a_slow_tap_falls_behind_alone(world):
    from sonorium.core.channel import BROADCAST_BUFFER_SIZE
    gate, slow, fast = threading.Event(), [], []

    def stuck(block):
        gate.wait(5)
        slow.append(block)
    slow_tap = world.taps.tap("den", stuck, owner="slow")
    world.taps.tap("den", fast.append, owner="fast")
    time.sleep(0.05)
    world.play(1)
    time.sleep(0.05)
    world.play(BROADCAST_BUFFER_SIZE + 20, start=1)
    assert wait_for(lambda: len(fast) == BROADCAST_BUFFER_SIZE + 21)
    gate.set()
    assert wait_for(lambda: slow_tap.dropped > 0)
    assert slow_tap.dropped + len(slow) <= BROADCAST_BUFFER_SIZE + 21


def test_async_handlers_limits_and_closing_by_owner(world):
    from sonorium.core.taps import LIMITS, TapError
    got = []

    async def main():
        world.taps.loop = asyncio.get_running_loop()

        async def handler(block):
            got.append(block.mixer_sample)
        world.taps.tap("den", handler, owner="lights")
        await asyncio.sleep(0.05)
        world.play(3)
        for _ in range(100):
            if len(got) == 3:
                break
            await asyncio.sleep(0.02)
    asyncio.run(main())
    assert got == [0, 1024, 2048]
    with pytest.raises(LookupError):
        world.taps.tap("nope", print, owner="lights")
    for _ in range(LIMITS["taps_per_plugin"] - 1):
        world.taps.tap("den", lambda b: None, owner="lights")
    with pytest.raises(TapError):
        world.taps.tap("den", lambda b: None, owner="lights")
    assert world.taps.close_owner("lights") == LIMITS["taps_per_plugin"]
    assert world.taps.taps() == []


def test_a_track_tap_gets_one_tracks_blocks_before_mixing(tmp_path):
    from sonorium.core.taps import Taps
    from sonorium.engine import Engine, PcmFileDecoder
    engine = Engine(make_theme(tmp_path), playback_values=FAST_VALUES, seed=4, decoder=PcmFileDecoder())
    channel = SimpleNamespace(id=0)
    taps = Taps(SimpleNamespace(channel_of=lambda sid: channel, get=lambda sid: SimpleNamespace(id=sid)))
    drone, chirp = [], []
    one = taps.tap("den", drone.append, track="Drone", owner="meter")
    two = taps.tap("den", chirp.append, track="Chirp", owner="lights")
    engine.mix.track_taps = taps.track_taps_for(0)
    engine.mix.sample_clock = lambda: engine.clock.samples
    blocks = int(2 * RATE / 1024)
    for _ in range(blocks):
        engine.render(1024)
    assert wait_for(lambda: len(drone) == blocks)
    assert [b.mixer_sample for b in drone[:3]] == [0, 1024, 2048] and drone[0].track == "Drone"
    assert max(int(np.abs(b.samples).max()) for b in drone) > 1000  # its own sound (a 220 Hz sine)
    assert len(chirp) <= blocks  # only blocks it was pulled for
    one.close()
    two.close()
    assert taps.track_taps_for(0) == {}


def test_a_theme_file_is_decoded_by_core(tmp_path):
    from sonorium.core.taps import decode_theme_file
    folder = make_theme(tmp_path)
    pcm = decode_theme_file(folder, "Chirp")
    assert pcm.dtype == np.float32 and abs(len(pcm) - int(0.6 * RATE)) <= 1
    assert 0.1 < float(np.abs(pcm).max()) <= 1.0
    assert len(decode_theme_file(folder, "Birds/Bird 1", max_seconds=0.25)) == int(0.25 * RATE)
    for bad in ("Nope", "../Test Theme/Chirp", "metadata"):
        with pytest.raises(LookupError):
            decode_theme_file(folder, bad)


def test_plugins_tap_and_decode_through_self_audio(tmp_path):
    from playback_helpers import addon_modules  # noqa: F401  (the real package is loaded here)
    from sonorium.core import taps as taps_module
    from sonorium.plugins import base, events, host as host_module
    folder = make_theme(tmp_path)
    world = World()
    old = taps_module.set_taps(world.taps)
    try:
        sm = SimpleNamespace(channel_of=world.sm.channel_of, get=world.sm.get,
                             theme_folder=lambda tid: folder if tid == "engine-test-theme" else None)
        host = host_module.PluginHost(tmp_path / "config", bus=events.EventBus(), session_manager=sm)

        class Meter(base.BasePlugin):
            id, name = "meter", "Meter"
        meter = Meter(tmp_path / "meter", {})
        meter._host, meter._manifest = host, {"api_version": "1.2"}
        got = []
        meter.audio.tap("den", got.append)
        pcm = asyncio.run(meter.audio.decode("engine-test-theme", "Drone", max_seconds=1.0))
        assert len(pcm) == RATE
        from sonorium.plugins.actions import ActionError
        with pytest.raises(ActionError):
            asyncio.run(meter.audio.decode("nope", "Drone"))
        with pytest.raises(ActionError):
            asyncio.run(meter.audio.decode("engine-test-theme", "Nope"))
        with pytest.raises(ActionError):
            meter.audio.tap("nope", got.append)
        time.sleep(0.05)
        world.play(2)
        assert wait_for(lambda: len(got) == 2)
        host.plugin_disabled(meter)  # switched off: its taps close
        assert world.taps.taps() == []
        assert "audio.tap" in host.capabilities() and "audio.decode" in host.capabilities()
    finally:
        taps_module.set_taps(old)
