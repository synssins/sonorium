"""A track's volume eases to a new value (a slider, a preset) instead of stepping, except under a transition."""

from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

from test_file_fades import recording, _abrupt_start, _rms  # noqa: E402

RATE = recording.SAMPLE_RATE


def test_eased_volume_moves_at_most_full_scale_per_quarter_second():
    step = 1024 / (recording.VOLUME_EASE_SECONDS * RATE)
    assert recording.eased_volume(None, 0.7, 1024) == 0.7  # a new stream starts at its volume
    assert recording.eased_volume(1.0, 0.0, 1024) == pytest.approx(1.0 - step)
    assert recording.eased_volume(0.50, 0.51, 1024) == 0.51  # small changes (a glide's steps) pass as they are


def _levels(tmp_path, pinned):
    path, n = _abrupt_start(tmp_path, seconds=4.0, tail=0.0001)
    instance = SimpleNamespace(meta=SimpleNamespace(path=path, duration_samples=n), volume=1.0, name="t",
                               fade_in=0.0, fade_out=0.0, is_pinned=lambda name: pinned)
    stream = recording.CrossfadeRecordingStream(instance)
    before = [_rms(next(stream)) for _ in range(20)]
    instance.volume = 0.0
    after = [_rms(next(stream)) for _ in range(20)]
    return np.median(before), after


def test_a_volume_change_eases_over_a_quarter_second(tmp_path):
    level, after = _levels(tmp_path, pinned=False)
    chunk = 1024 / RATE
    # Still sounding a tenth of a second later, silent after a quarter second
    assert after[int(0.1 / chunk)] > 0.4 * level
    assert after[int(0.3 / chunk)] == 0.0


def test_a_transition_sets_the_volume_at_once(tmp_path):
    level, after = _levels(tmp_path, pinned=True)
    assert after[0] == 0.0
