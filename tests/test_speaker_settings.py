"""
Speaker metadata and per-speaker settings: source/type/online in the speaker
hierarchy, HA + network duplicates merged by IP, name/room/volume offset/
play-via overrides, manual speakers, the test sound and their API endpoints.
No network access and no audio: protocol probes and speaker commands are mocked.
"""

import asyncio
import importlib
import importlib.util
import json
import logging
import sys
import time
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "sonorium_addon" / "sonorium"


def _is_sonorium(name):
    return name == "sonorium" or name.startswith("sonorium.")


def _package(name, path):
    module = types.ModuleType(name)
    module.__path__ = [str(path)]
    return module


@pytest.fixture(scope="module", autouse=True)
def addon_modules():
    """
    The add-on's modules without fmtr, HA or MQTT: bare packages (their
    __init__ files import everything) plus stubs for obs, runtime and fmtr.
    """
    saved = {name: module for name, module in sys.modules.items() if _is_sonorium(name) or name.startswith("fmtr")}
    for name in saved:
        del sys.modules[name]

    obs = types.ModuleType("sonorium.obs")
    obs.logger = logging.getLogger("sonorium-speaker-settings-test")
    obs.logger.instrument = lambda *a, **k: (lambda method: method)
    runtime = types.ModuleType("sonorium.runtime")
    runtime.ha_configured = lambda: True
    runtime.ha_websocket_url = lambda url: url
    fmtr = types.ModuleType("fmtr")
    fmtr_tools = types.ModuleType("fmtr.tools")
    fmtr_tools.http = MagicMock()
    fmtr.tools = fmtr_tools
    sys.modules.update({
        "sonorium": _package("sonorium", PACKAGE),
        "sonorium.core": _package("sonorium.core", PACKAGE / "core"),
        "sonorium.ha": _package("sonorium.ha", PACKAGE / "ha"),
        "sonorium.web": _package("sonorium.web", PACKAGE / "web"),
        "sonorium.obs": obs,
        "sonorium.runtime": runtime,
        "fmtr": fmtr,
        "fmtr.tools": fmtr_tools,
    })
    g = globals()
    g["registry_mod"] = importlib.import_module("sonorium.ha.registry")
    g["settings_mod"] = importlib.import_module("sonorium.core.speaker_settings")
    g["state_mod"] = importlib.import_module("sonorium.core.state")
    g["tone_mod"] = importlib.import_module("sonorium.core.speaker_test_tone")
    g["manual_mod"] = importlib.import_module("sonorium.network.manual")
    g["models"] = importlib.import_module("sonorium.network.models")
    g["service_mod"] = importlib.import_module("sonorium.network.service")
    try:
        yield
    finally:
        for name in [n for n in sys.modules if _is_sonorium(n) or n.startswith("fmtr")]:
            del sys.modules[name]
        sys.modules.update(saved)


# --- Helpers ---

def make_registry(ha_speakers=(), extras=(), settings=None, areas=None, floors=None):
    """An HARegistry with HA data already 'fetched' (no Home Assistant calls)."""
    reg = registry_mod.HARegistry("http://ha/api", "token")
    for floor_id, name in (floors or {}).items():
        reg._ha_floors[floor_id] = registry_mod.Floor(floor_id=floor_id, name=name)
    for area_id, (name, floor_id) in (areas or {}).items():
        reg._ha_areas[area_id] = registry_mod.Area(area_id=area_id, name=name, floor_id=floor_id)
    for speaker in ha_speakers:
        reg._ha_speakers[speaker.entity_id] = speaker
    extras_list = list(extras)
    reg.set_extra_speaker_source(lambda: extras_list)
    settings = settings if settings is not None else {}
    reg.set_speaker_settings_source(lambda: settings)
    reg.apply_speaker_settings()
    return reg, settings


def ha_speaker(entity_id="media_player.office", name="Office", area_id="office", ip=None, **kwargs):
    return registry_mod.Speaker(entity_id=entity_id, name=name, area_id=area_id, ip_address=ip,
                                address=ip or entity_id, **kwargs)


AREAS = {"office": ("Office", "ground"), "kitchen": ("Kitchen", "ground"), "attic": ("Attic", None)}
FLOORS = {"ground": "Ground Level"}


# --- Settings validation ---

