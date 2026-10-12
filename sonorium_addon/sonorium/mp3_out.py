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

# What a listener receives: constant 128 kb/s MP3 at SAMPLE_RATE, two
# channels carrying the same mono mix (what speakers have always been sent)
MP3_BITRATE = 128_000
MP3_LAYOUT = "stereo"
MP3_SAMPLE_FORMAT = "s32p"
# What the mixer hands over: 16-bit mono blocks
MIX_SAMPLE_FORMAT, MIX_LAYOUT = "s16", "mono"


class Mp3Encoder:
    """
    One listener's MP3 encoder (LAME, through PyAV). Feed it the mixer's
    mono int16 blocks, shape (1, n); it gives back whole MP3 frames as bytes,
    ready to write to the HTTP response. A frame comes out every 1152
    samples, so some blocks give none and some give one or two.

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

    def encode(self, block) -> list[bytes]:
        """The MP3 frames completed by this block (often none)."""
        frame = av.AudioFrame.from_ndarray(block, format=MIX_SAMPLE_FORMAT, layout=MIX_LAYOUT)
        frame.rate = SAMPLE_RATE
        return [bytes(packet) for packet in self._codec.encode(frame)]

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
