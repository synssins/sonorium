"""
Linked files, end to end: two real themes on disk ("Camping" with crickets
and an empty "Weather" group, "Rain" with rain), the real metadata manager,
the real theme refresh and the real mixer, with generated tone mp3s told
apart by FFT. A linked file can be ungrouped or in any group (intrusion
groups are retired: see test_linked_files.py for their conversion).
"""

import asyncio
import io
import json
import sys
import types
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

np = pytest.importorskip("numpy")
av = pytest.importorskip("av")
pytest.importorskip("fastapi")

ADDON = str(Path(__file__).resolve().parents[1] / "sonorium_addon")
RATE = 44100
CHUNK = 1024
CRICKETS_HZ, RAIN_HZ, THUNDER_HZ = 1000.0, 300.0, 600.0
m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def addon_package():
    """The add-on's sonorium package (api.py with fmtr stubbed), for these tests only."""
    saved = {k: v for k, v in sys.modules.items()
             if k in ("sonorium", "fmtr") or k.startswith(("sonorium.", "fmtr."))}
    for k in saved:
        del sys.modules[k]
    sys.path.insert(0, ADDON)
    try:
        utils = pytest.importorskip("sonorium.utils")
        fmtr = types.ModuleType("fmtr")
        tools = types.ModuleType("fmtr.tools")
        tools.api = SimpleNamespace(Base=object, Endpoint=MagicMock())
        tools.http = MagicMock()
        iterator_tools = types.ModuleType("fmtr.tools.iterator_tools")
        iterator_tools.IndexList = utils.IndexList
        tools.iterator_tools = iterator_tools
        fmtr.tools = tools
        sys.modules.update({"fmtr": fmtr, "fmtr.tools": tools, "fmtr.tools.iterator_tools": iterator_tools})
        import sonorium.api
        import sonorium.core.intrusions
        import sonorium.core.theme_metadata
        import sonorium.recording
        import sonorium.theme
        m.api = sonorium.api
        m.meta = sonorium.core.theme_metadata
        m.intrusions = sonorium.core.intrusions
        m.recording = sonorium.recording
        m.theme = sonorium.theme
        m.IndexList = utils.IndexList
        yield
    finally:
        sys.path.remove(ADDON)
        for k in [k for k in sys.modules
                  if k in ("sonorium", "fmtr") or k.startswith(("sonorium.", "fmtr."))]:
            del sys.modules[k]
        sys.modules.update(saved)


# --- audio and themes on disk ---------------------------------------------------

def tone_mp3(path: Path, seconds: float, freq: float, amp=6000):
    path.parent.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(seconds * RATE)) / RATE
    samples = (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16).reshape(1, -1)
    out = av.open(str(path), "w")
    stream = out.add_stream("mp3", rate=RATE)
    stream.layout = "mono"
    for i in range(0, samples.shape[1], 1152):
        frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(samples[:, i:i + 1152]), format="s16p", layout="mono")
        frame.rate = RATE
        for packet in stream.encode(frame):
            out.mux(packet)
    for packet in stream.encode(None):
        out.mux(packet)
    out.close()


def write_theme(folder: Path, theme_id: str, groups=None, tracks=None, presets=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "metadata.json").write_text(json.dumps({
        "spec_version": 2, "id": theme_id, "name": folder.name,
        "tracks": tracks or {}, "groups": groups or {},
    }), encoding="utf-8")
    (folder / "presets.json").write_text(json.dumps({"presets": presets or {}}), encoding="utf-8")


@pytest.fixture(scope="module")
def tones(tmp_path_factory):
    """The tone files, made once (mp3 encoding takes a moment)."""
    base = tmp_path_factory.mktemp("tones")
    for name, freq in (("crickets", CRICKETS_HZ), ("rain", RAIN_HZ), ("thunder", THUNDER_HZ)):
        tone_mp3(base / f"{name}.mp3", 30.0, freq)
    return base


@pytest.fixture
def world(tmp_path, tones):
    """Camping (crickets + an empty "Weather" group) and Rain (rain), loaded by the real API."""
    import shutil
    root = tmp_path / "themes"
    write_theme(root / "Camping", "camping-id", groups={"Weather": {}})
    (root / "Camping" / "Weather").mkdir()
    shutil.copy(tones / "crickets.mp3", root / "Camping" / "Crickets.mp3")
    write_theme(root / "Rain", "rain-id", tracks={"Rain": {"volume": 0.8, "presence": 1.0}})
    shutil.copy(tones / "rain.mp3", root / "Rain" / "Rain.mp3")

    device = SimpleNamespace(path_audio=root, themes=m.IndexList(), theme_metas={}, metas=m.IndexList(),
                             master_volume=1.0)
    api = object.__new__(m.api.ApiSonorium)
    api.client = SimpleNamespace(device=device)
    api._theme_metadata_manager = m.meta.ThemeMetadataManager(root)
    api._theme_refresh_task = None
    api._session_manager = None
    api._mqtt_manager = None
    run(api.refresh_themes())
    return SimpleNamespace(root=root, api=api, device=device, tones=tones)


