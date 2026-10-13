"""
The modules other Sonorium apps use without the web server: theme editing
(core/theme_edit.py), playlists (core/playlists.py) and network speakers
(sonorium/network). They import nothing of the server, the settings, Home
Assistant, MQTT, plugins or sign-in (checked in a separate process with those
blocked), playlists load, save and check as the server's routes do, and the
server still builds its network speaker service the same way.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "sonorium_addon"
TESTS = ROOT / "tests"
PACKAGE = ADDON / "sonorium"

BLOCKED_PACKAGES = ("fastapi", "uvicorn", "starlette", "pydantic_settings", "pydantic", "paho")
BLOCKED_SONORIUM = ("sonorium.settings", "sonorium.runtime", "sonorium.web", "sonorium.web_server", "sonorium.api",
                    "sonorium.auth", "sonorium.plugins", "sonorium.ha", "sonorium.client", "sonorium.alerts",
                    "sonorium.device", "sonorium.core.session_manager", "sonorium.core.group_manager",
                    "sonorium.core.state")

ISOLATION_SCRIPT = r'''
import asyncio, sys
from pathlib import Path
BLOCKED = {blocked!r}
BLOCKED_SONORIUM = {blocked_sonorium!r}

def blocked(name):
    return name.split(".")[0] in BLOCKED or any(name == b or name.startswith(b + ".") for b in BLOCKED_SONORIUM)

class Blocker:
    def find_spec(self, name, path=None, target=None):
        if blocked(name):
            raise ImportError(f"blocked: {{name}}")
        return None

sys.meta_path.insert(0, Blocker())
for name in BLOCKED:
    sys.modules[name] = None  # a sentinel: importing it raises ImportError

from engine_theme import make_theme, make_second_theme
from sonorium.core import playlists, theme_edit as te
from sonorium.network import discovery, heos, manual, models, router, service, streaming

root = Path(sys.argv[1]) / "themes"
folder = make_theme(root)
make_second_theme(root)

# Theme editing on a folder
te.set_track(folder, "Drone", volume=0.4, muted=False, playback_mode="continuous", volume_low=0.1, fade_in=2)
te.set_group(folder, "Birds", volume=0.5, mode="merry_go_round", crossfade=8)
made = te.new_preset(folder, "Evening")
te.save_preset(folder, made["preset_id"])
te.load_preset(folder, "calm")
te.set_default_preset(folder, made["preset_id"])
te.rename_preset(folder, made["preset_id"], "Late evening")
te.create_sequence(folder, {{"name": "Night", "presets": ["calm", made["preset_id"]]}})
te.move_track(folder, "Chirp", "Small")
te.delete_preset(folder, "quiet")
assert te.get_preset(folder, made["preset_id"])["name"] == "Late evening"

# Details, an imported preset, a step's tracks, groups, linked files, uploads (with their file operations)
assert te.set_details(folder, name="Garden", description="d", icon="x", categories=["Nature"],
                      short_file_threshold=10)["name"] == "Garden"
assert te.import_preset(folder, '{{"Drone": {{"volume": 0.2}}}}', "Shared")["preset_id"] == "shared"
assert "in" in te.transition_tracks(folder, "calm", made["preset_id"])
assert te.rename_group(folder, "Birds", "Flock")["ops"][0] == {{"op": "move", "src": "Birds", "dst": "Flock"}}
second = root / "Second Theme"
assert te.add_links(folder, second, ["Tone"], "Flock")["links"][0]["added"]
assert te.remove_link(folder, "Flock/Tone")["removed"] == "Flock/Tone"
assert te.add_links(folder, second, ["Tone"])["links"][0]["key"] == "Tone"
copied = te.copy_link_in(folder, "Tone", second)
assert copied["track"] == "Tone" and copied["ops"][0] == {{"op": "copy", "src": "Tone.wav", "dst": "Tone.wav"}}
assert te.upload(folder, "Hum.wav", (folder / "Drone.wav").read_bytes(), "Hums")["track"] == "Hums/Hum"
assert te.delete_group(folder, "Flock")["folder_removed"]
te.convert_on_load = False
te.set_track(folder, "Drone", volume=0.5)
assert te.load(folder, convert=False).tracks["Drone"].volume == 0.5
from sonorium.core import theme_metadata
assert theme_metadata.track_seconds(folder / "metadata.json") is None

# Playlists
lookups = playlists.folder_lookups(root)
store = {{}}
pid, saved = playlists.create_playlist(store, {{"name": "Evening", "entries": [
    {{"theme_id": "engine-test-theme", "sequence_id": "night"}}, {{"theme_id": "engine-second-theme"}}]}}, *lookups)
playlists.save_playlists(Path(sys.argv[1]) / "playlists.json", store)
assert playlists.load_playlists(Path(sys.argv[1]) / "playlists.json") == store

# Network speakers: configuration passed in, nothing played
config = Path(sys.argv[1]) / "config"
config.mkdir()
svc = service.NetworkSpeakerService(config_dir=config)
assert svc.discovery._speakers_file == config / "network_speakers.json"
assert isinstance(svc.streaming, streaming.NetworkStreamingManager) and svc.speakers == {{}}
assert "http://h:8008/stream/1" in streaming.didl_metadata("http://h:8008/stream/1")
assert router.split_speaker_ids(["net:sonos:x", "media_player.y"]) == (["media_player.y"], ["net:sonos:x"])

loaded = sorted(m for m in sys.modules if sys.modules[m] is not None and blocked(m))
assert not loaded, loaded
print("CORE-MODULES-OK")
'''


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ADDON), str(TESTS)])
    env["SONORIUM_LOG_LEVEL"] = "warning"
    env["PYTHONUTF8"] = "1"
    return env


def test_core_modules_import_and_work_without_the_server(tmp_path):
    pytest.importorskip("numpy")
    script = ISOLATION_SCRIPT.format(blocked=BLOCKED_PACKAGES, blocked_sonorium=BLOCKED_SONORIUM)
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], env=_env(), capture_output=True,
                            text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    assert "CORE-MODULES-OK" in result.stdout


# --- playlists ---

def _playlists():
    import importlib.util
    import logging
    import types
    from unittest.mock import patch
    package = types.ModuleType("sonorium")
    package.__path__ = [str(PACKAGE)]
    obs = types.ModuleType("sonorium.obs")
    obs.logger = logging.getLogger("sonorium-core-modules-test")
    core = types.ModuleType("sonorium.core")
    core.__path__ = [str(PACKAGE / "core")]
    with patch.dict(sys.modules, {"sonorium": package, "sonorium.obs": obs, "sonorium.core": core}):
        spec = importlib.util.spec_from_file_location("sonorium.core.playlists", PACKAGE / "core" / "playlists.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        presets_spec = importlib.util.spec_from_file_location("sonorium.core.theme_presets",
                                                              PACKAGE / "core" / "theme_presets.py")
        presets = importlib.util.module_from_spec(presets_spec)
        sys.modules[presets_spec.name] = presets
        presets_spec.loader.exec_module(presets)
        yield module


@pytest.fixture
def pl():
    yield from _playlists()


LOOKUPS = (lambda t: t in ("a", "b"), lambda t: {"calm"} if t == "a" else set(), lambda t: {"walk"} if t == "a" else set())


def test_playlists_are_made_changed_and_deleted_as_the_routes_do(pl):
    store = {}
    pid, saved = pl.create_playlist(store, {"name": " Evening ", "entries": [{"theme_id": "a", "preset_id": "calm"}]},
                                    *LOOKUPS)
    assert store == {pid: saved} and saved["name"] == "Evening" and saved["play_min"] == pl.DEFAULT_PLAY_MIN
    with pytest.raises(pl.PlaylistConflict, match="A playlist with that name already exists"):
        pl.create_playlist(store, {"name": "evening", "entries": [{"theme_id": "b"}]}, *LOOKUPS)
    with pytest.raises(pl.PlaylistError, match="unknown theme 'c'"):
        pl.create_playlist(store, {"name": "Other", "entries": [{"theme_id": "c"}]}, *LOOKUPS)
    other, _ = pl.create_playlist(store, {"name": "Other", "entries": [{"theme_id": "b"}]}, *LOOKUPS)
    with pytest.raises(pl.PlaylistConflict):
        pl.update_playlist(store, other, {"name": "EVENING"}, *LOOKUPS)
    changed = pl.update_playlist(store, pid, {"order": "shuffle"}, *LOOKUPS)
    assert changed["order"] == "shuffle" and changed["name"] == "Evening" and store[pid] is changed
    with pytest.raises(KeyError):
        pl.update_playlist(store, "nope", {}, *LOOKUPS)
    assert pl.delete_playlist(store, other)["name"] == "Other" and list(store) == [pid]
    assert issubclass(pl.PlaylistConflict, pl.PlaylistError)


def test_playlists_load_and_save_keep_the_rest_of_the_file(pl, tmp_path):
    state = tmp_path / "state.json"
    assert pl.load_playlists(state) == {}
    state.write_text(json.dumps({"sessions": {"x": 1}, "playlists": {"p": {"name": "P"}, "bad": 3}}), encoding="utf-8")
    assert pl.load_playlists(state) == {"p": {"name": "P"}}
    pl.save_playlists(state, {"q": {"name": "Q"}})
    assert json.loads(state.read_text(encoding="utf-8")) == {"sessions": {"x": 1}, "playlists": {"q": {"name": "Q"}}}


def test_same_name_ignores_case_spaces_and_the_item_itself(pl):
    items = {"1": {"name": "Rain "}, "2": {"name": "Wind"}}
    assert pl.same_name(items, " rain")
    assert not pl.same_name(items, "rain", except_id="1")
    assert not pl.same_name(items, "Storm")


# --- the server wires the network speakers as before ---

def test_the_server_builds_the_network_service_from_its_config_folder():
    tree = ast.parse((PACKAGE / "api.py").read_text(encoding="utf-8"))
    method = next(node for node in ast.walk(tree)
                  if isinstance(node, ast.FunctionDef) and node.name == "_init_network_speakers")
    calls = [ast.unparse(node) for node in ast.walk(method)
             if isinstance(node, ast.Call) and ast.unparse(node.func) == "NetworkSpeakerService"]
    assert calls == ["NetworkSpeakerService(config_dir=runtime.CONNECTION_FILE.parent)"]


@pytest.mark.parametrize("relative", ["network/discovery.py", "network/streaming.py", "network/models.py",
                                      "network/heos.py", "network/manual.py", "network/service.py",
                                      "network/__init__.py", "core/theme_edit.py", "core/playlists.py",
                                      "core/transitions.py", "core/theme_groups.py"])
def test_no_server_module_is_named_anywhere_in_the_core_modules(relative):
    """
    Not even inside a function: the modules other apps copy never reach for the
    server. (network/router.py, the server's Home Assistant + network router,
    names Home Assistant's controller for type checking only.)
    """
    tree = ast.parse((PACKAGE / relative).read_text(encoding="utf-8"))
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    bad = [n for n in names if n.split(".")[0] in BLOCKED_PACKAGES
           or any(n == b or n.startswith(b + ".") for b in BLOCKED_SONORIUM)]
    assert not bad, bad
