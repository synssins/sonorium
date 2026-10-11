"""Home Assistant dashboard cards: the MQTT entities and attributes they use, and the installer (mock MQTT and WebSocket)."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.state", "sonorium.ha.mqtt_entities", "sonorium.ha.cards") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("asyncio.sleep", AsyncMock()):
        yield


def run(coro):
    return asyncio.run(coro)


def published(publish):
    return {c.args[0]: c.args[1] for c in publish.call_args_list}


def make_entities(session, publish):
    return m.mqtt_entities.SessionMQTTEntities(session, "sonorium", publish, {"identifiers": ["sonorium_device"]})


def make_manager(sessions=(), version="1.6.2"):
    store = m.state.StateStore()
    for session in sessions:
        store.sessions[session.id] = session
    session_manager = MagicMock()
    session_manager.get_speaker_summary.return_value = "Study"
    session_manager.playback_status.return_value = {}
    metadata = MagicMock()
    metadata.get_folder_for_id.side_effect = lambda tid: f"folder-{tid}"
    metadata.get_metadata_by_folder.side_effect = lambda folder: SimpleNamespace(icon="🌧️", presets={})
    manager = m.mqtt_entities.SonoriumMQTTManager(store, session_manager, MagicMock(), theme_metadata_manager=metadata,
                                                  version=version)
    manager._mqtt_publish = AsyncMock()
    return manager


# ---------- per-channel entities ----------

def test_speakers_muted_switch_and_status_attributes():
    session = m.state.Session(id="abc", name="Office", speakers_muted=True,
                              recent=[{"at": "2026-10-10T10:00:00+00:00", "text": "Played"},
                                      {"at": "2026-10-10T11:00:00+00:00", "text": "Speakers muted"}])
    publish = AsyncMock()
    entities = make_entities(session, publish)
    run(entities.publish_discovery())
    run(entities.update_state())
    sent = published(publish)

    switch = json.loads(sent["homeassistant/switch/sonorium_office_speakers_muted/config"])
    assert switch["command_topic"] == "sonorium/office/speakers_muted/set"
    assert switch["state_topic"] == "sonorium/office/speakers_muted/state"
    assert switch["default_entity_id"] == "switch.sonorium_office_speakers_muted"
    assert sent["sonorium/office/speakers_muted/state"] == "ON"

    status = json.loads(sent["homeassistant/sensor/sonorium_office_status/config"])
    assert status["json_attributes_topic"] == "sonorium/office/status/attributes"
    recent = json.loads(sent["sonorium/office/status/attributes"])["recent"]
    assert [e["text"] for e in recent] == ["Speakers muted", "Played"]  # newest first


def test_removing_a_channel_removes_its_speakers_muted_switch():
    publish = AsyncMock()
    run(make_entities(m.state.Session(id="abc", name="Office"), publish).remove_discovery())
    assert published(publish)["homeassistant/switch/sonorium_office_speakers_muted/config"] == ""


def test_entity_ids_default_and_registered():
    entities = make_entities(m.state.Session(id="abc", name="Office"), AsyncMock())
    ids = entities.entity_ids()
    assert ids["play"] == "switch.sonorium_office_play"
    assert ids["preset"] == "select.sonorium_office_preset"
    assert ids["speakers_muted"] == "switch.sonorium_office_speakers_muted"
    assert ids["step"] == "sensor.sonorium_office_step"
    assert set(ids) == {"play", "theme", "preset", "playlist", "next", "speakers_muted", "volume", "status", "step",
                        "speakers"}
    # A renamed entity goes by its name in Home Assistant
    renamed = entities.entity_ids({"sonorium_office_play": "switch.office_sounds"})
    assert renamed["play"] == "switch.office_sounds" and renamed["theme"] == "select.sonorium_office_theme"


# ---------- the channels attribute ----------

def test_channels_attribute_on_active_sessions_sensor():
    office = m.state.Session(id="abc", name="Office", theme_id="t1")
    bedroom = m.state.Session(id="def", name="Bedroom")
    manager = make_manager([office, bedroom])
    run(manager.initialize())
    sent = published(manager._mqtt_publish)

    config = json.loads(sent["homeassistant/sensor/sonorium_global_active_sessions/config"])
    assert config["json_attributes_topic"] == "sonorium/active_sessions/attributes"
    attributes = json.loads(sent["sonorium/active_sessions/attributes"])
    assert attributes["sonorium_version"] == "1.6.2"
    assert [c["name"] for c in attributes["channels"]] == ["Office", "Bedroom"]
    first = attributes["channels"][0]
    assert first["id"] == "abc" and first["theme_id"] == "t1" and first["icon"] == "🌧️"
    assert first["entities"]["volume"] == "number.sonorium_office_volume"
    assert attributes["channels"][1]["icon"] == ""  # no theme


def test_channels_attribute_follows_renames_and_registered_ids():
    office = m.state.Session(id="abc", name="Office")
    manager = make_manager([office])
    run(manager.initialize())
    manager._mqtt_publish.reset_mock()

    # Unchanged: not sent again
    run(manager.publish_channels())
    assert "sonorium/active_sessions/attributes" not in published(manager._mqtt_publish)

    office.name = "Study"
    manager.registered_entity_ids = {"sonorium_office_play": "switch.study_play"}
    run(manager.refresh_session_discovery(office))
    attributes = json.loads(published(manager._mqtt_publish)["sonorium/active_sessions/attributes"])
    assert attributes["channels"][0]["name"] == "Study"
    assert attributes["channels"][0]["entities"]["play"] == "switch.study_play"


def test_discovery_changes_ask_for_entity_ids_again():
    office = m.state.Session(id="abc", name="Office")
    manager = make_manager([office])
    manager.on_discovery = MagicMock()
    run(manager.add_session_entities(office))
    manager.on_discovery.assert_called_once()


def test_removed_channel_leaves_the_channels_attribute():
    office = m.state.Session(id="abc", name="Office")
    manager = make_manager([office])
    run(manager.initialize())
    del manager.state.sessions["abc"]
    run(manager.remove_session_entities("abc"))
    attributes = json.loads(published(manager._mqtt_publish)["sonorium/active_sessions/attributes"])
    assert attributes["channels"] == []


def test_speakers_muted_command():
    office = m.state.Session(id="abc", name="Office")
    manager = make_manager([office])
    manager.mqtt_client.subscribe = MagicMock()
    run(manager.initialize())
    topics = {c.args[0] for c in manager.mqtt_client.subscribe.call_args_list}
    assert "sonorium/office/speakers_muted/set" in topics

    def mute(session_id, muted):
        office.speakers_muted = muted
        return True
    manager.session_manager.set_speakers_muted.side_effect = mute
    manager._mqtt_publish.reset_mock()
    run(manager.handle_command("sonorium/office/speakers_muted/set", "ON"))
    manager.session_manager.set_speakers_muted.assert_called_once_with("abc", True)
    assert published(manager._mqtt_publish)["sonorium/office/speakers_muted/state"] == "ON"

    run(manager.handle_command("sonorium/office/speakers_muted/set", "OFF"))
    manager.session_manager.set_speakers_muted.assert_called_with("abc", False)
    assert published(manager._mqtt_publish)["sonorium/office/speakers_muted/state"] == "OFF"


# ---------- installing the cards ----------

class FakeWs:
    """Home Assistant's WebSocket API: the dashboard resources and the entity registry."""

    def __init__(self, resources=(), errors=None, registry=()):
        self.resources = [dict(r) for r in resources]
        self.errors = errors or {}  # command type -> error code
        self.registry = list(registry)
        self.sent = []

    async def __call__(self, messages):
        replies = []
        for message in messages:
            self.sent.append(message)
            kind = message["type"]
            if kind in self.errors:
                replies.append({"success": False, "error": {"code": self.errors[kind], "message": "no"}})
            elif kind == "lovelace/resources":
                replies.append({"success": True, "result": [dict(r) for r in self.resources]})
            elif kind == "lovelace/resources/create":
                self.resources.append({"id": f"r{len(self.resources) + 1}", "type": message["res_type"],
                                       "url": message["url"]})
                replies.append({"success": True, "result": self.resources[-1]})
            elif kind == "lovelace/resources/update":
                for r in self.resources:
                    if r["id"] == message["resource_id"]:
                        r.update(type=message["res_type"], url=message["url"])
                replies.append({"success": True, "result": {}})
            elif kind == "lovelace/resources/delete":
                self.resources = [r for r in self.resources if r["id"] != message["resource_id"]]
                replies.append({"success": True, "result": None})
            elif kind == "config/entity_registry/list":
                replies.append({"success": True, "result": self.registry})
            else:
                replies.append({"success": False, "error": {"code": "unknown_command"}})
        return replies

    def changes(self):
        return [s["type"] for s in self.sent if s["type"] != "lovelace/resources"]


