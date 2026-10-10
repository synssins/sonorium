"""
Ebb & Flow: the level rises and falls between the two knobs of the track's
Volume, Interval sets how lively, Pause between plays plays the file once and
pauses, and an Ebb & Flow group with Pause between plays is never silent.
"""

import random
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

from test_file_fades import recording  # noqa: E402

RATE = recording.SAMPLE_RATE
CHUNK = 1024
FULL = 10000


def test_levels_stay_between_the_knobs_and_each_move_is_audible():
    rng = random.Random(5)
    levels = [recording.ebb_level(0.25, None, rng) for _ in range(5000)]
    assert min(levels) >= 0.25 and max(levels) <= 1.0
    assert all(abs(recording.ebb_level(0.25, 0.6, rng) - 0.6) >= 0.25 * 0.75 for _ in range(500))
    assert recording.ebb_level(1.0, 0.4) == 1.0  # both knobs together: a steady level


def test_interval_sets_how_lively():
    slow_glide, slow_hold = recording.ebb_timing(0.0)
    fast_glide, fast_hold = recording.ebb_timing(1.0)
    assert slow_glide[0] > fast_glide[1] and slow_hold[0] > fast_hold[1]


class Ones:
    """A base stream of a steady tone level, so the output is the Ebb & Flow gain."""
    CHUNK_SIZE = CHUNK

    def __init__(self, instance=None):
        self.instance = instance

    def __next__(self):
        return np.full((1, CHUNK), FULL, dtype=np.int16)


def track(name="Waves", seconds=60.0, **settings):
    values = dict(presence=0.5, volume=1.0, volume_low=0.3, pause_between=False, pause_min=2.0, pause_max=4.0,
                  is_enabled=True, log_label="")
    values.update(settings)
    return SimpleNamespace(name=name, meta=SimpleNamespace(duration_samples=int(seconds * RATE)), **values)


def gains(stream, seconds):
    return np.concatenate([next(stream).reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]) / FULL


def test_it_rises_and_falls_between_the_knobs():
    random.seed(3)
    stream = recording.PresenceMixingStream(Ones(), track(presence=0.8))
    g = gains(stream, 300)
    assert g.min() >= 0.3 - 0.001 and g.max() <= 1.0  # never below the lower knob
    per_5s = g[: len(g) // (5 * RATE) * 5 * RATE].reshape(-1, 5 * RATE).mean(axis=1)
    assert per_5s.max() - per_5s.min() > 0.3  # it does move
    assert np.abs(np.diff(g)).max() < 0.001  # glides, no steps


def test_pause_between_plays_plays_the_file_once_then_pauses():
    random.seed(4)
    stream = recording.PresenceMixingStream(Ones(), track(seconds=8.0, pause_between=True, presence=1.0))
    g = gains(stream, 120)
    quiet = g < 0.001
    assert quiet[: int(1.5 * RATE)].all()  # its first start is staggered by a pause (2-4 s)
    # It plays and pauses in turn: several quiet stretches, and it's heard in between
    edges = np.flatnonzero(np.diff(quiet.astype(int)) == 1)
    assert len(edges) >= 5
    assert (~quiet).mean() > 0.4
    assert np.abs(np.diff(g)).max() < 0.002  # fades in and out, no cuts


def test_an_ebb_flow_group_with_pause_between_plays_is_never_silent():
    random.seed(6)
    group = recording.EbbGroup(lambda: True)
    views = [track(f"Wave {i}", seconds=6.0 + i, pause_between=True, presence=1.0, pause_min=3.0, pause_max=12.0)
             for i in range(3)]
    streams = []
    for v in views:
        s = recording.PresenceMixingStream(Ones(), v)
        group.register(v.name, v)
        s.ebb_group = group
        streams.append(s)
    loudest = []
    pauses_seen = 0
    for _ in range(int(240 * RATE / CHUNK)):
        chunks = [np.abs(next(s).reshape(-1)) for s in streams]
        loudest.append(max(c.max() for c in chunks))
        pauses_seen += sum(c.max() == 0 for c in chunks)
    assert min(loudest) > 0  # at least one file is always playing
    assert pauses_seen > 0  # and they do pause


def test_a_group_without_its_toggle_leaves_each_file_to_its_own():
    random.seed(7)
    group = recording.EbbGroup(lambda: False)
    v = track("Alone", seconds=5.0, pause_between=True, presence=1.0, pause_min=20.0, pause_max=30.0)
    s = recording.PresenceMixingStream(Ones(), v)
    group.register(v.name, v)
    s.ebb_group = group
    g = gains(s, 15)
    assert (g < 0.001).all()  # staggered on its own: nothing makes it play


def test_track_view_in_an_ebb_flow_group():
    theme = SimpleNamespace(groups={"Waves": {"mode": "ebb_flow", "pause_between": True, "volume": 0.5}})
    inst = recording.RecordingThemeInstance(SimpleNamespace(name="Waves/One", group="Waves", is_short_file=lambda t: True), theme)
    inst.volume_low = 0.4
    view = recording.TrackView(inst, {})
    assert view.playback_mode == recording.PlaybackMode.PRESENCE  # even a short file
    assert view.exclusive is False and view.exclusion_group is None and view.ebb_group_name == "Waves"
    assert view.pause_between is True  # the group's toggle overrides the track's
    assert view.volume_low == pytest.approx(0.2)  # the group's volume divides both knobs
    # A preset can turn the group's toggle off: each track's own then counts
    off = recording.preset_group_overrides({"Waves": {"pause_between": False}})
    assert recording.TrackView(inst, off).pause_between is False
    assert recording.group_pauses("Waves", off, theme.groups) is False
    assert recording.group_pauses("Waves", {}, theme.groups) is True


def test_presets_carry_the_ebb_flow_settings():
    overrides = recording.preset_track_overrides({"Rain": {"volume": 0.8, "volume_low": 0.3, "pause_between": True,
                                                           "pause_min": 30, "pause_max": 600}})
    rain = overrides["Rain"]
    assert (rain["volume_low"], rain["pause_between"], rain["pause_min"], rain["pause_max"]) == (0.3, True, 30, 600)
