"""
Standalone network speakers (sonorium/network): speaker IDs, routing of
play/stop/volume by ID prefix, the speaker hierarchy, persistence, and that
the Home Assistant add-on never touches any of it. No network access: every
protocol call is mocked.
"""

import ast
import asyncio
import json
import logging
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "sonorium_addon" / "sonorium"


def _is_sonorium(name):
    return name == "sonorium" or name.startswith("sonorium.")


@pytest.fixture(scope="module", autouse=True)
def addon_package():
    """
    Import the add-on's `sonorium.network` without its other dependencies
    (fmtr, HA, MQTT), then put back whatever `sonorium` other tests use
    (test_linkplay_integration imports the Windows app's package).
    """
    saved = {name: module for name, module in sys.modules.items() if _is_sonorium(name)}
    for name in saved:
        del sys.modules[name]
    package = types.ModuleType("sonorium")
    package.__path__ = [str(PACKAGE)]
    obs = types.ModuleType("sonorium.obs")
    obs.logger = logging.getLogger("sonorium-network-test")
    sys.modules.update({"sonorium": package, "sonorium.obs": obs})

    import importlib
    g = globals()
    g["network"] = importlib.import_module("sonorium.network")
    g["discovery_mod"] = importlib.import_module("sonorium.network.discovery")
    g["streaming_mod"] = importlib.import_module("sonorium.network.streaming")
    models = importlib.import_module("sonorium.network.models")
    g.update(NetworkSpeaker=models.NetworkSpeaker, SpeakerStatus=models.SpeakerStatus, SpeakerType=models.SpeakerType)
    router = importlib.import_module("sonorium.network.router")
    g.update(SpeakerRouter=router.SpeakerRouter, split_speaker_ids=router.split_speaker_ids)
    g["NetworkSpeakerService"] = importlib.import_module("sonorium.network.service").NetworkSpeakerService
    try:
        yield
    finally:
        for name in [n for n in sys.modules if _is_sonorium(n)]:
            del sys.modules[name]
        sys.modules.update(saved)


def speaker(speaker_type=None, device_id="RINCON_1", host="10.0.0.5", name="Kitchen", status=None, **kwargs):
    speaker_type = speaker_type or SpeakerType.SONOS
    status = status or SpeakerStatus.AVAILABLE
    return NetworkSpeaker(
        id=network.make_speaker_id(speaker_type.value, device_id), name=name,
        speaker_type=speaker_type, host=host, status=status, **kwargs,
    )


# --- IDs ---

def test_speaker_id_format_and_parse():
    sid = network.make_speaker_id("sonos", "RINCON_000E58AB12")
    assert sid == "net:sonos:RINCON_000E58AB12"
    assert network.is_network_speaker_id(sid)
    assert network.parse_speaker_id(sid) == ("sonos", "RINCON_000E58AB12")


def test_speaker_id_never_looks_like_ha_entity():
    assert not network.is_network_speaker_id("media_player.kitchen")
    assert network.parse_speaker_id("media_player.kitchen") is None
    assert network.parse_speaker_id("net:sonos") is None


@pytest.mark.parametrize("raw", ["uuid with spaces", "a'b\"c<d>&e\\f", "x" * 200, ""])
def test_speaker_id_is_safe_in_html_and_js(raw):
    sid = network.make_speaker_id("dlna", raw)
    device_id = network.parse_speaker_id(sid)[1]
    assert device_id
    assert len(device_id) <= network.MAX_DEVICE_ID_LENGTH
    assert all(c.isalnum() or c in "._-" for c in device_id)


def test_display_name_shows_protocol():
    assert speaker().display_name == "Kitchen (Sonos)"
    assert speaker(SpeakerType.CHROMECAST, "u1").display_name == "Kitchen (Cast)"


# --- Routing ---

def make_router(ha=True):
    ha_controller = MagicMock()
    for name in ("play_media_multi", "stop_multi", "pause_multi", "set_volume_multi", "get_playing_states"):
        setattr(ha_controller, name, AsyncMock(side_effect=lambda ids, *a: {i: True for i in ids}))
    service = MagicMock()
    for name in ("play_multi", "stop_multi", "set_volume_multi"):
        setattr(service, name, AsyncMock(side_effect=lambda ids, *a: {i: True for i in ids}))
    service.playing_states = MagicMock(side_effect=lambda ids: {i: True for i in ids})
    return SpeakerRouter(ha_controller if ha else None, service), ha_controller, service


