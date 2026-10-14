"""
REST endpoints of the plugin framework (docs/PLUGIN_DEVELOPMENT.md).

Events          GET  /api/events/catalogue
                GET  /api/events/stream              (Server-Sent Events; token "events")
                GET|POST|DELETE /api/events/token
Triggers        GET  /api/plugins/{id}/triggers
                POST|DELETE /api/plugins/{id}/triggers/token
                GET|POST /api/triggers/{plugin_id}/{trigger}   (token: plugin's or trigger's)
Scheduler       GET  /api/schedules,  PUT /api/schedules/settings
                GET|POST /api/plugins/{id}/schedules
                DELETE /api/plugins/{id}/schedules/{job_id}
                PUT  /api/plugins/{id}/schedules/{job_id}/enabled
Extensions      GET  /api/extensions
                GET  /api/plugins/{id}/pages/{page_id}
                POST /api/plugins/{id}/pages/{page_id}/actions/{action}
                GET|PUT /api/plugins/{id}/connections/{connection_id}
                POST /api/plugins/{id}/connections/{connection_id}/test
UI              GET  /api/ui/targets, GET /api/ui/contributions, POST /api/ui/objects,
                POST /api/ui/actions/{plugin_id}/{element_id}   (web/ui_api.py)
Attributes      GET  /api/attributes                 (every plugin's schema)
                GET  /api/attributes/{kind}?id=..&theme=..
                PUT  /api/attributes/{kind}/{plugin_id}?id=..&theme=..

Paths for every plugin sit under /api/extensions, /api/schedules, ... and not
under /api/plugins/..., where GET /api/plugins/{plugin_id} would catch them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from sonorium.obs import logger
from sonorium.plugins import events as ev
from sonorium.plugins.extensions import (
    EXTENSION_ID, ExtensionError, normalize_connection_decls, normalize_page, normalize_page_decls,
    normalize_result, normalize_status, page_actions, page_fields, check_values,
)
from sonorium.plugins.loader import is_valid_plugin_id
from sonorium.plugins.scheduler import ScheduleError, resolve_timezone
from sonorium.plugins.tokens import install_log_redaction, token_from_request
from sonorium.plugins.triggers import MAX_PAYLOAD_BYTES, TRIGGER_ID

EVENTS_SCOPE = "events"
MAX_STREAMS = 20
# ActionError.code as an HTTP status
ACTION_ERROR_STATUS = {"not_found": 404, "invalid": 400, "conflict": 409, "unavailable": 503, "forbidden": 403}
KEEPALIVE_SECONDS = 15
PAGE_TIMEOUT, ACTION_TIMEOUT, TEST_TIMEOUT = 10, 30, 20


def _allowed(request: Request, ability: str) -> bool:
    """
    A caller sign-in recognised with that ability: a key, a linked phone, a
    signed-in browser or HA's sidebar. Not "nobody signed in" (Require login
    off): triggers and the event stream always needed a token, and still do.
    """
    from sonorium.auth.middleware import principal_of
    principal = principal_of(request)
    return principal.kind in ("key", "device", "session", "ingress") and principal.can(ability)


# What a caller needs to see an event, by the start of its type (the rest: "Read events" alone)
EVENT_ABILITIES = {
    "channel.": "ch.view", "sequence.": "ch.view", "sound.": "ch.view", "ramp.": "ch.view", "zone.": "ch.view",
    "link.": "ch.view", "playlist.": "pl.view", "theme.": "th.view", "attribute.": "th.view",
    "speaker.": "sp.view", "device.": "sp.view", "group.": "sp.view", "spaces.": "sp.view", "settings.": "st.view",
}


def event_visible(principal, event) -> bool:
    """
    The caller may see this event: the ability its type needs (EVENT_ABILITIES),
    and a key for some channels sees only those channels' events. None (the
    old events token, made by an admin): every event.
    """
    if principal is None:
        return True
    for prefix, ability in EVENT_ABILITIES.items():
        if event.type.startswith(prefix):
            if not principal.can(ability):
                return False
            break
    channel = (event.data or {}).get("channel_id") if isinstance(event.data, dict) else None
    if channel is not None and getattr(principal, "channels", None) is not None:
        return principal.can_channel(channel)
    return True


def event_caller_key(request: Request) -> str:
    """Who holds an event stream, for event_streams_per_caller: the account, phone or key, else the address."""
    from sonorium.auth.middleware import principal_of
    p = principal_of(request)
    for kind, value in (("user", p.user_id), ("device", p.device_id), ("key", getattr(p, "key_id", None))):
        if value:
            return f"{kind}:{value}"
    return "address:" + (request.client.host if request.client else "")


EVENT_SLOTS = None  # limits.Slots, made with the first router


def create_plugin_framework_router(plugin_manager, host) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["plugin-framework"])
    install_log_redaction()
    streams = {"open": 0}
    from sonorium import limits
    global EVENT_SLOTS
    if EVENT_SLOTS is None:
        EVENT_SLOTS = limits.Slots()
    slots = EVENT_SLOTS

    def take_stream_slot(request: Request) -> str:
        """One more event stream for this caller (event_streams_per_caller), else 429."""
        key = event_caller_key(request)
        if not slots.take(key, limits.count(limits.EVENT_STREAMS)):
            raise HTTPException(status_code=429, detail="Too many event streams are open. Close one and try again.")
        return key

    def _host():
        if host is None or plugin_manager is None:
            raise HTTPException(status_code=503, detail="Plugin system not available")
        return host

    def _plugin(plugin_id: str, enabled: bool = False):
        _host()
        if not is_valid_plugin_id(plugin_id):
            raise HTTPException(status_code=400, detail="Invalid plugin id")
        plugin = plugin_manager.get_plugin(plugin_id)
        if plugin is None:
            raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")
        if enabled and not plugin.enabled:
            raise HTTPException(status_code=409, detail=f"Plugin is not enabled: {plugin_id}")
        return plugin

    def _check_id(value: str, what: str) -> str:
        if not isinstance(value, str) or not EXTENSION_ID.match(value):
            raise HTTPException(status_code=400, detail=f"Invalid {what}")
        return value

    async def _json(request: Request) -> dict:
        """The request's JSON object, at most request_body_limit_kb (sonorium/limits.py; 413 beyond)."""
        from sonorium import limits
        return await limits.read_json_object(request)

    async def _call_plugin(plugin, what: str, coro, timeout: float):
        """Run a plugin method; its failure is a 502 (and an error event), never a crash."""
        token = ev.current_origin.set(f"plugin:{plugin.id}")
        try:
            return await asyncio.wait_for(coro, timeout)
        except HTTPException:
            raise
        except Exception as e:
            message = "timed out" if isinstance(e, asyncio.TimeoutError) else str(e) or type(e).__name__
            logger.warning(f"Plugin {plugin.id}: {what} failed: {message}")
            host.bus.publish("error", {"where": what, "message": message, "plugin_id": plugin.id})
            raise HTTPException(status_code=502, detail=f"The plugin failed: {message}")
        finally:
            ev.current_origin.reset(token)

    # --- Events ---

    @router.get("/events/catalogue")
    async def event_catalogue():
        """Every event Sonorium publishes, with its fields; plugins add plugin.<id>.<name>."""
        return {
            "version": ev.CATALOGUE_VERSION,
            "events": {name: {"description": e["description"], "fields": e["fields"]}
                       for name, e in ev.CATALOGUE.items()},
            "plugin_events": "plugin.<plugin_id>.<name>",
        }

    @router.get("/events/token")
    async def event_token_status():
        return {"set": _host().tokens.info(EVENTS_SCOPE) is not None, **(host.tokens.info(EVENTS_SCOPE) or {})}

    @router.post("/events/token")
    async def create_event_token():
        """A new token for the event stream (replaces the old one). Shown only now."""
        return {"token": _host().tokens.create(EVENTS_SCOPE)}

    @router.delete("/events/token")
    async def revoke_event_token():
        return {"revoked": _host().tokens.revoke(EVENTS_SCOPE)}

    @router.get("/events/stream")
    async def event_stream(request: Request, types: str = "*", max_events: int = 0):
        """
        Server-Sent Events: each event as "id: <boot_id>:<n>", "event: <type>", "data: <JSON>".
        types: comma-separated patterns. Last-Event-ID (header or ?last_event_id)
        replays recent events after it (an id from before a restart: all of this
        run's). max_events > 0 closes the stream after that many.
        """
        bus = _host().bus
        # A key with "Read events", a linked phone or a signed-in user; or the old events token
        signed_in = _allowed(request, "ev.read")
        if not signed_in and not host.tokens.check(EVENTS_SCOPE, token_from_request(request)):
            raise HTTPException(status_code=401, detail="A valid token is needed",
                                headers={"WWW-Authenticate": "Bearer"})
        from sonorium.auth.middleware import principal_of
        viewer = principal_of(request) if signed_in else None  # events filtered by what it may see
        try:
            patterns = [ev.check_pattern(p) for p in types.split(",") if p.strip()] or ["*"]
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if streams["open"] >= MAX_STREAMS:
            raise HTTPException(status_code=503, detail="Too many event streams open")
        slot = take_stream_slot(request)
        last = request.headers.get("last-event-id") or request.query_params.get("last_event_id") or ""
        last_id = ev.replay_after(last)  # "<boot_id>:<n>"; another run's id replays all of this one

        queue: asyncio.Queue = asyncio.Queue(maxsize=500)

        async def collect(event):
            if not event_visible(viewer, event):
                return
            if queue.full():
                queue.get_nowait()  # a stalled reader loses the oldest
            queue.put_nowait(event)

        # A personal channel's events go only to its account's streams
        from sonorium.web.personal_routes import audience_of
        audience = audience_of(request)
        sub = bus.subscribe(patterns, collect, owner=None, queue_size=500, audience=audience)
        streams["open"] += 1

        def frame(event) -> str:
            return f"id: {ev.stream_id(event)}\nevent: {event.type}\ndata: {json.dumps(event.to_dict(), default=str)}\n\n"

        async def generate():
            sent, newest = 0, 0
            try:
                yield "retry: 5000\n\n"
                if last_id is not None:
                    for event in bus.history(last_id, patterns, audience=audience):
                        if not event_visible(viewer, event):
                            continue
                        yield frame(event)
                        newest, sent = event.id, sent + 1
                        if max_events and sent >= max_events:
                            return
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if event.id <= newest:
                        continue
                    yield frame(event)
                    sent += 1
                    if max_events and sent >= max_events:
                        return
            finally:
                bus.unsubscribe(sub)
                streams["open"] -= 1
                slots.give(slot)

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- The cue timeline (core/timeline.py) ---

    def _timeline_reader(request: Request):
        if not _allowed(request, "ev.read") and not _host().tokens.check(EVENTS_SCOPE, token_from_request(request)):
            raise HTTPException(status_code=401, detail="A valid token is needed",
                                headers={"WWW-Authenticate": "Bearer"})
        timeline = getattr(_host(), "timeline", None)
        if timeline is None:
            raise HTTPException(status_code=503, detail="The cue timeline isn't available")
        return timeline

    def _channel_list(value: str):
        names = [c.strip() for c in (value or "").split(",") if c.strip()]
        return names or None

    @router.get("/timeline")
    async def timeline_info(request: Request, channels: str = ""):
        """The cue events, the Look-ahead, and the cues scheduled and not yet ended."""
        timeline = _timeline_reader(request)
        from sonorium.web.personal_routes import audience_of
        audience = audience_of(request)
        wanted = _channel_list(channels)
        pending = [{k: v for k, v in c.items() if not k.startswith("_")} for c in timeline.pending(audience=audience)
                   if wanted is None or c["channel_id"] in wanted]
        return {**timeline.describe(), "pending": pending}

    @router.get("/timeline/stream")
    async def timeline_stream(request: Request, channels: str = "", max_events: int = 0):
        """
        Server-Sent Events of cue.scheduled, cue.cancelled and cue.ended, as
        /api/events/stream ("id: <boot_id>:<n>", "event: <type>", "data:
        <JSON>"). channels: comma-separated channel ids (none: every channel).
        """
        timeline = _timeline_reader(request)
        if streams["open"] >= MAX_STREAMS:
            raise HTTPException(status_code=503, detail="Too many event streams open")
        slot = take_stream_slot(request)
        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)

        async def collect(event):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

        from sonorium.web.personal_routes import audience_of
        sub = timeline.subscribe(collect, _channel_list(channels), owner=None, audience=audience_of(request),
                                 queue_size=2000)
        streams["open"] += 1

        async def generate():
            sent = 0
            try:
                yield "retry: 5000\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), KEEPALIVE_SECONDS)
                    except asyncio.TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    yield (f"id: {ev.stream_id(event)}\nevent: {event.type}\n"
                           f"data: {json.dumps(event.to_dict(), default=str)}\n\n")
                    sent += 1
                    if max_events and sent >= max_events:
                        return
            finally:
                sub.close()
                streams["open"] -= 1
                slots.give(slot)

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- Triggers ---

    @router.get("/plugins/{plugin_id}/triggers")
    async def list_triggers(plugin_id: str):
        """The plugin's triggers, their URLs and which tokens are set (tokens are never shown again)."""
        return _host().triggers.describe(_plugin(plugin_id))

    @router.post("/plugins/{plugin_id}/triggers/token")
    async def create_trigger_token(plugin_id: str, request: Request):
        """
        A new token ({} = the plugin's, {"trigger": id} = one trigger's),
        replacing the old one. The token is in this answer only.
        """
        plugin = _plugin(plugin_id)
        body = await _json(request) if (await request.body()) else {}
        trigger = body.get("trigger")
        triggers = host.triggers
        if trigger is None:
            scope = triggers.plugin_scope(plugin.id)
        else:
            if not isinstance(trigger, str) or triggers.find(plugin, trigger) is None:
                raise HTTPException(status_code=404, detail=f"No trigger '{trigger}'")
            scope = triggers.trigger_scope(plugin.id, trigger)
        return {"token": host.tokens.create(scope), "trigger": trigger}

    @router.delete("/plugins/{plugin_id}/triggers/token")
    async def revoke_trigger_token(plugin_id: str, trigger: Optional[str] = None):
        plugin = _plugin(plugin_id)
        triggers = host.triggers
        if trigger is not None and not TRIGGER_ID.match(trigger):
            raise HTTPException(status_code=400, detail="Invalid trigger id")
        scope = triggers.plugin_scope(plugin.id) if trigger is None else triggers.trigger_scope(plugin.id, trigger)
        return {"revoked": host.tokens.revoke(scope)}

    @router.api_route("/triggers/{plugin_id}/{trigger}", methods=["GET", "POST"])
    async def call_trigger(plugin_id: str, trigger: str, request: Request):
        """Call a plugin's trigger (202 at once; the plugin's handler runs afterwards)."""
        _host()
        triggers = host.triggers
        client = request.client.host if request.client else "unknown"
        if not is_valid_plugin_id(plugin_id) or not TRIGGER_ID.match(trigger):
            raise HTTPException(status_code=400, detail="Invalid plugin or trigger id")
        blocked, wait = triggers.auth_limiter.blocked(client)
        if blocked:
            raise HTTPException(status_code=429, detail="Too many failed attempts",
                                headers={"Retry-After": str(int(wait) + 1)})
        # A key with "Run triggers" (or an admin); or the plugin's or trigger's own token
        if not _allowed(request, "tr.run") and not triggers.authenticate(plugin_id, trigger, token_from_request(request)):
            triggers.auth_limiter.allow(client)
            raise HTTPException(status_code=401, detail="A valid token is needed",
                                headers={"WWW-Authenticate": "Bearer"})
        plugin = plugin_manager.get_plugin(plugin_id)
        declared = triggers.find(plugin, trigger) if plugin is not None else None
        if declared is None:
            raise HTTPException(status_code=404, detail="No such trigger")
        if not plugin.enabled:
            raise HTTPException(status_code=409, detail="The plugin is not enabled")
        if request.method not in declared["methods"]:
            raise HTTPException(status_code=405, detail=f"Use {' or '.join(declared['methods'])}")
        allowed, wait = triggers.trigger_limiter.allow(f"{plugin_id}/{trigger}")
        if not allowed:
            raise HTTPException(status_code=429, detail="Too many calls",
                                headers={"Retry-After": str(int(wait) + 1)})
        payload = {k: v for k, v in request.query_params.items() if k.lower() != "token"}
        if request.method == "POST":
            payload.update(await _trigger_body(request))
        event_id = triggers.fire(plugin, declared, payload, request.method, client)
        return JSONResponse(status_code=202, content={"accepted": True, "event_id": event_id})

    async def _trigger_body(request: Request) -> dict:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > MAX_PAYLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
        body = await request.body()
        if len(body) > MAX_PAYLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")
        if not body.strip():
            return {}
        content_type = (request.headers.get("content-type") or "").lower()
        if "application/x-www-form-urlencoded" in content_type:
            return dict(parse_qsl(body.decode("utf-8", "replace")))
        try:
            data = json.loads(body)
        except ValueError:
            raise HTTPException(status_code=400, detail="The body must be a JSON object")
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="The body must be a JSON object")
        return data

    # --- Scheduler ---

    @router.get("/schedules")
    async def list_all_schedules():
        scheduler = _host().scheduler
        return {"timezone": scheduler.timezone_name, "timezone_effective": str(resolve_timezone(scheduler.timezone_name)),
                "jobs": scheduler.list()}

    @router.put("/schedules/settings")
    async def schedule_settings(request: Request):
        """{"timezone": "Europe/London"} ("" = the container's / system's)."""
        body = await _json(request)
        try:
            name = _host().scheduler.set_timezone(body.get("timezone") or "")
        except ScheduleError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"timezone": name, "timezone_effective": str(resolve_timezone(name))}

    @router.get("/plugins/{plugin_id}/schedules")
    async def list_plugin_schedules(plugin_id: str):
        plugin = _plugin(plugin_id)
        return {"plugin_id": plugin.id, "jobs": host.scheduler.list(plugin.id)}

    @router.post("/plugins/{plugin_id}/schedules", status_code=201)
    async def add_plugin_schedule(plugin_id: str, request: Request):
        """{"id", "kind", "spec", "name", "data", "missed", "timezone", "enabled"}: add or replace a job."""
        plugin = _plugin(plugin_id)
        body = await _json(request)
        try:
            return host.scheduler.add(plugin.id, body.get("id"), body.get("kind"), body.get("spec") or {},
                                      name=body.get("name") or "", data=body.get("data"),
                                      missed=body.get("missed") or "skip", enabled=body.get("enabled", True),
                                      timezone_name=body.get("timezone") or None)
        except ScheduleError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.delete("/plugins/{plugin_id}/schedules/{job_id}")
    async def remove_plugin_schedule(plugin_id: str, job_id: str):
        plugin = _plugin(plugin_id)
        if not host.scheduler.remove(plugin.id, job_id):
            raise HTTPException(status_code=404, detail="No such job")
        host.set_job_callback(plugin.id, job_id, None)
        return {"removed": True}

    @router.put("/plugins/{plugin_id}/schedules/{job_id}/enabled")
    async def enable_plugin_schedule(plugin_id: str, job_id: str, request: Request):
        plugin = _plugin(plugin_id)
        body = await _json(request)
        if not isinstance(body.get("enabled"), bool):
            raise HTTPException(status_code=400, detail="'enabled' must be true or false")
        job = host.scheduler.set_enabled(plugin.id, job_id, body["enabled"])
        if job is None:
            raise HTTPException(status_code=404, detail="No such job")
        return job

    # --- Extension points ---

    def _connection_decl(plugin, connection_id: str) -> dict:
        _check_id(connection_id, "connection id")
        for decl in normalize_connection_decls(plugin.get_connections(), plugin.id):
            if decl["id"] == connection_id:
                return decl
        raise HTTPException(status_code=404, detail=f"No connection '{connection_id}'")

    def _status(plugin, connection_id: str) -> dict:
        try:
            return normalize_status(plugin.connection_status(connection_id))
        except Exception as e:
            return {"state": "error", "message": str(e)[:300]}

    @router.get("/extensions")
    async def list_extensions():
        """What enabled plugins add to the UI: left-menu pages and Settings > Connections sections."""
        _host()
        pages, connections = [], []
        for plugin in plugin_manager.plugins.values():
            if not plugin.enabled:
                continue
            try:
                pages.extend({**p, "plugin_name": plugin.name} for p in normalize_page_decls(plugin.get_pages(), plugin.id))
                for decl in normalize_connection_decls(plugin.get_connections(), plugin.id):
                    connections.append({**decl, "plugin_name": plugin.name, "status": _status(plugin, decl["id"]),
                                        "values": host.connections.public(plugin.id, decl["id"], decl["fields"])})
            except Exception as e:
                logger.warning(f"Plugin {plugin.id}: its extension points could not be read: {e}")
        return {"pages": pages, "connections": connections}

    def _page_decl(plugin, page_id: str) -> dict:
        _check_id(page_id, "page id")
        for decl in normalize_page_decls(plugin.get_pages(), plugin.id):
            if decl["id"] == page_id:
                return decl
        raise HTTPException(status_code=404, detail=f"No page '{page_id}'")

    async def _page(plugin, page_id: str) -> dict:
        raw = await _call_plugin(plugin, "page", plugin.get_page(page_id), PAGE_TIMEOUT)
        try:
            return normalize_page(raw, f"Plugin {plugin.id}")
        except ExtensionError as e:
            raise HTTPException(status_code=502, detail=f"The plugin's page is not valid: {e}")

    @router.get("/plugins/{plugin_id}/pages/{page_id}")
    async def get_page(plugin_id: str, page_id: str):
        plugin = _plugin(plugin_id, enabled=True)
        decl = _page_decl(plugin, page_id)
        page = await _page(plugin, page_id)
        return {**decl, **page, "title": page["title"] or decl["title"]}

    @router.post("/plugins/{plugin_id}/pages/{page_id}/actions/{action}")
    async def page_action(plugin_id: str, page_id: str, action: str, request: Request):
        """A page's button or row action: {"values": {field: value}, "row": row id or null}."""
        plugin = _plugin(plugin_id, enabled=True)
        _page_decl(plugin, page_id)
        _check_id(action, "action")
        body = await _json(request)
        page = await _page(plugin, page_id)
        if action not in page_actions(page):
            raise HTTPException(status_code=404, detail=f"The page has no action '{action}'")
        try:
            values = check_values(page_fields(page), body.get("values") or {})
        except ExtensionError as e:
            raise HTTPException(status_code=400, detail=str(e))
        row = body.get("row")
        if row is not None and not isinstance(row, (str, int)):
            raise HTTPException(status_code=400, detail="'row' must be a row id")
        result = await _call_plugin(plugin, "page_action",
                                    plugin.page_action(page_id, action, {"values": values, "row": row}),
                                    ACTION_TIMEOUT)
        return normalize_result(result)

    @router.get("/plugins/{plugin_id}/connections/{connection_id}")
    async def get_connection(plugin_id: str, connection_id: str):
        """The connection's fields, saved values (secrets only as "<name>_set") and status."""
        plugin = _plugin(plugin_id)
        decl = _connection_decl(plugin, connection_id)
        return {**decl, "values": host.connections.public(plugin.id, connection_id, decl["fields"]),
                "status": _status(plugin, connection_id) if plugin.enabled else {"state": "unknown", "message": ""}}

    @router.put("/plugins/{plugin_id}/connections/{connection_id}")
    async def put_connection(plugin_id: str, connection_id: str, request: Request):
        """
        {"values": {...}, "clear": [names]}: save some values. An empty secret
        keeps the saved one; "clear" removes fields (secrets included).
        """
        plugin = _plugin(plugin_id)
        decl = _connection_decl(plugin, connection_id)
        body = await _json(request)
        try:
            public = host.connections.update(plugin.id, connection_id, decl["fields"], body.get("values") or {},
                                             body.get("clear"))
        except ExtensionError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if plugin.enabled:
            values = host.connections.values(plugin.id, connection_id, decl["fields"])
            try:
                await _call_plugin(plugin, "connection_changed",
                                   plugin.on_connection_changed(connection_id, values), ACTION_TIMEOUT)
            except HTTPException:
                pass  # saved anyway; the error event says what the plugin made of it
        return {"values": public, "status": _status(plugin, connection_id) if plugin.enabled else
                {"state": "unknown", "message": ""}}

    @router.post("/plugins/{plugin_id}/connections/{connection_id}/test")
    async def test_connection(plugin_id: str, connection_id: str, request: Request):
        """Try the connection with the saved values and the (unsaved) ones sent: {"values": {...}}."""
        plugin = _plugin(plugin_id, enabled=True)
        decl = _connection_decl(plugin, connection_id)
        if not decl["test"]:
            raise HTTPException(status_code=404, detail="This connection has no test")
        body = await _json(request) if (await request.body()) else {}
        try:
            values = host.connections.merged(plugin.id, connection_id, decl["fields"], body.get("values") or {})
        except ExtensionError as e:
            raise HTTPException(status_code=400, detail=str(e))
        result = await _call_plugin(plugin, "test_connection", plugin.test_connection(connection_id, values),
                                    TEST_TIMEOUT)
        clean = normalize_result(result)
        clean.pop("refresh", None)
        return clean

    _add_audio_routes(router, plugin_manager, host, _host, _plugin, _json)
    _add_attribute_routes(router, _host, _json)
    _add_device_routes(router, _host, _json)
    _add_permission_routes(router, plugin_manager, _host)
    _add_plugin_http_routes(router, _host, _plugin, _call_plugin)
    from sonorium.web.ui_api import add_ui_routes
    add_ui_routes(router, plugin_manager, host)
    return router


