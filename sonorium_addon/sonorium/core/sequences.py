"""
Sequences: a theme's presets played in order (docs/THEME_FORMAT.md section 5).

Stored in the theme's presets.json next to the presets:

    {
      "presets": {...},
      "sequence_defaults": {"hold_min": 300, "hold_max": 900, "ramp": 30},
      "sequences": {
        "gentle_storm": {
          "name": "Gentle storm",
          "presets": ["drizzle", "rain", "downpour"],
          "at_end": "reverse",
          "hold_min": null, "hold_max": null, "ramp": null,
          "owner": null,
          "transitions": {"rain": {"in": {...}, "out": {...}}}
        }
      }
    }

"transitions" (optional) holds the advanced transitions of the sequence's
presets, checked by core/transitions.py; a preset or track without one
follows the ramp (Smooth).

A sequence's own hold_min / hold_max / ramp (seconds) override the theme's
sequence_defaults; null (or absent) uses the default. "owner" is null for a
theme sequence (every sequence for now; per-user sequences come later).

A channel picks a sequence where it picks a preset: the choice is the
sequence id with SEQUENCE_PREFIX ("seq:gentle_storm"), see choice_id() and
sequence_choice().

This module only reads, checks and writes those keys; the runtime is in
core/playback_runner.py.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from sonorium.core.theme_presets import (
    BrokenJsonError,
    PRESETS_FILE,
    load_presets_doc,
    write_json_atomic,
)

AT_END = ("reverse", "restart", "stop")
DEFAULT_AT_END = "reverse"
TIMING_FIELDS = ("hold_min", "hold_max", "ramp")
DEFAULTS = {"hold_min": 300.0, "hold_max": 900.0, "ramp": 30.0}
MAX_SECONDS = 7 * 24 * 3600  # a week: anything longer is a typo
MAX_NAME = 100
# A channel's preset choice that is a sequence: "seq:<sequence_id>"
SEQUENCE_PREFIX = "seq:"


class SequenceError(ValueError):
    """A sequence (or its defaults) that can't be saved: the message says why."""


def _doc(folder: Path) -> dict:
    try:
        return dict(load_presets_doc(folder))
    except BrokenJsonError:
        return {}


