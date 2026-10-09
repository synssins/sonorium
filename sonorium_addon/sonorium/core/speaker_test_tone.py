"""
The short test sound Settings > Speakers plays on one speaker: a soft two-note
chime (about 3 seconds), encoded to MP3 once and served from memory.
"""
from __future__ import annotations

import io
from functools import lru_cache

import numpy as np

SAMPLE_RATE = 44100
DURATION = 3.0  # seconds
# Two soft notes (C5, then E5 over it) with a gentle attack and long decay
NOTES = ((523.25, 0.0), (659.25, 0.9))
PEAK = 0.3  # of full scale: quiet even before the speaker volume is lowered


def tone_samples(duration: float = DURATION, rate: int = SAMPLE_RATE) -> np.ndarray:
    """Mono int16 samples of the chime."""
    t = np.arange(int(duration * rate)) / rate
    signal = np.zeros_like(t)
    for freq, start in NOTES:
        local = t - start
        active = local >= 0
        attack = np.clip(local / 0.03, 0.0, 1.0)
        decay = np.exp(-np.clip(local, 0.0, None) * 2.2)
        note = np.sin(2 * np.pi * freq * local) + 0.25 * np.sin(4 * np.pi * freq * local)
        signal += np.where(active, note * attack * decay, 0.0)
    # Fade the tail to silence so the file ends cleanly
    fade = np.clip((duration - t) / 0.3, 0.0, 1.0)
    signal *= fade
    signal *= PEAK / max(1e-9, float(np.max(np.abs(signal))))
    return (signal * 32767).astype(np.int16)


@lru_cache(maxsize=1)
def tone_mp3() -> bytes:
    """The chime as an MP3 file (built on first use)."""
    import av

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp3") as container:
        stream = container.add_stream("mp3", rate=SAMPLE_RATE)
        stream.bit_rate = 128_000
        stream.layout = "mono"
        samples = tone_samples()
        frame_size = 1152
        for start in range(0, len(samples), frame_size):
            chunk = samples[start:start + frame_size]
            frame = av.AudioFrame.from_ndarray(chunk.reshape(1, -1), format="s16", layout="mono")
            frame.rate = SAMPLE_RATE
            frame.pts = start
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()
