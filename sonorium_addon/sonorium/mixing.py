"""
How a theme's tracks are summed into one signal.

The mix used to be divided by the square root of every *enabled* track,
including sparse ones that were silent at the time, so themes with many
occasional sounds played quietly. Now:

1. Only tracks actually sounding in a chunk count (peak above ACTIVE_PEAK).
2. The level divides by the square root of that count, but changes smoothly:
   it comes down in about ATTACK_SECONDS when more sounds start, and goes
   back up over RELEASE_SECONDS, ramped across each chunk so there's no step.
3. After the output gain, a soft limiter above LIMIT_KNEE of full scale
   bends peaks toward LIMIT_CEILING (about -1 dBFS) instead of hard
   clipping, leaving room for the MP3 encoder's overshoot.

Pure numpy and deterministic, so the Android mixer can match it exactly.
"""
import math

import numpy as np

FULL_SCALE = 32767.0
ACTIVE_PEAK = 100.0  # about -50 dBFS: quieter than this counts as silent
ATTACK_SECONDS = 0.3
RELEASE_SECONDS = 3.0
LIMIT_KNEE = 0.7  # fraction of full scale where the soft limiter starts
LIMIT_CEILING = 0.89  # about -1 dBFS: room for the MP3 encoder's overshoot


def target_gain(active_tracks: int) -> float:
    """Level for this many sounding tracks (1 for none or one)."""
    return 1.0 / math.sqrt(max(1, active_tracks))


def soft_limit(signal: np.ndarray) -> np.ndarray:
    """Leave everything below the knee untouched; bend louder peaks smoothly toward the ceiling."""
    knee = LIMIT_KNEE * FULL_SCALE
    headroom = (LIMIT_CEILING - LIMIT_KNEE) * FULL_SCALE
    magnitude = np.abs(signal)
    over = magnitude > knee
    if not np.any(over):
        return signal
    limited = signal.copy()
    limited[over] = np.sign(signal[over]) * (knee + headroom * np.tanh((magnitude[over] - knee) / headroom))
    return limited


class MixLevel:
    """Sums one chunk from each track into one int16 chunk, with a smoothed level."""

    def __init__(self, sample_rate: int, chunk_size: int):
        chunk_seconds = chunk_size / sample_rate
        # One-pole smoothing coefficients per chunk
        self._attack = 1.0 - math.exp(-chunk_seconds / ATTACK_SECONDS)
        self._release = 1.0 - math.exp(-chunk_seconds / RELEASE_SECONDS)
        self.gain = 1.0

    def mix(self, chunks: list[np.ndarray], output_gain: float) -> np.ndarray:
        """`chunks`: one (1, n) int16 array per enabled track. Returns a (1, n) int16 array."""
        data = np.vstack(chunks).astype(np.float32)
        active = int(np.count_nonzero(np.max(np.abs(data), axis=1) > ACTIVE_PEAK))
        target = target_gain(active)
        rate = self._attack if target < self.gain else self._release
        new_gain = self.gain + (target - self.gain) * rate

        # Ramp from the last level to the new one across the chunk: no audible steps
        ramp = np.linspace(self.gain, new_gain, data.shape[1], dtype=np.float32)
        self.gain = new_gain

        mixed = data.sum(axis=0) * ramp * output_gain
        mixed = soft_limit(mixed)
        return np.clip(mixed, -32768, 32767).astype(np.int16).reshape(1, -1)
