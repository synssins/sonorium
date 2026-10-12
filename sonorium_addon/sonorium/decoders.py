"""
Where the mixer's audio comes from: a Decoder turns an audio file into what
the mixer works in, 16-bit mono at the mixer's sample rate (44100 Hz).

Every read of a track goes through the decoder in use (recording.AudioReader):
a loop pass, an Intermittent play (the whole file at once) and the length
check. A loop does not seek: each pass opens the file again.

- PyAvDecoder (the default): any file FFmpeg reads (MP3, WAV, FLAC, Ogg, ...),
  any rate or channel count, through PyAV. The only module that imports `av`
  for playback, and only when a file is opened.
- PcmFileDecoder: 16-bit PCM WAV files at the mixer's rate, or raw 16-bit
  mono PCM (.pcm/.raw), read with the standard library. With `lookup` it reads
  an already decoded copy of each file instead of the file itself.

Any object with the same open() works (see Decoder): what it opens is an
object with has_audio, header_length(), blocks(flush) and close().

The decoder in use is the default one (set_default_decoder), or, in one
thread for a while, the one given to use_decoder() (the engine uses this, so
two engines, or an engine next to the server, never share a decoder).

No imports from the rest of Sonorium, so recording.py can load this file on
its own.
"""
from __future__ import annotations

import contextlib
import itertools
import threading
import wave
from pathlib import Path
from typing import Callable, Iterator, Optional, Protocol

import numpy as np

# The mixer's sample rate (recording.SAMPLE_RATE)
SAMPLE_RATE = 44100


class DecodedAudio(Protocol):
    """One opened file, read from its start (open the file again to read it again)."""

    @property
    def has_audio(self) -> bool:
        """The file holds audio (False: nothing to play)."""

    def header_length(self) -> Optional[int]:
        """The file's length as its header gives it, in samples at the mixer's rate (None: unknown)."""

    def blocks(self, flush: bool = True) -> Iterator[np.ndarray]:
        """
        The audio, start to end: 1-D arrays of mono samples at the mixer's rate,
        int16 or float32 on the int16 scale (-32768..32767), any lengths. `flush`
        asks for every last sample (False may leave out a converter's tail). The
        file is closed after the last block, or when the reading is abandoned.
        """

    def close(self) -> None:
        """Let go of the file (more than once is fine)."""


class Decoder(Protocol):
    def open(self, path) -> DecodedAudio:
        """Open an audio file. Raises if it can't be read."""


# --- PyAV (FFmpeg) ---

class _PyAvAudio:
    """
    One read of an audio file through PyAV: the file's first audio stream,
    converted to 16-bit mono at the mixer's rate, whatever it was recorded as.
    """

    def __init__(self, source, rate: int):
        import av  # only here: the rest of playback never needs PyAV
        self.path = source
        self._file = av.open(source)
        audio = self._file.streams.audio
        self._audio = audio[0] if len(audio) else None
        self._rate = rate
        self._to_mixer_format = av.AudioResampler(format="s16", layout="mono", rate=rate)
        self._closed = False

    @property
    def has_audio(self) -> bool:
        return self._audio is not None

    def header_length(self) -> Optional[int]:
        audio = self._audio
        if audio is None or not (audio.duration and audio.time_base):
            return None
        return int(float(audio.duration * audio.time_base) * self._rate)

    def blocks(self, flush: bool = True):
        end_of_file = [None] if flush else []  # None asks the converter for what it holds
        try:
            for frame in itertools.chain(self._file.decode(self._audio), end_of_file):
                for converted in self._to_mixer_format.resample(frame):
                    yield converted.to_ndarray().reshape(-1).astype(np.float32)
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._file.close()


class PyAvDecoder:
    """Decodes any file FFmpeg reads, through PyAV (`av`): what Sonorium has always done."""

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = int(sample_rate)

    def open(self, path) -> _PyAvAudio:
        return _PyAvAudio(path, self.sample_rate)

    def __repr__(self) -> str:
        return "PyAvDecoder()"


# --- PCM files (standard library) ---

RAW_SUFFIXES = (".pcm", ".raw")


