"""
Where the mixer's audio comes from: a Decoder turns an audio file into what
the mixer works in, 16-bit samples at the mixer's sample rate (44100 Hz),
mono or stereo as the mix is (see "Channels" below).

Every read of a track goes through the decoder in use (recording.AudioReader):
a loop pass, an Intermittent play (the whole file at once) and the length
check. A loop does not seek: each pass opens the file again.

- PyAvDecoder (the default): any file FFmpeg reads (MP3, WAV, FLAC, Ogg, ...),
  any rate or channel count, through PyAV. The only module that imports `av`
  for playback, and only when a file is opened.
- PcmFileDecoder: 16-bit PCM WAV files at the mixer's rate, or raw 16-bit
  mono PCM (.pcm/.raw), read with the standard library; FLAC files at the
  mixer's rate (the audio cache, sonorium.engine.pcm_cache) through PyAV.
  With `lookup` it reads an already decoded copy of each file instead of the
  file itself.

Any object with the same open() works (see Decoder): what it opens is an
object with has_audio, header_length(), blocks(flush) and close().

The decoder in use is the default one (set_default_decoder), or, in one
thread for a while, the one given to use_decoder() (the engine uses this, so
two engines, or an engine next to the server, never share a decoder).

Channels: a mix is mono (1, what Sonorium has always played) or stereo (2).
The channel count in use is the default one (set_default_channels: the
server's Stereo switch) or, in one thread for a while, the one given to
use_channels() (a mix sets its own while it makes a block). Mono blocks are
1-D arrays (n,); stereo blocks are (2, n), left then right. A mono file in a
stereo mix plays centred: the same samples on both sides. fold_to_mono() and
to_channels() convert a block.

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
        The audio, start to end: arrays of samples at the mixer's rate, int16
        or float32 on the int16 scale (-32768..32767), any lengths: 1-D mono
        arrays, or (2, n) when the file was opened with channels=2 (a decoder
        that only gives mono is made stereo by the reader: centred). `flush`
        asks for every last sample (False may leave out a converter's tail).
        The file is closed after the last block, or when the reading is
        abandoned.
        """

    def close(self) -> None:
        """Let go of the file (more than once is fine)."""


class Decoder(Protocol):
    def open(self, path) -> DecodedAudio:
        """
        Open an audio file. Raises if it can't be read. A decoder with
        `accepts_channels = True` is also called as open(path, channels=2)
        for a stereo mix and then gives (2, n) blocks.
        """


# --- channels ---

MONO, STEREO = 1, 2


def check_channels(channels) -> int:
    """1 or 2 (ValueError for anything else)."""
    if isinstance(channels, bool) or channels not in (MONO, STEREO):
        raise ValueError(f"channels must be 1 (mono) or 2 (stereo), not {channels!r}")
    return int(channels)