MIXED = ["media_player.kitchen", "net:sonos:RINCON_1", "media_player.office", "net:dlna:abc"]


def test_split_by_prefix():
    assert split_speaker_ids(MIXED) == (
        ["media_player.kitchen", "media_player.office"],
        ["net:sonos:RINCON_1", "net:dlna:abc"],
    )


def test_play_mixed_session_goes_to_both():
    router, ha, net = make_router()
    url = "http://10.0.0.2:8008/stream/channel1"
    result = asyncio.run(router.play_media_multi(MIXED, url))
    ha.play_media_multi.assert_awaited_once_with(["media_player.kitchen", "media_player.office"], url, "music")
    net.play_multi.assert_awaited_once_with(["net:sonos:RINCON_1", "net:dlna:abc"], url)
    assert result == {sid: True for sid in MIXED}


def test_stop_pause_volume_routing():
    router, ha, net = make_router()
    asyncio.run(router.stop_multi(MIXED))
    ha.stop_multi.assert_awaited_once_with(["media_player.kitchen", "media_player.office"])
    net.stop_multi.assert_awaited_once_with(["net:sonos:RINCON_1", "net:dlna:abc"])

    net.stop_multi.reset_mock()
    asyncio.run(router.pause_multi(MIXED))
    ha.pause_multi.assert_awaited_once_with(["media_player.kitchen", "media_player.office"])
    net.stop_multi.assert_awaited_once_with(["net:sonos:RINCON_1", "net:dlna:abc"])  # pause = stop

    asyncio.run(router.set_volume_multi(MIXED, 0.4))
    ha.set_volume_multi.assert_awaited_once_with(["media_player.kitchen", "media_player.office"], 0.4)
    net.set_volume_multi.assert_awaited_once_with(["net:sonos:RINCON_1", "net:dlna:abc"], 0.4)


def test_network_only_never_calls_ha():
    router, ha, net = make_router()
    asyncio.run(router.play_media_multi(["net:sonos:RINCON_1"], "http://x/stream/channel1"))
    ha.play_media_multi.assert_not_awaited()


def test_without_home_assistant_ha_ids_fail_and_network_still_plays():
    router, _, net = make_router(ha=False)
    result = asyncio.run(router.play_media_multi(MIXED, "http://x/stream/channel1"))
    assert result["media_player.kitchen"] is False and result["media_player.office"] is False
    assert result["net:sonos:RINCON_1"] is True
    net.play_multi.assert_awaited_once()
    with pytest.raises(AttributeError):
        router.get_state  # HA-only methods aren't available without HA


def test_one_side_failing_doesnt_break_the_other():
    router, ha, net = make_router()
    net.play_multi.side_effect = RuntimeError("boom")
    result = asyncio.run(router.play_media_multi(MIXED, "http://x/stream/channel1"))
    assert result["media_player.kitchen"] is True
    assert result["net:sonos:RINCON_1"] is False and result["net:dlna:abc"] is False


def test_router_delegates_ha_only_methods():
    router, ha, _ = make_router()
    assert router.get_state is ha.get_state


# --- Service ---

class FakeDiscovery:
    def __init__(self, speakers):
        self.speakers = {s.id: s for s in speakers}
        self.discover_all = AsyncMock(side_effect=lambda timeout, full: list(self.speakers.values()))

    def get_speaker(self, speaker_id):
        return self.speakers.get(speaker_id)


def make_service(speakers):
    streaming = MagicMock()
    streaming.start_streaming = AsyncMock(return_value=True)
    streaming.stop_streaming = AsyncMock(return_value=True)
    streaming.set_volume = AsyncMock(return_value=True)
    streaming.stop_all = AsyncMock()
    return NetworkSpeakerService(Path("."), discovery=FakeDiscovery(speakers), streaming=streaming), streaming


def test_service_plays_known_speakers_and_rejects_unknown():
    kitchen = speaker()
    service, streaming = make_service([kitchen])
    result = asyncio.run(service.play_multi([kitchen.id, "net:sonos:missing"], "http://x/stream/channel2"))
    assert result == {kitchen.id: True, "net:sonos:missing": False}
    streaming.start_streaming.assert_awaited_once_with(kitchen, "http://x/stream/channel2")