# Who may read and change plugin attributes, by object kind (users: below)
ATTRIBUTE_READ = {"theme": "th.view", "track": "th.view", "file": "th.view", "preset": "th.view", "group": "th.view",
                  "channel": "ch.view", "speaker": "sp.view", "speaker_group": "sp.view", "area": "sp.view",
                  "sequence": "pl.view", "step": "pl.view", "playlist": "pl.view", "playlist_entry": "pl.view",
                  "key": "admin.keys", "connection": "admin.connection", "plugin": "admin.plugins",
                  "device": "sp.view"}
ATTRIBUTE_WRITE = {"theme": "th.edit", "track": "th.edit", "file": "th.edit", "preset": "th.edit", "group": "th.edit",
                   "channel": "ch.change", "speaker": "sp.set", "speaker_group": "sp.set", "area": "st.change",
                   "sequence": "pl.edit", "step": "pl.edit", "playlist": "pl.edit", "playlist_entry": "pl.edit",
                   "key": "admin.keys", "connection": "admin.connection", "plugin": "admin.plugins",
                   "device": "sp.set"}


def _add_attribute_routes(router, _host, _json) -> None:
    """Plugin attributes on core objects (plugins/attributes.py, core/plugin_data.py)."""
    from sonorium.auth.middleware import principal_of
    from sonorium.core.plugin_data import OBJECT_CLASSES, registry
    from sonorium.plugins.actions import ActionError

    def _ref(kind: str, request: Request) -> dict:
        if kind not in OBJECT_CLASSES:
            raise HTTPException(status_code=404, detail=f"Unknown object kind '{kind}' (one of {', '.join(OBJECT_CLASSES)})")
        params = request.query_params
        return {"kind": kind, "id": params.get("id") or "", "theme": params.get("theme")}

    def _check(request: Request, ref: dict, write: bool) -> None:
        """The caller's abilities for this kind of object (the route's own entry lets any of them in)."""
        principal = principal_of(request)
        kind = ref["kind"]
        if kind == "user":
            allowed = principal.can("admin.users") or (principal.can("self") and principal.user_id == ref["id"])
        else:
            allowed = principal.can((ATTRIBUTE_WRITE if write else ATTRIBUTE_READ)[kind])
            if allowed and kind == "channel":
                allowed = principal.can_channel(ref["id"])
        if not allowed:
            raise HTTPException(status_code=403, detail="Not allowed")

    def _guard(work):
        try:
            return work()
        except ActionError as e:
            raise HTTPException(status_code=ACTION_ERROR_STATUS.get(e.code, 400), detail=e.message)

    @router.get("/attributes")
    async def attribute_schemas():
        """Every installed plugin's declared attributes: {plugin_id: {class: {name: field}}}, and the size limit."""
        return {"classes": list(OBJECT_CLASSES), "limit": registry.limit(), "plugins": registry.declarations()}

    @router.get("/attributes/{kind}")
    async def get_attributes(kind: str, request: Request):
        """An object's plugin attributes ({plugin_id: {name: value}}); ?id=...&theme=... name the object."""
        ref = _ref(kind, request)
        _check(request, ref, write=False)
        service = _host().attributes
        return {"kind": kind, "id": ref["id"], "theme": ref["theme"], "plugins": _guard(lambda: service.get(ref))}

    @router.put("/attributes/{kind}/{plugin_id}")
    async def set_attributes(kind: str, plugin_id: str, request: Request):
        """
        Change one plugin's attributes on an object: body {name: value, or null
        to remove it}. Checked against the plugin's schema and the size limit
        (400 with every problem). Returns that plugin's values afterwards.
        """
        ref = _ref(kind, request)
        _check(request, ref, write=True)
        if not is_valid_plugin_id(plugin_id):
            raise HTTPException(status_code=400, detail="Invalid plugin id")
        body = await _json(request)
        service = _host().attributes
        values = _guard(lambda: service.set(ref, plugin_id, body))
        return {"kind": kind, "id": ref["id"], "theme": ref["theme"], "plugin_id": plugin_id, "values": values}


