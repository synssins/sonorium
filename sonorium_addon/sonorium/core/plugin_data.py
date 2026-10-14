"""
Plugin attributes: values a plugin keeps on Sonorium's own objects, the way
Active Directory keeps extended attributes. A plugin declares a schema; core
stores values only for declared attributes, checks each one against its
declaration, and carries them along wherever the object goes (saves, edits,
theme export and import). Core never reads what they mean.

Object classes:

    theme    a theme              metadata.json, top-level "plugins"
    track    a track of a theme   metadata.json, the track's entry "plugins"
    file     a file in a group    the same entry ("Group/Name" keys only)
    preset   a theme's preset     presets.json, the preset's "plugins"
    channel  a channel            state.json, the channel's "plugins"
    speaker  a speaker            state.json, its speaker settings' "plugins"
    user     an account           users.json, the user's "plugins"
    group    a theme's group      metadata.json, the group's entry in "groups" ("plugins")
    sequence a theme's sequence   presets.json, the sequence's "plugins"
    step     a step of a sequence presets.json, the sequence's "step_plugins" {preset_id: block}
                                  (id "<sequence_id>/<preset_id>")
    playlist a playlist           state.json "playlists", the playlist's "plugins"
    playlist_entry  an entry      the entry's "plugins" (id "<playlist_id>/<entry id>": an entry
                                  keeps its id, and its values, when the order changes)
    speaker_group  a speaker group  state.json, the group's "plugins"
    area     an area              state.json settings "object_plugins" {"area": {id: block}}
    key      a key                the same, under "key" (a key's record is the sign-in store's)
    connection  a connection      the same, under "connection" (Home Assistant, MQTT and
                                  plugins' connections keep their values elsewhere)
    device   a plugin's device    the same, under "device" (core/devices.py keeps the device)

On every object the values sit under the plugin's id, so two plugins can both
have a "cue" attribute: {"plugins": {"wled": {"cue": "flash"}}}.

A declaration (manifest "attributes", BasePlugin.get_attributes(), or
self.attributes.declare() at load):

    {"track": {"cue": {"type": "enum", "options": ["flash", "rumble"],
                       "default": "flash", "label": "Light cue", "help": "..."},
               "intensity": {"type": "number", "min": 0, "max": 100, "integer": True}},
     "speaker": {"light_sync": {"type": "number", "min": 0, "label": "Light sync"}}}

Types and their limits (all optional unless said):
    string  max_length, pattern (a regular expression the whole text matches)
    number  min, max, integer (true: whole numbers only)
    bool    -
    enum    options (required): values, or {"value", "label"}
    list    items (required: a declaration for each item), min_items, max_items
    object  fields (required: name -> declaration); other keys are not kept
Every attribute may have default, label and help (for the UI, later).

What is kept, and when it goes:
- Anything not in the schema is discarded: a plugin that isn't installed, an
  attribute it doesn't declare, a value that doesn't fit. A theme imported into
  a Sonorium without the plugin loses those values on import (logged at info);
  a theme read from disk loses them when it loads (logged at debug).
- A plugin switched off keeps its schema and its values (nothing is shown for
  it). Switch it on again and they are there.
- A plugin uninstalled loses its schema, and its values are removed from every
  object (AttributeService.forget_plugin in plugins/attributes.py).
- Size: at most `plugin_data_limit` bytes (Settings > Advanced playback values,
  4096 by default, 0 = no limit) per plugin per object, as compact JSON. A
  change that would go over it is refused with a message naming the limit and
  where to change it; values already stored (an import, a lowered limit) are
  kept and a warning is logged.

The schemas live in state.json (SonoriumSettings.plugin_attributes) so they
are known before any plugin loads; bind() points the registry there. Without
a binding (tests, the engine on its own) stored values pass through as they
are, and only an import (strict) filters.

No imports from the web server, plugins or Home Assistant: theme_metadata.py
and state.py use this module.
"""
from __future__ import annotations

