"""
Plugin settings: the schema format, defaults and validation.

A plugin describes its persistent settings with get_settings_schema() (or
"settings_schema" in its manifest.json): setting name -> field, using the
same vocabulary as the fields of get_ui_schema():

    {
        "hash_check_duplicates": {
            "type": "boolean",          # string, url, number, boolean, select
            "label": "Use hash to detect duplicate files",
            "help": "More thorough but slower",   # optional
            "default": True,            # optional, used while the setting isn't set
        },
        "quality": {
            "type": "select",
            "label": "Quality",
            "options": [{"value": "high", "label": "High"}, {"value": "low", "label": "Low"}],
            "default": "high",
        },
        "max_tracks": {"type": "number", "label": "Max tracks", "min": 1, "max": 8, "default": 8},
        "api_url": {"type": "url", "label": "API URL", "required": False, "placeholder": "https://..."},
    }

Types:
  string   text
  url      text that is empty or an http(s) URL
  number   int or float (not a boolean); optional "min"/"max"
  boolean  true/false
  select   one of "options" (each {"value", "label"} or a plain value)
"required": True refuses an empty string/url/select value.

PUT /api/plugins/{id}/settings validates the values sent against the schema
(validate_settings): unknown names and wrong types are refused with one
message listing every problem. The stored settings are the plugin's effective
settings: defaults, overridden by the stored values that are still valid.
A plugin without a schema keeps whatever it stores.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from sonorium.obs import logger

FIELD_TYPES = ("string", "url", "number", "boolean", "select")


class SettingsError(ValueError):
    """Settings that don't match the plugin's schema."""


def _option_values(field: dict) -> list:
    values = []
    for option in field.get("options") or []:
        values.append(option.get("value") if isinstance(option, dict) else option)
    return values


def normalize_schema(schema: Any, plugin_id: str = "") -> dict:
    """The schema with only well-formed fields (others are logged and left out)."""
    if not isinstance(schema, dict):
        return {}
    fields = {}
    for name, field in schema.items():
        if not isinstance(name, str) or not name or not isinstance(field, dict):
            logger.warning(f"Plugin {plugin_id}: settings field {name!r} is not a field, ignored")
            continue
        if field.get("type") not in FIELD_TYPES:
            logger.warning(f"Plugin {plugin_id}: settings field '{name}' has unknown type {field.get('type')!r}, ignored")
            continue
        if field["type"] == "select" and not _option_values(field):
            logger.warning(f"Plugin {plugin_id}: select setting '{name}' has no options, ignored")
            continue
        fields[name] = dict(field)
    return fields


def check_value(name: str, field: dict, value: Any) -> Any:
    """The value if it fits the field (numbers as given, strings stripped); SettingsError if not."""
    kind = field["type"]
    if kind in ("string", "url"):
        if not isinstance(value, str):
            raise SettingsError(f"'{name}' must be text")
        value = value.strip()
        if kind == "url" and value:
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise SettingsError(f"'{name}' must be an http(s) URL")
    elif kind == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"'{name}' must be a number")
        if "min" in field and value < field["min"]:
            raise SettingsError(f"'{name}' must be at least {field['min']}")
        if "max" in field and value > field["max"]:
            raise SettingsError(f"'{name}' must be at most {field['max']}")
    elif kind == "boolean":
        if not isinstance(value, bool):
            raise SettingsError(f"'{name}' must be true or false")
    elif kind == "select":
        if value not in _option_values(field) and not (value == "" and not field.get("required")):
            raise SettingsError(f"'{name}' must be one of {', '.join(repr(v) for v in _option_values(field))}")
    if field.get("required") and value in ("", None):
        raise SettingsError(f"'{name}' is required")
    return value


def defaults(schema: dict) -> dict:
    """Each field's default (fields without one are left out)."""
    return {name: field["default"] for name, field in schema.items() if "default" in field}


def effective_settings(schema: dict, stored: dict) -> dict:
    """
    Defaults overridden by the stored values that still fit the schema. A
    stored value that doesn't (an old setting, a changed type) is left out.
    Without a schema the stored values are used as they are.
    """
    stored = dict(stored or {})
    if not schema:
        return stored
    result = defaults(schema)
    for name, value in stored.items():
        field = schema.get(name)
        if field is None:
            continue
        try:
            result[name] = check_value(name, field, value)
        except SettingsError:
            logger.debug(f"Stored setting '{name}' no longer fits its field, using the default")
    return result


def validate_settings(schema: dict, values: Any) -> dict:
    """
    The values sent for an update, checked against the schema. SettingsError
    (listing every problem) for a name the schema doesn't have or a value that
    doesn't fit. A plugin without a schema has no settings to change.
    """
    if not isinstance(values, dict):
        raise SettingsError("settings must be an object")
    if not schema and values:
        raise SettingsError("This plugin has no settings")
    clean, problems = {}, []
    for name, value in values.items():
        field = schema.get(name)
        if field is None:
            problems.append(f"unknown setting '{name}'")
            continue
        try:
            clean[name] = check_value(name, field, value)
        except SettingsError as e:
            problems.append(str(e))
    if problems:
        raise SettingsError("; ".join(problems))
    return clean