class Body:
    def __init__(self, data):
        self._data = data

    async def json(self):
        return self._data


def run(coro):
    return asyncio.run(coro)


def theme(world, name):
    return next(t for t in world.device.themes if t.name == name)


def metadata_on_disk(world, name):
    return json.loads((world.root / name / "metadata.json").read_text(encoding="utf-8"))


def link_rain(world, group=None):
    """Link Rain's file into Camping: ungrouped (no "group" sent), or into group."""
    body = {"theme": "rain-id", "tracks": ["Rain"], **({"group": group} if group else {})}
    response = run(world.api.add_links("camping-id", Body(body)))
    assert response.status_code == 201
    return json.loads(response.body)["links"]


def place_rain(world):
    """The ungrouped linked Rain out of Unassigned, into Background (as the editor's Mode menu does)."""
    result = run(world.api.set_track_playback_mode("camping-id", "Rain", Body({"playback_mode": "continuous"})))
    assert result["playback_mode"] == "continuous"


# --- measuring the mix ---------------------------------------------------------

def mix(stream, seconds):
    chunks = stream.iter_chunks()
    return np.concatenate([next(chunks).reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]).astype(np.float64)


def level(signal, freq):
    """Amplitude of one frequency over the last second of the signal."""
    part = signal[-RATE:]
    spectrum = np.abs(np.fft.rfft(part * np.hanning(len(part))))
    bin_ = int(round(freq * len(part) / RATE))
    return spectrum[bin_ - 2:bin_ + 3].max() / (len(part) / 4)


# --- tests ----------------------------------------------------------------------

def test_a_b_linked_file_plays_from_the_source_folder_and_starts_muted_and_unassigned(world):
    links = link_rain(world)
    assert links == [{"key": "Rain", "theme": "rain-id", "track": "Rain", "added": True}]

    camping = theme(world, "Camping")
    linked = next(i for i in camping.instances if i.name == "Rain")
    assert linked.meta.path == world.root / "Rain" / "Rain.mp3"  # not copied
    assert not (world.root / "Camping" / "Rain.mp3").exists()
    assert linked.meta.group is None  # ungrouped
    # (b) added muted and Unassigned, with the source theme's other settings
    assert linked.is_enabled is False
    saved = metadata_on_disk(world, "Camping")
    assert saved["links"] == {"Rain": {"theme": "rain-id", "track": "Rain"}}
    assert saved["tracks"]["Rain"]["muted"] is True and saved["tracks"]["Rain"]["playback_mode"] == "unassigned"
    assert saved["tracks"]["Rain"]["volume"] == 0.8

    # The theme's own sound is unchanged: crickets only
    signal = mix(camping.get_stream(), 3.0)
    assert level(signal, CRICKETS_HZ) > 1000 and level(signal, RAIN_HZ) < 50
    # Unassigned doesn't play, even turned on
    on = m.recording.preset_track_overrides({"Rain": {"muted": False}, "Crickets": {"muted": True}})
    assert level(mix(camping.get_stream(dict(on)), 3.0), RAIN_HZ) < 50

    # (a) placed (Background) and a preset turning it on: the rain plays through Camping's mix, from the Rain folder
    place_rain(world)
    signal = mix(theme(world, "Camping").get_stream(dict(on)), 3.0)
    assert level(signal, RAIN_HZ) > 1000 and level(signal, CRICKETS_HZ) < 50


def test_c_switching_presets_on_a_playing_mix(world):
    link_rain(world)
    place_rain(world)
    camping = theme(world, "Camping")
    overrides = {}
    stream = camping.get_stream(overrides)
    chunks = stream.iter_chunks()

    def pull(seconds):
        return np.concatenate([next(chunks).reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]).astype(np.float64)

    before = pull(2.0)
    assert level(before, CRICKETS_HZ) > 1000 and level(before, RAIN_HZ) < 50
    # The rain preset (linked track on, crickets muted) applied in place, as a channel does
    overrides.update(m.recording.preset_track_overrides({"Rain": {"muted": False}, "Crickets": {"muted": True}}))
    during = pull(4.0)  # after the 3 s on/off crossfade
    assert level(during, RAIN_HZ) > 1000 and level(during, CRICKETS_HZ) < 50
    # Back to the theme's own settings
    overrides.clear()
    after = pull(4.0)
    assert level(after, CRICKETS_HZ) > 1000 and level(after, RAIN_HZ) < 50


