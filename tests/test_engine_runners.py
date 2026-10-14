"""
The engine's sequences, playlists and theme changes (sonorium/engine/runner.py,
the server's core/playback_runner.py driven by the engine's audio clock).
"""
import json
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "sonorium_addon"
TESTS = ROOT / "tests"
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from engine_theme import FAST_VALUES, RATE, make_second_theme, make_theme  # noqa: E402

FIRST, SECOND = "engine-test-theme", "engine-second-theme"


def _ours(name):
    return name == "sonorium" or name.startswith("sonorium.")


@pytest.fixture(autouse=True)
def _real_sonorium():
    """The add-on's real package for the test, and whatever was imported before put back after (other tests stub it)."""
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
def themes(tmp_path):
    return make_theme(tmp_path), make_second_theme(tmp_path)


def _engine(theme_dir, seed=1, **kwargs):
    import sonorium.engine as engine
    return engine.Engine(theme_dir, playback_values=FAST_VALUES, seed=seed, decoder=engine.PcmFileDecoder(), **kwargs)


def _watch(e, seconds, every=0.1):
    """status() every `every` seconds of audio for `seconds`."""
    out = []
    for _ in range(int(round(seconds / every))):
        e.render(int(every * RATE))
        out.append(e.status())
    return out


def _changes(values):
    """The values with repeats in a row dropped."""
    out = []
    for value in values:
        if not out or out[-1] != value:
            out.append(value)
    return out


def _playlist(**fields):
    playlist = {"id": "evening", "name": "Evening", "order": "in_order", "repeat": True, "play_min": 2, "play_max": 2,
                "fit_sequences": False, "entries": [
                    {"theme_id": FIRST, "preset_id": "calm", "sequence_id": None},
                    {"theme_id": SECOND, "preset_id": "soft", "sequence_id": None}]}
    playlist.update(fields)
    return playlist


# --- sequences ---

def test_sequence_steps_in_order_and_restarts(themes):
    e = _engine(themes[0])
    e.start_sequence("walk")
    seen = _watch(e, 25)
    steps = _changes([(s["preset_id"], s["sequence"]["step"]) for s in seen])
    assert steps[:7] == [("calm", 1), ("busy", 2), ("quiet", 3), ("calm", 1), ("busy", 2), ("quiet", 3), ("calm", 1)]
    assert all(s["sequence"]["steps"] == 3 and s["sequence"]["at_end"] == "restart" for s in seen)
    e.close()


def test_sequence_reverses_at_the_end(themes):
    e = _engine(themes[0], sequence_id="bounce")
    assert e.preset_id == "calm" and e.sequence_id == "bounce"
    seen = _watch(e, 7)
    assert _changes([s["preset_id"] for s in seen])[:6] == ["calm", "busy", "quiet", "busy", "calm", "busy"]
    e.close()


def test_sequence_stops_on_its_last_preset(themes):
    e = _engine(themes[0], sequence_id="once")
    seen = _watch(e, 5)
    assert _changes([s["preset_id"] for s in seen]) == ["calm", "busy"]
    assert seen[-1]["sequence"] is None and e.sequence_id is None
    assert any("Stopped: Once" == r["text"] for r in seen[-1]["recent"])
    e.close()


def test_hold_times_come_from_the_sequence(themes):
    e = _engine(themes[0], sequence_id="bounce")  # hold 1 s, ramp 0
    seen = _watch(e, 6, every=1024 / RATE)
    starts = [i for i in range(1, len(seen)) if seen[i]["preset_id"] != seen[i - 1]["preset_id"]]
    gaps = [(b - a) * 1024 / RATE for a, b in zip(starts, starts[1:])]
    assert gaps and all(abs(g - 1.0) < 0.05 for g in gaps), gaps
    left = seen[starts[0] + 2]["sequence"]["seconds_left"]
    assert 0.8 < left < 1.0
    e.close()


def test_while_playing_values_are_laid_over_the_step(themes):
    e = _engine(themes[0])
    e.start_sequence("walk")
    seen = set()
    for _ in range(48):
        s = _watch(e, 0.25, every=0.25)[0]
        if e._channel.is_gliding:
            continue
        drone = e._channel._track_overrides.get("Drone", {})
        if s["preset_id"] == "busy":
            assert drone.get("volume") == 0.05  # the step's While playing value, not the preset's
        elif s["preset_id"] == "calm":
            assert drone.get("volume") == 0.3
        seen.add(s["preset_id"])
    assert {"calm", "busy"} <= seen
    e.close()


def test_coming_in_transitions_are_planned_before_their_step(themes):
    e = _engine(themes[0])
    planned = []
    schedule = e._channel.schedule_transitions
    e._channel.schedule_transitions = lambda plans: (planned.append([dict(p) for p in plans]), schedule(plans))
    e.start_sequence("walk")
    _watch(e, 8)
    wind = [p for plans in planned for p in plans if p.get("key", p.get("track")) == "Wind" or "Wind" in str(p)]
    assert wind, planned
    assert any(p.get("pre_fade_used", 0) > 0 for p in wind)
    e.close()


