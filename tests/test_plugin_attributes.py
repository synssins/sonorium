"""
Plugin attributes (Plugin Framework 2, phase 0): a plugin declares attributes
on core objects; core keeps only declared values, checks them, keeps them
through every save and edit, carries them in theme exports, and drops what
isn't in the schema on import. Also the per-speaker Sync setting.
"""
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from playback_helpers import addon_modules

m = SimpleNamespace()

WLED = {
    "theme": {"scene": {"type": "string", "max_length": 20, "default": "storm"}},
    "track": {"cue": {"type": "enum", "options": ["flash", "rumble", "auto"], "default": "auto",
                      "label": "Light cue"},
              "intensity": {"type": "number", "min": 0, "max": 100, "integer": True}},
    "file": {"cue": {"type": "enum", "options": ["flash", "rumble", "auto"], "default": "auto",
                     "label": "Light cue"}},
    "preset": {"palette": {"type": "list", "items": {"type": "string", "pattern": "^#[0-9a-f]{6}$"},
                           "max_items": 4}},
    "channel": {"strip": {"type": "string"}},
    "speaker": {"light_sync": {"type": "number", "min": 0, "label": "Light sync"}},
    "user": {"favourite": {"type": "object", "fields": {"colour": {"type": "string"},
                                                        "on": {"type": "bool"}}}},
}


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.plugin_data", "sonorium.core.theme_metadata", "sonorium.core.theme_edit",
                       "sonorium.core.theme_presets", "sonorium.core.theme_package", "sonorium.core.state",
                       "sonorium.core.speaker_settings", "sonorium.plugins.attributes", "sonorium.plugins.events",
                       "sonorium.plugins.host", "sonorium.plugins.manager", "sonorium.plugins.loader",
                       "sonorium.web.plugin_framework_api", "sonorium.auth.principal",
                       "sonorium.auth.permissions", "sonorium.ha.registry") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture
def store(tmp_path):
    """A state store with the registry bound to it, wled installed."""
    store = m.state.StateStore(tmp_path / "state.json")
    limit = {"value": 4096}
    m.plugin_data.registry.bind(lambda: store.settings.plugin_attributes, save=store.save,
                                limit=lambda: limit["value"])
    m.plugin_data.registry.declare("wled", WLED)
    store.limit = limit
    yield store
    m.plugin_data.registry.unbind()


