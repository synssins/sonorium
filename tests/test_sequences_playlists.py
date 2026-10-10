"""
Sequences (presets in order inside a theme) and playlists (themes in order)
on a channel, with a fake clock; and the one-time conversion of theme
cycling into playlists.
"""

import asyncio
import json
import random
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from playback_helpers import FakeChannelManager, FakeClock, addon_modules

m = SimpleNamespace()


@pytest.fixture(autouse=True, scope="module")
def modules():
    with addon_modules("sonorium.core.session_manager", "sonorium.core.state", "sonorium.core.playback_runner",
                       "sonorium.core.sequences", "sonorium.core.playlists") as mods:
        m.__dict__.update(mods.__dict__)
        yield


THEMES = {
    "storm": {
        "presets": {"a": {"name": "Drizzle", "is_default": True, "tracks": {}},
                    "b": {"name": "Rain", "tracks": {}},
                    "c": {"name": "Downpour", "tracks": {}}},
        "sequence_defaults": {"hold_min": 10, "hold_max": 10, "ramp": 5},
        "sequences": {
            "up_down": {"name": "Up and down", "presets": ["a", "b", "c"], "at_end": "reverse"},
            "loop": {"name": "Loop", "presets": ["a", "b", "c"], "at_end": "restart"},
            "once": {"name": "Once", "presets": ["a", "b", "c"], "at_end": "stop"},
            "random_hold": {"name": "Random", "presets": ["a", "b"], "hold_min": 10, "hold_max": 20, "ramp": 2},
        },
    },
    "tavern": {"presets": {"quiet": {"name": "Quiet", "is_default": True, "tracks": {}},
                           "busy": {"name": "Busy", "tracks": {}}}},
    "forest": {"presets": {"dawn": {"name": "Dawn", "tracks": {}}, "dusk": {"name": "Dusk", "tracks": {}}},
               "sequences": {"day": {"name": "Day", "presets": ["dawn", "dusk"], "at_end": "restart",
                                     "hold_min": 50, "hold_max": 50, "ramp": 4}}},
    "beach": {"presets": {}},
}


def build(tmp_path, themes=THEMES, crossfade=4.5):
    folders = {}
    for theme_id, doc in themes.items():
        folder = tmp_path / theme_id
        folder.mkdir(exist_ok=True)
        (folder / "presets.json").write_text(json.dumps(doc), encoding="utf-8")
        folders[theme_id] = folder
    store = m.state.StateStore(tmp_path / "state.json")
    store.settings.crossfade_duration = crossfade
    store.settings.enabled_speakers = ["media_player.x"]
    registry = MagicMock()
    registry.resolve_selection.return_value = ["media_player.x"]
    controller = MagicMock()
    for name in ("play_media_multi", "set_volume_multi", "stop_multi", "pause_multi"):
        setattr(controller, name, AsyncMock(side_effect=lambda ids, *a: {i: True for i in ids}))
    metadata = SimpleNamespace(get_folder_for_id=folders.get, get_metadata_by_folder=lambda folder: None)
    channels = FakeChannelManager()
    manager = m.session_manager.SessionManager(store, registry, controller, "http://test",
                                               channel_manager=channels, theme_metadata_manager=metadata)
    themes_by_id = {t: SimpleNamespace(id=t, name=t.title()) for t in themes}
    manager.get_theme = themes_by_id.get
    clock = FakeClock()
    manager.runner = m.playback_runner.PlaybackRunner(manager, clock=clock, rng=random.Random(7))
    session = manager.create(theme_id="storm",
                             adhoc_selection=m.state.SpeakerSelection(include_speakers=["media_player.x"]))
    return SimpleNamespace(manager=manager, session=session, clock=clock, channel=channels.channel,
                           controller=controller, store=store, folders=folders)


def run(coro):
    async def main():
        result = await coro
        await asyncio.sleep(0)  # let play's speaker task run
        return result
    return asyncio.run(main())


def at(t, when):
    """Move the fake clock to `when` seconds after the start and run the timers."""
    t.clock.now = 1000.0 + when
    run(t.manager.runner.tick())


# --- Sequences ---

