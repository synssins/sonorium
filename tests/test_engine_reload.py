"""
Engine.reload_theme(): changes saved to a theme's files while it plays
(metadata.json, presets.json, here with core/theme_edit.py) reach the running
mix, as a server channel follows the Theme Editor, and the preset or sequence
keeps playing.

The theme is generated on the spot (tests/engine_theme.py). Only Drone (a
looping sine) is left on, so its level is the same whatever the random parts do.
"""

import json
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "sonorium_addon"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from engine_theme import FAST_VALUES, RATE, make_theme  # noqa: E402


def _ours(name):
    return name == "sonorium" or name.startswith("sonorium.")


@pytest.fixture(autouse=True)
def _real_sonorium():
    """The add-on's real package for the test, and whatever was imported before put back after."""
    saved_modules = {name: module for name, module in sys.modules.items() if _ours(name)}
    saved_path = list(sys.path)
    for name in saved_modules:
        del sys.modules[name]
    sys.path.insert(0, str(ADDON))
    try:
        yield
    finally:
        for name in [n for n in sys.modules if _ours(n)]:
            del sys.modules[name]
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


@pytest.fixture
def theme(tmp_path):
    """The engine test theme with only Drone on (its own settings and in every preset)."""
    folder = make_theme(tmp_path)
    meta_path = folder / "metadata.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    for key, settings in metadata["tracks"].items():
        settings["muted"] = key != "Drone"
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    presets_path = folder / "presets.json"
    presets = json.loads(presets_path.read_text(encoding="utf-8"))
    for preset in presets["presets"].values():
        preset["tracks"] = {"Drone": {"volume": 0.8}}
    presets_path.write_text(json.dumps(presets, indent=2), encoding="utf-8")
    return folder


def rms(pcm) -> float:
    return float(np.sqrt(np.mean(pcm.astype("float64") ** 2)))


def engine_on(folder, **kwargs):
    from sonorium.engine import Engine, PcmFileDecoder
    return Engine(folder, playback_values=FAST_VALUES, seed=5, decoder=PcmFileDecoder(), **kwargs)


def level_after(folder, seconds=2.0, **kwargs) -> float:
    """Drone's level in a fresh engine on the files as they are now."""
    with engine_on(folder, **kwargs) as fresh:
        fresh.render(int(seconds * RATE))
        return rms(fresh.render(RATE // 2))


def test_a_track_volume_saved_while_playing_reaches_the_mix(theme):
    from sonorium.core import theme_edit
    with engine_on(theme, preset_id="") as eng:
        eng.render(2 * RATE)
        before = rms(eng.render(RATE // 2))
        theme_edit.set_track(theme, "Drone", volume=0.2)
        eng.reload_theme()
        eng.render(RATE // 2)  # the volume eases over a quarter second
        after = rms(eng.render(RATE // 2))
        assert eng.preset_id is None
        assert next(t for t in eng.mix.theme.tracks if t.name == "Drone").volume == 0.2
    assert after < 0.6 * before
    assert after == pytest.approx(level_after(theme, preset_id=""), rel=0.05)


def test_the_playing_preset_glides_to_its_saved_values(theme):
    from sonorium.core import theme_edit
    with engine_on(theme, preset_id="calm") as eng:
        eng.render(2 * RATE)
        before = rms(eng.render(RATE // 2))
        presets = json.loads((theme / "presets.json").read_text(encoding="utf-8"))
        presets["presets"]["calm"]["tracks"]["Drone"]["volume"] = 0.1
        (theme / "presets.json").write_text(json.dumps(presets), encoding="utf-8")
        eng.reload_theme(glide_seconds=1.0)
        mid = rms(eng.render(RATE // 4))
        eng.render(RATE)  # the glide is done
        after = rms(eng.render(RATE // 2))
        assert eng.preset_id == "calm"
    assert after < mid < before
    assert after == pytest.approx(level_after(theme, preset_id="calm"), rel=0.05)
    assert theme_edit.get_preset(theme, "calm")["tracks"]["Drone"]["volume"] == 0.1


def test_a_running_sequence_keeps_running(theme):
    from sonorium.core import theme_edit
    with engine_on(theme, sequence_id="walk") as eng:
        eng.render(RATE)
        theme_edit.set_group(theme, "Birds", volume=0.5)
        eng.reload_theme()
        assert eng.sequence_id == "walk"
        assert eng.mix.theme.groups["Birds"]["volume"] == 0.5
        seen = set()
        for _ in range(12):
            eng.render(RATE // 2)
            seen.add(eng.preset_id)
        assert eng.sequence_id == "walk" and seen == {"calm", "busy", "quiet"}  # still stepping


def test_a_deleted_preset_keeps_playing_its_values(theme):
    from sonorium.core import theme_edit
    with engine_on(theme, preset_id="calm") as eng:
        eng.render(2 * RATE)
        before = rms(eng.render(RATE // 2))
        theme_edit.delete_preset(theme, "calm")
        eng.reload_theme()
        eng.render(RATE)
        assert eng.preset_id == "calm" and "calm" not in eng.presets
        assert rms(eng.render(RATE // 2)) == pytest.approx(before, rel=0.05)
        eng.set_preset("quiet")
        assert eng.preset_id == "quiet"


def test_reload_on_a_closed_engine_raises(theme):
    eng = engine_on(theme)
    eng.close()
    with pytest.raises(RuntimeError):
        eng.reload_theme()
