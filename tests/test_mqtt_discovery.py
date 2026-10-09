"""MQTT discovery identity regressions, without a broker or addon dependencies."""

import ast
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


def load_addon_module(name, relative_path):
    root = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"
    spec = importlib.util.spec_from_file_location(name, root / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class MQTTDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        obs = types.ModuleType("sonorium.obs")
        obs.logger = Mock()
        obs.logger.instrument.side_effect = lambda *args, **kwargs: lambda method: method
        with patch.dict(sys.modules, {"sonorium.obs": obs}):
            cls.state_module = load_addon_module("mqtt_test_state", "core/state.py")
            cls.entities_module = load_addon_module("mqtt_test_entities", "ha/mqtt_entities.py")

    def setUp(self):
        self.publish = AsyncMock()
        self.session = self.state_module.Session(id="session-123", name="Bedroom")

    def make_entities(self, session):
        return self.entities_module.SessionMQTTEntities(
            session, "sonorium", self.publish, {"identifiers": ["sonorium_device"]}
        )

    def make_session_endpoints(self):
        path = Path(__file__).resolve().parents[1] / "sonorium_addon/sonorium/web/api_v2.py"
        source = ast.parse(path.read_text(encoding="utf-8"))
        factory = next(
            node for node in source.body
            if isinstance(node, ast.FunctionDef) and node.name == "create_api_router"
        )
        endpoints = [
            node for node in factory.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name in {"create_session", "update_session", "delete_session"}
        ]
        for endpoint in endpoints:
            endpoint.decorator_list = []
        module = ast.Module(body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            *endpoints,
        ], type_ignores=[])
        namespace = {
            "session_manager": Mock(),
            "mqtt_manager": AsyncMock(),
            "logger": Mock(),
            "_session_to_response": lambda session, manager: session,
        }
        exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
        return namespace

    async def test_web_create_publishes_discovery_and_state(self):
        endpoints = self.make_session_endpoints()
        endpoints["session_manager"].create.return_value = self.session
        await endpoints["create_session"](Mock())
        endpoints["mqtt_manager"].add_session_entities.assert_awaited_once_with(self.session)
        endpoints["mqtt_manager"].sync_all_states.assert_awaited_once()

    async def test_web_delete_removes_retained_discovery(self):
        endpoints = self.make_session_endpoints()
        endpoints["session_manager"].delete.return_value = True
        await endpoints["delete_session"](self.session.id)
        endpoints["mqtt_manager"].remove_session_entities.assert_awaited_once_with(self.session.id)
        endpoints["mqtt_manager"].sync_all_states.assert_awaited_once()

    async def test_web_speaker_edit_syncs_mqtt_without_rename(self):
        endpoints = self.make_session_endpoints()
        endpoints["session_manager"].get.return_value = self.session
        endpoints["session_manager"].update.return_value = (self.session, [], [])
        request = Mock(volume=None)
        await endpoints["update_session"](self.session.id, request)
        endpoints["mqtt_manager"].refresh_session_discovery.assert_not_awaited()
        endpoints["mqtt_manager"].sync_all_states.assert_awaited_once()

    async def test_new_session_subscribes_to_its_commands(self):
        store = self.state_module.StateStore()
        store.sessions[self.session.id] = self.session
        client = Mock()
        sessions = Mock()
        sessions.get_speaker_summary.return_value = "Study"
        manager = self.entities_module.SonoriumMQTTManager(store, sessions, client)
        manager._mqtt_publish = self.publish
        await manager.add_session_entities(self.session)
        topics = {call.args[0] for call in client.subscribe.call_args_list}
        self.assertIn("sonorium/bedroom/play/set", topics)
        self.assertIn("sonorium/bedroom/volume/set", topics)

    async def test_session_update_refreshes_presets_before_state(self):
        store = self.state_module.StateStore()
        manager = self.entities_module.SonoriumMQTTManager(store, Mock(), Mock())
        entities = Mock()
        entities.update_preset_options = AsyncMock()
        entities.update_state = AsyncMock()
        entities.update_speakers_sensor = AsyncMock()
        manager._session_entities[self.session.id] = entities
        await manager.update_session_state(self.session)
        self.assertEqual(entities.mock_calls[:2], [
            unittest.mock.call.update_preset_options(), unittest.mock.call.update_state(),
        ])

    async def test_rename_and_restart_keep_all_discovery_configs(self):
        entities = self.make_entities(self.session)
        await entities.publish_discovery()
        original = {
            call.args[0]: json.loads(call.args[1])
            for call in self.publish.call_args_list
        }
        self.session.name = "Night Mode"
        restarted = self.make_entities(
            self.state_module.Session.from_dict(self.session.to_dict())
        )
        self.publish.reset_mock()
        await restarted.publish_discovery()
        self.assertEqual(len(self.publish.call_args_list), 6)
        for call in self.publish.call_args_list:
            topic, payload = call.args
            config = json.loads(payload)
            previous = original[topic]
            for key in ("unique_id", "default_entity_id", "state_topic", "command_topic"):
                self.assertEqual(config.get(key), previous.get(key))
            self.assertTrue(config["name"].startswith("Night Mode "))
            self.assertTrue(call.kwargs["retain"])

    def test_slug_survives_state_file_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "state.json"
            store = self.state_module.StateStore(state_file)
            store.sessions[self.session.id] = self.session
            self.session.name = "Night Mode"
            store.save()
            restarted = self.state_module.StateStore(state_file)
            restarted.load()
            loaded = restarted.sessions[self.session.id]
            self.assertEqual(loaded.name, "Night Mode")
            self.assertEqual(loaded.get_entity_slug(), "bedroom")

    def test_legacy_session_preserves_existing_slug(self):
        session = self.state_module.Session.from_dict(
            {"id": "legacy", "name": "Bedroom Level"}
        )
        self.assertEqual(session.get_entity_slug(), "bedroom_level")
        self.assertEqual(session.to_dict()["entity_slug"], "bedroom_level")

    def test_name_without_alphanumeric_characters_uses_session_id(self):
        session = self.state_module.Session(id="session-123", name="!!!")
        self.assertEqual(session.get_entity_slug(), "session-123")
        session.name = "Bedroom"
        self.assertEqual(session.get_entity_slug(), "session-123")

    async def test_removal_after_restart_clears_original_topics(self):
        original = self.make_entities(self.session)
        await original.publish_discovery()
        topics = {call.args[0] for call in self.publish.call_args_list}
        self.session.name = "Night Mode"
        restarted = self.make_entities(
            self.state_module.Session.from_dict(self.session.to_dict())
        )
        self.publish.reset_mock()
        await restarted.remove_discovery()
        self.assertEqual({call.args[0] for call in self.publish.call_args_list}, topics)
        for call in self.publish.call_args_list:
            self.assertEqual(call.args[1], "")
            self.assertTrue(call.kwargs["retain"])

    async def test_command_subscriptions_after_rename_use_original_slug(self):
        self.session.name = "Night Mode"
        session = self.state_module.Session.from_dict(self.session.to_dict())
        store = self.state_module.StateStore()
        store.sessions[session.id] = session
        client = Mock()
        manager = self.entities_module.SonoriumMQTTManager(store, Mock(), client)
        await manager._subscribe_commands()
        topics = {call.args[0] for call in client.subscribe.call_args_list}
        for suffix in ("play", "theme", "preset", "volume"):
            self.assertIn(f"sonorium/bedroom/{suffix}/set", topics)
            self.assertNotIn(f"sonorium/night_mode/{suffix}/set", topics)


if __name__ == "__main__":
    unittest.main()