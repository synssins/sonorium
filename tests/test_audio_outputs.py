"""
Audio output devices as speakers (core/audio_outputs.py, plugin API 1.2):
a plugin registers an output type, its devices become speakers (remembered,
online/offline, Use switch, Offset), a channel plays on them like on any
speaker (open at start, blocks scaled by volume and Offset, close after
Stop), open()/write() failures, one device at a time with take-over, the
Test sound, attach/detach in speaker terms, uninstall and a failed
on_enable(). A fake output type stands in for real audio.
"""
import asyncio
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

from playback_helpers import addon_modules  # noqa: E402
from output_world import LEVEL, MODULES, World, wait_for  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules(*MODULES, "sonorium.plugins.host", "sonorium.plugins.manager",
                       "sonorium.plugins.services", "sonorium.web.api_v2",
                       "sonorium.web.plugin_framework_api") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture
def w(tmp_path):
    world = World(m, tmp_path)
    yield world
    world.close()


@pytest.fixture
def w1(tmp_path):
    world = World(m, tmp_path, one=True)
    yield world
    world.close()


def level_of(sink):
    return int(np.abs(sink.blocks[-1]).max())


def test_speaker_ids_are_url_safe_and_stable():
    sid = m.audio_outputs.speaker_id_for("host_audio", "hw:0,0 / Bose ♪")
    assert sid.startswith("output:host_audio:") and len(sid.rsplit(":", 1)[1]) == 16
    assert sid == m.audio_outputs.speaker_id_for("host_audio", "hw:0,0 / Bose ♪")
    assert all(c.isalnum() or c in ":_.-" for c in sid)


def test_registered_devices_are_speakers(w):
    w.register()
    assert w.fake.discovered == 1  # listed when registered
    assert w.outputs.types() == [{"id": "fake", "title": "Fake devices", "plugin_id": "fakeplugin",
                                  "available": True, "message": "", "one_device_at_a_time": False}]
    one = w.registry.get_speaker(w.sid("dev/1"))
    assert one.type == "output" and one.online and one.name == "Device one"
    d = one.to_dict()
    assert d["output_type"] == "fake" and d["device"] == "dev/1"
    out = d["output"]
    assert (out["api"], out["is_default"], out["channels"], out["sample_rates"]) == ("Fake API", True, 2, [44100, 48000])
    assert out["type_title"] == "Fake devices" and out["error"] == "" and out["playing"] == []
    assert w.registry.get_speaker(w.sid("dev/2")).to_dict()["output"]["is_default"] is False
    # Junk entries dropped, the first of two with the same id kept
    assert sorted(w.store.main.data["output_devices"]) == sorted([w.sid("dev/1"), w.sid("dev/2")])
    actions = m.actions.Actions(w.sm, w.state, w.registry)
    listed = {s["id"]: s for s in w.call(actions.list_speakers)}
    assert listed[w.sid("dev/1")]["type"] == "output" and listed[w.sid("dev/1")]["device"] == "dev/1"
    assert listed[w.sid("dev/1")]["output_type"] == "fake" and listed["net:a"]["type"] == "dlna"

    # Gone from the next listing: offline, still remembered; the plugin off: all offline
    w.fake.devices = w.fake.devices[:1]
    w.call(w.outputs.discover_all)
    assert not w.registry.get_speaker(w.sid("dev/2")).online
    w.outputs.unregister_owner("fakeplugin")
    assert not w.registry.get_speaker(w.sid("dev/1")).online
    assert w.outputs.types() == []
    # A rename stays with Sonorium
    w.state.settings.speaker_settings[w.sid("dev/1")] = {"name": "Desk"}
    w.registry.apply_speaker_settings()
    assert w.registry.get_speaker(w.sid("dev/1")).name == "Desk"