def make_theme(tmp_path, name="Storm", extra_meta=None, presets=None):
    folder = tmp_path / "audio" / name
    (folder / "Thunder").mkdir(parents=True)
    for f in ("Rain.mp3", "Thunder/Close.mp3", "Thunder/Far.mp3"):
        (folder / f).write_bytes(b"x")
    tracks = {k: {"playback_mode": "continuous"} for k in ("Rain", "Thunder/Close", "Thunder/Far")}
    meta = {"spec_version": 2, "id": f"id-{name}", "name": name, "ebb_duration": True, "auto_retired": True,
            "intrusions_retired": True, "tracks": tracks}
    meta.update(extra_meta or {})
    (folder / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (folder / "presets.json").write_text(json.dumps({"presets": presets or {
        "calm": {"name": "Calm", "is_default": True, "tracks": {"Rain": {"volume": 0.3}}}}}), encoding="utf-8")
    return folder


def read_meta(folder):
    return json.loads((folder / "metadata.json").read_text(encoding="utf-8"))


def read_presets(folder):
    return json.loads((folder / "presets.json").read_text(encoding="utf-8"))["presets"]


# --- declarations and validation ------------------------------------------------------

def test_declarations_keep_the_good_and_log_the_bad(caplog):
    decls = {
        "track": {"ok": {"type": "number", "min": 0, "max": 9},
                  "Bad name!": {"type": "string"},
                  "weird": {"type": "colour"},
                  "enum_empty": {"type": "enum", "options": []},
                  "bad_default": {"type": "number", "max": 1, "default": 5}},
        "planet": {"x": {"type": "string"}},
        "speaker": {"flag": {"type": "boolean"}, "mode": {"type": "select", "options": ["a", "b"]}},
    }
    with caplog.at_level(logging.WARNING):
        clean = m.plugin_data.normalize_declarations(decls, "demo")
    assert clean == {"track": {"ok": {"type": "number", "min": 0, "max": 9}},
                     "speaker": {"flag": {"type": "bool"}, "mode": {"type": "enum", "options": ["a", "b"]}}}
    text = caplog.text
    for word in ("Bad name!", "colour", "enum_empty", "bad_default", "planet"):
        assert word in text


def test_track_and_file_share_one_meaning():
    clean = m.plugin_data.normalize_declarations(
        {"track": {"cue": {"type": "string"}}, "file": {"cue": {"type": "number"}, "other": {"type": "bool"}}}, "x")
    assert clean["file"] == {"other": {"type": "bool"}}


def test_values_are_checked_against_their_declaration():
    check = m.plugin_data.check_value
    E = m.plugin_data.PluginDataError
    num = {"type": "number", "min": 0, "max": 100, "integer": True}
    assert check(num, 50.0) == 50 and isinstance(check(num, 50.0), int)
    for bad in (101, -1, 2.5, True, "5", float("nan")):
        with pytest.raises(E):
            check(num, bad)
    enum = {"type": "enum", "options": [1, "a"]}
    assert check(enum, 1) == 1
    with pytest.raises(E):
        check(enum, True)  # True == 1, but it isn't the option
    lst = {"type": "list", "items": {"type": "string", "max_length": 3}, "max_items": 2}
    assert check(lst, ["ab", "c"]) == ["ab", "c"]
    for bad in (["abcd"], ["a", "b", "c"], "ab"):
        with pytest.raises(E):
            check(lst, bad)
    obj = {"type": "object", "fields": {"on": {"type": "bool"}}}
    assert check(obj, {"on": True}) == {"on": True}
    with pytest.raises(E):
        check(obj, {"on": True, "extra": 1})
    assert check(obj, {"on": True, "extra": 1}, strict=False) == {"on": True}
    pattern = {"type": "string", "pattern": "^#[0-9a-f]{6}$"}
    assert check(pattern, "#a0b1c2") == "#a0b1c2"
    with pytest.raises(E):
        check(pattern, "red")


def test_update_lists_every_problem_and_none_removes(store):
    reg = m.plugin_data.registry
    block = reg.update_block({}, "wled", "track", {"cue": "flash", "intensity": 90})
    assert block == {"wled": {"cue": "flash", "intensity": 90}}
    with pytest.raises(m.plugin_data.PluginDataError) as e:
        reg.update_block(block, "wled", "track", {"cue": "laser", "nope": 1, "intensity": 900})
    assert "laser" not in str(e.value) or "must be one of" in str(e.value)
    assert "'nope' is not an attribute" in str(e.value) and "at most 100" in str(e.value)
    assert reg.update_block(block, "wled", "track", {"intensity": None}) == {"wled": {"cue": "flash"}}
    assert reg.update_block(block, "wled", "track", None) == {}
    with pytest.raises(m.plugin_data.PluginDataError, match="not installed"):
        reg.update_block({}, "hue", "track", {"x": 1})
    # other plugins' values are left alone
    assert reg.update_block({"other": {"a": 1}}, "wled", "channel", {"strip": "desk"}) == \
        {"other": {"a": 1}, "wled": {"strip": "desk"}}


def test_defaults_fill_in_when_reading(store):
    reg = m.plugin_data.registry
    assert reg.values({}, "wled", ("track",)) == {"cue": "auto"}
    assert reg.values({"wled": {"cue": "flash", "intensity": "loud"}}, "wled", ("track",)) == {"cue": "flash"}


# --- the size limit ---------------------------------------------------------------------

def test_size_limit_refuses_with_a_message_and_is_adjustable(store):
    reg = m.plugin_data.registry
    big = "x" * 5000
    with pytest.raises(m.plugin_data.PluginDataError) as e:
        reg.update_block({}, "wled", "channel", {"strip": big})
    assert "4096 bytes" in str(e.value) and "plugin_data_limit" in str(e.value)
    store.limit["value"] = 8192
    assert reg.update_block({}, "wled", "channel", {"strip": big})["wled"]["strip"] == big
    store.limit["value"] = 0  # no limit
    assert reg.update_block({}, "wled", "channel", {"strip": "y" * 20000})


def test_stored_values_over_a_lowered_limit_are_kept_with_a_warning(store, caplog):
    store.limit["value"] = 10
    with caplog.at_level(logging.WARNING):
        kept = m.plugin_data.registry.clean_block({"wled": {"strip": "a long strip name"}}, ("channel",), "Channel")
    assert kept == {"wled": {"strip": "a long strip name"}}
    assert "over the 10-byte limit" in caplog.text


def test_the_limit_is_a_settings_value():
    import importlib.util
    spec = importlib.util.spec_from_file_location("pv_under_test", Path(m.plugin_data.__file__).parents[1] / "playback_values.py")
    pv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pv)
    assert pv.DEFAULTS[m.plugin_data.LIMIT_SETTING] == 4096.0
    assert pv.validate({"plugin_data_limit": 16384}) == {"plugin_data_limit": 16384.0}


