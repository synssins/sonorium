"""
The last step before a listener: the mixer's blocks become MP3, sent no
faster than they play.

Two places hand audio to listeners: a theme's own stream (/stream/{id},
theme.py) and a channel's broadcast (core/channel.py). Both use the pieces
here, so every speaker gets the same format and the same timing.
"""
import time

import av

from sonorium.recording import SAMPLE_RATE

import math

import numpy as np

# What a listener receives: constant 128 kb/s MP3 at SAMPLE_RATE, two
# channels: the mono mix on both (what speakers have always been sent), or
# the stereo mix's left and right (Settings > Audio Settings > Stereo)
MP3_BITRATE = 128_000
MP3_LAYOUT = "stereo"
MP3_SAMPLE_FORMAT = "s32p"
# What the mixer hands over: 16-bit mono blocks (1, n), or stereo (2, n)
MIX_SAMPLE_FORMAT, MIX_LAYOUT = "s16", "mono"
# FFmpeg puts a mono block on both sides 3 dB down (1/sqrt(2) each): a
# stereo block gets the same, so a centred sound is as loud with Stereo on
# as off. 16-bit to 32-bit samples: times 65536.
CENTRE_GAIN = math.sqrt(0.5)
_S16_TO_S32 = 65536.0


class Mp3Encoder:
    """
    One listener's MP3 encoder (LAME, through PyAV). Feed it the mixer's
    int16 blocks, mono (1, n) or stereo (2, n), in any order; it gives back
    whole MP3 frames as bytes, ready to write to the HTTP response. A frame
    comes out every 1152 samples, so some blocks give none and some give one
    or two.

    A mono block goes through FFmpeg's own mono-to-stereo conversion, exactly
    as Sonorium has always sent it; a stereo block keeps its left and right.
    The stream stays one MP3 stream when blocks change from one to the other
    (the Stereo switch changed while a speaker listens).

    It works straight on the codec, with no container: raw MP3 frames are a
    valid stream on their own, and there is no file or header to write.
    """

    def __init__(self):
        codec = av.CodecContext.create("mp3", "w")
        codec.sample_rate = SAMPLE_RATE
        codec.bit_rate = MP3_BITRATE
        codec.layout = MP3_LAYOUT
        codec.format = MP3_SAMPLE_FORMAT
        self._codec = codec
        self._upmix = None  # mono blocks to the codec's format (FFmpeg's conversion)

    def _planes(self, block) -> list:
        """The block as the codec takes it: s32 planes (2, n)."""
        block = np.asarray(block)
        if block.ndim == 1 or block.shape[0] == 1:
            if self._upmix is None:
                self._upmix = av.AudioResampler(format=MP3_SAMPLE_FORMAT, layout=MP3_LAYOUT, rate=SAMPLE_RATE)
            frame = av.AudioFrame.from_ndarray(block.reshape(1, -1), format=MIX_SAMPLE_FORMAT, layout=MIX_LAYOUT)
            frame.rate = SAMPLE_RATE
            return [converted.to_ndarray() for converted in self._upmix.resample(frame)]
        return [np.rint(block[:2].astype(np.float64) * (CENTRE_GAIN * _S16_TO_S32)).astype(np.int32)]

    def encode(self, block) -> list[bytes]:
        """The MP3 frames completed by this block (often none)."""
        out = []
        for planes in self._planes(block):
            # Every frame made the same way, whichever kind of block it came from
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(planes), format=MP3_SAMPLE_FORMAT,
                                               layout=MP3_LAYOUT)
            frame.rate = SAMPLE_RATE
            out.extend(bytes(packet) for packet in self._codec.encode(frame))
        return out

    def close(self) -> None:
        """Let go of the encoder. Nothing is flushed: the listener has gone."""
        self._codec = None


class RealtimePacer:
    """
    Keeps a producer from running ahead of real time. Tell it how much audio
    went out after each block; when that is more than the wall-clock time
    since the pacer was made, it sleeps off the difference. A producer that
    is behind (a slow start, a stall) never sleeps, so it catches up.
    """

    def __init__(self):
        self._started = time.time()
        self.sent = 0.0  # seconds of audio sent so far

    def advance(self, seconds: float) -> float:
        """Count `seconds` more audio as sent; sleep while ahead. Returns how far ahead it was (negative: behind)."""
        self.sent += seconds
        lead = self.sent - (time.time() - self._started)
        if lead > 0:
            time.sleep(lead)
        return lead