def test_service_volume_and_stop():
    kitchen = speaker()
    service, streaming = make_service([kitchen])
    asyncio.run(service.set_volume_multi([kitchen.id], 0.25))
    streaming.set_volume.assert_awaited_once_with(kitchen, 0.25)
    assert asyncio.run(service.stop_multi([kitchen.id])) == {kitchen.id: True}


def test_discover_notifies_and_runs_once_at_a_time():
    service, _ = make_service([speaker()])
    changes = []
    service.on_change = lambda: changes.append(True)

    async def run():
        return await asyncio.gather(service.discover(), service.discover())

    assert asyncio.run(run()) == [1, 1]
    assert service.discovery.discover_all.await_count == 1
    assert changes == [True]


def test_hierarchy_entries():
    service, _ = make_service([speaker()])
    assert service.hierarchy_speakers() == [{
        "entity_id": "net:sonos:RINCON_1", "name": "Kitchen", "ip_address": "10.0.0.5",
        "type": "sonos", "online": True, "source": ["discovered"],
    }]


# --- Discovery ---

def test_dedupe_prefers_sonos_over_dlna_and_airplay_on_same_host():
    found = [speaker(), speaker(SpeakerType.DLNA, "uuid-1"), speaker(SpeakerType.AIRPLAY, "AA:BB")]
    kept = discovery_mod.dedupe_by_host({s.id: s for s in found})
    assert list(kept) == ["net:sonos:RINCON_1"]


def test_dedupe_prefers_available_type():
    gone = speaker(status=SpeakerStatus.UNAVAILABLE)
    dlna = speaker(SpeakerType.DLNA, "uuid-1")
    kept = discovery_mod.dedupe_by_host({gone.id: gone, dlna.id: dlna})
    assert list(kept) == [dlna.id]


def test_dedupe_keeps_cast_groups_on_same_host():
    a = speaker(SpeakerType.CHROMECAST, "u1")
    b = speaker(SpeakerType.CHROMECAST, "u2", is_group=True)
    assert set(discovery_mod.dedupe_by_host({a.id: a, b.id: b})) == {a.id, b.id}


def test_parse_description_and_usn():
    xml = (
        "<root><device><deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>"
        "<friendlyName>Office</friendlyName><manufacturer>Arylic</manufacturer>"
        "<modelName>A50</modelName><UDN>uuid:FF31F09E-1234</UDN></device></root>"
    )
    info = discovery_mod.parse_device_description(xml)
    assert info == {"friendlyName": "Office", "modelName": "A50", "manufacturer": "Arylic",
                    "uuid": "FF31F09E-1234", "is_renderer": True}
    assert discovery_mod.uuid_from_usn("uuid:abcd::urn:schemas-upnp-org:device:MediaRenderer:1") == "abcd"


def patched_discovery(tmp_path, results):
    """A NetworkSpeakerDiscovery whose protocol scans return `results` (no network)."""
    d = discovery_mod.NetworkSpeakerDiscovery(tmp_path)
    names = ["_discover_chromecast", "_discover_sonos", "_discover_dlna", "_discover_mdns",
             "_discover_airplay", "_discover_heos", "_discover_linkplay"]
    for name in names:
        setattr(d, name, AsyncMock(return_value=results.get(name, [])))
    return d


def test_discovered_speakers_persist_across_restarts(tmp_path):
    kitchen = speaker()
    d = patched_discovery(tmp_path, {"_discover_sonos": [kitchen]})
    asyncio.run(d.discover_all(timeout=1, full=False))
    d._discover_linkplay.assert_not_called()  # quick scans skip the subnet probe

    saved = json.loads((tmp_path / "network_speakers.json").read_text())
    assert [s["id"] for s in saved["speakers"]] == [kitchen.id]

    # Restart: known before any scan; a scan that misses it keeps it, unavailable
    d2 = patched_discovery(tmp_path, {})
    assert d2.get_speaker(kitchen.id).name == "Kitchen"
    asyncio.run(d2.discover_all(timeout=1, full=True))
    d2._discover_linkplay.assert_awaited_once()
    assert d2.get_speaker(kitchen.id).status == SpeakerStatus.UNAVAILABLE


