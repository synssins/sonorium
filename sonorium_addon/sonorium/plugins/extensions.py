"""
Extension points: places in Sonorium's UI a plugin can fill, described as
data the UI renders in its own style. A plugin never sends HTML: every
plugin page and connection looks and behaves like the rest of Sonorium.

Pages (a left-menu entry each)
    get_pages() -> [{"id", "title", "icon"}]
    async get_page(page_id) -> page schema (below), built fresh on each request
    async page_action(page_id, action, data) -> {"success", "message", "refresh"}

Connections (a section in Settings > Connections each)
    get_connections() -> [{"id", "title", "description", "icon", "fields", "test"}]
    async test_connection(connection_id, values) -> {"success", "message"}
    connection_status(connection_id) -> {"state", "message"}
    connection_values(connection_id) (BasePlugin) -> saved values, secrets included

Icons: a name ("alarm", "mdi:alarm-clock") or a small inline SVG (no scripts,
event attributes, links or foreignObject; at most 8 KB).

Fields: the settings vocabulary (string, url, number, boolean, select; see
settings_schema.py) plus textarea, time (HH:MM), date (YYYY-MM-DD), datetime
(ISO), multiselect (a list of option values) and, in connections only,
secret (write-only: saved apart, never sent back, shown as "<name>_set").

A page schema:
    {"title": "Alarms", "refresh": 30,          # refresh: seconds, optional
     "sections": [{"id": "list", "title": "Alarms", "description": "...",
                   "items": [ITEM, ...]}]}
ITEM is one of:
    {"type": "text", "text": "Plain text", "style": "normal|muted|success|warning|error"}
    {"type": "field", "name": "time", "field": {"type": "time", "label": "Time"}, "value": "07:30"}
    {"type": "table", "id": "alarms", "columns": [{"key": "time", "label": "Time", "format": "text"}],
     "rows": [{"id": "a1", "time": "07:30"}], "empty": "No alarms yet",
     "row_actions": [{"id": "delete", "label": "Delete", "style": "danger", "confirm": "Delete it?"}]}
    {"type": "button", "action": "add", "label": "Add", "style": "primary|secondary|danger",
     "confirm": "optional question"}
    {"type": "status", "state": "ok|warning|error|off|unknown", "text": "Connected"}
A button (or a row action) calls page_action(page_id, action, {"values": {field
values of the page}, "row": row id or null}).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Optional

from sonorium.obs import logger
from sonorium.plugins.settings_schema import FIELD_TYPES, SettingsError, check_value, _option_values

EXTRA_FIELD_TYPES = ("textarea", "time", "date", "datetime", "multiselect")
SECRET = "secret"
PAGE_FIELD_TYPES = FIELD_TYPES + EXTRA_FIELD_TYPES
CONNECTION_FIELD_TYPES = PAGE_FIELD_TYPES + (SECRET,)

EXTENSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ICON_NAME = re.compile(r"^[a-z0-9][a-z0-9:_-]{0,63}$")
MAX_SVG_BYTES = 8 * 1024
_SVG_FORBIDDEN = re.compile(r"<\s*script|<\s*foreignobject|\son[a-z]+\s*=|javascript:|href\s*=|<\s*iframe|"
                            r"<\s*!entity|<\s*!doctype|url\s*\(", re.IGNORECASE)
TEXT_STYLES = ("normal", "muted", "success", "warning", "error")
BUTTON_STYLES = ("primary", "secondary", "danger")
STATUS_STATES = ("ok", "warning", "error", "off", "unknown")
CONNECTION_STATES = ("connected", "disconnected", "error", "not_configured", "unknown")
COLUMN_FORMATS = ("text", "number", "boolean", "datetime", "status")
MAX_SECTIONS, MAX_ITEMS, MAX_ROWS, MAX_COLUMNS, MAX_TEXT = 50, 200, 1000, 20, 4000


class ExtensionError(ValueError):
    """A declaration or values that don't fit the schema."""


def _text(value, limit: int = 200) -> str:
    return ("" if value is None else str(value))[:limit]


