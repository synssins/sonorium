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
import time
import uuid
from pathlib import Path

STANDALONE = os.environ.get("SONORIUM_STANDALONE") == "1"

# This run of Sonorium (new at every start): GET /api/info and the event stream's ids carry it
BOOT_ID = uuid.uuid4().hex
STARTED_AT = time.time()  # when this process started (GET /api/info started_at)


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
}

# "On" features that can still be switched off through PUT /api/install/features
# (e.g. by a host's console); Settings > Advanced lists only the "off" ones
SWITCH_OFF_ALLOWED = {"network_speakers"}


def feature_state(name: str) -> str:
    states = FEATURES[name]
    return states.get(INSTALL, states["default"])


def feature_switchable(name: str) -> bool:
    """Whether PUT /api/install/features may change the feature here."""
    state = feature_state(name)
    return state == "off" or (state == "on" and name in SWITCH_OFF_ALLOWED)


def feature_enabled(name: str, overrides: dict | None = None) -> bool:
    """Whether a feature runs: its default here, or its override (Advanced, or the install features API)."""
    state = feature_state(name)
    if state == "off":
        return bool((overrides or {}).get(name, False))
    if state == "on" and name in SWITCH_OFF_ALLOWED:
        return bool((overrides or {}).get(name, True))
    return state == "on"


def features(overrides: dict | None = None) -> dict:
    """Every feature for the web UI: shown at all, running, in Advanced, and switchable through the API."""
    return {
        name: {
            "available": feature_state(name) != "hidden",
            "enabled": feature_enabled(name, overrides),
            "overridable": feature_state(name) == "off",
            "switchable": feature_switchable(name),
        }
        for name in FEATURES
    }

def http_port() -> int:
    """
    The plain HTTP port Sonorium listens on (web UI, API and streams):
    SONORIUM_HTTP_PORT, else 8008 (standalone) or 8080 (the add-on, whose
    8008 is the host port mapped to it).
    """
    default = 8008 if STANDALONE else 8080
    try:
        port = int((os.environ.get("SONORIUM_HTTP_PORT") or "").strip() or default)
    except ValueError:
        return default
    return port if 0 < port < 65536 else default


def stream_port() -> int:
    """The HTTP port as speakers reach it: SONORIUM_HTTP_PORT, else 8008 (standalone and the add-on's mapping)."""
    return http_port() if (os.environ.get("SONORIUM_HTTP_PORT") or "").strip() else 8008


CONNECTION_FILE = Path(os.environ.get("SONORIUM_CONNECTION_FILE", "/config/sonorium/connection.json"))

# Fields stored in connection.json. Secrets are never returned by the API.
CONNECTION_FIELDS = ("ha_url", "ha_token", "mqtt_host", "mqtt_port", "mqtt_username", "mqtt_password", "stream_url")
SECRET_FIELDS = ("ha_token", "mqtt_password")


def load_connection() -> dict:
    """
    Saved standalone connection settings (empty dict if none yet). With a
    secret helper (SONORIUM_SECRET_HELPER) the secrets come from it.
    """
    data = _read_connection_file()
    current = {k: data[k] for k in CONNECTION_FIELDS if data.get(k) not in (None, "")}
    if secret_helper():
        for name in SECRET_FIELDS:
            if name not in current:  # a plain one still in the file is used until it's moved
                value = _helper_secret(name)
                if value:
                    current[name] = value
    return current


def save_connection(updates: dict) -> dict:
    """
    Merge updates into the saved settings and write them (owner-only file).
    A missing or empty secret in `updates` keeps the saved value, so the UI
    never has to send secrets back. With a secret helper, new secrets go to
    it, never into the file; SecretStoreError if it can't take one (nothing
    is saved then).
    """
    current = load_connection()
    new_secrets = {}
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
            if key in SECRET_FIELDS:
                new_secrets[key] = value

    if secret_helper():
        for name, value in new_secrets.items():
            store_secret(name, str(value))
    _write_connection(current)
    return current


def clear_connection(keys) -> dict:
    """
    Remove saved settings (secrets included) and write back the rest. With a
    secret helper its copies are erased; SecretStoreError if that fails
    (nothing is removed then).
    """
    current = load_connection()
    if secret_helper():
        for name in keys:
            if name in SECRET_FIELDS:
                erase_secret(name)
    for key in keys:
        current.pop(key, None)
    _write_connection(current)
    return current


