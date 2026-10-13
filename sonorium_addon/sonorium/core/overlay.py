"""
Sounds played over a channel's mix: announcements, alerts, sound effects.

A sound is mixed into the channel's stream on top of the theme, so every
speaker, browser and output of the channel hears it at the same moment. While
a sound that asks for it plays, the channel's own mix is ducked (made
quieter) and comes back afterwards, like a radio presenter talking over the
music:

    duck  {"db": 15, "attack": 0.5, "release": 1.5}
          db       how much quieter the mix gets (decibels; 6 dB is about half as loud)
          attack   seconds the mix takes to get that quiet; the sound starts once it has
          release  seconds the mix takes to come back after the last sound

Several sounds on one channel follow their `policy` and `priority`:

    now      plays at once, over anything already playing (sound effects)
    queue    waits until the sounds playing now have finished; higher priority first
    replace  cuts what plays or waits with the same or a lower priority, then plays;
             a sound with a higher priority keeps playing and this one waits for it

Every sound has a maximum length (the Advanced page's "Longest sound", or
less): a longer one is cut there with a short fade.

The channel's generator thread calls ChannelOverlay.mix() for each chunk;
everything else (add, cancel) comes from the event loop. With nothing playing
or waiting and no duck, the channel skips this module (core/channel.py).

Samples are float32 in the int16 range (what the mixer produces), mono, at
the stream's sample rate: decode.py turns files, URLs and buffers into that.
Over a stereo mix (Settings > Audio Settings > Stereo) a sound plays centred,
the same on both sides, and the duck lowers both alike.
"""
from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

POLICIES = ("now", "queue", "replace")
CUT_FADE_SECONDS = 0.02    # a sound cut short (replaced, cancelled) fades out this quickly
CAP_FADE_SECONDS = 0.05    # a sound cut at the maximum length fades out over this
MAX_DUCK_DB = 96.0         # 96 dB down is silence for 16-bit audio


class OverlayError(ValueError):
    """A sound that can't be played (the message is shown to the caller)."""


@dataclass
class Duck:
    db: float = 15.0
    attack: float = 0.5
    release: float = 1.5

    @classmethod
    def check(cls, value, defaults: "Duck") -> Optional["Duck"]:
        """None/False: no duck; True: the defaults; a dict: the defaults with these changes."""
        if value is None or value is False:
            return None
        if value is True:
            return Duck(defaults.db, defaults.attack, defaults.release)
        if not isinstance(value, dict):
            raise OverlayError("'duck' must be true, false or an object (db, attack, release)")
        result = Duck(defaults.db, defaults.attack, defaults.release)
        for key in ("db", "attack", "release"):
            if key in value and value[key] is not None:
                number = value[key]
                if isinstance(number, bool) or not isinstance(number, (int, float)) or number < 0:
                    raise OverlayError(f"duck '{key}' must be a number, 0 or more")
                setattr(result, key, float(number))
        result.db = min(result.db, MAX_DUCK_DB)
        return result

    def to_dict(self) -> dict:
        return {"db": self.db, "attack": self.attack, "release": self.release}


_ids = itertools.count(1)


@dataclass
class Sound:
    """One sound on one channel."""
    sound_id: str
    pcm: np.ndarray                 # float32, int16 range, mono, stream rate
    volume: float = 1.0             # gain (0..1, more is louder than the file)
    duck: Optional[Duck] = None
    policy: str = "queue"
    priority: int = 0
    capped: bool = False            # cut at the maximum length
    meta: dict = field(default_factory=dict)
    state: str = "new"              # queued, waiting (for the duck), playing, done
    pos: int = 0
    seq: int = field(default_factory=lambda: next(_ids))
    cut_left: Optional[int] = None  # samples left of a cut's fade-out
    cut_reason: str = ""

    @property
    def seconds(self) -> float:
        return float(self.pcm.shape[-1])

    def to_dict(self, rate: int) -> dict:
        return {"sound_id": self.sound_id, "state": self.state, "priority": self.priority, "policy": self.policy,
                "length": round(self.pcm.shape[-1] / rate, 3), "position": round(self.pos / rate, 3),
                "volume": self.volume, "duck": self.duck.to_dict() if self.duck else None, "capped": self.capped,
                **{k: v for k, v in self.meta.items() if k in ("label", "origin")}}