def test_one_protocol_failing_keeps_the_others(tmp_path):
    d = patched_discovery(tmp_path, {"_discover_sonos": [speaker()]})
    d._discover_dlna.side_effect = OSError("no multicast")
    found = asyncio.run(d.discover_all(timeout=1))
    assert [s.id for s in found] == ["net:sonos:RINCON_1"]


# --- Streaming (protocol libraries mocked) ---

def test_sonos_plays_as_radio_and_sets_volume():
    device = MagicMock()
    fake_soco = types.ModuleType("soco")
    fake_soco.SoCo = MagicMock(return_value=device)
    kitchen = speaker()
    manager = streaming_mod.NetworkStreamingManager()
    with patch.dict(sys.modules, {"soco": fake_soco}):
        assert asyncio.run(manager.start_streaming(kitchen, "http://x/stream/channel1"))
        asyncio.run(manager.set_volume(kitchen, 0.3))
        assert asyncio.run(manager.stop_streaming(kitchen.id))
    fake_soco.SoCo.assert_called_with("10.0.0.5")
    device.play_uri.assert_called_once_with("http://x/stream/channel1", title="Sonorium", force_radio=True)
    assert device.volume == 30
    device.stop.assert_called_once()
    assert not manager.is_playing(kitchen.id)


def test_failed_start_leaves_no_session():
    fake_soco = types.ModuleType("soco")
    fake_soco.SoCo = MagicMock(side_effect=OSError("unreachable"))
    kitchen = speaker()
    manager = streaming_mod.NetworkStreamingManager()
    with patch.dict(sys.modules, {"soco": fake_soco}):
        assert asyncio.run(manager.start_streaming(kitchen, "http://x/stream/channel1")) is False
    assert manager.get_session(kitchen.id) is None


def test_linkplay_airplay_device_uses_http_api():
    arylic = speaker(SpeakerType.AIRPLAY, "AA:BB", name="Office", manufacturer="Arylic")
    manager = streaming_mod.NetworkStreamingManager()
    with patch.object(streaming_mod.NetworkStreamingManager, "_linkplay_command", AsyncMock(return_value=True)) as cmd:
        assert asyncio.run(manager.start_streaming(arylic, "http://x/stream/channel3"))
        asyncio.run(manager.stop_streaming(arylic.id))
    assert cmd.await_args_list[0].args == ("10.0.0.5", "setPlayerCmd:play:http://x/stream/channel3")
    assert cmd.await_args_list[1].args[1] == "setPlayerCmd:stop"


def test_linkplay_detection():
    assert streaming_mod.is_linkplay_device(speaker(SpeakerType.AIRPLAY, "x", name="Arylic Up2Stream"))
    assert not streaming_mod.is_linkplay_device(speaker(SpeakerType.AIRPLAY, "x", name="HomePod", manufacturer=None))


def test_heos_play_stream_command():
    heos = speaker(SpeakerType.HEOS, "123", extra={"pid": 123})
    manager = streaming_mod.NetworkStreamingManager()
    ok = {"heos": {"result": "success"}}
    with patch("sonorium.network.heos.heos_command", AsyncMock(return_value=ok)) as cmd:
        assert asyncio.run(manager.start_streaming(heos, "http://x/stream/channel1"))
        asyncio.run(manager.stop_streaming(heos.id))
    assert cmd.await_args_list[0].args == ("10.0.0.5", "browse/play_stream?pid=123&url=http://x/stream/channel1")
    assert cmd.await_args_list[1].args == ("10.0.0.5", "player/set_play_state?pid=123&state=stop")


def test_didl_metadata_escapes_url():
    meta = streaming_mod.didl_metadata("http://x/stream?a=1&b=2")
    assert "a=1&amp;b=2" in meta and "audio/mpeg" in meta


# --- Speaker hierarchy (ha/registry.py) ---

