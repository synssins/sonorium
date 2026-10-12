"""
Sonorium's web server: one FastAPI app (web UI, REST API, audio streams)
served by uvicorn on one or more ports.

- new_app() makes the app with Sonorium's API docs settings;
- Route and add_routes() register handlers from a plain table, each
  documented in the API docs by its own docstring;
- listener() describes one port to serve the app on, and serve() runs the
  main port plus any extra ones (HTTPS, HA ingress) until shutdown.
"""
import asyncio
import contextlib
import logging
from typing import Callable, NamedTuple

import uvicorn
from fastapi import FastAPI

from sonorium import host
from sonorium.obs import logger

# Every interface: speakers, phones and browsers on the LAN reach Sonorium directly
LISTEN_HOST = "0.0.0.0"
# Interactive API docs (Swagger UI), with "Try it out" switched on
DOCS_PATH = "/docs"
DOCS_UI_OPTIONS = {"tryItOutEnabled": True}
# uvicorn's loggers (the Logs page collects them from start-up: logbuffer.attach)
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
# At shutdown (a restart, docker stop): open streams and the event stream never end on
# their own, so after this long they are closed and the shutdown goes on
GRACEFUL_SHUTDOWN_SECONDS = 5


def mute_uvicorn_handlers() -> None:
    """uvicorn's loggers start bare: no handlers of their own, nothing passed up to the root logger."""
    for name in UVICORN_LOGGERS:
        uvicorn_log = logging.getLogger(name)
        uvicorn_log.handlers.clear()
        uvicorn_log.propagate = False


def new_app(title: str) -> FastAPI:
    return FastAPI(title=title, docs_url=DOCS_PATH, swagger_ui_parameters=dict(DOCS_UI_OPTIONS))


class Route(NamedTuple):
    """One entry of the app's route table: HTTP method, path, handler."""
    method: str
    path: str
    handler: Callable


def add_routes(app: FastAPI, routes: list[Route]) -> None:
    """
    Register the table in order (order matters: a specific path must come
    before a catch-all one). The handler's docstring is the route's summary
    and description in the API docs; routes are listed untagged.
    """
    for route in routes:
        text = (route.handler.__doc__ or "").strip() or None
        app.add_api_route(route.path, route.handler, methods=[route.method], tags=[None],
                          summary=text, description=text)


def listener(app: FastAPI, port: int, **options) -> uvicorn.Config:
    """
    One port serving the app. The caller's address is always the real peer,
    never a forwarded header: sign-in trusts HA's ingress by the
    Supervisor's address.
    """
    options.setdefault("timeout_graceful_shutdown", GRACEFUL_SHUTDOWN_SECONDS)
    return uvicorn.Config(app, host=LISTEN_HOST, port=port, access_log=False, proxy_headers=False, **options)


class _ExtraServer(uvicorn.Server):
    """A port beside the main one: the main server handles signals and stops it."""

    def install_signal_handlers(self) -> None:  # uvicorn < 0.29
        pass

    def capture_signals(self):  # uvicorn >= 0.29
        return contextlib.nullcontext()


async def _serve_extra(server: uvicorn.Server) -> None:
    try:
        await server.serve()
    except (SystemExit, Exception) as e:  # uvicorn exits when the port is taken
        logger.error(f"Listener on port {server.config.port} stopped: {e}")


async def serve(main: uvicorn.Config, extras: list[uvicorn.Config] = ()) -> None:
    """Serve on `main` until it shuts down; `extras` start with it and are stopped after it."""
    logger.debug(f"Web server on http://{main.host}:{main.port}")
    side = [_ExtraServer(config) for config in extras]
    running = [asyncio.create_task(_serve_extra(server)) for server in side]
    server = uvicorn.Server(main)

    def stop() -> None:  # a restart (sonorium/host.py): the same clean shutdown as a signal
        server.should_exit = True

    host.set_stopper(stop)
    try:
        await server.serve()
    finally:
        host.set_stopper(None)
        for server in side:
            server.should_exit = True
        if running:
            await asyncio.gather(*running, return_exceptions=True)


mute_uvicorn_handlers()
