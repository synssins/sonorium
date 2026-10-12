"""
A tiny generated theme for the engine tests: sine and noise WAV files written
on the spot, with metadata.json and presets.json, so no real theme is needed.

    Drone.wav       220 Hz sine, 6 s, Background (loops with a crossfade)
    Wind.wav        noise, 8 s, Ebb & Flow (drifts at random)
    Chirp.wav       2 kHz sine, 0.6 s, Intermittent (waits at random)
    Birds/*.wav     three short tones in a group (takes turns)
"""
import json
import wave
from pathlib import Path

import numpy as np

RATE = 44100


def write_wav(path: Path, samples, rate: int = RATE, channels: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(samples, dtype="<i2")
    with wave.open(str(path), "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(data.tobytes())


def sine(freq: float, seconds: float, amp: float = 8000.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)


def noise(seconds: float, amp: float = 6000.0, seed: int = 7):
    return (np.random.default_rng(seed).uniform(-1, 1, int(seconds * RATE)) * amp).astype(np.int16)


# Settings > Advanced values that make the random parts happen within seconds
FAST_VALUES = {"sparse_min_interval": 1.0, "sparse_max_interval": 4.0, "group_gap": 2.0}


def make_theme(root: Path, name: str = "Test Theme") -> Path:
    folder = Path(root) / name
    write_wav(folder / "Drone.wav", sine(220.0, 6.0))
    write_wav(folder / "Wind.wav", noise(8.0))
    write_wav(folder / "Chirp.wav", sine(2000.0, 0.6, 5000.0))
    for i, freq in enumerate((900.0, 1200.0, 1500.0)):
        write_wav(folder / "Birds" / f"Bird {i + 1}.wav", sine(freq, 0.8 + 0.2 * i, 4000.0))
    metadata = {
        "spec_version": 2, "id": "engine-test-theme", "name": name,
        "short_file_threshold": 15.0, "ebb_duration": True, "auto_retired": True, "intrusions_retired": True,
        "tracks": {
            "Drone": {"volume": 0.8, "presence": 1.0, "playback_mode": "continuous"},
            "Wind": {"volume": 0.9, "presence": 0.2, "playback_mode": "presence", "volume_low": 0.1},
            "Chirp": {"volume": 0.7, "presence": 0.9, "playback_mode": "sparse"},
            "Birds/Bird 1": {"volume": 0.6, "presence": 0.8},
            "Birds/Bird 2": {"volume": 0.6, "presence": 0.8},
            "Birds/Bird 3": {"volume": 0.6, "presence": 0.8},
        },
        "groups": {"Birds": {"mode": "intermittent", "gap_min": 1.0, "gap_max": 3.0}},
    }
    (folder / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    presets = {"presets": {
        "calm": {"name": "Calm", "tracks": {"Drone": {"volume": 0.3}, "Wind": {"muted": True}}},
        "busy": {"name": "Busy", "is_default": True, "tracks": {"Chirp": {"presence": 1.0, "volume": 0.9}}},
    }}
    (folder / "presets.json").write_text(json.dumps(presets, indent=2), encoding="utf-8")
    return folder
