"""
Listening to the audio: read-only copies of what a channel plays (taps), and
a theme's files decoded for analysis. Neither can hurt playback.

A channel tap reads the channel's mix the way a speaker does, from the
channel's broadcast (core/channel.py), in its own thread: a slow listener
only falls behind itself (blocks it missed are counted in `dropped`), and it
never counts as a listener, so it keeps no channel running. Blocks are the
mix as it goes to the speakers (after fades, ramps and sounds over it):
int16, shape (1, 1024), mono, 44.1 kHz, read-only, the same array for every
listener (a stereo mix, Settings > Audio Settings > Stereo, is folded to mono
for taps: left and right averaged), each with its place on the channel's
sample clock:

    TapBlock(samples, mixer_sample, mixer_at)
        mixer_sample  the block's first sample, counted from when the channel
                      started making audio (its broadcast's chunks x 1024)
        mixer_at      when it left the mixer (Unix seconds, server clock):
                      the cue timeline's clock (core/timeline.py)

A track tap (track="Rain") gets one track's own blocks before mixing (its
level, its fades), opt-in because it keeps a copy per block: the mix hands
each block to it without waiting (a bounded queue; the oldest dropped).

decode_theme_file(): a file of a theme, by its track key, decoded by core
(core/decode.py) to float32 in -1..1, mono, 44.1 kHz, so plugins don't ship
FFmpeg. Only the theme's own audio files can be read.

Every limit is in LIMITS (adjustable here).
"""
from __future__ import annotations

import asyncio
import inspect
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from sonorium.decoders import to_channels
from sonorium.obs import logger

SAMPLE_RATE = 44100
BLOCK = 1024
LIMITS = {
    "taps_per_plugin": 16,        # channel and track taps together
    "track_queue_blocks": 64,     # a track tap's queue (about 1.5 s); the oldest dropped when full
    "handler_timeout": 5.0,       # an async handler's longest wait per block
    "decode_max_seconds": 1800.0,  # the longest part of a file decoded (0: the whole file)
}


@dataclass(frozen=True)
class TapBlock:
    samples: np.ndarray   # int16 (1, 1024), read-only
    mixer_sample: int
    mixer_at: float
    channel_id: str
    track: Optional[str] = None


class TapError(ValueError):
    """A tap or a decode that can't be done (the message is shown to the caller)."""


def _read_only(block: np.ndarray) -> np.ndarray:
    """The block as taps get it: mono (a stereo block folded), read-only."""
    view = to_channels(block, 1).view()
    view.flags.writeable = False
    return view


class Tap:
    """One listener: a thread calling handler(TapBlock) for each block, until close()."""

    def __init__(self, service: "Taps", owner: Optional[str], channel_id: str, handler: Callable,
                 track: Optional[str] = None, loop=None):
        self.service, self.owner, self.channel_id, self.track = service, owner, channel_id, track
        self.handler = handler
        self.loop = loop
        self.delivered = 0
        self.dropped = 0
        self.failures = 0
        self._stop = threading.Event()
        self._queue: deque = deque(maxlen=LIMITS["track_queue_blocks"])  # track taps only
        self._ready = threading.Event()
        self._is_async = inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(
            getattr(handler, "__call__", None))
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"tap-{owner or 'api'}-{channel_id}")

    def start(self) -> "Tap":
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        self._ready.set()
        self.service._closed(self)

    @property
    def closed(self) -> bool:
        return self._stop.is_set()

    # Called by the mix for a track tap: never waits
    def push(self, block: np.ndarray, mixer_sample: int) -> None:
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1
        self._queue.append((block, mixer_sample, time.time()))
        self._ready.set()

    def _deliver(self, block: TapBlock) -> None:
        try:
            if self._is_async:
                loop = self.loop
                if loop is None or loop.is_closed():
                    return
                future = asyncio.run_coroutine_threadsafe(self.handler(block), loop)
                future.result(timeout=LIMITS["handler_timeout"])
            else:
                result = self.handler(block)
                if inspect.isawaitable(result) and self.loop is not None:
                    asyncio.run_coroutine_threadsafe(result, self.loop).result(timeout=LIMITS["handler_timeout"])
            self.delivered += 1
        except Exception as e:
            self.failures += 1
            if self.failures <= 3 or self.failures % 1000 == 0:
                logger.warning(f"Audio tap of {self.owner or 'a caller'} on {self.channel_id} failed: {e}")

    def _run(self) -> None:
        if self.track is not None:
            self._run_track()
        else:
            self._run_channel()

    def _run_track(self) -> None:
        while not self._stop.is_set():
            if not self._queue:
                self._ready.wait(0.25)
                self._ready.clear()
                continue
            block, sample, at = self._queue.popleft()
            self._deliver(TapBlock(block, sample, at, self.channel_id, self.track))

    def _run_channel(self) -> None:
        channel, last, base = None, 0, 0
        while not self._stop.is_set():
            current = self.service.channel_of(self.channel_id)
            if current is not channel:
                channel = current
                last = channel.get_current_sequence() if channel is not None else 0
            if channel is None:
                self._stop.wait(0.25)
                continue
            fresh = channel.get_chunks_since(last)
            if not fresh:
                channel.wait_for_data(timeout=0.1)
                continue
            newest = channel.get_current_sequence()
            if fresh[0][0] > last + 1:
                self.dropped += fresh[0][0] - last - 1  # fell behind the broadcast: those blocks are gone
            now = time.time()
            for seq, block in fresh:
                if self._stop.is_set():
                    return
                width = block.shape[-1]
                at = now - (newest - seq) * width / SAMPLE_RATE
                self._deliver(TapBlock(_read_only(block), (seq - 1) * width, at, self.channel_id))
                last = seq


