"""
Plugin contributions to the web UI: the catalogue of targets, the open spots
on each, and the contract every spot enforces (docs/PLUGIN_DEVELOPMENT.md,
"Adding to any screen").

A target is a part of the web UI with a stable id: a screen
("screen.themes"), a kind of row or card ("speaker.row"), an editor
("channel.editor"). Each target has FIXED parts, which plugins can never
change, hide, reorder or remove, and OPEN SPOTS, where plugins may add. A
plugin only adds, and only in the open spots:

    header     buttons after a screen's buttons
    top        one strip per plugin above a screen's content
    menu       items in a ⋯ menu, after its items, before destructive ones
    settings   one collapsed group per plugin in an editor (or in the ⋯ menu
               of a thing without an editor dialog: a track, a speaker)
    badges     a small badge after a row's badges
    columns    a value at the end of a row's name column, with a heading
    info       one line on a card, after its lines
    plugin     the plugin's UI in its row in Settings › Plugins
    pages      an entry in the left menu or in Settings, opening a page drawn
               from the same element kinds

Every spot has a contract (SPOT_TYPES): which element kinds it takes, how
many per plugin, the longest label and text, the field types and bindings it
accepts, how many fields one element may hold. Core validates a plugin's
contributions against the contracts when the plugin registers them: an
element outside its contract is refused (not drawn) with a log line and a
warning on the plugin's row in Settings › Plugins naming the spot and the
rule. Too many elements in one spot refuses all of that plugin's elements in
the spot; nothing is ever cut short. Every limit is in this file.

Element kinds:

    field    one control bound to a value: {"bind": {"attribute": name}} (the
             plugin's declared attribute on the object the spot shows),
             {"bind": {"setting": name}} (one of the plugin's settings) or
             {"bind": {"action": id}} (no stored value: the plugin's
             ui_action() gets each change). Types: text, number, slider,
             toggle, select, colour, list.
    action   a button or menu item calling the plugin's ui_action()
    badge    a short badge; text, or a value (attribute or live)
    column   a labelled value in a row
    info     a line of text, or a value
    group    a titled set of fields
    page     a whole page: sections of the kinds above

Contracts are versioned with the plugin API (compat.API_VERSION): a minor
version only ever adds targets, spots or element kinds, or raises a limit.
"""
from __future__ import annotations

import copy
import math
import re
from typing import Any, Optional

from sonorium.plugins import compat

CONTRACT_VERSION = compat.API_VERSION

FIELD_TYPES = ("text", "number", "slider", "toggle", "select", "colour", "list")
ELEMENT_KINDS = ("field", "action", "badge", "column", "info", "group", "page")
BINDINGS = ("attribute", "setting", "action")
VALUE_SOURCES = ("attribute", "live")
STATES = ("ok", "warning", "error", "off")
INFO_STYLES = ("normal", "muted", "success", "warning", "error")

# Limits every element shares (help text, options, conditions, ...)
COMMON = {
    "id_pattern": r"^[a-z0-9][a-z0-9_-]{0,39}$",
    "id_words": "a-z, 0-9, _ and -; up to 40 characters",
    "attr_fact_pattern": r"attr\.[A-Za-z][A-Za-z0-9_]{0,63}",
    "help_max": 120,
    "confirm_max": 80,
    "unit_max": 8,
    "placeholder_max": 40,
    "options_max": 20,
    "option_label_max": 24,
    "list_items_max": 20,
    "text_value_max": 200,
    "number_abs_max": 1_000_000_000,
    "slider_steps_max": 1000,
    "when_max": 4,
    "short_name_max": 16,
    "elements_per_plugin_max": 500,
}

# Pages (the "pages" spot): what one page may hold
PAGE = {
    "title_max": 24,
    "sections_max": 6,
    "section_title_max": 40,
    "elements_per_section_max": 12,
    "kinds": ("field", "action", "info", "group", "badge"),
    "bindings": ("setting", "action"),
    "field_types": FIELD_TYPES,
    "max_fields_per_item": 6,
    "label_max": 24,
    "text_max": 200,
}

