"""The mix level counts only sounding tracks, changes smoothly, and limits softly."""

import importlib.util
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

spec = importlib.util.spec_from_file_location(
    "mixing_under_test", Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium" / "mixing.py")
mixing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mixing)

N = 1024


def tone(amplitude):
    return (np.sin(np.linspace(0, 40 * np.pi, N)) * amplitude).astype(np.int16).reshape(1, -1)


def silent():
    return np.zeros((1, N), np.int16)


def settle(level, chunks, rounds=400):
    for _ in range(rounds):
        level.mix(chunks, 1.0)
    return level.gain


def test_silent_tracks_dont_lower_the_level():
    many_quiet_sparse = [tone(4000)] + [silent()] * 9
    assert settle(mixing.MixLevel(44100, N), many_quiet_sparse) == pytest.approx(1.0, abs=1e-3)
    four_sounding = [tone(4000)] * 4
    assert settle(mixing.MixLevel(44100, N), four_sounding) == pytest.approx(0.5, abs=1e-3)


def test_level_changes_smoothly_within_and_between_chunks():
    level = mixing.MixLevel(44100, N)
    settle(level, [tone(4000)])
    before = level.gain
    out = level.mix([tone(4000)] * 4, 1.0)  # three more sounds start at once
    assert 0.5 < level.gain < before  # moving down, not jumped
    assert before - level.gain < 0.2
    # Comes down faster than it goes back up
    down = settle(level, [tone(4000)] * 4, rounds=13)
    level_up = mixing.MixLevel(44100, N)
    level_up.gain = 0.5
    up = settle(level_up, [tone(4000)], rounds=13)
    assert (down - 0.5) < (1.0 - up)
    assert out.dtype == np.int16 and out.shape == (1, N)


def test_soft_limiter_only_touches_loud_peaks():
    quiet = np.array([1000.0, -20000.0])
    assert np.array_equal(mixing.soft_limit(quiet), quiet)
    loud = mixing.soft_limit(np.array([60000.0, -90000.0]))
    assert np.all(np.abs(loud) < mixing.LIMIT_CEILING * 32767) and np.all(np.abs(loud) > mixing.LIMIT_KNEE * 32767)
    assert loud[0] > 0 > loud[1]
