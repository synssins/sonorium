"""
The audio cache: a theme's files decoded ahead of time at the mixer's rate,
for PcmFileDecoder, in the same folder layout under another folder
("Rain/Lute/Song.mp3" -> "<cache>/Lute/Song.flac"). Read them back with
PcmFileDecoder(lookup=cache_lookup(theme_dir, cache_dir)).

Every copy is 16-bit stereo FLAC (lossless): a mono file is stored centred
(the same samples on both sides, which FLAC stores at almost no cost), a
stereo file keeps its image. A mono mix folds the copy back to mono at
playback (left and right averaged: exactly the file's samples for a mono
file), so switching between mono and stereo never decodes a theme again.

Older caches hold 16-bit WAV copies (mono). cache_lookup still reads them
where there is no FLAC copy, and predecode_theme replaces each with a FLAC
copy (the WAV is removed once the FLAC is written).

Writing FLAC needs PyAV with FFmpeg's FLAC encoder and muxer (the add-on and
Docker images build them, sonorium_addon/ffmpeg/build.sh); without them
predecode_theme writes stereo WAV instead (flac_available() says which).
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Optional

import numpy as np

from sonorium.decoders import (CACHE_SUFFIXES, STEREO, Decoder, PyAvDecoder, SAMPLE_RATE, cache_lookup,
                               mirror_lookup)
from sonorium.theme_files import theme_audio_files

__all__ = ["predecode_theme", "write_flac", "write_wav", "flac_available", "cache_lookup"]


def flac_available() -> bool:
    """PyAV is installed with FFmpeg's FLAC encoder and muxer."""
    try:
        import av
        av.codec.Codec("flac", "w")
        av.format.ContainerFormat("flac", "w")
        return True
    except Exception:
        return False


def _int16(block) -> np.ndarray:
    """A decoder's (2, n) block as int16 (2, n)."""
    return np.clip(np.asarray(block, dtype=np.float32), -32768, 32767).astype(np.int16)


def write_flac(target, blocks) -> None:
    """Stereo (2, n) blocks (int16 scale) to a 16-bit stereo FLAC file at the mixer's rate."""
    import av
    with av.open(str(target), "w", format="flac") as out:
        stream = out.add_stream("flac", rate=SAMPLE_RATE, layout="stereo")
        stream.codec_context.format = "s16"
        for block in blocks:
            data = _int16(block)
            if not data.shape[-1]:
                continue
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(data.T).reshape(1, -1), format="s16",
                                               layout="stereo")
            frame.rate = SAMPLE_RATE
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)


def write_wav(target, blocks) -> None:
    """Stereo (2, n) blocks (int16 scale) to a 16-bit stereo WAV file at the mixer's rate."""
    with wave.open(str(target), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        for block in blocks:
            out.writeframes(np.ascontiguousarray(_int16(block).T).astype("<i2").tobytes())


def predecode_theme(theme_dir, cache_dir, decoder: Optional[Decoder] = None, flac: Optional[bool] = None) -> list[Path]:
    """
    Write a stereo copy of every audio file of the theme (decoded with
    `decoder`, PyAV by default), as FLAC (or, with flac=False or without a
    FLAC encoder, WAV). An older copy of the file in the other format is
    removed. Returns the paths written.
    """
    theme_dir, cache_dir = Path(theme_dir), Path(cache_dir)
    decoder = decoder or PyAvDecoder()
    use_flac = flac_available() if flac is None else bool(flac)
    suffix = ".flac" if use_flac else ".wav"
    lookup = mirror_lookup(theme_dir, cache_dir, suffix)
    written = []
    for source in theme_audio_files(theme_dir):
        target = lookup(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        if getattr(decoder, "accepts_channels", False):
            audio = decoder.open(source, channels=STEREO)
            blocks = audio.blocks(True)
        else:  # a mono-only decoder: its samples centred
            from sonorium.decoders import to_channels
            audio = decoder.open(source)
            blocks = (to_channels(np.asarray(b, dtype=np.float32), STEREO) for b in audio.blocks(True))
        partial = target.with_name(target.name + ".part")
        try:
            (write_flac if use_flac else write_wav)(partial, blocks)
        finally:
            audio.close()
        partial.replace(target)
        for other in CACHE_SUFFIXES:
            stale = target.with_suffix(other)
            if other != suffix and stale.exists():
                stale.unlink()
        written.append(target)
    return written