def test_channel_plays_on_a_device_with_volume_and_offset(w):
    w.register()
    dev1 = w.sid("dev/1")
    w.enable(dev1)
    w.state.settings.speaker_settings[dev1] = {"volume_offset": -10}
    den = w.channel("Den", ["net:a", dev1], volume=50)
    assert w.play(den)
    assert w.fake.opens == [("dev/1", 44100, {})]
    sink = w.fake.sinks[0]
    assert wait_for(lambda: len(sink.blocks) > 3)
    assert level_of(sink) == int(LEVEL * 0.4)  # 50 % with -10: a linear gain of 0.4
    # The network speaker went through the media controller; the device didn't
    played = w.controller.play_media_multi.call_args[0][0]
    assert played == ["net:a"]
    assert all(dev1 not in c[0][0] for c in w.controller.set_volume_multi.call_args_list)

    w.call(lambda: w.sm.set_volume(den.id, 100))
    assert wait_for(lambda: level_of(sink) == int(LEVEL * 0.9))
    w.state.settings.speaker_settings[dev1] = {}
    assert wait_for(lambda: level_of(sink) == LEVEL)  # 100 %: the blocks as mixed

    [output] = w.outputs.attachments(den.id)
    assert output["running"] and output["type"] == "fake" and output["device"] == "dev/1"
    assert output["latency"] == 0.05 and output["error"] == "" and output["options"] == {}
    assert w.registry.get_speaker(dev1).to_dict()["output"]["playing"] == [den.id]

    assert w.stop(den)
    assert wait_for(lambda: sink.closed == 1)
    assert not w.outputs.attachments(den.id)[0]["running"]
    assert den.id not in w.sm._session_channels  # the device doesn't hold the mix after Stop
    assert w.play(den)  # Play again: a new sink
    assert len(w.fake.sinks) == 2 and wait_for(lambda: len(w.fake.sinks[1].blocks) > 0)
    w.call(lambda: w.sm.pause(den.id))
    w.settle()
    assert wait_for(lambda: w.fake.sinks[1].closed == 1)


def test_switched_off_device_doesnt_play_and_cant_be_attached(w):
    w.register()
    dev1 = w.sid("dev/1")
    den = w.channel("Den", ["net:a", dev1])
    assert w.play(den)
    assert w.fake.opens == []  # in the channel, switched off
    with pytest.raises(m.audio_outputs.OutputError, match="Switch the speaker on"):
        w.call(lambda: w.outputs.attach(den.id, "fake", "dev/2"))


def test_open_failure_shows_on_the_device_and_the_channel_plays_on(w):
    w.register()
    dev1, dev2 = w.sid("dev/1"), w.sid("dev/2")
    w.enable(dev1, dev2)
    w.fake.open_error["dev/1"] = m.audio_outputs.OutputError("The device is in use by another program")
    den = w.channel("Den", ["net:a", dev1, dev2])
    assert w.play(den)
    assert den.is_playing and w.controller.play_media_multi.called
    assert [s.device_id for s in w.fake.sinks] == ["dev/2"]
    row = w.registry.get_speaker(dev1).to_dict()["output"]
    assert row["error"] == "The device is in use by another program"
    by_device = {o["device"]: o for o in w.outputs.attachments(den.id)}
    assert by_device["dev/1"]["error"] == "The device is in use by another program"
    assert not by_device["dev/1"]["running"] and by_device["dev/2"]["running"]
    w.settle()
    assert len(w.fake.opens) == 2  # no retry meanwhile
    del w.fake.open_error["dev/1"]
    w.stop(den)
    w.play(den)  # the next start tries again
    assert sorted(s.device_id for s in w.fake.sinks if not s.closed) == ["dev/1", "dev/2"]
    assert w.registry.get_speaker(dev1).to_dict()["output"]["error"] == ""


def test_write_error_stops_that_sink_once(w):
    w.register()
    dev1 = w.sid("dev/1")
    w.enable(dev1)
    w.fake.fail_writes["dev/1"] = 2
    den = w.channel("Den", [dev1])
    assert w.play(den)
    sink = w.fake.sinks[0]
    assert wait_for(lambda: sink.closed == 1)
    [output] = w.outputs.attachments(den.id)
    assert output["error"] == "device unplugged" and not output["running"]
    w.settle()
    assert sink.closed == 1 and len(w.fake.sinks) == 1  # closed once, not opened again


