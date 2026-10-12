"""
Runtime mode: Home Assistant add-on (default) or standalone (Docker).

Standalone mode is switched on only by SONORIUM_STANDALONE=1, which the
standalone Dockerfile sets. Its connection settings (Home Assistant URL and
token, MQTT broker, stream URL) are entered in the web UI after startup and
stored in /config/sonorium/connection.json (inside the container's /config
volume), so the container needs no environment variables beyond that switch.

load_connection_into_env() must run before sonorium.settings is imported:
it feeds the same SONORIUM__* variables the add-on's run.sh provides.
"""
import json
import os
import uuid
from pathlib import Path

STANDALONE = os.environ.get("SONORIUM_STANDALONE") == "1"

# This run of Sonorium (new at every start): GET /api/info and the event stream's ids carry it
BOOT_ID = uuid.uuid4().hex


def _detect_install() -> str:
    """How Sonorium was installed: the one place this is decided."""
    if not STANDALONE:
        return "addon"
    if Path("/.dockerenv").exists() or os.environ.get("container"):
        return "docker"
    return "windows" if os.name == "nt" else "linux"


INSTALL = _detect_install()
INSTALL_LABELS = {"addon": "HA App", "docker": "Docker", "windows": "Windows", "linux": "Linux"}
INSTALL_LABEL = INSTALL_LABELS[INSTALL]

# What each install shows: "on", "off" (can be switched on in Settings >
# Advanced) or "hidden" (never offered). Installs not listed use "default".
FEATURES = {
    # Home Assistant and MQTT settings: the HA app gets both from Home Assistant
    "connection_settings": {"addon": "hidden", "default": "on"},
    # Speakers found on the network or added by address, outside Home Assistant
    "network_speakers": {"addon": "off", "default": "on"},
    # Adding, renaming and deleting floors and areas; the HA app shows
    # Home Assistant's, which stays in charge of them
    "space_editing": {"addon": "hidden", "default": "on"},
    # Plugins may install Python packages from the internet (plugins/packages.py)
    "plugin_packages": {"default": "off"},
    # Keys for devices and programs (auth/keys.py): on, and can be switched off; applies at once
    "keys": {"default": "on", "switchable": True, "live": True},
    # Announcements from Home Assistant, MQTT, the API and plugins (core/announce.py,
    # ha/announcements.py): on, and can be switched off; applies at once
    "announcements": {"default": "on", "switchable": True, "live": True},
}


def feature_state(name: str) -> str:
    states = FEATURES[name]
    return states.get(INSTALL, states["default"])


def feature_overridable(name: str) -> bool:
    """Switchable in Settings > Advanced: an "off" feature, or an "on" one marked switchable."""
    state = feature_state(name)
    return state == "off" or (state == "on" and bool(FEATURES[name].get("switchable")))


def feature_enabled(name: str, overrides: dict | None = None) -> bool:
    """Whether a feature runs: its default here, or the Advanced override for a switchable one."""
    state = feature_state(name)
    if feature_overridable(name):
        return bool((overrides or {}).get(name, state == "on"))
    return state == "on"


def features(overrides: dict | None = None) -> dict:
    """
    Every feature for the web UI and the app: shown at all, running, switchable
    in Advanced, on out of the box ("default") and whether a change applies at
    once ("live") or after a restart.
    """
    return {
        name: {
            "available": feature_state(name) != "hidden",
            "enabled": feature_enabled(name, overrides),
            "overridable": feature_overridable(name),
            "default": feature_state(name) == "on",
            "live": bool(FEATURES[name].get("live")),
        }
        for name in FEATURES
    }

CONNECTION_FILE = Path(os.environ.get("SONORIUM_CONNECTION_FILE", "/config/sonorium/connection.json"))

# Fields stored in connection.json. Secrets are never returned by the API.
CONNECTION_FIELDS = ("ha_url", "ha_token", "mqtt_host", "mqtt_port", "mqtt_username", "mqtt_password", "stream_url")
SECRET_FIELDS = ("ha_token", "mqtt_password")


