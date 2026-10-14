"""
Plugin attributes on every object a screen shows (core/plugin_data.py, plugins/attributes.py):
theme groups, sequences and their steps, playlists and their entries, speaker groups, areas,
keys and connections, next to the objects of tests/test_plugin_attributes.py. Same rules:
declared only, checked, carried with the object (saves, edits, exports), removed on uninstall.
"""
import json
import logging
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()

NOTE = {"note": {"type": "string", "max_length": 20, "default": ""}}
KINDS = ("group", "sequence", "step", "playlist", "playlist_entry", "speaker_group", "area", "key", "connection", "plugin")
DEMO = {kind: dict(NOTE) for kind in KINDS}


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.plugin_data", "sonorium.core.theme_metadata", "sonorium.core.theme_edit",
                       "sonorium.core.theme_package", "sonorium.core.state", "sonorium.core.sequences",
                       "sonorium.core.playlists", "sonorium.plugins.attributes", "sonorium.plugins.ui_contract") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture
def world(tmp_path):
    store = m.state.StateStore(tmp_path / "state.json")
    m.plugin_data.registry.bind(lambda: store.settings.plugin_attributes, save=store.save)
    m.plugin_data.registry.declare("demo", DEMO)
    folder = tmp_path / "audio" / "Storm"
    (folder / "Thunder").mkdir(parents=True)
    for f in ("Rain.mp3", "Thunder/Close.mp3"):
        (folder / f).write_bytes(b"x")
    meta = {"spec_version": 2, "id": "id-Storm", "name": "Storm", "ebb_duration": True, "auto_retired": True,
            "intrusions_retired": True, "tracks": {k: {"playback_mode": "sparse"} for k in ("Rain", "Thunder/Close")},
            "groups": {"Thunder": {"mode": "intermittent"}}}
    (folder / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (folder / "presets.json").write_text(json.dumps({
        "presets": {"calm": {"name": "Calm", "tracks": {}}, "loud": {"name": "Loud", "tracks": {}}},
        "sequences": {"flow": {"name": "Flow", "presets": ["calm", "loud"], "at_end": "reverse"}}}), encoding="utf-8")
    themes = m.theme_metadata.ThemeMetadataManager(folder.parent)
    themes.scan_themes()
    pid, _ = m.playlists.create_playlist(store.playlists, {"name": "Evening", "entries": [
        {"theme_id": "id-Storm"}, {"theme_id": "id-Storm", "preset_id": "calm"}]},
        lambda t: True, lambda t: {"calm", "loud"}, lambda t: {"flow"})
    store.state.speaker_groups["g1"] = m.state.SpeakerGroup(id="g1", name="Downstairs")
    events = []
    service = m.attributes.AttributeService(store, SimpleNamespace(theme_metadata_manager=themes),
                                            publish=lambda kind, data, **kw: events.append((kind, data)))
    entry_id = store.playlists[pid]["entries"][1]["id"]
    refs = {
        "group": {"kind": "group", "theme": "id-Storm", "id": "Thunder"},
        "sequence": {"kind": "sequence", "theme": "id-Storm", "id": "flow"},
        "step": {"kind": "step", "theme": "id-Storm", "id": "flow/loud"},
        "playlist": {"kind": "playlist", "id": pid},
        "playlist_entry": {"kind": "playlist_entry", "id": f"{pid}/{entry_id}"},
        "speaker_group": {"kind": "speaker_group", "id": "g1"},
        "area": {"kind": "area", "id": "area_kitchen"},
        "key": {"kind": "key", "id": "k1"},
        "connection": {"kind": "connection", "id": "mqtt"},
        "plugin": {"kind": "plugin", "id": "theme_merge"},
    }
    yield SimpleNamespace(store=store, service=service, folder=folder, themes=themes, pid=pid, refs=refs,
                          events=events, tmp=tmp_path)
    m.plugin_data.registry.unbind()


def test_every_object_a_screen_shows_keeps_attributes():
    assert set(m.ui_contract.ATTRIBUTE_KINDS) == set(m.plugin_data.OBJECT_CLASSES)
    shown = {t["object"] for t in m.ui_contract.TARGETS if t["object"]}
    assert shown <= set(m.plugin_data.OBJECT_CLASSES)  # no target shows an object without attributes


@pytest.mark.parametrize("kind", KINDS)
def test_set_get_and_the_event_on_each_object(world, kind):
    ref = world.refs[kind]
    s = world.service
    assert s.get(ref, "demo") == {"note": ""}
    assert s.set(ref, "demo", {"note": kind[:20]}) == {"note": kind[:20]}
    assert s.get(ref)["demo"] == {"note": kind[:20]}
    event = world.events[-1]
    assert event[0] == "attribute.changed" and event[1]["kind"] == kind
    if kind in ("group", "sequence", "step"):
        assert event[1]["theme_id"] == "id-Storm"
    with pytest.raises(m.attributes.ActionError) as e:
        s.set(ref, "demo", {"note": "x" * 21})
    assert e.value.code == "invalid"
    with pytest.raises(m.attributes.ActionError):
        s.set(ref, "demo", {"other": 1})  # not declared


def test_unknown_objects_are_not_found(world):
    s = world.service
    for ref in ({"kind": "group", "theme": "id-Storm", "id": "Nope"},
                {"kind": "sequence", "theme": "id-Storm", "id": "nope"},
                {"kind": "step", "theme": "id-Storm", "id": "flow/nope"},
                {"kind": "playlist", "id": "nope"},
                {"kind": "playlist_entry", "id": f"{world.pid}/nope"},
                {"kind": "speaker_group", "id": "nope"}):
        with pytest.raises(m.attributes.ActionError) as e:
            s.get(ref)
        assert e.value.code == "not_found", ref


def test_values_are_saved_where_the_object_is(world):
    s = world.service
    for kind, ref in world.refs.items():
        s.set(ref, "demo", {"note": "n-" + kind[:10]})
    meta = json.loads((world.folder / "metadata.json").read_text(encoding="utf-8"))
    assert meta["groups"]["Thunder"]["plugins"] == {"demo": {"note": "n-group"}}
    seq = json.loads((world.folder / "presets.json").read_text(encoding="utf-8"))["sequences"]["flow"]
    assert seq["plugins"] == {"demo": {"note": "n-sequence"}}
    assert seq["step_plugins"] == {"loud": {"demo": {"note": "n-step"}}}
    saved = json.loads(world.store.state_file.read_text(encoding="utf-8"))
    playlist = saved["playlists"][world.pid]
    assert playlist["plugins"] == {"demo": {"note": "n-playlist"}}
    assert playlist["entries"][1]["plugins"] == {"demo": {"note": "n-playlist_e"}}
    assert saved["speaker_groups"]["g1"]["plugins"] == {"demo": {"note": "n-speaker_gr"}}
    assert saved["settings"]["object_plugins"] == {"area": {"area_kitchen": {"demo": {"note": "n-area"}}},
                                                   "key": {"k1": {"demo": {"note": "n-key"}}},
                                                   "connection": {"mqtt": {"demo": {"note": "n-connection"}}},
                                                   "plugin": {"theme_merge": {"demo": {"note": "n-plugin"}}}}
    # a fresh load reads them back
    again = m.state.StateStore(world.store.state_file)
    again.load()
    assert again.speaker_groups["g1"].plugins == {"demo": {"note": "n-speaker_gr"}}
    assert again.playlists[world.pid]["entries"][1]["plugins"] == {"demo": {"note": "n-playlist_e"}}


def test_an_entry_keeps_its_values_when_the_order_changes(world):
    s, pid = world.service, world.pid
    s.set(world.refs["playlist_entry"], "demo", {"note": "second"})
    entries = [dict(e) for e in world.store.playlists[pid]["entries"]]
    m.playlists.update_playlist(world.store.playlists, pid, {"entries": list(reversed(entries))},
                                lambda t: True, lambda t: {"calm", "loud"}, lambda t: {"flow"})
    now = world.store.playlists[pid]["entries"]
    assert now[0]["plugins"] == {"demo": {"note": "second"}} and now[0]["preset_id"] == "calm"
    assert s.get(world.refs["playlist_entry"], "demo") == {"note": "second"}  # by its id, wherever it is
    # a duplicated entry gets an id of its own
    m.playlists.update_playlist(world.store.playlists, pid, {"entries": [now[0], now[0]]},
                                lambda t: True, lambda t: {"calm", "loud"}, lambda t: {"flow"})
    ids = [e["id"] for e in world.store.playlists[pid]["entries"]]
    assert len(set(ids)) == 2


def test_sequence_edits_keep_values_and_a_removed_step_takes_its_own(world):
    s = world.service
    s.set(world.refs["sequence"], "demo", {"note": "seq"})
    s.set(world.refs["step"], "demo", {"note": "step"})
    sequences = m.sequences.load_sequences(world.folder)
    assert sequences["flow"]["step_plugins"] == {"loud": {"demo": {"note": "step"}}}
    edited = m.sequences.clean_sequence({"name": "Flow 2"}, ["calm", "loud"], current=sequences["flow"])
    assert edited["plugins"] == {"demo": {"note": "seq"}} and "loud" in edited["step_plugins"]
    edited = m.sequences.clean_sequence({"presets": ["calm"]}, ["calm", "loud"], current=edited)
    assert "step_plugins" not in edited and edited["plugins"] == {"demo": {"note": "seq"}}


def test_group_values_follow_group_edits(world):
    s = world.service
    s.set(world.refs["group"], "demo", {"note": "grp"})
    metadata = world.themes.get_metadata("id-Storm")
    settings = m.theme_edit.clean_group_update(metadata.groups["Thunder"], {"muted": True})
    assert settings["plugins"] == {"demo": {"note": "grp"}} and settings["muted"] is True
    merged = m.theme_edit.merge_preset_groups(metadata.groups, {"Thunder": {"volume": 0.5, "plugins": {"x": {}}}})
    assert merged["Thunder"]["plugins"] == {"demo": {"note": "grp"}}  # a preset never brings plugin values
    from sonorium.core.theme_groups import remap_groups
    renamed = remap_groups(dict(metadata.groups), {"Thunder": "Storms"})
    assert renamed["Storms"]["plugins"] == {"demo": {"note": "grp"}}
    # a preset captures only group playback settings, never plugin values
    assert "plugins" not in m.theme_edit.capture_groups(metadata.groups)["Thunder"]


def test_theme_objects_travel_with_the_export_and_import_keeps_only_declared(world, caplog):
    s = world.service
    for kind in ("group", "sequence", "step"):
        s.set(world.refs[kind], "demo", {"note": kind})
    package = m.theme_package.build_package(world.folder)
    meta = json.loads(package.by_path["metadata.json"].data)
    presets = json.loads(package.by_path["presets.json"].data)
    assert meta["groups"]["Thunder"]["plugins"] == {"demo": {"note": "group"}}
    assert presets["sequences"]["flow"]["plugins"] == {"demo": {"note": "sequence"}}
    assert presets["sequences"]["flow"]["step_plugins"]["loud"] == {"demo": {"note": "step"}}
    # an import where the plugin isn't installed: values go, with an info line
    raw = json.loads((world.folder / "metadata.json").read_text(encoding="utf-8"))
    raw["groups"]["Thunder"]["plugins"]["hue"] = {"x": 1}
    (world.folder / "metadata.json").write_text(json.dumps(raw), encoding="utf-8")
    m.plugin_data.registry.forget("demo")
    with caplog.at_level(logging.INFO):
        assert m.theme_metadata.drop_unknown_plugin_data(world.folder)
    raw = json.loads((world.folder / "metadata.json").read_text(encoding="utf-8"))
    assert "plugins" not in raw["groups"]["Thunder"]
    seq = json.loads((world.folder / "presets.json").read_text(encoding="utf-8"))["sequences"]["flow"]
    assert "plugins" not in seq and "step_plugins" not in seq
    assert "demo (not installed)" in caplog.text


def test_uninstall_removes_values_from_every_object(world):
    s = world.service
    for kind, ref in world.refs.items():
        s.set(ref, "demo", {"note": "x"})
    m.plugin_data.registry.declare("other", {"playlist": {"y": {"type": "bool"}}})
    s.set(world.refs["playlist"], "other", {"y": True})
    assert s.forget_plugin("demo") >= 7
    meta = json.loads((world.folder / "metadata.json").read_text(encoding="utf-8"))
    assert "plugins" not in meta["groups"]["Thunder"]
    seq = json.loads((world.folder / "presets.json").read_text(encoding="utf-8"))["sequences"]["flow"]
    assert "plugins" not in seq and "step_plugins" not in seq
    saved = json.loads(world.store.state_file.read_text(encoding="utf-8"))
    playlist = saved["playlists"][world.pid]
    assert playlist["plugins"] == {"other": {"y": True}}
    assert all("plugins" not in e for e in playlist["entries"])
    assert not saved["speaker_groups"]["g1"].get("plugins")
    assert saved["settings"]["object_plugins"] == {}
