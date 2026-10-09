"""Standalone runtime helpers: HA URLs and the connection settings file."""

import importlib.util
import json
import os
from pathlib import Path

import pytest


def load_runtime(monkeypatch, tmp_path, standalone=True):
    monkeypatch.setenv("SONORIUM_STANDALONE", "1" if standalone else "0")
    monkeypatch.setenv("SONORIUM_CONNECTION_FILE", str(tmp_path / "sonorium" / "connection.json"))
    path = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium" / "runtime.py"
    spec = importlib.util.spec_from_file_location("runtime_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("api_url, expected", [
    ("http://supervisor/core/api", "ws://supervisor/core/api/websocket"),
    ("http://192.168.1.104:8123/api", "ws://192.168.1.104:8123/api/websocket"),
    ("https://ha.example.com/api/", "wss://ha.example.com/api/websocket"),
])
def test_websocket_url(monkeypatch, tmp_path, api_url, expected):
    runtime = load_runtime(monkeypatch, tmp_path)
    assert runtime.ha_websocket_url(api_url) == expected


@pytest.mark.parametrize("entered, expected", [
    ("http://192.168.1.104:8123", "http://192.168.1.104:8123/api"),
    ("http://192.168.1.104:8123/", "http://192.168.1.104:8123/api"),
    ("https://ha.example.com/api", "https://ha.example.com/api"),
])
def test_ha_api_url(monkeypatch, tmp_path, entered, expected):
    runtime = load_runtime(monkeypatch, tmp_path)
    assert runtime.ha_api_url(entered) == expected


def test_blank_secrets_keep_saved_values(monkeypatch, tmp_path):
    runtime = load_runtime(monkeypatch, tmp_path)
    runtime.save_connection({"ha_url": "http://ha:8123", "ha_token": "secret", "mqtt_password": "pw"})
    runtime.save_connection({"ha_url": "http://ha2:8123", "ha_token": "", "mqtt_password": None})
    saved = runtime.load_connection()
    assert saved == {"ha_url": "http://ha2:8123", "ha_token": "secret", "mqtt_password": "pw"}


def test_connection_file_is_owner_only(monkeypatch, tmp_path):
    runtime = load_runtime(monkeypatch, tmp_path)
    runtime.save_connection({"ha_token": "secret"})
    if os.name == "posix":
        assert (runtime.CONNECTION_FILE.stat().st_mode & 0o777) == 0o600
    assert json.loads(runtime.CONNECTION_FILE.read_text()) == {"ha_token": "secret"}


def test_env_from_saved_connection(monkeypatch, tmp_path):
    runtime = load_runtime(monkeypatch, tmp_path)
    for key in ("SONORIUM__HA_CORE_API", "SUPERVISOR_TOKEN", "SONORIUM__MQTT_HOST", "SONORIUM__MQTT_PORT", "SONORIUM__STREAM_URL"):
        monkeypatch.delenv(key, raising=False)
    runtime.save_connection({"ha_url": "http://ha:8123", "ha_token": "t", "stream_url": "http://nas:8008"})
    runtime.load_connection_into_env()
    assert os.environ["SONORIUM__HA_CORE_API"] == "http://ha:8123/api"
    assert os.environ["SUPERVISOR_TOKEN"] == "t"
    assert os.environ["SONORIUM__MQTT_HOST"] == "none"
    assert os.environ["SONORIUM__STREAM_URL"] == "http://nas:8008"


def test_addon_mode_leaves_env_alone(monkeypatch, tmp_path):
    runtime = load_runtime(monkeypatch, tmp_path, standalone=False)
    monkeypatch.delenv("SONORIUM__HA_CORE_API", raising=False)
    runtime.load_connection_into_env()
    assert "SONORIUM__HA_CORE_API" not in os.environ


def test_install_type_and_features_addon(monkeypatch, tmp_path):
    runtime = load_runtime(monkeypatch, tmp_path, standalone=False)
    assert (runtime.INSTALL, runtime.INSTALL_LABEL) == ("addon", "HA App")
    features = runtime.features()
    assert features["connection_settings"] == {"available": False, "enabled": False, "overridable": False}
    assert features["network_speakers"] == {"available": True, "enabled": False, "overridable": True}
    assert runtime.feature_enabled("network_speakers", {"network_speakers": True})
    assert not runtime.feature_enabled("connection_settings", {"connection_settings": True})  # hidden stays off


def test_install_type_and_features_docker(monkeypatch, tmp_path):
    monkeypatch.setenv("container", "docker")
    runtime = load_runtime(monkeypatch, tmp_path)
    assert (runtime.INSTALL, runtime.INSTALL_LABEL) == ("docker", "Docker")
    assert all(f == {"available": True, "enabled": True, "overridable": False} for f in runtime.features().values())