import copy
import json
import math
import re
from typing import Any, Callable, Iterable, Optional

from sonorium.obs import logger

OBJECT_CLASSES = ("theme", "track", "file", "preset", "channel", "speaker", "user", "group", "sequence", "step",
                  "playlist", "playlist_entry", "speaker_group", "area", "key", "connection", "plugin", "device")
# Kept in state.json settings "object_plugins" ({kind: {id: block}}): objects whose records live elsewhere
SIDE_TABLE_CLASSES = ("area", "key", "connection", "plugin", "device")
TYPES = ("string", "number", "bool", "enum", "list", "object")
# Accepted for the same words as plugin settings use
TYPE_ALIASES = {"boolean": "bool", "select": "enum", "text": "string", "dict": "object", "array": "list"}

# A track's entry carries both classes: moving a file into or out of a group keeps its values
TRACK_CLASSES = ("track", "file")

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
DEFAULT_LIMIT = 4096
MAX_DEPTH = 4
LIMIT_SETTING = "plugin_data_limit"


class PluginDataError(ValueError):
    """A declaration or value that doesn't fit (message is shown to the caller)."""


# --- declarations ------------------------------------------------------------------


def _option_values(field: dict) -> list:
    return [o.get("value") if isinstance(o, dict) else o for o in field.get("options") or []]