def test_a_linked_file_in_a_group_plays_the_groups_way(world):
    """A linked file is an ordinary file of its group: it takes turns in an Intermittent group."""
    import shutil
    shutil.copy(world.tones / "thunder.mp3", world.root / "Camping" / "Weather" / "Thunder.mp3")
    run(world.api.refresh_themes())
    assert link_rain(world, "Weather")[0]["key"] == "Weather/Rain"
    camping = theme(world, "Camping")
    assert next(i for i in camping.instances if i.name == "Weather/Rain").meta.group == "Weather"
    saved = metadata_on_disk(world, "Camping")["tracks"]["Weather/Rain"]
    assert saved["muted"] is True and saved["playback_mode"] != "unassigned"  # as the source has it
    stream = camping.get_stream(m.recording.preset_track_overrides({"Weather/Rain": {"muted": False}}))
    views = [stream._views[name] for name in ("Weather/Rain", "Weather/Thunder")]
    assert all(v.exclusion_group == "Weather" for v in views)
    assert all(v.playback_mode == m.recording.PlaybackMode.SPARSE for v in views)

    # Ebb & Flow: every file of the group at once, the linked one too (a playing mix follows)
    run(world.api.update_group("camping-id", "Weather", Body({"mode": "ebb_flow"})))
    next(stream.iter_chunks())
    assert stream._views["Weather/Rain"].exclusion_group is None
    assert stream._views["Weather/Rain"].playback_mode == m.recording.PlaybackMode.PRESENCE


def test_d_has_intrusion_flag_on_presets(world):
    """The ⚭ mark: a preset that turns a linked file on, wherever it is."""
    link_rain(world)
    link_rain(world, "Weather")
    api = world.api
    metadata = api._theme_metadata_manager.get_metadata("camping-id")
    metadata.presets.update({
        "rain": {"name": "Rain", "tracks": {"Crickets": {"muted": True}, "Rain": {"muted": False}}},
        "quiet": {"name": "Quiet", "tracks": {"Crickets": {"muted": False}}},
        "in_group": {"name": "In group", "tracks": {"Weather/Rain": {"muted": False}}},
        "group_off": {"name": "Group off", "tracks": {"Weather/Rain": {"muted": False}},
                      "groups": {"Weather": {"muted": True}}},
    })
    api._theme_metadata_manager.save_metadata("camping-id", metadata)
    listed = run(api.list_presets("camping-id"))
    flags = {p["id"]: p["has_intrusion"] for p in listed["presets"]}
    assert flags == {"rain": True, "quiet": False, "in_group": True, "group_off": False}
    assert listed["default_has_intrusion"] is False  # linked files start muted
    assert listed["sequences"] == []

    # A theme's own file in a group doesn't count, only linked files
    import shutil
    shutil.copy(world.tones / "thunder.mp3", world.root / "Camping" / "Weather" / "Thunder.mp3")
    run(api.refresh_themes())
    metadata = api._theme_metadata_manager.get_metadata("camping-id")
    metadata.presets["thunder"] = {"name": "Thunder", "tracks": {"Weather/Thunder": {"muted": False}}}
    api._theme_metadata_manager.save_metadata("camping-id", metadata)
    assert {p["id"]: p["has_intrusion"] for p in run(api.list_presets("camping-id"))["presets"]}["thunder"] is False

    # Sequences are listed next to the presets (a channel picks them like a
    # preset); one has an intrusion if any of its presets has
    from sonorium.core import sequences as seq
    folder = api._theme_metadata_manager.get_folder_for_id("camping-id")
    seq.save_sequences(folder, {
        "wet": {"name": "Wet", "presets": ["quiet", "rain"], "at_end": "reverse", "ramp": 20},
        "dry": {"name": "Dry", "presets": ["quiet", "group_off"], "at_end": "stop"},
    })
    listed = run(api.list_presets("camping-id"))
    assert [p["id"] for p in listed["presets"]] == ["rain", "quiet", "in_group", "group_off", "thunder"]  # unchanged
    sequences = {s["id"]: s for s in listed["sequences"]}
    assert sequences["wet"]["has_intrusion"] is True and sequences["dry"]["has_intrusion"] is False
    wet = sequences["wet"]
    assert (wet["name"], wet["presets"], wet["at_end"], wet["choice"]) == ("Wet", ["quiet", "rain"], "reverse", "seq:wet")
    assert wet["effective"] == {"hold_min": 300, "hold_max": 900, "ramp": 20}
    assert (wet["pass_min"], wet["pass_max"]) == (2 * 300 + 20, 2 * 900 + 20)

    # A preset saved from the editor keeps the linked track (muted here)
    run(api.create_preset("camping-id", Body({"name": "Snapshot"})))
    snapshot = api._theme_metadata_manager.get_metadata("camping-id").presets["snapshot"]
    assert snapshot["tracks"]["Rain"]["muted"] is True


