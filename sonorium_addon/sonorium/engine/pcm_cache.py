"""
Decode a theme's files ahead of time into 16-bit mono WAV at the mixer's
rate, for PcmFileDecoder: the same folder layout under another folder
("Rain/Lute/Song.mp3" -> "<cache>/Lute/Song.wav"). Read the copies back with
PcmFileDecoder(lookup=mirror_lookup(theme_dir, cache_dir)).
"""
from __future__ import annotations

import wave
from pathlib import Path
from typing import Optional

import numpy as np

from sonorium.decoders import Decoder, PyAvDecoder, SAMPLE_RATE, mirror_lookup
from sonorium.theme_files import theme_audio_files


def predecode_theme(theme_dir, cache_dir, decoder: Optional[Decoder] = None) -> list[Path]:
    """Write a WAV copy of every audio file of the theme (with `decoder`, PyAV by default). Returns the WAV paths."""
    theme_dir, cache_dir = Path(theme_dir), Path(cache_dir)
    decoder = decoder or PyAvDecoder()
    lookup = mirror_lookup(theme_dir, cache_dir)
    written = []
    for source in theme_audio_files(theme_dir):
        target = lookup(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        audio = decoder.open(source)
        with wave.open(str(target), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            for block in audio.blocks(True):
                out.writeframes(np.clip(np.asarray(block, dtype=np.float32), -32768, 32767)
                                .astype("<i2").tobytes())
        written.append(target)
    return written