def test_options_and_attach_detach_in_speaker_terms(w):
    w.register()
    dev1 = w.sid("dev/1")
    w.enable(dev1)
    den = w.channel("Den", ["net:a"])
    out = w.call(lambda: w.outputs.attach(den.id, "fake", "dev/1", {"buffer_ms": 80}))
    assert not out["running"] and w.fake.opens == []  # stopped channel: added, not played
    assert dev1 in den.adhoc_selection.include_speakers
    assert w.call(lambda: w.outputs.attach(den.id, "fake", "dev/1"))["attachment_id"] == out["attachment_id"]
    assert w.play(den)
    assert w.fake.opens == [("dev/1", 44100, {"buffer_ms": 80})]  # kept with the channel's output
    assert w.outputs.attachments(den.id)[0]["running"]
    assert w.call(lambda: w.outputs.detach(den.id, out["attachment_id"]))
    assert wait_for(lambda: w.fake.sinks[0].closed == 1)
    assert dev1 not in den.adhoc_selection.include_speakers and w.outputs.attachments(den.id) == []
    assert not w.call(lambda: w.outputs.detach(den.id, out["attachment_id"]))
    # Added to a playing channel: plays at once
    out = w.call(lambda: w.outputs.attach(den.id, "fake", "dev/1"))
    assert out["running"] and len(w.fake.sinks) == 2
    with pytest.raises(LookupError):
        w.call(lambda: w.outputs.attach("nope", "fake", "dev/1"))
    with pytest.raises(LookupError):
        w.call(lambda: w.outputs.attach(den.id, "nope", "dev/1"))
    w.fake.ok = (False, "No sound card in this container")
    with pytest.raises(m.audio_outputs.OutputError, match="No sound card"):
        w.call(lambda: w.outputs.attach(den.id, "fake", "dev/2"))


def test_device_in_a_room_taken_out_by_exclusion(w):
    w.register()
    dev1 = w.sid("dev/1")
    w.enable(dev1)
    den = w.channel("Den", [])
    den.adhoc_selection = m.state.SpeakerSelection(include_speakers=["net:a", dev1])
    w.outputs._take_out(den, dev1)
    assert dev1 not in w.sm._resolve_selection(den)
    w.outputs._add_to_channel(den, dev1)
    assert dev1 in w.sm._resolve_selection(den) and dev1 not in den.adhoc_selection.exclude_speakers


def test_one_device_at_a_time_take_over(w1):
    w = w1
    w.register()
    dev1, dev2 = w.sid("dev/1"), w.sid("dev/2")
    w.enable(dev1, dev2)
    assert w.outputs.types()[0]["one_device_at_a_time"] is True
    den = w.channel("Den", ["net:a", dev1])
    office = w.channel("Office", [dev1])
    hall = w.channel("Hall", [dev2])
    assert w.play(den)
    assert w.sm.speaker_conflicts(hall) == ["Fake devices is playing 'Den'"]

    # No take-over: Hall starts without its device
    assert w.play(hall)
    assert [s.device_id for s in w.fake.sinks] == ["dev/1"]
    assert w.outputs.attachments(hall.id)[0]["error"] == "Fake devices is playing 'Den'"
    w.stop(hall)

    # Through the API: 409 (conflict) unless take_over; a stopped channel just gets the device
    w.call(lambda: w.outputs.attach(office.id, "fake", "dev/1"))
    office_2 = w.channel("Office 2", ["net:a"])
    w.play(office_2)
    with pytest.raises(m.audio_outputs.OutputBusy, match="Fake devices is playing 'Den'"):
        w.call(lambda: w.outputs.attach(office_2.id, "fake", "dev/2"))
    assert dev2 not in w.sm._resolve_selection(office_2)

    # Take-over: Den loses the device for good, keeps playing on its other speaker;
    # the old sink is closed before the new one opens
    w.fake.events.clear()
    assert w.play(hall, take_over=True)
    assert w.fake.events == [("close", "dev/1"), ("open", "dev/2")]
    assert dev1 not in w.sm._resolve_selection(den) and den.is_playing
    assert w.outputs.attachments(hall.id)[0]["running"]

    # A channel left without speakers stops
    solo = w.channel("Solo", [dev1])
    w.play(solo, take_over=True)
    assert wait_for(lambda: not hall.is_playing)
    assert dev2 not in w.sm._resolve_selection(hall)

    # Test is refused while the type plays
    with pytest.raises(m.audio_outputs.OutputBusy, match="Fake devices is playing 'Solo'. Stop it first."):
        w.call(lambda: w.outputs.test(dev2))