def test_e_missing_source_is_skipped_and_the_link_kept(world):
    link_rain(world)
    place_rain(world)
    api = world.api
    camping = theme(world, "Camping")
    stream = camping.get_stream(m.recording.preset_track_overrides({"Rain": {"muted": False}}))
    assert "Rain" in stream._views

    # The source theme's folder is renamed: found again by its id
    (world.root / "Rain").rename(world.root / "Rain storm")
    run(api.refresh_themes())
    assert any(i.name == "Rain" for i in theme(world, "Camping").instances)

    # The source file is removed: the rescan updates Camping, and the playing mix lets it go
    (world.root / "Rain storm" / "Rain.mp3").unlink()
    run(api.refresh_themes())
    camping = theme(world, "Camping")
    assert all(i.name != "Rain" for i in camping.instances)
    assert "Rain" not in stream._views
    saved = metadata_on_disk(world, "Camping")
    assert "Rain" in saved["links"] and "Rain" in saved["tracks"]

    tracks = {t["name"]: t for t in run(api.get_theme_tracks("camping-id"))["tracks"]}
    assert tracks["Rain"]["linked"] == {"theme_id": "rain-id", "theme_name": "Rain", "track": "Rain", "missing": True}
    assert tracks["Rain"]["group"] is None
    mix(stream, 0.5)  # still plays


def test_f_sync_entries_keeps_linked_entries(tmp_path):
    folder = tmp_path / "Camping"
    (folder / "Weather").mkdir(parents=True)
    (folder / "Crickets.mp3").write_bytes(b"x")
    metadata = m.meta.ThemeMetadata(
        id="c", name="Camping", groups={"Weather": {}, "Storms": {"mode": "ebb_flow"}},
        links={"Weather/Rain": {"theme": "r", "track": "Rain"}, "Storms/Hail": {"theme": "r", "track": "Hail"},
               "Drizzle": {"theme": "r", "track": "Drizzle"}, "a/b/c": {"theme": "r", "track": "x"}},
        tracks={"Crickets": {}, "Weather/Rain": {"muted": False, "volume": 0.5}, "Drizzle": {"volume": 0.3}, "Gone": {}},
        presets={"p": {"tracks": {"Weather/Rain": {"muted": False}, "Drizzle": {}, "Gone": {}}}})
    assert set(metadata.links) == {"Weather/Rain", "Storms/Hail", "Drizzle"}  # one level of group at most
    changes = m.meta.sync_entries_with_files(folder, metadata)
    assert metadata.tracks["Weather/Rain"].volume == 0.5  # kept although no such file is in the folder
    assert metadata.tracks["Drizzle"].volume == 0.3  # ungrouped too
    assert metadata.tracks["Storms/Hail"].muted is True  # a link without an entry gets one, muted
    assert "Gone" not in metadata.tracks
    assert metadata.presets["p"]["tracks"] == {"Weather/Rain": {"muted": False}, "Drizzle": {}}
    assert set(metadata.groups) == {"Weather", "Storms"}  # "Storms" has no folder but holds a link
    assert changes["removed"] == ["Gone"]
    # Saved and read back
    again = m.meta.ThemeMetadata.from_dict(json.loads(json.dumps(metadata.to_dict())))
    assert again.links == metadata.links


def test_g_export_is_self_contained(world):
    link_rain(world)
    link_rain(world, "Weather")
    theme_path = world.root / "Camping"
    metadata_doc, presets_doc = m.meta.theme_documents_for_export(theme_path)
    entries = m.intrusions.export_entries(theme_path, metadata_doc)
    source = world.root / "Rain" / "Rain.mp3"
    assert sorted(entries) == [("Rain.mp3", source), ("Weather/Rain.mp3", source)]
    assert "links" not in metadata_doc and metadata_doc["tracks"]["Weather/Rain"]["muted"] is True

    # The zip as the export endpoint writes it, imported elsewhere
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        for path in theme_path.rglob("*"):
            if path.is_file() and path.name not in ("metadata.json", "presets.json"):
                z.write(path, f"Camping/{path.relative_to(theme_path).as_posix()}")
        for relative, source in entries:
            z.write(source, f"Camping/{relative}")
        z.writestr("Camping/metadata.json", json.dumps(metadata_doc))
        z.writestr("Camping/presets.json", json.dumps(presets_doc))
    names = zipfile.ZipFile(io.BytesIO(buffer.getvalue())).namelist()
    assert not any(n.startswith("Rain/") for n in names)  # never another whole theme
    other = world.root.parent / "elsewhere"
    zipfile.ZipFile(io.BytesIO(buffer.getvalue())).extractall(other)
    imported = m.meta.load_theme_folder(other / "Camping")
    assert imported.links == {}
    assert m.intrusions.file_keys(other / "Camping") == ["Crickets", "Rain", "Weather/Rain"]
    assert m.meta.sync_entries_with_files(other / "Camping", imported) is None  # its settings match its files
    assert imported.tracks["Weather/Rain"].muted is True
    assert imported.tracks["Rain"].playback_mode == "unassigned"