# --- themes: every save path keeps declared values ------------------------------------------

def test_declared_track_values_survive_every_theme_edit(store, tmp_path):
    te, tm = m.theme_edit, m.theme_metadata
    folder = make_theme(tmp_path)
    metadata = te.load(folder)
    metadata.tracks["Thunder/Close"].plugins = {"wled": {"cue": "flash", "intensity": 90}}
    metadata.plugins = {"wled": {"scene": "night"}}
    metadata.presets["calm"]["plugins"] = {"wled": {"palette": ["#a0b1c2"]}}
    te.save(folder, metadata)

    def check():
        raw = read_meta(folder)
        assert raw["tracks"]["Thunder/Close"]["plugins"] == {"wled": {"cue": "flash", "intensity": 90}}, raw
        assert raw["plugins"] == {"wled": {"scene": "night"}}
        assert read_presets(folder)["calm"]["plugins"] == {"wled": {"palette": ["#a0b1c2"]}}

    check()
    te.set_track(folder, "Thunder/Close", volume=0.5)
    check()
    te.set_track(folder, "Rain", muted=True)
    check()
    te.new_preset(folder, "Loud")
    check()
    te.save_preset(folder, "calm")
    check()
    te.load_preset(folder, "calm")
    check()
    te.rename_preset(folder, "calm", "Quiet")
    check()
    te.reset_theme_tracks(folder)
    check()
    assert "plugins" not in read_meta(folder)["tracks"].get("Rain", {})  # an empty block isn't written

    # moving the file out of its group: its values follow its new key
    new_key = te.move_track(folder, "Thunder/Close", None)
    raw = read_meta(folder)
    assert raw["tracks"][new_key]["plugins"] == {"wled": {"cue": "flash", "intensity": 90}}


def test_the_server_metadata_manager_keeps_them(store, tmp_path):
    folder = make_theme(tmp_path, extra_meta={"plugins": {"wled": {"scene": "night"}}})
    raw = read_meta(folder)
    raw["tracks"]["Rain"]["plugins"] = {"wled": {"cue": "rumble"}}
    (folder / "metadata.json").write_text(json.dumps(raw), encoding="utf-8")
    manager = m.theme_metadata.ThemeMetadataManager(folder.parent)
    manager.scan_themes()
    manager.update_track_settings("id-Storm", "Rain", volume=0.2)
    manager.update_metadata("id-Storm", description="Rolling thunder", is_favorite=True)
    raw = read_meta(folder)
    assert raw["tracks"]["Rain"]["plugins"] == {"wled": {"cue": "rumble"}} and raw["tracks"]["Rain"]["volume"] == 0.2
    assert raw["plugins"] == {"wled": {"scene": "night"}} and raw["description"] == "Rolling thunder"


def test_undeclared_values_go_when_a_theme_loads(store, tmp_path):
    folder = make_theme(tmp_path, extra_meta={"plugins": {"wled": {"scene": "night", "secret": 1},
                                                          "hue": {"scene": "x"}}})
    metadata = m.theme_edit.load(folder)
    assert metadata.plugins == {"wled": {"scene": "night"}}


def test_without_a_registry_values_pass_through(tmp_path):
    m.plugin_data.registry.unbind()
    folder = make_theme(tmp_path, extra_meta={"plugins": {"hue": {"scene": "x"}}})
    metadata = m.theme_edit.load(folder)
    assert metadata.plugins == {"hue": {"scene": "x"}}


# --- export and import ------------------------------------------------------------------

