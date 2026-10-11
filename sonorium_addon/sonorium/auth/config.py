"""
Switches read from the environment each time they're used (so tests can change them).

    BYPASS_LOGIN=true                    Docker: no sign-in in the web UI (recovery)
    SONORIUM__BYPASS_LOGIN               the add-on option bypass_login (run.sh / options.json)
    SONORIUM__WEB_UI_ONLY_THROUGH_HA     add-on option web_ui_only_through_ha
    SONORIUM_HTTPS_PORT                  the HTTPS port (default 8808; always on)
    SONORIUM_CONFIG_DIR                  Sonorium's config folder (default /config/sonorium)
"""
import os
from pathlib import Path

from sonorium import runtime

_TRUE = {"1", "true", "yes", "on"}

# Home Assistant's Supervisor: the only address the ingress proxy (HA's sidebar) calls from
SUPERVISOR_IP = "172.30.32.2"
DEFAULT_HTTPS_PORT = 8808
DEFAULT_INGRESS_PORT = 8099


def _flag(*names: str) -> bool:
    return any((os.environ.get(n) or "").strip().lower() in _TRUE for n in names)


def bypass_login() -> bool:
    """The recovery override: the web UI needs no sign-in while it's on."""
    return _flag("BYPASS_LOGIN", "SONORIUM__BYPASS_LOGIN")


def web_ui_only_through_ha() -> bool:
    """Add-on only: the direct ports serve streams and the key/device API, not the web UI."""
    return not runtime.STANDALONE and _flag("SONORIUM__WEB_UI_ONLY_THROUGH_HA", "WEB_UI_ONLY_THROUGH_HA")


def config_dir() -> Path:
    return Path(os.environ.get("SONORIUM_CONFIG_DIR") or "/config/sonorium")


def _port(name: str, default: int) -> int:
    value = (os.environ.get(name) or "").strip().lower()
    if value in ("off", "false", "no", "none"):
        return 0
    try:
        return int(value) if value else default
    except ValueError:
        return default


def https_port() -> int:
    """The port the HTTPS listener binds to inside the container (always on)."""
    return _port("SONORIUM_HTTPS_PORT", DEFAULT_HTTPS_PORT) or DEFAULT_HTTPS_PORT


def ingress_port() -> int:
    """Add-on only: HA's sidebar (ingress) reaches Sonorium on this port, which isn't mapped outside."""
    return 0 if runtime.STANDALONE else _port("SONORIUM_INGRESS_PORT", DEFAULT_INGRESS_PORT)


def addon_host_port(container_port: int) -> int:
    """
    Add-on: the host port a container port is mapped to (the add-on's Network
    settings), from the Supervisor. 0 when the user switched the mapping off;
    the container port when the Supervisor can't be asked.
    """
    if not container_port:
        return 0
    token = os.environ.get("SUPERVISOR_TOKEN")
    if runtime.STANDALONE or not token:
        return container_port
    try:
        import httpx
        response = httpx.get("http://supervisor/addons/self/info",
                             headers={"Authorization": f"Bearer {token}"}, timeout=5.0)
        network = (response.json().get("data") or {}).get("network") or {}
        key = f"{container_port}/tcp"
        if key in network:
            return int(network[key] or 0)
    except Exception:
        pass
    return container_port


def http_port() -> int:
    """The plain HTTP port inside the container: web UI, API and streams."""
    return 8008 if runtime.STANDALONE else 8080
