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


def make_manager(enabled, exact=True):
    module, state = load_session_manager()
    manager = module.SessionManager.__new__(module.SessionManager)
    settings = state.SonoriumSettings(enabled_speakers=list(enabled), enabled_speakers_exact=exact)
    manager.state = SimpleNamespace(settings=settings, speaker_groups={})
    manager.registry = Mock()
    manager.registry.resolve_selection.return_value = ["media_player.a", "media_player.disabled", "net:dlna:x"]
    session = SimpleNamespace(speaker_group_id=None, adhoc_selection=SimpleNamespace(
        include_floors=["ground"], include_areas=[], include_speakers=[], exclude_areas=[], exclude_speakers=[]))
    return manager, session


def test_disabled_speakers_are_not_played():
    manager, session = make_manager(["media_player.a", "net:dlna:x"])
    assert manager.get_resolved_speakers(session) == ["media_player.a", "net:dlna:x"]


def test_only_switched_on_speakers_are_played():
    manager, session = make_manager([])
    assert manager.get_resolved_speakers(session) == []


def test_old_empty_list_still_means_all_until_converted():
    manager, session = make_manager([], exact=False)
    assert manager.get_resolved_speakers(session) == ["media_player.a", "media_player.disabled", "net:dlna:x"]


def test_old_none_marker_plays_nothing():
    manager, session = make_manager(["__none__"], exact=False)
    assert manager.get_resolved_speakers(session) == []


def settings_module():
    return load_session_manager()[1]


def test_old_state_file_loads_as_not_exact():
    state = settings_module()
    assert state.SonoriumSettings.from_dict({"enabled_speakers": []}).enabled_speakers_exact is False
    assert state.SonoriumSettings().enabled_speakers_exact is True  # new installs


def test_migration_keeps_what_was_visible():
    state = settings_module()
    s = state.SonoriumSettings.from_dict({"enabled_speakers": []})
    assert s.migrate_enabled_speakers(["a", "b"]) is True
    assert (s.enabled_speakers, s.enabled_speakers_exact) == (["a", "b"], True)
    s = state.SonoriumSettings.from_dict({"enabled_speakers": ["__none__"]})
    s.migrate_enabled_speakers(["a", "b"])
    assert s.enabled_speakers == []
    s = state.SonoriumSettings.from_dict({"enabled_speakers": ["b"]})
    s.migrate_enabled_speakers(["a", "b"])
    assert s.enabled_speakers == ["b"]


def test_migration_waits_for_the_speaker_list():
    state = settings_module()
    s = state.SonoriumSettings.from_dict({"enabled_speakers": []})
    assert s.migrate_enabled_speakers([]) is False
    assert s.enabled_speakers_exact is False
    assert s.speaker_enabled("anything") is True  # old meaning kept meanwhile


def test_migration_keeps_entries_for_missing_speakers():
    state = settings_module()
    s = state.SonoriumSettings.from_dict({"enabled_speakers": ["b", "gone"]})
    s.migrate_enabled_speakers(["a", "b"])
    assert s.enabled_speakers == ["b", "gone"]


def preset_manager(presets_by_theme):
    module, _ = load_session_manager()
    manager = module.SessionManager.__new__(module.SessionManager)
    manager._theme_presets = lambda theme_id: presets_by_theme.get(theme_id, {})
    return manager


def test_new_theme_brings_its_own_preset():
    manager = preset_manager({
        "forest": {"calm": {"is_default": True}, "storm": {}},
        "tavern": {"slow_night": {}},
    })
    assert manager.preset_for_theme("forest", "slow_night") == "calm"  # old theme's preset never carries over
    assert manager.preset_for_theme("forest", "storm") == "storm"
    assert manager.preset_for_theme("forest", None) == "calm"
    assert manager.preset_for_theme("forest", "") is None  # none, on purpose
    assert manager.preset_for_theme("tavern", None) is None  # no default set
