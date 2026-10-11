"""
How Sonorium finds its MQTT broker (client.find_mqtt_broker): the settings
run.sh fills in, else the Supervisor's MQTT service. The expected results
were recorded from ClientSonorium.from_supervisor before the start-up code
was rewritten. Nothing here talks to a real Supervisor.
"""
import io
import json
import sys
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("paho.mqtt")
pytest.importorskip("fastapi")
pydantic = pytest.importorskip("pydantic")

from playback_helpers import PACKAGE  # noqa: E402

NO_BROKER = RuntimeError


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def settings(host="auto", port=0, user="", password=""):
    return SimpleNamespace(mqtt_host=host, mqtt_port=port, mqtt_username=user,
                           mqtt_password=pydantic.SecretStr(password),
                           ha_supervisor_api="http://supervisor", token="tok")


# name: (settings, standalone, the Supervisor's reply, expected (host, port, username, password))
CASES = {
    "manual host, no port": (settings("broker.lan"), False, None, ("broker.lan", 1883, None, None)),
    "manual, all set": (settings("broker.lan", 1884, "u", "p"), False, None, ("broker.lan", 1884, "u", "p")),
    "standalone, none": (settings("none", 1883, "u", "p"), True, None, (None, 1883, None, None)),
    "standalone, auto": (settings("auto"), True, None, (None, 1883, None, None)),
    "add-on, service in data": (settings("auto"), False,
                                {"data": {"host": "core-mosquitto", "port": 1883, "username": "addons", "password": "x"}},
                                ("core-mosquitto", 1883, "addons", "x")),
    "add-on, flat service, port 0": (settings(""), False, {"host": "core-mosquitto", "port": 0, "username": "a"},
                                     ("core-mosquitto", 1883, "a", None)),
    "add-on, own credentials win": (settings("auto", 0, "mine", "secret"), False,
                                    {"data": {"host": "h", "port": 1999, "username": "addons", "password": "x"}},
                                    ("h", 1999, "mine", "secret")),
    "add-on, port set, service without one": (settings("auto", 1890), False, {"data": {"host": "h"}},
                                              ("h", 1883, None, None)),
    "add-on, service without host": (settings("auto"), False, {"data": {"result": "ok"}}, NO_BROKER),
    "add-on, odd reply": (settings("auto"), False, [1, 2], NO_BROKER),
    "add-on, HTTP error": (settings("auto"), False, "HTTP 403", NO_BROKER),
    "add-on, Supervisor down": (settings("auto"), False, "down", NO_BROKER),
}


@pytest.fixture(scope="module")
def client():
    sys.path.insert(0, str(PACKAGE.parent))
    saved = {k: v for k, v in sys.modules.items() if k == "sonorium" or k.startswith("sonorium.")}
    for k in saved:
        del sys.modules[k]
    try:
        import sonorium.client as module
        yield module
    finally:
        sys.path.remove(str(PACKAGE.parent))
        for k in [k for k in sys.modules if k == "sonorium" or k.startswith("sonorium.")]:
            del sys.modules[k]
        sys.modules.update(saved)


@pytest.mark.parametrize("name", list(CASES))
def test_find_mqtt_broker(client, name):
    given, standalone, reply, expected = CASES[name]
    calls = []

    def urlopen(request, timeout=None):
        calls.append((request.full_url, request.get_header("Authorization"), timeout))
        if reply == "HTTP 403":
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b"no"))
        if reply == "down":
            raise OSError("down")
        return Reply(json.dumps(reply).encode())

    with patch.object(client.runtime, "STANDALONE", standalone), patch("urllib.request.urlopen", urlopen):
        if expected is NO_BROKER:
            with pytest.raises(RuntimeError, match="MQTT host not configured"):
                client.find_mqtt_broker(given)
        else:
            assert tuple(client.find_mqtt_broker(given)) == expected
    # The Supervisor is asked only when no broker is set, in the add-on
    asked = reply is not None
    assert calls == ([("http://supervisor/services/mqtt", "Bearer tok", 10)] if asked else [])