def _add_permission_routes(router, plugin_manager, _host) -> None:
    """What each plugin may do, and the user's approval (plugins/permissions.py)."""
    from sonorium.plugins.permissions import catalogue

    @router.get("/plugin-permissions")
    async def permission_catalogue():
        """Every permission a plugin can ask for, as the user is shown it."""
        return catalogue()

    def _plugin_or_waiting(plugin_id: str):
        """A loaded plugin or one waiting for approval (not loaded: none of its code has run)."""
        _host()
        if not is_valid_plugin_id(plugin_id):
            raise HTTPException(status_code=400, detail="Invalid plugin id")
        plugin = plugin_manager.find_plugin(plugin_id)
        if plugin is None:
            raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")
        return plugin

    @router.get("/plugins/{plugin_id}/permissions")
    async def plugin_permissions(plugin_id: str):
        plugin = _plugin_or_waiting(plugin_id)
        host = _host()
        return {**host.permissions.describe(plugin), "granted": host.permissions.granted(plugin.id)}

    @router.put("/plugins/{plugin_id}/permissions")
    async def approve_plugin_permissions(plugin_id: str, request: Request):
        """
        {"approve": true}: the user allows what the plugin asks for now, its
        Python packages too. Switching it on then installs them and loads it.
        """
        plugin = _plugin_or_waiting(plugin_id)
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict) or body.get("approve") is not True:
            raise HTTPException(status_code=400, detail='Send {"approve": true}')
        host = _host()
        plugin_manager.approve_plugin(plugin.id)
        return {**host.permissions.describe(plugin), "granted": host.permissions.granted(plugin.id)}