# The contract of each kind of open spot. Every limit lives here.
SPOT_TYPES: dict[str, dict] = {
    "header": {
        "kinds": ("action",), "max_per_plugin": 1, "label_max": 20, "text_max": 0,
        "field_types": (), "bindings": (), "max_fields_per_item": 0, "collapsible": False,
        "position": "after the screen's buttons; on a phone, in the screen's title-bar ⋯ (or, without one, "
                    "in one ⋯ for them), so the screen's buttons and help keep their row",
    },
    "top": {
        "kinds": ("info", "action", "field"), "max_per_plugin": 3, "label_max": 24, "text_max": 120,
        "field_types": ("toggle", "select", "number", "slider", "text"), "bindings": ("setting", "action"),
        "max_fields_per_item": 1, "collapsible": False,
        "position": "one strip per plugin, after the screen's bars, above its content",
    },
    "badges": {
        "kinds": ("badge",), "max_per_plugin": 1, "label_max": 24, "text_max": 12,
        "field_types": (), "bindings": (), "max_fields_per_item": 0, "collapsible": False,
        "position": "after the item's badges",
    },
    "columns": {
        "kinds": ("column",), "max_per_plugin": 1, "label_max": 12, "text_max": 16,
        "field_types": (), "bindings": (), "max_fields_per_item": 0, "collapsible": False,
        "position": "at the end of the name column, with its heading above",
    },
    "info": {
        "kinds": ("info",), "max_per_plugin": 1, "label_max": 24, "text_max": 80,
        "field_types": (), "bindings": (), "max_fields_per_item": 0, "collapsible": False,
        "position": "one line after the card's lines",
    },
    "menu": {
        "kinds": ("action", "field", "group"), "max_per_plugin": 3, "label_max": 20, "text_max": 0,
        "field_types": ("toggle", "select", "number"), "bindings": ("attribute", "action"),
        "max_fields_per_item": 2, "collapsible": False,
        "position": "after the menu's items, before destructive ones",
    },
    "settings": {
        "kinds": ("field", "info", "action", "group"), "max_per_plugin": 8, "label_max": 24, "text_max": 120,
        "field_types": FIELD_TYPES, "bindings": ("attribute", "action"),
        "max_fields_per_item": 6, "collapsible": True,
        "position": "one collapsed group per plugin, after the editor's settings",
    },
    "plugin": {
        "kinds": ("field", "info", "action", "group"), "max_per_plugin": 20, "label_max": 24, "text_max": 200,
        "field_types": FIELD_TYPES, "bindings": ("setting", "action"),
        "max_fields_per_item": 6, "collapsible": False, "own_only": True,
        "position": "the plugin's row in Settings › Plugins, after its settings",
    },
    "pages": {
        "kinds": ("page",), "max_per_plugin": 2, "label_max": 24, "text_max": 0,
        "field_types": FIELD_TYPES, "bindings": ("setting", "action"),
        "max_fields_per_item": 6, "collapsible": False,
        "position": "after the menu's entries",
    },
}

# Where a target's settings spot is drawn: in its editor dialog, or (things
# without an editor dialog) at the end of its ⋯ menu
IN_MENU = "in its ⋯ menu, after the menu items"

# Facts a visibility condition ("when") can test, per kind of object. Every
# condition may also test "phone" (the phone layout) and, on objects that keep
# plugin attributes, "attr.<name>" (an attribute the plugin declares).
FACTS: dict[str, dict] = {
    "channel": {"playing": "bool", "mode": ["theme", "playlist"], "theme": "text", "preset": "text",
                "muted": "bool", "speakers": "number"},
    "theme": {"favorite": "bool", "tracks": "number", "playing": "bool"},
    "track": {"section": ["unassigned", "continuous", "sparse", "presence"], "muted": "bool", "linked": "bool",
              "in_group": "bool"},
    "file": {"section": ["unassigned", "continuous", "sparse", "presence"], "muted": "bool", "linked": "bool",
             "in_group": "bool"},
    "group": {"section": ["continuous", "sparse", "presence"], "muted": "bool", "tracks": "number"},
    "preset": {"default": "bool"},
    "playlist": {"playing": "bool", "order": ["in_order", "shuffle"], "entries": "number"},
    "playlist_entry": {"sequence": "bool"},
    "sequence": {"steps": "number", "end": ["reverse", "restart", "stop"]},
    "step": {"advanced": "bool"},
    "speaker": {"online": "bool", "enabled": "bool", "type": "text", "device": "bool"},
    "speaker_group": {"speakers": "number"},
    "plugin": {"enabled": "bool", "builtin": "bool"},
    "user": {"role": ["admin", "user"], "me": "bool"},
    "key": {},
    "connection": {"configured": "bool"},
    "area": {"speakers": "number"},
}
GLOBAL_FACTS = {"phone": "bool"}