def check_icon(icon) -> Optional[dict]:
    """{"type": "name"|"svg", "value"}; None for no icon or one that isn't allowed (logged)."""
    if not icon:
        return None
    if isinstance(icon, str) and ICON_NAME.match(icon):
        return {"type": "name", "value": icon}
    if isinstance(icon, str) and icon.lstrip().lower().startswith("<svg"):
        if len(icon.encode("utf-8")) > MAX_SVG_BYTES:
            logger.warning("Plugin icon ignored: the SVG is larger than 8 KB")
            return None
        if _SVG_FORBIDDEN.search(icon):
            logger.warning("Plugin icon ignored: the SVG has scripts, links or event attributes")
            return None
        return {"type": "svg", "value": icon.strip()}
    logger.warning(f"Plugin icon ignored: {str(icon)[:40]!r} is neither an icon name nor an SVG")
    return None


# --- Fields ---

def normalize_field(name: str, field: Any, allowed=PAGE_FIELD_TYPES) -> Optional[dict]:
    if not isinstance(name, str) or not EXTENSION_ID.match(name) or not isinstance(field, dict):
        return None
    if field.get("type") not in allowed:
        return None
    if field["type"] in ("select", "multiselect") and not _option_values(field):
        return None
    clean = {k: v for k, v in field.items() if k in (
        "type", "label", "help", "default", "options", "min", "max", "required", "placeholder")}
    clean["label"] = _text(clean.get("label") or name)
    return clean


def normalize_fields(fields: Any, allowed=PAGE_FIELD_TYPES, owner: str = "") -> dict:
    result = {}
    for name, field in (fields or {}).items() if isinstance(fields, dict) else []:
        clean = normalize_field(name, field, allowed)
        if clean is None:
            logger.warning(f"{owner}: field {name!r} is not valid, ignored")
            continue
        result[name] = clean
    return result


def check_field_value(name: str, field: dict, value: Any) -> Any:
    """The value if it fits the field; ExtensionError if not."""
    kind = field["type"]
    try:
        if kind in FIELD_TYPES:
            return check_value(name, field, value)
    except SettingsError as e:
        raise ExtensionError(str(e))
    empty_ok = value == "" and not field.get("required")
    if kind in ("textarea", SECRET):
        if not isinstance(value, str):
            raise ExtensionError(f"'{name}' must be text")
        if len(value) > 20000:
            raise ExtensionError(f"'{name}' is too long")
    elif kind == "time":
        if not empty_ok and (not isinstance(value, str)
                             or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value.strip())):
            raise ExtensionError(f"'{name}' must be a time (HH:MM)")
        value = value.strip()
    elif kind == "date":
        try:
            if not empty_ok:
                date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ExtensionError(f"'{name}' must be a date (YYYY-MM-DD)")
    elif kind == "datetime":
        try:
            if not empty_ok:
                datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise ExtensionError(f"'{name}' must be a date and time (ISO 8601)")
    elif kind == "multiselect":
        options = _option_values(field)
        if not isinstance(value, list) or any(v not in options for v in value):
            raise ExtensionError(f"'{name}' must be a list of: {', '.join(repr(v) for v in options)}")
        value = list(dict.fromkeys(value))
    if field.get("required") and value in ("", None, []):
        raise ExtensionError(f"'{name}' is required")
    return value


def check_values(fields: dict, values: Any) -> dict:
    """Values for some of the fields, checked (every problem listed at once)."""
    if not isinstance(values, dict):
        raise ExtensionError("values must be an object")
    clean, problems = {}, []
    for name, value in values.items():
        field = fields.get(name)
        if field is None:
            problems.append(f"unknown field '{name}'")
            continue
        try:
            clean[name] = check_field_value(name, field, value)
        except ExtensionError as e:
            problems.append(str(e))
    if problems:
        raise ExtensionError("; ".join(problems))
    return clean


# --- Pages ---

def normalize_page_decls(decls: Any, plugin_id: str) -> list[dict]:
    pages, seen = [], set()
    for decl in decls or []:
        if not isinstance(decl, dict) or not EXTENSION_ID.match(str(decl.get("id", ""))) or decl["id"] in seen:
            logger.warning(f"Plugin {plugin_id}: page {decl!r} is not valid, ignored")
            continue
        seen.add(decl["id"])
        pages.append({"id": decl["id"], "title": _text(decl.get("title") or decl["id"], 60),
                      "icon": check_icon(decl.get("icon")), "plugin_id": plugin_id,
                      "path": f"/api/plugins/{plugin_id}/pages/{decl['id']}"})
    return pages


