"""Ebb & Flow outside a group: always heard, its level wandering; Interval sets the average."""

import random
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

from test_file_fades import recording  # noqa: E402

RATE = recording.SAMPLE_RATE
FLOOR = recording.EBB_FLOOR


@pytest.mark.parametrize("interval", [0.1, 0.5, 0.9])
def test_interval_sets_the_average_level(interval):
    rng = random.Random(5)
    levels = [recording.ebb_level(interval, None, rng) for _ in range(20000)]
    assert min(levels) >= FLOOR and max(levels) <= 1.0
    assert np.mean(levels) == pytest.approx(FLOOR + (1 - FLOOR) * interval, abs=0.02)


def test_each_move_goes_somewhere_audible():
    rng = random.Random(9)
    assert all(abs(recording.ebb_level(0.5, 0.6, rng) - 0.6) >= recording.EBB_MIN_MOVE for _ in range(500))
    assert recording.ebb_level(1.0, 0.4) == 1.0


class Ones:
    """A base stream of full-scale samples, so the output is the Ebb & Flow gain."""
    CHUNK_SIZE = 1024

    def __init__(self, instance=None):
        self.instance = instance

    def __next__(self):
        return np.full((1, self.CHUNK_SIZE), 10000, dtype=np.int16)


def gains(stream, seconds):
    return np.concatenate([next(stream).reshape(-1) for _ in range(int(seconds * RATE / 1024))]) / 10000


def test_it_wanders_and_is_never_silent():
    random.seed(3)
    track = SimpleNamespace(presence=0.5, name="Wind", log_label="")
    stream = recording.PresenceMixingStream(Ones(), track)
    g = gains(stream, 600)
    assert g.min() >= FLOOR - 0.001  # never below the floor
    per_10s = g[: len(g) // (10 * RATE) * 10 * RATE].reshape(-1, 10 * RATE).mean(axis=1)
    assert per_10s.max() - per_10s.min() > 0.3  # it does rise and fall
    assert np.abs(np.diff(g)).max() < 0.001  # glides, no steps


def test_turned_on_again_it_rises_from_silence():
    random.seed(4)
    track = SimpleNamespace(presence=0.5, name="Wind", log_label="")
    stream = recording.PresenceMixingStream(Ones(), track)
    gains(stream, 5)
    stream.restart()
    g = gains(stream, recording.TRACK_FADE_DURATION + 1)
    assert g[0] < 0.01
    assert g[-1] >= FLOOR
