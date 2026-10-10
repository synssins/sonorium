"""
Shared set-up for the playback tests (fades, glides, sequences, playlists).

Loads the add-on's own modules without fmtr, Home Assistant or MQTT: bare
packages (their __init__ files import everything) plus stubs for obs, and
for utils when httpx isn't installed (the CI runner installs only pytest,
numpy and av).
"""

import contextlib
import importlib
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "sonorium_addon" / "sonorium"
RATE = 44100
CHUNK = 1024


def _ours(name):
    return name == "sonorium" or name.startswith("sonorium.") or name == "fmtr" or name.startswith("fmtr.")


def _package(name, path):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    return module


@contextlib.contextmanager
def addon_modules(*names):
    """Import the named add-on modules ("sonorium.core.channel", ...) for the duration."""
    saved = {name: module for name, module in sys.modules.items() if _ours(name)}
    for name in saved:
        del sys.modules[name]

    obs = types.ModuleType("sonorium.obs")
    obs.logger = logging.getLogger("sonorium-playback-test")
    obs.logger.instrument = lambda *a, **k: (lambda method: method)
    stubs = {
        "sonorium": _package("sonorium", PACKAGE),
        "sonorium.core": _package("sonorium.core", PACKAGE / "core"),
        "sonorium.ha": _package("sonorium.ha", PACKAGE / "ha"),
        "sonorium.web": _package("sonorium.web", PACKAGE / "web"),
        "sonorium.obs": obs,
    }
    try:
        import httpx  # noqa: F401  (sonorium.utils needs it)
    except ImportError:
        utils = types.ModuleType("sonorium.utils")

        class IndexList(list):
            pass
        utils.IndexList = IndexList
        stubs["sonorium.utils"] = utils
    sys.modules.update(stubs)
    try:
        yield SimpleNamespace(**{name.rsplit(".", 1)[-1]: importlib.import_module(name) for name in names})
    finally:
        for name in [n for n in sys.modules if _ours(n)]:
            del sys.modules[name]
        sys.modules.update(saved)


def tone_mp3(path: Path, seconds: float, freq: float, amp=6000):
    import av
    import numpy as np
    t = np.arange(int(seconds * RATE)) / RATE
    samples = (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16).reshape(1, -1)
    out = av.open(str(path), "w")
    stream = out.add_stream("mp3", rate=RATE)
    stream.layout = "mono"
    for i in range(0, samples.shape[1], 1152):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples[:, i:i + 1152]), format="s16p", layout="mono")
        frame.rate = RATE
        for packet in stream.encode(frame):
            out.mux(packet)
    for packet in stream.encode(None):
        out.mux(packet)
    out.close()


def envelope(signal, freq, window=2205):
    """Amplitude of one frequency in each 50 ms window (projection on sin and cos)."""
    import numpy as np
    t = np.arange(window) / RATE
    s, c = np.sin(2 * np.pi * freq * t), np.cos(2 * np.pi * freq * t)
    levels = []
    for i in range(0, len(signal) - window + 1, window):
        part = signal[i:i + window]
        levels.append(2 * np.hypot(part @ s, part @ c) / window)
    return np.array(levels)


class FakeClock:
    """A monotonic clock the test moves by hand."""

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeChannel:
    """Records what the session manager asks of a channel (no audio)."""

    def __init__(self, channel_id=1):
        self.id = channel_id
        self.calls = []
        self.theme = None
        self.playing = False

    @property
    def is_faded_out(self):
        return True

    def set_theme(self, theme, overrides=None, crossfade=3.0, fade_in=0.0):
        self.calls.append(("set_theme", theme.id, crossfade, fade_in))
        self.theme = theme
        self.playing = True

    def set_track_overrides(self, overrides, glide_seconds=0.0):
        self.calls.append(("overrides", dict(overrides or {}), glide_seconds))

    def fade_in(self, seconds):
        self.calls.append(("fade_in", seconds))

    def fade_out(self, seconds):
        self.calls.append(("fade_out", seconds))

    def stop(self):
        self.calls.append(("stop",))
        self.playing = False

    def idle_seconds(self):
        return 0.0

    def glides(self):
        return [c[2] for c in self.calls if c[0] == "overrides"]


class FakeChannelManager:
    def __init__(self):
        self.channel = FakeChannel()

    def get_channel(self, channel_id):
        return self.channel if channel_id == self.channel.id else None

    def get_available_channel(self):
        return self.channel