def test_sequence_reverses_at_the_end(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "up_down")
    assert t.session.sequence_id == "up_down" and t.session.preset_id == "a"
    assert t.channel.glides()[-1] == 3.0  # started by hand: the quick glide
    seen = []
    for when in (12.9, 13, 28, 43, 58, 73):  # 3 s glide + 10 s hold, then 5 s ramp + 10 s hold
        at(t, when)
        seen.append(t.session.preset_id)
    assert seen == ["a", "b", "c", "b", "a", "b"]
    assert t.channel.glides()[-1] == 5  # steps glide over the sequence's ramp


def test_sequence_restarts_at_the_end(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "loop")
    seen = []
    for when in (13, 28, 43, 58):
        at(t, when)
        seen.append(t.session.preset_id)
    assert seen == ["b", "c", "a", "b"]


def test_sequence_stops_on_its_last_preset(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "once")
    for when in (13, 28):
        at(t, when)
    assert t.session.preset_id == "c" and t.session.sequence_id == "once"
    at(t, 43)
    assert t.session.preset_id == "c"
    assert t.session.sequence_id is None and t.manager.runner.running_sequence(t.session.id) is None


def test_hold_is_random_in_its_range_each_step(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "random_hold")
    holds = []
    for _ in range(12):
        sequence_run = t.manager.runner.running_sequence(t.session.id)
        start = t.clock.now
        t.clock.now = sequence_run.next_at
        run(t.manager.runner.tick())
        holds.append(sequence_run.next_at - t.clock.now - 2)  # minus the 2 s ramp
        assert sequence_run.next_at > t.clock.now > start
    assert all(10 <= h <= 20 for h in holds)
    assert len({round(h, 3) for h in holds}) > 6  # drawn fresh each time


def test_picking_a_preset_by_hand_stops_the_sequence(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "up_down")
    at(t, 13)
    t.manager.update(t.session.id, preset_id="c")
    assert t.session.sequence_id is None and t.manager.runner.running_sequence(t.session.id) is None
    assert t.session.preset_id == "c" and t.channel.glides()[-1] == 3.0
    at(t, 100)
    assert t.session.preset_id == "c"


def test_stop_then_play_restarts_the_sequence_from_its_first_preset(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "up_down")
    at(t, 13)
    assert t.session.preset_id == "b"
    run(t.manager.stop(t.session.id))
    assert t.manager.runner.running_sequence(t.session.id) is None
    assert t.session.sequence_id == "up_down"  # kept
    at(t, 40)
    assert t.session.preset_id == "b"  # nothing runs while stopped
    # Saved, so a restart of the add-on keeps it
    saved = m.state.StateStore(t.store.state_file)
    saved.load()
    assert saved.sessions[t.session.id].sequence_id == "up_down"
    run(t.manager.play(t.session.id))
    assert t.session.preset_id == "a"
    assert t.manager.runner.running_sequence(t.session.id).index == 0


def test_sequence_on_a_stopped_channel_waits_for_play(tmp_path):
    t = build(tmp_path)
    t.manager.start_sequence(t.session.id, "loop")
    assert t.session.preset_id == "a" and t.manager.runner.running_sequence(t.session.id) is None
    run(t.manager.play(t.session.id))
    assert t.manager.runner.running_sequence(t.session.id) is not None


def test_unknown_or_empty_sequence_is_refused(tmp_path):
    themes = json.loads(json.dumps(THEMES))
    themes["storm"]["sequences"]["empty"] = {"name": "Empty", "presets": ["gone"]}
    t = build(tmp_path, themes)
    with pytest.raises(LookupError):
        t.manager.start_sequence(t.session.id, "nope")
    with pytest.raises(ValueError):
        t.manager.start_sequence(t.session.id, "empty")  # its only preset no longer exists


def test_removing_a_preset_drops_it_from_sequences(tmp_path):
    t = build(tmp_path)
    changed = m.sequences.drop_preset(t.folders["storm"], "b")
    assert sorted(changed) == ["loop", "once", "random_hold", "up_down"]
    doc = json.loads((t.folders["storm"] / "presets.json").read_text(encoding="utf-8"))
    assert doc["sequences"]["up_down"]["presets"] == ["a", "c"]
    assert doc["presets"]["b"]  # presets.json's other keys are kept
    assert doc["sequence_defaults"] == {"hold_min": 10, "hold_max": 10, "ramp": 5}