def test_test_sound_through_the_sink(w):
    from sonorium.core.speaker_test_tone import tone_samples
    w.register()
    dev1 = w.sid("dev/1")
    result = w.call(lambda: w.outputs.test(dev1))  # works whether or not it's switched on
    assert result == {"speaker_id": dev1, "playing": True, "seconds": 3.0}
    assert w.fake.opens == [("dev/1", 44100, {})]
    with pytest.raises(m.audio_outputs.OutputBusy, match="already playing"):
        w.call(lambda: w.outputs.test(dev1))
    sink = w.fake.sinks[0]
    assert wait_for(lambda: sink.closed == 1, 6.0)
    total = sum(b.shape[1] for b in sink.blocks)
    assert total == len(tone_samples()) and all(b.shape[1] <= 1024 for b in sink.blocks)
    peak = int(max(np.abs(b).max() for b in sink.blocks))
    assert abs(peak - 0.3 * 0.2 * 32767) < 30  # the chime (peak 0.3) at the test volume 20 %

    # Refused while the device plays a channel; OutputError from open() is the test's error
    w.enable(dev1)
    den = w.channel("Den", [dev1])
    w.play(den)
    with pytest.raises(m.audio_outputs.OutputBusy, match="This speaker is playing 'Den'. Stop it first."):
        w.call(lambda: w.outputs.test(dev1))
    w.fake.open_error["dev/2"] = m.audio_outputs.OutputError("Unplugged")
    with pytest.raises(m.audio_outputs.OutputError, match="Unplugged"):
        w.call(lambda: w.outputs.test(w.sid("dev/2")))
    with pytest.raises(LookupError):
        w.call(lambda: w.outputs.test("output:fake:0000000000000000"))


def test_reregistered_type_starts_playing_channels_and_remove_offline(w):
    w.register()
    dev1, dev2 = w.sid("dev/1"), w.sid("dev/2")
    w.enable(dev1, dev2)
    den = w.channel("Den", ["net:a", dev1])
    w.play(den)
    w.outputs.unregister_owner("fakeplugin")
    assert wait_for(lambda: w.fake.sinks[0].closed == 1)
    assert dev1 in w.sm._resolve_selection(den)  # stays in the channel, offline
    w.register()  # switched on again: the playing channel starts on it
    assert len(w.fake.sinks) == 2 and wait_for(lambda: len(w.fake.sinks[1].blocks) > 0)

    with pytest.raises(m.audio_outputs.OutputError, match="offline"):
        w.outputs.remove_device(dev2)
    w.fake.devices = w.fake.devices[:1]
    w.call(w.outputs.discover_all)
    hall = w.channel("Hall", [dev2, "net:a"])
    w.outputs.remove_device(dev2)
    assert w.registry.get_speaker(dev2) is None and dev2 not in w.sm._resolve_selection(hall)
    assert dev2 not in w.state.settings.enabled_speakers


def test_uninstall_forgets_the_devices(w):
    w.register()
    dev1 = w.sid("dev/1")
    w.enable(dev1)
    w.state.settings.speaker_settings[dev1] = {"volume_offset": 5}
    den = w.channel("Den", ["net:a", dev1])
    w.outputs.forget_owner("fakeplugin")
    assert w.registry.get_speaker(dev1) is None and w.store.main.data["output_devices"] == {}
    assert dev1 not in w.sm._resolve_selection(den) and dev1 not in w.state.settings.speaker_settings


def test_channels_sent_to_an_output_before_1_2_keep_it(tmp_path):
    data = {"audio_outputs": {}}
    world = World(m, tmp_path, store_data=data)
    try:
        den = world.channel("Den", ["net:a"])
        data["audio_outputs"][den.id] = [{"id": "a_1", "type": "fake", "device": "dev/1", "options": {"x": 1}}]
        outputs = world.call(lambda: m.audio_outputs.AudioOutputs(world.sm, world.store))
        dev1 = world.sid("dev/1")
        assert dev1 in world.sm._resolve_selection(den) and world.state.settings.speaker_enabled(dev1)
        outputs.register_type("p", world.fake)
        assert [o["options"] for o in outputs.attachments(den.id)] == [{"x": 1}]
    finally:
        world.close()