class _PcmAudio:
    """A 16-bit PCM WAV file, or raw 16-bit little-endian mono PCM, read in blocks."""

    def __init__(self, path: Path, rate: int, block_frames: int):
        self.path = path
        self._block = max(1, int(block_frames))
        self._closed = False
        self._wav = None
        self._raw = None
        if path.suffix.lower() in RAW_SUFFIXES:
            self._raw = open(path, "rb")
            self._channels = 1
            self._frames = path.stat().st_size // 2
        else:
            self._wav = wave.open(str(path), "rb")
            if self._wav.getsampwidth() != 2:
                width = self._wav.getsampwidth() * 8
                self.close()
                raise ValueError(f"{path.name}: {width}-bit samples; PcmFileDecoder reads 16-bit PCM only")
            if self._wav.getframerate() != rate:
                found = self._wav.getframerate()
                self.close()
                raise ValueError(f"{path.name}: {found} Hz; PcmFileDecoder reads {rate} Hz only (no resampling)")
            self._channels = self._wav.getnchannels()
            self._frames = self._wav.getnframes()

    @property
    def has_audio(self) -> bool:
        return self._frames > 0

    def header_length(self) -> Optional[int]:
        return self._frames

    def _read(self) -> bytes:
        if self._wav is not None:
            return self._wav.readframes(self._block)
        return self._raw.read(self._block * 2)

    def blocks(self, flush: bool = True):
        try:
            while True:
                data = self._read()
                if not data:
                    return
                samples = np.frombuffer(data[: len(data) - len(data) % (2 * self._channels)], dtype="<i2")
                if self._channels > 1:
                    # Down to mono: the channels' average
                    samples = samples.reshape(-1, self._channels).astype(np.float32).mean(axis=1)
                yield samples.astype(np.float32)
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            for handle in (self._wav, self._raw):
                if handle is not None:
                    handle.close()


class PcmFileDecoder:
    """
    Reads PCM with the standard library: 16-bit WAV at the mixer's rate (any
    channel count, averaged to mono) or raw 16-bit little-endian mono (.pcm,
    .raw). No resampling: a file at another rate is refused (ValueError).

    `lookup` maps a track's own file (an MP3, say) to the PCM file to read
    for it: a copy decoded ahead of time (see mirror_lookup and
    sonorium.engine.pcm_cache), or by a platform's own decoder. Without it
    the track's file itself is read.
    """

    def __init__(self, sample_rate: int = SAMPLE_RATE, lookup: Optional[Callable[[Path], Path]] = None,
                 block_frames: int = 4096):
        self.sample_rate = int(sample_rate)
        self.lookup = lookup
        self.block_frames = int(block_frames)

    def open(self, path) -> _PcmAudio:
        source = Path(path)
        target = Path(self.lookup(source)) if self.lookup is not None else source
        return _PcmAudio(target, self.sample_rate, self.block_frames)

    def __repr__(self) -> str:
        return "PcmFileDecoder()"


def mirror_lookup(source_root, cache_root, suffix: str = ".wav") -> Callable[[Path], Path]:
    """
    A lookup for PcmFileDecoder: a file under `source_root` is read from the
    same place under `cache_root`, with `suffix` ("Rain/Lute/Song.mp3" ->
    "<cache>/Lute/Song.wav"). A file outside `source_root` keeps its own path.
    """
    source_root, cache_root = Path(source_root).resolve(), Path(cache_root)

    def lookup(path: Path) -> Path:
        try:
            relative = Path(path).resolve().relative_to(source_root)
        except ValueError:
            return Path(path)
        return (cache_root / relative).with_suffix(suffix)
    return lookup


# --- the decoder in use ---

_default: Decoder = PyAvDecoder()
_local = threading.local()


def set_default_decoder(decoder: Decoder) -> None:
    """The decoder every thread uses unless use_decoder() says otherwise."""
    global _default
    _default = decoder


def current_decoder() -> Decoder:
    """The decoder in use in this thread now."""
    return getattr(_local, "decoder", None) or _default


@contextlib.contextmanager
def use_decoder(decoder: Optional[Decoder]):
    """Use `decoder` in this thread inside the with block (None: no change)."""
    if decoder is None:
        yield
        return
    previous = getattr(_local, "decoder", None)
    _local.decoder = decoder
    try:
        yield
    finally:
        _local.decoder = previous


def open_audio(path) -> DecodedAudio:
    """Open a file with the decoder in use."""
    return current_decoder().open(path)