def test_next_and_previous_step_through_a_sequence(themes):
    e = _engine(themes[0], sequence_id="bounce")
    e.render(RATE // 2)
    assert e.next() == "sequence" and e.preset_id == "busy"
    assert e.next() == "sequence" and e.preset_id == "quiet"
    assert e.previous() == "sequence" and e.preset_id == "busy"
    assert e.previous() == "sequence" and e.preset_id == "calm"
    assert e.previous() == "sequence" and e.preset_id == "calm"  # the first step starts again
    assert e.status()["sequence"]["step"] == 1
    e.stop_sequence()
    assert e.next() is None and e.previous() is None
    e.close()


def test_picking_a_preset_by_hand_stops_the_sequence(themes):
    e = _engine(themes[0], sequence_id="walk")
    e.render(RATE)
    e.set_preset("quiet")
    assert e.sequence_id is None and e.status()["sequence"] is None
    e.close()


def test_unknown_or_empty_sequences_raise(themes):
    e = _engine(themes[0])
    with pytest.raises(LookupError):
        e.start_sequence("nope")
    e.close()


# --- playlists ---

def test_playlist_moves_through_its_entries_with_a_crossfade(themes):
    e = _engine(themes[0], themes_root=themes[0].parent)
    e.start_playlist(_playlist())
    seen = _watch(e, 9, every=0.25)
    order = _changes([(s["theme_id"], s["preset_id"], s["playlist"]["position"]) for s in seen])
    assert order[:4] == [(FIRST, "calm", 0), (SECOND, "soft", 1), (FIRST, "calm", 0), (SECOND, "soft", 1)]
    changed = [i for i in range(1, len(seen)) if seen[i]["theme_id"] != seen[i - 1]["theme_id"]]
    assert changed and seen[changed[0]]["crossfading"]
    assert seen[0]["playlist"]["name"] == "Evening" and seen[0]["playlist"]["entries"] == 2
    assert seen[0]["playlist"]["entry"] == {"theme_id": FIRST, "preset_id": "calm", "sequence_id": None}
    assert 0 < seen[0]["playlist"]["seconds_left"] <= 2
    json.dumps(seen)  # the whole status is JSON
    e.close()


def test_playlist_entry_with_a_sequence_plays_one_pass(themes):
    playlist = _playlist(entries=[{"theme_id": FIRST, "preset_id": None, "sequence_id": "bounce"},
                                  {"theme_id": SECOND, "preset_id": "soft", "sequence_id": None}])
    e = _engine(themes[0])
    e.start_playlist(playlist)
    seen = _watch(e, 6, every=0.1)
    second = next(i for i, s in enumerate(seen) if s["theme_id"] == SECOND)
    first = seen[:second]
    assert _changes([s["preset_id"] for s in first]) == ["calm", "busy", "quiet"]  # one pass, then the next entry
    assert first[0]["sequence"]["single_pass"] is True
    assert 2.9 <= second * 0.1 <= 3.2  # three holds of 1 s, no ramp
    assert seen[second]["playlist"]["position"] == 1 and seen[second]["sequence"] is None
    e.close()


def test_playlist_next_previous_and_missing_themes(themes):
    playlist = _playlist(play_min=60, play_max=60, entries=[
        {"theme_id": FIRST, "preset_id": "calm", "sequence_id": None},
        {"theme_id": "not-downloaded", "preset_id": "x", "sequence_id": None},
        {"theme_id": SECOND, "preset_id": None, "sequence_id": None}])
    e = _engine(themes[0])
    e.start_playlist(playlist)
    e.render(RATE)
    assert e.next() == "playlist" and e.theme_id == SECOND  # the missing theme is skipped
    assert e.status()["playlist"]["position"] == 2
    assert e.previous() == "playlist"  # back over the missing one
    assert e.status()["playlist"]["position"] == 0 and e.theme_id == FIRST
    assert e.previous() == "playlist" and e.status()["playlist"]["position"] == 2  # repeats: wraps to the last
    e.close()


def test_a_playlist_that_does_not_repeat_ends_and_fades_out(themes):
    e = _engine(themes[0], crossfade=1.0)
    e.start_playlist(_playlist(repeat=False))
    seen = _watch(e, 6, every=0.5)
    assert seen[-1]["playing"] is False and seen[-1]["ended"] == "ended"
    assert not np.any(e.render(RATE))  # silent after the fade
    e.start_playlist(_playlist())  # playing again fades back in
    e.render(2 * RATE)
    assert e.status()["playing"] and np.abs(e.render(RATE).astype(float)).mean() > 100
    e.close()


def test_stop_playlist_keeps_the_theme(themes):
    e = _engine(themes[0])
    e.start_playlist(_playlist(play_min=60, play_max=60))
    e.render(RATE)
    assert e.stop_playlist() is True and e.status()["playlist"] is None
    assert e.theme_id == FIRST and e.preset_id == "calm"
    e.close()


def test_bad_playlists_raise(themes):
    e = _engine(themes[0])
    with pytest.raises(ValueError):
        e.start_playlist(_playlist(entries=[]))
    with pytest.raises(ValueError):
        e.start_playlist(_playlist(entries=[{"theme_id": FIRST, "preset_id": "nope", "sequence_id": None}]))
    with pytest.raises(ValueError):  # nothing in it can be played
        e.start_playlist(_playlist(entries=[{"theme_id": "gone", "preset_id": None, "sequence_id": None}]))
    e.close()


# --- theme changes ---

def _block_rms(pcm):
    blocks = pcm[: len(pcm) // 1024 * 1024].astype(np.float64).reshape(-1, 1024)
    return np.sqrt((blocks ** 2).mean(axis=1))


def test_theme_change_crossfades_without_a_gap(themes):
    e = _engine(themes[0], preset_id="", crossfade=3.0)
    before = e.render(2 * RATE)
    e.set_theme(themes[1], preset_id="")
    assert e.theme_id == SECOND and e.status()["crossfading"]
    during = e.render(4 * RATE)
    after = e.render(RATE)
    floor = min(_block_rms(before).min(), _block_rms(after).min())
    assert _block_rms(during).min() > 0.3 * floor  # equal-power: no dip, no silence
    assert not e.status()["crossfading"]
    e.close()


def test_set_theme_with_a_sequence_and_back(themes):
    e = _engine(themes[0], sequence_id="walk")
    e.start_playlist(_playlist(play_min=60, play_max=60))
    e.render(RATE)
    e.set_theme(themes[1], sequence_id="pair")
    status = e.status()
    assert status["playlist"] is None  # a theme change stops the playlist, as on the server
    assert status["theme_id"] == SECOND and status["preset_id"] == "soft" and status["sequence"]["id"] == "pair"
    e.render(4 * RATE)
    e.set_theme(themes[0], preset_id="quiet")
    assert e.theme_id == FIRST and e.preset_id == "quiet" and e.sequence_id is None
    with pytest.raises(KeyError):
        e.set_theme(themes[1], preset_id="nope")
    e.render(4 * RATE)
    e.close()
    assert e.mix is None


def test_render_sizes_and_seeds_with_a_playlist_running(themes):
    def run(sizes, seed=4):
        e = _engine(themes[0], seed=seed)
        e.start_playlist(_playlist(order="shuffle", play_min=1.5, play_max=3,
                                   entries=_playlist()["entries"] + [{"theme_id": FIRST, "preset_id": None,
                                                                      "sequence_id": "walk"}]))
        out = np.concatenate([e.render(n) for n in sizes])
        e.close()
        return out

    whole = run([12 * RATE])
    pieces = run([999, 4096, 1, 30000] * 10 + [12 * RATE - (999 + 4096 + 1 + 30000) * 10])
    assert np.array_equal(whole, pieces)
    assert not np.array_equal(whole, run([12 * RATE], seed=5))


# --- golden renders (another process, another hash seed: the same bytes) ---

def _env(**extra):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(ADDON), str(TESTS)])
    env["SONORIUM_LOG_LEVEL"] = "warning"
    env["PYTHONUTF8"] = "1"
    env.update(extra)
    return env


