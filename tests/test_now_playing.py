"""
Now playing on the Status page: GET /api/now-playing reads each playing
channel's mix as the mixer has it (sonorium/now_playing.py). The countdowns
are the waits, pauses and gaps the mixer actually picked: these tests pull
the mix chunk by chunk and check that the change comes when the countdown
said it would.
"""
import random
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("av")

from playback_helpers import addon_modules, tone_mp3  # noqa: E402

m = SimpleNamespace()
RATE, CHUNK = 44100, 1024
CHUNK_SECONDS = CHUNK / RATE


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.recording", "sonorium.theme", "sonorium.now_playing",
                       "sonorium.playback_values") as mods:
        m.__dict__.update(mods.__dict__)
        yield


@pytest.fixture(autouse=True)
def values():
    """Short waits, so the tests hear a few plays: Wait between plays exactly 5 s, no randomness."""
    stored = {"sparse_min_interval": 5.0, "sparse_max_interval": 5.0, "sparse_interval_variance": 0.0}
    m.playback_values.set_source(lambda: stored)
    yield stored
    m.playback_values.set_source(None)


def theme_of(folder, tracks, groups=None):
    """A theme definition as refresh_themes builds one: tracks = [(file stem, group or None, settings)]."""
    theme = SimpleNamespace(name="Storm", groups=groups or {}, short_file_threshold=15.0,
                            sonorium=SimpleNamespace(master_volume=1.0))
    theme.instances = []
    for stem, group, settings in tracks:
        path = folder / group / f"{stem}.mp3" if group else folder / f"{stem}.mp3"
        meta = m.recording.RecordingMetadata(path, folder)
        instance = m.recording.RecordingThemeInstance(meta, theme)
        for key, value in settings.items():
            setattr(instance, key, value)
        theme.instances.append(instance)
    return theme


def rows(mix):
    """{name: row} of every row, groups and their tracks."""
    out = {}
    for section in m.now_playing.mix_now(mix):
        for row in section["rows"]:
            out[row["name"]] = dict(row, section=section["title"])
            for track in row.get("tracks", []):
                out[track["name"]] = dict(track, section=section["title"], group=row["name"])
    return out


def pull_until(chunks, mix, done, limit_seconds=120):
    """Mix chunks until done(rows) is true; the audio seconds it took."""
    for i in range(int(limit_seconds / CHUNK_SECONDS)):
        if done(rows(mix)):
            return i * CHUNK_SECONDS
        next(chunks)
    raise AssertionError("never happened")


def test_an_intermittent_track_counts_down_to_its_next_play(tmp_path):
    folder = tmp_path / "Storm"
    folder.mkdir()
    tone_mp3(folder / "Owl.mp3", 2.0, 440)
    random.seed(5)
    mix = m.theme.ThemeStream(theme_of(folder, [("Owl", None, {"playback_mode": m.recording.PlaybackMode.SPARSE})]))
    chunks = mix.iter_chunks()
    next(chunks)

    # The first wait (a random part of the 5 s wait): exact to the chunk
    row = rows(mix)["Owl"]
    assert (row["section"], row["mode"], row["state"], row["now"]) == ("Intermittent", "Intermittent", "wait", "Next in {t}")
    said = row["seconds"]
    took = pull_until(chunks, mix, lambda r: r["Owl"]["state"] == "on")
    assert abs(took - said) <= 2 * CHUNK_SECONDS
    playing = rows(mix)["Owl"]
    assert playing["now"] == "Playing" and playing["level"] == 1.0

    # After the 2 s play, the 5 s wait it picked
    pull_until(chunks, mix, lambda r: r["Owl"]["state"] == "wait")
    row = rows(mix)["Owl"]
    assert row["now"] == "Next in {t}" and abs(row["seconds"] - 5.0) <= 2 * CHUNK_SECONDS
    took = pull_until(chunks, mix, lambda r: r["Owl"]["state"] == "on")
    assert abs(took - row["seconds"]) <= 2 * CHUNK_SECONDS