def test_plugin_values_travel_with_the_export(store, tmp_path):
    folder = make_theme(tmp_path, extra_meta={"plugins": {"wled": {"scene": "night"}}})
    metadata = m.theme_edit.load(folder)
    metadata.tracks["Thunder/Far"].plugins = {"wled": {"cue": "rumble"}}
    metadata.presets["calm"]["plugins"] = {"wled": {"palette": ["#000000"]}}
    m.theme_edit.save(folder, metadata)
    package = m.theme_package.build_package(folder)
    exported_meta = json.loads(package.by_path["metadata.json"].data)
    exported_presets = json.loads(package.by_path["presets.json"].data)
    assert exported_meta["plugins"] == {"wled": {"scene": "night"}}
    assert exported_meta["tracks"]["Thunder/Far"]["plugins"] == {"wled": {"cue": "rumble"}}
    assert exported_presets["presets"]["calm"]["plugins"] == {"wled": {"palette": ["#000000"]}}


def test_import_keeps_declared_and_drops_the_rest_with_a_log_line(store, tmp_path, caplog):
    folder = make_theme(tmp_path, extra_meta={"plugins": {"wled": {"scene": "night", "unknown": 1},
                                                          "hue": {"room": "attic"}}},
                        presets={"calm": {"name": "Calm", "tracks": {},
                                          "plugins": {"hue": {"x": 1}, "wled": {"palette": ["#000000"]}}},
                                 "loud": {"name": "Loud", "tracks": {}, "plugins": {"hue": {"x": 1}}}})
    raw = read_meta(folder)
    raw["tracks"]["Rain"]["plugins"] = {"hue": {"x": 1}}
    raw["tracks"]["Thunder/Far"]["plugins"] = {"wled": {"cue": "laser"}}
    (folder / "metadata.json").write_text(json.dumps(raw), encoding="utf-8")

    with caplog.at_level(logging.INFO):
        assert m.theme_metadata.drop_unknown_plugin_data(folder)
    raw = read_meta(folder)
    assert raw["plugins"] == {"wled": {"scene": "night"}}
    assert "plugins" not in raw["tracks"]["Rain"] and "plugins" not in raw["tracks"]["Thunder/Far"]
    presets = read_presets(folder)
    assert presets["calm"]["plugins"] == {"wled": {"palette": ["#000000"]}}
    assert "plugins" not in presets["loud"]
    assert "hue (not installed)" in caplog.text and "wled.unknown (not declared)" in caplog.text
    assert any(r.levelno == logging.INFO for r in caplog.records)
    # nothing left to drop: the files aren't written again
    assert not m.theme_metadata.drop_unknown_plugin_data(folder)


def test_import_into_a_sonorium_without_the_plugin_drops_everything(tmp_path):
    m.plugin_data.registry.unbind()  # no plugin installed at all
    folder = make_theme(tmp_path, extra_meta={"plugins": {"wled": {"scene": "night"}}})
    assert m.theme_metadata.drop_unknown_plugin_data(folder)
    assert "plugins" not in read_meta(folder)


def test_import_of_a_broken_theme_file_does_not_raise(store, tmp_path):
    folder = make_theme(tmp_path)
    (folder / "metadata.json").write_text("{broken", encoding="utf-8")
    assert m.theme_metadata.drop_unknown_plugin_data(folder) is False


# --- speakers: Sync and plugin attributes -------------------------------------------------

def test_speaker_settings_take_sync_and_declared_plugin_values(store):
    clean = m.speaker_settings.clean_speaker_settings
    E = m.speaker_settings.SpeakerSettingsError
    result = clean({"name": "Office"}, {"sync": 4200, "plugins": {"wled": {"light_sync": 300}}})
    assert result == {"name": "Office", "sync": 4200, "plugins": {"wled": {"light_sync": 300}}}
    assert clean(result, {"sync": 4100.0})["sync"] == 4100
    assert "sync" not in clean(result, {"sync": None})
    for bad in (-1, 1.5, "fast", True, float("nan")):
        with pytest.raises(E):
            clean({}, {"sync": bad})
    with pytest.raises(E, match="not an attribute"):
        clean({}, {"plugins": {"wled": {"brightness": 3}}})
    with pytest.raises(E, match="not installed"):
        clean({}, {"plugins": {"hue": {"x": 1}}})
    with pytest.raises(E, match="Unknown speaker setting"):
        clean({}, {"colour": "red"})
    # other settings and the plugin's other values stay; null removes one plugin's values
    assert clean(result, {"plugins": {"wled": None}}) == {"name": "Office", "sync": 4200}


