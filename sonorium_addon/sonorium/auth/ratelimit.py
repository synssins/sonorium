"""Token-bucket rate limits by key (a client address, a device, a trigger)."""
from __future__ import annotations

import time
from typing import Callable


class RateLimiter:
    """Token buckets by key: allow() spends one; refills at `rate` a second up to `burst`."""

    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic, max_keys: int = 4096):
        self.rate = rate
        self.burst = burst
        self.clock = clock
        self.max_keys = max_keys
        self._buckets: dict[str, tuple[float, float]] = {}

    def _level(self, key: str) -> tuple[float, float]:
        now = self.clock()
        level, at = self._buckets.get(key, (float(self.burst), now))
        return min(float(self.burst), level + (now - at) * self.rate), now

    def allow(self, key: str) -> tuple[bool, float]:
        """(allowed, seconds to wait if not)."""
        level, now = self._level(key)
        if level < 1.0:
            self._buckets[key] = (level, now)
            return False, (1.0 - level) / self.rate
        if len(self._buckets) >= self.max_keys and key not in self._buckets:
            self._buckets.clear()  # never grows without bound
        self._buckets[key] = (level - 1.0, now)
        return True, 0.0

    def blocked(self, key: str) -> tuple[bool, float]:
        """Whether the key is out of tokens, without spending one."""
        level, _ = self._level(key)
        return (level < 1.0), ((1.0 - level) / self.rate if level < 1.0 else 0.0)