def test_a_group_counts_down_to_its_next_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(m.recording.ExclusionGroupCoordinator, "INITIAL_DELAY", 3.0)
    folder = tmp_path / "Storm"
    (folder / "Thunder").mkdir(parents=True)
    tone_mp3(folder / "Thunder" / "Crack A.mp3", 2.0, 300)
    tone_mp3(folder / "Thunder" / "Crack B.mp3", 2.0, 500)
    tone_mp3(folder / "Thunder" / "Crack C.mp3", 2.0, 700)
    random.seed(11)
    theme = theme_of(folder, [("Crack A", "Thunder", {}), ("Crack B", "Thunder", {}),
                              ("Crack C", "Thunder", {"is_enabled": False})],
                     groups={"Thunder": {"gap_min": 6, "gap_max": 6}})
    mix = m.theme.ThemeStream(theme)
    chunks = mix.iter_chunks()
    next(chunks)

    group = rows(mix)["Thunder"]
    assert (group["type"], group["section"], group["mode"]) == ("group", "Intermittent", "One at a time")
    assert group["now"] == "Next in {t}" and abs(group["seconds"] - 3.0) <= 2 * CHUNK_SECONDS
    assert [t["name"] for t in group["tracks"]] == ["Crack A", "Crack B", "Crack C"]
    assert rows(mix)["Crack C"]["now"] == "Muted" and rows(mix)["Crack C"]["state"] == "off"

    # The turn opens on time; the group then picks among the tracks asking
    took = pull_until(chunks, mix, lambda r: r["Thunder"]["now"] != "Next in {t}")
    assert abs(took - group["seconds"]) <= 2 * CHUNK_SECONDS
    assert rows(mix)["Thunder"]["now"] == "Picking next"
    pull_until(chunks, mix, lambda r: r["Thunder"]["state"] == "on")
    now = rows(mix)
    holder = next(n for n in ("Crack A", "Crack B") if now[n]["state"] == "on")
    other = "Crack B" if holder == "Crack A" else "Crack A"
    assert now["Thunder"]["now"] == f"{holder} playing" and now["Thunder"]["level"] == 1.0
    assert now[other]["now"] == "Waiting" and now[other]["level"] is None

    # When the play ends, the gap the group picked (6 s)
    pull_until(chunks, mix, lambda r: r["Thunder"]["state"] == "wait")
    group = rows(mix)["Thunder"]
    assert group["now"] == "Next in {t}" and abs(group["seconds"] - 6.0) <= 2 * CHUNK_SECONDS
    took = pull_until(chunks, mix, lambda r: r["Thunder"]["now"] == "Picking next")
    assert abs(took - group["seconds"]) <= 2 * CHUNK_SECONDS


def test_ebb_and_flow_shows_its_move_its_level_and_its_band(tmp_path):
    folder = tmp_path / "Storm"
    folder.mkdir()
    tone_mp3(folder / "Wind.mp3", 30.0, 200)
    random.seed(2)
    settings = {"playback_mode": m.recording.PlaybackMode.PRESENCE, "presence": 0.0, "volume": 0.8,
                "volume_low": 0.2}
    mix = m.theme.ThemeStream(theme_of(folder, [("Wind", None, settings)]))
    chunks = mix.iter_chunks()
    next(chunks)
    row = rows(mix)["Wind"]
    assert (row["section"], row["mode"], row["state"]) == ("Ebb & Flow", "Ebb & Flow", "on")
    assert row["band"] == [0.2, 0.8]
    assert 0.2 - 1e-3 <= row["level"] <= 0.8 + 1e-3
    for _ in range(4):
        row = rows(mix)["Wind"]
        assert row["now"] in ("Rising, {t}", "Falling, {t}", "Holding, {t}")
        took = pull_until(chunks, mix, lambda r: r["Wind"]["now"] != row["now"])
        assert abs(took - row["seconds"]) <= 3 * CHUNK_SECONDS
        level = rows(mix)["Wind"]["level"]
        assert 0.2 - 1e-3 <= level <= 0.8 + 1e-3


