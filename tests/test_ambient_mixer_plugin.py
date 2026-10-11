"""Ambient Mixer importer: mode mapping, seamless_loop, and imports written as 2.0 themes (no network)."""

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

from playback_helpers import addon_modules

BUILTIN = Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium" / "plugins" / "builtin"


@pytest.fixture(scope="module")
def m():
    with addon_modules("sonorium.plugins.loader", "sonorium.plugins.theme_api",
                       "sonorium.core.theme_metadata") as mods:
        folder = BUILTIN / "ambient_mixer"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        mods.Plugin = mods.loader.load_plugin_class(folder, manifest)
        mods.am = sys.modules[mods.Plugin.__module__]
        yield mods


def channel(m, **kw):
    base = dict(channel_num=1, name="Rain", audio_id="11", url="http://x/rain.mp3")
    return m.am.AudioChannel(**{**base, **kw})


# --- mapping -----------------------------------------------------------------

def test_background_channel(m):
    s = m.am.channel_track_settings(channel(m, volume=40, crossfade=True))
    assert s == {"playback_mode": "continuous", "presence": 1.0, "volume": 0.4,
                 "seamless_loop": False, "exclusive": False, "muted": False}


def test_seamless_loop_is_the_opposite_of_crossfade(m):
    assert m.am.channel_track_settings(channel(m, crossfade=False))["seamless_loop"] is True
    assert m.am.channel_track_settings(channel(m, crossfade=True))["seamless_loop"] is False


def test_random_channel_is_intermittent_as_often_as_on_ambient_mixer(m):
    s = m.am.channel_track_settings(channel(m, is_random=True, random_counter=5, random_unit="1h", mute=True))
    # 5 plays an hour: a 12 minute gap; presence p waits 180 + 1620 * (1 - p) seconds
    assert s["playback_mode"] == "sparse" and s["presence"] == pytest.approx(0.667, abs=0.001)
    assert s["muted"] is True
    assert 180 + 1620 * (1 - s["presence"]) == pytest.approx(720, abs=1)


@pytest.mark.parametrize("counter, unit, presence", [
    (1, "1h", 0.0),      # once an hour: as rare as Sonorium goes
    (10, "10m", 1.0),    # every minute: as often as Sonorium goes
    (20, "1h", 1.0),     # every 3 minutes
    (2, "1h", 0.0),      # every 30 minutes
    (4, "1h", 0.556),    # every 15 minutes
    (1, "junk", 0.0),    # unreadable unit: an hour
    (0, "1h", 0.0),
])
def test_random_presence(m, counter, unit, presence):
    assert m.am.random_presence(counter, unit) == pytest.approx(presence, abs=0.001)


def test_never_auto(m):
    for kw in ({}, {"is_random": True}, {"crossfade": True}, {"mute": True}):
        assert m.am.channel_track_settings(channel(m, **kw))["playback_mode"] in ("continuous", "sparse")


def test_volume_is_kept_within_range(m):
    assert m.am.channel_track_settings(channel(m, volume=150))["volume"] == 1.0
    assert m.am.channel_track_settings(channel(m, volume=-5))["volume"] == 0.0


# --- import (fake client) ----------------------------------------------------------

XML = """<?xml version="1.0"?>
<audio_template>
  <title>Rainy Night</title>
  <channel1><name_audio>Rain</name_audio><id_audio>101</id_audio><url_audio>http://am/rain.mp3</url_audio>
    <volume>60</volume><random>false</random><crossfade>true</crossfade><mute>false</mute></channel1>
  <channel2><name_audio>Thunder</name_audio><id_audio>102</id_audio><url_audio>http://am/thunder.mp3</url_audio>
    <volume>80</volume><random>true</random><random_counter>4</random_counter><random_unit>1h</random_unit>
    <crossfade>false</crossfade><mute>false</mute></channel2>
  <channel3><name_audio>Wind</name_audio><id_audio>103</id_audio><url_audio>http://am/wind.mp3</url_audio>
    <volume>30</volume><random>false</random><crossfade>false</crossfade><mute>true</mute></channel3>
</audio_template>"""


class FakeResponse:
    def __init__(self, text="", content=b""):
        self.text, self.content = text, content

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **k):
        if "ambient-mixer.com/m/" in url:
            return FakeResponse(text="<script>AmbientMixer.setup(4242)</script>")
        if "xml.ambient-mixer.com" in url:
            return FakeResponse(text=XML)
        return FakeResponse(content=url.rsplit("/", 1)[-1].encode())


