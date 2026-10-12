"""
The random numbers playback draws (a group's pick, an Intermittent wait, an
Ebb & Flow drift).

recording.py calls them through a ScopedRandom, which acts like the `random`
module: normally it *is* the module's shared generator (random.seed() still
sets it), but inside use_rng() in one thread it is the generator given there.
The engine (sonorium/engine) uses that for a render that is the same every
time for the same seed, without touching the server's playback in other
threads.

No imports from the rest of Sonorium, so recording.py can load this file on
its own.
"""
from __future__ import annotations

import contextlib
import random as _random
import threading

_local = threading.local()


class ScopedRandom:
    """random.uniform(), random.choice(), ... from this thread's generator in use (see use_rng)."""

    def __getattr__(self, name):
        return getattr(getattr(_local, "rng", None) or _random, name)


def current_rng():
    """The generator in use in this thread: a random.Random, or the random module itself."""
    return getattr(_local, "rng", None) or _random


@contextlib.contextmanager
def use_rng(rng):
    """Draw from `rng` (a random.Random) in this thread inside the with block (None: no change)."""
    if rng is None:
        yield
        return
    previous = getattr(_local, "rng", None)
    _local.rng = rng
    try:
        yield
    finally:
        _local.rng = previous
