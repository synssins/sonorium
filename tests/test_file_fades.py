"""A file's own Fade in / Fade out: applied when decoded, at every start, end and loop."""

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
        spec = importlib.util.spec_from_file_location("sonorium_recording_fades_under_test", path)
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
RATE = recording.SAMPLE_RATE
LEVEL = 8000


def _write_wav(path, samples):
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(np.asarray(samples, dtype=np.int16).tobytes())


def _abrupt_start(tmp_path, seconds=6.0, tail=1.0):
    """Noise that starts at full level and fades out by itself over its last `tail` seconds (like Wind_Massive)."""
    rng = np.random.default_rng(7)
    n = int(seconds * RATE)
    x = rng.uniform(-1, 1, n) * LEVEL
    k = int(tail * RATE)
    x[n - k:] *= np.linspace(1, 0.02, k)
    path = tmp_path / "abrupt.wav"
    _write_wav(path, x)
    return str(path), n


def _decoded(gen):
    return np.concatenate([np.asarray(b, dtype=np.float32).reshape(-1) for b in gen])


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


def test_no_fades_decodes_as_before(tmp_path):
    path, _ = _abrupt_start(tmp_path)
    plain = _decoded(recording.decode_mono(path))
    same = _decoded(recording.decode_faded(path, 0, 0))
    assert np.array_equal(plain, same)


def test_fades_shape_only_the_edges(tmp_path):
    path, n = _abrupt_start(tmp_path, tail=0.0001)
    fin, fout = RATE, RATE // 2
    plain = _decoded(recording.decode_mono(path))
    faded = _decoded(recording.decode_faded(path, fin, fout))
    assert len(faded) == len(plain)
    # Rises from silence over the first second, falls to silence over the last half second
    assert _rms(faded[:RATE // 20]) < 0.1 * _rms(plain[:RATE // 20])
    assert _rms(faded[fin - RATE // 20:fin]) > 0.95 * _rms(plain[fin - RATE // 20:fin])
    assert abs(faded[-1]) < 0.01 * LEVEL
    # The middle is untouched
    mid = slice(fin, len(plain) - fout)
    assert np.array_equal(faded[mid], plain[mid])
    # The same as fading the whole file at once (Intermittent plays decode the file whole)
    assert np.allclose(faded, recording.apply_file_fades(plain, fin, fout), atol=1e-3)


def test_fade_settings_are_read_from_the_track():
    track = SimpleNamespace(fade_in=2.0, fade_out="")
    assert recording.file_fade_samples(track) == (2 * RATE, 0)
    assert recording.file_fade_samples(SimpleNamespace()) == (0, 0)
    assert recording.file_fade_samples(SimpleNamespace(fade_in=999, fade_out=-3)) == (int(30 * RATE), 0)


def _arrival(tmp_path, fade_in):
    """
    Loop a file whose first 3 s are a 440 Hz tone at full level (an abrupt
    start) and whose last 6 s are noise that fades out by itself over 1.5 s.
    Returns how long after the loop's overlap begins the tone (the file's
    start, coming back) reaches 90% of its level, in seconds.
    """
    rng = np.random.default_rng(3)
    t = np.arange(int(3 * RATE)) / RATE
    tone = np.sin(2 * np.pi * 440 * t) * LEVEL
    noise = rng.uniform(-1, 1, int(6 * RATE)) * LEVEL * 0.5
    k = int(1.5 * RATE)
    noise[-k:] *= np.linspace(1, 0.02, k)
    x = np.concatenate([tone, noise])
    path = tmp_path / "tone_then_noise.wav"
    _write_wav(path, x)
    n = len(x)
    instance = SimpleNamespace(meta=SimpleNamespace(path=str(path), duration_samples=n), volume=1.0, name="t",
                               fade_in=fade_in, fade_out=0.0)
    stream = recording.CrossfadeRecordingStream(instance)
    size = stream.CHUNK_SIZE
    out = np.concatenate([next(stream).reshape(-1) for _ in range(int(1.6 * n / size))]).astype(np.float64)
    start = n - stream.overlap  # where the first loop's overlap begins
    w = np.exp(-2j * np.pi * 440 * np.arange(size) / RATE)
    for c in range(start // size, len(out) // size):
        chunk = out[c * size:(c + 1) * size]
        if 2 * abs(np.sum(chunk * w)) / size >= 0.9 * LEVEL:
            return (c * size - start) / RATE, stream.overlap
    return None, stream.overlap


def test_a_marked_fade_in_brings_the_start_back_slowly_at_each_loop(tmp_path):
    plain, overlap = _arrival(tmp_path, 0.0)
    assert overlap == recording.CROSSFADE_SAMPLES and plain is not None and plain < 1.3
    faded, overlap = _arrival(tmp_path, 4.0)
    assert overlap == 4 * RATE
    # equal power: 90% at asin(0.9) of the way, 2.85 s into a 4 s fade (1.07 s into the 1.5 s crossfade)
    assert faded is not None and 2.6 < faded < 3.1