def _normalize_button(item: Any, row: bool = False) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    action = item.get("id" if row else "action")
    if not isinstance(action, str) or not EXTENSION_ID.match(action):
        return None
    clean = {"label": _text(item.get("label") or action, 60),
             "style": item.get("style") if item.get("style") in BUTTON_STYLES else "secondary",
             "confirm": _text(item.get("confirm"), 300) or None}
    if row:
        return {"id": action, **clean}
    return {"type": "button", "action": action, **clean}


def _normalize_table(item: dict) -> Optional[dict]:
    if not EXTENSION_ID.match(str(item.get("id", ""))):
        return None
    columns = []
    for col in (item.get("columns") or [])[:MAX_COLUMNS]:
        if isinstance(col, dict) and isinstance(col.get("key"), str) and col["key"]:
            columns.append({"key": col["key"][:64], "label": _text(col.get("label") or col["key"], 60),
                            "format": col.get("format") if col.get("format") in COLUMN_FORMATS else "text"})
    if not columns:
        return None
    keys = {c["key"] for c in columns} | {"id"}
    rows = []
    for row in (item.get("rows") or [])[:MAX_ROWS]:
        if isinstance(row, dict):
            rows.append({k: (v if isinstance(v, (int, float, bool)) or v is None else _text(v, 500))
                         for k, v in row.items() if k in keys})
    row_actions = [a for a in (_normalize_button(a, row=True) for a in (item.get("row_actions") or [])) if a]
    return {"type": "table", "id": item["id"], "columns": columns, "rows": rows,
            "empty": _text(item.get("empty"), 200), "row_actions": row_actions}


def _normalize_item(item: Any, owner: str) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    kind = item.get("type")
    if kind == "text":
        style = item.get("style") if item.get("style") in TEXT_STYLES else "normal"
        return {"type": "text", "text": _text(item.get("text"), MAX_TEXT), "style": style}
    if kind == "field":
        name = item.get("name")
        field = normalize_field(name, item.get("field"))
        if field is None:
            logger.debug(f"{owner}: page field {name!r} is not valid, ignored")
            return None
        clean = {"type": "field", "name": name, "field": field}
        if "value" in item:
            clean["value"] = item["value"]
        return clean
    if kind == "table":
        return _normalize_table(item)
    if kind == "button":
        return _normalize_button(item)
    if kind == "status":
        state = item.get("state") if item.get("state") in STATUS_STATES else "unknown"
        return {"type": "status", "state": state, "text": _text(item.get("text"), 200)}
    logger.debug(f"{owner}: page item type {kind!r} is not known, ignored")
    return None


def normalize_page(page: Any, owner: str = "") -> dict:
    """A page schema with only well-formed parts (others dropped and logged)."""
    if not isinstance(page, dict):
        raise ExtensionError("A page must be an object")
    sections = []
    for section in (page.get("sections") or [])[:MAX_SECTIONS]:
        if not isinstance(section, dict):
            continue
        items = [i for i in (_normalize_item(item, owner) for item in (section.get("items") or [])[:MAX_ITEMS]) if i]
        sections.append({"id": _text(section.get("id"), 64) or None, "title": _text(section.get("title"), 120),
                         "description": _text(section.get("description"), MAX_TEXT), "items": items})
    refresh = page.get("refresh")
    if isinstance(refresh, bool) or not isinstance(refresh, (int, float)) or refresh < 5:
        refresh = None
    return {"title": _text(page.get("title"), 120), "refresh": refresh, "sections": sections}


def page_fields(page: dict) -> dict:
    """The fields of a (normalized) page: name -> field."""
    return {item["name"]: item["field"] for section in page["sections"] for item in section["items"]
            if item["type"] == "field"}


def page_actions(page: dict) -> set:
    """Every action a (normalized) page offers: buttons and row actions."""
    actions = set()
    for section in page["sections"]:
        for item in section["items"]:
            if item["type"] == "button":
                actions.add(item["action"])
            elif item["type"] == "table":
                actions.update(a["id"] for a in item["row_actions"])
    return actions


def normalize_result(result: Any) -> dict:
    if not isinstance(result, dict):
        return {"success": False, "message": "The plugin gave no result", "refresh": False}
    clean = {"success": bool(result.get("success")), "message": _text(result.get("message"), 1000),
             "refresh": bool(result.get("refresh", True))}
    if isinstance(result.get("data"), dict):
        clean["data"] = result["data"]
    return clean


# --- Connections ---

