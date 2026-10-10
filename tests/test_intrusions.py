"""
Intrusion groups and linked files, end to end: two real themes on disk ("Camping"
with crickets, "Rain" with rain), the real metadata manager, the real theme
refresh and the real mixer, with generated tone mp3s told apart by FFT.
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
    """Camping (crickets + an empty "Weather" intrusion group) and Rain (rain), loaded by the real API."""
    import shutil
    root = tmp_path / "themes"
    write_theme(root / "Camping", "camping-id", groups={"Weather": {"mode": "intrusion"}})
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


def link_rain(world):
    response = run(world.api.add_links("camping-id", Body({"theme": "rain-id", "tracks": ["Rain"], "group": "Weather"})))
    assert response.status_code == 201
    return json.loads(response.body)["links"]


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

def test_a_b_linked_file_plays_from_the_source_folder_and_starts_muted(world):
    links = link_rain(world)
    assert links == [{"key": "Weather/Rain", "theme": "rain-id", "track": "Rain", "added": True}]

    camping = theme(world, "Camping")
    linked = next(i for i in camping.instances if i.name == "Weather/Rain")
    assert linked.meta.path == world.root / "Rain" / "Rain.mp3"  # not copied
    assert not (world.root / "Camping" / "Weather" / "Rain.mp3").exists()
    assert linked.meta.group == "Weather"
    # (b) added muted, with the source theme's other settings
    assert linked.is_enabled is False
    saved = metadata_on_disk(world, "Camping")
    assert saved["links"] == {"Weather/Rain": {"theme": "rain-id", "track": "Rain"}}
    assert saved["tracks"]["Weather/Rain"]["muted"] is True
    assert saved["tracks"]["Weather/Rain"]["volume"] == 0.8

    # The theme's own sound is unchanged: crickets only
    signal = mix(camping.get_stream(), 3.0)
    assert level(signal, CRICKETS_HZ) > 1000 and level(signal, RAIN_HZ) < 50

    # (a) a preset turning it on: the rain plays through Camping's mix, from the Rain folder
    overrides = m.recording.preset_track_overrides({"Weather/Rain": {"muted": False}, "Crickets": {"muted": True}})
    signal = mix(camping.get_stream(overrides), 3.0)
    assert level(signal, RAIN_HZ) > 1000 and level(signal, CRICKETS_HZ) < 50


def test_c_switching_presets_on_a_playing_mix(world):
    link_rain(world)
    camping = theme(world, "Camping")
    overrides = {}
    stream = camping.get_stream(overrides)
    chunks = stream.iter_chunks()

    def pull(seconds):
        return np.concatenate([next(chunks).reshape(-1) for _ in range(int(seconds * RATE / CHUNK))]).astype(np.float64)

    before = pull(2.0)
    assert level(before, CRICKETS_HZ) > 1000 and level(before, RAIN_HZ) < 50
    # The rain preset (linked track on, crickets muted) applied in place, as a channel does
    overrides.update(m.recording.preset_track_overrides({"Weather/Rain": {"muted": False}, "Crickets": {"muted": True}}))
    during = pull(4.0)  # after the 3 s on/off crossfade
    assert level(during, RAIN_HZ) > 1000 and level(during, CRICKETS_HZ) < 50
    # Back to the theme's own settings
    overrides.clear()
    after = pull(4.0)
    assert level(after, CRICKETS_HZ) > 1000 and level(after, RAIN_HZ) < 50


def test_intrusion_group_tracks_play_as_their_own_settings_say(world):
    """No turn-taking: two tracks of an intrusion group sound at once, from the first second."""
    import shutil
    shutil.copy(world.tones / "thunder.mp3", world.root / "Camping" / "Weather" / "Thunder.mp3")
    run(world.api.refresh_themes())
    link_rain(world)
    camping = theme(world, "Camping")
    overrides = m.recording.preset_track_overrides({"Weather/Rain": {"muted": False}, "Weather/Thunder": {"muted": False},
                                                    "Crickets": {"muted": True}})
    stream = camping.get_stream(overrides)
    views = [stream._views[name] for name in ("Weather/Rain", "Weather/Thunder")]
    assert all(v.exclusion_group is None for v in views)  # no coordinator, no 60 s start delay
    assert all(v.playback_mode == m.recording.PlaybackMode.AUTO for v in views)  # its own mode, not forced
    assert not stream.exclusion_coordinators
    signal = mix(stream, 3.0)
    assert level(signal, RAIN_HZ) > 500 and level(signal, THUNDER_HZ) > 500

    # The group's masters still scale its tracks: muting the group silences both
    overrides.update(m.recording.preset_group_overrides({"Weather": {"muted": True}}))
    assert not any(v.is_enabled for v in views)

    # Back to Intermittent (a playing mix follows): the group takes turns again
    camping.groups = {"Weather": {"mode": "intermittent"}}
    next(stream.iter_chunks())
    assert stream._views["Weather/Rain"].exclusion_group == "Weather"


def test_d_has_intrusion_flag_on_presets(world):
    link_rain(world)
    api = world.api
    metadata = api._theme_metadata_manager.get_metadata("camping-id")
    metadata.presets.update({
        "rain": {"name": "Rain", "tracks": {"Crickets": {"muted": True}, "Weather/Rain": {"muted": False}}},
        "quiet": {"name": "Quiet", "tracks": {"Crickets": {"muted": False}}},
        "group_off": {"name": "Group off", "tracks": {"Weather/Rain": {"muted": False}},
                      "groups": {"Weather": {"muted": True}}},
    })
    api._theme_metadata_manager.save_metadata("camping-id", metadata)
    listed = run(api.list_presets("camping-id"))
    flags = {p["id"]: p["has_intrusion"] for p in listed["presets"]}
    assert flags == {"rain": True, "quiet": False, "group_off": False}
    assert listed["default_has_intrusion"] is False  # linked files start muted

    # A preset saved from the editor keeps the linked track (muted here)
    run(api.create_preset("camping-id", Body({"name": "Snapshot"})))
    snapshot = api._theme_metadata_manager.get_metadata("camping-id").presets["snapshot"]
    assert snapshot["tracks"]["Weather/Rain"]["muted"] is True


def test_e_missing_source_is_skipped_and_the_link_kept(world):
    link_rain(world)
    api = world.api
    camping = theme(world, "Camping")
    stream = camping.get_stream(m.recording.preset_track_overrides({"Weather/Rain": {"muted": False}}))
    assert "Weather/Rain" in stream._views

    # The source theme's folder is renamed: found again by its id
    (world.root / "Rain").rename(world.root / "Rain storm")
    run(api.refresh_themes())
    assert any(i.name == "Weather/Rain" for i in theme(world, "Camping").instances)

    # The source file is removed: the rescan updates Camping, and the playing mix lets it go
    (world.root / "Rain storm" / "Rain.mp3").unlink()
    run(api.refresh_themes())
    camping = theme(world, "Camping")
    assert all(i.name != "Weather/Rain" for i in camping.instances)
    assert "Weather/Rain" not in stream._views
    saved = metadata_on_disk(world, "Camping")
    assert "Weather/Rain" in saved["links"] and "Weather/Rain" in saved["tracks"]

    tracks = {t["name"]: t for t in run(api.get_theme_tracks("camping-id"))["tracks"]}
    assert tracks["Weather/Rain"]["linked"] == {"theme_id": "rain-id", "theme_name": "Rain", "track": "Rain", "missing": True}
    groups = {g["name"]: g for g in run(api.list_groups("camping-id"))["groups"]}
    assert groups["Weather"]["links"][0]["missing"] is True
    mix(stream, 0.5)  # still plays


def test_f_sync_entries_keeps_linked_entries(tmp_path):
    folder = tmp_path / "Camping"
    (folder / "Weather").mkdir(parents=True)
    (folder / "Crickets.mp3").write_bytes(b"x")
    metadata = m.meta.ThemeMetadata(
        id="c", name="Camping", groups={"Weather": {"mode": "intrusion"}, "Storms": {"mode": "intrusion"}},
        links={"Weather/Rain": {"theme": "r", "track": "Rain"}, "Storms/Hail": {"theme": "r", "track": "Hail"}},
        tracks={"Crickets": {}, "Weather/Rain": {"muted": False, "volume": 0.5}, "Gone": {}},
        presets={"p": {"tracks": {"Weather/Rain": {"muted": False}, "Gone": {}}}})
    changes = m.meta.sync_entries_with_files(folder, metadata)
    assert metadata.tracks["Weather/Rain"].volume == 0.5  # kept although no such file is in the folder
    assert metadata.tracks["Storms/Hail"].muted is True  # a link without an entry gets one, muted
    assert "Gone" not in metadata.tracks
    assert metadata.presets["p"]["tracks"] == {"Weather/Rain": {"muted": False}}
    assert set(metadata.groups) == {"Weather", "Storms"}  # "Storms" has no folder but holds a link
    assert set(metadata.links) == {"Weather/Rain", "Storms/Hail"}
    assert changes["removed"] == ["Gone"]
    # Saved and read back
    again = m.meta.ThemeMetadata.from_dict(json.loads(json.dumps(metadata.to_dict())))
    assert again.links == metadata.links


def test_g_export_is_self_contained(world):
    link_rain(world)
    theme_path = world.root / "Camping"
    metadata_doc, presets_doc = m.meta.theme_documents_for_export(theme_path)
    entries = m.intrusions.export_entries(theme_path, metadata_doc)
    assert entries == [("Weather/Rain.mp3", world.root / "Rain" / "Rain.mp3")]
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
    assert imported.groups["Weather"] == {"mode": "intrusion"}
    assert m.intrusions.file_keys(other / "Camping") == ["Crickets", "Weather/Rain"]
    assert m.meta.sync_entries_with_files(other / "Camping", imported) is None  # its settings match its files
    assert imported.tracks["Weather/Rain"].muted is True


def test_h_api_round_trip(world):
    from fastapi import HTTPException
    api = world.api

    # Picker: the other theme's tracks, read-only
    listing = run(api.list_link_source_tracks("camping-id", "rain-id"))
    assert listing["source"] == {"id": "rain-id", "name": "Rain"}
    (track,) = listing["tracks"]
    assert track["id"] == "Rain" and track["group"] is None and track["display_name"] == "Rain"
    assert 29 < track["duration_seconds"] < 31
    assert track["preview_url"] == "/api/themes/rain-id/tracks/Rain/audio" and track["linked_as"] is None

    def refused(coro, status):
        with pytest.raises(HTTPException) as error:
            run(coro)
        assert error.value.status_code == status

    body = {"theme": "rain-id", "tracks": ["Rain"], "group": "Weather"}
    refused(api.add_links("camping-id", Body({**body, "group": "Nope"})), 404)
    refused(api.add_links("camping-id", Body({**body, "tracks": ["Hail"]})), 404)
    refused(api.add_links("camping-id", Body({**body, "theme": "camping-id", "tracks": ["Crickets"]})), 400)
    refused(api.add_links("camping-id", Body({**body, "tracks": []})), 400)
    refused(api.add_links("camping-id", Body({**body, "theme": "nope"})), 404)
    (world.root / "Camping" / "Other").mkdir()
    refused(api.add_links("camping-id", Body({**body, "group": "Other"})), 400)  # not in Intrusion mode

    assert link_rain(world)[0]["added"] is True
    assert link_rain(world)[0]["added"] is False  # linked once only
    assert run(api.list_link_source_tracks("camping-id", "rain-id"))["tracks"][0]["linked_as"] == "Weather/Rain"

    tracks = {t["name"]: t for t in run(api.get_theme_tracks("camping-id"))["tracks"]}
    assert tracks["Weather/Rain"]["linked"] == {"theme_id": "rain-id", "theme_name": "Rain", "track": "Rain", "missing": False}
    assert tracks["Weather/Rain"]["group"] == "Weather" and tracks["Weather/Rain"]["intrusion_group"] is True
    assert "linked" not in tracks["Crickets"]
    groups = {g["name"]: g for g in run(api.list_groups("camping-id"))["groups"]}
    assert groups["Weather"]["intrusion"] is True and groups["Weather"]["tracks"] == ["Weather/Rain"]
    assert groups["Weather"]["settings"] == {"mode": "intrusion"}

    # The editor's preview serves the source file of a linked track
    audio = run(api.get_track_audio("camping-id", "Weather/Rain"))
    assert Path(audio.path) == world.root / "Rain" / "Rain.mp3"

    # A group holding links can't leave Intrusion mode
    refused(api.update_group("camping-id", "Weather", Body({"mode": "intermittent"})), 409)

    # Renaming the group carries its links along
    run(api.rename_group_folder("camping-id", "Weather", Body({"name": "Storms"})))
    saved = metadata_on_disk(world, "Camping")
    assert saved["links"] == {"Storms/Rain": {"theme": "rain-id", "track": "Rain"}}
    assert "Storms/Rain" in saved["tracks"] and saved["groups"]["Storms"] == {"mode": "intrusion"}

    # Remove: link, settings and preset entries go; no file is deleted
    metadata = api._theme_metadata_manager.get_metadata("camping-id")
    metadata.presets["p"] = {"name": "P", "tracks": {"Storms/Rain": {"muted": False}, "Crickets": {"muted": True}}}
    api._theme_metadata_manager.save_metadata("camping-id", metadata)
    assert run(api.delete_link("camping-id", "Storms/Rain")) == {"removed": "Storms/Rain"}
    saved = metadata_on_disk(world, "Camping")
    assert "links" not in saved and "Storms/Rain" not in saved["tracks"]
    presets = json.loads((world.root / "Camping" / "presets.json").read_text(encoding="utf-8"))["presets"]
    assert presets["p"]["tracks"] == {"Crickets": {"muted": True}}
    assert (world.root / "Rain" / "Rain.mp3").exists()
    assert all(i.name != "Storms/Rain" for i in theme(world, "Camping").instances)
    refused(api.delete_link("camping-id", "Storms/Rain"), 404)

    # Without links it may change mode again; making a group Intrusion keeps (or makes) its folder
    run(api.update_group("camping-id", "Storms", Body({"mode": "intermittent"})))
    run(api.update_group("camping-id", "Other", Body({"mode": "intrusion"})))
    assert (world.root / "Camping" / "Other").is_dir()