# Object kinds whose plugin attributes are stored (core/plugin_data.py): every object a target shows
from sonorium.core.plugin_data import OBJECT_CLASSES as ATTRIBUTE_KINDS  # noqa: E402


def _screen(view: str, title: str, fixed: list, menu: bool = False) -> dict:
    spots = ["header", "top"] + (["menu"] if menu else [])
    return {"id": f"screen.{view}", "title": title, "screen": view, "object": None, "spots": spots,
            "fixed": ["title", "actions"] + fixed}


# Every target. A new screen built with the shared helpers (docs/UI_STYLE.md,
# "Extensible screens") adds its line here; tests/test_ui_contributions.py
# checks that the web UI and this list name the same targets.
TARGETS: list[dict] = [
    # --- screens: any page has a header and a top spot ---
    _screen("sessions", "Channels", ["cards"]),
    _screen("playlists", "Playlists", ["list"]),
    _screen("themes", "Themes", ["search", "filters", "collection"], menu=True),
    _screen("settings-connection", "Settings › Connection", ["connections"]),
    _screen("settings-audio", "Settings › Audio", ["settings"], menu=True),
    _screen("settings-spaces", "Settings › Floors & Areas", ["floors"]),
    _screen("settings-speakers", "Settings › Speakers", ["toolbar", "list"]),
    _screen("settings-groups", "Settings › Speaker Groups", ["list"]),
    _screen("settings-plugins", "Settings › Plugins", ["tabs", "list"], menu=True),
    _screen("settings-logs", "Settings › Logs", ["log"]),
    _screen("settings-advanced", "Settings › Advanced", ["features", "values"]),
    _screen("plugin-users", "Settings › Users & devices", ["list"]),
    _screen("plugin-keys", "Settings › Keys", ["list"]),
    _screen("status", "Status", ["tiles", "channels"]),
    # --- the left menu ---
    {"id": "sidebar", "title": "Left menu", "screen": None, "object": None, "spots": ["pages"],
     "fixed": ["logo", "sections", "version"]},
    {"id": "settings.nav", "title": "Settings menu", "screen": None, "object": None, "spots": ["pages"],
     "fixed": ["entries"]},
    # --- channels ---
    {"id": "channel.card", "title": "Channel card", "screen": "sessions", "object": "channel",
     "spots": ["badges", "info", "menu"], "fixed": ["name", "pickers", "volume", "recent", "play", "edit"]},
    {"id": "channel.editor", "title": "Channel editor", "screen": "sessions", "object": "channel",
     "spots": ["settings"], "fixed": ["name", "plays", "theme", "preset", "volume", "speakers", "footer"]},
    {"id": "status.channel", "title": "Status: a playing channel", "screen": "status", "object": "channel",
     "spots": ["badges", "info"], "fixed": ["name", "tracks"]},
    # --- playlists ---
    {"id": "playlist.row", "title": "Playlist row", "screen": "playlists", "object": "playlist",
     "spots": ["badges", "columns", "menu"], "fixed": ["name", "entries", "order", "repeat", "play"]},
    {"id": "playlist.editor", "title": "Playlist editor", "screen": "playlists", "object": "playlist",
     "spots": ["settings", "menu"], "fixed": ["name", "timing", "order", "entries", "footer"]},
    {"id": "playlist.entry", "title": "Playlist entry", "screen": "playlists", "object": "playlist_entry",
     "spots": ["menu"], "fixed": ["theme", "preset"]},
    # --- themes ---
    {"id": "theme.card", "title": "Theme card and row", "screen": "themes", "object": "theme",
     "spots": ["badges", "menu"], "fixed": ["name", "description", "categories", "preview", "favorite", "edit"]},
    {"id": "theme.editor", "title": "Theme Editor", "screen": "themes", "object": "theme",
     "spots": ["settings", "menu"], "fixed": ["name", "icon", "categories", "description", "tracks", "footer"]},
    {"id": "theme.track", "title": "Theme Editor: a track", "screen": "themes", "object": "track",
     "spots": ["badges", "menu", "settings"], "settings_at": IN_MENU, "fixed": ["name", "mode", "sliders", "mute"]},
    {"id": "theme.file", "title": "Theme Editor: a file in a group", "screen": "themes", "object": "file",
     "spots": ["badges", "menu", "settings"], "settings_at": IN_MENU, "fixed": ["name", "mode", "sliders", "mute"]},
    {"id": "theme.group", "title": "Theme Editor: a group", "screen": "themes", "object": "group",
     "spots": ["badges", "menu", "settings"], "settings_at": IN_MENU, "fixed": ["name", "timing", "sliders", "mute"]},
    {"id": "theme.preset", "title": "Theme Editor: the loaded preset", "screen": "themes", "object": "preset",
     "spots": ["menu", "settings"], "settings_at": IN_MENU, "fixed": ["preset", "load", "new"]},
    {"id": "sequence.editor", "title": "Sequence editor", "screen": "themes", "object": "sequence",
     "spots": ["settings", "menu"], "fixed": ["name", "timing", "order", "presets", "footer"]},
    {"id": "sequence.step", "title": "Sequence editor: a step", "screen": "themes", "object": "step",
     "spots": ["menu"], "fixed": ["preset"]},
    {"id": "step.editor", "title": "Sequence step: Advanced", "screen": "themes", "object": "step",
     "spots": ["menu"], "fixed": ["tabs", "tracks", "footer"]},
    # --- speakers ---
    {"id": "speaker.row", "title": "Settings › Speakers: a speaker", "screen": "settings-speakers", "object": "speaker",
     "spots": ["badges", "columns", "menu", "settings"], "settings_at": IN_MENU,
     "fixed": ["name", "source", "address", "area", "offset", "test", "use"]},
    {"id": "speaker_group.row", "title": "Settings › Speaker Groups: a group", "screen": "settings-groups",
     "object": "speaker_group", "spots": ["badges", "columns", "menu"], "fixed": ["name", "includes", "speakers"]},
    {"id": "speaker_group.editor", "title": "Speaker group editor", "screen": "settings-groups",
     "object": "speaker_group", "spots": ["settings"], "fixed": ["name", "speakers", "footer"]},
    {"id": "area.row", "title": "Settings › Floors & Areas: an area", "screen": "settings-spaces", "object": "area",
     "spots": ["badges"], "fixed": ["name", "floor"]},
    # --- settings ---
    {"id": "plugin.row", "title": "Settings › Plugins: a plugin", "screen": "settings-plugins", "object": "plugin",
     "spots": ["badges", "menu", "plugin"], "fixed": ["name", "version", "description", "author", "on", "settings"]},
    {"id": "user.row", "title": "Settings › Users & devices: a user", "screen": "plugin-users", "object": "user",
     "spots": ["badges", "menu", "settings"], "settings_at": IN_MENU, "fixed": ["name", "role", "devices"]},
    {"id": "key.row", "title": "Settings › Keys: a key", "screen": "plugin-keys", "object": "key",
     "spots": ["menu"], "fixed": ["name", "abilities"]},
    {"id": "connection.row", "title": "Settings › Connection: a connection", "screen": "settings-connection",
     "object": "connection", "spots": ["menu"], "fixed": ["name", "summary", "status"]},
]