def test_h_api_round_trip(world):
    api = world.api

    # Picker: the other theme's tracks, read-only
    listing = run(api.list_link_source_tracks("camping-id", "rain-id"))
    assert listing["source"] == {"id": "rain-id", "name": "Rain"}
    (track,) = listing["tracks"]
    assert track["id"] == "Rain" and track["group"] is None and track["display_name"] == "Rain"
    assert 29 < track["duration_seconds"] < 31
    assert track["preview_url"] == "/api/themes/rain-id/tracks/Rain/audio" and track["linked_as"] is None

    body = {"theme": "rain-id", "tracks": ["Rain"], "group": "Weather"}
    refused(api.add_links("camping-id", Body({**body, "group": "Nope"})), 404)
    refused(api.add_links("camping-id", Body({**body, "group": 5})), 400)
    refused(api.add_links("camping-id", Body({**body, "tracks": ["Hail"]})), 404)
    refused(api.add_links("camping-id", Body({**body, "theme": "camping-id", "tracks": ["Crickets"]})), 400)
    refused(api.add_links("camping-id", Body({**body, "tracks": []})), 400)
    refused(api.add_links("camping-id", Body({**body, "theme": "nope"})), 404)

    # Into a group of any mode
    (world.root / "Camping" / "Other").mkdir()
    run(api.update_group("camping-id", "Other", Body({"mode": "merry_go_round"})))
    assert link_rain(world, "Other")[0]["key"] == "Other/Rain"
    # Ungrouped: the same file again, another place; linked once per place
    assert link_rain(world)[0] == {"key": "Rain", "theme": "rain-id", "track": "Rain", "added": True}
    assert link_rain(world)[0]["added"] is False
    response = run(api.add_links("camping-id", Body({"theme": "rain-id", "tracks": ["Rain"], "group": None})))
    assert json.loads(response.body)["links"][0]["added"] is False  # null is ungrouped too
    assert run(api.list_link_source_tracks("camping-id", "rain-id"))["tracks"][0]["linked_as"] in ("Rain", "Other/Rain")

    tracks = {t["name"]: t for t in run(api.get_theme_tracks("camping-id"))["tracks"]}
    assert tracks["Rain"]["linked"] == {"theme_id": "rain-id", "theme_name": "Rain", "track": "Rain", "missing": False}
    assert tracks["Rain"]["group"] is None and tracks["Rain"]["playback_mode"] == "unassigned"
    assert tracks["Other/Rain"]["group"] == "Other" and tracks["Other/Rain"]["intrusion_group"] is False
    assert "linked" not in tracks["Crickets"]
    groups = {g["name"]: g for g in run(api.list_groups("camping-id"))["groups"]}
    assert groups["Other"]["tracks"] == ["Other/Rain"] and groups["Other"]["intrusion"] is False
    assert [link["key"] for link in groups["Other"]["links"]] == ["Other/Rain"]
    assert groups["Weather"]["links"] == []

    # The editor's preview serves the source file of a linked track
    audio = run(api.get_track_audio("camping-id", "Rain"))
    assert Path(audio.path) == world.root / "Rain" / "Rain.mp3"

    # Intrusion is no longer a group mode; a group holding links changes mode freely
    refused(api.update_group("camping-id", "Weather", Body({"mode": "intrusion"})), 400)
    run(api.update_group("camping-id", "Other", Body({"mode": "intermittent", "remove_links": True})))
    assert "Other/Rain" in metadata_on_disk(world, "Camping")["links"]

    # Renaming the group carries its links along
    run(api.rename_group_folder("camping-id", "Other", Body({"name": "Storms"})))
    saved = metadata_on_disk(world, "Camping")
    assert saved["links"] == {"Storms/Rain": {"theme": "rain-id", "track": "Rain"}, "Rain": {"theme": "rain-id", "track": "Rain"}}
    assert "Storms/Rain" in saved["tracks"] and saved["groups"]["Storms"] == {"mode": "intermittent"}

    # Remove: link, settings and preset entries go; no file is deleted
    with_preset(world, {"Rain": {"muted": False}, "Crickets": {"muted": True}})
    assert run(api.delete_link("camping-id", "Rain")) == {"removed": "Rain"}
    saved = metadata_on_disk(world, "Camping")
    assert set(saved["links"]) == {"Storms/Rain"} and "Rain" not in saved["tracks"]
    assert saved_presets(world)["p"]["tracks"] == {"Crickets": {"muted": True}}
    assert (world.root / "Rain" / "Rain.mp3").exists()
    assert all(i.name != "Rain" for i in theme(world, "Camping").instances)
    refused(api.delete_link("camping-id", "Rain"), 404)