def make_cards(ws, tmp_path, install="addon", ha=True, mqtt=True, **kwargs):
    card = tmp_path / "src" / "sonorium-cards.js"
    card.parent.mkdir(exist_ok=True)
    card.write_text("// card", encoding="utf-8")
    mqtt_manager = None
    if mqtt:
        mqtt_manager = MagicMock(prefix="sonorium", _removed=False)
        mqtt_manager.publish_channels = AsyncMock()
    return m.cards.DashboardCards(ws, install=install, version="1.6.2", ha_linked=lambda: ha,
                                  mqtt_manager=mqtt_manager, www_dir=tmp_path / "www" / "sonorium",
                                  card_file=card, **kwargs)


def test_addon_copies_the_card_and_registers_the_resource(tmp_path):
    ws = FakeWs()
    cards = make_cards(ws, tmp_path)
    status = run(cards.install())
    assert status == {"state": "installed", "line": "Dashboard cards: installed", "reason": ""}
    assert (tmp_path / "www" / "sonorium" / "sonorium-cards.js").read_text(encoding="utf-8") == "// card"
    assert ws.resources == [{"id": "r1", "type": "module", "url": "/local/sonorium/sonorium-cards.js?v=1.6.2"}]


def test_already_registered_changes_nothing(tmp_path):
    ws = FakeWs([{"id": "x", "type": "module", "url": "/local/sonorium/sonorium-cards.js?v=1.6.2"}])
    assert run(make_cards(ws, tmp_path).install())["state"] == "installed"
    assert ws.changes() == []