def test_clean_settings_validates_and_drops_defaults():
    clean = settings_mod.clean_speaker_settings
    assert clean({}, {"name": "  Den ", "volume_offset": 10, "room": "office"}) == \
        {"name": "Den", "volume_offset": 10, "room": "office"}
    assert clean({"name": "Den"}, {"name": ""}) == {}  # empty name = original name
    assert clean({"volume_offset": 10}, {"volume_offset": 0}) == {}
    assert clean({"room": "office"}, {"room": None}) == {}  # null = back to the HA area
    assert clean({}, {"room": ""}) == {"room": ""}  # "" = no room
    assert clean({"play_via": "net:dlna:x"}, {"play_via": "ha"}) == {}
    for bad in (21, -25, "loud"):
        with pytest.raises(settings_mod.SpeakerSettingsError):
            clean({}, {"volume_offset": bad})
    with pytest.raises(settings_mod.SpeakerSettingsError):
        clean({}, {"colour": "red"})


@pytest.mark.parametrize("volume, offset, expected", [
    (0.5, 10, 0.6), (0.5, -20, 0.3), (0.95, 20, 1.0), (0.1, -20, 0.0), (0.33, 0, 0.33),
])
def test_offset_volume_clamps(volume, offset, expected):
    assert settings_mod.offset_volume(volume, offset) == pytest.approx(expected)


def test_old_state_files_still_load(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 1, "settings": {"enabled_speakers": ["media_player.a"]}}))
    store = state_mod.StateStore(path)
    store.load()
    assert store.settings.speaker_settings == {}
    store.settings.speaker_settings["media_player.a"] = {"volume_offset": -10}
    store.save()
    again = state_mod.StateStore(path)
    again.load()
    assert again.settings.speaker_settings == {"media_player.a": {"volume_offset": -10}}
    assert again.settings.enabled_speakers == ["media_player.a"]


# --- Hierarchy metadata, merging and overrides ---

def test_ha_speaker_type_and_online_from_registry_and_state():
    reg = registry_mod.HARegistry("http://ha/api", "token")
    reg._get = MagicMock(return_value=[
        {"entity_id": "media_player.kitchen", "state": "idle", "attributes": {"friendly_name": "Kitchen"}},
        {"entity_id": "media_player.tv", "state": "unavailable", "attributes": {"friendly_name": "TV"}},
        {"entity_id": "media_player.odd", "state": "playing", "attributes": {"friendly_name": "Odd"}},
    ])
    entity_registry = {
        "media_player.kitchen": {"platform": "cast"},
        "media_player.tv": {"platform": "dlna_dmr"},
        "media_player.odd": {"platform": "kodi"},
    }
    speakers = reg._fetch_speakers(entity_registry, {}, {})
    assert [(s.type, s.online, s.source) for s in speakers.values()] == [
        ("cast", True, ["ha"]), ("dlna", False, ["ha"]), ("other", True, ["ha"]),
    ]
    assert speakers["media_player.kitchen"].to_dict()["address"] == "media_player.kitchen"


def test_ha_and_network_speaker_with_same_ip_are_one_speaker():
    office = ha_speaker(ip="192.168.1.230", type="linkplay")
    extras = [
        {"entity_id": "net:dlna:abc", "name": "Office_C97A", "ip_address": "192.168.1.230",
         "type": "linkplay", "online": True, "source": ["discovered"]},
        {"entity_id": "net:dlna:den", "name": "Den", "ip_address": "192.168.1.99",
         "type": "dlna", "online": False, "source": ["discovered"]},
    ]
    reg, _ = make_registry([office], extras, areas=AREAS, floors=FLOORS)
    ids = reg.get_all_speaker_ids()
    assert sorted(ids) == ["media_player.office", "net:dlna:den"]
    merged = reg.get_speaker("media_player.office").to_dict()
    assert merged["source"] == ["ha", "discovered"]
    assert merged["merged"] == [{"id": "net:dlna:abc", "type": "linkplay", "source": "discovered"}]
    assert merged["play_via"] == "ha" and merged["address"] == "192.168.1.230"
    assert reg.get_merged_owner("net:dlna:abc") == "media_player.office"
    den = reg.get_speaker("net:dlna:den").to_dict()
    assert den["source"] == ["discovered"] and den["online"] is False and den["area_id"] is None