@pytest.fixture
def registry_module():
    fmtr = types.ModuleType("fmtr")
    fmtr_tools = types.ModuleType("fmtr.tools")
    fmtr_tools.http = MagicMock()
    fmtr.tools = fmtr_tools
    runtime = types.ModuleType("sonorium.runtime")
    runtime.ha_configured = lambda: False
    runtime.ha_websocket_url = lambda url: url
    stubs = {"fmtr": fmtr, "fmtr.tools": fmtr_tools, "sonorium.runtime": runtime}
    with patch.dict(sys.modules, stubs):
        import importlib.util
        spec = importlib.util.spec_from_file_location("registry_under_test", PACKAGE / "ha" / "registry.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["registry_under_test"] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
        yield module


def test_network_speakers_in_hierarchy_without_ha(registry_module):
    registry = registry_module.HARegistry("http://ha/api", "token")
    entries = [{"entity_id": "net:sonos:RINCON_1", "name": "Kitchen (Sonos)", "ip_address": "10.0.0.5"}]
    registry.set_extra_speaker_source(lambda: entries)
    hierarchy = registry.refresh()

    assert [s.entity_id for s in hierarchy.unassigned_speakers] == ["net:sonos:RINCON_1"]
    assert hierarchy.to_dict()["unassigned_speakers"][0]["name"] == "Kitchen (Sonos)"
    assert registry.get_speaker_name("net:sonos:RINCON_1") == "Kitchen (Sonos)"
    assert registry.get_all_speaker_ids() == ["net:sonos:RINCON_1"]
    assert registry.resolve_selection(include_speakers=["net:sonos:RINCON_1"]) == ["net:sonos:RINCON_1"]


def test_merge_after_discovery_replaces_previous_network_speakers(registry_module):
    registry = registry_module.HARegistry("http://ha/api", "token")
    entries = [{"entity_id": "net:sonos:A", "name": "A (Sonos)", "ip_address": None}]
    registry.set_extra_speaker_source(lambda: entries)
    registry.refresh()
    entries[:] = [{"entity_id": "net:dlna:B", "name": "B (DLNA)", "ip_address": None}]
    hierarchy = registry.merge_extra_speakers()
    assert [s.entity_id for s in hierarchy.unassigned_speakers] == ["net:dlna:B"]
    assert registry.get_speaker("net:sonos:A") is None


def test_network_speakers_listed_with_ha_speakers(registry_module):
    registry = registry_module.HARegistry("http://ha/api", "token")
    registry._ha_speakers["media_player.office"] = registry_module.Speaker(entity_id="media_player.office", name="Office")
    registry.set_extra_speaker_source(lambda: [{"entity_id": "net:sonos:A", "name": "Attic (Sonos)"}])
    hierarchy = registry.merge_extra_speakers()
    assert [s.entity_id for s in hierarchy.unassigned_speakers] == ["net:sonos:A", "media_player.office"]
    hierarchy = registry.merge_extra_speakers()  # no duplicates on re-merge
    assert len(hierarchy.unassigned_speakers) == 2


def test_addon_registry_has_no_extra_speakers(registry_module):
    registry = registry_module.HARegistry("http://ha/api", "token")
    assert registry.refresh().get_all_speakers() == []
    assert registry.merge_extra_speakers().get_all_speakers() == []


# --- The HA add-on never loads network speaker code ---

def _module_level_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        elif isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
    return names


@pytest.mark.parametrize("relative", [
    "api.py", "web/api_v2.py", "ha/registry.py", "ha/media_controller.py", "core/session_manager.py",
    "core/speaker_settings.py", "core/speaker_test_tone.py",
])
def test_shared_modules_dont_import_network_at_module_level(relative):
    assert not any(name.startswith("sonorium.network") for name in _module_level_imports(PACKAGE / relative))


def test_network_init_only_called_in_standalone():
    tree = ast.parse((PACKAGE / "api.py").read_text(encoding="utf-8"))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "_init_network_speakers"
    ]
    assert calls
    for call in calls:
        node = call
        while node in parents and not isinstance(node, ast.If):
            node = parents[node]
        assert isinstance(node, ast.If) and ast.unparse(node.test) == "runtime.STANDALONE"


def test_network_dependencies_only_in_standalone_image():
    addon = (ROOT / "sonorium_addon" / "Dockerfile").read_text(encoding="utf-8")
    standalone = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    for package in ("pyatv", "zeroconf", "async-upnp-client"):
        assert package not in addon
        assert package in standalone