def test_sync_defaults_to_the_estimate_for_the_speaker_type():
    ss = m.speaker_settings
    assert ss.estimated_sync_ms("cast") == 5000 and ss.estimated_sync_ms("esphome") == 1500
    assert ss.estimated_sync_ms(None) == 3000
    assert ss.sync_ms({}, "sonos") == 3000 and ss.sync_ms({"sync": 250}, "sonos") == 250
    assert ss.sync_ms({"sync": 0}, "cast") == 0


def test_the_speakers_api_shows_sync():
    speaker = m.registry.Speaker(entity_id="media_player.hub", name="Hub", type="cast")
    speaker.sync_estimate_ms = m.speaker_settings.estimated_sync_ms("cast")
    speaker.sync_ms = m.speaker_settings.sync_ms({"sync": 4200}, "cast")
    data = speaker.to_dict()
    assert data["sync_ms"] == 4200 and data["sync_estimate_ms"] == 5000


# --- channels ---------------------------------------------------------------------------

def test_channel_values_are_saved_with_the_channel(store, tmp_path):
    session = m.state.Session(id="c1", name="Den", plugins={"wled": {"strip": "desk"}})
    store.state.sessions["c1"] = session
    store.save()
    loaded = m.state.StateStore(tmp_path / "state.json")
    loaded.load()
    assert loaded.sessions["c1"].plugins == {"wled": {"strip": "desk"}}
    assert loaded.settings.plugin_attributes["wled"]["channel"] == {"strip": {"type": "string"}}


# --- the service: every object kind, events, switch-off and uninstall ------------------------

class Accounts:
    def __init__(self):
        self.users = {"u1": {"username": "chris", "role": "admin"}}
        self.saved = 0

    def save(self):
        self.saved += 1


@pytest.fixture
def world(store, tmp_path):
    folder = make_theme(tmp_path)
    themes = m.theme_metadata.ThemeMetadataManager(folder.parent)
    themes.scan_themes()
    store.state.sessions["c1"] = m.state.Session(id="c1", name="Den")
    speakers = SimpleNamespace(get_speaker=lambda sid: object() if sid == "media_player.hub" else None)
    events = []
    accounts = Accounts()
    service = m.attributes.AttributeService(
        store, SimpleNamespace(theme_metadata_manager=themes, registry=speakers),
        SimpleNamespace(plugin_state={"users": accounts}),
        publish=lambda kind, data, **kw: events.append((kind, data, kw)))
    return SimpleNamespace(service=service, folder=folder, themes=themes, store=store, events=events,
                           accounts=accounts)


REFS = [
    {"kind": "theme", "id": "id-Storm"},
    {"kind": "track", "theme": "id-Storm", "id": "Rain"},
    {"kind": "file", "theme": "id-Storm", "id": "Thunder/Far"},
    {"kind": "preset", "theme": "id-Storm", "id": "calm"},
    {"kind": "channel", "id": "c1"},
    {"kind": "speaker", "id": "media_player.hub"},
    {"kind": "user", "id": "u1"},
]
VALUES = {"theme": {"scene": "night"}, "track": {"cue": "flash"}, "file": {"cue": "rumble"},
          "preset": {"palette": ["#123456"]}, "channel": {"strip": "desk"}, "speaker": {"light_sync": 120},
          "user": {"favourite": {"colour": "blue", "on": True}}}


def test_set_and_get_on_every_kind(world):
    s = world.service
    for ref in REFS:
        values = s.set(ref, "wled", VALUES[ref["kind"]])
        assert all(values[k] == v for k, v in VALUES[ref["kind"]].items()), ref
        assert s.get(ref)["wled"] == values
    # saved where each kind lives
    raw = read_meta(world.folder)
    assert raw["plugins"] == {"wled": {"scene": "night"}}
    assert raw["tracks"]["Rain"]["plugins"] == {"wled": {"cue": "flash"}}
    assert raw["tracks"]["Thunder/Far"]["plugins"] == {"wled": {"cue": "rumble"}}
    assert read_presets(world.folder)["calm"]["plugins"] == {"wled": {"palette": ["#123456"]}}
    saved = json.loads((world.store.state_file).read_text())
    assert saved["sessions"]["c1"]["plugins"] == {"wled": {"strip": "desk"}}
    assert saved["settings"]["speaker_settings"]["media_player.hub"] == {"plugins": {"wled": {"light_sync": 120}}}
    assert world.accounts.users["u1"]["plugins"] == {"wled": {"favourite": {"colour": "blue", "on": True}}}
    kinds = [e[1]["kind"] for e in world.events if e[0] == "attribute.changed"]
    assert kinds == [r["kind"] for r in REFS]
    assert world.events[1][1] == {"kind": "track", "id": "Rain", "plugin_id": "wled", "keys": ["cue"],
                                  "theme_id": "id-Storm"}


