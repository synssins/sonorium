"""A track turning on or off in a playing mix crossfades over 3 s instead of cutting (real mixer, real mp3s)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
av = pytest.importorskip("av")

ADDON = str(Path(__file__).resolve().parents[1] / "sonorium_addon")
RATE = 44100
CHUNK = 1024
A_HZ, B_HZ = 1000.0, 300.0
m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def addon_package():
    """Imports the add-on's sonorium package for these tests only (other tests use another one)."""
    saved = {k: v for k, v in sys.modules.items() if k == "sonorium" or k.startswith("sonorium.")}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, ADDON)
    try:
        theme = pytest.importorskip("sonorium.theme")
        m.recording, m.theme = sys.modules["sonorium.recording"], theme
        yield
    finally:
        sys.path.remove(ADDON)
        for k in [k for k in sys.modules if k == "sonorium" or k.startswith("sonorium.")]:
            del sys.modules[k]
        sys.modules.update(saved)


def tone_mp3(path: Path, seconds: float, freq: float, amp=6000):
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


@pytest.fixture
def stream_of(tmp_path):
    folder = tmp_path / "Field"
    folder.mkdir()
    tone_mp3(folder / "A.mp3", 25.0, A_HZ)
    tone_mp3(folder / "B.mp3", 25.0, B_HZ)
    theme = SimpleNamespace(name="Field", groups={}, short_file_threshold=15.0,
                            sonorium=SimpleNamespace(master_volume=1.0))
    theme.instances = [m.recording.RecordingThemeInstance(m.recording.RecordingMetadata(folder / f"{n}.mp3", folder), theme)
                       for n in ("A", "B")]

    def make(overrides):
        return m.theme.ThemeStream(theme, overrides)
    return make


def pull(chunks, seconds):
    return np.concatenate([next(chunks).reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]).astype(np.float64)


def envelope(signal, freq, window=2205):
    """Amplitude of one frequency in each 50 ms window (projection on sin and cos)."""
    t = np.arange(window) / RATE
    s, c = np.sin(2 * np.pi * freq * t), np.cos(2 * np.pi * freq * t)
    levels = []
    for i in range(0, len(signal) - window + 1, window):
        part = signal[i:i + window]
        levels.append(2 * np.hypot(part @ s, part @ c) / window)
    return np.array(levels)


def test_switching_preset_crossfades_over_three_seconds(stream_of):
    assert m.theme.TRACK_TOGGLE_FADE_SECONDS == 3.0
    preset = m.recording.preset_track_overrides
    overrides = preset({"B": {"muted": True}})
    chunks = stream_of(overrides).iter_chunks()
    before = pull(chunks, 2.0)
    a_full = envelope(before, A_HZ)[-10:].mean()
    assert envelope(before, B_HZ)[-10:].max() < 0.01 * a_full

    # The channel's preset changes in place: A off, B on
    overrides.clear()
    overrides.update(preset({"A": {"muted": True}, "B": {"muted": False}}))
    switch = pull(chunks, 4.0)
    later = pull(chunks, 6.0)
    a, b = envelope(switch, A_HZ), envelope(switch, B_HZ)
    b_full = envelope(later, B_HZ)[-10:].mean()

    # A ramps down and B ramps up over about 3 s (60 windows of 50 ms)
    assert a[0] > 0.85 * a_full and b[0] < 0.1 * b_full
    assert 0.15 * a_full < a[30] < 0.7 * a_full and 0.2 * b_full < b[30] < 0.8 * b_full
    assert a[58] < 0.06 * a_full and a[62:].max() < 0.01 * a_full  # gone after 3 s
    assert b[62] > 0.6 * b_full
    assert np.all(np.diff(a[:60]) < 0.02 * a_full)  # only down
    assert np.all(np.diff(b[:60]) > -0.02 * b_full)  # only up
    # No gap: the two together never drop out
    assert (a / a_full + b / b_full).min() > 0.6
    # No click: nothing steeper than the steady tone was
    assert np.abs(np.diff(switch)).max() <= 1.05 * np.abs(np.diff(before)).max()


def test_mute_button_fades_out_and_the_track_then_pauses(stream_of):
    stream = stream_of({})
    chunks = stream.iter_chunks()
    pull(chunks, 1.0)
    track_a = next(s for s in stream.recording_streams if s.instance.name == "A")
    track_a.instance._instance.is_enabled = False  # the theme's own mute (the mixer's mute button)
    out = pull(chunks, 3.5)
    a = envelope(out, A_HZ)
    assert a[0] > 0.8 * a[:2].max() and a[30] < 0.7 * a[0] and a[-5:].max() < 0.01 * a[0]
    assert stream._toggle_gain["A"] == 0.0
    # Once silent it is no longer pulled (it pauses where it was)
    position = track_a.gen.gi_frame.f_locals.get("samples_played")
    assert position
    pull(chunks, 0.5)
    assert track_a.gen.gi_frame.f_locals.get("samples_played") == position

    # On again: it fades in from silence
    track_a.instance._instance.is_enabled = True
    a = envelope(pull(chunks, 3.5), A_HZ)
    assert a[0] < 0.1 * a[-5:].mean() and 0.2 * a[-5:].mean() < a[30] < 0.8 * a[-5:].mean()