@pytest.fixture
def importer(m, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(AsyncClient=FakeClient))
    real_sleep = asyncio.sleep
    monkeypatch.setattr(m.am.asyncio, "sleep", lambda s: real_sleep(0))
    audio = tmp_path / "audio"
    audio.mkdir()
    plugin = m.Plugin(plugin_dir=BUILTIN / "ambient_mixer", settings={"hash_check_duplicates": True},
                      audio_path=audio)

    def run(**data):
        return asyncio.run(plugin.handle_action("import", {"url": "https://ambient-mixer.com/m/rainy", **data}))
    return types.SimpleNamespace(run=run, audio=audio, plugin=plugin)


def modes(doc):
    found = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            found += [v] if k == "playback_mode" else modes(v)
    return found


def test_import_new_theme(m, importer):
    result = importer.run()
    assert result["success"], result
    assert result["refresh_themes"] is True and result["data"]["is_new_theme"] is True
    folder = Path(result["data"]["theme_path"])
    assert folder.name == "Rainy_Night"
    metadata = m.theme_metadata.load_theme_folder(folder)
    assert metadata.problems == [] and metadata.id == result["data"]["theme_id"]
    assert metadata.attribution["template_id"] == "4242"
    assert set(metadata.tracks) == {"Rain_101", "Thunder_102", "Wind_103"}
    assert metadata.tracks["Rain_101"].playback_mode == "continuous"
    assert metadata.tracks["Rain_101"].seamless_loop is False      # crossfade on
    assert metadata.tracks["Wind_103"].seamless_loop is True       # crossfade off
    assert metadata.tracks["Thunder_102"].playback_mode == "sparse"
    assert metadata.tracks["Wind_103"].muted is True
    assert (folder / "Rain_101.mp3").read_bytes() == b"rain.mp3"
    for name in ("metadata.json", "presets.json"):
        doc = json.loads((folder / name).read_text(encoding="utf-8"))
        assert "auto" not in modes(doc)
    assert json.loads((folder / "metadata.json").read_text(encoding="utf-8"))["spec_version"] == 2
    [preset] = metadata.presets.values()
    assert preset["name"] == "Rainy Night" and preset["is_default"] is True
    assert preset["tracks"]["Thunder_102"]["playback_mode"] == "sparse"
    assert (folder / "MANIFEST.json").exists() and (folder / "ATTRIBUTION.md").exists()
    assert not [p for p in folder.parent.iterdir() if p.name.startswith(".")]


def test_import_into_existing_theme_by_id(m, importer):
    first = importer.run()
    theme_id, folder = first["data"]["theme_id"], Path(first["data"]["theme_path"])
    (folder / "Fire.mp3").write_bytes(b"fire")
    attribution_before = (folder / "ATTRIBUTION.md").read_bytes()

    result = importer.run(existing_theme=theme_id, preset_name="Second")
    assert result["success"], result
    assert result["data"]["is_new_theme"] is False and result["data"]["tracks_existing"] == 3
    metadata = m.theme_metadata.load_theme_folder(folder)
    assert sorted(p.name for p in folder.iterdir() if p.suffix == ".mp3") == [
        "Fire.mp3", "Rain_101.mp3", "Thunder_102.mp3", "Wind_103.mp3"]  # files reused, not copied again
    presets = {p["name"]: p for p in metadata.presets.values()}
    assert set(presets) == {"Rainy Night", "Second"}
    assert presets["Second"]["is_default"] is False
    assert presets["Second"]["tracks"]["Fire"]["muted"] is True   # the theme's other track
    assert presets["Second"]["tracks"]["Rain_101"]["muted"] is False
    assert (folder / "ATTRIBUTION.md").read_bytes() == attribution_before


@pytest.mark.parametrize("value", ["../outside", "C:/Windows", "/etc", "nope"])
def test_existing_theme_must_be_a_listed_theme(m, importer, tmp_path, value):
    (tmp_path / "outside").mkdir()
    result = importer.run(existing_theme=value)
    assert result["success"] is False and "not found" in result["message"]
    assert list(importer.audio.iterdir()) == []


def test_ui_lists_themes_by_id(m, importer):
    first = importer.run()
    options = importer.plugin.get_ui_schema()["fields"][1]["options"]
    assert options[1] == {"value": first["data"]["theme_id"], "label": "Rainy Night"}