def test_bad_references_and_values(world):
    s, ActionError = world.service, m.attributes.ActionError
    cases = [({"kind": "planet", "id": "x"}, "invalid"), ({"kind": "track", "id": "Rain"}, "invalid"),
             ({"kind": "track", "theme": "nope", "id": "Rain"}, "not_found"),
             ({"kind": "track", "theme": "id-Storm", "id": "Nope"}, "not_found"),
             ({"kind": "file", "theme": "id-Storm", "id": "Rain"}, "invalid"),
             ({"kind": "speaker", "id": "media_player.gone"}, "not_found"),
             ({"kind": "channel", "id": "zz"}, "not_found")]
    for ref, code in cases:
        with pytest.raises(ActionError) as e:
            s.get(ref)
        assert e.value.code == code, ref
    with pytest.raises(ActionError) as e:
        s.set({"kind": "track", "theme": "id-Storm", "id": "Rain"}, "wled", {"cue": "laser"})
    assert e.value.code == "invalid"


def test_switched_off_keeps_values_and_schema(world, tmp_path):
    ref = {"kind": "channel", "id": "c1"}
    world.service.set(ref, "wled", {"strip": "desk"})
    plugin = SimpleNamespace(id="wled", name="WLED", enabled=True)
    plugin.on_disable = _async_none
    manager = m.manager.PluginManager(world.store, plugins_dir=tmp_path / "plugins")
    manager.plugins["wled"] = plugin
    import asyncio
    asyncio.run(manager.disable_plugin("wled"))
    assert m.plugin_data.registry.has("wled")
    assert world.service.get(ref)["wled"] == {"strip": "desk"}
    # and it still comes back from disk after a theme reload
    world.service.set({"kind": "track", "theme": "id-Storm", "id": "Rain"}, "wled", {"cue": "flash"})
    assert m.theme_edit.load(world.folder).tracks["Rain"].plugins == {"wled": {"cue": "flash"}}


async def _async_none(*a, **k):
    return None


def test_uninstall_removes_the_schema_and_every_value(world):
    s = world.service
    for ref in REFS:
        s.set(ref, "wled", VALUES[ref["kind"]])
    # another plugin's values stay
    m.plugin_data.registry.declare("other", {"channel": {"x": {"type": "bool"}}})
    s.set({"kind": "channel", "id": "c1"}, "other", {"x": True})

    assert s.forget_plugin("wled") == 4  # one theme (with its track, file and preset), a channel, a speaker, a user
    assert not m.plugin_data.registry.has("wled")
    raw = read_meta(world.folder)
    assert "plugins" not in raw and all("plugins" not in t for t in raw["tracks"].values())
    assert "plugins" not in read_presets(world.folder)["calm"]
    saved = json.loads(world.store.state_file.read_text())
    assert saved["sessions"]["c1"]["plugins"] == {"other": {"x": True}}
    assert "media_player.hub" not in saved["settings"]["speaker_settings"]
    assert "wled" not in saved["settings"]["plugin_attributes"]
    assert "plugins" not in world.accounts.users["u1"]
    # a theme with its values brought back later loses them on load
    raw["plugins"] = {"wled": {"scene": "night"}}
    (world.folder / "metadata.json").write_text(json.dumps(raw), encoding="utf-8")
    assert m.theme_edit.load(world.folder).plugins == {}


