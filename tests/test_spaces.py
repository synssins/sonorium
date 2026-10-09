"""Sonorium's own floors and areas, and merging them with Home Assistant's by name."""

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium"


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def spaces():
    return load("core/spaces.py", "spaces_under_test")


@pytest.fixture
def registry_module():
    fmtr = types.ModuleType("fmtr")
    fmtr_tools = types.ModuleType("fmtr.tools")
    fmtr_tools.http = MagicMock()
    fmtr.tools = fmtr_tools
    runtime = types.ModuleType("sonorium.runtime")
    runtime.ha_configured = lambda: False
    runtime.ha_websocket_url = lambda url: url
    with patch.dict(sys.modules, {"fmtr": fmtr, "fmtr.tools": fmtr_tools, "sonorium.runtime": runtime}):
        spec = importlib.util.spec_from_file_location("spaces_registry_under_test", ROOT / "ha" / "registry.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["spaces_registry_under_test"] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
        yield module


def test_add_rename_delete(spaces):
    local = {}
    up = spaces.add_floor(local, "  Upstairs ")
    den = spaces.add_area(local, "Den", up["id"])
    assert (up["name"], den["floor_id"]) == ("Upstairs", "floor_upstairs")
    with pytest.raises(spaces.SpaceError):
        spaces.add_area(local, "den")  # same name, any case
    spaces.rename(local, "area", den["id"], "Study")
    speaker_settings = {"media_player.a": {"room": den["id"]}, "media_player.b": {"room": den["id"], "volume_offset": 5}}
    spaces.delete_floor(local, up["id"])
    assert local["areas"][0]["floor_id"] is None
    spaces.delete_area(local, den["id"], speaker_settings)
    assert speaker_settings == {"media_player.b": {"volume_offset": 5}}


def make_registry(module, ha_floors, ha_areas, local):
    registry = module.HARegistry("http://ha/api", "token")
    registry._ha_floors = {f.floor_id: f for f in ha_floors}
    registry._ha_areas = {a.area_id: a for a in ha_areas}
    registry.set_local_spaces_source(lambda: local)
    registry.set_speaker_settings_source(lambda: {"net:dlna:x": {"room": "area_office"}})
    registry.set_extra_speaker_source(lambda: [{"entity_id": "net:dlna:x", "name": "Speaker", "ip_address": "10.0.0.5"}])
    registry._rebuild()
    return registry


def test_same_name_merges_into_home_assistant(registry_module):
    m = registry_module
    local = {"floors": [{"id": "floor_ground", "name": "ground level"}, {"id": "floor_attic", "name": "Attic"}],
             "areas": [{"id": "area_office", "name": "OFFICE", "floor_id": "floor_ground"},
                       {"id": "area_loft", "name": "Loft", "floor_id": "floor_ground"}]}
    registry = make_registry(m, [m.Floor("ground_level", "Ground Level")],
                             [m.Area("office", "Office", floor_id="ground_level")], local)
    h = registry.hierarchy
    ground = next(f for f in h.floors if f.floor_id == "ground_level")
    assert ground.name == "Ground Level" and ground.source == ["ha", "local"]  # HA's name wins
    assert [a.name for a in ground.areas] == ["Loft", "Office"]  # local area follows the merged floor
    office = registry.get_area("office")
    assert office.source == ["ha", "local"] and office.local_id == "area_office"
    assert [s.entity_id for s in office.speakers] == ["net:dlna:x"]  # room saved with the Sonorium ID
    assert any(f.name == "Attic" and f.source == ["local"] for f in h.floors)
    # Channels saved with Sonorium IDs still find the speaker
    assert registry.resolve_selection(include_areas=["area_office"]) == ["net:dlna:x"]
    assert registry.resolve_selection(include_floors=["floor_ground"]) == ["net:dlna:x"]


def test_without_home_assistant_only_local_spaces(registry_module):
    m = registry_module
    local = {"floors": [{"id": "floor_ground", "name": "Ground"}],
             "areas": [{"id": "area_office", "name": "Office", "floor_id": "floor_ground"}]}
    registry = make_registry(m, [], [], local)
    floor = registry.hierarchy.floors[0]
    assert (floor.floor_id, floor.source, [a.area_id for a in floor.areas]) == ("floor_ground", ["local"], ["area_office"])
    assert registry.resolve_selection(include_floors=["floor_ground"]) == ["net:dlna:x"]