def _number(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SequenceError(f"{name} must be a number of seconds")
    value = float(value)
    if value != value or value < 0 or value > MAX_SECONDS:
        raise SequenceError(f"{name} must be between 0 and {MAX_SECONDS} seconds")
    return value


def clean_defaults(data: dict, current: Optional[dict] = None) -> dict:
    """Checked sequence_defaults: `data` over `current` (or the built-in defaults)."""
    if not isinstance(data, dict):
        raise SequenceError("sequence_defaults must be an object")
    result = dict(DEFAULTS)
    result.update({k: v for k, v in (current or {}).items() if k in TIMING_FIELDS})
    for key in TIMING_FIELDS:
        if key in data and data[key] is not None:
            result[key] = _number(data[key], key)
    if result["hold_min"] > result["hold_max"]:
        raise SequenceError("hold_min must not be more than hold_max")
    return result


def load_defaults(folder: Path) -> dict:
    """The theme's sequence_defaults, with the built-in values for anything it leaves out."""
    saved = _doc(Path(folder)).get("sequence_defaults")
    result = dict(DEFAULTS)
    if isinstance(saved, dict):
        for key in TIMING_FIELDS:
            try:
                if saved.get(key) is not None:
                    result[key] = _number(saved[key], key)
            except SequenceError:
                pass
    if result["hold_min"] > result["hold_max"]:
        result["hold_min"], result["hold_max"] = result["hold_max"], result["hold_min"]
    return result


def save_defaults(folder: Path, defaults: dict) -> dict:
    """Save sequence_defaults (checked against the saved sequences). Raises SequenceError or OSError."""
    folder = Path(folder)
    cleaned = clean_defaults(defaults, load_defaults(folder))
    sequences = load_sequences(folder)
    for sequence_id, sequence in sequences.items():
        resolved = resolve_timing(sequence, cleaned)
        if resolved["hold_min"] > resolved["hold_max"]:
            raise SequenceError(f"sequence '{sequence.get('name', sequence_id)}' would have hold_min above hold_max")
    doc = _doc(folder)
    doc["sequence_defaults"] = cleaned
    write_json_atomic(folder / PRESETS_FILE, doc)
    return cleaned


def load_sequences(folder: Path) -> dict:
    """A theme's sequences ({sequence_id: data}); unreadable entries are skipped."""
    saved = _doc(Path(folder)).get("sequences")
    if not isinstance(saved, dict):
        return {}
    return {sid: dict(data) for sid, data in saved.items() if isinstance(sid, str) and isinstance(data, dict)}


def save_sequences(folder: Path, sequences: dict) -> None:
    """Write the sequences to presets.json, keeping its other keys. Raises OSError on failure."""
    folder = Path(folder)
    doc = _doc(folder)
    doc["sequences"] = dict(sequences)
    write_json_atomic(folder / PRESETS_FILE, doc)


def resolve_timing(sequence: dict, defaults: dict) -> dict:
    """A sequence's hold_min, hold_max and ramp: its own where set, else the theme's defaults."""
    result = {}
    for key in TIMING_FIELDS:
        value = sequence.get(key)
        result[key] = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else float(defaults[key])
    return result


def clean_sequence(data: dict, preset_ids, current: Optional[dict] = None) -> dict:
    """
    A checked sequence from request data. With `current`, only the fields in
    `data` change (a partial update). Every preset must be one of `preset_ids`.
    """
    if not isinstance(data, dict):
        raise SequenceError("expected a JSON object")
    result = dict(current or {})
    result.setdefault("at_end", DEFAULT_AT_END)
    result.setdefault("owner", None)
    for key in TIMING_FIELDS:
        result.setdefault(key, None)

    if "name" in data or current is None:
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            raise SequenceError("name is required")
        if len(name.strip()) > MAX_NAME:
            raise SequenceError(f"name must be at most {MAX_NAME} characters")
        result["name"] = name.strip()

    if "presets" in data or current is None:
        presets = data.get("presets")
        if not isinstance(presets, list) or not presets:
            raise SequenceError("presets must be a list of at least one preset id")
        known = set(preset_ids)
        for preset_id in presets:
            if not isinstance(preset_id, str):
                raise SequenceError("presets must be preset ids")
            if preset_id not in known:
                raise SequenceError(f"unknown preset '{preset_id}'")
        result["presets"] = list(presets)

    if "at_end" in data:
        if data["at_end"] not in AT_END:
            raise SequenceError(f"at_end must be one of {', '.join(AT_END)}")
        result["at_end"] = data["at_end"]

    for key in TIMING_FIELDS:
        if key in data:
            result[key] = None if data[key] is None else _number(data[key], key)

    if "owner" in data and data["owner"] is not None:
        raise SequenceError("owner must be null (personal sequences are not available yet)")

    from sonorium.core.transitions import TransitionError, clean_transitions, keep_presets
    presets_now = result.get("presets") or []
    if "transitions" in data:
        try:
            result["transitions"] = clean_transitions(data["transitions"], presets_now)
        except TransitionError as e:
            raise SequenceError(str(e))
    elif result.get("transitions"):
        result["transitions"] = keep_presets(result["transitions"], presets_now)  # presets taken out drop theirs
    if not result.get("transitions"):
        result.pop("transitions", None)
    return result


def step_transitions(sequence: dict, preset_id: str) -> dict:
    """A preset's advanced transitions in a sequence ({"in": {...}, "out": {...}}; empty sides are {})."""
    step = (sequence.get("transitions") or {}).get(preset_id) or {}
    return {"in": dict(step.get("in") or {}), "out": dict(step.get("out") or {})}


def set_step_transitions(sequence: dict, preset_id: str, data) -> dict:
    """The sequence with a preset's transitions replaced (checked). Raises SequenceError."""
    from sonorium.core.transitions import TransitionError, clean_step
    if preset_id not in (sequence.get("presets") or []):
        raise SequenceError(f"'{preset_id}' is not a preset of this sequence")
    try:
        cleaned = clean_step(data)
    except TransitionError as e:
        raise SequenceError(str(e))
    result = dict(sequence)
    transitions = dict(result.get("transitions") or {})
    if cleaned:
        transitions[preset_id] = cleaned
    else:
        transitions.pop(preset_id, None)
    if transitions:
        result["transitions"] = transitions
    else:
        result.pop("transitions", None)
    return result


def check_timing(sequence: dict, defaults: dict) -> None:
    resolved = resolve_timing(sequence, defaults)
    if resolved["hold_min"] > resolved["hold_max"]:
        raise SequenceError("hold_min must not be more than hold_max")


def new_sequence_id(name: str, existing) -> str:
    """An id from the name, like preset ids: lower case, runs of other characters as '_'."""
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "sequence"
    sequence_id, n = base, 1
    while sequence_id in existing:
        sequence_id = f"{base}_{n}"
        n += 1
    return sequence_id


def choice_id(sequence_id: str) -> str:
    """The preset choice that starts a sequence: "seq:<sequence_id>"."""
    return f"{SEQUENCE_PREFIX}{sequence_id}"


def sequence_choice(value) -> Optional[str]:
    """The sequence id if a preset choice is a sequence ("seq:<id>"), else None."""
    if isinstance(value, str) and value.startswith(SEQUENCE_PREFIX) and len(value) > len(SEQUENCE_PREFIX):
        return value[len(SEQUENCE_PREFIX):]
    return None


def pass_range(count: int, timing: dict) -> tuple[float, float]:
    """
    Shortest and longest time (seconds) of one pass through `count` presets:
    each preset held once (hold_min to hold_max) with a ramp between them.
    """
    if count <= 0:
        return 0.0, 0.0
    ramps = (count - 1) * timing["ramp"]
    return count * timing["hold_min"] + ramps, count * timing["hold_max"] + ramps


def fit_pass(holds: list[float], ramp: float, total: float) -> tuple[list[float], float]:
    """
    Squeeze (or stretch) one pass so it lasts exactly `total` seconds: the
    holds (one per preset) and the ramp (between presets) are scaled by the
    same factor. A ramp never exceeds half the shortest hold: if it would,
    the holds become equal shares and the ramp at most half of one. Returns
    (holds, ramp); with no time to share (all zero) the holds are equal.
    """
    count = len(holds)
    total = max(0.0, float(total))
    if count == 0:
        return [], 0.0
    if count == 1:
        return [total], 0.0
    ramps = count - 1
    length = sum(holds) + ramps * ramp
    if length <= 0 or min(holds) <= 0:
        holds = [1.0] * count  # equal shares, scaled below
        length = count + ramps * ramp
    scale = total / length
    holds = [h * scale for h in holds]
    ramp = ramp * scale
    if ramp <= min(holds) / 2:
        return holds, ramp
    # Equal shares: keep the scaled ramp if it fits in half a hold, else half a hold
    hold = (total - ramps * ramp) / count
    if ramp > hold / 2:
        hold = total / (count + ramps / 2)
        ramp = hold / 2
    return [hold] * count, ramp


def to_response(sequence_id: str, sequence: dict, defaults: dict) -> dict:
    """
    A sequence as the API returns it: its saved fields plus id, the timing it
    plays with ("effective") and how long one pass takes ("pass_min",
    "pass_max": every preset once, with the ramps between them).
    """
    effective = resolve_timing(sequence, defaults)
    pass_min, pass_max = pass_range(len(sequence.get("presets") or []), effective)
    return {
        "id": sequence_id,
        "name": sequence.get("name", sequence_id),
        "presets": list(sequence.get("presets") or []),
        "at_end": sequence.get("at_end") if sequence.get("at_end") in AT_END else DEFAULT_AT_END,
        "hold_min": sequence.get("hold_min"),
        "hold_max": sequence.get("hold_max"),
        "ramp": sequence.get("ramp"),
        "owner": sequence.get("owner"),
        "transitions": dict(sequence.get("transitions") or {}),
        "effective": effective,
        "pass_min": pass_min,
        "pass_max": pass_max,
    }


def to_choice(sequence_id: str, sequence: dict, defaults: dict, preset_intrusion: dict) -> dict:
    """
    A sequence as a choice next to a theme's presets: to_response() plus its
    preset choice ("choice": "seq:<id>") and "has_intrusion" (true if any of
    its presets turns on an intrusion; `preset_intrusion` is {preset_id: bool}).
    """
    result = to_response(sequence_id, sequence, defaults)
    result["choice"] = choice_id(sequence_id)
    result["has_intrusion"] = any(preset_intrusion.get(p, False) for p in result["presets"])
    return result


def drop_preset(folder: Path, preset_id: str) -> list[str]:
    """
    Take a removed preset out of every sequence of the theme. Returns the ids
    of the sequences that changed. A sequence left with no presets is kept
    (empty) so it can be edited; it can't be started until it has one.
    """
    folder = Path(folder)
    sequences = load_sequences(folder)
    changed = []
    for sequence_id, sequence in sequences.items():
        presets = sequence.get("presets") or []
        if preset_id in presets:
            sequence["presets"] = [p for p in presets if p != preset_id]
            changed.append(sequence_id)
        if preset_id in (sequence.get("transitions") or {}):
            sequence["transitions"] = {p: s for p, s in sequence["transitions"].items() if p != preset_id}
            if sequence_id not in changed:
                changed.append(sequence_id)
    if changed:
        save_sequences(folder, sequences)
    return changed