def test_update_moves_the_resource_to_the_new_version_and_drops_duplicates(tmp_path):
    ws = FakeWs([{"id": "a", "type": "module", "url": "/local/other-card.js"},
                 {"id": "x", "type": "module", "url": "/local/sonorium/sonorium-cards.js?v=1.6.1"},
                 {"id": "y", "type": "js", "url": "/local/sonorium/sonorium-cards.js"}])
    assert run(make_cards(ws, tmp_path).install())["state"] == "installed"
    assert ws.changes() == ["lovelace/resources/update", "lovelace/resources/delete"]
    assert ws.resources == [{"id": "a", "type": "module", "url": "/local/other-card.js"},
                            {"id": "x", "type": "module", "url": "/local/sonorium/sonorium-cards.js?v=1.6.2"}]


def test_standalone_registers_sonoriums_own_url(tmp_path):
    ws = FakeWs()
    cards = make_cards(ws, tmp_path, install="docker", ha_url="http://ha.local:8123",
                       sonorium_url="http://192.168.1.20:8008/")
    assert run(cards.install())["state"] == "installed"
    assert ws.resources[0]["url"] == "http://192.168.1.20:8008/static/ha/sonorium-cards.js?v=1.6.2"
    assert not (tmp_path / "www").exists()  # nothing copied outside the add-on