def _golden(theme_dir, out, seed, hashseed, *extra):
    cmd = [sys.executable, "-m", "sonorium.engine.render", str(theme_dir), "--seconds", "15", "--seed", str(seed),
           "--decoder", "pcm", "--values", json.dumps(FAST_VALUES), "--out", str(out), *extra]
    result = subprocess.run(cmd, env=_env(PYTHONHASHSEED=str(hashseed)), capture_output=True, text=True,
                            timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    return Path(out).read_bytes()


def test_golden_render_of_a_sequence(themes, tmp_path):
    first = _golden(themes[0], tmp_path / "a.wav", 1, 1, "--sequence", "walk")
    again = _golden(themes[0], tmp_path / "b.wav", 1, 2, "--sequence", "walk")
    other = _golden(themes[0], tmp_path / "c.wav", 2, 1, "--sequence", "walk")
    plain = _golden(themes[0], tmp_path / "d.wav", 1, 1)
    assert first == again
    assert first != other and first != plain


def test_golden_render_of_a_playlist(themes, tmp_path):
    path = tmp_path / "playlist.json"
    path.write_text(json.dumps(_playlist(order="shuffle", play_min=1.5, play_max=4)), encoding="utf-8")
    first = _golden(themes[0], tmp_path / "a.wav", 3, 1, "--playlist", str(path))
    again = _golden(themes[0], tmp_path / "b.wav", 3, 7, "--playlist", str(path))
    other = _golden(themes[0], tmp_path / "c.wav", 4, 1, "--playlist", str(path))
    assert first == again and first != other
    with wave.open(str(tmp_path / "a.wav")) as w:
        assert (w.getnchannels(), w.getframerate(), w.getnframes()) == (1, RATE, 15 * RATE)