_BY_ID = {t["id"]: t for t in TARGETS}


def target(target_id: str) -> Optional[dict]:
    return _BY_ID.get(target_id)


def spot_contract(spot: str) -> dict:
    """A spot's contract, with every common limit (for the catalogue and the docs)."""
    c = dict(SPOT_TYPES[spot])
    c["kinds"] = list(c["kinds"])
    c["field_types"] = list(c["field_types"])
    c["bindings"] = list(c["bindings"])
    if spot == "pages":
        c["page"] = {k: list(v) if isinstance(v, tuple) else v for k, v in PAGE.items()}
    return c


def catalogue() -> dict:
    """GET /api/ui/targets: every target, its fixed parts and its open spots with their contracts."""
    targets = []
    for t in TARGETS:
        obj = t["object"]
        spots = {}
        for spot in t["spots"]:
            c = spot_contract(spot)
            if obj not in ATTRIBUTE_KINDS and "attribute" in c["bindings"]:
                c["bindings"] = [b for b in c["bindings"] if b != "attribute"]
            if spot == "settings" and t.get("settings_at"):
                c["position"] = f"one collapsed group per plugin, {t['settings_at']}"
            spots[spot] = c
        facts = dict(GLOBAL_FACTS)
        facts.update(FACTS.get(obj, {}) if obj else {})
        if obj in ATTRIBUTE_KINDS:
            facts["attr.<name>"] = "an attribute the plugin declares, on the object"
        targets.append({"id": t["id"], "title": t["title"], "screen": t["screen"], "object": obj,
                        "attributes": obj in ATTRIBUTE_KINDS, "fixed": list(t["fixed"]),
                        "spots": spots, "facts": facts})
    return {"version": CONTRACT_VERSION, "element_kinds": list(ELEMENT_KINDS), "field_types": list(FIELD_TYPES),
            "limits": dict(COMMON), "targets": targets}