def normalize_connection_decls(decls: Any, plugin_id: str) -> list[dict]:
    connections, seen = [], set()
    for decl in decls or []:
        if not isinstance(decl, dict) or not EXTENSION_ID.match(str(decl.get("id", ""))) or decl["id"] in seen:
            logger.warning(f"Plugin {plugin_id}: connection {decl!r} is not valid, ignored")
            continue
        seen.add(decl["id"])
        connections.append({
            "id": decl["id"], "plugin_id": plugin_id,
            "title": _text(decl.get("title") or decl["id"], 60),
            "description": _text(decl.get("description"), MAX_TEXT),
            "icon": check_icon(decl.get("icon")),
            "fields": normalize_fields(decl.get("fields"), CONNECTION_FIELD_TYPES, f"Plugin {plugin_id}"),
            "test": bool(decl.get("test", False)),
            "path": f"/api/plugins/{plugin_id}/connections/{decl['id']}",
        })
    return connections


def secret_names(fields: dict) -> list[str]:
    return [name for name, field in fields.items() if field["type"] == SECRET]


class ConnectionValues:
    """
    Saved connection values: plain ones in plugin_framework.json
    ("connections"), secret ones in plugin_secrets.json ("connections").
    """

    def __init__(self, store):
        self.store = store

    def _section(self, file, plugin_id: str, connection_id: str, create: bool = False) -> dict:
        plugins = file.section("connections")
        if create:
            return plugins.setdefault(plugin_id, {}).setdefault(connection_id, {})
        return (plugins.get(plugin_id) or {}).get(connection_id) or {}

    def values(self, plugin_id: str, connection_id: str, fields: dict) -> dict:
        """Everything saved (defaults for the rest), secrets included: only for the plugin itself."""
        plain = self._section(self.store.main, plugin_id, connection_id)
        secret = self._section(self.store.secrets, plugin_id, connection_id)
        result = {name: field["default"] for name, field in fields.items()
                  if "default" in field and field["type"] != SECRET}
        for name, field in fields.items():
            source = secret if field["type"] == SECRET else plain
            if name in source:
                result[name] = source[name]
        return result

    def public(self, plugin_id: str, connection_id: str, fields: dict) -> dict:
        """What the API may show: plain values, and "<name>_set": true/false for each secret."""
        values = self.values(plugin_id, connection_id, fields)
        for name in secret_names(fields):
            values[f"{name}_set"] = bool(values.pop(name, ""))
        return values

    def merged(self, plugin_id: str, connection_id: str, fields: dict, values: dict) -> dict:
        """Saved values with unsaved ones on top (checked; empty secrets keep the saved ones): for a test."""
        secrets = set(secret_names(fields))
        given = {k: v for k, v in values.items() if not (k in secrets and v in ("", None))}
        return {**self.values(plugin_id, connection_id, fields), **check_values(fields, given)}

    def update(self, plugin_id: str, connection_id: str, fields: dict, values: dict,
               clear: Optional[list] = None) -> dict:
        """
        Save some values (all checked first). An empty or missing secret
        keeps the saved one, so the UI never sends secrets back; `clear`
        lists fields to remove (secrets included). Returns the public values.
        """
        secrets = set(secret_names(fields))
        given = {k: v for k, v in values.items() if not (k in secrets and v in ("", None))}
        clean = check_values(fields, given)
        if clear is not None and (not isinstance(clear, list) or any(n not in fields for n in clear)):
            raise ExtensionError("'clear' must list fields of the connection")
        plain = self._section(self.store.main, plugin_id, connection_id, create=True)
        secret = self._section(self.store.secrets, plugin_id, connection_id, create=True)
        for name, value in clean.items():
            (secret if name in secrets else plain)[name] = value
        for name in clear or []:
            (secret if name in secrets else plain).pop(name, None)
        self.store.main.save()
        self.store.secrets.save()
        return self.public(plugin_id, connection_id, fields)

    def forget_plugin(self, plugin_id: str) -> None:
        """Remove every saved value of a plugin (it was uninstalled)."""
        if self.store.main.section("connections").pop(plugin_id, None) is not None:
            self.store.main.save()
        if self.store.secrets.section("connections").pop(plugin_id, None) is not None:
            self.store.secrets.save()


def normalize_status(status: Any) -> dict:
    if not isinstance(status, dict):
        return {"state": "unknown", "message": ""}
    state = status.get("state") if status.get("state") in CONNECTION_STATES else "unknown"
    return {"state": state, "message": _text(status.get("message"), 300)}
