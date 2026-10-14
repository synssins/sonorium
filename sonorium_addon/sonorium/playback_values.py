"""
Settings > Advanced: the playback values that used to be fixed in code.

Every value is global: tracks and groups follow it unless they set their own
(a group's own Gap, a theme's own short file threshold). The mixer reads them
through get() as it goes, so a change reaches playing channels at once where
it can, otherwise on the next play or loop.

The values the user changed live in the settings store
(SonoriumSettings.playback_values, only the changed ones); set_source() points
this module at them. Without a source (tests, code without a store) every
value is its default. use_values() gives one thread its own values for a
while (an engine's render, sonorium/engine), leaving every other thread's alone.

No dependencies on the rest of the package, so recording.py can load it even
when it's loaded on its own.
"""

from __future__ import annotations

import contextlib
import math
import threading
from typing import Callable, Optional

# key -> default. Chances and randomness are fractions (0.30 = 30%); times are seconds.
DEFAULTS: dict[str, float] = {
    "sparse_min_interval": 180.0,       # Intermittent: Wait between plays, shortest (Interval 100%)
    "sparse_max_interval": 1800.0,      # ... longest (Interval 0%)
    "sparse_interval_variance": 0.30,   # Wait randomness, either way
    "group_gap": 120.0,                 # Gap after a group's track, when the group sets none
    "loop_crossfade": 1.5,              # Background: a loop crossfading into itself
    "group_crossfade_min": 1.0,         # Merry-go-round crossfade range
    "group_crossfade_max": 60.0,
    "track_fade": 6.0,                  # A track fading in or out of the mix
    "switch_fade": 3.0,                 # A track switched on or off, or muted
    "ebb_long_rest": 0.12,              # Ebb & Flow: Long pause chance
    "ebb_turn": 0.35,                   # Ebb & Flow: Turn chance
    "short_file_threshold": 15.0,       # New themes: Short file threshold
    # Sounds played over a channel: announcements, alerts, effects (core/overlay_service.py)
    "sound_max_length": 60.0,           # Longest sound (0: no limit)
    "duck_amount": 15.0,                # How much quieter the mix gets under a sound (dB)
    "duck_attack": 0.5,                 # ... how long it takes to get there
    "duck_release": 1.5,                # ... and to come back afterwards
    "wake_wait": 20.0,                  # A stopped channel: longest wait for its speakers to connect
    "wake_lead": 1.0,                   # ... then this long before the sound
    "wake_tail": 1.0,                   # ... and this long after it before they stop
    # Plugin attributes: bytes per plugin per object (0: no limit; core/plugin_data.py)
    "plugin_data_limit": 4096.0,
    # The cue timeline (core/timeline.py): how early an Intermittent play is decided and announced
    "timeline_lookahead": 0.5,
    # Theme backups from the app: space per account, MB (0: no limit; core/backups.py)
    "backup_limit_mb": 10240.0,
    "backup_total_limit_mb": 51200.0,   # ... every account together
    "backup_free_space_mb": 1024.0,     # ... and this much disk always left free
    # Limits (sonorium/limits.py; 0: no limit)
    "auth_body_limit_kb": 16.0,         # a sign-in, Link App or pairing request
    "request_body_limit_kb": 1024.0,    # any other request
    "upload_limit_mb": 2048.0,          # one upload: an audio file, a theme or plugin zip, a backup file
    "import_unpacked_limit_mb": 8192.0, # a theme zip's files, unpacked
    "import_file_limit": 5000.0,        # files in one theme zip
    "export_limit": 2.0,                # theme exports made at the same time
    "streams_per_address": 16.0,        # audio streams one address may have open
    "streams_limit": 64.0,              # audio streams open in all
    "event_streams_per_caller": 8.0,    # event streams one caller may have open
    "login_attempts_per_user": 10.0,    # failed sign-ins a minute for one username, from anywhere
    "stream_link_hours": 12.0,          # how long a stream address handed to a speaker works
    "lan_streams_open": 1.0,            # 1: LAN speakers play streams without a stream link; 0: every stream needs one
}

# (min key, max key): the min may not be above the max
RANGES = (("sparse_min_interval", "sparse_max_interval"), ("group_crossfade_min", "group_crossfade_max"))

# Values that are a share (0..1): a chance can't be above 100%, and randomness
# above 100% would make a wait negative
FRACTIONS = ("sparse_interval_variance", "ebb_long_rest", "ebb_turn")

_source: Optional[Callable[[], dict]] = None
_local = threading.local()


def set_source(source: Optional[Callable[[], dict]]) -> None:
    """Where the changed values are: a function returning the stored dict (None: defaults only)."""
    global _source
    _source = source


@contextlib.contextmanager
def use_values(values: Optional[dict]):
    """In this thread, inside the with block, the changed values are `values` (None: no change)."""
    if values is None:
        yield
        return
    previous = getattr(_local, "values", None)
    _local.values = dict(values)
    try:
        yield
    finally:
        _local.values = previous


def _stored() -> dict:
    scoped = getattr(_local, "values", None)
    if scoped is not None:
        return scoped
    if _source is None:
        return {}
    try:
        stored = _source()
    except Exception:
        return {}
    return stored if isinstance(stored, dict) else {}


def get(key: str) -> float:
    """The value now: the stored one, else the default."""
    default = DEFAULTS[key]
    value = _stored().get(key)
    if value is None or isinstance(value, bool):
        return default
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def values() -> dict[str, float]:
    """Every value now."""
    return {key: get(key) for key in DEFAULTS}


def wait_range() -> tuple[float, float]:
    """Intermittent Wait between plays (shortest, longest), seconds."""
    return get("sparse_min_interval"), get("sparse_max_interval")


def base_wait(presence: float) -> float:
    """
    The wait an Intermittent track's Interval (presence, 0-1) points at in the
    range: 100% the shortest, 0% the longest.
    """
    low, high = wait_range()
    return low + (high - low) * (1.0 - float(presence))


def presence_for_wait(seconds: float) -> float:
    """The Interval (0-1) whose wait is `seconds` in the current range (the inverse of base_wait, clamped)."""
    low, high = wait_range()
    if high <= low:
        return 1.0
    return min(1.0, max(0.0, 1.0 - (float(seconds) - low) / (high - low)))


def validate(updates, current: Optional[dict] = None) -> dict:
    """
    The stored dict after `updates` ({key: number, or None for the default}),
    checked: known keys, numbers, not negative (shares up to 1), each range's
    min not above its max. Values equal to the default aren't kept. Raises
    ValueError with a message for the user.
    """
    if not isinstance(updates, dict):
        raise ValueError("Expected a JSON object")
    result = {k: v for k, v in (current or {}).items() if k in DEFAULTS}
    for key, value in updates.items():
        if key not in DEFAULTS:
            raise ValueError(f"Unknown setting '{key}'")
        if value is None:
            result.pop(key, None)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"'{key}' must be a number")
        value = float(value)
        if not math.isfinite(value):
            raise ValueError(f"'{key}' must be a number")
        if value < 0:
            raise ValueError(f"'{key}' can't be negative")
        if key in FRACTIONS and value > 1:
            raise ValueError(f"'{key}' can't be above 100%")
        result[key] = value
    for low, high in RANGES:
        lo = result.get(low, DEFAULTS[low])
        hi = result.get(high, DEFAULTS[high])
        if lo > hi:
            raise ValueError(f"'{low}' can't be above '{high}'")
    return {k: v for k, v in result.items() if v != DEFAULTS[k]}
