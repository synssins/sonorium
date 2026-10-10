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

NONE = "(None)"  # the "nothing" option (HA reads a bare "None" state as unknown)


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
    sent = published(publish)
    configs = {topic: json.loads(payload) for topic, payload in sent.items()
               if topic.startswith("homeassistant/") and payload}

    preset = configs["homeassistant/select/sonorium_bedroom_preset/config"]
    assert preset["options"] == [NONE, "Rain", "Storm (sequence)", "Calm (sequence)"]
    assert preset["command_topic"] == "sonorium/bedroom/preset/set"

    # The separate Sequence select is gone; its retained config (and state) is emptied so HA drops it
    assert "homeassistant/select/sonorium_bedroom_sequence/config" not in configs
    assert sent["homeassistant/select/sonorium_bedroom_sequence/config"] == ""
    assert sent["sonorium/bedroom/sequence/state"] == ""

    playlist = configs["homeassistant/select/sonorium_bedroom_playlist/config"]
    assert playlist["options"] == [NONE, "Evening"]
    assert playlist["command_topic"] == "sonorium/bedroom/playlist/set"

    button = configs["homeassistant/button/sonorium_bedroom_next/config"]
    assert button["command_topic"] == "sonorium/bedroom/next/set" and button["payload_press"] == "PRESS"
    assert button["default_entity_id"] == "button.sonorium_bedroom_next"

    step = configs["homeassistant/sensor/sonorium_bedroom_step/config"]
    assert step["state_topic"] == "sonorium/bedroom/step/state"
    assert step["json_attributes_topic"] == "sonorium/bedroom/step/attributes"


def test_selects_get_a_none_state_with_their_discovery():
    session = m.state.Session(id="abc", name="Bedroom", theme_id="t1")
    publish = AsyncMock()
    run(make_entities(session, publish).publish_discovery())
    topics = [c.args[0] for c in publish.call_args_list]
    sent = published(publish)
    # Each select's state right after its config, so HA never shows it as unknown
    for name in ("preset", "playlist"):
        config = topics.index(f"homeassistant/select/sonorium_bedroom_{name}/config")
        assert topics[config + 1] == f"sonorium/bedroom/{name}/state"
        assert sent[f"sonorium/bedroom/{name}/state"] == NONE
        assert NONE in json.loads(sent[f"homeassistant/select/sonorium_bedroom_{name}/config"])["options"]
    assert NONE.lower() != "none"  # HA's MQTT select turns a "None" state into unknown


def test_preset_state_shows_the_running_sequence_then_the_preset():
    session = m.state.Session(id="abc", name="Bedroom", theme_id="t1", preset_id="rain", sequence_id="s1", playlist_id="p1")
    publish = AsyncMock()
    entities = make_entities(session, publish)
    run(entities.publish_discovery())
    publish.reset_mock()
    run(entities.update_state())
    states = published(publish)
    assert states["sonorium/bedroom/preset/state"] == "Storm (sequence)"
    assert states["sonorium/bedroom/playlist/state"] == "Evening"
    assert "sonorium/bedroom/sequence/state" not in states
    assert states["sonorium/bedroom/step/state"] == "Evening: Thunder (1/4) | Storm: Rain (2/3)"
    assert json.loads(states["sonorium/bedroom/step/attributes"]) == PLAYBACK

    session.sequence_id = session.playlist_id = None
    entities.get_playback = lambda s: {"sequence": None, "playlist": None}
    run(entities.update_state())
    states = published(publish)
    assert states["sonorium/bedroom/preset/state"] == "Rain"
    assert states["sonorium/bedroom/playlist/state"] == NONE
    assert states["sonorium/bedroom/step/state"] == "Off"

    session.preset_id = None
    run(entities.update_state())
    assert published(publish)["sonorium/bedroom/preset/state"] == NONE