def test_play_via_routes_merged_speaker_through_network_id():
    office = ha_speaker(ip="10.0.0.5")
    extras = [{"entity_id": "net:sonos:R1", "name": "Office", "ip_address": "10.0.0.5", "type": "sonos"}]
    reg, settings = make_registry([office], extras, settings={"media_player.office": {"play_via": "net:sonos:R1"}})
    assert reg.get_play_target("media_player.office") == "net:sonos:R1"
    settings["media_player.office"]["play_via"] = "net:sonos:gone"  # stale choice: back to HA
    reg.apply_speaker_settings()
    assert reg.get_play_target("media_player.office") == "media_player.office"
    assert reg.get_play_target("net:unknown:x") == "net:unknown:x"


def test_name_and_room_overrides():
    office = ha_speaker()
    extras = [{"entity_id": "net:cast:tv", "name": "65\" OLED", "ip_address": "10.0.0.8", "type": "cast"}]
    settings = {
        "media_player.office": {"name": "Desk speaker", "room": "kitchen", "volume_offset": -10},
        "net:cast:tv": {"room": "attic"},
    }
    reg, _ = make_registry([office], extras, settings=settings, areas=AREAS, floors=FLOORS)
    desk = reg.get_speaker("media_player.office")
    assert (desk.name, desk.original_name, desk.area_id, desk.default_area_id) == \
        ("Desk speaker", "Office", "kitchen", "office")
    assert (desk.floor_id, desk.area_name, desk.volume_offset) == ("ground", "Kitchen", -10)
    assert reg.get_speaker_name("media_player.office") == "Desk speaker"
    # A network speaker placed in a room is selectable through that room (and floor)
    assert reg.resolve_selection(include_areas=["attic"]) == ["net:cast:tv"]
    assert reg.resolve_selection(include_floors=["ground"]) == ["media_player.office"]
    hierarchy = reg.hierarchy.to_dict()
    assert hierarchy["unassigned_speakers"] == []
    assert [a["area_id"] for a in hierarchy["unassigned_areas"]] == ["attic"]

    settings["media_player.office"]["room"] = ""  # "No room"
    settings["net:cast:tv"].pop("room")
    reg.apply_speaker_settings()
    assert sorted(s.entity_id for s in reg.hierarchy.unassigned_speakers) == ["media_player.office", "net:cast:tv"]
    assert reg.resolve_selection(include_areas=["office"]) == []


def test_rebuild_does_not_change_fetched_ha_speakers():
    office = ha_speaker()
    reg, settings = make_registry([office], settings={"media_player.office": {"name": "X", "room": ""}}, areas=AREAS)
    assert office.name == "Office" and office.area_id == "office"
    settings.clear()
    reg.apply_speaker_settings()
    assert reg.get_speaker("media_player.office").name == "Office"
    assert reg.get_speakers_in_area("office") == ["media_player.office"]


# --- Speaker outputs (play_via + volume offsets) ---

def make_outputs(settings, targets=None):
    controller = MagicMock()
    for name in ("play_media_multi", "stop_multi", "pause_multi", "set_volume_multi", "get_playing_states"):
        setattr(controller, name, AsyncMock(side_effect=lambda ids, *a: {i: True for i in ids}))
    targets = targets or {}
    outputs = settings_mod.SpeakerOutputs(controller, lambda: settings, lambda sid: targets.get(sid, sid))
    return outputs, controller


def test_volume_offsets_per_speaker():
    outputs, controller = make_outputs({"a": {"volume_offset": 10}, "b": {"volume_offset": -20}})
    result = asyncio.run(outputs.set_volume_multi(["a", "b", "c"], 0.5))
    assert result == {"a": True, "b": True, "c": True}
    calls = {tuple(c.args[0]): c.args[1] for c in controller.set_volume_multi.await_args_list}
    assert calls == {("a",): pytest.approx(0.6), ("b",): pytest.approx(0.3), ("c",): pytest.approx(0.5)}


def test_play_via_maps_ids_both_ways():
    outputs, controller = make_outputs({}, targets={"media_player.office": "net:dlna:abc"})
    result = asyncio.run(outputs.play_media_multi(["media_player.office", "media_player.tv"], "http://x/s"))
    controller.play_media_multi.assert_awaited_once_with(["net:dlna:abc", "media_player.tv"], "http://x/s", "music")
    assert result == {"media_player.office": True, "media_player.tv": True}
    asyncio.run(outputs.stop_multi(["media_player.office"]))
    controller.stop_multi.assert_awaited_once_with(["net:dlna:abc"])
    assert outputs.get_state is controller.get_state  # everything else passes through


