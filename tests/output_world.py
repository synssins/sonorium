"""
A small world for the audio output tests: the real session manager, state
store, speaker registry and AudioOutputs, a fake output type that records
what it is asked, mixer channels that make a steady test signal (no theme,
no mp3s), and a media controller mock for the network speakers.

Import MODULES into the test module's addon_modules() and pass the
resulting namespace to World.
"""
import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

MODULES = ("sonorium.core.audio_outputs", "sonorium.core.channel", "sonorium.core.session_manager",
           "sonorium.core.state", "sonorium.core.playback_runner", "sonorium.core.sequences",
           "sonorium.core.playlists", "sonorium.core.speaker_settings", "sonorium.core.speaker_test_tone",
           "sonorium.ha.registry", "sonorium.plugins.actions", "sonorium.plugins.events")

LEVEL = 10000  # every sample the test channels make


class Store:
    """The plugin framework's store (main.section / save) in memory."""

    def __init__(self, data=None):
        self.main = SimpleNamespace(data=data if data is not None else {}, saves=0)
        self.main.section = lambda name, kind=dict: self.main.data.setdefault(name, kind())

        def save():
            self.main.saves += 1
        self.main.save = save


class FakeSink:
    def __init__(self, output, device_id, options):
        self.output, self.device_id, self.options = output, device_id, options
        self.blocks = []
        self.closed = 0
        self.latency = 0.05
        self.fail_after = output.fail_writes.get(device_id)

    def write(self, block):
        assert block.dtype.name == "int16" and block.ndim == 2 and block.shape[0] == 1
        if self.fail_after is not None and len(self.blocks) >= self.fail_after:
            raise RuntimeError("device unplugged")
        self.blocks.append(block.copy())
        if self.output.write_delay:
            time.sleep(self.output.write_delay)

    def close(self):
        self.closed += 1
        self.output.events.append(("close", self.device_id))


def make_output_type(m, type_id="fake", title="Fake devices", one=False):
    base = m.audio_outputs.OutputType

    class FakeOutput(base):
        def __init__(self):
            self.id, self.title, self.one_device_at_a_time = type_id, title, one
            self.devices = [{"id": "dev/1", "name": "Device one", "api": "Fake API", "is_default": True,
                             "channels": 2, "sample_rates": [44100, 48000]},
                            {"id": "dev/2", "name": "Device two", "api": "Fake API"}]
            self.ok = (True, "")
            self.open_error = {}     # device id -> exception open() raises
            self.fail_writes = {}    # device id -> blocks written before write() raises
            self.write_delay = 0.0
            self.sinks = []
            self.opens = []          # (device id, sample rate, options)
            self.events = []         # ("open"|"close", device id), in order
            self.discovered = 0

        def available(self):
            return self.ok

        async def discover(self):
            self.discovered += 1
            dup = [{"id": self.devices[0]["id"], "name": "dup"}] if self.devices else []
            return [dict(d) for d in self.devices] + ["not a dict", {"id": 5}, {"name": "no id"}] + dup

        def open(self, device_id, sample_rate, options):
            self.opens.append((device_id, sample_rate, options))
            if device_id in self.open_error:
                raise self.open_error[device_id]
            self.events.append(("open", device_id))
            sink = FakeSink(self, device_id, options)
            self.sinks.append(sink)
            return sink

        def sinks_of(self, device_id):
            return [s for s in self.sinks if s.device_id == device_id]

    return FakeOutput()


def make_channel_class(m):
    np = __import__("numpy")

    class SteadyChannel(m.channel.Channel):
        """A mixer channel whose mix is a steady LEVEL (no theme)."""

        def set_theme(self, theme, overrides=None, crossfade=3.0, fade_in=0.0):
            self.run_silent()

        def next_chunk(self):
            return np.full((1, 1024), LEVEL, dtype=np.int16)

    return SteadyChannel


class Channels:
    def __init__(self, cls):
        self.cls = cls
        self.channels = {}

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    def get_available_channel(self):
        in_use = set(self.sm._session_channels.values())
        for channel in self.channels.values():
            if channel.id not in in_use and not channel._generator_running:
                return channel
        channel = self.cls(id=len(self.channels) + 1)
        self.channels[channel.id] = channel
        return channel


def wait_for(predicate, seconds=3.0):
    end = time.time() + seconds
    while not predicate() and time.time() < end:
        time.sleep(0.01)
    return predicate()


class World:
    def __init__(self, m, tmp_path, store_data=None, one=False):
        self.m = m
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        state = self.state = m.state.StateStore(tmp_path / "state.json")
        state.settings.crossfade_duration = 0
        state.settings.enabled_speakers = ["net:a"]
        state.settings.enabled_speakers_exact = True
        self.registry = m.registry.HARegistry("http://ha.invalid/api", "token")
        self.registry.set_speaker_settings_source(lambda: state.settings.speaker_settings)
        self.registry.set_extra_speaker_source(lambda: [{"entity_id": "net:a", "name": "Kitchen", "type": "dlna"}])
        self.registry.merge_extra_speakers()
        controller = MagicMock()
        for name in ("play_media_multi", "set_volume_multi", "stop_multi", "pause_multi"):
            setattr(controller, name, AsyncMock(side_effect=lambda ids, *a: {i: True for i in ids}))
        self.controller = controller
        self.channels = Channels(make_channel_class(m))
        sm = self.sm = m.session_manager.SessionManager(state, self.registry, controller, "http://test",
                                                        channel_manager=self.channels)
        self.channels.sm = sm
        themes = {"storm": SimpleNamespace(id="storm", name="Storm")}
        sm.get_theme = themes.get
        sm._theme_presets = lambda theme_id: {}
        self.store = Store(store_data)
        self.outputs = self.call(lambda: m.audio_outputs.AudioOutputs(sm, self.store, rate=44100))
        self.fake = make_output_type(m, one=one)

    # --- Running things on the world's loop ---

    def call(self, fn):
        """fn() on the loop (a coroutine's result awaited)."""
        async def run():
            result = fn()
            if asyncio.iscoroutine(result):
                result = await result
            return result
        return asyncio.run_coroutine_threadsafe(run(), self.loop).result(30)

    def settle(self):
        """Let tasks the loop started (play, registration) run."""
        for _ in range(5):
            self.call(lambda: asyncio.sleep(0.02))

    def register(self, output_type=None, owner="fakeplugin"):
        self.call(lambda: self.outputs.register_type(owner, output_type or self.fake))
        self.settle()

    def sid(self, device_id, type_id="fake"):
        return self.m.audio_outputs.speaker_id_for(type_id, device_id)

    def enable(self, *speaker_ids):
        for sid in speaker_ids:
            if sid not in self.state.settings.enabled_speakers:
                self.state.settings.enabled_speakers.append(sid)

    def channel(self, name, speakers, volume=50):
        session = self.sm.create(theme_id="storm", custom_name=name,
                                 adhoc_selection=self.m.state.SpeakerSelection(include_speakers=list(speakers)))
        session.volume = volume
        return session

    def play(self, session, take_over=None):
        ok = self.call(lambda: self.sm.play(session.id, take_over=take_over))
        self.settle()
        return ok

    def stop(self, session):
        ok = self.call(lambda: self.sm.stop(session.id))
        self.settle()
        return ok

    def close(self):
        self.outputs.stop_all()
        for channel in self.channels.channels.values():
            channel.stop()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
