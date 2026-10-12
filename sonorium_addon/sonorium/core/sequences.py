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


def migrate_track_keys(folder: Path, track_keys: dict, group_renames: Optional[dict] = None) -> bool:
    """
    Follow moved tracks and renamed or removed groups (core/theme_groups.py
    Change) in every sequence's transitions and While playing layers. True
    when presets.json changed. Raises OSError.
    """
    from sonorium.core.theme_groups import migrate_sequences
    folder = Path(folder)
    doc = _doc(folder)
    saved = doc.get("sequences")
    if not isinstance(saved, dict) or not (track_keys or group_renames):
        return False
    migrated = migrate_sequences(saved, track_keys, group_renames)
    if migrated == saved:
        return False
    doc["sequences"] = migrated
    write_json_atomic(folder / PRESETS_FILE, doc)
    return True


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
    if result.get("while"):
        result["while"] = {p: layer for p, layer in result["while"].items() if p in presets_now}
    if not result.get("while"):
        result.pop("while", None)
    return result


# A sequence step's While playing layer (sequence["while"][preset_id]): the
# settings this step changes on top of the preset, per track and per group.
# Every entry lists every field; null means "not changed here: the preset's".
STEP_TRACK_FIELDS = ("volume", "volume_low", "presence", "muted", "rise", "pause_between", "pause_min", "pause_max")
STEP_GROUP_FIELDS = ("volume", "presence", "muted", "gap_min", "gap_max", "pause_between", "rise")
_STEP_SHARES = {"volume", "volume_low", "presence", "rise"}  # 0..1
_STEP_FLAGS = {"muted", "pause_between"}


def _clean_step_entry(entry, fields, where: str) -> Optional[dict]:
    if not isinstance(entry, dict):
        raise SequenceError(f"{where}: expected an object")
    unknown = set(entry) - set(fields)
    if unknown:
        raise SequenceError(f"{where}: unknown setting '{sorted(unknown)[0]}'")
    result = {}
    for field in fields:
        value = entry.get(field)
        if value is None:
            result[field] = None
        elif field in _STEP_FLAGS:
            if not isinstance(value, bool):
                raise SequenceError(f"{where}: {field} must be true, false or null")
            result[field] = value
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
                raise SequenceError(f"{where}: {field} must be a number of 0 or more, or null")
            limit = 1.0 if field in _STEP_SHARES else MAX_SECONDS
            result[field] = min(limit, float(value))
    return result if any(v is not None for v in result.values()) else None


def clean_step_while(data) -> dict:
    """A checked While playing layer: {"tracks": {key: {...}}, "groups": {name: {...}}}, empty entries left out."""
    if data is None:
        return {"tracks": {}, "groups": {}}
    if not isinstance(data, dict) or set(data) - {"tracks", "groups"}:
        raise SequenceError('expected {"tracks": {...}, "groups": {...}}')
    result = {"tracks": {}, "groups": {}}
    for part, fields in (("tracks", STEP_TRACK_FIELDS), ("groups", STEP_GROUP_FIELDS)):
        entries = data.get(part) or {}
        if not isinstance(entries, dict):
            raise SequenceError(f"{part} must be an object")
        for key, entry in entries.items():
            cleaned = _clean_step_entry(entry, fields, f"{part[:-1]} '{key}'")
            if cleaned:
                result[part][str(key)] = cleaned
    return result


def step_while(sequence: dict, preset_id: str) -> dict:
    """A preset's While playing layer in a sequence (every field listed, null where unchanged)."""
    return clean_step_while((sequence.get("while") or {}).get(preset_id))


def set_step_while(sequence: dict, preset_id: str, data) -> dict:
    """The sequence with a preset's While playing layer replaced (checked). Raises SequenceError."""
    if preset_id not in (sequence.get("presets") or []):
        raise SequenceError(f"'{preset_id}' is not a preset of this sequence")
    cleaned = clean_step_while(data)
    result = dict(sequence)
    layers = dict(result.get("while") or {})
    if cleaned["tracks"] or cleaned["groups"]:
        layers[preset_id] = cleaned
    else:
        layers.pop(preset_id, None)
    if layers:
        result["while"] = layers
    else:
        result.pop("while", None)
    return result


def merge_step(preset: dict, step: Optional[dict]) -> dict:
    """A preset ({"tracks", "groups"}) with a step's While playing values on top (nulls keep the preset's)."""
    if not step:
        return preset
    merged = {"tracks": {k: dict(v or {}) for k, v in (preset.get("tracks") or {}).items()},
              "groups": {k: dict(v or {}) for k, v in (preset.get("groups") or {}).items()}}
    for part in ("tracks", "groups"):
        for key, values in (step.get(part) or {}).items():
            merged[part].setdefault(key, {}).update({f: v for f, v in (values or {}).items() if v is not None})
    return merged


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
        # The steps whose While playing layer changes something (the editor marks them)
        "while_steps": sorted(preset for preset, layer in (sequence.get("while") or {}).items()
                              if isinstance(layer, dict) and (layer.get("tracks") or layer.get("groups"))),
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
        if preset_id in (sequence.get("while") or {}):
            sequence["while"] = {p: s for p, s in sequence["while"].items() if p != preset_id}
            if sequence_id not in changed:
                changed.append(sequence_id)
    if changed:
        save_sequences(folder, sequences)
    return changed
