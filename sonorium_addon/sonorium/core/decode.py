"""
Turn a sound into what the mixer plays: a file, a URL, bytes or a buffer of
samples becomes mono float32 samples (int16 range) at the stream's rate.

Decoding uses PyAV (FFmpeg), so anything FFmpeg reads works (MP3, WAV, OGG,
FLAC, AAC, ...). It is blocking work: call it with asyncio.to_thread().
Decoding stops at `max_seconds`, so a long (or endless) URL never fills
memory.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import Union

import numpy as np

URL_TIMEOUT_SECONDS = 15
MAX_BYTES = 64 * 1024 * 1024


class DecodeError(ValueError):
    """The sound could not be read (the message is shown to the caller)."""


def _resampler(rate: int):
    import av
    return av.AudioResampler(format="s16", layout="mono", rate=rate)


def _collect(frames, rate: int, max_samples: int) -> np.ndarray:
    resampler = _resampler(rate)
    parts, total = [], 0
    for frame in frames:
        for out in resampler.resample(frame):
            data = out.to_ndarray().reshape(-1)
            parts.append(data)
            total += data.shape[0]
        if max_samples and total >= max_samples:
            break
    else:
        for out in resampler.resample(None):
            data = out.to_ndarray().reshape(-1)
            parts.append(data)
            total += data.shape[0]
    pcm = np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, dtype=np.float32)
    if max_samples and pcm.shape[0] > max_samples:
        pcm = pcm[:max_samples + 1]  # one sample over: the caller sees it was longer and caps it
    return pcm


def decode(source: Union[str, Path, bytes], rate: int, max_seconds: float = 0.0) -> np.ndarray:
    """
    A file path, an http(s) URL or the bytes of an audio file, decoded. Raises
    DecodeError. Samples past max_seconds (plus one, so the caller knows it was
    cut) are not decoded.
    """
    import av
    max_samples = int(max_seconds * rate) if max_seconds and max_seconds > 0 else 0
    options = {}
    if isinstance(source, (bytes, bytearray)):
        if len(source) > MAX_BYTES:
            raise DecodeError("The sound is too large")
        target = io.BytesIO(bytes(source))
    elif isinstance(source, Path) or (isinstance(source, str) and not source.lower().startswith(("http://", "https://"))):
        path = Path(source)
        if not path.is_file():
            raise DecodeError(f"No such file: {path.name}")
        target = str(path)
    else:
        target = str(source)
        options = {"timeout": str(URL_TIMEOUT_SECONDS * 1_000_000), "rw_timeout": str(URL_TIMEOUT_SECONDS * 1_000_000)}
    try:
        container = av.open(target, options=options) if options else av.open(target)
    except Exception as e:
        raise DecodeError(f"Could not open the sound: {e}")
    try:
        streams = [s for s in container.streams if s.type == "audio"]
        if not streams:
            raise DecodeError("The file has no audio")
        return _collect(container.decode(streams[0]), rate, max_samples)
    except DecodeError:
        raise
    except Exception as e:
        raise DecodeError(f"Could not decode the sound: {e}")
    finally:
        container.close()


def from_samples(samples, sample_rate: int, rate: int, max_seconds: float = 0.0) -> np.ndarray:
    """
    A buffer of samples made by a plugin (a beep, generated noise): a numpy
    array, mono (n,) or (channels, n); float in -1..1 or int16. Resampled to
    the stream's rate and mixed down to mono.
    """
    import av
    arr = np.asarray(samples)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    if arr.ndim != 2 or arr.shape[1] == 0:
        raise DecodeError("samples must be a 1-D or (channels, n) array")
    if arr.shape[0] > arr.shape[1]:
        arr = arr.T  # (n, channels) given
    if not isinstance(sample_rate, int) or not 1000 <= sample_rate <= 384000:
        raise DecodeError("sample_rate must be a whole number of samples per second")
    if np.issubdtype(arr.dtype, np.floating):
        arr = np.clip(arr, -1.0, 1.0) * 32767.0
    mono = arr.astype(np.float32).mean(axis=0)
    if sample_rate == rate:
        pcm = mono
    else:
        frame = av.AudioFrame.from_ndarray(np.clip(mono, -32768, 32767).astype(np.int16).reshape(1, -1),
                                           format="s16", layout="mono")
        frame.rate = sample_rate
        pcm = _collect([frame], rate, 0)
    max_samples = int(max_seconds * rate) if max_seconds and max_seconds > 0 else 0
    if max_samples and pcm.shape[0] > max_samples:
        pcm = pcm[:max_samples + 1]
    return pcm.astype(np.float32)