def fold_to_mono(block: np.ndarray) -> np.ndarray:
    """
    A (2, n) block down to (1, n): the average of left and right (int16:
    rounded half up, as FFmpeg's own downmix rounds; float: (L + R) / 2). A
    centred sound (the same on both sides) comes back exactly as it was. A
    1-D or (1, n) block is returned as it is.
    """
    block = np.asarray(block)
    if block.ndim < 2 or block.shape[0] == 1:
        return block
    if block.dtype == np.int16:
        total = block.astype(np.int32).sum(axis=0)
        return ((total + block.shape[0] // 2) // block.shape[0]).astype(np.int16).reshape(1, -1)
    return block.astype(np.float32).mean(axis=0).reshape(1, -1).astype(block.dtype, copy=False)


def to_channels(block: np.ndarray, channels: int) -> np.ndarray:
    """
    A 2-D block (channels first) with `channels` rows: a mono block is copied
    to both sides (centred), a stereo one folded to mono (fold_to_mono). A
    1-D block counts as mono. A block that already fits is returned as it is.
    """
    block = np.asarray(block)
    if block.ndim < 2:
        block = block.reshape(1, -1)
    have = block.shape[0]
    if have == channels:
        return block
    if channels == MONO:
        return fold_to_mono(block)
    if have == MONO:
        return np.repeat(block, channels, axis=0)
    return block[:channels]  # more channels than wanted: the first ones (left, right)


def _centred(block: np.ndarray, channels: int) -> np.ndarray:
    """A decoder's block (1-D mono, or channels first) as `channels` rows of float32."""
    return to_channels(np.asarray(block, dtype=np.float32), channels)


# --- PyAV (FFmpeg) ---

class _PyAvAudio:
    """
    One read of an audio file through PyAV: the file's first audio stream,
    converted to 16-bit at the mixer's rate, whatever it was recorded as:
    mono (1-D blocks), or with channels=2 stereo ((2, n) blocks; a mono file
    is decoded as mono and copied to both sides, so it plays centred and folds
    back to exactly its mono samples).
    """

    def __init__(self, source, rate: int, channels: int = MONO):
        import av  # only here: the rest of playback never needs PyAV
        self.path = source
        self._file = av.open(source)
        audio = self._file.streams.audio
        self._audio = audio[0] if len(audio) else None
        self._rate = rate
        self.channels = check_channels(channels)
        self._copies = 1  # stereo from a mono file: the mono samples on both sides
        if self.channels == MONO or self._source_channels() <= 1:
            self._to_mixer_format = av.AudioResampler(format="s16", layout="mono", rate=rate)
            self._copies = self.channels
        else:
            self._to_mixer_format = av.AudioResampler(format="s16p", layout="stereo", rate=rate)
        self._closed = False

    def _source_channels(self) -> int:
        audio = self._audio
        if audio is None:
            return 1
        try:
            return int(audio.codec_context.layout.nb_channels)
        except Exception:
            return int(getattr(audio, "channels", 1) or 1)

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
                    if self.channels == MONO:
                        yield converted.to_ndarray().reshape(-1).astype(np.float32)
                    elif self._copies > 1:
                        mono = converted.to_ndarray().reshape(1, -1).astype(np.float32)
                        yield np.repeat(mono, self._copies, axis=0)
                    else:
                        yield converted.to_ndarray().astype(np.float32)  # s16p stereo: (2, n)
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._file.close()


class PyAvDecoder:
    """Decodes any file FFmpeg reads, through PyAV (`av`): what Sonorium has always done."""
    accepts_channels = True

    def __init__(self, sample_rate: int = SAMPLE_RATE):
        self.sample_rate = int(sample_rate)

    def open(self, path, channels: int = MONO) -> _PyAvAudio:
        return _PyAvAudio(path, self.sample_rate, channels)

    def __repr__(self) -> str:
        return "PyAvDecoder()"


# --- PCM files (standard library) ---

RAW_SUFFIXES = (".pcm", ".raw")


class _PcmAudio:
    """
    A 16-bit PCM WAV file, or raw 16-bit little-endian mono PCM, read in
    blocks: mono (a stereo file's channels averaged) or, with channels=2,
    stereo (a mono file centred).
    """

    def __init__(self, path: Path, rate: int, block_frames: int, channels: int = MONO):
        self.path = path
        self.channels = check_channels(channels)
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
                if self.channels == STEREO:
                    yield _centred(samples.reshape(-1, self._channels).T, STEREO)
                    continue
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


class _FlacAudio:
    """
    A FLAC file at the mixer's rate (the audio cache: 16-bit stereo, see
    sonorium.engine.pcm_cache), decoded with PyAV, no resampling: stereo
    blocks (2, n), or mono (left and right averaged, as for a stereo WAV).
    """

    def __init__(self, path: Path, rate: int, channels: int = MONO):
        import av  # only for FLAC
        self.path = path
        self.channels = check_channels(channels)
        self._file = av.open(str(path))
        audio = self._file.streams.audio
        self._audio = audio[0] if len(audio) else None
        if self._audio is not None and self._audio.codec_context.sample_rate != rate:
            found = self._audio.codec_context.sample_rate
            self.close()
            raise ValueError(f"{path.name}: {found} Hz; PcmFileDecoder reads {rate} Hz only (no resampling)")
        self._rate = rate
        self._closed = False

    @property
    def has_audio(self) -> bool:
        return self._audio is not None

    def header_length(self) -> Optional[int]:
        audio = self._audio
        if audio is None or not (audio.duration and audio.time_base):
            return None
        return int(round(float(audio.duration * audio.time_base) * self._rate))

    def blocks(self, flush: bool = True):
        try:
            for frame in self._file.decode(self._audio):
                data = frame.to_ndarray()
                if frame.format.is_planar:
                    samples = data  # (channels, n)
                else:
                    samples = data.reshape(-1, len(frame.layout.channels)).T  # interleaved
                if samples.dtype != np.int16:  # a FLAC of more than 16 bits: s32, scaled to 16
                    samples = (samples.astype(np.float64) / 65536.0)
                if self.channels == STEREO:
                    yield _centred(samples, STEREO)
                elif samples.shape[0] > 1:
                    yield samples.astype(np.float32).mean(axis=0)
                else:
                    yield samples.reshape(-1).astype(np.float32)
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._file.close()


class PcmFileDecoder:
    """
    Reads PCM with the standard library: 16-bit WAV at the mixer's rate (any
    channel count: averaged to mono for a mono mix, a mono file centred for a
    stereo one) or raw 16-bit little-endian mono (.pcm, .raw); and FLAC at the
    mixer's rate (the audio cache) through PyAV, imported only for a FLAC
    file. No resampling: a file at another rate is refused (ValueError).

    `lookup` maps a track's own file (an MP3, say) to the PCM file to read
    for it: a copy decoded ahead of time (see cache_lookup, mirror_lookup and
    sonorium.engine.pcm_cache), or by a platform's own decoder. Without it
    the track's file itself is read.
    """
    accepts_channels = True

    def __init__(self, sample_rate: int = SAMPLE_RATE, lookup: Optional[Callable[[Path], Path]] = None,
                 block_frames: int = 4096):
        self.sample_rate = int(sample_rate)
        self.lookup = lookup
        self.block_frames = int(block_frames)

    def open(self, path, channels: int = MONO):
        source = Path(path)
        target = Path(self.lookup(source)) if self.lookup is not None else source
        if target.suffix.lower() == ".flac":
            return _FlacAudio(target, self.sample_rate, channels)
        return _PcmAudio(target, self.sample_rate, self.block_frames, channels)

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


CACHE_SUFFIXES = (".flac", ".wav")  # the audio cache's entries: FLAC now, WAV from before


def cache_lookup(source_root, cache_root) -> Callable[[Path], Path]:
    """
    A lookup for PcmFileDecoder reading the audio cache (sonorium.engine.pcm_cache):
    the file's FLAC copy, or a WAV copy an older cache made when there is no
    FLAC one ("Rain/Lute/Song.mp3" -> "<cache>/Lute/Song.flac", else ".wav").
    """
    mirrors = [mirror_lookup(source_root, cache_root, suffix) for suffix in CACHE_SUFFIXES]

    def lookup(path: Path) -> Path:
        first = mirrors[0](path)
        if first == Path(path) or first.exists():
            return first
        for mirror in mirrors[1:]:
            other = mirror(path)
            if other.exists():
                return other
        return first
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


class _Centred:
    """A mono-only decoder's file read for a stereo mix: each block on both sides."""

    def __init__(self, audio, channels: int):
        self._audio = audio
        self.channels = channels

    @property
    def has_audio(self) -> bool:
        return bool(self._audio.has_audio)

    def header_length(self):
        return self._audio.header_length()

    def blocks(self, flush: bool = True):
        for block in self._audio.blocks(flush):
            yield _centred(block, self.channels)

    def close(self) -> None:
        self._audio.close()


def open_audio(path, channels: Optional[int] = None) -> DecodedAudio:
    """
    Open a file with the decoder in use, for a mix of `channels` (None: the
    channel count in use). Mono opens it exactly as always (open(path)).
    """
    decoder = current_decoder()
    channels = current_channels() if channels is None else check_channels(channels)
    if channels == MONO:
        return decoder.open(path)
    if getattr(decoder, "accepts_channels", False):
        return decoder.open(path, channels=channels)
    return _Centred(decoder.open(path), channels)


# --- the channel count in use ---

_default_channels = MONO


def set_default_channels(channels: int) -> None:
    """The channel count every thread mixes at unless use_channels() says otherwise (the server's Stereo switch)."""
    global _default_channels
    _default_channels = check_channels(channels)


def default_channels() -> int:
    return _default_channels


def current_channels() -> int:
    """The channel count in use in this thread now: 1 (mono) or 2 (stereo)."""
    return getattr(_local, "channels", None) or _default_channels


@contextlib.contextmanager
def use_channels(channels: Optional[int]):
    """Mix at `channels` in this thread inside the with block (None: no change). Never across a yield."""
    if channels is None:
        yield
        return
    channels = check_channels(channels)
    previous = getattr(_local, "channels", None)
    _local.channels = channels
    try:
        yield
    finally:
        _local.channels = previous
