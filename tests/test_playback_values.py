"""Settings > Advanced's playback values: defaults, persistence, the API's checks, and the mixer following a change."""

import random
import wave
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

from playback_helpers import CHUNK, RATE, addon_modules  # noqa: E402

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.playback_values", "sonorium.recording", "sonorium.theme",
                       "sonorium.core.state", "sonorium.core.theme_metadata") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture
def stored():
    """The values as the settings store holds them (only the changed ones)."""
    values = {}
    m.playback_values.set_source(lambda: values)
    yield values
    m.playback_values.set_source(None)


def write_wav(path, seconds, amp=8000):
    rng = np.random.default_rng(7)
    samples = (rng.uniform(-1, 1, int(seconds * RATE)) * amp).astype(np.int16)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(RATE)
        f.writeframes(samples.tobytes())
    return len(samples)


# --- defaults ------------------------------------------------------------------

def test_defaults_without_a_store():
    pv = m.playback_values
    pv.set_source(None)
    assert pv.values() == pv.DEFAULTS
    assert pv.DEFAULTS == {
        "sparse_min_interval": 180.0, "sparse_max_interval": 1800.0, "sparse_interval_variance": 0.30,
        "group_gap": 120.0, "loop_crossfade": 1.5, "group_crossfade_min": 1.0, "group_crossfade_max": 60.0,
        "track_fade": 6.0, "switch_fade": 3.0, "ebb_long_rest": 0.12, "ebb_turn": 0.35,
        "short_file_threshold": 15.0,
    }
    # the old constants are the defaults
    r = m.recording
    assert (r.SPARSE_MIN_INTERVAL, r.SPARSE_MAX_INTERVAL, r.SPARSE_INTERVAL_VARIANCE) == (180.0, 1800.0, 0.30)
    assert (r.ExclusionGroupCoordinator.MIN_GAP_AFTER_EXCLUSIVE, r.LOOP_CROSSFADE_DURATION, r.TRACK_FADE_DURATION) == (120.0, 1.5, 6.0)
    assert (r.MIN_GROUP_CROSSFADE, r.MAX_GROUP_CROSSFADE, r.EBB_LONG_REST, r.EBB_TURN) == (1.0, 60.0, 0.12, 0.35)
    assert m.theme.TRACK_TOGGLE_FADE_SECONDS == 3.0 and m.theme.DEFAULT_SHORT_FILE_THRESHOLD == 15.0


def test_a_broken_stored_value_reads_as_the_default(stored):
    stored.update(loop_crossfade="x", track_fade=True, switch_fade=float("nan"), group_gap=None)
    v = m.playback_values.values()
    assert (v["loop_crossfade"], v["track_fade"], v["switch_fade"], v["group_gap"]) == (1.5, 6.0, 3.0, 120.0)


# --- persistence ------------------------------------------------------------------

def test_saved_and_loaded_with_the_settings(tmp_path):
    store = m.state.StateStore(tmp_path / "state.json")
    store.settings.playback_values = {"loop_crossfade": 2.5, "sparse_max_interval": 3600.0}
    store.save()
    again = m.state.StateStore(tmp_path / "state.json")
    again.load()
    assert again.settings.playback_values == {"loop_crossfade": 2.5, "sparse_max_interval": 3600.0}
    # an older state file without them: all defaults
    assert m.state.SonoriumSettings.from_dict({}).playback_values == {}


# --- validation ------------------------------------------------------------------

def test_validate_keeps_only_changed_values():
    v = m.playback_values.validate
    assert v({"loop_crossfade": 0.2, "track_fade": 6}, {}) == {"loop_crossfade": 0.2}
    assert v({"loop_crossfade": None}, {"loop_crossfade": 0.2, "ebb_turn": 0.5}) == {"ebb_turn": 0.5}
    assert v({"sparse_min_interval": 0, "sparse_max_interval": 0}) == {"sparse_min_interval": 0.0, "sparse_max_interval": 0.0}
    assert v({"sparse_max_interval": 100000}) == {"sparse_max_interval": 100000.0}  # far out: accepted


@pytest.mark.parametrize("body, word", [
    ({"nope": 1}, "Unknown"),
    ({"loop_crossfade": -1}, "negative"),
    ({"loop_crossfade": "2"}, "number"),
    ({"loop_crossfade": True}, "number"),
    ({"ebb_turn": 1.5}, "100%"),
    ({"sparse_min_interval": 2000}, "above"),
    ({"group_crossfade_min": 30, "group_crossfade_max": 20}, "above"),
    ([1, 2], "object"),
])
def test_validate_refuses(body, word):
    with pytest.raises(ValueError, match=word):
        m.playback_values.validate(body, {})