def test_playback_mode_accepts_unassigned(world):
    api = world.api
    for mode in ("unassigned", "continuous", "sparse", "presence", "auto"):
        assert run(api.set_track_playback_mode("camping-id", "Crickets", Body({"playback_mode": mode}))) == {
            "status": "ok", "track": "Crickets", "playback_mode": mode}
        assert metadata_on_disk(world, "Camping")["tracks"]["Crickets"]["playback_mode"] == mode
    run(api.set_track_playback_mode("camping-id", "Crickets", Body({"playback_mode": "unassigned"})))
    crickets = next(i for i in theme(world, "Camping").instances if i.name == "Crickets")
    assert crickets.playback_mode == m.recording.PlaybackMode.UNASSIGNED
    assert level(mix(theme(world, "Camping").get_stream(), 2.0), CRICKETS_HZ) < 50  # not played
    assert "error" in run(api.set_track_playback_mode("camping-id", "Crickets", Body({"playback_mode": "loud"})))


# --- editor operations: move a link, delete its group, copy a link in -----------

def refused(coro, status):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        run(coro)
    assert error.value.status_code == status


def with_preset(world, tracks, groups=None):
    api = world.api
    metadata = api._theme_metadata_manager.get_metadata("camping-id")
    metadata.presets["p"] = {"name": "P", "tracks": tracks, **({"groups": groups} if groups else {})}
    api._theme_metadata_manager.save_metadata("camping-id", metadata)


def with_sequence(world, key, group=None):
    """A sequence over preset "p" whose advanced transitions and While playing layer name the track key."""
    from sonorium.core import sequences as seq
    folder = world.root / "Camping"
    layer = {"tracks": {key: {"volume": 0.5}}, "groups": {group: {"muted": False}} if group else {}}
    seq.save_sequences(folder, {"s": {"name": "S", "presets": ["p"], "at_end": "stop",
                                      "transitions": {"p": {"in": {key: {"styles": ["gusts"]}}, "out": {}}},
                                      "while": {"p": layer}}})


def saved_presets(world):
    return json.loads((world.root / "Camping" / "presets.json").read_text(encoding="utf-8"))["presets"]


def saved_sequence(world):
    return json.loads((world.root / "Camping" / "presets.json").read_text(encoding="utf-8"))["sequences"]["s"]


