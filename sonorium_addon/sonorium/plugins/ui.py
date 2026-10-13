"""
What plugins add to the web UI's screens (plugins/ui_contract.py has the
targets, spots and contracts): each plugin's contributions, checked when it
registers them, and the live values it sends.

A plugin registers its contributions with get_ui() (default: the manifest's
"ui"), read when it is enabled, or later with self.ui.register(elements).
Elements outside their spot's contract are refused: logged once, and listed
as warnings on the plugin's row in Settings › Plugins (plugin.to_dict()
"ui_warnings"). A plugin switched off or uninstalled adds nothing.

Live values: self.ui.set_value(element_id, value, ref=None) keeps the value
(for every object, or one object: ref {"kind", "id", "theme"}) and publishes
"ui.value", which the web UI follows on the event stream.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from sonorium.obs import logger
from sonorium.plugins import ui_contract

MAX_LIVE_PER_PLUGIN = 2000
LIVE_TEXT_MAX = 200


def ref_key(ref: Optional[dict]) -> str:
    """'*' for every object, else 'kind|theme|id'."""
    if not ref:
        return "*"
    return f"{ref.get('kind') or ''}|{ref.get('theme') or ''}|{ref.get('id') or ''}"


def _live_value(value: Any) -> Any:
    """A live value: text, a number, true/false or None, or {"text", "state"} for a badge."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > LIVE_TEXT_MAX:
            raise ValueError(f"a live value is at most {LIVE_TEXT_MAX} characters")
        return value
    if isinstance(value, dict) and set(value) <= {"text", "state"}:
        state = value.get("state")
        if state is not None and state not in ui_contract.STATES:
            raise ValueError(f"state is one of {', '.join(ui_contract.STATES)}")
        text = value.get("text")
        if text is not None and (not isinstance(text, str) or len(text) > LIVE_TEXT_MAX):
            raise ValueError(f"text is at most {LIVE_TEXT_MAX} characters")
        return {"text": text, "state": state}
    raise ValueError("a live value is text, a number, true/false, None or {\"text\", \"state\"}")


