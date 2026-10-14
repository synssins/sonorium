"""
"Load my device presets into themes" (core/backups.py): an account's backed-up
mixes put into the matching server themes as ordinary, editable presets and
sequences, named after the phone they came from: "Night mix (Galaxy S25 Ultra)".

The only way a backup ever writes into a server theme. It runs when the
account switches it on (every mix it has), and then after each mix a phone
backs up, while it stays on. Switched off, nothing more is loaded; what was
loaded stays, as ordinary presets.

Matching: a mix's key is the id of the server theme the phone's copy came
from; only a theme with exactly that id is used (no name or folder guessing).
A theme that isn't there (deleted, or never on this server) is skipped.

What goes in, checked as the server checks its own:

- a preset keeps only the theme's tracks and groups, each value checked as
  the Theme Editor checks it; plugin attributes only as their plugins'
  schemas allow (strict, as an import); never the default;
- a sequence keeps only presets that were loaded (their server ids), its
  timing, transitions and While playing layers checked as the API checks them.

The next backup from the same phone (account.json "loaded" remembers, per
phone and theme, which server preset each phone preset became, and both
fingerprints):

    phone preset unchanged                     nothing happens
    changed, server copy not edited since      the server copy is updated in place
    changed, server copy edited or deleted     a new copy is added (the edited one stays as it is)
    gone from the phone                        the server copy stays (an ordinary preset now)

Sequences follow the same rules.
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable, Optional

from sonorium.obs import logger

PRESET_FIELDS = ("name", "tracks", "groups", "plugins")
SEQUENCE_FIELDS = ("name", "presets", "at_end", "hold_min", "hold_max", "ramp", "transitions", "while",
                   "plugins", "step_plugins")
MAX_NAME = 100


def fingerprint(item: dict, fields) -> str:
    data = {k: item.get(k) for k in fields if item.get(k) not in (None, {}, [])}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def labelled(name: str, device_name: str) -> str:
    """ "Night mix (Galaxy S25 Ultra)", at most MAX_NAME characters."""
    suffix = f" ({device_name})" if device_name else ""
    base = (name or "Preset").strip() or "Preset"
    return base[:max(1, MAX_NAME - len(suffix))].rstrip() + suffix


def _share(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return max(0.0, min(1.0, float(value)))


def clean_groups(groups, group_names) -> dict:
    from sonorium.recording import GROUP_PRESET_SETTINGS
    out = {}
    for name, values in (groups or {}).items() if isinstance(groups, dict) else ():
        if name not in group_names or not isinstance(values, dict):
            continue
        kept = {}
        for k, v in values.items():
            if k not in GROUP_PRESET_SETTINGS:
                continue
            if k in ("muted", "pause_between"):
                if isinstance(v, bool):
                    kept[k] = v
            elif k in ("gap_min", "gap_max"):
                if not isinstance(v, bool) and isinstance(v, (int, float)) and 0 <= v <= 86400:
                    kept[k] = float(v)
            else:
                share = _share(v)
                if share is not None:
                    kept[k] = share
        if kept:
            out[name] = kept
    return out


def clean_preset(preset: dict, track_keys, group_names, device_name: str, where: str) -> dict:
    """A phone preset as a server preset of this theme (see the module notes)."""
    from sonorium.core import plugin_data, theme_edit
    tracks = {}
    for key, values in (preset.get("tracks") or {}).items():
        if key not in track_keys or not isinstance(values, dict):
            continue
        kept = {}
        for k, v in values.items():
            if k not in theme_edit.TRACK_SETTINGS:
                continue
            try:
                kept.update(theme_edit.clean_track_values({k: v}))
            except (theme_edit.EditError, ValueError, TypeError):
                pass
        if kept:
            tracks[key] = kept
    out = {"name": labelled(preset.get("name") or "", device_name), "is_default": False, "tracks": tracks}
    groups = clean_groups(preset.get("groups"), group_names)
    if groups:
        out["groups"] = groups
    if isinstance(preset.get("plugins"), dict) and preset["plugins"]:
        holder = {"p": {"plugins": preset["plugins"]}}
        plugin_data.filter_presets(holder, where, strict=True)
        if holder["p"].get("plugins"):
            out["plugins"] = holder["p"]["plugins"]
    return out


def clean_sequence(sequence: dict, id_map: dict, device_name: str, where: str) -> Optional[dict]:
    """A phone sequence with its presets as the server ids they were loaded as (None: none of them was)."""
    from sonorium.core import plugin_data, sequences as seq
    presets = [id_map[p] for p in (sequence.get("presets") or []) if isinstance(p, str) and p in id_map]
    if not presets:
        return None
    data = {"name": labelled(sequence.get("name") or "", device_name), "presets": presets}
    for k in ("at_end", "hold_min", "hold_max", "ramp"):
        if k in sequence:
            data[k] = sequence[k]
    transitions = sequence.get("transitions")
    if isinstance(transitions, dict):
        data["transitions"] = {id_map[p]: v for p, v in transitions.items() if p in id_map}
    try:
        cleaned = seq.clean_sequence(data, presets)
    except seq.SequenceError:  # a value this server doesn't take: the presets in order, the theme's timing
        data.pop("transitions", None)
        for k in ("at_end", "hold_min", "hold_max", "ramp"):
            data.pop(k, None)
        try:
            cleaned = seq.clean_sequence(data, presets)
        except seq.SequenceError:
            return None
    for p, layer in (sequence.get("while") or {}).items() if isinstance(sequence.get("while"), dict) else ():
        if p in id_map:
            try:
                cleaned = seq.set_step_while(cleaned, id_map[p], layer)
            except seq.SequenceError:
                pass
    if isinstance(sequence.get("plugins"), dict):
        cleaned["plugins"] = sequence["plugins"]
    if isinstance(sequence.get("step_plugins"), dict):
        cleaned["step_plugins"] = {id_map[p]: b for p, b in sequence["step_plugins"].items() if p in id_map}
    holder = {"s": cleaned}
    plugin_data.filter_sequences(holder, where, strict=True)
    return holder["s"]


def _unique_name(name: str, taken) -> str:
    names = {str(n).casefold() for n in taken}
    if name.casefold() not in names:
        return name
    n = 2
    while f"{name} {n}".casefold() in names:
        n += 1
    return f"{name} {n}"


def merge(items: dict, phone_items: dict, record: dict, make: Callable, fields, new_id: Callable) -> tuple:
    """
    Phone items into the server's (`items`, changed in place) by the rules in
    the module notes. record: {phone id: {"id", "hash", "source"}}, the last
    time. Returns (new record, ids added or updated).
    """
    changed, out = [], {}
    for phone_id, phone_item in phone_items.items():
        made = make(phone_id, phone_item)
        if made is None:
            continue
        source = fingerprint(phone_item, fields)
        last = record.get(phone_id)
        if last and last.get("source") == source:
            out[phone_id] = last  # unchanged on the phone
            continue
        current = items.get(last["id"]) if last else None
        if last and current is not None and fingerprint(current, fields) == last.get("hash"):
            server_id = last["id"]  # not edited on the server since: update in place
            keep = {k: v for k, v in current.items() if k not in fields}
            items[server_id] = {**keep, **made}
        else:
            made["name"] = _unique_name(made["name"], [i.get("name") for i in items.values()])
            server_id = new_id(made["name"], items)
            items[server_id] = made
        out[phone_id] = {"id": server_id, "hash": fingerprint(items[server_id], fields), "source": source}
        changed.append(server_id)
    return out, changed


class ThemeAccess:
    """How the loader reaches the server's themes (the web app gives it the theme metadata manager)."""

    def __init__(self, folder_for: Callable, metadata_for: Callable, save: Callable,
                 preset_saved: Optional[Callable] = None):
        self.folder_for = folder_for          # theme id -> folder or None (that id exactly)
        self.metadata_for = metadata_for      # folder -> ThemeMetadata
        self.save = save                      # (theme id, folder, metadata) -> bool
        self.preset_saved = preset_saved      # (theme id, preset id): playing channels follow


