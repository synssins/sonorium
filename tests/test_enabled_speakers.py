"""Speakers disabled in Settings > Speakers are never played, even by channels saved earlier."""

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"


def load_session_manager():
    obs = types.ModuleType("sonorium.obs")
    obs.logger = Mock()
    obs.logger.instrument.side_effect = lambda *a, **k: (lambda f: f)
    saved = {n: sys.modules.get(n) for n in ("sonorium", "sonorium.obs", "sonorium.core", "sonorium.core.state")}
    try:
        sys.modules["sonorium"] = types.ModuleType("sonorium")
        sys.modules["sonorium.obs"] = obs
        sys.modules["sonorium.core"] = types.ModuleType("sonorium.core")
        spec = importlib.util.spec_from_file_location("sonorium.core.state", ROOT / "core" / "state.py")
        state = importlib.util.module_from_spec(spec)
        sys.modules["sonorium.core.state"] = state
        spec.loader.exec_module(state)
        spec = importlib.util.spec_from_file_location("session_manager_under_test", ROOT / "core" / "session_manager.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, state
    finally:
        for name, mod in saved.items():
            if mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


def make_manager(enabled):
    module, state = load_session_manager()
    manager = module.SessionManager.__new__(module.SessionManager)
    manager.state = SimpleNamespace(settings=SimpleNamespace(enabled_speakers=enabled), speaker_groups={})
    manager.registry = Mock()
    manager.registry.resolve_selection.return_value = ["media_player.a", "media_player.disabled", "net:dlna:x"]
    session = SimpleNamespace(speaker_group_id=None, adhoc_selection=SimpleNamespace(
        include_floors=["ground"], include_areas=[], include_speakers=[], exclude_areas=[], exclude_speakers=[]))
    return manager, session


def test_disabled_speakers_are_not_played():
    manager, session = make_manager(["media_player.a", "net:dlna:x"])
    assert manager.get_resolved_speakers(session) == ["media_player.a", "net:dlna:x"]


def test_no_enabled_list_means_all_enabled():
    manager, session = make_manager([])
    assert manager.get_resolved_speakers(session) == ["media_player.a", "media_player.disabled", "net:dlna:x"]


def test_all_disabled_plays_nothing():
    manager, session = make_manager(["__none__"])
    assert manager.get_resolved_speakers(session) == []