def _read_connection_file() -> dict:
    try:
        data = json.loads(CONNECTION_FILE.read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_connection(current: dict) -> None:
    if secret_helper():  # secrets live in the helper, never in the file
        current = {k: v for k, v in current.items() if k not in SECRET_FIELDS}
    CONNECTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONNECTION_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(current, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(CONNECTION_FILE)


# --- Secret helper: SONORIUM_SECRET_HELPER (docs/HOSTING.md) ---
#
# The variable holds one program path and no arguments (a path with spaces
# needs no quoting). It is run without a shell, as:
#     <helper> store <name>   the value on stdin (UTF-8, no trailing newline); exit 0
#     <helper> get <name>     prints the value on stdout, exit 0; exit 1: it has none
#     <helper> erase <name>   exit 0 (also when it had none)
# Names: the SECRET_FIELDS. Each call may take SECRET_HELPER_TIMEOUT seconds.

SECRET_HELPER_TIMEOUT = 10
_secret_cache: dict = {}  # what the helper answered this run (None: it has none)


class SecretStoreError(RuntimeError):
    """The secret helper couldn't store or erase a secret."""


def secret_helper() -> str:
    """The secret helper program, or "" when secrets are kept in connection.json."""
    return (os.environ.get("SONORIUM_SECRET_HELPER") or "").strip()


def _logger():
    try:
        from sonorium.obs import logger
    except ImportError:  # loaded on its own (tests)
        import logging
        logger = logging.getLogger("sonorium")
    return logger


def _run_helper(action: str, name: str, value: str = ""):
    import subprocess
    return subprocess.run([secret_helper(), action, name], input=value.encode("utf-8"),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=SECRET_HELPER_TIMEOUT, shell=False, check=False)


def _helper_error(result) -> str:
    return result.stderr.decode("utf-8", "replace").strip() or f"exit code {result.returncode}"


def _helper_secret(name: str):
    """A secret from the helper (asked once per run), or None. Never raises: a failure is logged."""
    if name in _secret_cache:
        return _secret_cache[name]
    value = None
    try:
        result = _run_helper("get", name)
        if result.returncode == 0:
            value = result.stdout.decode("utf-8")
            for ending in ("\r\n", "\n"):  # one line ending after the value is allowed
                if value.endswith(ending):
                    value = value[:-len(ending)]
                    break
            value = value or None
        elif result.returncode == 1:
            _logger().warning(f"Secret helper has no {name} (or couldn't read it): it is treated as not set")
        else:
            _logger().error(f"Secret helper: 'get {name}' failed ({_helper_error(result)}): treated as not set")
    except Exception as e:  # no such program, timeout
        _logger().error(f"Secret helper: 'get {name}' failed ({e}): treated as not set")
    _secret_cache[name] = value
    return value


def _helper_change(action: str, name: str, value: str = "") -> None:
    done = "saved" if action == "store" else "removed"
    try:
        result = _run_helper(action, name, value)
    except Exception as e:
        detail = str(e) or type(e).__name__
    else:
        if result.returncode == 0:
            _secret_cache[name] = value or None
            return
        detail = _helper_error(result)
    _logger().error(f"Secret helper: '{action} {name}' failed ({detail})")
    raise SecretStoreError(f"The {name} couldn't be {done}: the secret helper failed ({detail})")


def store_secret(name: str, value: str) -> None:
    """Give a secret to the helper. SecretStoreError if it can't take it."""
    _helper_change("store", name, value)


def erase_secret(name: str) -> None:
    """Remove a secret from the helper. SecretStoreError if it can't."""
    _helper_change("erase", name)


def move_secrets_to_helper() -> None:
    """
    A secret helper was newly set: plain secrets still in connection.json move
    into it and leave the file. One the helper can't take stays in the file
    (and is used) and is tried again at the next start.
    """
    if not secret_helper():
        return
    data = _read_connection_file()
    moved = False
    for name in SECRET_FIELDS:
        if data.get(name) in (None, ""):
            continue
        try:
            store_secret(name, str(data[name]))
        except SecretStoreError:
            continue
        data.pop(name)
        moved = True
        _logger().info(f"Moved {name} from {CONNECTION_FILE.name} to the secret helper")
    if moved:
        tmp = CONNECTION_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
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
    move_secrets_to_helper()
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
