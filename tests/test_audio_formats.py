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

# Sonorium's images build FFmpeg to read these formats but write only MP3, so
# inside an image the files are made elsewhere first:
#   python tests/test_audio_formats.py DIR
# and SONORIUM_TEST_AUDIO=DIR points the test at them.
MADE = os.environ.get("SONORIUM_TEST_AUDIO")

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


def test_has_what_sonorium_reads_and_writes():
    """The decoders, demuxers and MP3 encoder Sonorium's own FFmpeg build must keep (sonorium_addon/ffmpeg/build.sh)."""
    for decoder in ("mp3float", "flac", "vorbis", "opus", "aac", "alac", "pcm_s16le", "pcm_s24le", "pcm_f32le"):
        av.codec.Codec(decoder, "r")
    assert {"mp3", "wav", "flac", "ogg", "mov,mp4,m4a,3gp,3g2,mj2"} <= av.formats_available
    assert av.codec.Codec("mp3", "w").name == "libmp3lame"
    # The audio cache (sonorium/engine/pcm_cache.py) writes stereo FLAC
    av.codec.Codec("flac", "w")
    av.format.ContainerFormat("flac", "w")


@pytest.mark.parametrize("ext", list(FORMATS))
def test_decodes_with_pyav_alone(recording, tmp_path, no_programs, ext):
    container, codec, rate, layout, fmt, options = FORMATS[ext]
    if MADE:
        path = Path(MADE) / f"tone{ext}"
        assert path.exists(), f"{path} is missing: make the files with python tests/test_audio_formats.py DIR"
    else:
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


if __name__ == "__main__":
    # python tests/test_audio_formats.py DIR: make the test files in DIR
    import sys

    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    for ext, spec in FORMATS.items():
        write_tone(out / f"tone{ext}", *spec)
        print(out / f"tone{ext}")
