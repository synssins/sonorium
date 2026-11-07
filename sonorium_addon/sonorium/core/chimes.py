"""
Chimes: the sound an announcement plays before its words.

A chime is named by one string, the same in Settings, on a channel and in an
announcement:

    ""             the default (a channel: Settings > Audio Settings' chime)
    "none"         no chime
    "tone:<id>"    a chime Sonorium makes (TONES), no file needed
    "file:<name>"  a file in the chimes folder (audio_dir()/.chimes)

The chimes folder starts with a dot, so no theme scanner takes it for a
theme. Files in it are uploaded, or made from a tone with its own pitch and
speed (Settings > Audio Settings > Chimes).

An announcement with no chime of its own plays each channel's chime, or the
default when the channel has none (core/announce.py).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np

from sonorium import paths
from sonorium.core import speaker_test_tone as tone_maker
from sonorium.theme_files import AUDIO_EXTENSIONS

FOLDER = ".chimes"
RATE = tone_maker.SAMPLE_RATE
BASE = 523.25  # C5, the test sound's first note
# Notes as (semitones above BASE, start in beats); a beat is 0.3 s at speed 1
TONES = {
    "two-tone": ("Two-tone", ((0, 0), (4, 3))),  # the speaker test sound
    "doorbell": ("Doorbell", ((4, 0), (0, 1.6))),
    "rising": ("Rising", ((0, 0), (4, 1), (7, 2))),
    "falling": ("Falling", ((7, 0), (4, 1), (0, 2))),
    "soft": ("Soft", ((0, 0),)),
}
BEAT = 0.3
TAIL = 2.1  # seconds the last note rings
PITCH_RANGE = (-12, 12)  # semitones
SPEED_RANGE = (0.5, 2.0)
MAX_NAME = 80


def tone(tone_id: str, pitch: float = 0, speed: float = 1.0) -> np.ndarray:
    """Mono int16 samples of a tone, `pitch` semitones up or down, `speed` times as fast. ValueError for bad values."""
    if tone_id not in TONES:
        raise ValueError(f"Unknown chime tone '{tone_id}'")
    for value, (low, high), what in ((pitch, PITCH_RANGE, "pitch"), (speed, SPEED_RANGE, "speed")):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f"'{what}' must be {low} to {high}")
    notes = [(BASE * 2 ** ((step + pitch) / 12), beat * BEAT / speed) for step, beat in TONES[tone_id][1]]
    return tone_maker.tone_samples(notes[-1][1] + TAIL, RATE, notes)


def folder() -> Path:
    return paths.audio_dir() / FOLDER


def files() -> list[str]:
    """The chime files, by name."""
    try:
        return sorted((p.name for p in folder().iterdir() if _usable(p)), key=str.lower)
    except OSError:
        return []


def _usable(path: Path) -> bool:
    # No links: a chime file is in the folder itself, never somewhere it points to
    return path.suffix.lower() in AUDIO_EXTENSIONS and path.is_file() and not path.is_symlink() \
        and not path.name.startswith(".")


def file_path(name) -> Path:
    """The chime file `name`, or ValueError (not a plain name, not there, or not a chime)."""
    if not paths.plain_name(name) or len(name) > MAX_NAME:
        raise ValueError("Not a chime file name")
    path = folder() / name
    if not _usable(path) or path.resolve().parent != folder().resolve():
        raise ValueError(f"No chime file '{name}'")
    return path


def check(value) -> str:
    """A chime setting as stored: "", "none", "tone:<id>" or "file:<name>" (the file must be there). ValueError."""
    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise ValueError("A chime is a string")
    if value == "none":
        return value
    kind, _, name = value.partition(":")
    if kind == "tone" and name in TONES:
        return value
    if kind == "file":
        file_path(name)
        return value
    raise ValueError(f"Unknown chime '{value}'")


def play_args(value) -> Optional[dict]:
    """overlay.play() arguments for a chime setting ({"source"} or {"samples", "sample_rate"}), None for no chime."""
    value = check(value)
    if value in ("", "none"):
        return None
    kind, _, name = value.partition(":")
    if kind == "tone":
        return {"samples": tone(name), "sample_rate": RATE}
    return {"source": file_path(name)}


def from_request(value):
    """
    The chime an announcement sent (Home Assistant, MQTT, the API), checked:
    False for no chime, an http(s) URL as is, a Path for a file in the audio
    folder (a chime file by name, "file:<name>", or a full path inside the
    audio folder), or a chime id. ValueError for anything else.
    """
    if value is False or value == "none":
        return False
    if not isinstance(value, str) or not value.strip():
        raise ValueError("'chime' must be a URL, a chime name, or false")
    value = value.strip()
    if value.lower().startswith(("http://", "https://")):
        return value
    if value.startswith("tone:"):
        return check(value)
    if value.startswith("file:"):
        return file_path(value[5:])
    if paths.plain_name(value):
        return file_path(value)
    root = paths.audio_dir().resolve()
    try:
        path = Path(value).resolve()
    except (OSError, RuntimeError, ValueError):
        raise ValueError("Not a sound in Sonorium's audio folder")
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError("Not a sound in Sonorium's audio folder")
    return path


def channel_chime(session, default: str) -> str:
    """The chime an announcement plays on a channel: its own, else the default."""
    own = getattr(session, "chime", "") or ""
    return own or default or "none"