def test_remove_discovery_removes_the_new_entities_and_the_retired_sequence_select():
    publish = AsyncMock()
    run(make_entities(m.state.Session(id="abc", name="Bedroom"), publish).remove_discovery())
    removed = published(publish)
    for topic in ("homeassistant/select/sonorium_bedroom_sequence/config",
                  "homeassistant/select/sonorium_bedroom_preset/config",
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
    manager.get_presets_for_theme = lambda tid: [{"id": "rain", "name": "Rain"}]
    manager._mqtt_publish = AsyncMock()
    run(manager.add_session_entities(session))
    return manager, sessions, client, session


def test_preset_select_picks_presets_and_starts_sequences():
    manager, sessions, client, session = make_manager()
    topics = {c.args[0] for c in client.subscribe.call_args_list}
    assert {"sonorium/bedroom/preset/set", "sonorium/bedroom/playlist/set", "sonorium/bedroom/next/set"} <= topics
    assert "sonorium/bedroom/sequence/set" not in topics

    run(manager.handle_command("sonorium/bedroom/preset/set", "Storm (sequence)"))
    sessions.update.assert_called_with("abc", preset_id="seq:s1")
    run(manager.handle_command("sonorium/bedroom/preset/set", "Rain"))
    sessions.update.assert_called_with("abc", preset_id="rain")
    for none in (NONE, "None", ""):
        sessions.update.reset_mock()
        run(manager.handle_command("sonorium/bedroom/preset/set", none))
        sessions.update.assert_called_once_with("abc", preset_id="")  # no preset: stops a sequence too
    sessions.update.reset_mock()
    run(manager.handle_command("sonorium/bedroom/preset/set", "Nope"))
    sessions.update.assert_not_called()


def test_playlist_and_next_commands():
    manager, sessions, client, session = make_manager()
    run(manager.handle_command("sonorium/bedroom/playlist/set", "Evening"))
    sessions.set_playlist.assert_called_once_with("abc", "p1")
    run(manager.handle_command("sonorium/bedroom/playlist/set", NONE))
    sessions.clear_playlist.assert_called_once_with("abc")
    run(manager.handle_command("sonorium/bedroom/playlist/set", "None"))
    assert sessions.clear_playlist.call_count == 2

    run(manager.handle_command("sonorium/bedroom/next/set", "PRESS"))
    sessions.skip.assert_awaited_once_with("abc")


def test_next_on_a_stopped_channel_does_nothing():
    manager, sessions, client, session = make_manager()
    session.is_playing = False
    run(manager.handle_command("sonorium/bedroom/next/set", "PRESS"))
    sessions.skip.assert_not_awaited()


def test_a_sequence_that_cannot_start_is_logged_not_raised():
    manager, sessions, client, session = make_manager()
    sessions.update.side_effect = ValueError("no presets")
    run(manager.handle_command("sonorium/bedroom/preset/set", "Storm (sequence)"))  # no exception
    sessions.update.side_effect = LookupError("gone")
    manager._selected_session_id = "abc"
    run(manager.handle_command("sonorium/preset/set", "Storm (sequence)"))  # the global select too


def test_global_preset_select_offers_sequences_and_shows_the_running_one():
    manager, sessions, client, session = make_manager()
    manager._selected_session_id = "abc"
    session.sequence_id = "s1"
    manager._mqtt_publish.reset_mock()
    run(manager._update_global_control_states())
    sent = {c.args[0]: c.args[1] for c in manager._mqtt_publish.call_args_list}
    assert json.loads(sent["homeassistant/select/sonorium_preset/config"])["options"] == [NONE, "Rain", "Storm (sequence)"]
    assert sent["sonorium/preset/state"] == "Storm (sequence)"
    run(manager.handle_command("sonorium/preset/set", "Storm (sequence)"))
    sessions.update.assert_called_with("abc", preset_id="seq:s1")

    manager._selected_session_id = None
    run(manager._update_global_control_states())
    sent = {c.args[0]: c.args[1] for c in manager._mqtt_publish.call_args_list}
    assert sent["sonorium/preset/state"] == NONE


def test_playlist_and_sequence_changes_republish_the_selects():
    manager, sessions, client, session = make_manager()
    manager.state.state.playlists["p2"] = {"name": "Morning", "entries": []}
    manager._mqtt_publish.reset_mock()
    run(manager.refresh_playlists())
    configs = {c.args[0]: c.args[1] for c in manager._mqtt_publish.call_args_list}
    assert json.loads(configs["homeassistant/select/sonorium_bedroom_playlist/config"])["options"] == [NONE, "Evening", "Morning"]

    sessions.theme_sequences.return_value = ({"s1": {"name": "Storm"}, "s3": {"name": "Gale"}}, {})
    manager._mqtt_publish.reset_mock()
    run(manager.refresh_sequences("t1"))
    configs = {c.args[0]: c.args[1] for c in manager._mqtt_publish.call_args_list}
    assert json.loads(configs["homeassistant/select/sonorium_bedroom_preset/config"])["options"] == [
        NONE, "Rain", "Storm (sequence)", "Gale (sequence)"]
    assert "homeassistant/select/sonorium_bedroom_sequence/config" not in configs or \
        configs["homeassistant/select/sonorium_bedroom_sequence/config"] == ""


def test_remove_all_entities_includes_the_new_ones_and_the_retired_sequence_select():
    manager, sessions, client, session = make_manager()
    manager._mqtt_publish.reset_mock()
    run(manager.remove_all_entities())
    removed = {c.args[0] for c in manager._mqtt_publish.call_args_list if c.args[1] == ""}
    assert "homeassistant/select/sonorium_bedroom_sequence/config" in removed
    assert "homeassistant/button/sonorium_bedroom_next/config" in removed
    assert "homeassistant/sensor/sonorium_bedroom_step/config" in removed
