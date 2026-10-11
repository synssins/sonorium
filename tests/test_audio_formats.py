"""
Every audio format Sonorium accepts (theme_files.AUDIO_EXTENSIONS: MP3, WAV,
FLAC, Ogg) decodes through PyAV's own libraries alone: no ffmpeg or ffprobe
program, nothing started with subprocess. The Docker images ship no ffmpeg
package, so this is what playback relies on. M4A/AAC (served by the track
audio endpoint, not accepted for upload) is checked too.

The files are made here with PyAV, a 440 Hz tone, and must come back at
44.1 kHz mono with their length and pitch intact.
"""
import os
import subprocess
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
av = pytest.importorskip("av")

from playback_helpers import RATE, addon_modules  # noqa: E402

SECONDS = 2.0
FREQ = 440.0

# extension: (container format, codec, sample rate, layout, sample format, extra codec options)
FORMATS = {
    ".mp3": ("mp3", "mp3", 44100, "stereo", "s16p", {}),
    ".wav": ("wav", "pcm_s16le", 22050, "mono", "s16", {}),
    ".flac": ("flac", "flac", 48000, "stereo", "s16", {}),
    ".ogg": ("ogg", "vorbis", 44100, "stereo", "fltp", {"strict": "experimental"}),
    ".opus.ogg": ("ogg", "libopus", 48000, "mono", "s16", {}),
    ".m4a": ("ipod", "aac", 44100, "stereo", "fltp", {}),
}


def write_tone(path: Path, container: str, codec: str, rate: int, layout: str, fmt: str, options: dict):
    channels = 2 if layout == "stereo" else 1
    t = np.arange(int(SECONDS * rate)) / rate
    mono = 0.3 * np.sin(2 * np.pi * FREQ * t)
    out = av.open(str(path), "w", format=container)
    stream = out.add_stream(codec, rate=rate)
    stream.layout = layout
    if options:
        stream.codec_context.options = options
    step = stream.codec_context.frame_size or 1024
    for start in range(0, len(mono), step):
        part = mono[start:start + step]
        if fmt.startswith("s16"):
            part = (part * 32767).astype(np.int16)
        else:
            part = part.astype(np.float32)
        planes = np.tile(part, (channels, 1))
        data = planes if fmt.endswith("p") else planes.T.reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(data), format=fmt, layout=layout)
        frame.rate = rate
        frame.pts = start
        for packet in stream.encode(frame):
            out.mux(packet)
    for packet in stream.encode(None):
        out.mux(packet)
    out.close()


@pytest.fixture(scope="module")
def recording():
    with addon_modules("sonorium.recording") as m:
        yield m.recording


@pytest.fixture
def no_programs(monkeypatch):
    """No ffmpeg on PATH, and starting any program fails the test."""
    monkeypatch.setenv("PATH", "")

    def refuse(*args, **kwargs):
        raise AssertionError(f"a program was started: {args[:1]}")

    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr(os, "system", refuse)


def test_accepted_formats_are_the_ones_checked():
    with addon_modules("sonorium.theme_files") as m:
        assert m.theme_files.AUDIO_EXTENSIONS == {".mp3", ".wav", ".flac", ".ogg"}


@pytest.mark.parametrize("ext", list(FORMATS))
def test_decodes_with_pyav_alone(recording, tmp_path, no_programs, ext):
    container, codec, rate, layout, fmt, options = FORMATS[ext]
    try:
        av.codec.Codec(codec, "w")
    except Exception:
        pytest.skip(f"this PyAV build has no {codec} encoder to make the test file")
    path = tmp_path / f"tone{ext}"
    write_tone(path, container, codec, rate, layout, fmt, options)

    samples = np.concatenate([np.asarray(b, dtype=np.float32).reshape(-1) for b in recording.decode_mono(path)])
    # Length: within an encoder's padding of the tone
    assert abs(len(samples) / RATE - SECONDS) < 0.1
    assert abs(recording.file_length_samples(path) / RATE - SECONDS) < 0.1
    assert abs(recording.AudioFile(path).duration_seconds - SECONDS) < 0.1
    # Pitch: the strongest frequency is still the tone, now at 44.1 kHz mono
    middle = samples[len(samples) // 4: 3 * len(samples) // 4]
    spectrum = np.abs(np.fft.rfft(middle * np.hanning(len(middle))))
    peak = np.argmax(spectrum) * RATE / len(middle)
    assert abs(peak - FREQ) < 3.0
    assert np.max(np.abs(middle)) > 0.2 * 32767