def test_status_reports_step_and_next_change(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.start_sequence(t.session.id, "up_down")
    at(t, 13)
    status = t.manager.playback_status(t.session)["sequence"]
    assert status["name"] == "Up and down" and status["running"]
    assert (status["step"], status["steps"]) == (2, 3)
    assert status["current_preset_id"] == "b" and status["current_preset_name"] == "Rain"
    assert status["seconds_until_change"] == 15
    assert status["next_change_at"].endswith("+00:00")


# --- Playlists ---

def add_playlist(t, entries, **extra):
    playlist = {"name": "Evening", "entries": entries, "order": "in_order", "repeat": True,
                "play_min": 10, "play_max": 20, "owner": None, **extra}
    t.store.playlists["p1"] = playlist
    return playlist


ENTRIES = [
    {"theme_id": "tavern", "preset_id": "busy", "sequence_id": None},
    {"theme_id": "forest", "preset_id": None, "sequence_id": "day"},
    {"theme_id": "storm", "preset_id": None, "sequence_id": None},
]


def test_playlist_plays_each_entry_for_a_random_time_and_applies_it(tmp_path):
    t = build(tmp_path)
    add_playlist(t, ENTRIES)
    t.manager.set_playlist(t.session.id, "p1")
    assert (t.session.theme_id, t.session.preset_id) == ("tavern", "busy")  # set while stopped
    run(t.manager.play(t.session.id))
    runner = t.manager.runner
    themes, durations = [], []
    for _ in range(6):
        playlist_run = runner.running_playlist(t.session.id)
        durations.append(playlist_run.next_at - t.clock.now)
        themes.append(t.session.theme_id)
        if t.session.theme_id == "forest":
            assert t.session.sequence_id == "day" and t.session.preset_id == "dawn"
            assert runner.running_sequence(t.session.id) is not None
        elif t.session.theme_id == "storm":
            assert t.session.preset_id == "a"  # no preset in the entry: the theme's default
            assert t.session.sequence_id is None
        t.clock.now = playlist_run.next_at
        run(runner.tick())
    assert themes == ["tavern", "forest", "storm", "tavern", "forest", "storm"]
    assert all(10 <= d <= 20 for d in durations) and len(set(durations)) > 3
    crossfades = [c for c in t.channel.calls if c[0] == "set_theme"]
    assert crossfades[-1][2] == 4.5  # theme changes crossfade over the Crossfade setting


def test_shuffle_never_repeats_an_entry_back_to_back(tmp_path):
    t = build(tmp_path)
    entries = [{"theme_id": th, "preset_id": None, "sequence_id": None} for th in ("tavern", "forest", "storm", "beach")]
    add_playlist(t, entries, order="shuffle", play_min=5, play_max=5)
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    order = []
    for _ in range(40):
        order.append(t.session.playlist_order[t.session.playlist_position])
        t.clock.now += 5
        run(t.manager.runner.tick())
    assert all(a != b for a, b in zip(order, order[1:]))
    for i in range(0, 40, 4):  # every round plays each entry once
        assert sorted(order[i:i + 4]) == [0, 1, 2, 3]
    assert order[:4] != order[4:8] or order[4:8] != order[8:12]  # really shuffled


def test_playlist_without_repeat_stops_the_channel_at_the_end(tmp_path):
    t = build(tmp_path)
    add_playlist(t, ENTRIES[:2], repeat=False, play_min=10, play_max=10)
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    at(t, 10)
    assert t.session.theme_id == "forest" and t.session.is_playing
    at(t, 20)
    assert not t.session.is_playing
    t.controller.stop_multi.assert_awaited()
    assert t.session.playlist_id == "p1"  # play again: it starts over


def test_choosing_a_theme_by_hand_stops_the_playlist(tmp_path):
    t = build(tmp_path)
    add_playlist(t, ENTRIES)
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    t.manager.update(t.session.id, theme_id="beach")
    assert t.session.playlist_id is None and t.manager.runner.running_playlist(t.session.id) is None
    assert t.session.theme_id == "beach"


def test_clearing_the_playlist_keeps_the_theme(tmp_path):
    t = build(tmp_path)
    add_playlist(t, ENTRIES)
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    t.manager.clear_playlist(t.session.id)
    assert t.session.playlist_id is None and t.session.theme_id == "tavern" and t.session.is_playing


def test_next_skips_to_the_next_entry_or_sequence_step(tmp_path):
    t = build(tmp_path)
    add_playlist(t, ENTRIES)
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    assert run(t.manager.skip(t.session.id)) == "playlist"
    assert t.session.theme_id == "forest"
    t.manager.clear_playlist(t.session.id)
    assert run(t.manager.skip(t.session.id)) == "sequence"  # the entry's sequence keeps running
    assert t.session.preset_id == "dusk"
    t.manager.stop_sequence(t.session.id)
    assert run(t.manager.skip(t.session.id)) is None


def test_playlist_position_is_saved_and_resumed(tmp_path):
    t = build(tmp_path)
    add_playlist(t, ENTRIES, play_min=10, play_max=10)
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    at(t, 10)
    run(t.manager.stop(t.session.id))
    saved = m.state.StateStore(t.store.state_file)
    saved.load()
    session = saved.sessions[t.session.id]
    assert (session.playlist_id, session.playlist_position, session.playlist_order) == ("p1", 1, [0, 1, 2])
    assert saved.playlists["p1"]["name"] == "Evening"
    run(t.manager.play(t.session.id))
    assert t.session.theme_id == "forest"  # carries on with the entry it was on


def test_entry_with_a_missing_theme_is_skipped(tmp_path):
    t = build(tmp_path)
    add_playlist(t, [{"theme_id": "gone", "preset_id": None, "sequence_id": None}] + ENTRIES[:1])
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    assert t.session.theme_id == "tavern"


# --- Cycling becomes playlists ---

def old_state(**sessions):
    return {"version": 1, "settings": {}, "speaker_groups": {}, "sessions": sessions}


def old_session(sid, name, theme, **cycle):
    return {"id": sid, "name": name, "theme_id": theme, "cycle_config": cycle}


def test_cycling_is_converted_to_playlists_once():
    state = m.state.SonoriumState.from_dict(old_state(
        s1=old_session("s1", "Hall", "storm", enabled=True, interval_minutes=15, randomize=True, theme_ids=["tavern", "storm"]),
        s2=old_session("s2", "Den", "beach", enabled=True, interval_minutes=60, randomize=False, theme_ids=[]),
        s3=old_session("s3", "Loft", "beach", enabled=False, interval_minutes=30, theme_ids=["forest", "beach"]),
        s4=old_session("s4", "Attic", "beach", enabled=False, interval_minutes=60, theme_ids=[]),
    ))
    assert m.playlists.migrate_cycling(state, ["beach", "forest", "storm", "tavern"]) == 3
    hall = state.playlists[state.sessions["s1"].playlist_id]
    assert hall["name"] == "Hall cycle" and hall["order"] == "shuffle" and hall["repeat"] is True
    assert hall["play_min"] == hall["play_max"] == 900
    assert [e["theme_id"] for e in hall["entries"]] == ["tavern", "storm"]
    assert state.sessions["s1"].playlist_position == 1  # on its current theme
    den = state.playlists[state.sessions["s2"].playlist_id]
    assert [e["theme_id"] for e in den["entries"]] == ["beach", "forest", "storm", "tavern"]  # all themes
    assert den["order"] == "in_order" and den["play_min"] == 3600
    assert state.sessions["s3"].playlist_id is None  # cycling was off: kept, not played
    assert any(p["name"] == "Loft cycle" for p in state.playlists.values())
    assert state.sessions["s4"].playlist_id is None
    assert state.cycling_migrated
    assert m.playlists.migrate_cycling(state, ["beach"]) == 0  # once only
    saved = state.to_dict()
    assert "cycle_config" not in saved["sessions"]["s1"]
    assert saved["cycling_migrated"] is True and len(saved["playlists"]) == 3
    again = m.state.SonoriumState.from_dict(saved)
    assert again.sessions["s1"].playlist_id == state.sessions["s1"].playlist_id