def _normalize_field(name: str, decl: Any, where: str, depth: int = 0) -> dict:
    """A well-formed declaration, or PluginDataError saying what is wrong."""
    if not isinstance(decl, dict):
        raise PluginDataError(f"{where}: '{name}' must be an object")
    kind = TYPE_ALIASES.get(decl.get("type"), decl.get("type"))
    if kind not in TYPES:
        raise PluginDataError(f"{where}: '{name}' has unknown type {decl.get('type')!r} (one of {', '.join(TYPES)})")
    field: dict = {"type": kind}
    for text in ("label", "help"):
        if isinstance(decl.get(text), str):
            field[text] = decl[text]

    def number(key, whole=False):
        if key in decl:
            value = decl[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                    or (whole and (value < 0 or int(value) != value)):
                raise PluginDataError(f"{where}: '{name}'.{key} must be a {'whole ' if whole else ''}number")
            field[key] = int(value) if whole else value

    if kind == "string":
        number("max_length", whole=True)
        if "pattern" in decl:
            try:
                re.compile(decl["pattern"])
            except (TypeError, re.error):
                raise PluginDataError(f"{where}: '{name}'.pattern is not a regular expression")
            field["pattern"] = decl["pattern"]
    elif kind == "number":
        number("min")
        number("max")
        if decl.get("integer"):
            field["integer"] = True
        if "min" in field and "max" in field and field["min"] > field["max"]:
            raise PluginDataError(f"{where}: '{name}'.min is above its max")
    elif kind == "enum":
        options = decl.get("options")
        if not isinstance(options, list) or not options:
            raise PluginDataError(f"{where}: enum '{name}' needs options")
        clean = []
        for option in options:
            value = option.get("value") if isinstance(option, dict) else option
            if not isinstance(value, (str, int, float, bool)):
                raise PluginDataError(f"{where}: enum '{name}' has an option that isn't text, a number or true/false")
            clean.append({"value": value, "label": option["label"]}
                         if isinstance(option, dict) and isinstance(option.get("label"), str) else value)
        field["options"] = clean
    elif kind in ("list", "object"):
        if depth >= MAX_DEPTH:
            raise PluginDataError(f"{where}: '{name}' is nested deeper than {MAX_DEPTH} levels")
        if kind == "list":
            field["items"] = _normalize_field(f"{name}[]", decl.get("items"), where, depth + 1)
            number("min_items", whole=True)
            number("max_items", whole=True)
        else:
            fields = decl.get("fields")
            if not isinstance(fields, dict) or not fields:
                raise PluginDataError(f"{where}: object '{name}' needs fields")
            field["fields"] = {}
            for sub, sub_decl in fields.items():
                if not isinstance(sub, str) or not NAME.match(sub):
                    raise PluginDataError(f"{where}: '{name}' has a field name {sub!r} that isn't a plain name")
                field["fields"][sub] = _normalize_field(f"{name}.{sub}", sub_decl, where, depth + 1)
    if "default" in decl:
        try:
            field["default"] = check_value(field, decl["default"], name)
        except PluginDataError as e:
            raise PluginDataError(f"{where}: default of '{name}' doesn't fit: {e}")
    return field


def normalize_declarations(decls: Any, plugin_id: str) -> dict:
    """
    {class: {name: field}} with only well-formed entries; each problem is
    logged as a warning and that attribute left out (the rest still count).
    """
    if not isinstance(decls, dict):
        if decls:
            logger.warning(f"Plugin {plugin_id}: attributes must be an object of object classes, ignored")
        return {}
    result: dict = {}
    for cls, attrs in decls.items():
        if cls not in OBJECT_CLASSES:
            logger.warning(f"Plugin {plugin_id}: unknown object class {cls!r} in attributes "
                           f"(one of {', '.join(OBJECT_CLASSES)}), ignored")
            continue
        if not isinstance(attrs, dict):
            logger.warning(f"Plugin {plugin_id}: attributes of '{cls}' must be an object, ignored")
            continue
        for name, decl in attrs.items():
            if not isinstance(name, str) or not NAME.match(name):
                logger.warning(f"Plugin {plugin_id}: attribute name {name!r} on '{cls}' isn't a plain name "
                               f"(letters, digits, _; up to 64), ignored")
                continue
            try:
                field = _normalize_field(name, decl, f"Plugin {plugin_id}, {cls}")
            except PluginDataError as e:
                logger.warning(f"{e}; ignored")
                continue
            result.setdefault(cls, {})[name] = field
    # A track's entry holds track and file values together: one name, one meaning
    track, file = result.get("track") or {}, result.get("file") or {}
    for name in [n for n in file if n in track and file[n] != track[n]]:
        logger.warning(f"Plugin {plugin_id}: attribute '{name}' is declared differently for track and file; "
                       f"the file one is ignored (they share the track's entry)")
        del file[name]
    if "file" in result and not result["file"]:
        del result["file"]
    return result


# --- values -------------------------------------------------------------------------


def check_value(field: dict, value: Any, name: str = "value", strict: bool = True) -> Any:
    """
    The value if it fits the field; PluginDataError if not. Numbers stay as
    given (integer fields: a whole float becomes an int). Objects keep only
    their declared fields: an unknown one is an error when strict, else left out.
    """
    kind = field["type"]
    if kind == "string":
        if not isinstance(value, str):
            raise PluginDataError(f"'{name}' must be text")
        if "max_length" in field and len(value) > field["max_length"]:
            raise PluginDataError(f"'{name}' is longer than {field['max_length']} characters")
        if "pattern" in field and not re.fullmatch(field["pattern"], value):
            raise PluginDataError(f"'{name}' doesn't match the pattern {field['pattern']!r}")
        return value
    if kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise PluginDataError(f"'{name}' must be a number")
        if field.get("integer"):
            if int(value) != value:
                raise PluginDataError(f"'{name}' must be a whole number")
            value = int(value)
        if "min" in field and value < field["min"]:
            raise PluginDataError(f"'{name}' must be at least {field['min']}")
        if "max" in field and value > field["max"]:
            raise PluginDataError(f"'{name}' must be at most {field['max']}")
        return value
    if kind == "bool":
        if not isinstance(value, bool):
            raise PluginDataError(f"'{name}' must be true or false")
        return value
    if kind == "enum":
        options = _option_values(field)
        # 1 == True in Python: compare types too
        if not any(value == o and type(value) is type(o) for o in options):
            raise PluginDataError(f"'{name}' must be one of {', '.join(repr(o) for o in options)}")
        return value
    if kind == "list":
        if not isinstance(value, list):
            raise PluginDataError(f"'{name}' must be a list")
        if "min_items" in field and len(value) < field["min_items"]:
            raise PluginDataError(f"'{name}' needs at least {field['min_items']} items")
        if "max_items" in field and len(value) > field["max_items"]:
            raise PluginDataError(f"'{name}' has more than {field['max_items']} items")
        return [check_value(field["items"], item, f"{name}[{i}]", strict) for i, item in enumerate(value)]
    if kind == "object":
        if not isinstance(value, dict):
            raise PluginDataError(f"'{name}' must be an object")
        fields = field["fields"]
        unknown = [k for k in value if k not in fields]
        if unknown and strict:
            raise PluginDataError(f"'{name}' has no field {', '.join(repr(k) for k in unknown)}")
        return {k: check_value(fields[k], v, f"{name}.{k}", strict) for k, v in value.items() if k in fields}
    raise PluginDataError(f"'{name}' has an unknown type")


def size_of(values: Any) -> int:
    """Bytes of the values as compact JSON (UTF-8): what the size limit counts."""
    return len(json.dumps(values, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


# --- the registry ----------------------------------------------------------------------


class Registry:
    """Every installed plugin's declarations: {plugin_id: {class: {name: field}}}."""

    def __init__(self):
        self._source: Optional[Callable[[], dict]] = None
        self._save: Optional[Callable[[], None]] = None
        self._limit: Optional[Callable[[], float]] = None
        self._local: dict = {}

    # binding

    def bind(self, source: Callable[[], dict], save: Optional[Callable[[], None]] = None,
             limit: Optional[Callable[[], float]] = None) -> None:
        """Keep the declarations in the dict source() returns (state.json), saved with save()."""
        self._source, self._save, self._limit = source, save, limit

    def unbind(self) -> None:
        self._source = self._save = self._limit = None
        self._local = {}

    @property
    def bound(self) -> bool:
        return self._source is not None

    def _decls(self) -> dict:
        if self._source is None:
            return self._local
        try:
            decls = self._source()
        except Exception:
            return {}
        return decls if isinstance(decls, dict) else {}

    def _persist(self) -> None:
        if self._save is not None:
            try:
                self._save()
            except Exception as e:
                logger.error(f"Plugin attributes not saved: {e}")

    def limit(self) -> int:
        """Bytes per plugin per object (0: no limit)."""
        if self._limit is None:
            return DEFAULT_LIMIT
        try:
            value = float(self._limit())
        except Exception:
            return DEFAULT_LIMIT
        return max(0, int(value)) if math.isfinite(value) else DEFAULT_LIMIT

    # declarations

    def declare(self, plugin_id: str, decls: Any) -> dict:
        """A plugin's whole schema (replacing the one it had). Returns it as kept."""
        clean = normalize_declarations(decls, plugin_id)
        store = self._decls()
        if store.get(plugin_id) != clean:
            if clean:
                store[plugin_id] = clean
            else:
                store.pop(plugin_id, None)
            if self._source is None:
                self._local = store
            self._persist()
            count = sum(len(v) for v in clean.values())
            logger.info(f"Plugin {plugin_id}: {count} attribute(s) declared" if count
                        else f"Plugin {plugin_id}: no attributes declared")
        return copy.deepcopy(clean)

    def forget(self, plugin_id: str) -> bool:
        """The plugin was uninstalled: its schema goes. True if it had one."""
        store = self._decls()
        if plugin_id not in store:
            return False
        del store[plugin_id]
        self._persist()
        return True

    def declarations(self, plugin_id: Optional[str] = None) -> dict:
        decls = self._decls()
        if plugin_id is not None:
            return copy.deepcopy(decls.get(plugin_id) or {})
        return copy.deepcopy(decls)

    def has(self, plugin_id: str) -> bool:
        return plugin_id in self._decls()

    def fields(self, plugin_id: str, classes: Iterable[str]) -> dict:
        """{name: field} the plugin declares for any of these classes (the first class wins a name)."""
        own = self._decls().get(plugin_id) or {}
        result: dict = {}
        for cls in classes:
            for name, field in (own.get(cls) or {}).items():
                result.setdefault(name, field)
        return result

    # values

    def clean_block(self, block: Any, classes: Iterable[str], where: str = "", strict: bool = False) -> dict:
        """
        A stored "plugins" block with only what the schema has: values of
        installed plugins, declared for one of these classes, that fit. What
        is left out is logged (info when strict, i.e. an import; else debug).
        Unbound and not strict: the block as it is (a copy).
        """
        if not isinstance(block, dict) or not block:
            return {}
        if not self.bound and not strict:
            return copy.deepcopy(block)
        classes = tuple(classes)
        result, dropped = {}, []
        limit = self.limit()
        for plugin_id, values in block.items():
            if not isinstance(values, dict) or not self.has(plugin_id):
                dropped.append(f"{plugin_id} (not installed)" if isinstance(values, dict) else f"{plugin_id} (not an object)")
                continue
            fields = self.fields(plugin_id, classes)
            kept = {}
            for name, value in values.items():
                field = fields.get(name)
                if field is None:
                    dropped.append(f"{plugin_id}.{name} (not declared)")
                    continue
                try:
                    kept[name] = check_value(field, value, name, strict=False)
                except PluginDataError as e:
                    dropped.append(f"{plugin_id}.{name} ({e})")
            if kept:
                if limit and size_of(kept) > limit:
                    logger.warning(f"{where or 'Plugin data'}: {plugin_id}'s values are {size_of(kept)} bytes, "
                                   f"over the {limit}-byte limit (kept; changes are refused until smaller)")
                result[plugin_id] = kept
        if dropped:
            message = f"{where or 'Plugin data'}: left out (not in any installed plugin's schema): {', '.join(dropped)}"
            (logger.info if strict else logger.debug)(message)
        return result

    def values(self, block: Any, plugin_id: str, classes: Iterable[str], defaults: bool = True) -> dict:
        """The plugin's values on an object: its defaults, overridden by the stored values that fit."""
        fields = self.fields(plugin_id, tuple(classes))
        result = {n: copy.deepcopy(f["default"]) for n, f in fields.items() if "default" in f} if defaults else {}
        stored = (block or {}).get(plugin_id) if isinstance(block, dict) else None
        for name, value in (stored or {}).items() if isinstance(stored, dict) else ():
            field = fields.get(name)
            if field is None:
                continue
            try:
                result[name] = check_value(field, value, name, strict=False)
            except PluginDataError:
                pass
        return result

    def update_block(self, block: Any, plugin_id: str, cls: str, updates: Any) -> dict:
        """
        The block after a plugin's values change on an object of class `cls`:
        `updates` is {name: value, or None to remove it}; None for `updates`
        removes all of the plugin's values. Checked against the schema and the
        size limit; PluginDataError (every problem in one message) if not.
        Other plugins' values are kept as they are.
        """
        if cls not in OBJECT_CLASSES:
            raise PluginDataError(f"Unknown object class '{cls}'")
        result = copy.deepcopy(block) if isinstance(block, dict) else {}
        if updates is None:
            result.pop(plugin_id, None)
            return result
        if not isinstance(updates, dict):
            raise PluginDataError("Attribute values must be an object")
        if not self.has(plugin_id):
            raise PluginDataError(f"Plugin '{plugin_id}' has no attributes (not installed, or declares none)")
        fields = self.fields(plugin_id, (cls,))
        current = self.values(result, plugin_id, (cls,), defaults=False)
        # Values for the entry's other class (a track's file values) stay
        if cls in TRACK_CLASSES:
            current = {**self.values(result, plugin_id, TRACK_CLASSES, defaults=False), **current}
        problems = []
        for name, value in updates.items():
            field = fields.get(name)
            if field is None:
                problems.append(f"'{name}' is not an attribute {plugin_id} declares for a {cls}")
                continue
            if value is None:
                current.pop(name, None)
                continue
            try:
                current[name] = check_value(field, value, name)
            except PluginDataError as e:
                problems.append(str(e))
        if problems:
            raise PluginDataError("; ".join(problems))
        limit = self.limit()
        if limit and current and size_of(current) > limit:
            raise PluginDataError(
                f"{plugin_id}'s values on this {cls} would be {size_of(current)} bytes; the limit is {limit} bytes "
                f"per plugin per object (Settings > Advanced, '{LIMIT_SETTING}'; 0 = no limit). "
                f"Keep bigger data in a file and store its name.")
        if current:
            result[plugin_id] = current
        else:
            result.pop(plugin_id, None)
        return result


registry = Registry()


def clean_block(block: Any, classes: Iterable[str], where: str = "", strict: bool = False) -> dict:
    """registry.clean_block (the module's registry)."""
    return registry.clean_block(block, classes, where=where, strict=strict)


def filter_theme(metadata, where: str = "", strict: bool = False) -> None:
    """
    A ThemeMetadata's plugin values (theme, tracks, presets) cut down to the
    schema, in place. strict for an import: without the plugin, its values go.
    """
    label = where or f"Theme '{getattr(metadata, 'name', '')}'"
    metadata.plugins = clean_block(getattr(metadata, "plugins", None), ("theme",), label, strict)
    for key, track in (getattr(metadata, "tracks", None) or {}).items():
        if hasattr(track, "plugins"):
            track.plugins = clean_block(track.plugins, TRACK_CLASSES, f"{label}, track '{key}'", strict)
    filter_presets(getattr(metadata, "presets", None), label, strict)
    filter_groups(getattr(metadata, "groups", None), label, strict)


def filter_groups(groups: Any, where: str = "", strict: bool = False) -> None:
    """Each theme group's "plugins" cut down to the schema, in place (dropped when empty)."""
    for name, settings in (groups or {}).items() if isinstance(groups, dict) else ():
        if not isinstance(settings, dict) or "plugins" not in settings:
            continue
        kept = clean_block(settings.get("plugins"), ("group",), f"{where}, group '{name}'", strict)
        if kept:
            settings["plugins"] = kept
        else:
            del settings["plugins"]


def filter_sequences(sequences: Any, where: str = "", strict: bool = False) -> None:
    """Each sequence's "plugins" and its steps' ("step_plugins") cut down to the schema, in place."""
    for sid, sequence in (sequences or {}).items() if isinstance(sequences, dict) else ():
        if not isinstance(sequence, dict):
            continue
        if "plugins" in sequence:
            kept = clean_block(sequence.get("plugins"), ("sequence",), f"{where}, sequence '{sid}'", strict)
            if kept:
                sequence["plugins"] = kept
            else:
                del sequence["plugins"]
        steps = sequence.get("step_plugins")
        if steps is not None:
            clean = {}
            for preset_id, block in steps.items() if isinstance(steps, dict) else ():
                kept = clean_block(block, ("step",), f"{where}, sequence '{sid}' step '{preset_id}'", strict)
                if kept and preset_id in (sequence.get("presets") or []):
                    clean[preset_id] = kept
            if clean:
                sequence["step_plugins"] = clean
            else:
                del sequence["step_plugins"]


def filter_presets(presets: Any, where: str = "", strict: bool = False) -> None:
    """Each preset's "plugins" cut down to the schema, in place (dropped when empty)."""
    for preset_id, preset in (presets or {}).items() if isinstance(presets, dict) else ():
        if not isinstance(preset, dict) or "plugins" not in preset:
            continue
        kept = clean_block(preset.get("plugins"), ("preset",), f"{where}, preset '{preset_id}'", strict)
        if kept:
            preset["plugins"] = kept
        else:
            del preset["plugins"]