def cap(pcm: np.ndarray, max_samples: int, rate: int) -> tuple[np.ndarray, bool]:
    """The samples cut at max_samples with a short fade-out (and whether they were cut)."""
    if max_samples <= 0 or pcm.shape[-1] <= max_samples:
        return pcm, False
    out = pcm[:max_samples].astype(np.float32, copy=True)
    fade = min(max_samples, max(1, int(CAP_FADE_SECONDS * rate)))
    out[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return out, True


class ChannelOverlay:
    """
    The sounds of one channel and its duck. on_event(kind, sound, reason)
    is called outside the lock: kind "started" or "ended" (reason finished,
    replaced, cancelled, stopped).
    """

    def __init__(self, sample_rate: int, on_event: Optional[Callable] = None):
        self.rate = sample_rate
        self.on_event = on_event
        self._lock = threading.Lock()
        self._sounds: list[Sound] = []      # playing, waiting or being cut
        self._queue: list[Sound] = []
        self._duck_now = 0.0                # dB the mix is down now
        self._release = 1.5                 # seconds, of the last sound that ducked
        self._release_db = 0.0              # its depth: the release moves at db / release
        self._pending: list = []            # events found with the lock held

    # --- State ---

    @property
    def busy(self) -> bool:
        """Something to mix: a sound playing or waiting, or the mix not back to full level."""
        return bool(self._sounds or self._queue or self._duck_now > 0.0)

    @property
    def duck_db(self) -> float:
        return self._duck_now

    def sounds(self) -> list[dict]:
        with self._lock:
            return [s.to_dict(self.rate) for s in self._sounds + self._queue if s.cut_left is None]

    def has(self, sound_id: str) -> bool:
        with self._lock:
            return any(s.sound_id == sound_id for s in self._sounds + self._queue)

    # --- Adding and cancelling ---

    def add(self, sound: Sound) -> str:
        """Place a sound by its policy. Returns its state: "waiting" (starts now, after the duck) or "queued"."""
        if sound.policy not in POLICIES:
            raise OverlayError(f"'policy' must be one of {', '.join(POLICIES)}")
        events = []
        with self._lock:
            self._pending = []
            live = [s for s in self._sounds if s.cut_left is None]
            if sound.policy == "replace":
                for other in live:
                    if other.priority <= sound.priority:
                        self._cut(other, "replaced")
                for other in [q for q in self._queue if q.priority <= sound.priority]:
                    self._queue.remove(other)
                    other.state = "done"
                    events.append(("ended", other, "replaced"))
                blocked = any(s.priority > sound.priority for s in live)
            elif sound.policy == "queue":
                blocked = bool(live or self._queue)
            else:
                blocked = False
            if blocked:
                sound.state = "queued"
                self._queue.append(sound)
                self._queue.sort(key=lambda s: (-s.priority, s.seq))
            else:
                sound.state = "waiting"
                self._sounds.append(sound)
            state = sound.state
            events.extend(self._pending)
            self._pending = []
        self._emit(events)
        return state

    def _cut(self, sound: Sound, reason: str) -> None:
        """Fade a playing sound out quickly, then drop it (call with the lock held)."""
        if sound.state == "waiting":
            self._sounds.remove(sound)
            sound.state = "done"
            sound.cut_reason = reason
            sound.cut_left = 0
            self._pending.append(("ended", sound, reason))
            return
        sound.cut_left = max(1, int(CUT_FADE_SECONDS * self.rate))
        sound.cut_reason = reason

    def cancel(self, sound_id: str, reason: str = "cancelled") -> bool:
        events = []
        with self._lock:
            self._pending = []
            found = False
            for sound in list(self._queue):
                if sound.sound_id == sound_id:
                    self._queue.remove(sound)
                    sound.state = "done"
                    events.append(("ended", sound, reason))
                    found = True
            for sound in list(self._sounds):
                if sound.sound_id == sound_id and sound.cut_left is None:
                    self._cut(sound, reason)
                    found = True
            events.extend(self._pending)
            self._pending = []
        self._emit(events)
        return found

    def clear(self, reason: str = "stopped") -> None:
        """Drop every sound at once (the channel stopped) and the duck with it."""
        with self._lock:
            gone = [s for s in self._sounds + self._queue if s.state != "done"]
            self._sounds, self._queue = [], []
            self._duck_now = 0.0
            for sound in gone:
                sound.state = "done"
        self._emit([("ended", s, s.cut_reason or reason) for s in gone])

    def _emit(self, events) -> None:
        if self.on_event is None:
            return
        for kind, sound, reason in events:
            try:
                self.on_event(kind, sound, reason)
            except Exception:
                pass

    # --- Mixing (the generator thread) ---

    def _duck_target(self) -> tuple[float, float]:
        """(dB the mix should be down, attack seconds) for what plays, waits or is queued."""
        ducking = [s.duck for s in self._sounds + self._queue if s.duck is not None and s.cut_left is None]
        if not ducking:
            return 0.0, 0.0
        return max(d.db for d in ducking), min(d.attack for d in ducking)

    def _duck_curve(self, size: int) -> np.ndarray:
        """The dB the mix is down at each sample of this chunk (moves self._duck_now)."""
        target, attack = self._duck_target()
        now = self._duck_now
        if now == target:
            return np.full(size, now)
        if target > now:
            rate = target / (attack * self.rate) if attack > 0 else np.inf
            steps = now + rate * np.arange(1, size + 1)
            curve = np.minimum(steps, target)
        else:
            release = self._release
            full = max(self._release_db, now, 1e-9)
            rate = full / (release * self.rate) if release > 0 else np.inf
            steps = now - rate * np.arange(1, size + 1)
            curve = np.maximum(steps, target)
        self._duck_now = float(curve[-1])
        return curve

    def mix(self, chunk: np.ndarray) -> np.ndarray:
        """The chunk with the channel's mix ducked and the sounds added."""
        events = []
        with self._lock:
            self._pending = []
            size = chunk.shape[-1]
            curve = self._duck_curve(size)
            out = chunk.astype(np.float32)  # (channels, n): the duck and each sound act on every channel alike
            if curve.any():
                out = out * (10.0 ** (-curve / 20.0)).astype(np.float32)
            for sound in list(self._sounds):
                start = 0
                if sound.state == "waiting":
                    need = sound.duck.db if sound.duck is not None else 0.0
                    hit = np.nonzero(curve >= need - 1e-6)[0] if need > 0 else np.array([0])
                    if not hit.size:
                        continue
                    start = int(hit[0])
                    sound.state = "playing"
                    events.append(("started", sound, ""))
                take = min(size - start, sound.pcm.shape[-1] - sound.pos)
                if sound.cut_left is not None:
                    take = min(take, sound.cut_left)
                if take > 0:
                    part = sound.pcm[sound.pos:sound.pos + take] * sound.volume
                    if sound.cut_left is not None:
                        left = sound.cut_left
                        total = max(1, int(CUT_FADE_SECONDS * self.rate))
                        part = part * (np.arange(left, left - take, -1, dtype=np.float32) / total)
                        sound.cut_left -= take
                    out[..., start:start + take] += part  # a mono sound: centred in a stereo mix
                    sound.pos += take
                if sound.pos >= sound.pcm.shape[-1] or (sound.cut_left is not None and sound.cut_left <= 0):
                    self._sounds.remove(sound)
                    sound.state = "done"
                    if sound.duck is not None:
                        self._release = sound.duck.release
                        self._release_db = sound.duck.db
                    events.append(("ended", sound, sound.cut_reason or "finished"))
            # The next queued sound starts once nothing it waits for still plays
            if self._queue and not any(s.cut_left is None for s in self._sounds):
                nxt = self._queue.pop(0)
                nxt.state = "waiting"
                self._sounds.append(nxt)
            events.extend(self._pending)
            self._pending = []
        self._emit(events)
        return np.clip(out, -32768, 32767).astype(chunk.dtype).reshape(chunk.shape)
