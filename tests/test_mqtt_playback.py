"""Home Assistant entities for sequences and playlists: discovery payloads, states and commands (mock MQTT)."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.state", "sonorium.ha.mqtt_entities") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture(autouse=True)
def no_sleep():
    with patch("asyncio.sleep", AsyncMock()):
        yield


def run(coro):
    import asyncio
    return asyncio.run(coro)


PLAYBACK = {
    "sequence": {"id": "s1", "name": "Storm", "running": True, "step": 2, "steps": 3,
                 "current_preset_id": "rain", "current_preset_name": "Rain", "next_change_at": "2026-10-10T12:00:00+00:00"},
    "playlist": {"id": "p1", "name": "Evening", "running": True, "entry_index": 0, "position": 0, "entries": 4,
                 "theme_id": "t1", "theme_name": "Thunder", "next_change_at": "2026-10-10T12:30:00+00:00"},
}


def make_entities(session, publish):
    return m.mqtt_entities.SessionMQTTEntities(
        session, "sonorium", publish, {"identifiers": ["sonorium_device"]},
        themes=[{"id": "t1", "name": "Thunder"}],
        get_presets_for_theme=lambda tid: [{"id": "rain", "name": "Rain"}],
        get_sequences_for_theme=lambda tid: [{"id": "s1", "name": "Storm"}, {"id": "s2", "name": "Calm"}],
        get_playlists=lambda: [{"id": "p1", "name": "Evening"}],
        get_playback=lambda s: PLAYBACK,
    )


def published(publish):
    return {c.args[0]: c.args[1] for c in publish.call_args_list}


def test_discovery_configs():
    session = m.state.Session(id="abc", name="Bedroom", theme_id="t1")
    publish = AsyncMock()
    run(make_entities(session, publish).publish_discovery())
    configs = {topic: json.loads(payload) for topic, payload in published(publish).items()}

    sequence = configs["homeassistant/select/sonorium_bedroom_sequence/config"]
    assert sequence["options"] == ["None", "Storm", "Calm"]
    assert sequence["command_topic"] == "sonorium/bedroom/sequence/set"
    assert sequence["state_topic"] == "sonorium/bedroom/sequence/state"
    assert sequence["unique_id"] == "sonorium_bedroom_sequence"
    assert sequence["default_entity_id"] == "select.sonorium_bedroom_sequence"
    assert sequence["name"] == "Bedroom Sequence" and sequence["device"] == {"identifiers": ["sonorium_device"]}

    playlist = configs["homeassistant/select/sonorium_bedroom_playlist/config"]
    assert playlist["options"] == ["None", "Evening"]
    assert playlist["command_topic"] == "sonorium/bedroom/playlist/set"

    button = configs["homeassistant/button/sonorium_bedroom_next/config"]
    assert button["command_topic"] == "sonorium/bedroom/next/set" and button["payload_press"] == "PRESS"
    assert button["default_entity_id"] == "button.sonorium_bedroom_next"

    step = configs["homeassistant/sensor/sonorium_bedroom_step/config"]
    assert step["state_topic"] == "sonorium/bedroom/step/state"
    assert step["json_attributes_topic"] == "sonorium/bedroom/step/attributes"


def test_states_show_sequence_playlist_and_step():
    session = m.state.Session(id="abc", name="Bedroom", theme_id="t1", sequence_id="s1", playlist_id="p1")
    publish = AsyncMock()
    entities = make_entities(session, publish)
    run(entities.publish_discovery())
    publish.reset_mock()
    run(entities.update_state())
    states = published(publish)
    assert states["sonorium/bedroom/sequence/state"] == "Storm"
    assert states["sonorium/bedroom/playlist/state"] == "Evening"
    assert states["sonorium/bedroom/step/state"] == "Evening: Thunder (1/4) | Storm: Rain (2/3)"
    assert json.loads(states["sonorium/bedroom/step/attributes"]) == PLAYBACK

    session.sequence_id = session.playlist_id = None
    entities.get_playback = lambda s: {"sequence": None, "playlist": None}
    run(entities.update_state())
    states = published(publish)
    assert states["sonorium/bedroom/sequence/state"] == "None"
    assert states["sonorium/bedroom/playlist/state"] == "None"
    assert states["sonorium/bedroom/step/state"] == "Off"


def test_remove_discovery_removes_the_new_entities_too():
    publish = AsyncMock()
    run(make_entities(m.state.Session(id="abc", name="Bedroom"), publish).remove_discovery())
    removed = published(publish)
    for topic in ("homeassistant/select/sonorium_bedroom_sequence/config",
                  "homeassistant/select/sonorium_bedroom_playlist/config",
                  "homeassistant/button/sonorium_bedroom_next/config",
                  "homeassistant/sensor/sonorium_bedroom_step/config"):
        assert removed[topic] == ""


def make_manager():
    store = m.state.StateStore()
    session = m.state.Session(id="abc", name="Bedroom", theme_id="t1", is_playing=True)
    store.sessions[session.id] = session
    store.state.playlists["p1"] = {"name": "Evening", "entries": []}
    sessions = MagicMock()
    sessions.get_speaker_summary.return_value = "Desk"
    sessions.theme_sequences.return_value = ({"s1": {"name": "Storm"}}, {})
    sessions.playback_status.return_value = {"sequence": None, "playlist": None}
    sessions.skip = AsyncMock(return_value="playlist")
    client = MagicMock()
    manager = m.mqtt_entities.SonoriumMQTTManager(store, sessions, client)
    manager._mqtt_publish = AsyncMock()
    run(manager.add_session_entities(session))
    return manager, sessions, client, session


def test_commands_start_and_stop_sequences_and_playlists():
    manager, sessions, client, session = make_manager()
    topics = {c.args[0] for c in client.subscribe.call_args_list}
    assert {"sonorium/bedroom/sequence/set", "sonorium/bedroom/playlist/set", "sonorium/bedroom/next/set"} <= topics

    run(manager.handle_command("sonorium/bedroom/sequence/set", "Storm"))
    sessions.start_sequence.assert_called_once_with("abc", "s1")
    run(manager.handle_command("sonorium/bedroom/sequence/set", "None"))
    sessions.stop_sequence.assert_called_once_with("abc")
    run(manager.handle_command("sonorium/bedroom/sequence/set", "Nope"))
    assert sessions.start_sequence.call_count == 1

    run(manager.handle_command("sonorium/bedroom/playlist/set", "Evening"))
    sessions.set_playlist.assert_called_once_with("abc", "p1")
    run(manager.handle_command("sonorium/bedroom/playlist/set", "None"))
    sessions.clear_playlist.assert_called_once_with("abc")

    run(manager.handle_command("sonorium/bedroom/next/set", "PRESS"))
    sessions.skip.assert_awaited_once_with("abc")


def test_next_on_a_stopped_channel_does_nothing():
    manager, sessions, client, session = make_manager()
    session.is_playing = False
    run(manager.handle_command("sonorium/bedroom/next/set", "PRESS"))
    sessions.skip.assert_not_awaited()


def test_a_sequence_that_cannot_start_is_logged_not_raised():
    manager, sessions, client, session = make_manager()
    sessions.start_sequence.side_effect = ValueError("no presets")
    run(manager.handle_command("sonorium/bedroom/sequence/set", "Storm"))  # no exception


def test_playlist_changes_republish_the_selects():
    manager, sessions, client, session = make_manager()
    manager.state.state.playlists["p2"] = {"name": "Morning", "entries": []}
    manager._mqtt_publish.reset_mock()
    run(manager.refresh_playlists())
    configs = {c.args[0]: c.args[1] for c in manager._mqtt_publish.call_args_list}
    assert json.loads(configs["homeassistant/select/sonorium_bedroom_playlist/config"])["options"] == ["None", "Evening", "Morning"]

    sessions.theme_sequences.return_value = ({"s1": {"name": "Storm"}, "s3": {"name": "Gale"}}, {})
    manager._mqtt_publish.reset_mock()
    run(manager.refresh_sequences("t1"))
    configs = {c.args[0]: c.args[1] for c in manager._mqtt_publish.call_args_list}
    assert json.loads(configs["homeassistant/select/sonorium_bedroom_sequence/config"])["options"] == ["None", "Storm", "Gale"]


def test_remove_all_entities_includes_the_new_ones():
    manager, sessions, client, session = make_manager()
    manager._mqtt_publish.reset_mock()
    run(manager.remove_all_entities())
    removed = {c.args[0] for c in manager._mqtt_publish.call_args_list if c.args[1] == ""}
    assert "homeassistant/select/sonorium_bedroom_sequence/config" in removed
    assert "homeassistant/button/sonorium_bedroom_next/config" in removed
    assert "homeassistant/sensor/sonorium_bedroom_step/config" in removed
