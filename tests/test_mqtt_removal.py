"""
Removing MQTT (Settings > Connection) or switching Announcements off removes
every Home Assistant entity Sonorium created over MQTT: empty retained
discovery configs, sent while the broker is still connected.
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from playback_helpers import addon_modules
from test_connection_remove import app, make_api, make_registry  # noqa: F401  (fixture)

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.plugins.mqtt", "sonorium.ha.announcements") as mods:
        m.__dict__.update(mods.__dict__)
        yield


class FakeClient:
    """An MQTT client that records what was published, and when it was closed."""

    def __init__(self):
        self.is_connected = True
        self.log = []

    def publish(self, topic, payload, retain=False):
        assert self.is_connected, f"published {topic} after the broker went"
        self.log.append((topic, payload, retain))

    def subscribe(self, topic_filter):
        pass

    def set_message_handler(self, handler):
        pass

    async def close(self):
        self.log.append(("closed", None, None))
        self.is_connected = False

    def retained(self):
        """What the broker keeps: the last retained payload per topic (an empty one removes it)."""
        kept = {}
        for topic, payload, retain in self.log:
            if retain:
                kept[topic] = payload
        return {topic: payload for topic, payload in kept.items() if payload}


class Bus:
    def subscribe(self, *a, **k):
        pass

    def unsubscribe_owner(self, owner):
        pass


def announcements_host(client, channels):
    router = m.mqtt.MqttRouter()
    router.bind(client)
    actions = SimpleNamespace(list_channels=AsyncMock(return_value=channels))
    tts = SimpleNamespace(register=lambda *a, **k: None)
    return SimpleNamespace(mqtt=router, actions=actions, bus=Bus(), tts=tts)


CHANNELS = [{"id": "abc123", "name": "Kitchen", "entity_slug": "kitchen"}]
NOTIFY = "homeassistant/notify/sonorium_abc123_announce/config"
PLUGIN = "homeassistant/sensor/sonorium_weather_temp/config"


def test_router_remembers_discovery_configs_and_removes_them():
    client = FakeClient()
    router = m.mqtt.MqttRouter()
    router.bind(client)

    async def scenario():
        await router.publish(PLUGIN, {"name": "Temperature"}, retain=True)
        await router.publish("homeassistant/sensor/sonorium_gone/config", {"name": "Gone"}, retain=True)
        await router.publish("homeassistant/sensor/sonorium_gone/config", "", retain=True)  # removed already
        await router.publish("sonorium/weather/state", "21", retain=True)  # not an entity
        return await router.remove_discovery()

    assert asyncio.run(scenario()) == 1
    assert client.log[-1] == (PLUGIN, "", True)
    assert client.retained() == {"sonorium/weather/state": "21"}


def test_announcements_off_removes_the_notify_entities():
    client = FakeClient()
    announcements = m.announcements.Announcements(announcements_host(client, CHANNELS))
    asyncio.run(announcements.start(True))
    assert json.loads(client.retained()[NOTIFY])["default_entity_id"] == "notify.sonorium_kitchen_announce"
    asyncio.run(announcements.set_enabled(False))
    assert NOTIFY not in client.retained()


def test_removing_mqtt_removes_announce_and_plugin_entities_before_disconnecting(app):
    app.runtime.save_connection({"mqtt_host": "broker"})
    api = make_api(app, make_registry(app))
    client = FakeClient()
    api.backend.mqtt_client = client
    host = announcements_host(client, CHANNELS)
    api._plugin_host = host
    api._announcements = m.announcements.Announcements(host)

    async def scenario():
        await api._announcements.start(True)
        await host.mqtt.publish(PLUGIN, {"name": "Temperature"}, retain=True)  # a plugin's entity
        assert set(client.retained()) == {NOTIFY, PLUGIN}
        return await api.delete_connection_mqtt()

    result = asyncio.run(scenario())
    assert result["status"] == "ok" and result["restart_required"] is False
    assert client.retained() == {}  # Home Assistant drops them all
    assert client.log[-1][0] == "closed"  # removed first, then disconnected


def test_saving_mqtt_without_a_broker_removes_the_entities_before_the_restart(app, monkeypatch):
    app.runtime.save_connection({"mqtt_host": "broker"})
    api = make_api(app, make_registry(app))
    client = FakeClient()
    api.backend.mqtt_client = client
    host = announcements_host(client, CHANNELS)
    api._plugin_host = host
    api._announcements = m.announcements.Announcements(host)
    restarts = []
    api._restart_soon = lambda reason="": restarts.append(reason)
    request = SimpleNamespace(json=AsyncMock(return_value={"mqtt_host": ""}))

    async def scenario():
        await api._announcements.start(True)
        return await api.put_connection(request)

    assert asyncio.run(scenario())["restarting"] is True
    assert client.retained() == {} and restarts
