"""
The core's sign-in routes (Users and Keys add theirs: users.py, keys.py).

    GET    /api/info                     public: server id, version, ports, login required, mDNS name
    GET    /api/auth/me                  public: who you are, CSRF token, bypass, plugins' fields
    GET    /api/ui/plugins               public: the built-in plugins' web UI bundles
    GET    /plugin-ui/{plugin_id}/{path} public: a file of such a bundle
    POST   /api/link                     a Link App QR code (bound to you when you're signed in)
    GET    /api/link/{link_id}           the Link App dialog: has its code linked a phone yet
    POST   /api/pair                     public, HTTPS: one-time code -> phone token (shown once)
    GET    /api/devices                  linked phones: yours, or everyone's for an admin
    GET    /api/devices/self             a linked phone: its own record
    PUT    /api/devices/self             a linked phone: rename itself
    DELETE /api/devices/self             a linked phone: unlink itself (its token stops working at once)
    DELETE /api/devices/{device_id}      unlink a phone (takes effect on its next request)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Callable, Optional
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from sonorium import runtime
from sonorium.auth import config
from sonorium.auth.middleware import principal_of
from sonorium.auth.scopes import scopes_of
from sonorium.auth.ratelimit import RateLimiter
from sonorium.auth.service import AuthService
from sonorium.obs import logger as _logger

# "sonorium.auth": names users and addresses, so only admins see it on the Logs page (logbuffer.py)
logger = _logger.getChild("auth")

API_VERSION = 1
UI_HOST_API = 1  # the web UI's window.SONORIUM_HOST contract (static/js/api.js SONORIUM_HOST_API)
PAIR_RATE, PAIR_BURST = 5 / 60, 5  # pairing: 5 attempts a minute per address
SERVER_NAME = "Sonorium"  # the name when Settings > Advanced > Server name is empty (servername.DEFAULT)


def server_name() -> str:
    """The server's name (Settings > Advanced > Server name, "Sonorium" when empty):
    GET /api/info "name" and the mDNS TXT name (announce.py) both read it."""
    from sonorium import servername
    return servername.get()


def client_of(request: Request) -> str:
    return request.client.host if request.client else "unknown"


async def json_body(request: Request) -> dict:
    """
    The request's JSON object, at most auth_body_limit_kb read (413 beyond):
    sign-in routes are open to anyone, so a body is never read without a limit.
    """
    from sonorium import limits
    return await limits.read_json_object(request, limits.size(limits.AUTH_BODY))


def limited(limiter: RateLimiter, key: str, spend: bool) -> None:
    """429 when the key is over its limit; spend=True counts this attempt."""
    blocked, wait = limiter.blocked(key)
    if not blocked and spend:
        allowed, wait = limiter.allow(key)
        blocked = not allowed
    if blocked:
        raise HTTPException(429, "Too many attempts. Try again in a minute.",
                            headers={"Retry-After": str(int(wait) + 1)})


def public_address(url: str) -> Optional[dict]:
    """
    A configured address clients reach Sonorium at from anywhere (a reverse
    proxy's, e.g. https://demo.example): {"url", "scheme", "host", "port"}.
    None for "auto", nothing, or a LAN address (a private IP or a .local
    name over http), which works as before.
    """
    import ipaddress
    text = (url or "").strip()
    if not text or text.lower() == "auto":
        return None
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname
    if parts.scheme == "http":
        if host.endswith(".local") or host == "localhost":
            return None
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return None
        except ValueError:
            pass
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return None
    shown = f"[{host}]" if ":" in host else host
    default = 443 if parts.scheme == "https" else 80
    origin = f"{parts.scheme}://{shown}" + ("" if port == default else f":{port}")
    return {"url": origin, "scheme": parts.scheme, "host": host, "port": port}


def capabilities(service: AuthService, features: dict, *, plugins: bool) -> list[str]:
    """
    The "capabilities" list of /api/info: what this server offers, so the app hides
    the rest. It describes the server, not the caller (permissions stay per route).
    """
    from sonorium.auth import keys
    from sonorium.auth.users import ZONES_STATE

    def on(name: str) -> bool:
        return bool((features.get(name) or {}).get("enabled"))

    zones = service.plugin_state.get(ZONES_STATE)
    caps = ["channels.create", "speakers", "speaker_groups", "spaces", "audio_settings", "advanced",
            "logs", "status", "listen", "theme_packages", "theme_backup"]
    caps += [name for name, present in (
        ("connection", on("connection_settings")),  # Home Assistant and MQTT settings (not in the HA app)
        ("plugins", plugins),  # the plugin system started
        ("users", service.accounts_loaded),  # Users & devices
        ("keys", keys.enabled(service)),  # Settings > Advanced > Keys is on
        ("zones", bool(zones is not None and zones.enabled)),  # Settings > Advanced > Zones is on
    ) if present]
    return sorted(caps)


def create_core_auth_router(service: AuthService, *, stream_url: Callable[[], str],
                            features: Callable[[], dict], version: str,
                            https_host_port: Callable[[], int],
                            mdns_host: Callable[[], Optional[str]] = lambda: None,
                            plugins_available: Callable[[], bool] = lambda: False,
                            public_url: Callable[[], Optional[str]] = lambda: None,
                            https_listen_port: Callable[[], int] = lambda: 0) -> APIRouter:
    """
    public_url: the Stream URL as configured (SONORIUM__STREAM_URL or the
    setting; None for "auto"). When it is a public address (public_address),
    /api/info and Link App codes give that address, not the server's own.
    """
    router = APIRouter(tags=["auth"])
    pair_limiter = RateLimiter(PAIR_RATE, PAIR_BURST)

    def public() -> Optional[dict]:
        try:
            return public_address(public_url() or "")
        except Exception:
            return None

    def link_address() -> tuple[str, int]:
        """(host, HTTP port) clients are given: the public address's, else the stream URL's."""
        pub = public()
        if pub is not None:
            return pub["host"], pub["port"] if pub["scheme"] == "http" else runtime.stream_port()
        url = urlsplit(stream_url() or "")
        return url.hostname or "", url.port or (443 if url.scheme == "https" else runtime.stream_port())

    def https_port() -> int:
        """The HTTPS port clients are given: the public address's (https), else the server's as the LAN sees it."""
        pub = public()
        if pub is not None and pub["scheme"] == "https":
            return pub["port"]
        return https_host_port() if service.identity.tls_ready else 0

    def base_url(host: str, http: int) -> str:
        """The one address to reach the API at: the public one, else the server's HTTPS (or HTTP) address."""
        pub = public()
        if pub is not None:
            return pub["url"]
        shown = f"[{host}]" if ":" in host else host
        return f"https://{shown}:{https_port()}" if https_port() else f"http://{shown}:{http}"

    def login_required() -> bool:
        return service.login_required()

    def link_hosts(host: str) -> list[str]:
        """For the QR code: the speakers' address first, the server's other LAN addresses, then sonorium.local."""
        from sonorium.auth.announce import lan_addresses
        hosts = lan_addresses(host)
        name = mdns_host()
        if name and name not in hosts:
            hosts.append(name)
        return hosts

    service.https_only = lambda request: _https_only(service, request, https_port)
    service.link_address = link_address

    @router.get("/api/info")
    async def info():
        """Public: what this server is, for the app (works before linking)."""
        host, http = link_address()
        identity = service.identity
        feats = features()
        return {
            "id": identity.server_id, "name": server_name(), "version": version, "api": API_VERSION,
            "install": runtime.INSTALL, "features": feats,
            "capabilities": capabilities(service, feats, plugins=bool(plugins_available())),
            "ports": {"http": http, "https": https_port() or None}, "host": host, "hostname": mdns_host() or None,
            # The address to use (a reverse proxy's when one is set up), and the ports Sonorium itself listens on
            "url": base_url(host, http), "public_url": (public() or {}).get("url"),
            "internal_ports": {"http": runtime.http_port(), "https": (https_listen_port() or None)
                               if service.identity.tls_ready else None},
            "login_required": login_required(), "pair": True, "fingerprint": identity.fingerprint,
            "plugins": service.plugin_ids(),  # "users" kept for released apps; use "accounts"
            "accounts": service.accounts_loaded,  # user accounts: sign in with a username and password
            "started_at": datetime.fromtimestamp(runtime.STARTED_AT, timezone.utc).isoformat(timespec="seconds"),  # UTC
            "boot_id": runtime.BOOT_ID,  # new at every start; the event stream's ids are "<boot_id>:<n>"
            # the web UI's version, and of its window.SONORIUM_HOST hooks for the app (static/js/api.js)
            "ui": {"version": version, "host_api": UI_HOST_API},
            # GET/PUT /api/me/channel and its listen routes exist (web/personal_routes.py)
            "personal_channel": True,
        }

    @router.get("/api/auth/me")
    async def me(request: Request):
        p = principal_of(request)
        ingress = p.listener == "ingress"
        body = {
            "kind": p.kind, "user": service.describe_user(p.user_id), "csrf": p.csrf, "reset": p.reset,
            "perms": sorted(p.perms), "scopes": scopes_of(p),  # e.g. {"channels": [ids]} (auth/scopes.py)
            "bypass": config.bypass_login() and not ingress, "ingress": ingress,
            "web_ui": ingress or not config.web_ui_only_through_ha(), "words": service.identity.words,
            "plugins": service.plugin_ids(), "accounts": service.accounts_loaded,
            "starting": not service.ready, "closed": service.closed,
        }
        for _, hook in service.provider("me"):
            try:
                body.update(hook(None, p) or {})
            except Exception as e:
                logger.warning(f"Sign-in: a plugin's /api/auth/me fields failed: {e}")
        return body

    @router.get("/api/ui/plugins")
    async def ui_plugins():
        """The built-in plugins' web UI bundles (JS and CSS) to load, in order."""
        out = []
        for plugin_id, b in service.ui_bundles.items():
            if not b.get("active"):
                continue
            stamp = f"?v={b.get('version') or ''}"
            out.append({"id": plugin_id,
                        "js": f"/plugin-ui/{plugin_id}/{b['js']}{stamp}" if b.get("js") else None,
                        "css": f"/plugin-ui/{plugin_id}/{b['css']}{stamp}" if b.get("css") else None})
        return {"plugins": out}

    @router.get("/plugin-ui/{plugin_id}/{path:path}")
    async def plugin_ui_file(plugin_id: str, path: str):
        bundle = service.ui_bundles.get(plugin_id)
        if not bundle or not bundle.get("active"):
            raise HTTPException(404, "Not Found")
        root = bundle["folder"].resolve()
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file() or target.suffix not in (".js", ".css", ".svg", ".png"):
            raise HTTPException(404, "Not Found")
        response = FileResponse(target)
        response.headers["Cache-Control"] = "no-cache"
        return response

    @router.post("/api/link")
    async def link(request: Request):
        """A Link App QR code: works once, for 5 minutes. Signed in, it links the phone to you."""
        from sonorium.auth.qr import pair_uri, qr_svg
        p = principal_of(request)
        user = service.describe_user(p.user_id)
        identity = service.identity
        code, expires = service.new_pair_code(p.user_id if user else None)
        host, http = link_address()
        pub = public()
        # Behind a reverse proxy: only its address (the server's own don't reach the phone)
        hosts = [host] if pub is not None else await asyncio.to_thread(link_hosts, host)
        uri = pair_uri(server_id=identity.server_id, name="Sonorium", hosts=hosts,
                       http_port=http, https_port=https_port(), fingerprint=identity.fingerprint or "",
                       code=code, username=user["username"] if user else None,
                       url=pub["url"] if pub is not None else None)
        logger.info(f"Link App: a code{' for ' + user['username'] if user else ''} (works once, for 5 minutes)")
        return {"uri": uri, "qr_svg": qr_svg(uri), "code": code, "username": user["username"] if user else None,
                "expires_in": int(expires - service.clock()), "words": identity.words,
                "host": host, "hosts": hosts, "http_port": http, "https_port": https_port() or None,
                "url": base_url(host, http), "public_url": pub["url"] if pub is not None else None,
                "link_id": service.pair_link_of(code)}

    @router.get("/api/link/{link_id}")
    async def link_state(link_id: str, request: Request):
        """The Link App dialog: has its code linked a phone yet ("waiting", "linked" with the device, "expired")?"""
        p = principal_of(request)
        found = service.pair_link_state(link_id)
        if found is None or (found["user_id"] != (p.user_id if service.describe_user(p.user_id) else None)
                             and not p.can("admin.users")):
            raise HTTPException(404, "No such code")
        return {"state": found["state"], "device": found["device"]}

    @router.post("/api/pair")
    async def pair(request: Request):
        """The app: the QR code's one-time code -> a phone token (shown once)."""
        client = client_of(request)
        limited(pair_limiter, client, spend=True)
        service.https_only(request)
        data = await json_body(request)
        valid, user_id, link = service.use_pair_code(str(data.get("code") or ""))
        if not valid:
            logger.warning(f"Link App: a device at {client} tried a wrong or expired code")
            raise HTTPException(401, "This code doesn't work. Show a new code in Sonorium.")
        device_id, token = service.add_device(user_id, str(data.get("device_name") or ""),
                                              str(data.get("platform") or ""), str(data.get("model") or ""))
        service.pair_linked(link, device_id)
        user = service.describe_user(user_id)
        logger.info(f"Link App: {data.get('device_name') or 'a device'} at {client} is linked"
                    f"{' to ' + user['username'] if user else ''}")
        return {"device_id": device_id, "token": token, "user": user, "server_id": service.identity.server_id}

    @router.get("/api/devices")
    async def devices(request: Request):
        """Linked phones: yours, or everyone's for an admin (or with no accounts)."""
        p = principal_of(request)
        rows = service.devices() if p.can("admin.users") else service.devices(p.user_id)
        for row in rows:
            user = service.describe_user(row["user_id"])
            row["username"] = user["username"] if user else None
        return {"devices": rows}

    # A linked phone's own record (before /api/devices/{device_id}, which would take "self")

    def own_device(request: Request) -> dict:
        p = principal_of(request)
        row = service.device(p.device_id) if p.kind == "device" else None
        if row is None:
            raise HTTPException(404, "No such device")
        return row

    @router.get("/api/devices/self")
    async def get_own_device(request: Request):
        """The phone calling: its record (name, platform, model, linked, last seen)."""
        return own_device(request)

    @router.put("/api/devices/self")
    async def rename_own_device(request: Request):
        """The phone calling: a new name ({"device_name": "..."}), as shown in Users & devices."""
        row = own_device(request)
        data = await json_body(request)
        name = data.get("device_name", data.get("name"))
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(400, "A device_name is needed")
        return service.rename_device(row["id"], name)

    @router.delete("/api/devices/self")
    async def remove_own_device(request: Request):
        """The phone calling unlinks itself: its token stops working at once."""
        row = own_device(request)
        service.remove_device(row["id"])
        logger.info(f"Devices: {row['name']} unlinked itself")
        return {"removed": True}

    @router.delete("/api/devices/{device_id}")
    async def remove_device(device_id: str, request: Request):
        """Unlink a phone: yours, or anyone's for an admin (a phone: only itself). Its next request is refused."""
        p = principal_of(request)
        record = service.tokens.get(device_id)
        if record is None or record.get("kind") != "device":
            raise HTTPException(404, "No such device")
        if not p.can("self") and device_id != p.device_id:
            raise HTTPException(403, "Not allowed")
        if record.get("user_id") != p.user_id and not p.can("admin.users"):
            raise HTTPException(403, "Not allowed")
        service.remove_device(device_id)
        logger.info(f"Devices: {record.get('name') or 'a device'} unlinked")
        return {"removed": True}

    return router


def _https_only(service: AuthService, request: Request, https_port: Callable[[], int]) -> None:
    """Handing out a token: only over HTTPS (unless the HTTPS port can't be reached)."""
    if request.url.scheme != "https" and https_port():
        raise HTTPException(403, "Use the HTTPS port to link a device.")
