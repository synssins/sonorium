"""
Theme packages for offline playback (docs/APP_API.md section 10): a theme's
file list with checksums, each file on its own (resumable), and a reference
render of a preset. add_theme_package_routes() adds them to the /api router
that create_api_router() builds; the package listing (GET
/api/themes/packages) is in api.py, ahead of GET /api/themes/{theme_id}.
Each route has its permission in sonorium/auth/permissions.py.
"""
from __future__ import annotations

import threading
from typing import Callable

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse

# A reference render: at most this long, one at a time, a few a minute per caller
RENDER_MAX_SECONDS = 120
RENDER_RATE = 1 / 20.0
RENDER_BURST = 3


def _etag(sha256: str) -> str:
    return f'"{sha256}"'


def _etag_matches(header: str, etag: str) -> bool:
    values = [v.strip() for v in (header or "").split(",")]
    return "*" in values or etag in values or f"W/{etag}" in values


def add_theme_package_routes(router, *, find_theme_folder: Callable) -> None:
    """The theme package routes, on create_api_router()'s /api router."""
    from sonorium.auth.ratelimit import RateLimiter

    def package_or_404(theme_id: str):
        from sonorium.core import theme_package
        folder = find_theme_folder(theme_id)
        if not folder:
            raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")
        return theme_package.build_package(folder)

    @router.get("/themes/{theme_id}/package")
    async def get_theme_package(theme_id: str):
        """A theme's files with their sizes and SHA-256, its content hash, documents and the playback values."""
        from sonorium import playback_values
        package = await run_in_threadpool(package_or_404, theme_id)
        return {
            "theme_id": package.theme_id,
            "name": package.name,
            "spec_version": package.spec_version,
            "content_hash": package.content_hash,
            "size_bytes": package.size_bytes,
            "files": package.manifest(),
            "metadata": package.metadata,
            "presets": package.presets,
            "playback_values": playback_values.values(),
        }

    @router.get("/themes/{theme_id}/files/{path:path}")
    async def get_theme_file(theme_id: str, path: str, request: Request):
        """One file of a theme's package, by its path in the package; Range and ETag (its SHA-256) supported."""
        from sonorium.core import theme_package
        package = await run_in_threadpool(package_or_404, theme_id)
        # Looked up in the package's list only: no path from the request ever reaches the file system
        entry = package.by_path.get(path)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"No file '{path}' in theme '{theme_id}'")
        etag = _etag(entry.sha256)
        headers = {"ETag": etag, "Accept-Ranges": "bytes", "Cache-Control": "no-cache"}
        media_type = _media_type(entry.path)

        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(status_code=304, headers=headers)

        range_header = request.headers.get("range")
        if_range = request.headers.get("if-range")
        if range_header and if_range and if_range.strip() != etag:
            range_header = None  # the file changed since the client's copy: send it whole
        try:
            span = theme_package.parse_range(range_header, entry.size)
        except theme_package.RangeNotSatisfiable:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{entry.size}"})

        start, end = span if span else (0, entry.size - 1)
        length = max(0, end - start + 1)
        status = 206 if span else 200
        if span:
            headers["Content-Range"] = f"bytes {start}-{end}/{entry.size}"
        headers["Content-Length"] = str(length)
        if entry.data is not None:
            return Response(entry.data[start:start + length], status_code=status, headers=headers,
                            media_type=media_type)
        return StreamingResponse(theme_package.iter_file(entry.source, start, length), status_code=status,
                                 headers=headers, media_type=media_type)

    limiter = RateLimiter(RENDER_RATE, RENDER_BURST)
    busy = threading.Lock()

    @router.post("/themes/{theme_id}/render")
    async def render_theme(theme_id: str, request: Request):
        """A WAV (mono, 44.1 kHz, 16-bit) of a preset mixed by the engine with a fixed seed. Admin only."""
        body = await _json_object(request)
        preset_id = body.get("preset_id")
        if preset_id is not None and not isinstance(preset_id, str):
            raise HTTPException(status_code=400, detail="preset_id must be a string or null")
        seconds = body.get("seconds", 30)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 1 <= seconds <= RENDER_MAX_SECONDS:
            raise HTTPException(status_code=400, detail=f"seconds must be a number from 1 to {RENDER_MAX_SECONDS}")
        seed = body.get("seed", 0)
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise HTTPException(status_code=400, detail="seed must be an integer")
        master_gain = body.get("master_gain")
        if master_gain is not None and (isinstance(master_gain, bool) or not isinstance(master_gain, (int, float))
                                        or not 0 <= master_gain <= 100):
            raise HTTPException(status_code=400, detail="master_gain must be a number from 0 to 100")

        folder = find_theme_folder(theme_id)
        if not folder:
            raise HTTPException(status_code=404, detail=f"Theme '{theme_id}' not found")

        allowed, wait = limiter.allow(_caller_key(request))
        if not allowed:
            raise HTTPException(status_code=429, detail="Too many renders; try again later",
                                headers={"Retry-After": str(int(wait) + 1)})
        if not busy.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="A render is already running; try again later",
                                headers={"Retry-After": "5"})
        try:
            data = await run_in_threadpool(_render_wav, folder, preset_id, float(seconds), seed, master_gain)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'\""))
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(status_code=422, detail=str(e))
        finally:
            busy.release()
        return Response(data, media_type="audio/wav", headers={"Content-Length": str(len(data))})


def _render_wav(folder, preset_id, seconds: float, seed: int, master_gain) -> bytes:
    """The engine's mix of the theme folder as WAV bytes (the server's Settings > Advanced values)."""
    import io
    from sonorium import playback_values
    from sonorium.paths import audio_dir
    out = io.BytesIO()
    _render_into(out, folder, preset_id, seconds, seed, playback_values.values(), master_gain, audio_dir())
    return out.getvalue()


def _render_into(out, folder, preset_id, seconds, seed, values, master_gain, themes_root) -> None:
    import numpy as np
    from sonorium.engine import DEFAULT_MASTER_GAIN, SAMPLE_RATE, Engine
    import wave
    total = int(round(seconds * SAMPLE_RATE))
    engine = Engine(folder, preset_id=preset_id, playback_values=values, seed=seed, themes_root=themes_root,
                    master_gain=DEFAULT_MASTER_GAIN if master_gain is None else float(master_gain))
    try:
        parts, done = [], 0
        while done < total:
            n = min(4096, total - done)
            parts.append(engine.render(n))
            done += n
    finally:
        engine.close()
    samples = np.concatenate(parts) if parts else np.zeros(0, np.int16)
    with wave.open(out, "wb") as w:  # as engine/render.py's write_wav: mono, 16-bit
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(np.asarray(samples, dtype="<i2").tobytes())


async def _json_object(request: Request) -> dict:
    raw = await request.body()
    if not raw.strip():
        return {}
    import json
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object")
    return data


def _caller_key(request: Request) -> str:
    """Who asks: the signed-in caller (user, phone, key), else the address."""
    try:
        from sonorium.auth.principal import current_caller
        caller = current_caller()
    except Exception:
        caller = None
    if caller is not None:
        for kind, value in (("device", caller.device_id), ("key", caller.key_id), ("user", caller.user_id)):
            if value:
                return f"{kind}:{value}"
    return "addr:" + (request.client.host if request.client else "unknown")


_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
          ".json": "application/json", ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8"}


def _media_type(path: str) -> str:
    dot = path.rfind(".")
    return _TYPES.get(path[dot:].lower() if dot >= 0 else "", "application/octet-stream")
