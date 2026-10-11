"""
Volume ramps: a channel's mix gets louder or quieter gradually, over seconds
or an hour (an alarm clock waking someone, a sleep timer fading out).

A ramp is a gain on the channel's mix, worked out for every sample of every
chunk, so it rises smoothly instead of in steps. It sits after the theme's
mix and the channel's start/stop fade, and before announcements and sound
effects (core/overlay.py), so a sound played during a sleep fade is still
heard. The speakers' own volume is not touched: set it first (the channel's
volume), then ramp the mix within it.

    from_level, to_level   0.0 (silent) .. 1.0 (the mix as it is); up to 4.0 (+12 dB)
    seconds                how long the ramp takes (0: at once)
    curve                  how the level moves between them:
        linear   in a straight line (sounds fast at first, slow at the end)
        db       evenly in decibels: sounds even to the ear (the default)
        ease     slow, faster, slow again (an S-curve)
        exp      slow at first, fast at the end

A ramp that finishes holds its last level until the channel stops or another
ramp starts. A ramp that ends at 1.0 removes itself (no cost afterwards).
Cancelling holds the level where it got to, or goes back to 1.0 (reset).
"""
from __future__ import annotations

import math
import threading
from typing import Callable, Optional

import numpy as np

CURVES = ("linear", "db", "ease", "exp")
MAX_LEVEL = 4.0
DB_FLOOR = -60.0       # the "db" curve starts and ends here when a level is 0
RESET_SECONDS = 0.05   # cancel with reset: back to full level this quickly (no click)


class RampError(ValueError):
    """A ramp that can't be made (the message is shown to the caller)."""


def check(from_level, to_level, seconds, curve) -> tuple[float, float, float, str]:
    for name, value in (("from", from_level), ("to", to_level), ("seconds", seconds)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise RampError(f"'{name}' must be a number")
    if not 0 <= from_level <= MAX_LEVEL or not 0 <= to_level <= MAX_LEVEL:
        raise RampError(f"Levels must be between 0 and {MAX_LEVEL:g}")
    if seconds < 0:
        raise RampError("'seconds' can't be negative")
    if curve not in CURVES:
        raise RampError(f"'curve' must be one of {', '.join(CURVES)}")
    return float(from_level), float(to_level), float(seconds), curve


def _db(level: float) -> float:
    return DB_FLOOR if level <= 0 else max(DB_FLOOR, 20.0 * math.log10(level))


def curve_levels(progress: np.ndarray, from_level: float, to_level: float, curve: str) -> np.ndarray:
    """The level at each point of the ramp (progress 0..1)."""
    p = np.clip(progress, 0.0, 1.0)
    if curve == "db":
        a, b = _db(from_level), _db(to_level)
        levels = 10.0 ** ((a + (b - a) * p) / 20.0)
        # Exactly 0 at a silent end (the floor is -60 dB, not silence)
        if to_level <= 0:
            levels = np.where(p >= 1.0, 0.0, levels)
        if from_level <= 0:
            levels = np.where(p <= 0.0, 0.0, levels)
        return levels
    if curve == "ease":
        f = 0.5 - 0.5 * np.cos(np.pi * p)
    elif curve == "exp":
        f = p ** 3
    else:
        f = p
    return from_level + (to_level - from_level) * f


class ChannelRamp:
    """
    One channel's ramp. apply(chunk) is called by the channel's generator
    thread for every chunk; start() and cancel() come from the event loop.
    on_done(ramp, reason) is called (from the generator thread) when a ramp
    finishes: reason "finished".
    """

    def __init__(self, sample_rate: int, on_done: Optional[Callable] = None):
        self.sample_rate = sample_rate
        self.on_done = on_done
        self._lock = threading.Lock()
        self.level = 1.0            # where the gain is now
        self._active: Optional[dict] = None
        self.ramp_id: Optional[str] = None

    @property
    def active(self) -> bool:
        return self._active is not None

    @property
    def idle(self) -> bool:
        """Nothing to do: no ramp running and the level is 1.0 (the channel can drop this stage)."""
        return self._active is None and self.level == 1.0

    def start(self, ramp_id: str, from_level: Optional[float], to_level: float, seconds: float,
              curve: str = "db") -> dict:
        """Start a ramp (from None: from where the level is now). Replaces a running one."""
        with self._lock:
            start = self.level if from_level is None else from_level
            self.ramp_id = ramp_id
            length = int(round(seconds * self.sample_rate))
            if length <= 0:
                self._active = None
                self.level = to_level
                return {"id": ramp_id, "from": start, "to": to_level, "seconds": 0.0, "curve": curve}
            self._active = {"from": start, "to": to_level, "length": length, "pos": 0, "curve": curve}
            self.level = start
            return {"id": ramp_id, "from": start, "to": to_level, "seconds": seconds, "curve": curve}

    def cancel(self, reset: bool = False) -> Optional[float]:
        """Stop the running ramp: hold the level, or (reset) glide back to 1.0. Returns the level it was at."""
        with self._lock:
            level = self.level
            self._active = None
            if reset and self.level != 1.0:
                length = max(1, int(RESET_SECONDS * self.sample_rate))
                self._active = {"from": self.level, "to": 1.0, "length": length, "pos": 0,
                                "curve": "linear", "quiet": True}
            return level

    def reset(self) -> None:
        """Back to full level at once, no ramp (the channel stopped)."""
        with self._lock:
            self._active = None
            self.level = 1.0
            self.ramp_id = None

    def progress(self) -> Optional[dict]:
        with self._lock:
            ramp = self._active
            if ramp is None or ramp.get("quiet"):
                return None
            return {"id": self.ramp_id, "level": self.level, "from": ramp["from"], "to": ramp["to"],
                    "curve": ramp["curve"], "elapsed": ramp["pos"] / self.sample_rate,
                    "seconds": ramp["length"] / self.sample_rate}

    def apply(self, chunk: np.ndarray) -> np.ndarray:
        """The chunk with the ramp's gain on every sample."""
        finished = None
        with self._lock:
            ramp = self._active
            if ramp is None:
                if self.level == 1.0:
                    return chunk
                return np.clip(chunk.astype(np.float32) * self.level, -32768, 32767).astype(chunk.dtype)
            size = chunk.shape[-1]
            positions = ramp["pos"] + np.arange(1, size + 1, dtype=np.float64)
            gains = curve_levels(positions / ramp["length"], ramp["from"], ramp["to"], ramp["curve"])
            ramp["pos"] += size
            self.level = float(gains[-1])
            if ramp["pos"] >= ramp["length"]:
                self.level = ramp["to"]
                self._active = None
                if not ramp.get("quiet"):
                    finished = self.ramp_id
        out = np.clip(chunk.astype(np.float32) * gains.astype(np.float32), -32768, 32767).astype(chunk.dtype)
        if finished is not None and self.on_done is not None:
            try:
                self.on_done(finished, "finished")
            except Exception:
                pass
        return out
