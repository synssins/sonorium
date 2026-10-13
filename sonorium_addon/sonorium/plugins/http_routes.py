"""
A plugin's own HTTP routes, under /api/plugins/{plugin_id}/http/, each with
the ability a caller needs (auth/permissions.py: Channels view, Use plugin
pages and buttons, ...). Declared as data (manifest "http", or
get_http_routes()):

    [{"id": "status", "path": "status", "methods": ["GET"], "ability": "ch.view"},
     {"id": "flash", "path": "lights/{light}/flash", "methods": ["POST"], "ability": "pg.use"}]

Sonorium's sign-in checks the ability before the plugin is called (each route
is in the permission table while the plugin is on; a path it doesn't declare
is 404). The plugin answers with data: http_request(route_id, request) ->

    {"status": 200, "json": {...}}                 JSON
    {"status": 200, "text": "..."}                 plain text
    {"status": 200, "body": b"...", "content_type": "image/png"}

never a web page or a script: HTML, SVG, XML and JavaScript are refused, so
nothing a plugin sends can run with the signed-in user's rights. Every limit
is in LIMITS. Needs the permission "http" (plugins/permissions.py).
"""
from __future__ import annotations

import re
from typing import Optional

LIMITS = {
    "routes_per_plugin": 20,
    "path_max": 120,
    "body_max_bytes": 1024 * 1024,
    "response_max_bytes": 4 * 1024 * 1024,
    "timeout": 30.0,
}
METHODS = ("GET", "POST", "PUT", "DELETE")
ROUTE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
SEGMENT = re.compile(r"^(?:[a-z0-9][a-z0-9_.-]{0,59}|\{[a-z][a-z0-9_]{0,29}\})$")
# What a plugin may answer with (no page, script, SVG or XML: they could run in the user's session)
CONTENT_TYPES = ("application/json", "text/plain", "text/csv", "application/octet-stream", "image/png", "image/jpeg",
                 "image/gif", "image/webp", "audio/mpeg", "audio/ogg", "audio/wav", "audio/flac")
HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'",
           "Cache-Control": "no-store"}


def allowed_abilities() -> frozenset:
    from sonorium.auth.permissions import ABILITIES
    return frozenset(ABILITIES | {"admin.plugins"})


def normalize_routes(decls, plugin_id: str) -> tuple[list, list]:
    """(routes as kept, problems). Each route: id, path, methods, ability, template (the full path)."""
    if not decls:
        return [], []
    if not isinstance(decls, list):
        return [], ["'http' must be a list of routes"]
    kept, problems, seen = [], [], set()
    abilities = allowed_abilities()
    for decl in decls:
        if len(kept) >= LIMITS["routes_per_plugin"]:
            problems.append(f"at most {LIMITS['routes_per_plugin']} routes")
            break
        if not isinstance(decl, dict):
            problems.append("a route is an object")
            continue
        rid = decl.get("id")
        if not isinstance(rid, str) or not ROUTE_ID.match(rid) or rid in seen:
            problems.append(f"route {rid!r}: an id of a-z, 0-9, _ and -, once")
            continue
        path = str(decl.get("path") or "").strip("/")
        if not path or len(path) > LIMITS["path_max"] or not all(SEGMENT.match(s) for s in path.split("/")):
            problems.append(f"route {rid}: a path of a-z, 0-9, _ . - and {{name}} segments")
            continue
        methods = decl.get("methods") or ["GET"]
        if not isinstance(methods, list) or not methods or any(m not in METHODS for m in methods):
            problems.append(f"route {rid}: methods are some of {', '.join(METHODS)}")
            continue
        ability = decl.get("ability")
        if ability not in abilities:
            problems.append(f"route {rid}: an ability is needed, one of {', '.join(sorted(abilities))}")
            continue
        seen.add(rid)
        kept.append({"id": rid, "path": path, "methods": list(dict.fromkeys(methods)), "ability": ability,
                     "template": f"/api/plugins/{plugin_id}/http/{path}"})
    return kept, problems


def permission_table(routes: list) -> dict:
    """{(method, template): RoutePermission} for the sign-in's registry."""
    from sonorium.auth.permissions import RoutePermission
    return {(m, r["template"]): RoutePermission(r["ability"]) for r in routes for m in r["methods"]}


def match(routes: list, method: str, path: str) -> Optional[tuple[dict, dict]]:
    """The route a plugin-relative path is, with its path parameters (None: no such route)."""
    parts = path.strip("/").split("/")
    for route in routes:
        if method not in route["methods"]:
            continue
        want = route["path"].split("/")
        if len(want) != len(parts):
            continue
        params = {}
        for w, p in zip(want, parts):
            if w.startswith("{"):
                if not p:
                    break
                params[w[1:-1]] = p
            elif w != p:
                break
        else:
            return route, params
    return None


def response_parts(result) -> tuple[int, bytes, str]:
    """(status, body, content type) from what the plugin answered; ValueError for what isn't allowed."""
    import json
    if not isinstance(result, dict):
        raise ValueError("the plugin's answer is not an object")
    status = result.get("status", 200)
    if not isinstance(status, int) or isinstance(status, bool) or not 200 <= status <= 599:
        raise ValueError("status must be 200 to 599")
    if "json" in result:
        body, ctype = json.dumps(result["json"], default=str).encode("utf-8"), "application/json"
    elif "text" in result:
        body, ctype = str(result["text"]).encode("utf-8"), "text/plain; charset=utf-8"
    elif "body" in result:
        body = result["body"]
        ctype = str(result.get("content_type") or "application/octet-stream").split(";")[0].strip().lower()
        if not isinstance(body, (bytes, bytearray)):
            raise ValueError("body must be bytes")
        if ctype not in CONTENT_TYPES:
            raise ValueError(f"content type {ctype} is not allowed")
        body = bytes(body)
    else:
        body, ctype = b"", "text/plain; charset=utf-8"
    if len(body) > LIMITS["response_max_bytes"]:
        raise ValueError("the answer is too large")
    return status, body, ctype