# --- Test sound ---

def test_test_tone_is_short_and_quiet():
    samples = tone_mod.tone_samples()
    assert len(samples) == int(tone_mod.DURATION * tone_mod.SAMPLE_RATE)
    assert abs(int(samples.max())) <= 0.31 * 32767 and abs(int(samples[-1])) < 50


def test_test_tone_mp3():
    pytest.importorskip("av")
    data = tone_mod.tone_mp3()
    assert data[:3] == b"ID3" or data[:2] == b"\xff\xfb"
    assert 10_000 < len(data) < 200_000


# --- Manual speakers ---

def fake_aiohttp():
    module = types.ModuleType("aiohttp")

    class Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    module.ClientSession = Session
    module.ClientTimeout = lambda **k: None
    return module


def probe(monkeypatch, address, kind="auto", answers=None, port=None, name=None):
    """probe_speaker with protocol probes answering from `answers` (type -> info)."""
    answers = answers or {}
    monkeypatch.setitem(sys.modules, "aiohttp", fake_aiohttp())
    monkeypatch.setattr(manual_mod, "resolve_host", AsyncMock(return_value="192.168.1.61"))
    probes = {k: AsyncMock(return_value=answers.get(k)) for k in manual_mod.AUTO_ORDER}
    speaker = asyncio.run(manual_mod.probe_speaker(address, kind, port, name, probes=probes))
    return speaker, probes


def test_auto_detect_picks_first_protocol_that_answers(monkeypatch):
    answers = {
        "airplay": {"name": "Patio", "port": 7000, "extra": {"identifier": "AA"}},
        "dlna": {"name": "Patio DLNA", "port": 49152, "extra": {"location": "http://x/description.xml"}},
    }
    speaker, probes = probe(monkeypatch, "patio-speaker.local", answers=answers, name="Patio speaker")
    assert speaker.id == "net:dlna:manual-patio-speaker.local"
    assert speaker.name == "Patio speaker" and speaker.host == "192.168.1.61"
    assert speaker.extra["manual"] and speaker.extra["address"] == "patio-speaker.local"
    assert all(p.await_count == 1 for p in probes.values())


def test_linkplay_is_played_through_its_http_api(monkeypatch):
    streaming_mod = importlib.import_module("sonorium.network.streaming")
    answers = {"linkplay": {"name": "Office_C97A", "uuid": "FF31", "port": 80, "extra": {}}}
    speaker, _ = probe(monkeypatch, "192.168.1.230", "linkplay", answers)
    assert speaker.speaker_type == models.SpeakerType.AIRPLAY
    assert streaming_mod.is_linkplay_device(speaker)
    assert service_mod.ui_type(speaker) == "linkplay"


def test_requested_type_not_answering_is_an_error(monkeypatch):
    with pytest.raises(manual_mod.ProbeError, match="No Sonos speaker"):
        probe(monkeypatch, "10.0.0.9", "sonos")
    for bad in ("", "http://x/y", "10.0.0.9:80"):
        with pytest.raises(manual_mod.ProbeError):
            manual_mod.validate_address(bad)


def manual_speaker(address="10.0.0.9", kind="sonos", host="10.0.0.9"):
    return manual_mod.build_manual_speaker(kind, address, host, {"name": "Patio", "port": 1400})


def test_manual_speakers_survive_restart(tmp_path):
    store = manual_mod.ManualSpeakers(tmp_path)
    store.add(manual_speaker())
    again = manual_mod.ManualSpeakers(tmp_path)
    loaded = again.get("net:sonos:manual-10.0.0.9")
    assert loaded.name == "Patio" and loaded.extra["manual"] and loaded.extra["address"] == "10.0.0.9"
    assert again.remove(loaded.id) and manual_mod.ManualSpeakers(tmp_path).speakers == {}