def test_a_track_muted_mid_play_fades_then_shows_muted(tmp_path):
    folder = tmp_path / "Storm"
    folder.mkdir()
    tone_mp3(folder / "Rain.mp3", 20.0, 330)
    theme = theme_of(folder, [("Rain", None, {"volume": 0.5})])
    mix = m.theme.ThemeStream(theme)
    chunks = mix.iter_chunks()
    next(chunks)
    row = rows(mix)["Rain"]
    assert (row["section"], row["mode"], row["state"], row["now"], row["level"]) == \
        ("Background", "Background", "on", "Playing", 0.5)
    theme.instances[0].is_enabled = False
    next(chunks)
    next(chunks)
    assert (rows(mix)["Rain"]["state"], rows(mix)["Rain"]["now"]) == ("fade", "Fading out")
    pull_until(chunks, mix, lambda r: r["Rain"]["state"] == "off")
    assert rows(mix)["Rain"]["now"] == "Muted" and rows(mix)["Rain"]["level"] is None


def test_speaker_buffers_take_the_longest():
    assert m.now_playing.speaker_buffer_seconds([]) == 0.0
    assert m.now_playing.speaker_buffer_seconds(["sonos", "cast"]) == 5.0
    assert m.now_playing.speaker_buffer_seconds(["somethingelse"]) == m.now_playing.DEFAULT_SPEAKER_BUFFER_SECONDS


# --- The endpoint ---

def test_now_playing_lists_only_playing_channels(tmp_path):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    with addon_modules("sonorium.core.session_manager", "sonorium.core.state", "sonorium.core.playback_runner",
                       "sonorium.core.sequences", "sonorium.core.playlists", "sonorium.web.api_v2",
                       "sonorium.recording", "sonorium.theme") as mods:
        import test_sequences_playlists as runner_tests
        runner_tests.m.__dict__.update(mods.__dict__)
        t = runner_tests.build(tmp_path)
        t.manager.registry.get_speaker_name = lambda speaker_id: "Desk"
        t.manager.registry.get_speaker = lambda speaker_id: SimpleNamespace(type="cast")
        app = FastAPI()
        app.include_router(mods.api_v2.create_api_router(
            session_manager=t.manager, group_manager=MagicMock(), ha_registry=MagicMock(), state_store=t.store))
        client = TestClient(app)

        body = client.get("/api/now-playing").json()
        assert body["channels"] == [] and body["at"] > 0  # idle: not listed

        runner_tests.run(t.manager.play(t.session.id))
        folder = tmp_path / "mix"
        folder.mkdir()
        tone_mp3(folder / "Rain.mp3", 20.0, 330)
        theme = SimpleNamespace(name="Storm", groups={}, short_file_threshold=15.0,
                                sonorium=SimpleNamespace(master_volume=1.0))
        theme.instances = [mods.recording.RecordingThemeInstance(
            mods.recording.RecordingMetadata(folder / "Rain.mp3", folder), theme)]
        t.channel._theme_stream = mods.theme.ThemeStream(theme)
        t.channel.current_theme_name = "Storm"
        next(t.channel._theme_stream.iter_chunks())

        body = client.get("/api/now-playing").json()
        [channel] = body["channels"]
        assert channel["id"] == t.session.id and channel["theme_name"] == "Storm"
        assert channel["preset_name"] and channel["speakers"] == "Desk" and channel["speakers_playing"] is True
        assert channel["listening"] is False and channel["buffer_seconds"] == 5.0
        [section] = channel["sections"]
        assert section["title"] == "Background"
        assert [(r["name"], r["state"], r["now"]) for r in section["rows"]] == [("Rain", "on", "Playing")]

        # Speakers muted: what is heard is a browser's, if any; no speaker buffer
        t.manager.set_speakers_muted(t.session.id, True)
        assert client.get("/api/now-playing").json()["channels"][0]["buffer_seconds"] == 0.0
