"""G10: the older routes' 200 {"error": ...} answers get an error status (web/legacy_errors.py)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from playback_helpers import addon_modules  # noqa: E402


@pytest.fixture
def client():
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse, StreamingResponse
    from fastapi.testclient import TestClient
    with addon_modules("sonorium.web.legacy_errors") as mods:
        app = FastAPI()

        @app.middleware("http")  # as api.py's cards CORS: the answer then comes in parts
        async def passthrough(request, call_next):
            return await call_next(request)

        app.add_middleware(mods.legacy_errors.LegacyErrorStatus)

        @app.get("/api/themes/{theme_id}")
        async def theme(theme_id: str):
            return {"id": theme_id} if theme_id == "ok" else {"error": "Theme not found"}

        @app.put("/api/themes/{theme_id}/tracks/{track}/volume")
        async def volume(theme_id: str, track: str):
            return {"error": "Volume is required"}

        @app.post("/api/categories")
        async def category():
            return {"error": "Category already exists"}

        @app.post("/api/themes/{theme_id}/favorite")
        async def favorite(theme_id: str):
            return {"error": "Failed to save favorite status"}

        @app.get("/api/channels/{n}")
        async def channel(n: int):
            return {"error": "Channel system not initialized"}

        @app.get("/api/themes/{theme_id}/presets")
        async def with_more(theme_id: str):
            return {"error": "x", "status": "partial"}  # not exactly {"error"}: left alone

        @app.get("/api/themes/{theme_id}/tracks/{track}/audio")
        async def audio(theme_id: str, track: str):
            return StreamingResponse(iter([b'{"error": "Theme not found"}']), media_type="application/json")

        @app.get("/api/plugins/{plugin_id}")
        async def plugin(plugin_id: str):
            return {"error": "not found"}  # another route family: left alone

        @app.get("/api/themes/{theme_id}/already")
        async def already(theme_id: str):
            return JSONResponse({"error": "Theme not found"}, status_code=404)

        yield TestClient(app)


@pytest.mark.parametrize("method, path, status", [
    ("GET", "/api/themes/nope", 404),
    ("PUT", "/api/themes/a/tracks/rain/volume", 400),
    ("POST", "/api/categories", 409),
    ("POST", "/api/themes/a/favorite", 500),
    ("GET", "/api/channels/3", 503),
])
def test_error_answers_get_a_status(client, method, path, status):
    r = client.request(method, path)
    assert r.status_code == status
    body = r.json()
    assert body["detail"] == body["error"] and body["detail"]  # the API's field, and the old one


def test_everything_else_is_untouched(client):
    assert client.get("/api/themes/ok").json() == {"id": "ok"}
    assert client.get("/api/themes/a/presets").status_code == 200
    assert client.get("/api/themes/a/tracks/rain/audio").status_code == 200  # streamed: not read
    assert client.get("/api/plugins/x").status_code == 200
    r = client.get("/api/themes/a/already")
    assert r.status_code == 404 and r.json() == {"error": "Theme not found"}