@pytest.mark.parametrize("ha,mqtt,state,line", [
    (False, True, "needs_ha", "Dashboard cards need Home Assistant"),
    (True, False, "needs_mqtt", "Dashboard cards need MQTT"),
    (False, False, "needs_ha", "Dashboard cards need Home Assistant"),
])
def test_needs_both_links(tmp_path, ha, mqtt, state, line):
    ws = FakeWs()
    status = run(make_cards(ws, tmp_path, ha=ha, mqtt=mqtt).install())
    assert (status["state"], status["line"]) == (state, line)
    assert ws.sent == []


def test_mqtt_removed_later_shows_needs_mqtt(tmp_path):
    cards = make_cards(FakeWs(), tmp_path)
    run(cards.install())
    cards.mqtt_manager._removed = True
    assert cards.status()["state"] == "needs_mqtt"


@pytest.mark.parametrize("errors,reason", [
    ({"lovelace/resources/create": "unknown_command"}, "Home Assistant's dashboard resources are set in YAML"),
    ({"lovelace/resources/create": "unauthorized"}, "The access token isn't an administrator's"),
    ({"lovelace/resources": "unauthorized"}, "The access token isn't an administrator's"),
])
def test_refused_means_add_by_hand(tmp_path, errors, reason):
    status = run(make_cards(FakeWs(errors=errors), tmp_path).install())
    assert status == {"state": "by_hand", "line": "Dashboard cards: add by hand", "reason": reason}


def test_https_home_assistant_and_http_sonorium_is_added_by_hand(tmp_path):
    ws = FakeWs()
    cards = make_cards(ws, tmp_path, install="docker", ha_url="https://ha.example.com",
                       sonorium_url="http://192.168.1.20:8008")
    status = run(cards.install())
    assert status["state"] == "by_hand" and status["reason"] == "Home Assistant uses https and Sonorium http"
    assert ws.sent == []


def test_unreachable_home_assistant(tmp_path):
    async def down(messages):
        raise ConnectionError("refused")
    status = run(make_cards(down, tmp_path).install())
    assert status["state"] == "by_hand" and status["reason"] == "Home Assistant didn't answer"


def test_start_retries_while_home_assistant_is_down(tmp_path):
    ws = FakeWs()
    calls = []

    async def flaky(messages):
        calls.append(messages)
        if len(calls) == 1:
            raise ConnectionError("starting")
        return await ws(messages)
    cards = make_cards(flaky, tmp_path)
    run(cards.start(tries=3))
    assert cards.status()["state"] == "installed"


def test_entity_ids_from_the_registry(tmp_path):
    ws = FakeWs(registry=[
        {"platform": "mqtt", "unique_id": "sonorium_office_play", "entity_id": "switch.office_sounds"},
        {"platform": "mqtt", "unique_id": "other_thing", "entity_id": "switch.other"},
        {"platform": "hue", "unique_id": "sonorium_office_theme", "entity_id": "light.x"},
    ])
    cards = make_cards(ws, tmp_path)
    assert run(cards.refresh_entity_ids())
    assert cards.mqtt_manager.registered_entity_ids == {"sonorium_office_play": "switch.office_sounds"}
    cards.mqtt_manager.publish_channels.assert_awaited_once()


def test_helpers():
    assert m.cards.is_ours("/local/sonorium/sonorium-cards.js?v=1")
    assert m.cards.is_ours("http://10.0.0.2:8008/static/ha/sonorium-cards.js")
    assert not m.cards.is_ours("/local/sonorium-cards-other.js")
    assert m.cards.origin_of("http://HA.local:8123/lovelace") == "http://ha.local:8123"
    assert m.cards.origin_of("") == ""
    assert m.cards.resource_url("docker", "1.0", "") == ""


def test_the_bundled_card_file_exists():
    path = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium" / "web" / "static" / "ha" / "sonorium-cards.js"
    assert path.exists() and m.cards.CARD_FILE.name == path.name