def test_plugin_facade_and_manifest_declaration(world, tmp_path):
    import asyncio
    plugins_dir = tmp_path / "plugins"
    (plugins_dir / "lights").mkdir(parents=True)
    (plugins_dir / "lights" / "manifest.json").write_text(json.dumps({
        "id": "lights", "name": "Lights", "version": "1.0.0", "api_version": "1.2", "plugin_class": "Lights",
        "requires": ["attributes"],
        "attributes": {"channel": {"zone": {"type": "string", "default": "all"}}}}), encoding="utf-8")
    (plugins_dir / "lights" / "plugin.py").write_text(
        "from sonorium.plugins.base import BasePlugin\n"
        "class Lights(BasePlugin):\n"
        "    id = 'lights'\n    name = 'Lights'\n", encoding="utf-8")
    host = m.host.PluginHost(tmp_path / "config", session_manager=world.service.session_manager,
                             state_store=world.store)
    host.attributes = world.service
    m.loader.BUILTIN_PLUGINS_DIR = None
    manager = m.manager.PluginManager(world.store, plugins_dir=plugins_dir, audio_path=tmp_path / "audio", host=host)
    asyncio.run(manager.initialize())
    plugin = manager.get_plugin("lights")
    assert plugin is not None
    assert world.store.settings.plugin_attributes["lights"] == {"channel": {"zone": {"type": "string", "default": "all"}}}
    ref = {"kind": "channel", "id": "c1"}
    assert plugin.attributes.get(ref) == {"zone": "all"}
    assert plugin.attributes.set(ref, {"zone": "kitchen"}) == {"zone": "kitchen"}
    assert world.events[-1][2] == {"origin": "plugin:lights"}
    assert plugin.attributes.clear(ref) == {"zone": "all"}
    plugin.attributes.declare({"speaker": {"lag": {"type": "number"}}})
    assert plugin.attributes.schema() == {"speaker": {"lag": {"type": "number"}}}


# --- HTTP routes ----------------------------------------------------------------------------

@pytest.fixture
def client(world):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    perms = {"value": frozenset(m.permissions.ALL_PERMS)}
    app = FastAPI()

    @app.middleware("http")
    async def signed_in(request: Request, call_next):
        request.state.auth = m.principal.Principal("session", perms=perms["value"], user_id="u2")
        return await call_next(request)

    host = SimpleNamespace(attributes=world.service)
    app.include_router(m.plugin_framework_api.create_plugin_framework_router(SimpleNamespace(), host))
    with TestClient(app) as c:
        yield SimpleNamespace(c=c, perms=perms)


def test_routes_read_and_change_attributes(client):
    c = client.c
    schema = c.get("/api/attributes").json()
    assert schema["limit"] == 4096 and "track" in schema["plugins"]["wled"]
    r = c.put("/api/attributes/track/wled", params={"theme": "id-Storm", "id": "Thunder/Close"},
              json={"cue": "flash", "intensity": 70})
    assert r.status_code == 200, r.text
    assert r.json()["values"] == {"cue": "flash", "intensity": 70}
    r = c.get("/api/attributes/track", params={"theme": "id-Storm", "id": "Thunder/Close"})
    assert r.json()["plugins"] == {"wled": {"cue": "flash", "intensity": 70}}
    bad = c.put("/api/attributes/track/wled", params={"theme": "id-Storm", "id": "Rain"}, json={"cue": "laser"})
    assert bad.status_code == 400 and "must be one of" in bad.json()["detail"]
    assert c.get("/api/attributes/track", params={"theme": "nope", "id": "Rain"}).status_code == 404
    assert c.get("/api/attributes/planet", params={"id": "x"}).status_code == 404
    assert c.put("/api/attributes/channel/wled", params={"id": "c1"}, json=["x"]).status_code == 400


def test_routes_check_the_ability_for_the_kind(client):
    c, perms = client.c, client.perms
    perms["value"] = frozenset({"th.view", "self"})
    assert c.get("/api/attributes/track", params={"theme": "id-Storm", "id": "Rain"}).status_code == 200
    assert c.put("/api/attributes/track/wled", params={"theme": "id-Storm", "id": "Rain"},
                 json={"cue": "flash"}).status_code == 403
    assert c.get("/api/attributes/speaker", params={"id": "media_player.hub"}).status_code == 403
    assert c.get("/api/attributes/user", params={"id": "u1"}).status_code == 403  # someone else
    perms["value"] = frozenset({"sp.set", "sp.view"})
    assert c.put("/api/attributes/speaker/wled", params={"id": "media_player.hub"},
                 json={"light_sync": 50}).status_code == 200


def test_every_attribute_route_has_a_permission_entry():
    table = m.permissions._ROUTES
    for line in ("GET     /api/attributes ", "GET     /api/attributes/{kind} ", "PUT     /api/attributes/{kind}/{plugin_id} "):
        assert line in table