class UiService:
    """
    attribute_registry: core/plugin_data.Registry (the plugins' declared attributes)
    publish: the event bus's publish
    """

    def __init__(self, attribute_registry=None, publish: Optional[Callable] = None):
        self.registry = attribute_registry
        self.publish = publish
        self._raw: dict[str, Any] = {}          # plugin_id -> what it registered last (self.ui.register)
        self._checked: dict[str, tuple] = {}    # plugin_id -> (fingerprint, elements, warnings)
        self._live: dict[str, dict] = {}        # plugin_id -> {element_id: {ref_key: value}}

    # --- contributions -----------------------------------------------------------------

    def _source(self, plugin) -> Any:
        if plugin.id in self._raw:
            return self._raw[plugin.id]
        try:
            return plugin.get_ui()
        except Exception as e:
            return {"__error__": f"get_ui() failed: {e}"}

    def check(self, plugin) -> tuple[list, list]:
        """(elements, warnings) for a plugin, validated again only when what it registers changes."""
        raw = self._source(plugin)
        attributes = self.registry.declarations(plugin.id) if self.registry is not None else {}
        try:
            settings = plugin.settings_schema()
        except Exception:
            settings = {}
        fingerprint = repr((raw, attributes, settings))
        cached = self._checked.get(plugin.id)
        if cached and cached[0] == fingerprint:
            return cached[1], cached[2]
        if isinstance(raw, dict) and "__error__" in raw:
            elements, warnings = [], [raw["__error__"]]
        else:
            elements, warnings = ui_contract.validate(plugin.id, raw, attributes=attributes, settings=settings)
        for w in warnings:
            logger.warning(f"Plugin {plugin.id}: UI contribution refused: {w}")
        if elements:
            logger.debug(f"Plugin {plugin.id}: {len(elements)} UI element(s) accepted")
        self._checked[plugin.id] = (fingerprint, elements, warnings)
        return elements, warnings

    def warnings(self, plugin) -> list[str]:
        try:
            return list(self.check(plugin)[1])
        except Exception as e:
            return [f"contributions could not be checked: {e}"]

    def register(self, plugin, elements: Any) -> list[str]:
        """The plugin's contributions from now on (replacing get_ui()). Returns the warnings."""
        self._raw[plugin.id] = elements
        self._checked.pop(plugin.id, None)
        warnings = self.check(plugin)[1]
        self._changed(plugin.id)
        return warnings

    def elements(self, plugin) -> list[dict]:
        return list(self.check(plugin)[0]) if plugin.enabled else []

    def element(self, plugin, element_id: str) -> Optional[dict]:
        """The plugin's accepted element with this id (fields inside groups and pages included)."""
        for e in self.elements(plugin):
            if e["id"] == element_id:
                return e
            for f in e.get("fields") or []:
                if f["id"] == element_id:
                    return {**f, "kind": "field", "target": e["target"], "spot": e["spot"]}
            for s in e.get("sections") or []:
                for item in s["elements"]:
                    if item["id"] == element_id:
                        return {**item, "target": e["target"], "spot": e["spot"]}
                    for f in item.get("fields") or []:
                        if f["id"] == element_id:
                            return {**f, "kind": "field", "target": e["target"], "spot": e["spot"]}
        return None

    def forget(self, plugin_id: str) -> None:
        """The plugin was switched off or uninstalled."""
        self._raw.pop(plugin_id, None)
        self._checked.pop(plugin_id, None)
        self._live.pop(plugin_id, None)

    def _changed(self, plugin_id: str) -> None:
        if self.publish is not None:
            try:
                self.publish("ui.changed", {"plugin_id": plugin_id})
            except Exception as e:
                logger.debug(f"ui.changed not published: {e}")

    # --- live values -------------------------------------------------------------------

    def set_value(self, plugin, element_id: str, value: Any, ref: Optional[dict] = None) -> None:
        if self.element(plugin, element_id) is None:
            raise ValueError(f"No accepted UI element '{element_id}' (refused elements take no values)")
        clean = _live_value(value)
        key = ref_key(ref)
        store = self._live.setdefault(plugin.id, {})
        if sum(len(v) for v in store.values()) >= MAX_LIVE_PER_PLUGIN and key not in store.get(element_id, {}):
            raise ValueError(f"A plugin keeps at most {MAX_LIVE_PER_PLUGIN} live values")
        if clean is None:
            store.get(element_id, {}).pop(key, None)
        else:
            store.setdefault(element_id, {})[key] = clean
        if self.publish is not None:
            try:
                self.publish("ui.value", {"plugin_id": plugin.id, "element": element_id, "ref": key, "value": clean},
                             origin=f"plugin:{plugin.id}")
            except Exception as e:
                logger.debug(f"ui.value not published: {e}")

    def live(self, plugin_ids) -> dict:
        """{"plugin/element": {ref_key: value}} for these plugins."""
        out = {}
        for pid in plugin_ids:
            for element_id, values in (self._live.get(pid) or {}).items():
                out[f"{pid}/{element_id}"] = dict(values)
        return out

    def for_plugin(self, plugin) -> "PluginUi":
        return PluginUi(self, plugin)


class PluginUi:
    """self.ui: a plugin's contributions and live values."""

    def __init__(self, service: UiService, plugin):
        self._service = service
        self._plugin = plugin

    def register(self, elements: list) -> list[str]:
        """Replace the plugin's contributions; returns the warnings (refused elements, each naming its rule)."""
        return self._service.register(self._plugin, elements)

    def warnings(self) -> list[str]:
        return self._service.warnings(self._plugin)

    def set_value(self, element_id: str, value: Any, ref: Optional[dict] = None) -> None:
        """A live value for a badge, column, info line or action-bound field ({"live": true}); None removes it."""
        self._service.set_value(self._plugin, element_id, value, ref)
