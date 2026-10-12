"""
A restart asked for in Settings or the web UI plays the channels that were
playing again (core/restart_resume.py, WebApp._remember_for_restart and
_resume_after_restart); a stop or a crash leaves them stopped.
"""

import asyncio
import json
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from playback_helpers import FakeChannelManager, FakeClock, addon_modules
from test_connection_remove import app  # noqa: F401  (fixture: standalone runtime + api module)

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.session_manager", "sonorium.core.state", "sonorium.core.playback_runner",
                       "sonorium.core.restart_resume", "sonorium.host") as mods:
        m.__dict__.update(mods.__dict__)
        yield


THEMES = {
    "storm": {"presets": {"a": {"name": "Drizzle", "is_default": True, "tracks": {}},
                          "b": {"name": "Rain", "tracks": {}}},
              "sequences": {"loop": {"name": "Loop", "presets": ["a", "b"], "at_end": "restart",
                                     "hold_min": 10, "hold_max": 10, "ramp": 5}}},
    "tavern": {"presets": {"quiet": {"name": "Quiet", "is_default": True, "tracks": {}}}},
}


def build(tmp_path):
    folders = {}
    for theme_id, doc in THEMES.items():
        folder = tmp_path / theme_id
        folder.mkdir(exist_ok=True)
        (folder / "presets.json").write_text(json.dumps(doc), encoding="utf-8")
        folders[theme_id] = folder
    store = m.state.StateStore(tmp_path / "state.json")
    store.settings.enabled_speakers = ["media_player.x"]
    registry = MagicMock()
    registry.resolve_selection.return_value = ["media_player.x"]
    controller = MagicMock()
    for name in ("play_media_multi", "set_volume_multi", "stop_multi", "pause_multi"):
        setattr(controller, name, AsyncMock(side_effect=lambda ids, *a: {i: True for i in ids}))
    metadata = SimpleNamespace(get_folder_for_id=folders.get, get_metadata_by_folder=lambda folder: None)
    manager = m.session_manager.SessionManager(store, registry, controller, "http://test",
                                               channel_manager=FakeChannelManager(), theme_metadata_manager=metadata)
    themes = {t: SimpleNamespace(id=t, name=t.title()) for t in THEMES}
    manager.get_theme = themes.get
    manager.runner = m.playback_runner.PlaybackRunner(manager, clock=FakeClock(), rng=random.Random(7))
    return SimpleNamespace(manager=manager, store=store)


def new_channel(t, theme_id, **fields):
    session = t.manager.create(theme_id=theme_id,
                               adhoc_selection=m.state.SpeakerSelection(include_speakers=["media_player.x"]))
    for name, value in fields.items():
        setattr(session, name, value)
    return session


def run(coro):
    async def main():
        result = await coro
        await asyncio.sleep(0)
        return result
    return asyncio.run(main())


def test_playing_channels_play_again_with_their_settings(tmp_path):
    t = build(tmp_path)
    storm = new_channel(t, "storm", volume=35)
    tavern = new_channel(t, "tavern", volume=80)
    stopped = new_channel(t, "tavern")
    run(t.manager.play(storm.id))
    t.manager.start_sequence(storm.id, "loop")
    run(t.manager.play(tavern.id))
    assert storm.is_playing and tavern.is_playing and not stopped.is_playing

    assert m.restart_resume.remember(t.manager.state.sessions.values(), tmp_path) == 2
    t.store.save()

    # The next start: state.json comes back with every channel stopped
    t2 = build(tmp_path)
    t2.store.load()
    assert not any(s.is_playing for s in t2.store.sessions.values())
    # a change made meanwhile is put back to what was playing
    t2.store.sessions[storm.id].volume = 60

    entries = m.restart_resume.take(tmp_path)
    assert not (tmp_path / m.restart_resume.RESUME_FILE).exists()  # taken once
    told = []

    async def on_started(session):
        told.append(session.id)

    started = run(m.restart_resume.resume(t2.manager, entries, on_started=on_started, retry_seconds=0))
    assert sorted(started) == sorted([storm.id, tavern.id]) == sorted(told)
    sessions = t2.store.sessions
    assert sessions[storm.id].is_playing and sessions[tavern.id].is_playing
    assert not sessions[stopped.id].is_playing
    assert sessions[storm.id].volume == 35 and sessions[tavern.id].volume == 80
    assert sessions[storm.id].sequence_id == "loop"
    assert t2.manager.runner.running_sequence(storm.id) is not None


def test_a_channel_that_cant_start_yet_is_tried_again(tmp_path):
    t = build(tmp_path)
    session = new_channel(t, "storm")
    run(t.manager.play(session.id))
    m.restart_resume.remember([session], tmp_path)
    t.manager.state.sessions[session.id].is_playing = False
    answers = iter([False, True])
    real_play = t.manager.play

    async def play(session_id):
        return await real_play(session_id) if next(answers) else False

    t.manager.play = play
    started = run(m.restart_resume.resume(t.manager, m.restart_resume.take(tmp_path), retry_seconds=0, attempts=3))
    assert started == [session.id] and session.is_playing


def test_nothing_playing_leaves_no_file(tmp_path):
    t = build(tmp_path)
    new_channel(t, "storm")
    assert m.restart_resume.remember(t.manager.state.sessions.values(), tmp_path) == 0
    assert not (tmp_path / m.restart_resume.RESUME_FILE).exists()
    assert m.restart_resume.take(tmp_path) == []


def test_an_old_file_is_ignored_and_removed(tmp_path):
    (tmp_path / m.restart_resume.RESUME_FILE).write_text(
        json.dumps({"saved_at": 1000.0, "channels": [{"id": "x"}]}), encoding="utf-8")
    assert m.restart_resume.take(tmp_path, now=1000.0 + m.restart_resume.MAX_AGE_SECONDS + 1) == []
    assert not (tmp_path / m.restart_resume.RESUME_FILE).exists()


# --- The web app: only a requested restart remembers the channels -------------------------

def _shutdown_remembers(app, tmp_path, pending):
    from sonorium import host
    host._reset_for_tests()
    host._pending = pending
    try:
        api = object.__new__(app.api.WebApp)
        session = SimpleNamespace(id="s1", is_playing=True, owner=None, theme_id="storm", preset_id="a",
                                  sequence_id=None, playlist_id=None, playlist_position=0, playlist_order=[],
                                  volume=40)
        api._state_store = SimpleNamespace(sessions={"s1": session}, state_file=tmp_path / "state.json")
        api._remember_for_restart()
    finally:
        host._reset_for_tests()
    return (tmp_path / "resume_after_restart.json").exists()


def test_requested_restart_remembers_playing_channels(app, tmp_path):
    assert _shutdown_remembers(app, tmp_path, "restart")
    saved = json.loads((tmp_path / "resume_after_restart.json").read_text(encoding="utf-8"))
    assert saved["channels"] == [{"id": "s1", "theme_id": "storm", "preset_id": "a", "sequence_id": None,
                                  "playlist_id": None, "playlist_position": 0, "playlist_order": [],
                                  "volume": 40}]


def test_a_stop_remembers_nothing(app, tmp_path):
    assert not _shutdown_remembers(app, tmp_path, None)