def _add_plugin_http_routes(router, _host, _plugin, _call_plugin) -> None:
    """Each plugin's own routes (plugins/http_routes.py): the sign-in checked each route's ability first."""
    from fastapi.responses import Response
    from sonorium.plugins.http_routes import HEADERS, LIMITS, match, response_parts

    @router.api_route("/plugins/{plugin_id}/http/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
    async def plugin_http(plugin_id: str, path: str, request: Request):
        plugin = _plugin(plugin_id)
        routes = _host().http_routes.get(plugin.id) if plugin.enabled else None
        found = match(routes or [], request.method, path)
        if found is None:
            raise HTTPException(status_code=404, detail="Not Found")
        route, params = found
        # The sign-in checked the route's ability; checked again here, so a path the
        # sign-in's table didn't match can never reach a route it would have refused
        from sonorium.auth.middleware import principal_of
        caller = principal_of(request)
        from sonorium.auth.permissions import RoutePermission
        if not RoutePermission(route["ability"]).allows(caller.perms):
            if caller.kind in ("anonymous", "open"):
                raise HTTPException(status_code=401, detail="Sign in first")
            raise HTTPException(status_code=403, detail="Not allowed")
        from sonorium import limits
        body = await limits.read_body(request, LIMITS["body_max_bytes"])
        data = None
        if body and "json" in (request.headers.get("content-type") or ""):
            try:
                data = json.loads(body)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid JSON body")
        info = {"method": request.method, "path": path, "params": params, "query": dict(request.query_params),
                "json": data, "body": body,
                "caller": {"kind": caller.kind, "user_id": getattr(caller, "user_id", None),
                           "role": getattr(caller, "role", None)}}
        result = await _call_plugin(plugin, f"http {route['id']}", plugin.http_request(route["id"], info),
                                    LIMITS["timeout"])
        try:
            status, content, ctype = response_parts(result)
        except ValueError as e:
            logger.warning(f"Plugin {plugin.id}: route {route['id']} answered badly: {e}")
            raise HTTPException(status_code=502, detail=f"The plugin's answer was refused: {e}")
        return Response(content=content, status_code=status, media_type=ctype, headers=dict(HEADERS))


def _add_device_routes(router, _host, _json) -> None:
    """Devices of any kind plugins bring (core/devices.py)."""
    from sonorium.core.devices import DeviceError

    def _svc():
        devices = getattr(_host(), "devices", None)
        if devices is None:
            raise HTTPException(status_code=503, detail="Devices are not available")
        return devices

    async def _guard(work):
        try:
            result = work()
            return (await result) if asyncio.iscoroutine(result) else result
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'\""))
        except DeviceError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/plugin-devices/kinds")
    async def device_kinds():
        """The kinds of device registered now (plugins switched on)."""
        return {"kinds": _svc().kinds()}

    @router.get("/plugin-devices")
    async def list_devices(kind: Optional[str] = None, plugin: Optional[str] = None):
        """The remembered devices of kinds registered now (?kind=, ?plugin=)."""
        return {"devices": _svc().list(kind, plugin)}

    @router.post("/plugin-devices/discover")
    async def discover_devices(kind: Optional[str] = None):
        """Ask the plugins for their devices again (one kind, or every kind)."""
        return {"devices": await _guard(lambda: _svc().discover(kind))}

    @router.put("/plugin-devices/{device_id}")
    async def update_device(device_id: str, request: Request):
        """{name (null: the plugin's), area ("" none), use, channels, areas}: the user's choices for a device."""
        body = await _json(request)
        svc = _svc()
        settings = {k: body[k] for k in ("name", "area", "use") if k in body}
        unknown = set(body) - {"name", "area", "use", "channels", "areas"}
        if unknown:
            raise HTTPException(status_code=400, detail=f"Unknown device setting: {', '.join(sorted(unknown))}")
        device = await _guard(lambda: svc.update(device_id, settings) if settings else svc.get(device_id))
        if "channels" in body or "areas" in body:
            device = await _guard(lambda: svc.assign(device_id, body.get("channels"), body.get("areas")))
        return device

    @router.delete("/plugin-devices/{device_id}")
    async def remove_device(device_id: str):
        await _guard(lambda: _svc().remove(device_id))
        return {"removed": device_id}

    @router.post("/plugin-devices/{device_id}/actions/{action}")
    async def device_action(device_id: str, action: str, request: Request):
        """One of the device's kind's actions (Test, Identify, ...): {"success", "message"}."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        params = body.get("params") if isinstance(body, dict) else None
        return await _guard(lambda: _svc().action(device_id, action, params))

    @router.get("/sessions/{session_id}/plugin-devices")
    async def channel_devices(session_id: str, kind: Optional[str] = None):
        """The devices following a channel: assigned to it, or in an area of its speakers."""
        return {"devices": _svc().assigned(session_id, kind)}


def _add_audio_routes(router, plugin_manager, host, _host, _plugin, _json) -> None:
    """Sounds, announcements, ramps, audio outputs, command maps, alerts (framework phase 2)."""
    from sonorium.core.announce import AnnouncementsOff, TtsUnavailable
    from sonorium.core.audio_outputs import OutputBusy, OutputError, OutputFailed
    from sonorium.core.decode import DecodeError
    from sonorium.core.overlay import OverlayError
    from sonorium.core.ramps import RampError
    from sonorium.plugins import commands as cmd
    from sonorium.plugins import compat
    from sonorium.plugins.tts import TtsError

    def _svc(name: str, what: str):
        service = getattr(_host(), name)
        if service is None:
            raise HTTPException(status_code=503, detail=f"{what} isn't available yet")
        return service

    def _channels(value, personal: bool = False) -> list:
        """
        Channel ids from ids or names (one, a list, or "all"); 404 names an
        unknown one. personal: "all" also takes the personal channels being
        listened to (announcements).
        """
        from sonorium.core.announce import resolve_channels
        if value is None:
            raise HTTPException(status_code=400, detail="'channels' is needed: a channel name or id, a list, or \"all\"")
        try:
            return resolve_channels(getattr(_svc("overlay", "Playing sounds"), "sessions", None), value,
                                    personal=personal)
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e.args[0] if e.args else e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    def _number(body, key, default):
        value = body.get(key, default)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise HTTPException(status_code=400, detail=f"'{key}' must be a number")
        return value

    async def _guard(work):
        """Run a coroutine (or a function, sync or async), its errors as HTTP answers."""
        import inspect
        try:
            result = work() if callable(work) else work
            return (await result) if inspect.isawaitable(result) else result
        except LookupError as e:
            raise HTTPException(status_code=404, detail=str(e).strip("'\""))
        except AnnouncementsOff as e:
            raise HTTPException(status_code=409, detail=str(e))
        except TtsUnavailable as e:
            raise HTTPException(status_code=503, detail=str(e))
        except (OverlayError, DecodeError, RampError, OutputError, cmd.CommandError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        except TtsError as e:
            raise HTTPException(status_code=502, detail=str(e))

    async def _output_guard(work):
        """
        _guard for the audio-output routes, which run a plugin's code: its
        ActionError as the matching status with its message, anything else
        as 502 "<type title>: <message>", never a bare 500.
        """
        from sonorium.plugins.actions import ActionError
        try:
            return await _guard(work)
        except HTTPException:
            raise
        except ActionError as e:
            raise HTTPException(status_code=ACTION_ERROR_STATUS.get(e.code, 400), detail=e.message)
        except OutputFailed as e:
            raise HTTPException(status_code=502, detail=str(e))
        except OutputBusy as e:
            raise HTTPException(status_code=409, detail=e.message,
                                headers={"X-Sonorium-Take-Over": "ask"} if e.take_over else None)
        except Exception as e:
            logger.error(f"Audio output failed: {e}", exc_info=e)
            raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {str(e) or type(e).__name__}")

    # --- Framework ---

    @router.get("/plugin-framework")
    async def framework_info():
        """The plugin API version, every capability, and which this install offers now."""
        info = compat.describe()
        info["offered"] = sorted(_host().capabilities())
        info["tts_providers"] = host.tts.providers()
        return info

    @router.get("/plugins/{plugin_id}/packages")
    async def plugin_packages(plugin_id: str):
        """A plugin's Python packages: what it asks for, whether they're allowed and installed."""
        plugin_id_ok = plugin_manager.find_plugin(plugin_id) or plugin_manager.refused.get(plugin_id)
        if plugin_id_ok is None:
            raise HTTPException(status_code=404, detail=f"Plugin not found: {plugin_id}")
        plugin = plugin_manager.find_plugin(plugin_id)
        manifest = plugin._manifest if plugin is not None else {}
        if plugin is None:
            from sonorium.plugins.loader import load_manifest
            manifest = load_manifest(plugin_manager.plugins_dir / plugin_id)
        return _host().packages.status(plugin_id, manifest)

    # --- Alerts (the web UI's alert bar) ---

    @router.get("/alerts")
    async def list_alerts():
        from sonorium import alerts, logbuffer
        from sonorium.auth.principal import current_caller, sees_private
        active = alerts.active()
        if not sees_private(current_caller()):  # addresses only for admins, as on the Logs page
            active = [{**a, "message": logbuffer.hide_addresses(a["message"])} for a in active]
        return {"alerts": active}

    @router.post("/alerts/{alert_id}/dismiss")
    async def dismiss_alert(alert_id: int):
        from sonorium import alerts
        if not alerts.dismiss(alert_id):
            raise HTTPException(status_code=404, detail="No such alert")
        return {"dismissed": True}

    # --- Sounds and announcements ---

    @router.post("/announce")
    async def announce(request: Request):
        """
        Other systems announce through Sonorium (a key with Make announcements, or signed in):
        {"channels": "Kitchen" | ["Kitchen", "office"] | "all", "text": "Dinner is ready",
         "url": "https://.../chime.mp3", "volume": 0-200, "duck": true | false | dB | {db, attack, release},
         "provider", "language", "start_stopped"}. Only channels and text (or url) are needed.
        A chime and/or spoken text, ducking the channels' mix (a stopped channel is started for it).
        Answers which channels play it and which skipped it (Announcements off).
        409 while Announcements are switched off in Settings > Advanced.
        """
        from sonorium.core.announce import announce as do_announce, check_on, parse_duck
        from sonorium.auth.middleware import principal_of
        await _guard(check_on)
        body = await _json(request)
        text = body.get("text", body.get("message"))
        url = body.get("url", body.get("chime"))
        if text is not None and not isinstance(text, str):
            raise HTTPException(status_code=400, detail="'text' must be text")
        if url is not None and (not isinstance(url, str) or not url.lower().startswith(("http://", "https://"))):
            raise HTTPException(status_code=400, detail="'url' must be an http(s) URL")
        if not (text or "").strip() and not url:
            raise HTTPException(status_code=400, detail="Send 'text' to speak, a chime 'url', or both")
        overlay = _svc("overlay", "Playing sounds")
        caller = principal_of(request)
        wanted = body.get("channels")
        channels = _channels(wanted, personal=caller.channels is None)
        if caller.channels is not None:  # a key limited to some channels
            if wanted in (None, "all", "*", ["all"], ["*"]):
                channels = [c for c in channels if caller.can_channel(c)]
            elif not all(caller.can_channel(c) for c in channels):
                raise HTTPException(status_code=403, detail="Not allowed for this channel")
        if not channels:
            raise HTTPException(status_code=404, detail="No channel to announce on")
        try:
            duck = parse_duck(body.get("duck"))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return await _guard(do_announce(
            overlay, host.tts, channels, text=(text or "").strip() or None, sound=url,
            volume=_number(body, "volume", 100), duck=duck, provider=body.get("provider"),
            language=body.get("language"), start_stopped=bool(body.get("start_stopped", True)), origin="api"))

    @router.get("/sounds")
    async def list_sounds():
        """Sounds playing, waiting or queued on every channel."""
        return {"sounds": _svc("overlay", "Playing sounds").sounds()}

    @router.post("/sounds")
    async def play_sound(request: Request):
        """
        {"channels", "url", "volume", "duck", "policy", "priority", "max_seconds",
         "start_stopped", "override", "label", "announcement"}: a sound over the channels' mix
        ("announcement": true skips channels with announcements off).
        """
        body = await _json(request)
        url = body.get("url")
        if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="'url' must be an http(s) URL")
        priority = body.get("priority", 0)
        return await _guard(_svc("overlay", "Playing sounds").play(
            _channels(body.get("channels")), url, volume=_number(body, "volume", 100), duck=body.get("duck", True),
            policy=body.get("policy", "queue"), priority=priority, max_seconds=_number(body, "max_seconds", None),
            start_stopped=bool(body.get("start_stopped", False)), override=bool(body.get("override", False)),
            label=str(body.get("label") or "")[:80], origin="api", announcement=bool(body.get("announcement", False))))

    @router.delete("/sounds/{sound_id}")
    async def cancel_sound(sound_id: str):
        if not _svc("overlay", "Playing sounds").cancel(sound_id):
            raise HTTPException(status_code=404, detail="No such sound playing or queued")
        return {"cancelled": True}

    # --- Ramps ---

    @router.get("/sessions/{session_id}/ramp")
    async def ramp_status(session_id: str):
        return await _guard(lambda: (_svc("ramps", "Volume ramps").status(session_id)))

    @router.post("/sessions/{session_id}/ramp")
    async def ramp_start(session_id: str, request: Request):
        """{"to": 0-1, "seconds", "from", "curve": linear|db|ease|exp, "then": stop|pause, "start": bool}"""
        body = await _json(request)
        return await _guard(_svc("ramps", "Volume ramps").start(
            session_id, _number(body, "to", None), _number(body, "seconds", None),
            from_level=_number(body, "from", None), curve=body.get("curve", "db"), then=body.get("then"),
            start=bool(body.get("start", False)), origin="api"))

    @router.delete("/sessions/{session_id}/ramp")
    async def ramp_cancel(session_id: str, reset: bool = False):
        result = await _guard(lambda: (_svc("ramps", "Volume ramps").cancel(session_id, reset)))
        return {"cancelled": result is not None, **(result or {})}

    # --- Audio outputs ---

    @router.get("/audio-outputs")
    async def output_types():
        return {"types": _svc("outputs", "Audio outputs").types()}

    @router.post("/audio-outputs/discover")
    async def discover_outputs():
        """
        Every type lists its devices again (Settings > Speakers opened, Rescan,
        the channel editor's speaker picker): {"types": [type + "error",
        "speakers": [the type's device speakers as GET /api/speakers lists them]]}.
        """
        outputs = _svc("outputs", "Audio outputs")
        types = await outputs.discover_all()
        registry = getattr(getattr(outputs, "sessions", None), "registry", None)
        for info in types:
            speakers = [registry.get_speaker(sid) if registry is not None else None for sid in info["speakers"]]
            info["speakers"] = [s.to_dict() for s in speakers if s is not None]
        return {"types": types}

    @router.delete("/audio-outputs/speakers/{speaker_id}")
    async def remove_output_device(speaker_id: str):
        """Remove on an offline device's row: forgotten, and taken out of the channels that have it."""
        outputs = _svc("outputs", "Audio outputs")
        await _output_guard(lambda: outputs.remove_device(speaker_id))
        return {"removed": True}

    @router.get("/audio-outputs/{type_id}/devices")
    async def output_devices(type_id: str):
        return {"devices": await _output_guard(lambda: _svc("outputs", "Audio outputs").discover(type_id))}

    @router.get("/sessions/{session_id}/audio-outputs")
    async def channel_outputs(session_id: str):
        return {"attachments": _svc("outputs", "Audio outputs").attachments(session_id)}

    @router.post("/sessions/{session_id}/audio-outputs", status_code=201)
    async def attach_output(session_id: str, request: Request):
        """
        {"type", "device", "options"?, "take_over"?}: add the device to the
        channel's speakers; it plays at once if the channel is playing.
        """
        body = await _json(request)
        take_over = body.get("take_over", False)
        if not isinstance(take_over, bool):
            raise HTTPException(status_code=400, detail="'take_over' must be true or false")
        return await _output_guard(lambda: (_svc("outputs", "Audio outputs").attach(
            session_id, body.get("type"), body.get("device"), body.get("options"), take_over=take_over)))

    @router.delete("/sessions/{session_id}/audio-outputs/{attachment_id}")
    async def detach_output(session_id: str, attachment_id: str):
        if not await _output_guard(lambda: _svc("outputs", "Audio outputs").detach(session_id, attachment_id)):
            raise HTTPException(status_code=404, detail="No such output on this channel")
        return {"removed": True}

    # --- Command maps ---

    @router.get("/command-actions")
    async def command_actions():
        """What a command map entry can do (Sonorium's actions, then the plugins')."""
        return {"actions": cmd.catalogue(getattr(_host(), "automation", None))}

    @router.get("/command-conditions")
    async def command_conditions():
        """What an entry's "only if" can check (Sonorium's conditions, then the plugins')."""
        return {"conditions": cmd.conditions(getattr(_host(), "automation", None))}

    def _map_decl(plugin, map_id: str) -> dict:
        for decl in plugin.get_command_maps() or []:
            if isinstance(decl, dict) and decl.get("id") == map_id:
                return decl
        raise HTTPException(status_code=404, detail=f"No command map '{map_id}'")

    @router.get("/plugins/{plugin_id}/command-maps")
    async def command_maps(plugin_id: str):
        plugin = _plugin(plugin_id)
        maps = []
        for decl in plugin.get_command_maps() or []:
            if isinstance(decl, dict) and cmd.MAP_ID.match(str(decl.get("id", ""))):
                maps.append({"id": decl["id"], "title": str(decl.get("title") or decl["id"]),
                             "codes": decl.get("codes") or [],
                             "entries": host.commands.entries(plugin.id, decl["id"])})
        return {"plugin_id": plugin.id, "maps": maps}

    @router.put("/plugins/{plugin_id}/command-maps/{map_id}")
    async def put_command_map(plugin_id: str, map_id: str, request: Request):
        """{"entries": [{"code", "label", "action", "channel", "args", "enabled"}]}: replace the map."""
        plugin = _plugin(plugin_id)
        _map_decl(plugin, map_id)
        body = await _json(request)
        return {"entries": await _guard(lambda: (host.commands.set_entries(plugin.id, map_id, body.get("entries"))))}

    @router.post("/plugins/{plugin_id}/command-maps/{map_id}/learn")
    async def learn_command(plugin_id: str, map_id: str, seconds: float = 20.0):
        """Wait for the next code the plugin receives on this map: {"code"} (null if none came in time)."""
        plugin = _plugin(plugin_id, enabled=True)
        _map_decl(plugin, map_id)
        return {"code": await host.commands.learn(plugin.id, map_id, seconds)}

    @router.post("/plugins/{plugin_id}/command-maps/{map_id}/test")
    async def test_command(plugin_id: str, map_id: str, request: Request):
        """{"code", "value"}: act as if the plugin received the code."""
        plugin = _plugin(plugin_id, enabled=True)
        _map_decl(plugin, map_id)
        body = await _json(request)
        if not isinstance(body.get("code"), str):
            raise HTTPException(status_code=400, detail="'code' must be text")
        return await host.commands.received(plugin.id, map_id, body["code"], host.actions.for_plugin(plugin.id),
                                            body.get("value"))
