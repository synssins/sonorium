"""
How a theme's tracks are summed into one signal.

The mix used to be divided by the square root of every *enabled* track,
including sparse ones that were silent at the time, so themes with many
occasional sounds played quietly. Now:

1. Only tracks actually sounding in a chunk count (peak above ACTIVE_PEAK),
   and each counts by how loud it is next to the loudest one (by power): a
   track fading in, or one set low, counts as a fraction of a track. (Counting
   every sounding track as a whole one made the mix duck the moment a track
   began a long fade-in.)
2. The level divides by the square root of that count, but changes smoothly:
   it comes down over about ATTACK_SECONDS when more sound starts, and goes
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
ATTACK_SECONDS = 2.0  # was 0.3 s: a duck that fast sounded like a cut
RELEASE_SECONDS = 3.0
LIMIT_KNEE = 0.7  # fraction of full scale where the soft limiter starts
LIMIT_CEILING = 0.89  # about -1 dBFS: room for the MP3 encoder's overshoot


def target_gain(active_tracks: float) -> float:
    """Level for this many sounding tracks (1 for none or one; may be fractional)."""
    return 1.0 / math.sqrt(max(1.0, active_tracks))


def effective_count(data: np.ndarray) -> float:
    """
    How many tracks are sounding, each weighed by its power next to the
    loudest one's: n equally loud tracks count n; a track 20 dB down counts
    0.01. Silent tracks (peak at or below ACTIVE_PEAK) count 0.
    """
    peaks = np.max(np.abs(data), axis=1)
    sounding = data[peaks > ACTIVE_PEAK]
    if sounding.shape[0] == 0:
        return 0.0
    power = np.mean(sounding.astype(np.float64) ** 2, axis=1)
    loudest = float(np.max(power))
    return float(np.sum(power) / loudest) if loudest > 0 else 0.0


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
        # Last effective count of sounding tracks (for logging)
        self.count = 0.0

    def mix(self, chunks: list[np.ndarray], output_gain: float) -> np.ndarray:
        """
        `chunks`: one (channels, n) int16 array per enabled track, all with
        the same channel count (1 mono, 2 stereo). Returns a (channels, n)
        int16 array. In stereo a track counts by the power of its mono fold
        (so the level moves exactly as in a mono mix of the same sounds), and
        the level, gain and limiter act on both channels alike.
        """
        channels = chunks[0].shape[0]
        if channels == 1:
            data = np.vstack(chunks).astype(np.float32)  # (tracks, n)
            weigh = data
        else:
            data = np.stack(chunks).astype(np.float32)  # (tracks, channels, n)
            weigh = data.mean(axis=1)  # each track as its mono fold: the level moves as the mono mix's would
        self.count = effective_count(weigh)
        target = target_gain(self.count)
        rate = self._attack if target < self.gain else self._release
        new_gain = self.gain + (target - self.gain) * rate

        # Ramp from the last level to the new one across the chunk: no audible steps
        ramp = np.linspace(self.gain, new_gain, data.shape[-1], dtype=np.float32)
        self.gain = new_gain

        mixed = data.sum(axis=0) * ramp * output_gain
        mixed = soft_limit(mixed)
        return np.clip(mixed, -32768, 32767).astype(np.int16).reshape(channels, -1)
