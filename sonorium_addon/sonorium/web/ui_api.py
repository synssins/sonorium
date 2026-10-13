"""
What plugins add to the web UI's screens (plugins/ui.py, plugins/ui_contract.py).

    GET  /api/ui/targets                          every target, its fixed parts, its open spots and their contracts
    GET  /api/ui/contributions                    the enabled plugins' accepted elements, badges and live values
    POST /api/ui/objects                          {"refs": [...]}: the plugin attributes of the objects on screen
    POST /api/ui/actions/{plugin_id}/{element_id} {"ref", "value"}: an action pressed, an action-bound field changed

Fields bound to attributes are saved with PUT /api/attributes/{kind}/{plugin_id};
fields bound to a plugin's settings with PUT /api/plugins/{id}/settings. Live
values arrive as "ui.value" events on GET /api/events/stream.
"""
from __future__ import annotations

import asyncio
import re

from fastapi import APIRouter, HTTPException, Request

from sonorium.obs import logger
from sonorium.plugins import events as ev
from sonorium.plugins import ui_contract
from sonorium.plugins.actions import ActionError
from sonorium.plugins.loader import is_valid_plugin_id
from sonorium.plugins.ui import ref_key

ACTION_TIMEOUT = 30
MAX_REFS = 500
_ID = re.compile(ui_contract.COMMON["id_pattern"])


def _clean_ref(ref) -> dict | None:
    """{"kind", "id", "theme"} with short text values, or None (a screen or page)."""
    if ref is None:
        return None
    if not isinstance(ref, dict) or not isinstance(ref.get("kind"), str) or not isinstance(ref.get("id"), (str, int)):
        raise HTTPException(status_code=400, detail="'ref' is {\"kind\", \"id\", \"theme\"} or null")
    theme = ref.get("theme")
    clean = {"kind": ref["kind"][:40], "id": str(ref["id"])[:300],
             "theme": theme[:300] if isinstance(theme, str) and theme else None}
    return clean


def add_ui_routes(router: APIRouter, plugin_manager, host, prefix: str = "/ui") -> APIRouter:
    """The routes above on the plugin framework's router (prefix "/api")."""

    def _host():
        if host is None or plugin_manager is None:
            raise HTTPException(status_code=503, detail="Plugin system not available")
        return host

    def _enabled():
        return [p for p in plugin_manager.plugins.values() if p.enabled] if plugin_manager else []

    @router.get(prefix + "/targets")
    async def targets():
        """Every target with its fixed parts and its open spots, each spot with its contract."""
        return ui_contract.catalogue()

    @router.get(prefix + "/contributions")
    async def contributions():
        """What the enabled plugins add: their accepted elements, their badges and their live values."""
        if host is None or plugin_manager is None:
            return {"version": ui_contract.CONTRACT_VERSION, "plugins": {}, "elements": [], "live": {}}
        plugins, elements = {}, []
        for plugin in _enabled():
            try:
                accepted = host.ui.elements(plugin)
            except Exception as e:
                logger.warning(f"Plugin {plugin.id}: its UI contributions could not be read: {e}")
                continue
            if not accepted:
                continue
            plugins[plugin.id] = {"name": plugin.name, "short_name": plugin.short_name(), "icon": plugin._manifest_icon()}
            elements.extend(accepted)
        return {"version": ui_contract.CONTRACT_VERSION, "plugins": plugins, "elements": elements,
                "live": host.ui.live(plugins.keys()), "attribute_kinds": list(ui_contract.ATTRIBUTE_KINDS)}

    @router.post(prefix + "/objects")
    async def objects(request: Request):
        """{"refs": [{"kind", "id", "theme"}]} -> {"values": {"kind|theme|id": {plugin_id: {name: value}}}}."""
        from sonorium.auth.middleware import principal_of
        from sonorium.web.plugin_framework_api import ATTRIBUTE_READ
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON body")
        refs = body.get("refs") if isinstance(body, dict) else None
        if not isinstance(refs, list) or len(refs) > MAX_REFS:
            raise HTTPException(status_code=400, detail=f"'refs' is a list of at most {MAX_REFS} objects")
        service = _host().attributes
        principal = principal_of(request)
        out = {}
        for raw in refs:
            ref = _clean_ref(raw)
            if ref is None or ref["kind"] not in ui_contract.ATTRIBUTE_KINDS:
                continue
            kind = ref["kind"]
            if kind == "user":
                allowed = principal.can("admin.users") or (principal.can("self") and principal.user_id == ref["id"])
            else:
                allowed = principal.can(ATTRIBUTE_READ[kind]) and (kind != "channel" or principal.can_channel(ref["id"]))
            if not allowed:
                continue
            try:
                out[ref_key(ref)] = service.get(ref)
            except ActionError:
                continue
        return {"values": out}

    @router.post(prefix + "/actions/{plugin_id}/{element_id}")
    async def action(plugin_id: str, element_id: str, request: Request):
        """An action element pressed, or an action-bound field changed: {"ref", "value"}."""
        _host()
        if not is_valid_plugin_id(plugin_id) or not _ID.match(element_id):
            raise HTTPException(status_code=400, detail="Invalid plugin or element id")
        plugin = plugin_manager.get_plugin(plugin_id)
        if plugin is None or not plugin.enabled:
            raise HTTPException(status_code=404, detail=f"Plugin not found or off: {plugin_id}")
        element = host.ui.element(plugin, element_id)
        if element is None or not (element["kind"] == "action"
                                   or (element["kind"] == "field" and "action" in element.get("bind", {}))):
            raise HTTPException(status_code=404, detail=f"No action '{element_id}' from {plugin_id}")
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        ref = _clean_ref(body.get("ref"))
        value = body.get("value")
        call_id = element["bind"]["action"] if element["kind"] == "field" else element_id
        token = ev.current_origin.set(f"plugin:{plugin.id}")
        try:
            result = await asyncio.wait_for(plugin.ui_action(call_id, ref, value), ACTION_TIMEOUT)
        except Exception as e:
            message = "timed out" if isinstance(e, asyncio.TimeoutError) else str(e) or type(e).__name__
            logger.warning(f"Plugin {plugin.id}: ui_action {call_id} failed: {message}")
            host.bus.publish("error", {"where": "ui_action", "message": message, "plugin_id": plugin.id})
            raise HTTPException(status_code=502, detail=f"The plugin failed: {message}")
        finally:
            ev.current_origin.reset(token)
        if not isinstance(result, dict):
            return {"success": False, "message": "The plugin gave no result", "refresh": False}
        clean = {"success": bool(result.get("success")), "message": str(result.get("message") or "")[:300],
                 "refresh": bool(result.get("refresh", False))}
        if "value" in result:
            clean["value"] = result["value"]
        return clean

    return router
