"""
How much a caller may send or hold open: request bodies, uploads, imports,
exports, streams and event streams. Every limit is a Settings > Advanced
value (playback_values.DEFAULTS) with a default; 0 is no limit.

    auth_body_limit_kb         sign-in, Link App and pairing requests (before anyone is signed in)
    request_body_limit_kb      any other request (JSON and the like)
    upload_limit_mb            one upload: a theme's audio file, a theme or plugin zip, a backup file
    import_unpacked_limit_mb   a theme zip's files, unpacked
    import_file_limit          files in one theme zip
    export_limit               theme exports being made at the same time
    streams_per_address        audio streams one address may have open
    streams_limit              audio streams open in all
    event_streams_per_caller   event streams one caller may have open

BodyLimitMiddleware applies the body limits to every request: past the
Content-Length or the bytes that came in, the answer is 413. Most bodies are
read in full (at most the limit) before the route sees them; uploads are
counted as they stream (the route writes them to disk).
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Optional

from fastapi import HTTPException

KB = 1024
MB = 1024 * 1024

AUTH_BODY = "auth_body_limit_kb"
REQUEST_BODY = "request_body_limit_kb"
UPLOAD = "upload_limit_mb"
IMPORT_UNPACKED = "import_unpacked_limit_mb"
IMPORT_FILES = "import_file_limit"
EXPORTS = "export_limit"
STREAMS_PER_ADDRESS = "streams_per_address"
STREAMS = "streams_limit"
EVENT_STREAMS = "event_streams_per_caller"
LOGIN_PER_USER = "login_attempts_per_user"
STREAM_LINK_HOURS = "stream_link_hours"
LAN_STREAMS_OPEN = "lan_streams_open"



def value(key: str) -> float:
    """A limit now (Settings > Advanced, else its default)."""
    from sonorium import playback_values
    return playback_values.get(key)


def count(key: str) -> Optional[int]:
    """A count limit; None for no limit (0)."""
    n = value(key)
    return int(n) if n > 0 else None


def size(key: str) -> Optional[int]:
    """A size limit in bytes (the key's unit: _kb or _mb); None for no limit (0)."""
    n = value(key)
    if n <= 0:
        return None
    return int(n * (KB if key.endswith("_kb") else MB))


def _describe(limit: int) -> str:
    if limit >= MB:
        return f"{limit / MB:g} MB"
    if limit >= KB:
        return f"{limit / KB:g} KB"
    return f"{limit} bytes"


class BodyTooLarge(HTTPException):
    """413: the request's body is over its limit."""

    def __init__(self, limit: int):
        super().__init__(status_code=413, detail=f"Too large: at most {_describe(limit)}")
        self.limit = limit


def declared_length(headers) -> Optional[int]:
    raw = (headers.get("content-length") or "").strip()
    return int(raw) if raw.isdigit() else None


async def read_body(request, limit: Optional[int]) -> bytes:
    """The request's body, at most `limit` bytes read (None: no limit); 413 beyond."""
    declared = declared_length(request.headers)
    if limit is not None and declared is not None and declared > limit:
        raise BodyTooLarge(limit)
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if limit is not None and len(raw) > limit:
            raise BodyTooLarge(limit)
    return bytes(raw)


async def read_json(request, limit: Optional[int] = None, *, empty: Any = ...) -> Any:
    """
    The request's JSON, at most `limit` bytes (default: request_body_limit_kb).
    413 beyond, 400 when it isn't JSON; `empty` is the answer for no body
    (left out: no body is 400 too).
    """
    raw = await read_body(request, size(REQUEST_BODY) if limit is None else limit)
    if not raw.strip() and empty is not ...:
        return empty
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")


async def read_json_object(request, limit: Optional[int] = None, *, empty: Any = ...) -> dict:
    """read_json for a JSON object (400 for anything else)."""
    data = await read_json(request, limit, empty=empty)
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    return data


async def copy_upload(source, out, limit: Optional[int], chunk: int = MB) -> int:
    """An uploaded file (Starlette UploadFile) copied to `out` in chunks; BodyTooLarge past `limit`. The bytes copied."""
    total = 0
    while True:
        data = await source.read(chunk)
        if not data:
            return total
        total += len(data)
        if limit is not None and total > limit:
            raise BodyTooLarge(limit)
        out.write(data)


# --- Which limit a request has ------------------------------------------------------

# (path pattern, limit key, streamed): first match wins. Streamed bodies are counted
# as the route reads them; the rest are read in full (at most the limit) first.
RULES: list[tuple[re.Pattern, str, bool]] = [
    (re.compile(r"^/api/auth/"), AUTH_BODY, False),
    (re.compile(r"^/api/(pair|pair/login|link)/?$"), AUTH_BODY, False),
    (re.compile(r"^/api/themes/[^/]+/upload$"), UPLOAD, True),
    (re.compile(r"^/api/themes/import$"), UPLOAD, True),
    (re.compile(r"^/api/plugins/upload$"), UPLOAD, True),
    (re.compile(r"^/api/me/backups/[^/]+/themes/[^/]+/files/"), UPLOAD, True),
    # A theme backup's JSON has its own limit (core/backups.py MAX_JSON_BYTES), checked as its routes read it
    (re.compile(r"^/api/me/backups/"), UPLOAD, True),
]


def rule_for(path: str) -> tuple[str, bool]:
    """(limit key, streamed) for a request path."""
    for pattern, key, streamed in RULES:
        if pattern.match(path):
            return key, streamed
    return REQUEST_BODY, False


class BodyLimitMiddleware:
    """413 for a request body over its limit (rule_for), before or while the route reads it."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        key, streamed = rule_for(scope.get("path") or "")
        limit = size(key)
        if limit is None:
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        declared = declared_length(headers)
        if declared is not None and declared > limit:
            return await self._too_large(scope, receive, send, limit)

        if streamed:
            seen = 0

            async def counted():
                nonlocal seen
                message = await receive()
                if message["type"] == "http.request":
                    seen += len(message.get("body") or b"")
                    if seen > limit:
                        raise BodyTooLarge(limit)
                return message
            return await self.app(scope, counted, send)

        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":  # the caller went away
                return
            body.extend(message.get("body") or b"")
            if len(body) > limit:
                return await self._too_large(scope, receive, send, limit)
            if not message.get("more_body"):
                break
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        return await self.app(scope, replay, send)

    @staticmethod
    async def _too_large(scope, receive, send, limit: int):
        from starlette.responses import JSONResponse
        response = JSONResponse({"detail": BodyTooLarge(limit).detail}, status_code=413,
                                headers={"Connection": "close"})
        return await response(scope, receive, send)


# --- Counted slots: streams, event streams, exports -----------------------------------

class Slots:
    """Open things counted by key and in all, each against a limit read when one is taken."""

    def __init__(self):
        self._lock = threading.Lock()
        self._by_key: dict[str, int] = {}
        self._total = 0

    def take(self, key: str, per_key: Optional[int], total: Optional[int] = None) -> bool:
        """One more for `key`, if under both limits (None: no limit). False: over a limit, nothing taken."""
        with self._lock:
            held = self._by_key.get(key, 0)
            if per_key is not None and held >= per_key:
                return False
            if total is not None and self._total >= total:
                return False
            self._by_key[key] = held + 1
            self._total += 1
            return True

    def give(self, key: str) -> None:
        with self._lock:
            held = self._by_key.get(key, 0)
            if held <= 1:
                self._by_key.pop(key, None)
            else:
                self._by_key[key] = held - 1
            if held:
                self._total -= 1

    def held(self, key: Optional[str] = None) -> int:
        with self._lock:
            return self._total if key is None else self._by_key.get(key, 0)