def test_api(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from unittest.mock import MagicMock
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    with addon_modules("sonorium.web.api_v2", "sonorium.core.state", "sonorium.playback_values", "sonorium.recording") as mods:
        store = mods.state.StateStore(tmp_path / "state.json")
        app = FastAPI()
        app.include_router(mods.api_v2.create_api_router(
            session_manager=MagicMock(), group_manager=MagicMock(), ha_registry=MagicMock(), state_store=store))
        c = TestClient(app)
        url = "/api/settings/playback-values"

        r = c.get(url)
        assert r.status_code == 200
        assert r.json()["values"] == r.json()["defaults"] == mods.playback_values.DEFAULTS

        r = c.put(url, json={"loop_crossfade": 0.2, "sparse_min_interval": 30, "sparse_max_interval": 7200})
        assert r.status_code == 200, r.text
        assert r.json()["values"]["loop_crossfade"] == 0.2 and r.json()["defaults"]["loop_crossfade"] == 1.5
        assert store.settings.playback_values == {"loop_crossfade": 0.2, "sparse_min_interval": 30.0, "sparse_max_interval": 7200.0}
        assert mods.playback_values.get("loop_crossfade") == 0.2  # the mixer reads it now
        assert mods.recording.loop_crossfade_samples() == int(0.2 * RATE)

        # saved: a new store reads it back
        again = mods.state.StateStore(tmp_path / "state.json")
        again.load()
        assert again.settings.playback_values["loop_crossfade"] == 0.2

        for body in ({"bogus": 1}, {"track_fade": -2}, {"track_fade": "6"}, {"sparse_min_interval": 9000},
                     {"ebb_long_rest": 2}):
            r = c.put(url, json=body)
            assert r.status_code == 400, body
        assert store.settings.playback_values["loop_crossfade"] == 0.2  # a refused change changes nothing

        r = c.put(url, json={"loop_crossfade": None})  # back to the default
        assert r.status_code == 200 and r.json()["values"]["loop_crossfade"] == 1.5
        assert "loop_crossfade" not in store.settings.playback_values
        mods.playback_values.set_source(None)


# --- the mixer follows ------------------------------------------------------------

def test_group_gap_when_the_group_sets_none(stored):
    clock = SimpleNamespace(now=0.0)
    group = m.recording.ExclusionGroupCoordinator(clock=lambda: clock.now)
    stored["group_gap"] = 7.0
    clock.now = group.INITIAL_DELAY + 1
    assert group.is_blocked("a")
    clock.now += group.COLLECT_SECONDS
    assert group.try_start_playing("a", 2.0)
    clock.now += 1.0
    group.finish_playing("a")
    assert group.get_wait_time() == pytest.approx(7.0)
    # a group's own gap still wins
    own = m.recording.ExclusionGroupCoordinator(gap_range=(50.0, 50.0), clock=lambda: clock.now)
    assert own._next_gap() == 50.0


def test_merry_go_round_crossfade_range(stored):
    cf = m.recording.group_crossfade
    assert cf({"crossfade": 90}) == 60.0 and cf({}) == 10.0
    stored.update(group_crossfade_min=15, group_crossfade_max=120)
    assert cf({"crossfade": 90}) == 90.0 and cf({}) == 15.0 and cf({"crossfade": 200}) == 120.0


def test_ebb_turn_chance(stored):
    rng = random.Random(3)
    # from the middle a step can't reach a knob, so only the Turn chance turns it
    stored["ebb_turn"] = 0.0
    assert all(m.recording.ebb_step(0.0, 0.5, 1, 0.0, rng)[1] == 1 for _ in range(200))
    stored["ebb_turn"] = 1.0
    assert all(m.recording.ebb_step(0.0, 0.5, 1, 0.0, rng)[1] == -1 for _ in range(200))


def test_short_file_threshold_for_new_themes(stored):
    stored["short_file_threshold"] = 40.0
    assert m.theme_metadata.ThemeMetadata(name="New").short_file_threshold == 40.0
    assert m.theme_metadata.ThemeMetadata(name="Own", short_file_threshold=5.0).short_file_threshold == 5.0
    meta = SimpleNamespace(duration_seconds=20.0)
    instance = m.recording.ThemeTrack.__new__(m.recording.ThemeTrack)
    instance.theme = None
    assert instance.short_file_threshold == 40.0
    assert m.recording.AudioFile.is_short_file(meta) is True
    assert m.recording.AudioFile.is_short_file(meta, 15.0) is False


def test_switch_fade_is_read_as_the_mix_runs(stored):
    step = m.theme.ThemeMix._fade_step
    assert step(m.theme.switch_fade_seconds()) == pytest.approx(CHUNK / (3.0 * RATE))
    stored["switch_fade"] = 10.0
    assert step(m.theme.switch_fade_seconds()) == pytest.approx(CHUNK / (10.0 * RATE))


def _loop_instance(path, n):
    return SimpleNamespace(meta=SimpleNamespace(path=str(path), duration_samples=n), volume=1.0, name="noise",
                           fade_in=0.0, fade_out=0.0)


def test_loop_crossfade_on_the_next_loop(tmp_path, stored):
    path = tmp_path / "noise.wav"
    n = write_wav(path, 4.0)
    stream = m.recording.CrossfadeRecordingStream(_loop_instance(path, n))
    next(stream)
    assert stream.overlap == int(1.5 * RATE)
    stored["loop_crossfade"] = 0.5
    stream2 = m.recording.CrossfadeRecordingStream(_loop_instance(path, n))
    next(stream2)
    assert stream2.overlap == int(0.5 * RATE)  # the next play
    # the playing one takes it up at its next loop
    for _ in range(int(1.2 * n / CHUNK)):
        next(stream)
    assert stream.overlap == int(0.5 * RATE)


def _sparse(path, n):
    instance = SimpleNamespace(meta=SimpleNamespace(path=str(path), duration_samples=n, duration_seconds=n / RATE),
                               presence=1.0, is_enabled=True, exclusive=False, name="bird", volume=1.0,
                               fade_in=0.0, fade_out=0.0)
    return m.recording.SparsePlaybackStream(instance, play_now=True)


def _silent_run(stream, limit):
    """Chunks until sound, then the silent chunks until it sounds again."""
    while not next(stream).any():
        pass
    while next(stream).any():
        pass
    count = 1
    while not next(stream).any():
        count += 1
        if count > limit:
            break
    return count


def test_wait_between_plays_and_randomness(tmp_path, stored):
    path = tmp_path / "bird.wav"
    n = write_wav(path, 0.5)
    stored.update(sparse_min_interval=2.0, sparse_max_interval=2.0, sparse_interval_variance=0.0)
    gap = _silent_run(_sparse(path, n), 10_000)
    assert gap * CHUNK / RATE == pytest.approx(2.0, abs=0.1)
    stored.update(sparse_min_interval=4.0, sparse_max_interval=4.0)
    gap = _silent_run(_sparse(path, n), 10_000)
    assert gap * CHUNK / RATE == pytest.approx(4.0, abs=0.1)


def test_a_wait_under_way_follows_a_change(tmp_path, stored):
    path = tmp_path / "bird.wav"
    n = write_wav(path, 0.5)
    stored.update(sparse_min_interval=2.0, sparse_max_interval=2.0, sparse_interval_variance=0.0)
    stream = _sparse(path, n)
    while not next(stream).any():
        pass
    while next(stream).any():
        pass
    half = int(1.0 * RATE / CHUNK)
    for _ in range(half):
        assert not next(stream).any()
    stored.update(sparse_min_interval=6.0, sparse_max_interval=6.0)  # the half left (1 s) becomes 3 s
    count = 0
    while not next(stream).any():
        count += 1
    assert count * CHUNK / RATE == pytest.approx(3.0, abs=0.15)


def test_track_fade_on_an_intermittent_play(tmp_path, stored):
    path = tmp_path / "bird.wav"
    n = write_wav(path, 3.0)
    stored.update(track_fade=0.0)  # no fade: full level from the first chunk
    stream = _sparse(path, n)
    first = next(stream)
    while not first.any():
        first = next(stream)
    assert np.abs(first.astype(np.float64)).max() > 4000
    stored.update(track_fade=6.0)  # 1 s (a third of the file): the first chunk is nearly silent
    stream = _sparse(path, n)
    first = next(stream)
    while not first.any():
        first = next(stream)
    assert np.abs(first.astype(np.float64)).max() < 500


def test_ambient_mixer_presence_follows_the_wait_range(stored):
    import json
    import sys
    from playback_helpers import PACKAGE
    with addon_modules("sonorium.plugins.loader", "sonorium.playback_values") as mods:
        folder = PACKAGE / "plugins" / "builtin" / "ambient_mixer"
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        am = sys.modules[mods.loader.load_plugin_class(folder, manifest).__module__]
        values = {}
        mods.playback_values.set_source(lambda: values)
        assert am.random_presence(5, "1h") == pytest.approx(0.667, abs=0.001)  # 12 min in 3-30 min
        values.update(sparse_min_interval=60.0, sparse_max_interval=3660.0)
        assert am.random_presence(5, "1h") == pytest.approx(1 - 660 / 3600, abs=0.001)  # 12 min in 1-61 min
        values.update(sparse_min_interval=900.0, sparse_max_interval=1800.0)
        assert am.random_presence(5, "1h") == 1.0  # shorter than the shortest: clamped
        mods.playback_values.set_source(None)