def test_service_lists_manual_speakers_with_manual_source(tmp_path):
    discovered = models.NetworkSpeaker(id="net:dlna:x", name="Patio DLNA", speaker_type=models.SpeakerType.DLNA,
                                       host="10.0.0.9", status=models.SpeakerStatus.AVAILABLE)
    other = models.NetworkSpeaker(id="net:dlna:y", name="Den", speaker_type=models.SpeakerType.DLNA,
                                  host="10.0.0.10", status=models.SpeakerStatus.AVAILABLE)
    discovery = MagicMock()
    discovery.speakers = {discovered.id: discovered, other.id: other}
    discovery.get_speaker = lambda sid: discovery.speakers.get(sid)
    store = manual_mod.ManualSpeakers(tmp_path)
    store.add(manual_speaker())
    service = service_mod.NetworkSpeakerService(tmp_path, discovery=discovery, streaming=MagicMock(), manual=store)

    # The manual speaker hides the discovered one at the same host
    assert sorted(service.speakers) == ["net:dlna:y", "net:sonos:manual-10.0.0.9"]
    entries = {e["entity_id"]: e for e in service.hierarchy_speakers()}
    assert entries["net:sonos:manual-10.0.0.9"]["source"] == ["manual"]
    assert entries["net:sonos:manual-10.0.0.9"]["type"] == "sonos"
    assert entries["net:dlna:y"]["source"] == ["discovered"]
    assert service.get_speaker("net:sonos:manual-10.0.0.9").name == "Patio"

    service.check_manual = AsyncMock(return_value=manual_speaker())
    with pytest.raises(manual_mod.ProbeError, match="already been added"):
        asyncio.run(service.add_manual("10.0.0.9"))
    service.streaming.stop_streaming = AsyncMock(return_value=True)
    assert asyncio.run(service.remove_manual("net:sonos:manual-10.0.0.9"))
    assert not asyncio.run(service.remove_manual("net:dlna:y"))  # only manual speakers can be removed


# --- API endpoints ---

@pytest.fixture
def api(tmp_path):
    """create_api_router with a real registry/state store and mocked speakers."""
    pytest.importorskip("fastapi")
    pytest.importorskip("multipart")  # the router has upload endpoints
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    api_v2 = importlib.import_module("sonorium.web.api_v2")
    store = state_mod.StateStore(tmp_path / "state.json")
    office = ha_speaker(ip="192.168.1.230", type="linkplay")
    extras = [{"entity_id": "net:dlna:abc", "name": "Office_C97A", "ip_address": "192.168.1.230", "type": "linkplay"}]
    reg, _ = make_registry([office], extras, areas=AREAS, floors=FLOORS)
    reg.set_speaker_settings_source(lambda: store.settings.speaker_settings)
    reg.apply_speaker_settings()

    controller = MagicMock()
    for name in ("play_media_multi", "stop_multi", "set_volume_multi"):
        setattr(controller, name, AsyncMock(side_effect=lambda ids, *a: {i: True for i in ids}))
    controller.get_state = AsyncMock(return_value={"attributes": {"volume_level": 0.45}})
    outputs = settings_mod.SpeakerOutputs(controller, lambda: store.settings.speaker_settings, reg.get_play_target)
    session_manager = MagicMock()
    session_manager.media_controller = outputs
    session_manager.stream_base_url = "http://10.0.0.2:8008"
    session_manager.list.return_value = []

    def build(network_service=None):
        app = FastAPI()
        app.include_router(api_v2.create_api_router(
            session_manager=session_manager, group_manager=MagicMock(), ha_registry=reg,
            state_store=store, network_service=network_service,
        ))
        return TestClient(app)

    return types.SimpleNamespace(build=build, store=store, registry=reg, controller=controller,
                                 session_manager=session_manager, api_v2=api_v2)