def load_mix(access: ThemeAccess, theme_id: str, mix: dict, device_name: str, record: dict) -> tuple[dict, dict]:
    """
    One phone's mix into the server theme `theme_id`. Returns (the new record,
    a summary {"theme_id", "presets": n changed, "sequences": n changed} or
    {"theme_id", "skipped": reason}).
    """
    from sonorium.core import sequences as seq, theme_edit
    folder = access.folder_for(theme_id)
    if folder is None:
        return record, {"theme_id": theme_id, "skipped": "no server theme with this id"}
    metadata = access.metadata_for(folder)
    where = f"Backup of theme '{getattr(metadata, 'name', theme_id)}' from {device_name}"
    track_keys = set(theme_edit.track_keys(folder, metadata))
    group_names = set((getattr(metadata, "groups", None) or {}).keys())
    presets = metadata.presets if metadata.presets is not None else {}
    metadata.presets = presets

    preset_record, preset_changed = merge(
        presets, mix.get("presets") or {}, record.get("presets") or {},
        lambda pid, p: clean_preset(p, track_keys, group_names, device_name, f"{where}, preset '{pid}'"),
        PRESET_FIELDS, theme_edit.new_preset_id)
    if preset_changed and not access.save(theme_id, folder, metadata):
        raise OSError(f"Couldn't save the presets of theme '{theme_id}'")
    for preset_id in preset_changed:
        if access.preset_saved:
            access.preset_saved(theme_id, preset_id)

    id_map = {pid: r["id"] for pid, r in preset_record.items() if r["id"] in presets}
    sequences = seq.load_sequences(folder)
    sequence_record, sequence_changed = merge(
        sequences, mix.get("sequences") or {}, record.get("sequences") or {},
        lambda sid, s: clean_sequence(s, id_map, device_name, f"{where}, sequence '{sid}'"),
        SEQUENCE_FIELDS, lambda name, existing: seq.new_sequence_id(name, existing))
    if sequence_changed:
        seq.save_sequences(folder, sequences)
        saved = seq.load_sequences(folder)  # as the server reads them back: the fingerprint to compare next time
        for r in sequence_record.values():
            if r["id"] in sequence_changed and r["id"] in saved:
                r["hash"] = fingerprint(saved[r["id"]], SEQUENCE_FIELDS)
    if preset_changed or sequence_changed:
        logger.info(f"{where}: {len(preset_changed)} preset(s) and {len(sequence_changed)} sequence(s) loaded")
    return ({"presets": preset_record, "sequences": sequence_record},
            {"theme_id": theme_id, "presets": len(preset_changed), "sequences": len(sequence_changed)})


def load_account(store, owner: str, access: ThemeAccess, device_name: Callable[[str, dict], str],
                 only: Optional[tuple[str, str]] = None) -> list[dict]:
    """
    The account's mixes (or only (device id, key)) into the server themes,
    when the account has loading on. Never raises: a theme that fails is
    logged and reported.
    """
    from sonorium.core.backups import loaded_key
    results = []
    with store.lock(owner):
        account = store.account(owner)
        if not account["settings"].get("load_into_themes"):
            return results
        for device_id, device, doc in store.mixes(owner):
            if only is not None and (device_id, doc["key"]) != only:
                continue
            k = loaded_key(device_id, doc["key"])
            try:
                record, summary = load_mix(access, doc["key"], doc.get("mix") or {},
                                           device_name(device_id, device), account["loaded"].get(k) or {})
                account["loaded"][k] = record
            except Exception as e:
                logger.warning(f"Backup of theme '{doc['key']}' could not be loaded: {e}")
                summary = {"theme_id": doc["key"], "skipped": str(e)}
            results.append(summary)
        store.save_account(owner, account)
    return results
