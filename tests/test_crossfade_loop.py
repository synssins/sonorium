"""Crossfade looping must not abort mid-crossfade and restart the track (issue #38)."""

import importlib.util
import logging
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")


def _load_recording():
    # Load recording.py on its own; the sonorium package pulls in HA/MQTT deps.
    obs = types.ModuleType("sonorium.obs")
    obs.logger = logging.getLogger("sonorium-test")
    saved = {name: sys.modules.get(name) for name in ("sonorium", "sonorium.obs")}
    sys.modules["sonorium"] = types.ModuleType("sonorium")
    sys.modules["sonorium.obs"] = obs
    try:
        path = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium" / "recording.py"
        spec = importlib.util.spec_from_file_location("sonorium_recording_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod
    return module


recording = _load_recording()
CROSSFADE_SAMPLES = recording.CROSSFADE_SAMPLES
SAMPLE_RATE = recording.SAMPLE_RATE
CrossfadeRecordingStream = recording.CrossfadeRecordingStream

TRACK_SECONDS = 4
LOOPS = 5


def _write_noise_wav(path, seconds):
    rng = np.random.default_rng(38)
    samples = (rng.uniform(-1, 1, int(seconds * SAMPLE_RATE)) * 8000).astype(np.int16)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SAMPLE_RATE)
        f.writeframes(samples.tobytes())
    return len(samples)


def _run(tmp_path, monkeypatch, duration_error=0):
    path = tmp_path / "noise.wav"
    n = _write_noise_wav(path, TRACK_SECONDS)
    meta = SimpleNamespace(path=str(path), duration_samples=n + duration_error)
    instance = SimpleNamespace(meta=meta, volume=1.0, name="noise")

    opens = []
    real_open = recording.av.open
    monkeypatch.setattr(recording.av, "open", lambda *a, **k: opens.append(1) or real_open(*a, **k))

    stream = CrossfadeRecordingStream(instance)
    chunks = int(LOOPS * (n - CROSSFADE_SAMPLES) / stream.CHUNK_SIZE)
    rms = np.array([np.sqrt(np.mean(next(stream).astype(np.float64) ** 2)) for _ in range(chunks)])
    return len(opens), rms


def test_each_loop_opens_the_file_once(tmp_path, monkeypatch):
    opens, _ = _run(tmp_path, monkeypatch)
    # First play + one new decoder per crossfade. A restart after an aborted
    # crossfade would open the file again.
    assert opens <= LOOPS + 1


def test_no_dropout_at_loop_point(tmp_path, monkeypatch):
    _, rms = _run(tmp_path, monkeypatch)
    # Equal-power crossfade of uncorrelated noise keeps the level steady.
    assert rms.min() > 0.6 * np.median(rms)


def test_overstated_duration_recovers(tmp_path, monkeypatch):
    # Metadata says the track is longer than it decodes to (common with MP3 headers).
    opens, rms = _run(tmp_path, monkeypatch, duration_error=SAMPLE_RATE // 4)
    assert opens <= LOOPS + 1
    # After the first loop the real length is known, so later loops are clean.
    first_loop = int((TRACK_SECONDS * SAMPLE_RATE) / 1024) + 5
    assert rms[first_loop:].min() > 0.6 * np.median(rms)
