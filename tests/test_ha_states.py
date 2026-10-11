"""
The Home Assistant states helper (utils.HomeAssistantClient, which replaced
homeassistant_api) and how device.py turns its states into media players.
httpx is mocked: nothing here talks to a real Home Assistant.
"""
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

httpx = pytest.importorskip("httpx")

ADDON = Path(__file__).resolve().parent.parent / "sonorium_addon" / "sonorium"

STATES = [
    {"entity_id": "light.kitchen", "state": "on", "attributes": {"friendly_name": "Kitchen"}},
    {"entity_id": "media_player.nest_hub", "state": "idle",
     "attributes": {"friendly_name": "Nest Hub", "supported_features": 152463, "volume_level": 0.3},
     "last_changed": "2026-10-11T10:00:00+00:00", "context": {"id": "x"}},
    {"entity_id": "media_player.no_name", "state": "unavailable", "attributes": {}},
]


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ADDON / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def utils():
    obs = types.ModuleType("sonorium.obs")
    obs.logger = MagicMock()
    with patch.dict(sys.modules, {"sonorium.obs": obs}):
        sys.modules.setdefault("sonorium", types.ModuleType("sonorium"))
        yield _load("_sonorium_utils_states", "utils.py")


def _response(status=200, json_data=None):
    request = httpx.Request("GET", "http://supervisor/core/api/states")
    return httpx.Response(status, json=json_data, request=request)


def test_get_states_calls_rest_api_with_bearer_token(utils):
    with patch.object(utils.httpx, "get", return_value=_response(json_data=STATES)) as get:
        client = utils.HomeAssistantClient(api_url="http://supervisor/core/api/", token="abc")
        assert client.get_states() == STATES
    get.assert_called_once()
    args, kwargs = get.call_args
    assert args == ("http://supervisor/core/api/states",)
    assert kwargs["headers"]["Authorization"] == "Bearer abc"
    assert kwargs["timeout"] == utils.HomeAssistantClient.TIMEOUT


def test_get_states_raises_on_error_reply(utils):
    with patch.object(utils.httpx, "get", return_value=_response(status=401, json_data={"message": "no"})):
        with pytest.raises(httpx.HTTPStatusError):
            utils.HomeAssistantClient(api_url="http://ha:8123/api", token="bad").get_states()


def test_media_state_from_rest_state(utils):
    """device.MediaState takes the REST state dicts as it took homeassistant_api's State models."""
    # MediaState only needs dataclasses; read it out of device.py without the rest of Sonorium
    source = (ADDON / "device.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("@dataclass\nclass MediaState")
    end = source.index("@dataclass(kw_only=True)")
    namespace = {}
    exec("from dataclasses import dataclass, fields\nfrom typing import Self\n" + source[start:end], namespace)
    MediaState = namespace["MediaState"]

    players = [MediaState.from_state(s) for s in STATES if s.get("entity_id", "").startswith("media_player.")]
    assert [(p.entity_id, p.state, p.friendly_name, p.supported_features) for p in players] == [
        ("media_player.nest_hub", "idle", "Nest Hub", 152463),
        ("media_player.no_name", "unavailable", "media_player.no_name", 0),
    ]
