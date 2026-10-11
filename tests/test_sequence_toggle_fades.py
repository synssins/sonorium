"""
A sequence step fades the tracks it turns on or off over the step's ramp time
(a preset picked by hand keeps the quick 3 s), and an Ebb & Flow track turned
on again starts afresh: from its file's beginning, fading in from silence
(real mixer, real mp3s, measured from the output).
"""

import random
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
av = pytest.importorskip("av")

from playback_helpers import CHUNK, RATE, addon_modules, envelope, tone_mp3  # noqa: E402

A_HZ, B_HZ = 1000.0, 300.0
START_HZ, LATER_HZ = 500.0, 700.0
m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.channel", "sonorium.theme", "sonorium.recording") as mods:
        m.__dict__.update(mods.__dict__)
        yield


def parts_mp3(path: Path, parts, amp=6000):
    """An mp3 of tones one after another: parts = [(seconds, Hz), ...] (phase kept, no click)."""
    freqs = np.concatenate([np.full(int(seconds * RATE), freq) for seconds, freq in parts])
    phase = 2 * np.pi * np.cumsum(freqs) / RATE
    samples = (amp * np.sin(phase)).astype(np.int16).reshape(1, -1)
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


@pytest.fixture(scope="module")
def tones(tmp_path_factory):
    folder = tmp_path_factory.mktemp("tones")
    tone_mp3(folder / "A.mp3", 60.0, A_HZ)
    tone_mp3(folder / "B.mp3", 60.0, B_HZ)
    parts_mp3(folder / "E.mp3", [(10.0, START_HZ), (50.0, LATER_HZ)])
    return folder


def make_theme(folder, names):
    theme = SimpleNamespace(id="t", name="t", groups={}, short_file_threshold=15.0,
                            sonorium=SimpleNamespace(master_volume=1.0))
    theme.instances = [m.recording.ThemeTrack(m.recording.AudioFile(folder / f"{n}.mp3", folder), theme)
                       for n in names]
    theme.get_stream = lambda overrides=None: m.theme.ThemeStream(theme, overrides)
    return theme


def channel_on(theme, overrides):
    channel = m.channel.Channel(id=1)
    channel._ensure_generator_running = lambda: None  # the test pulls the chunks itself
    channel.set_theme(theme, overrides)
    return channel


def pull(channel, seconds):
    return np.concatenate([channel.next_chunk().reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]).astype(np.float64)


def preset(tracks):
    return m.recording.preset_track_overrides(tracks)


def test_sequence_step_fades_tracks_on_and_off_over_its_ramp(tones):
    channel = channel_on(make_theme(tones, ["A", "B"]), preset({"B": {"muted": True}}))
    before = pull(channel, 1.0)
    a_full = envelope(before, A_HZ)[-5:].mean()
    assert envelope(before, B_HZ)[-5:].max() < 0.01 * a_full

    # A sequence step with a 20 s ramp: A off, B on
    channel.set_track_overrides(preset({"A": {"muted": True}, "B": {"muted": False}}),
                                glide_seconds=20.0, fade_seconds=20.0)
    switch = pull(channel, 28.0)
    a, b = envelope(switch, A_HZ) / a_full, envelope(switch, B_HZ) / a_full  # same loudness files
    # A's share of the two (the mixer lowers its level while both sound, not their balance)
    share = a / (a + b)

    # 20 windows of 50 ms per second: A is still mostly there after 3 s, half way at 10 s, gone at 20 s
    assert share[60] > 0.8 and b[60] < 0.25  # not the 3 s fade
    assert 0.4 < share[200] < 0.6
    assert share[380] < 0.1 and a[410:].max() < 0.01
    assert b[-10:].mean() > 0.95  # B in at full level (the mixer's level back up, alone again)
    assert np.all(np.diff(share[:400]) < 0.02)  # A only down, B only up
    assert np.all(np.diff(b[:380]) > -0.02)
    assert (a + b)[:420].min() > 0.6  # no gap between them
    # No click: nothing steeper than the steady tone was
    assert np.abs(np.diff(switch)).max() <= 1.05 * np.abs(np.diff(before)).max()


def test_preset_picked_by_hand_still_fades_tracks_over_three_seconds(tones):
    channel = channel_on(make_theme(tones, ["A", "B"]), preset({"B": {"muted": True}}))
    a_full = envelope(pull(channel, 1.0), A_HZ)[-5:].mean()
    channel.set_track_overrides(preset({"A": {"muted": True}, "B": {"muted": False}}), glide_seconds=3.0)
    switch = pull(channel, 5.0)
    a, b = envelope(switch, A_HZ) / a_full, envelope(switch, B_HZ) / a_full
    share = a / (a + b)
    assert 0.4 < share[30] < 0.6  # half way at 1.5 s
    assert share[50] < 0.25 and a[62:].max() < 0.01  # done after 3 s


def test_ebb_and_flow_track_turned_on_again_restarts_from_its_beginning(tones):
    random.seed(3)
    theme = make_theme(tones, ["E"])
    track = theme.instances[0]
    track.playback_mode = m.recording.PlaybackMode.PRESENCE
    track.presence = 0.0  # a short Duration: it comes back in quickly
    track.volume_low = track.volume  # both knobs together: a steady level
    channel = channel_on(theme, {})
    assert isinstance(channel._theme_stream.recording_streams[0], m.recording.PresenceMixingStream)

    playing = pull(channel, 12.0)  # past the file's first part
    full = envelope(playing, START_HZ)[:150].mean()
    assert full > 0
    assert envelope(playing, LATER_HZ)[-10:].mean() > 0.8 * full  # in its later part, at full level

    # Muted mid-active-period: it fades out and pauses where it was
    track.is_enabled = False
    pull(channel, 4.0)
    assert channel._theme_stream._toggle_gain["E"] == 0.0

    # On again: from the file's beginning (its first part), fading in from silence
    track.is_enabled = True
    back = pull(channel, 8.0)
    start, later = envelope(back, START_HZ) / full, envelope(back, LATER_HZ) / full
    assert later.max() < 0.05  # not resumed in the later part
    assert start[0] < 0.05  # from silence, no jump
    assert start[20] < 0.5  # still coming in after 1 s
    assert start[-10:].mean() > 0.9  # in at full level
    assert np.all(np.diff(start[:120]) > -0.02)  # only up


def test_ebb_and_flow_track_that_stays_on_is_not_restarted(tones):
    random.seed(3)
    theme = make_theme(tones, ["E", "A"])
    track = theme.instances[0]
    track.playback_mode = m.recording.PlaybackMode.PRESENCE
    track.presence = 0.0  # a short Duration: it comes back in quickly
    track.volume_low = track.volume  # both knobs together: a steady level
    channel = channel_on(theme, {})
    full = envelope(pull(channel, 12.0), LATER_HZ)[-10:].mean()
    # A sequence step that turns another track off: E plays on where it is
    channel.set_track_overrides(preset({"A": {"muted": True}}), glide_seconds=5.0, fade_seconds=5.0)
    out = pull(channel, 3.0)
    assert envelope(out, START_HZ).max() < 0.05 * full
    assert envelope(out, LATER_HZ).min() > 0.9 * full