# ------------------------------------------------------------------------------------------
# Validation
# ------------------------------------------------------------------------------------------

class Refused(ValueError):
    """An element outside its spot's contract (the message names the rule)."""


# A field type and the attribute / setting types it can show
_ATTR_TYPES = {"text": ("string",), "colour": ("string",), "number": ("number",), "slider": ("number",),
               "toggle": ("bool",), "select": ("enum",), "list": ("list",)}
_SETTING_TYPES = {"text": ("string", "url"), "colour": ("string",), "number": ("number",), "slider": ("number",),
                  "toggle": ("boolean",), "select": ("select",), "list": ()}
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _text(element: dict, key: str, limit: int, what: str, required: bool = False) -> Optional[str]:
    value = element.get(key)
    if value is None or value == "":
        if required:
            raise Refused(f"{what} is missing")
        return None
    if not isinstance(value, str):
        raise Refused(f"{what} must be text")
    if limit <= 0:
        raise Refused(f"{what} is not allowed here")
    if len(value) > limit:
        raise Refused(f"{what} is {len(value)} characters; the limit here is {limit}")
    return value


def _number(value, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Refused(f"{what} must be a number")
    if abs(value) > COMMON["number_abs_max"]:
        raise Refused(f"{what} is outside ±{COMMON['number_abs_max']}")
    return value


def _check_when(when: Any, obj: Optional[str]) -> Any:
    """Visibility conditions: {fact: value | [values] | {"not": value}}; a list means any of them."""
    if when is None:
        return None
    groups = when if isinstance(when, list) else [when]
    if not groups or len(groups) > COMMON["when_max"]:
        raise Refused(f"'when' holds 1 to {COMMON['when_max']} conditions")
    known = dict(GLOBAL_FACTS)
    known.update(FACTS.get(obj, {}) if obj else {})
    for group in groups:
        if not isinstance(group, dict) or not group or len(group) > COMMON["when_max"]:
            raise Refused(f"each 'when' condition is an object of 1 to {COMMON['when_max']} facts")
        for fact, test in group.items():
            if fact.startswith("attr."):
                if obj not in ATTRIBUTE_KINDS or not re.fullmatch(COMMON["attr_fact_pattern"], fact):
                    raise Refused(f"'when' fact '{fact}' is not available here")
            elif fact not in known:
                raise Refused(f"'when' fact '{fact}' is not one of: {', '.join(sorted(known)) or 'none'}")
            values = test.get("not") if isinstance(test, dict) and set(test) == {"not"} else test
            for v in values if isinstance(values, list) else [values]:
                if not isinstance(v, (str, int, float, bool)) or v is None:
                    raise Refused(f"'when' value for '{fact}' must be text, a number or true/false")
    return copy.deepcopy(when)


def _check_value_source(element: dict, ctx: dict) -> Optional[dict]:
    source = element.get("value")
    if source is None:
        return None
    if not isinstance(source, dict) or len(source) != 1 or next(iter(source)) not in VALUE_SOURCES:
        raise Refused("'value' is {\"attribute\": name} or {\"live\": true}")
    if "attribute" in source:
        _attribute_decl(source["attribute"], ctx)
        return {"attribute": source["attribute"]}
    if source["live"] is not True:
        raise Refused("'value' is {\"live\": true} for a value the plugin sends")
    return {"live": True}


def _attribute_decl(name: Any, ctx: dict) -> dict:
    obj = ctx["object"]
    if obj not in ATTRIBUTE_KINDS:
        raise Refused(f"a {obj or 'screen'} keeps no attributes: bind to an action instead")
    if not isinstance(name, str):
        raise Refused("the attribute name must be text")
    decl = (ctx["attributes"].get(obj) or {}).get(name)
    if decl is None:
        raise Refused(f"the plugin declares no attribute '{name}' for a {obj}")
    return decl


def _check_field(element: dict, rules: dict, ctx: dict, label_max: int) -> dict:
    kind = element.get("type")
    if kind not in FIELD_TYPES:
        raise Refused(f"field type {kind!r} is not one of {', '.join(FIELD_TYPES)}")
    if kind not in rules["field_types"]:
        raise Refused(f"field type '{kind}' is not allowed here (allowed: {', '.join(rules['field_types'])})")
    out = {"type": kind}
    out["label"] = _text(element, "label", label_max, "the label", required=True)
    for key, limit in (("help", COMMON["help_max"]), ("unit", COMMON["unit_max"]),
                       ("placeholder", COMMON["placeholder_max"])):
        value = _text(element, key, limit, f"'{key}'")
        if value is not None:
            out[key] = value
    if element.get("compact"):
        out["compact"] = True
    bind = element.get("bind")
    if not isinstance(bind, dict) or len(bind) != 1 or next(iter(bind)) not in BINDINGS:
        raise Refused("'bind' is {\"attribute\": name}, {\"setting\": name} or {\"action\": id}")
    how, name = next(iter(bind.items()))
    allowed = [b for b in rules["bindings"] if b != "attribute" or ctx["object"] in ATTRIBUTE_KINDS]
    if how not in allowed:
        raise Refused(f"binding to {'an' if how[0] in 'aeiou' else 'a'} {how} is not allowed here "
                      f"(allowed: {', '.join(allowed) or 'none'})")
    out["bind"] = {how: name}
    decl = None
    if how == "attribute":
        decl = _attribute_decl(name, ctx)
        if decl["type"] not in _ATTR_TYPES[kind]:
            raise Refused(f"a {kind} field can't show the {decl['type']} attribute '{name}'")
        if kind == "list" and (decl.get("items") or {}).get("type") not in ("string", "number"):
            raise Refused(f"a list field shows a list of text or numbers; '{name}' holds something else")
        if kind == "list":
            out["items_type"] = decl["items"]["type"]
        if "default" in decl:
            out["default"] = copy.deepcopy(decl["default"])
        for key in ("min", "max", "integer", "max_length", "max_items"):
            if key in decl:
                out[key] = decl[key]
        if decl["type"] == "enum":
            out["options"] = copy.deepcopy(decl["options"])
    elif how == "setting":
        decl = (ctx["settings"] or {}).get(name)
        if decl is None:
            raise Refused(f"the plugin has no setting '{name}'")
        if decl.get("type") not in _SETTING_TYPES[kind]:
            raise Refused(f"a {kind} field can't show the {decl.get('type')} setting '{name}'")
        for key in ("default", "min", "max", "options"):
            if key in decl:
                out[key] = copy.deepcopy(decl[key])
    else:
        if not isinstance(name, str) or not re.fullmatch(COMMON["id_pattern"], name):
            raise Refused(f"the action id is not a plain id ({COMMON['id_words']})")
        if "default" in element:
            out["default"] = element["default"]
    # The element's range and options (an attribute's or setting's come first)
    if kind in ("number", "slider"):
        for key in ("min", "max", "step"):
            if key in element and key not in out:
                out[key] = _number(element[key], f"'{key}'")
        if kind == "slider":
            if "min" not in out or "max" not in out:
                raise Refused("a slider needs 'min' and 'max'")
            if out["max"] <= out["min"]:
                raise Refused("a slider's 'max' must be above its 'min'")
            step = out.get("step") or 1
            if step <= 0 or (out["max"] - out["min"]) / step > COMMON["slider_steps_max"]:
                raise Refused(f"a slider has at most {COMMON['slider_steps_max']} steps")
    if kind == "select":
        options = out.get("options", element.get("options"))
        if not isinstance(options, list) or not options:
            raise Refused("a select needs 'options'")
        if len(options) > COMMON["options_max"]:
            raise Refused(f"a select has {len(options)} options; the limit is {COMMON['options_max']}")
        clean = []
        for o in options:
            value = o.get("value") if isinstance(o, dict) else o
            label = o.get("label", value) if isinstance(o, dict) else o
            if not isinstance(value, (str, int, float, bool)):
                raise Refused("a select option is text, a number or true/false")
            if len(str(label)) > COMMON["option_label_max"]:
                raise Refused(f"option '{label}' is {len(str(label))} characters; the limit is "
                              f"{COMMON['option_label_max']}")
            clean.append({"value": value, "label": str(label)})
        out["options"] = clean
    if kind == "list":
        limit = min(out.get("max_items", COMMON["list_items_max"]), COMMON["list_items_max"])
        out["max_items"] = limit
    if kind == "colour" and "default" in out and not (isinstance(out["default"], str) and _COLOUR.match(out["default"])):
        raise Refused("a colour's default is #rrggbb")
    if kind == "text":
        out["max_length"] = min(out.get("max_length", COMMON["text_value_max"]), COMMON["text_value_max"])
    return out


def _check_element(element: Any, rules: dict, ctx: dict, kinds, in_page: bool = False) -> dict:
    if not isinstance(element, dict):
        raise Refused("an element must be an object")
    kind = element.get("kind")
    if kind not in ELEMENT_KINDS:
        raise Refused(f"kind {kind!r} is not one of {', '.join(ELEMENT_KINDS)}")
    if kind not in kinds:
        raise Refused(f"kind '{kind}' is not allowed here (allowed: {', '.join(kinds)})")
    out: dict = {"kind": kind}
    label_max = rules["label_max"]
    text_max = rules["text_max"]
    if kind == "field":
        out.update(_check_field(element, rules, ctx, label_max))
    elif kind == "action":
        out["label"] = _text(element, "label", label_max, "the label", required=True)
        confirm = _text(element, "confirm", COMMON["confirm_max"], "'confirm'")
        if confirm:
            out["confirm"] = confirm
        if element.get("style") not in (None, "secondary"):
            raise Refused("an action's style is 'secondary' (primary and destructive buttons are fixed parts)")
    elif kind in ("badge", "column", "info"):
        if kind == "column":
            out["label"] = _text(element, "label", label_max, "the heading", required=True)
        elif element.get("label") is not None:
            out["label"] = _text(element, "label", label_max, "the label")
        source = _check_value_source(element, ctx)
        text = _text(element, "text", text_max, "the text", required=source is None)
        if source:
            out["value"] = source
        if text is not None:
            out["text"] = text
        state = element.get("state")
        if kind == "badge" and state is not None:
            if state not in STATES:
                raise Refused(f"a badge's state is one of {', '.join(STATES)}")
            out["state"] = state
        if kind == "info" and element.get("style") is not None:
            if element["style"] not in INFO_STYLES:
                raise Refused(f"an info line's style is one of {', '.join(INFO_STYLES)}")
            out["style"] = element["style"]
    elif kind == "group":
        out["label"] = _text(element, "label", label_max, "the label", required=True)
        fields = element.get("fields")
        if not isinstance(fields, list) or not fields:
            raise Refused("a group needs 'fields'")
        if len(fields) > rules["max_fields_per_item"]:
            raise Refused(f"a group holds {len(fields)} fields; the limit here is {rules['max_fields_per_item']}")
        out["fields"] = []
        for i, f in enumerate(fields):
            if not isinstance(f, dict):
                raise Refused(f"field {i + 1} of the group must be an object")
            fid = f.get("id")
            if not isinstance(fid, str) or not re.fullmatch(COMMON["id_pattern"], fid):
                raise Refused(f"field {i + 1} of the group needs a plain 'id'")
            try:
                out["fields"].append({"id": fid, **_check_field(f, rules, ctx, label_max)})
            except Refused as e:
                raise Refused(f"field '{fid}': {e}")
    elif kind == "page":
        out.update(_check_page(element, ctx))
    for key in ("help",):
        value = _text(element, key, COMMON["help_max"], f"'{key}'")
        if value is not None:
            out[key] = value
    when = _check_when(element.get("when"), ctx["object"])
    if when is not None:
        out["when"] = when
    return out


def _check_page(element: dict, ctx: dict) -> dict:
    out = {"title": _text(element, "title", PAGE["title_max"], "the title", required=True)}
    icon = element.get("icon")
    if icon is not None:
        from sonorium.plugins.extensions import check_icon
        out["icon"] = check_icon(icon)
    sections = element.get("sections")
    if not isinstance(sections, list) or not sections:
        raise Refused("a page needs 'sections'")
    if len(sections) > PAGE["sections_max"]:
        raise Refused(f"a page has {len(sections)} sections; the limit is {PAGE['sections_max']}")
    page_rules = dict(PAGE)
    page_ctx = {**ctx, "object": None}
    out["sections"] = []
    seen = set()
    for n, section in enumerate(sections, 1):
        if not isinstance(section, dict):
            raise Refused(f"section {n} must be an object")
        title = _text(section, "title", PAGE["section_title_max"], f"section {n}'s title")
        elements = section.get("elements")
        if not isinstance(elements, list) or not elements:
            raise Refused(f"section {n} needs 'elements'")
        if len(elements) > PAGE["elements_per_section_max"]:
            raise Refused(f"section {n} has {len(elements)} elements; the limit is {PAGE['elements_per_section_max']}")
        clean = []
        for item in elements:
            eid = item.get("id") if isinstance(item, dict) else None
            if not isinstance(eid, str) or not re.fullmatch(COMMON["id_pattern"], eid) or eid in seen:
                raise Refused(f"section {n}: every element needs a plain 'id' of its own")
            seen.add(eid)
            try:
                clean.append({"id": eid, **_check_element(item, page_rules, page_ctx, PAGE["kinds"], in_page=True)})
            except Refused as e:
                raise Refused(f"section {n}, element '{eid}': {e}")
        out["sections"].append({"title": title, "elements": clean})
    return out


def validate(plugin_id: str, raw: Any, *, attributes: Optional[dict] = None, settings: Optional[dict] = None) -> tuple:
    """
    A plugin's contributions checked against the contracts:
    (elements, warnings). Each element as drawn: {plugin, target, spot, id,
    kind, ...}. Each warning names the spot and the rule broken.

    attributes: the plugin's declared attributes ({class: {name: field}})
    settings: its settings schema ({name: field})
    """
    warnings: list[str] = []
    if raw is None or raw == []:
        return [], warnings
    if not isinstance(raw, list):
        return [], ["ui: contributions must be a list of elements"]
    if len(raw) > COMMON["elements_per_plugin_max"]:
        return [], [f"ui: {len(raw)} elements; a plugin may add at most {COMMON['elements_per_plugin_max']}"]
    by_spot: dict[tuple, list] = {}
    seen_ids: set = set()
    for n, element in enumerate(raw, 1):
        where = f"element {n}"
        if not isinstance(element, dict):
            warnings.append(f"{where}: must be an object; refused")
            continue
        eid = element.get("id")
        tid, spot = element.get("target"), element.get("spot")
        where = f"{tid}/{spot}" if isinstance(tid, str) and isinstance(spot, str) else where
        if not isinstance(eid, str) or not re.fullmatch(COMMON["id_pattern"], eid):
            warnings.append(f"{where}: element {n} needs a plain 'id' ({COMMON['id_words']}); refused")
            continue
        if eid in seen_ids:
            warnings.append(f"{where}: id '{eid}' is used twice; refused")
            continue
        seen_ids.add(eid)
        t = target(tid) if isinstance(tid, str) else None
        if t is None:
            warnings.append(f"{where}: '{eid}': there is no target {tid!r} (GET /api/ui/targets lists them); refused")
            continue
        if spot not in t["spots"]:
            if spot in t["fixed"]:
                warnings.append(f"{where}: '{eid}': '{spot}' is a fixed part of {tid}; plugins only add to its "
                                f"open spots ({', '.join(t['spots'])}); refused")
            else:
                warnings.append(f"{where}: '{eid}': {tid} has no spot {spot!r} (open spots: "
                                f"{', '.join(t['spots'])}); refused")
            continue
        by_spot.setdefault((tid, spot), []).append((eid, element))
    elements = []
    for (tid, spot), items in by_spot.items():
        t = target(tid)
        rules = SPOT_TYPES[spot]
        where = f"{tid}/{spot}"
        if len(items) > rules["max_per_plugin"]:
            warnings.append(f"{where}: {len(items)} elements; a plugin may add at most {rules['max_per_plugin']} "
                            f"here; all {len(items)} refused")
            continue
        ctx = {"object": t["object"], "attributes": attributes or {}, "settings": settings or {}}
        for eid, element in items:
            try:
                clean = _check_element(element, rules, ctx, rules["kinds"])
            except Refused as e:
                warnings.append(f"{where}: '{eid}': {e}; refused")
                continue
            elements.append({"plugin": plugin_id, "target": tid, "spot": spot, "id": eid, **clean})
    return elements, warnings