def test_speaker_settings_endpoints(api):
    client = api.build()
    r = client.put("/api/speakers/media_player.office/settings",
                   json={"name": "Desk", "room": "kitchen", "volume_offset": 10, "play_via": "net:dlna:abc"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settings"] == {"name": "Desk", "room": "kitchen", "volume_offset": 10, "play_via": "net:dlna:abc"}
    assert body["speaker"]["name"] == "Desk" and body["speaker"]["area_id"] == "kitchen"
    assert json.loads(api.store.state_file.read_text())["settings"]["speaker_settings"]["media_player.office"]

    # Only fields sent change; null resets
    r = client.put("/api/speakers/media_player.office/settings", json={"name": None})
    assert r.json()["settings"] == {"room": "kitchen", "volume_offset": 10, "play_via": "net:dlna:abc"}
    assert r.json()["speaker"]["name"] == "Office"

    assert client.put("/api/speakers/media_player.office/settings", json={"volume_offset": 30}).status_code == 400
    assert client.put("/api/speakers/media_player.office/settings", json={"room": "garage"}).status_code == 400
    assert client.put("/api/speakers/media_player.office/settings", json={"play_via": "net:x:y"}).status_code == 400
    assert client.get("/api/speakers/media_player.nope/settings").status_code == 404
    hierarchy = client.get("/api/speakers/hierarchy").json()
    kitchen = hierarchy["floors"][0]["areas"][0]
    assert kitchen["name"] == "Kitchen" and kitchen["speakers"][0]["source"] == ["ha", "discovered"]


def test_test_sound_plays_quietly_then_stops(api, monkeypatch):
    monkeypatch.setattr(api.api_v2.asyncio, "sleep", AsyncMock())
    client = api.build()
    client.put("/api/speakers/media_player.office/settings", json={"volume_offset": 10})
    r = client.post("/api/speakers/media_player.office/test")
    assert r.status_code == 200, r.text
    c = api.controller
    assert c.set_volume_multi.await_args_list[0].args == (["media_player.office"], pytest.approx(0.3))
    c.play_media_multi.assert_awaited_once_with(["media_player.office"], "http://10.0.0.2:8008/api/test-tone.mp3", "music")
    for _ in range(100):  # the stop runs in a background task
        if c.set_volume_multi.await_count >= 2:
            break
        time.sleep(0.01)
    c.stop_multi.assert_awaited_once_with(["media_player.office"])
    assert c.set_volume_multi.await_args_list[-1].args == (["media_player.office"], 0.45)  # volume restored

    session = MagicMock(is_playing=True)
    session.name = "Evening"
    api.session_manager.list.return_value = [session]
    api.session_manager.get_resolved_speakers.return_value = ["media_player.office"]
    assert client.post("/api/speakers/media_player.office/test").status_code == 409


def test_test_tone_endpoint(api):
    pytest.importorskip("av")
    r = api.build().get("/api/test-tone.mp3")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg" and len(r.content) > 10_000


def test_manual_and_network_endpoints_are_standalone_only(api):
    client = api.build(network_service=None)
    assert client.post("/api/speakers/manual", json={"address": "10.0.0.9"}).status_code == 404
    assert client.post("/api/speakers/manual/check", json={"address": "10.0.0.9"}).status_code == 404
    assert client.delete("/api/speakers/manual/net:sonos:manual-10.0.0.9").status_code == 404
    assert client.get("/api/network-speakers").status_code == 404


def test_manual_speaker_endpoints(api, tmp_path):
    speaker = manual_speaker()
    service = MagicMock()
    service.check_manual = AsyncMock(return_value=speaker)
    service.add_manual = AsyncMock(return_value=speaker)
    service.remove_manual = AsyncMock(return_value=True)
    service.speakers = {speaker.id: speaker}
    service.last_scan, service.last_scan_found = "2026-10-09T08:00:00+00:00", 4
    service.streaming.is_playing.return_value = False
    extras = [{"entity_id": speaker.id, "name": "Patio", "ip_address": "10.0.0.9", "type": "sonos", "source": ["manual"]}]
    api.registry.set_extra_speaker_source(lambda: extras)
    api.store.settings.enabled_speakers = ["media_player.office"]
    client = api.build(network_service=service)

    r = client.post("/api/speakers/manual/check", json={"address": "10.0.0.9"})
    assert r.json()["found"] and r.json()["type"] == "sonos" and "10.0.0.9" in r.json()["message"]

    r = client.post("/api/speakers/manual", json={"address": "10.0.0.9", "type": "sonos", "name": "Patio", "room": "attic"})
    assert r.status_code == 201, r.text
    assert api.store.settings.speaker_settings[speaker.id] == {"room": "attic"}
    assert speaker.id in api.store.settings.enabled_speakers
    assert api.registry.get_speaker(speaker.id).area_id == "attic"
    assert api.registry.get_speaker(speaker.id).source == ["manual"]

    listing = client.get("/api/network-speakers").json()
    assert listing["last_scan"] == "2026-10-09T08:00:00+00:00" and listing["found"] == 4
    assert listing["speakers"][0]["manual"] is True

    extras.clear()
    r = client.delete(f"/api/speakers/manual/{speaker.id}")
    assert r.status_code == 200
    assert speaker.id not in api.store.settings.speaker_settings
    assert speaker.id not in api.store.settings.enabled_speakers

    service.check_manual = AsyncMock(side_effect=manual_mod.ProbeError("Can't find x on the network"))
    assert client.post("/api/speakers/manual/check", json={"address": "x"}).json() == \
        {"found": False, "message": "Can't find x on the network"}
