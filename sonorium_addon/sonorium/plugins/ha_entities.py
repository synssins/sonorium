"""
Home Assistant entities a plugin declares, published by Sonorium under its own
device through MQTT discovery, so a plugin needs no Home Assistant code.
Declared as data (manifest "ha_entities", or get_ha_entities()):

    [{"id": "lights", "type": "switch", "name": "Storm lights", "icon": "mdi:weather-lightning",
      "bind": {"setting": "enabled"}},                       # a boolean setting of the plugin
     {"id": "flash", "type": "button", "name": "Flash now", "bind": {"action": "flash"}},
     {"id": "distance", "type": "number", "name": "Storm distance", "min": 0, "max": 5, "step": 0.5,
      "unit": "km", "bind": {"setting": "distance"}},
     {"id": "mode", "type": "select", "name": "Light mode", "options": ["mood", "storm"],
      "bind": {"action": "mode"}},                            # the plugin keeps the value (set_state)
     {"id": "online", "type": "sensor", "name": "Strips online", "bind": {"live": true}}]

Types: switch, select, number, sensor, button. Bindings:
    setting  one of the plugin's settings: Home Assistant shows it and changes
             it (checked against the settings schema, saved, on_settings_changed)
    action   the plugin's ha_action(entity_id, value) is called (a button: value
             None); its state is what the plugin last set (set_state)
    live     a value the plugin sets (set_state); read-only in Home Assistant

Anything outside this contract is refused and listed on the plugin's row.
The entities appear while the plugin is on and MQTT is connected, and are
removed when it is switched off. Needs the permission "ha.entities".
Every limit is in LIMITS.
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Optional

from sonorium.obs import logger

TYPES = ("switch", "select", "number", "sensor", "button")
LIMITS = {"entities_per_plugin": 20, "name_max": 40, "options_max": 30, "option_max": 40, "unit_max": 12,
          "state_max": 200}
ENTITY_ID = re.compile(r"^[a-z0-9][a-z0-9_]{0,39}$")
ICON = re.compile(r"^mdi:[a-z0-9-]{1,60}$")
SETTING_TYPES = {"switch": ("boolean",), "select": ("select",), "number": ("number",), "sensor": None}


def normalize_entities(decls, settings_schema: dict) -> tuple[list, list]:
    """(entities as kept, problems)."""
    if not decls:
        return [], []
    if not isinstance(decls, list):
        return [], ["'ha_entities' must be a list"]
    kept, problems, seen = [], [], set()
    for decl in decls:
        if len(kept) >= LIMITS["entities_per_plugin"]:
            problems.append(f"Home Assistant: at most {LIMITS['entities_per_plugin']} entities")
            break
        if not isinstance(decl, dict):
            problems.append("Home Assistant: an entity is an object")
            continue
        eid = decl.get("id")
        where = f"Home Assistant entity {eid!r}"
        if not isinstance(eid, str) or not ENTITY_ID.match(eid) or eid in seen:
            problems.append(f"{where}: an id of a-z, 0-9 and _, once")
            continue
        kind = decl.get("type")
        if kind not in TYPES:
            problems.append(f"{where}: type is one of {', '.join(TYPES)}")
            continue
        name = decl.get("name")
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > LIMITS["name_max"]:
            problems.append(f"{where}: a name of 1 to {LIMITS['name_max']} characters")
            continue
        bind = decl.get("bind")
        if not isinstance(bind, dict) or len(bind) != 1:
            problems.append(f"{where}: bind to one of setting, action or live")
            continue
        (how, what), = bind.items()
        entity = {"id": eid, "type": kind, "name": name.strip(), "bind": how, "target": what}
        if how == "setting":
            field = (settings_schema or {}).get(what) if isinstance(what, str) else None
            allowed = SETTING_TYPES.get(kind)
            if field is None or allowed is None or field.get("type") not in allowed:
                problems.append(f"{where}: a {kind} shows a setting of type {', '.join(allowed or ['-'])}")
                continue
            if kind == "select":
                entity["options"] = [str(o.get("value") if isinstance(o, dict) else o) for o in field.get("options") or []]
            if kind == "number":
                entity.update({k: field[k] for k in ("min", "max") if k in field})
        elif how == "action":
            if not isinstance(what, str) or not ENTITY_ID.match(what) or kind == "sensor":
                problems.append(f"{where}: an action id (not for a sensor)")
                continue
        elif how == "live":
            if what is not True or kind == "button":
                problems.append(f"{where}: live is true (not for a button)")
                continue
        else:
            problems.append(f"{where}: bind to one of setting, action or live")
            continue
        if kind == "select" and "options" not in entity:
            options = decl.get("options")
            if not isinstance(options, list) or not 0 < len(options) <= LIMITS["options_max"] or \
                    not all(isinstance(o, str) and 0 < len(o) <= LIMITS["option_max"] for o in options):
                problems.append(f"{where}: options, 1 to {LIMITS['options_max']} texts")
                continue
            entity["options"] = list(options)
        if kind == "number":
            bad = [k for k in ("min", "max", "step") if k in decl
                   and (isinstance(decl[k], bool) or not isinstance(decl[k], (int, float)))]
            if bad:
                problems.append(f"{where}: {bad[0]} is a number")
                continue
            entity.update({k: decl[k] for k in ("min", "max", "step") if k in decl})
            entity.setdefault("min", 0)
            entity.setdefault("max", 100)
            if entity["min"] >= entity["max"]:
                problems.append(f"{where}: min below max")
                continue
        unit = decl.get("unit")
        if unit is not None:
            if not isinstance(unit, str) or len(unit) > LIMITS["unit_max"]:
                problems.append(f"{where}: a unit of up to {LIMITS['unit_max']} characters")
                continue
            entity["unit"] = unit
        icon = decl.get("icon")
        if icon is not None:
            if not isinstance(icon, str) or not ICON.match(icon):
                problems.append(f"{where}: an icon like mdi:lightbulb")
                continue
            entity["icon"] = icon
        seen.add(eid)
        kept.append(entity)
    return kept, problems


class PluginEntities:
    """mqtt: the plugins' MQTT router (plugins/mqtt.py); plugin_of(id); update_settings(id, values) (async)."""

    def __init__(self, mqtt, plugin_of=None, update_settings=None):
        self.mqtt = mqtt
        self.plugin_of = plugin_of or (lambda pid: None)
        self.update_settings = update_settings
        self._entities: dict[str, list] = {}   # plugin id -> entities published (while on)
        self._states: dict[tuple, Any] = {}    # (plugin id, entity id) -> state set by the plugin
        self.problems: dict[str, list] = {}

    def _owner(self, plugin_id: str) -> str:
        return f"ha:{plugin_id}"

    def _object(self, plugin_id: str, eid: str) -> str:
        return f"sonorium_plugin_{plugin_id}_{eid}".replace("-", "_")

    def _topic(self, plugin_id: str, eid: str, leaf: str) -> str:
        return f"{self.mqtt.prefix}/plugin/{plugin_id}/{eid}/{leaf}"

    def _config_topic(self, plugin_id: str, entity: dict) -> str:
        return f"homeassistant/{entity['type']}/{self._object(plugin_id, entity['id'])}/config"

    def declare(self, plugin, allowed: bool) -> list:
        """The plugin's entities as it is switched on (allowed: its permissions let it). Returns the problems."""
        try:
            decls = plugin.get_ha_entities()
        except Exception as e:
            decls, problems = [], [f"get_ha_entities() failed: {e}"]
        else:
            problems = []
        if decls and not allowed:
            self._entities.pop(plugin.id, None)
            self.problems[plugin.id] = ["Home Assistant entities: not in its permissions (ha.entities)"]
            return self.problems[plugin.id]
        try:
            schema = plugin.settings_schema()
        except Exception:
            schema = {}
        entities, more = normalize_entities(decls, schema)
        problems += more
        for p in problems:
            logger.warning(f"Plugin {plugin.id}: {p}")
        self.problems[plugin.id] = problems
        if entities:
            self._entities[plugin.id] = entities
        else:
            self._entities.pop(plugin.id, None)
        return problems

    def entities(self, plugin_id: Optional[str] = None) -> list:
        if plugin_id is not None:
            return [dict(e) for e in self._entities.get(plugin_id, [])]
        return [dict(e, plugin_id=pid) for pid, es in self._entities.items() for e in es]

    async def publish(self, plugin_id: str) -> int:
        """Discovery configs, command subscriptions and states of one plugin's entities. Returns how many."""
        entities = self._entities.get(plugin_id) or []
        if not entities or not self.mqtt.available:
            return 0
        plugin = self.plugin_of(plugin_id)
        name = getattr(plugin, "name", None) or plugin_id
        self.mqtt.unsubscribe(self._owner(plugin_id))
        for entity in entities:
            config = {"name": entity["name"], "unique_id": self._object(plugin_id, entity["id"]),
                      "object_id": self._object(plugin_id, entity["id"]), "device": dict(self.mqtt.device_info)}
            if entity["type"] != "button":
                config["state_topic"] = self._topic(plugin_id, entity["id"], "state")
            if entity["type"] != "sensor" and entity["bind"] != "live":
                config["command_topic"] = self._topic(plugin_id, entity["id"], "set")
                self.mqtt.subscribe(self._owner(plugin_id), config["command_topic"],
                                    lambda topic, payload, pid=plugin_id, e=entity: self.command(pid, e, payload))
            if entity["type"] == "switch":
                config.update({"payload_on": "ON", "payload_off": "OFF"})
            if entity["type"] == "select":
                config["options"] = list(entity["options"])
            if entity["type"] == "number":
                config.update({k: entity[k] for k in ("min", "max", "step") if k in entity})
                config["mode"] = "box"
            if "unit" in entity:
                config["unit_of_measurement"] = entity["unit"]
            if "icon" in entity:
                config["icon"] = entity["icon"]
            config["origin"] = {"name": f"Sonorium: {name}"}
            await self.mqtt.publish(self._config_topic(plugin_id, entity), json.dumps(config), retain=True)
        await self.publish_states(plugin_id)
        return len(entities)

    def _state(self, plugin_id: str, entity: dict):
        if entity["bind"] == "setting":
            plugin = self.plugin_of(plugin_id)
            return (getattr(plugin, "settings", None) or {}).get(entity["target"])
        return self._states.get((plugin_id, entity["id"]))

    @staticmethod
    def _payload(entity: dict, value) -> Optional[str]:
        if value is None:
            return None
        if entity["type"] == "switch":
            return "ON" if value else "OFF"
        return str(value)[:LIMITS["state_max"]]

    async def publish_states(self, plugin_id: str) -> None:
        for entity in self._entities.get(plugin_id) or []:
            if entity["type"] == "button":
                continue
            payload = self._payload(entity, self._state(plugin_id, entity))
            if payload is not None:
                await self.mqtt.publish(self._topic(plugin_id, entity["id"], "state"), payload, retain=True)

    async def set_state(self, plugin_id: str, eid: str, value) -> None:
        """The plugin's value for an action- or live-bound entity."""
        entity = next((e for e in self._entities.get(plugin_id) or [] if e["id"] == eid), None)
        if entity is None:
            raise LookupError(f"No Home Assistant entity '{eid}'")
        if entity["bind"] == "setting":
            raise ValueError(f"'{eid}' shows a setting: change the setting instead")
        self._states[(plugin_id, eid)] = value
        payload = self._payload(entity, value)
        if payload is not None and self.mqtt.available:
            await self.mqtt.publish(self._topic(plugin_id, eid, "state"), payload, retain=True)

    async def command(self, plugin_id: str, entity: dict, payload: str) -> None:
        """Home Assistant changed an entity: the setting, or the plugin's ha_action."""
        text = (payload or "").strip()
        if entity["type"] == "switch":
            value: Any = text.upper() == "ON"
        elif entity["type"] == "number":
            try:
                value = float(text)
            except ValueError:
                return
            if value.is_integer():
                value = int(value)
        elif entity["type"] == "select":
            if text not in entity["options"]:
                return
            value = text
        else:
            value = None
        plugin = self.plugin_of(plugin_id)
        if plugin is None or not plugin.enabled:
            return
        if entity["bind"] == "setting":
            if self.update_settings is not None:
                try:
                    await self.update_settings(plugin_id, {entity["target"]: value})
                except Exception as e:
                    logger.warning(f"Plugin {plugin_id}: Home Assistant set {entity['id']}: {e}")
                await self.publish_states(plugin_id)
            return
        try:
            result = await asyncio.wait_for(plugin.ha_action(entity["target"], value), 30)
        except Exception as e:
            logger.warning(f"Plugin {plugin_id}: ha_action({entity['target']}) failed: {e}")
            return
        if isinstance(result, dict) and "value" in result and entity["type"] != "button":
            await self.set_state(plugin_id, entity["id"], result["value"])

    async def remove(self, plugin_id: str) -> None:
        """The plugin is off: its entities leave Home Assistant."""
        entities = self._entities.pop(plugin_id, None) or []
        self.mqtt.unsubscribe(self._owner(plugin_id))
        if self.mqtt.available:
            for entity in entities:
                await self.mqtt.publish(self._config_topic(plugin_id, entity), "", retain=True)