def test_failed_on_enable_removes_its_output_types(w, tmp_path):
    host = m.host.PluginHost(tmp_path / "config", session_manager=w.sm, state_store=w.state)
    host.set_audio(None, None, w.outputs)
    manager = m.manager.PluginManager(w.state, plugins_dir=tmp_path / "plugins", audio_path=tmp_path / "audio",
                                      host=host)
    fake = w.fake

    class Bad:
        id = name = "bad"
        enabled = False

        async def on_enable(self):
            w.outputs.register_type("bad", fake)
            raise RuntimeError("no driver")
    manager.plugins["bad"] = Bad()
    assert not w.call(lambda: manager.enable_plugin("bad"))
    assert w.outputs.types() == []


def test_routes(w1, tmp_path):
    """Settings > Speakers and the channel editor over HTTP: discover, play with take_over, attach, Test, Remove."""
    pytest.importorskip("multipart")
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    w = w1
    w.register()
    dev1, dev2 = w.sid("dev/1"), w.sid("dev/2")
    w.enable(dev1, dev2)
    app = FastAPI()
    app.include_router(m.api_v2.create_api_router(session_manager=w.sm, group_manager=MagicMock(),
                                                  ha_registry=w.registry, state_store=w.state))
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(
        SimpleNamespace(), SimpleNamespace(outputs=w.outputs)))
    client = TestClient(app)

    r = client.post("/api/audio-outputs/discover")
    assert r.status_code == 200, r.text
    [kind] = r.json()["types"]
    assert kind["title"] == "Fake devices" and kind["one_device_at_a_time"] and kind["error"] == ""
    assert [s["entity_id"] for s in kind["speakers"]] == sorted([dev1, dev2])
    assert kind["speakers"][0]["type"] == "output" and "output" in kind["speakers"][0]
    listed = {s["entity_id"]: s for s in client.get("/api/speakers").json()}
    assert listed[dev1]["output"]["device"] == "dev/1"

    den = w.channel("Den", ["net:a", dev1])
    hall = w.channel("Hall", ["net:a", dev2])
    assert w.play(den)
    r = client.post(f"/api/sessions/{hall.id}/play")
    assert r.status_code == 409 and r.json()["detail"] == "Fake devices is playing 'Den'"
    assert r.headers["X-Sonorium-Take-Over"] == "ask"
    assert client.post(f"/api/sessions/{hall.id}/play", json={"take_over": "yes"}).status_code == 400
    r = client.post(f"/api/sessions/{hall.id}/play", json={"take_over": False})
    assert r.status_code == 200, r.text
    w.settle()
    assert wait_for(lambda: hall.is_playing)

    r = client.post(f"/api/speakers/{dev2}/test")
    assert r.status_code == 409 and r.json()["detail"] == "Fake devices is playing 'Den'. Stop it first."

    office = w.channel("Office", ["net:a"])
    w.play(office)
    body = {"type": "fake", "device": "dev/2"}
    r = client.post(f"/api/sessions/{office.id}/audio-outputs", json=body)
    assert r.status_code == 409 and r.headers["X-Sonorium-Take-Over"] == "ask"
    r = client.post(f"/api/sessions/{office.id}/audio-outputs", json={**body, "take_over": True})
    assert r.status_code == 201 and r.json()["running"], r.text
    assert wait_for(lambda: dev1 not in w.sm._resolve_selection(den))
    outputs = client.get(f"/api/sessions/{office.id}/audio-outputs").json()["attachments"]
    assert [o["device"] for o in outputs] == ["dev/2"]
    w.state.settings.enabled_speakers.remove(dev1)
    r = client.post(f"/api/sessions/{office.id}/audio-outputs", json={"type": "fake", "device": "dev/1"})
    assert r.status_code == 400 and "Switch the speaker on" in r.json()["detail"]
    r = client.delete(f"/api/sessions/{office.id}/audio-outputs/{outputs[0]['attachment_id']}")
    assert r.json() == {"removed": True}

    assert client.delete(f"/api/audio-outputs/speakers/{dev1}").status_code == 400  # online
    w.fake.devices = w.fake.devices[1:]
    w.registry.refresh = lambda: w.registry.hierarchy  # no Home Assistant here
    assert client.post("/api/speakers/refresh").status_code == 200  # Rescan lists output devices again too
    assert not w.registry.get_speaker(dev1).online
    assert client.delete(f"/api/audio-outputs/speakers/{dev1}").json() == {"removed": True}
    assert client.delete(f"/api/audio-outputs/speakers/{dev1}").status_code == 404
