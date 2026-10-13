"""
A test plugin that adds to EVERY open spot of EVERY target in the catalogue
(GET /api/ui/targets): one element of each kind the spot's contract allows.
tests/test_ui_contributions.py loads it; the visual check runs it in a real
Sonorium. Its elements come from the catalogue itself, so a target added
later is covered without changing this file.
"""
from sonorium.plugins.base import BasePlugin
from sonorium.plugins import ui_contract

ATTRIBUTES = {
    "demo_on": {"type": "bool", "default": False, "label": "On"},
    "demo_level": {"type": "number", "min": 0, "max": 100, "default": 50, "label": "Level"},
    "demo_mode": {"type": "enum", "options": ["calm", "busy"], "default": "calm", "label": "Mode"},
    "demo_note": {"type": "string", "max_length": 40, "default": "", "label": "Note"},
    "demo_colour": {"type": "string", "pattern": "#[0-9a-fA-F]{6}", "default": "#06d6a0", "label": "Colour"},
    "demo_tags": {"type": "list", "items": {"type": "string"}, "max_items": 5, "default": [], "label": "Tags"},
}
SETTINGS = {
    "demo_toggle": {"type": "boolean", "label": "Toggle", "default": False},
    "demo_level": {"type": "number", "label": "Level", "min": 0, "max": 10, "default": 3},
    "demo_choice": {"type": "select", "label": "Choice", "options": ["one", "two"], "default": "one"},
    "demo_text": {"type": "string", "label": "Text", "default": ""},
}


def _field(eid, ftype, attr_ok, setting_ok, label=None):
    """One field of the type, bound to an attribute, a setting or an action, as the spot allows."""
    attr = {"text": "demo_note", "number": "demo_level", "slider": "demo_level", "toggle": "demo_on",
            "select": "demo_mode", "colour": "demo_colour", "list": "demo_tags"}[ftype]
    setting = {"text": "demo_text", "number": "demo_level", "slider": "demo_level", "toggle": "demo_toggle",
               "select": "demo_choice", "colour": None, "list": None}[ftype]
    f = {"id": eid, "kind": "field", "type": ftype, "label": label or ftype.capitalize()}
    if attr_ok:
        f["bind"] = {"attribute": attr}
    elif setting_ok and setting:
        f["bind"] = {"setting": setting}
    else:
        f["bind"] = {"action": "changed"}
        if ftype == "select":
            f["options"] = ["a", "b"]
            f["default"] = "a"
        if ftype == "colour":
            f["default"] = "#06d6a0"
    if ftype in ("number", "slider") and "attribute" not in f["bind"] and "setting" not in f["bind"]:
        f.update({"min": 0, "max": 10})
    if ftype == "slider" and "setting" in f["bind"]:
        f.update({"min": 0, "max": 10})
    return f


def everywhere() -> list:
    """One element of each allowed kind in every open spot, within every contract."""
    out = []
    for ti, t in enumerate(ui_contract.TARGETS):
        attrs = t["object"] in ui_contract.ATTRIBUTE_KINDS
        for spot in t["spots"]:
            rules = ui_contract.SPOT_TYPES[spot]
            base = f"t{ti}-{spot[:4]}"
            attr_ok = attrs and "attribute" in rules["bindings"]
            setting_ok = "setting" in rules["bindings"]
            items = []
            for kind in rules["kinds"]:
                eid = f"{base}-{kind}"
                if kind == "action":
                    items.append({"id": eid, "kind": "action", "label": "Every"})
                elif kind == "badge":
                    items.append({"id": eid, "kind": "badge", "text": "Every", "state": "ok"})
                elif kind == "column":
                    items.append({"id": eid, "kind": "column", "label": "Every", "value": {"live": True}, "text": "–"})
                elif kind == "info":
                    items.append({"id": eid, "kind": "info", "text": f"Every: {t['id']}"[:rules["text_max"]]})
                elif kind == "field":
                    types = rules["field_types"]
                    room = rules["max_per_plugin"] - (len(rules["kinds"]) - 1)
                    for n, ftype in enumerate(types[:max(1, room)]):
                        items.append(_field(f"{eid}{n}", ftype, attr_ok, setting_ok))
                elif kind == "group":
                    count = min(2, rules["max_fields_per_item"])
                    fields = [_field(f"{eid}-f{n}", ftype, attr_ok, setting_ok)
                              for n, ftype in enumerate((["colour", "list"] if spot == "settings"
                                                         else ["number", "toggle"])[:count])]
                    items.append({"id": eid, "kind": "group", "label": "Group", "fields": fields})
                elif kind == "page":
                    items.append({"id": eid, "kind": "page", "title": "Every page", "icon": "plugin", "sections": [
                        {"title": "Values", "elements": [
                            _field(f"{eid}-s0", "toggle", False, True),
                            _field(f"{eid}-s1", "number", False, True),
                            {"id": f"{eid}-s2", "kind": "info", "text": "A page drawn from data"},
                            {"id": f"{eid}-s3", "kind": "action", "label": "Run"}]}]})
            for item in items[:rules["max_per_plugin"]]:
                out.append({"target": t["id"], "spot": spot, **item})
    return out


class EverywherePlugin(BasePlugin):
    id = "ui_everywhere"
    name = "Everywhere"
    version = "1.0.0"
    description = "Adds one element of each kind to every open spot"
    author = "Tests"

    def get_attributes(self):
        return {cls: dict(ATTRIBUTES) for cls in ui_contract.ATTRIBUTE_KINDS}

    def get_settings_schema(self):
        return dict(SETTINGS)

    def get_ui(self):
        return everywhere()

    async def ui_action(self, element_id, ref, value=None):
        self.last_action = (element_id, ref, value)
        return {"success": True, "message": f"{element_id} on {(ref or {}).get('kind') or 'screen'}", "value": value}