class Taps:
    """The taps of every channel. session_manager: channels by id; loop: where async handlers run."""

    def __init__(self, session_manager=None):
        self.session_manager = session_manager
        self.loop = None
        self._taps: list[Tap] = []
        self._lock = threading.Lock()
        self._track_taps: dict[int, dict] = {}  # channel number -> {track: [Tap]} (shared with its mixes)

    def channel_of(self, session_id: str):
        sm = self.session_manager
        finder = getattr(sm, "channel_of", None)
        if finder is None:
            return None
        try:
            return finder(session_id)
        except Exception:
            return None

    def track_taps_for(self, channel_number: int) -> dict:
        """The dict a channel's mixes read their track taps from (Channel._label_stream)."""
        return self._track_taps.setdefault(channel_number, {})

    def _channel_number(self, session_id: str) -> Optional[int]:
        channel = self.channel_of(session_id)
        return getattr(channel, "id", None) if channel is not None else None

    def tap(self, channel_id: str, handler: Callable, *, track: Optional[str] = None,
            owner: Optional[str] = None) -> Tap:
        if not callable(handler):
            raise TapError("handler must be callable")
        sm = self.session_manager
        getter = getattr(sm, "get", None)
        if getter is not None and getter(channel_id) is None:
            raise LookupError(f"Channel not found: {channel_id}")
        if track is not None and (not isinstance(track, str) or not track):
            raise TapError("track: a track key")
        with self._lock:
            mine = [t for t in self._taps if t.owner == owner and owner is not None]
            if owner is not None and len(mine) >= LIMITS["taps_per_plugin"]:
                raise TapError(f"At most {LIMITS['taps_per_plugin']} audio taps per plugin")
            tap = Tap(self, owner, channel_id, handler, track, loop=self.loop)
            self._taps.append(tap)
        if track is not None:
            self._wire_track(tap)
        return tap.start()

    def _wire_track(self, tap: Tap) -> None:
        """A track tap on whichever channel the session plays on now (and later: _rewire on each start)."""
        number = self._channel_number(tap.channel_id)
        tap._number = number
        if number is not None:
            self.track_taps_for(number).setdefault(tap.track, []).append(tap)

    def rewire(self) -> None:
        """Channels were assigned again (a channel started): track taps follow their session."""
        with self._lock:
            taps = [t for t in self._taps if t.track is not None]
        for tap in taps:
            number = self._channel_number(tap.channel_id)
            if number != getattr(tap, "_number", None):
                self._unwire(tap)
                self._wire_track(tap)

    def _unwire(self, tap: Tap) -> None:
        number = getattr(tap, "_number", None)
        table = self._track_taps.get(number) if number is not None else None
        if table is not None:
            lst = table.get(tap.track) or []
            if tap in lst:
                lst.remove(tap)
            if not lst:
                table.pop(tap.track, None)

    def _closed(self, tap: Tap) -> None:
        with self._lock:
            if tap in self._taps:
                self._taps.remove(tap)
        if tap.track is not None:
            self._unwire(tap)

    def close_owner(self, owner: str) -> int:
        with self._lock:
            taps = [t for t in self._taps if t.owner == owner]
        for tap in taps:
            tap.close()
        return len(taps)

    def taps(self, owner: Optional[str] = None) -> list[dict]:
        with self._lock:
            taps = [t for t in self._taps if owner is None or t.owner == owner]
        return [{"channel_id": t.channel_id, "track": t.track, "owner": t.owner, "delivered": t.delivered,
                 "dropped": t.dropped, "failures": t.failures} for t in taps]


def push_track_blocks(taps: Optional[dict], name: str, block: np.ndarray, mixer_sample: int) -> None:
    """The mix's side (theme.ThemeMix._pull): hand a track's block to its taps, never waiting."""
    if not taps:
        return
    listeners = taps.get(name)
    if not listeners:
        return
    view = _read_only(block)
    for tap in list(listeners):
        tap.push(view, mixer_sample)


def decode_theme_file(theme_folder: Path, key: str, max_seconds: Optional[float] = None) -> np.ndarray:
    """
    One of the theme's own audio files, by track key ("Rain", "Thunder/Close"),
    as float32 in -1..1, mono, 44.1 kHz. LookupError for a key the theme has no
    file for; TapError if it can't be decoded. Blocking: run it in a thread.
    """
    from sonorium.core.decode import DecodeError, decode
    from sonorium.theme_files import theme_audio_files, track_key
    folder = Path(theme_folder)
    match = next((p for p in theme_audio_files(folder) if track_key(folder, p) == key), None)
    if match is None:
        raise LookupError(f"No file '{key}' in this theme")
    limit = LIMITS["decode_max_seconds"] if max_seconds is None else float(max_seconds)
    try:
        pcm = decode(match, SAMPLE_RATE, max(0.0, limit))
    except DecodeError as e:
        raise TapError(str(e))
    if limit and pcm.shape[0] > int(limit * SAMPLE_RATE):
        pcm = pcm[:int(limit * SAMPLE_RATE)]
    return (pcm / 32768.0).astype(np.float32)


_taps = Taps()


def get_taps() -> Taps:
    return _taps


def set_taps(taps: Taps) -> Taps:
    global _taps
    old, _taps = _taps, taps
    return old