def test_moving_a_link_into_and_out_of_groups_only_rekeys_it(world):
    api = world.api
    (world.root / "Camping" / "Storms").mkdir()
    run(api.update_group("camping-id", "Storms", Body({"mode": "ebb_flow"})))
    link_rain(world)
    run(api.set_track_volume("camping-id", "Rain", Body({"volume": 0.4})))
    with_preset(world, {"Rain": {"muted": False, "volume": 0.9}})
    with_sequence(world, "Rain")
    stream = theme(world, "Camping").get_stream(m.recording.preset_track_overrides({"Rain": {"muted": False}}))
    assert "Rain" in stream._views

    # Into a group (the editor's drag and drop): only its key changes, everywhere
    assert run(api.move_track_to_group("camping-id", "Rain", Body({"group": "Storms"}))) == {"track": "Storms/Rain"}
    saved = metadata_on_disk(world, "Camping")
    assert saved["links"] == {"Storms/Rain": {"theme": "rain-id", "track": "Rain"}}
    assert "Rain" not in saved["tracks"] and saved["tracks"]["Storms/Rain"]["volume"] == 0.4
    assert saved_presets(world)["p"]["tracks"] == {"Storms/Rain": {"muted": False, "volume": 0.9}}
    sequence = saved_sequence(world)
    assert set(sequence["transitions"]["p"]["in"]) == {"Storms/Rain"}
    assert set(sequence["while"]["p"]["tracks"]) == {"Storms/Rain"}
    assert not (world.root / "Camping" / "Storms" / "Rain.mp3").exists()  # still a link, not a copy
    assert (world.root / "Rain" / "Rain.mp3").exists()
    tracks = {t["name"]: t for t in run(api.get_theme_tracks("camping-id"))["tracks"]}
    assert tracks["Storms/Rain"]["linked"]["theme_id"] == "rain-id" and tracks["Storms/Rain"]["group"] == "Storms"
    # A playing mix follows, and the file plays its group's way
    assert "Storms/Rain" in stream._views and "Rain" not in stream._views
    assert stream._views["Storms/Rain"].playback_mode == m.recording.PlaybackMode.PRESENCE

    # Into another group, created if missing (as for a file)
    assert run(api.move_track_to_group("camping-id", "Storms/Rain", Body({"group": "New"}))) == {"track": "New/Rain"}
    assert (world.root / "Camping" / "New").is_dir()
    # Moving it where it is changes nothing
    assert run(api.move_track_to_group("camping-id", "New/Rain", Body({"group": "New"}))) == {"track": "New/Rain"}
    refused(api.move_track_to_group("camping-id", "New/Rain", Body({"group": "a/b"})), 400)

    # Out of the group: ungrouped again, keeping everything
    assert run(api.move_track_to_group("camping-id", "New/Rain", Body({"group": None}))) == {"track": "Rain"}
    assert set(metadata_on_disk(world, "Camping")["links"]) == {"Rain"}
    assert saved_presets(world)["p"]["tracks"] == {"Rain": {"muted": False, "volume": 0.9}}
    assert set(saved_sequence(world)["transitions"]["p"]["in"]) == {"Rain"}

    # A clash gets " (2)": with another link, and with a file of the theme
    link_rain(world, "Storms")
    assert run(api.move_track_to_group("camping-id", "Rain", Body({"group": "Storms"}))) == {"track": "Storms/Rain (2)"}
    assert run(api.move_track_to_group("camping-id", "Storms/Rain", Body({"group": None}))) == {"track": "Rain"}
    import shutil
    shutil.copy(world.tones / "rain.mp3", world.root / "Camping" / "Storms" / "Rain.mp3")
    run(api.refresh_themes())
    assert run(api.move_track_to_group("camping-id", "Rain", Body({"group": "Storms"}))) == {"track": "Storms/Rain (3)"}
    assert set(metadata_on_disk(world, "Camping")["links"]) == {"Storms/Rain (2)", "Storms/Rain (3)"}


def test_a_file_moved_next_to_a_link_with_its_name_is_renamed(world):
    """A real file never lands on a linked file's key (moving it, deleting its group, uploading)."""
    import shutil
    api = world.api
    link_rain(world)
    shutil.copy(world.tones / "rain.mp3", world.root / "Camping" / "Weather" / "Rain.mp3")
    run(api.refresh_themes())
    assert run(api.move_track_to_group("camping-id", "Weather/Rain", Body({"group": None}))) == {"track": "Rain (2)"}
    assert (world.root / "Camping" / "Rain (2).mp3").exists()
    saved = metadata_on_disk(world, "Camping")
    assert saved["links"] == {"Rain": {"theme": "rain-id", "track": "Rain"}} and "Rain (2)" in saved["tracks"]

    from sonorium.core.theme_metadata import add_unassigned_track
    assert add_unassigned_track(world.root / "Camping", "Fresh") is True
    assert metadata_on_disk(world, "Camping")["tracks"]["Fresh"]["playback_mode"] == "unassigned"
    assert add_unassigned_track(world.root / "Camping", "Fresh") is False  # an entry is kept
    metadata = api._theme_metadata_manager.get_metadata("camping-id")
    assert add_unassigned_track(world.root / "Camping", "Fresh 2", metadata, api._theme_metadata_manager) is True
    assert metadata.tracks["Fresh 2"].playback_mode == "unassigned"
    assert metadata_on_disk(world, "Camping")["tracks"]["Fresh 2"]["playback_mode"] == "unassigned"