def load_connection() -> dict:
    """Saved standalone connection settings (empty dict if none yet)."""
    try:
        data = json.loads(CONNECTION_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {}
    return {k: data[k] for k in CONNECTION_FIELDS if data.get(k) not in (None, "")}


def save_connection(updates: dict) -> dict:
    """
    Merge updates into the saved settings and write them (owner-only file).
    A missing or empty secret in `updates` keeps the saved value, so the UI
    never has to send secrets back.
    """
    current = load_connection()
    for key in CONNECTION_FIELDS:
        if key not in updates:
            continue
        value = updates[key]
        if key in SECRET_FIELDS and value in (None, ""):
            continue
        if value in (None, ""):
            current.pop(key, None)
        else:
            current[key] = value

    _write_connection(current)
    return current


def clear_connection(keys) -> dict:
    """Remove saved settings (secrets included) and write back the rest."""
    current = load_connection()
    for key in keys:
        current.pop(key, None)
    _write_connection(current)
    return current


def _write_connection(current: dict) -> None:
    CONNECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONNECTION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(CONNECTION_FILE)


def ha_api_url(ha_url: str) -> str:
    """Home Assistant REST base URL from what the user entered."""
    url = ha_url.strip().rstrip("/")
    return url if url.endswith("/api") else f"{url}/api"


def ha_websocket_url(api_url: str) -> str:
    """
    WebSocket URL for an HA REST base URL: http://ha:8123/api ->
    ws://ha:8123/api/websocket, and http://supervisor/core/api ->
    ws://supervisor/core/api/websocket (accepted by the Supervisor proxy).
    """
    url = api_url.rstrip("/")
    if url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    elif url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    return f"{url}/websocket"


def ha_configured() -> bool:
    """
    Whether Home Assistant is available to use. Always true for the add-on;
    in standalone mode only once an HA URL has been saved. Standalone runs
    fine without HA.
    """
    return not STANDALONE or bool(os.environ.get("SONORIUM__HA_CORE_API"))


def load_connection_into_env() -> None:
    """In standalone mode, expose saved settings as the SONORIUM__* variables."""
    if not STANDALONE:
        return
    conn = load_connection()
    env = {
        "SONORIUM__HA_CORE_API": ha_api_url(conn["ha_url"]) if conn.get("ha_url") else None,
        "SUPERVISOR_TOKEN": conn.get("ha_token"),
        # "none" (not "auto"): there's no Supervisor to auto-detect a broker from
        "SONORIUM__MQTT_HOST": conn.get("mqtt_host") or "none",
        "SONORIUM__MQTT_PORT": str(conn.get("mqtt_port") or 1883),
        "SONORIUM__MQTT_USERNAME": conn.get("mqtt_username"),
        "SONORIUM__MQTT_PASSWORD": conn.get("mqtt_password"),
        "SONORIUM__STREAM_URL": conn.get("stream_url") or "auto",
    }
    for key, value in env.items():
        if value:
            os.environ[key] = value


# Saved fields removed together in Settings > Connection
HA_FIELDS = ("ha_url", "ha_token")
MQTT_FIELDS = ("mqtt_host", "mqtt_port", "mqtt_username", "mqtt_password")


def clear_ha_env() -> None:
    """
    Home Assistant removed: ha_configured() is False from now on. Popped (not
    just ignored) because a restart re-executes with this environment.
    """
    for key in ("SONORIUM__HA_CORE_API", "SUPERVISOR_TOKEN"):
        os.environ.pop(key, None)


def clear_mqtt_env() -> None:
    """MQTT removed: the variables load_connection_into_env sets when there's no broker."""
    os.environ["SONORIUM__MQTT_HOST"] = "none"
    os.environ["SONORIUM__MQTT_PORT"] = "1883"
    for key in ("SONORIUM__MQTT_USERNAME", "SONORIUM__MQTT_PASSWORD"):
        os.environ.pop(key, None)
