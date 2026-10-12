"""
Error statuses for the older theme, category and channel routes (api.py).

Some of them answer 200 with {"error": "..."} when something is wrong (a
theme that isn't found, a missing field). This middleware gives those
answers an error status, so the app (docs/APP_API.md G10) can rely on the
status alone:

    "... not found"                 404
    "... already exists"            409
    "... not initialized/available" 503
    "Failed ..."                    500
    anything else                   400

The body keeps "error" and gains "detail" (the API's usual error field), with
the same message: the web UI's api() reads either. Only JSON bodies that are
exactly {"error": <text>} from those routes are changed; nothing else is
read or held back (streams and files pass straight through).
"""
from __future__ import annotations

import json

PREFIXES = ("/api/themes", "/api/categories", "/api/channels")
MAX_BODY = 4096  # such answers are tiny; anything longer is passed on as it is


def status_for(message: str) -> int:
    text = message.lower()
    if "not found" in text:
        return 404
    if "already exists" in text:
        return 409
    if "not initialized" in text or "not available" in text:
        return 503
    if text.startswith("failed"):
        return 500
    return 400


def _legacy_error(body: bytes):
    """The message of a body that is exactly {"error": <text>}, else None."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if isinstance(data, dict) and list(data) == ["error"] and isinstance(data["error"], str):
        return data["error"]
    return None


class LegacyErrorStatus:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path") or ""
        if scope["type"] != "http" or not path.startswith(PREFIXES):
            return await self.app(scope, receive, send)

        held = {}  # a 200 JSON answer's start and body so far, while its body is read

        async def flush(start, body: bytes, more: bool):
            await send(start)
            await send({"type": "http.response.body", "body": body, "more_body": more})

        async def wrapped(message):
            if message["type"] == "http.response.start" and not held:
                headers = dict(message.get("headers") or [])
                is_json = headers.get(b"content-type", b"").startswith(b"application/json")
                small = int(headers.get(b"content-length", b"0") or 0) <= MAX_BODY
                if message["status"] == 200 and is_json and small and b"content-length" in headers:
                    held.update(start=message, body=b"")
                    return
                return await send(message)
            if message["type"] == "http.response.body" and "start" in held:
                body = held["body"] + message.get("body", b"")
                if message.get("more_body"):  # sent in parts (e.g. through another middleware)
                    if len(body) <= MAX_BODY:
                        held["body"] = body
                        return
                    start = held.pop("start")
                    held.pop("body")
                    return await flush(start, body, True)
                start = held.pop("start")
                held.pop("body")
                error = _legacy_error(body)
                if error is not None:
                    body = json.dumps({"detail": error, "error": error}).encode("utf-8")
                    headers = [(k, v) for k, v in start.get("headers") or [] if k != b"content-length"]
                    headers.append((b"content-length", str(len(body)).encode()))
                    start = {**start, "status": status_for(error), "headers": headers}
                return await flush(start, body, False)
            return await send(message)

        return await self.app(scope, receive, wrapped)