def test_deleting_a_group_ungroups_its_links(world):
    import shutil
    api = world.api
    shutil.copy(world.tones / "thunder.mp3", world.root / "Camping" / "Weather" / "Thunder.mp3")
    run(api.refresh_themes())
    link_rain(world)  # "Rain" is taken at the top level
    link_rain(world, "Weather")
    with_preset(world, {"Weather/Rain": {"muted": False}, "Weather/Thunder": {"volume": 0.2}}, {"Weather": {"volume": 0.5}})
    with_sequence(world, "Weather/Rain", "Weather")

    result = run(api.delete_group_folder("camping-id", "Weather"))
    assert result["tracks"] == {"Weather/Thunder": "Thunder", "Weather/Rain": "Rain (2)"}
    assert result["folder_removed"] is True
    saved = metadata_on_disk(world, "Camping")
    assert set(saved["links"]) == {"Rain", "Rain (2)"} and "Weather" not in saved["groups"]
    assert saved_presets(world)["p"]["tracks"] == {"Rain (2)": {"muted": False}, "Thunder": {"volume": 0.2}}
    assert "groups" not in saved_presets(world)["p"] or "Weather" not in saved_presets(world)["p"]["groups"]
    sequence = saved_sequence(world)
    assert set(sequence["transitions"]["p"]["in"]) == {"Rain (2)"}
    assert sequence["while"]["p"] == {"tracks": {"Rain (2)": {"volume": 0.5}}, "groups": {}}
    assert (world.root / "Camping" / "Thunder.mp3").exists() and (world.root / "Rain" / "Rain.mp3").exists()


def test_copying_a_link_into_the_theme(world):
    api = world.api
    (world.root / "Camping" / "Plain").mkdir()
    link_rain(world, "Weather")
    run(api.set_track_volume("camping-id", "Weather/Rain", Body({"volume": 0.4})))
    with_preset(world, {"Weather/Rain": {"muted": False, "volume": 0.9}, "Crickets": {"muted": True}})
    with_sequence(world, "Weather/Rain")
    stream = theme(world, "Camping").get_stream()
    assert "Weather/Rain" in stream._views
    source = world.root / "Rain" / "Rain.mp3"
    source_bytes = source.read_bytes()

    refused(api.copy_link_in("camping-id", "Crickets", Body({"group": None})), 404)  # not a link
    refused(api.copy_link_in("camping-id", "Weather/Rain", Body({"group": "Nope"})), 404)
    refused(api.copy_link_in("camping-id", "Weather/Rain", Body({})), 400)

    # Into the theme (no group)
    assert run(api.copy_link_in("camping-id", "Weather/Rain", Body({"group": None}))) == {"track": "Rain"}
    copied = world.root / "Camping" / "Rain.mp3"
    assert copied.read_bytes() == source_bytes and source.read_bytes() == source_bytes  # copied; source untouched
    saved = metadata_on_disk(world, "Camping")
    assert "links" not in saved and "Weather/Rain" not in saved["tracks"]
    assert saved["tracks"]["Rain"]["volume"] == 0.4 and saved["tracks"]["Rain"]["muted"] is True
    assert saved_presets(world)["p"]["tracks"] == {"Rain": {"muted": False, "volume": 0.9}, "Crickets": {"muted": True}}
    assert set(saved_sequence(world)["transitions"]["p"]["in"]) == {"Rain"}
    camping = theme(world, "Camping")
    rain = next(i for i in camping.instances if i.name == "Rain")
    assert rain.meta.path == copied and not getattr(rain.meta, "link", None)
    # A playing mix follows
    assert "Rain" in stream._views and "Weather/Rain" not in stream._views
    # The copy plays from this theme's folder
    signal = mix(camping.get_stream(m.recording.preset_track_overrides({"Rain": {"muted": False}, "Crickets": {"muted": True}})), 3.0)
    assert level(signal, RAIN_HZ) > 1000

    # Again, into the root: the name is taken, so "Rain (2)"; into a plain group: its folder
    link_rain(world, "Weather")
    assert run(api.copy_link_in("camping-id", "Weather/Rain", Body({"group": None}))) == {"track": "Rain (2)"}
    assert (world.root / "Camping" / "Rain (2).mp3").exists()
    link_rain(world, "Weather")
    assert run(api.copy_link_in("camping-id", "Weather/Rain", Body({"group": "Plain"}))) == {"track": "Plain/Rain"}
    assert (world.root / "Camping" / "Plain" / "Rain.mp3").exists()
    # An ungrouped link copied in where it is keeps its key (a file of the theme now)
    link_rain(world)
    assert metadata_on_disk(world, "Camping")["links"] == {"Rain (3)": {"theme": "rain-id", "track": "Rain"}}
    assert run(api.copy_link_in("camping-id", "Rain (3)", Body({"group": None}))) == {"track": "Rain (3)"}
    assert (world.root / "Camping" / "Rain (3).mp3").exists() and "links" not in metadata_on_disk(world, "Camping")

    # A missing source can't be copied
    link_rain(world, "Weather")
    source.unlink()
    run(api.refresh_themes())
    refused(api.copy_link_in("camping-id", "Weather/Rain", Body({"group": None})), 409)
    assert "Weather/Rain" in metadata_on_disk(world, "Camping")["links"]
