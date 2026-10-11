"""
Sequences (presets in order inside a theme) and playlists (themes in order)
on a channel, with a fake clock; and the one-time conversion of theme
cycling into playlists.
"""

import asyncio
import json
import random
from datetime import datetime
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
    assert t.channel.fades[-1] is None  # and tracks turning on or off take the usual 3 s
    seen = []
    for when in (12.9, 13, 28, 43, 58, 73):  # 3 s glide + 10 s hold, then 5 s ramp + 10 s hold
        at(t, when)
        seen.append(t.session.preset_id)
    assert seen == ["a", "b", "c", "b", "a", "b"]
    assert t.channel.glides()[-1] == 5  # steps glide over the sequence's ramp
    assert t.channel.fades[-1] == 5  # and tracks turning on or off fade over it too


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
    others = [d for theme, d in zip(themes, durations) if theme != "forest"]
    assert all(10 <= d <= 20 for d in others) and len(set(others)) > 3
    # The entry with a sequence plays one full pass of it: 50 s, a 4 s ramp, 50 s
    assert [d for theme, d in zip(themes, durations) if theme == "forest"] == pytest.approx([104, 104])
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


def _shuffled_themes(tmp_path, themes, steps):
    t = build(tmp_path)
    entries = [{"theme_id": th, "preset_id": None, "sequence_id": None} for th in themes]
    add_playlist(t, entries, order="shuffle", play_min=5, play_max=5)
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    played = []
    for _ in range(steps):
        played.append(themes[t.session.playlist_order[t.session.playlist_position]])
        t.clock.now += 5
        run(t.manager.runner.tick())
    return played


def test_shuffle_never_plays_a_theme_twice_in_a_row_nor_a_b_a(tmp_path):
    # Two entries share each of two themes; the rule is about themes, across rounds too
    themes = ["tavern", "forest", "tavern", "storm", "beach", "forest"]
    played = _shuffled_themes(tmp_path, themes, 120)
    assert all(a != b for a, b in zip(played, played[1:]))  # never the same theme twice in a row
    assert all(a != c for a, c in zip(played, played[2:]))  # never A, B, A


def test_a_two_theme_shuffle_alternates(tmp_path):
    played = _shuffled_themes(tmp_path, ["tavern", "forest"], 20)
    assert all(a != b for a, b in zip(played, played[1:]))  # A, B, A, B is the only way


def test_playlist_without_repeat_stops_the_channel_at_the_end(tmp_path):
    t = build(tmp_path)
    add_playlist(t, ENTRIES[:2], repeat=False, play_min=10, play_max=10)
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    at(t, 10)
    assert t.session.theme_id == "forest" and t.session.is_playing
    at(t, 20)
    assert t.session.is_playing  # the sequence entry plays one full pass (50 + 4 + 50 s)
    at(t, 114)
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
    assert t.channel.glides()[-1] == 3.0 and t.channel.fades[-1] == 3.0  # Next by hand: the quick 3 s
    t.manager.stop_sequence(t.session.id)
    assert run(t.manager.skip(t.session.id)) is None


# --- Recent changes (the channel card's list) ---

def recent_texts(t):
    return [e["text"] for e in t.session.recent]


def test_recent_changes_follow_play_presets_and_sequences(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.update(t.session.id, preset_id="b")
    t.manager.update(t.session.id, preset_id="seq:once")
    for when in (13, 28, 43):
        at(t, when)
    t.manager.update(t.session.id, theme_id="tavern")
    run(t.manager.stop(t.session.id))
    assert recent_texts(t) == ["Started", "Preset: Rain", "Started: Once", "Step 1 of 3: Drizzle",
                               "Step 2 of 3: Rain", "Step 3 of 3: Downpour", "Stopped: Once",
                               "Theme: Tavern", "Stopped"]
    assert all(datetime.fromisoformat(e["at"]).tzinfo is not None for e in t.session.recent)


def test_recent_changes_follow_playlists(tmp_path):
    t = build(tmp_path)
    add_playlist(t, ENTRIES)
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    run(t.manager.skip(t.session.id))
    at(t, 50)  # the forest entry's sequence pass: its second preset
    t.manager.clear_playlist(t.session.id)
    t.manager.update(t.session.id, preset_id="dawn")  # by hand: stops the entry's sequence
    assert recent_texts(t) == ["Started", "Started: Evening", "Entry 1 of 3: Tavern", "Entry 2 of 3: Forest",
                               "Step 2 of 2: Dusk", "Stopped: Evening", "Stopped: Day", "Preset: Dawn"]


def test_recent_changes_keep_the_latest_and_are_saved(tmp_path):
    t = build(tmp_path)
    for i in range(25):
        t.manager.note(t.session, f"Preset: {i}")
    t.manager.note(t.session, "Preset: 24")  # the same again: kept once
    assert len(t.session.recent) == 20 and recent_texts(t)[0] == "Preset: 5" and recent_texts(t)[-1] == "Preset: 24"
    t.store.save()
    loaded = m.state.StateStore(tmp_path / "state.json").load()
    assert loaded.sessions[t.session.id].recent == t.session.recent
    bad = m.state.Session.from_dict({"id": "x", "name": "X", "recent": [{"at": "2026-01-01T00:00:00+00:00"}, "junk",
                                                                      {"at": "2026-01-01T00:00:00+00:00", "text": "Started"}]})
    assert bad.recent == [{"at": "2026-01-01T00:00:00+00:00", "text": "Started"}]


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


# --- Sequences picked like presets ---

def test_picking_a_sequence_as_the_preset_starts_it(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.update(t.session.id, preset_id="seq:up_down")
    assert t.session.sequence_id == "up_down" and t.session.preset_id == "a"
    sequence_run = t.manager.runner.running_sequence(t.session.id)
    assert sequence_run is not None and not sequence_run.single_pass
    at(t, 13)
    assert t.session.preset_id == "b"  # it runs like one started on its own
    # Another sequence replaces it; a normal preset stops it
    t.manager.update(t.session.id, preset_id="seq:loop")
    assert t.session.sequence_id == "loop" and t.session.preset_id == "a"
    t.manager.update(t.session.id, preset_id="c")
    assert t.session.sequence_id is None and t.manager.runner.running_sequence(t.session.id) is None
    assert t.session.preset_id == "c"


def test_a_new_theme_or_stopping_the_channel_stops_a_picked_sequence(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.update(t.session.id, preset_id="seq:loop")
    t.manager.update(t.session.id, theme_id="tavern")
    assert t.session.sequence_id is None and t.manager.runner.running_sequence(t.session.id) is None

    # A new theme with one of its sequences: crossfade to its first preset, then it runs
    t.manager.update(t.session.id, theme_id="forest", preset_id="seq:day")
    assert (t.session.theme_id, t.session.preset_id, t.session.sequence_id) == ("forest", "dawn", "day")
    assert [c for c in t.channel.calls if c[0] == "set_theme"][-1][1] == "forest"
    assert t.manager.runner.running_sequence(t.session.id) is not None

    run(t.manager.stop(t.session.id))
    assert t.manager.runner.running_sequence(t.session.id) is None  # its timer stops


def test_a_bad_sequence_choice_changes_nothing(tmp_path):
    themes = json.loads(json.dumps(THEMES))
    themes["storm"]["sequences"]["empty"] = {"name": "Empty", "presets": ["gone"]}
    t = build(tmp_path, themes)
    t.manager.update(t.session.id, preset_id="b")
    with pytest.raises(LookupError):
        t.manager.update(t.session.id, preset_id="seq:nope")
    with pytest.raises(ValueError):
        t.manager.update(t.session.id, preset_id="seq:empty")
    with pytest.raises(LookupError):  # a sequence of another theme
        t.manager.update(t.session.id, preset_id="seq:day")
    with pytest.raises(LookupError):  # checked against the new theme, before the theme changes
        t.manager.update(t.session.id, theme_id="tavern", preset_id="seq:up_down")
    assert (t.session.theme_id, t.session.preset_id, t.session.sequence_id) == ("storm", "b", None)


def test_a_channel_can_be_created_on_a_sequence(tmp_path):
    t = build(tmp_path)
    session = t.manager.create(theme_id="forest", preset_id="seq:day")
    assert (session.preset_id, session.sequence_id) == ("dawn", "day")
    with pytest.raises(LookupError):
        t.manager.create(theme_id="forest", preset_id="seq:nope")


# --- Playlist entries with a sequence: one pass ---

WIND = {"presets": {f"w{i}": {"name": f"Wind {i}", "tracks": {}} for i in range(1, 5)},
        "sequences": {"gust": {"name": "Gust", "presets": ["w1", "w2", "w3", "w4"], "at_end": "reverse",
                               "hold_min": 300, "hold_max": 900, "ramp": 30}}}


def walk(t, until_theme, limit=50):
    """Run the timers change by change; returns [(seconds since start, preset, ramp glide)] until the theme changes."""
    runner, start = t.manager.runner, t.clock.now
    steps = [(0.0, t.session.preset_id)]
    for _ in range(limit):
        times = [r.next_at for r in (runner.running_sequence(t.session.id), runner.running_playlist(t.session.id)) if r]
        t.clock.now = min(times)
        run(runner.tick())
        if t.session.theme_id == until_theme:
            return steps, t.clock.now - start
        if t.session.preset_id != steps[-1][1]:
            steps.append((t.clock.now - start, t.session.preset_id))
    raise AssertionError("the playlist never moved on")


def wind_playlist(tmp_path, **extra):
    themes = json.loads(json.dumps(THEMES))
    themes["wind"] = WIND
    t = build(tmp_path, themes)
    add_playlist(t, [{"theme_id": "wind", "preset_id": None, "sequence_id": "gust"},
                     {"theme_id": "tavern", "preset_id": None, "sequence_id": None}], **extra)
    run(t.manager.play(t.session.id))
    t.manager.set_playlist(t.session.id, "p1")
    return t


def test_a_sequence_entry_plays_one_full_pass_ignoring_the_play_time(tmp_path):
    t = wind_playlist(tmp_path, play_min=10, play_max=20)
    assert (t.session.theme_id, t.session.preset_id) == ("wind", "w1")
    sequence_run = t.manager.runner.running_sequence(t.session.id)
    assert sequence_run.single_pass and sequence_run.ramp == 30
    holds = list(sequence_run.holds)
    assert all(300 <= h <= 900 for h in holds) and len(set(holds)) == 4  # drawn in the sequence's range

    steps, length = walk(t, "tavern")
    assert [p for _, p in steps] == ["w1", "w2", "w3", "w4"]  # each preset once, first to last
    assert length == pytest.approx(sum(holds) + 3 * 30)  # then the playlist moves on (not 10-20 s)
    starts = [s for s, _ in steps]
    assert starts[1] == pytest.approx(holds[0])  # the glide to w2 starts after w1's hold
    assert starts[2] - starts[1] == pytest.approx(30 + holds[1])
    assert t.channel.glides()[-2] == 30  # presets glide over the sequence's ramp
    assert t.channel.fades[-2] == 30  # a playlist entry's sequence fades tracks over it too
    assert t.session.sequence_id is None and t.manager.runner.running_sequence(t.session.id) is None


def test_fit_sequences_squeezes_the_pass_into_the_play_time(tmp_path):
    t = wind_playlist(tmp_path, play_min=600, play_max=600, fit_sequences=True)
    sequence_run = t.manager.runner.running_sequence(t.session.id)
    holds, ramp = list(sequence_run.holds), sequence_run.ramp
    assert sum(holds) + 3 * ramp == pytest.approx(600)  # 4 presets and 3 ramps in 10 minutes
    assert 0 < ramp <= min(holds) / 2
    assert ramp < 30  # scaled down with the holds

    steps, length = walk(t, "tavern")
    assert [p for _, p in steps] == ["w1", "w2", "w3", "w4"]
    assert length == pytest.approx(600)
    assert t.channel.glides()[-2] == pytest.approx(ramp)


def test_status_of_a_sequence_entry_reports_the_preset_and_next_change(tmp_path):
    t = wind_playlist(tmp_path, play_min=600, play_max=600, fit_sequences=True)
    runner = t.manager.runner
    status = t.manager.playback_status(t.session)
    assert status["sequence"]["single_pass"] and status["sequence"]["steps"] == 4
    assert status["sequence"]["current_preset_id"] == "w1"
    assert status["sequence"]["seconds_until_change"] == round(runner.running_sequence(t.session.id).holds[0])
    assert status["playlist"]["seconds_until_change"] == 600
    for _ in range(3):  # to the last preset
        t.clock.now = runner.running_sequence(t.session.id).next_at
        run(runner.tick())
    status = t.manager.playback_status(t.session)
    assert status["sequence"]["current_preset_id"] == "w4" and status["sequence"]["step"] == 4
    # Nothing more in the pass: the next change is the playlist's
    assert status["sequence"]["next_change_at"] and status["playlist"]["next_change_at"]
    assert status["sequence"]["seconds_until_change"] == status["playlist"]["seconds_until_change"] > 0


def test_stopping_the_playlist_keeps_the_entry_sequence_running(tmp_path):
    t = wind_playlist(tmp_path, play_min=600, play_max=600, fit_sequences=True)
    t.manager.clear_playlist(t.session.id)
    sequence_run = t.manager.runner.running_sequence(t.session.id)
    assert sequence_run is not None and not sequence_run.single_pass
    t.clock.now = sequence_run.next_at
    run(t.manager.runner.tick())
    assert t.session.preset_id == "w2" and t.session.sequence_id == "gust"


def test_fit_pass_shares_and_ramp_limit():
    fit = m.sequences.fit_pass
    holds, ramp = fit([300, 600, 900, 400], 30, 600)
    assert sum(holds) + 3 * ramp == pytest.approx(600) and ramp == pytest.approx(30 * 600 / 2290)
    assert holds[2] / holds[0] == pytest.approx(3)  # in proportion
    # A long ramp would be more than half a hold: equal holds, ramp at most half of one
    holds, ramp = fit([10, 10, 10, 10], 100, 600)
    assert len(set(holds)) == 1 and ramp <= holds[0] / 2 + 1e-9
    assert sum(holds) + 3 * ramp == pytest.approx(600)
    holds, ramp = fit([1, 100, 100, 100], 10, 600)  # one tiny hold: equal shares, ramp kept
    assert len({round(h, 6) for h in holds}) == 1 and ramp <= holds[0] / 2
    assert sum(holds) + 3 * ramp == pytest.approx(600)
    holds, ramp = fit([0, 0], 0, 60)  # no time to share: equal holds
    assert holds == [30, 30] and ramp == 0
    assert fit([500], 30, 60) == ([60], 0.0)


def test_pass_min_and_max():
    defaults = {"hold_min": 10, "hold_max": 10, "ramp": 5}
    up_down = m.sequences.to_response("up_down", THEMES["storm"]["sequences"]["up_down"], defaults)
    assert (up_down["pass_min"], up_down["pass_max"]) == (3 * 10 + 2 * 5, 3 * 10 + 2 * 5)
    random_hold = m.sequences.to_response("random_hold", THEMES["storm"]["sequences"]["random_hold"], defaults)
    assert (random_hold["pass_min"], random_hold["pass_max"]) == (2 * 10 + 2, 2 * 20 + 2)
    choice = m.sequences.to_choice("up_down", THEMES["storm"]["sequences"]["up_down"], defaults, {"b": True})
    assert choice["choice"] == "seq:up_down" and choice["has_intrusion"] is True
    assert m.sequences.to_choice("up_down", {"presets": ["a"]}, defaults, {"b": True})["has_intrusion"] is False


# --- Cycling becomes playlists ---

def old_state(**sessions):
    return {"version": 1, "settings": {}, "speaker_groups": {}, "sessions": sessions}


# --- Listen here (a browser plays the channel's stream) ---

def released(t):
    return ("stop",) in t.channel.calls


def test_listen_then_play_then_stop_then_unlisten(tmp_path):
    t = build(tmp_path)
    sid = t.session.id
    assert t.manager.listen(sid) is t.channel
    assert [c[0] for c in t.channel.calls] == ["set_theme", "fade_in", "expect_local"]  # the mix starts, speakers don't
    assert not t.session.is_playing and not t.controller.play_media_multi.called
    run(t.manager.play(sid))  # the speakers join the same channel
    assert t.session.is_playing and t.controller.play_media_multi.called
    assert t.manager.get_session_channel(sid) == t.channel.id
    run(t.manager.stop(sid))  # only the speakers stop: the browser keeps the mix
    assert t.controller.stop_multi.called and not released(t)
    assert not any(c[0] == "fade_out" for c in t.channel.calls)
    run(t.manager.unlisten(sid))  # nothing listens now
    assert released(t) and t.manager.get_session_channel(sid) is None


def test_one_browser_leaving_keeps_the_mix_for_another(tmp_path):
    t = build(tmp_path)
    sid = t.session.id
    t.manager.listen(sid)
    t.manager.listen(sid)  # a second browser (another tab or device)
    run(t.manager.unlisten(sid))
    assert not released(t) and t.manager.is_listened(t.session)
    run(t.manager.unlisten(sid))
    assert released(t) and not t.manager.is_listened(t.session)


def test_muted_speakers_stay_muted_for_the_channel(tmp_path):
    t = build(tmp_path)
    sid = t.session.id
    assert t.manager.set_speakers_muted(sid, True) and t.session.speakers_muted
    run(t.manager.play(sid))
    assert t.channel.speakers_muted is True  # a channel taken up later gets it too
    t.manager.set_speakers_muted(sid, False)
    assert t.channel.speakers_muted is False and not t.session.speakers_muted
    assert not t.manager.set_speakers_muted("nope", True)


def test_play_then_listen_then_stop_then_unlisten(tmp_path):
    t = build(tmp_path)
    sid = t.session.id
    run(t.manager.play(sid))
    themes_set = sum(c[0] == "set_theme" for c in t.channel.calls)
    t.manager.listen(sid)
    assert sum(c[0] == "set_theme" for c in t.channel.calls) == themes_set  # already running: no restart
    run(t.manager.stop(sid))
    assert t.controller.stop_multi.called and not released(t)
    run(t.manager.unlisten(sid))
    assert released(t)


def test_a_mix_kept_for_browsers_is_released_when_none_listens(tmp_path):
    t = build(tmp_path)
    sid = t.session.id
    t.manager.listen(sid)
    assert t.manager.release_idle_sessions() == [] and not released(t)  # a browser listens
    t.channel.local_idle = 20  # the browser went without unlistening
    assert t.manager.release_idle_sessions() == []  # nothing stopped for the speakers
    assert released(t) and "Stopped" not in recent_texts(t)


def test_speakers_keep_playing_when_the_browser_goes(tmp_path):
    t = build(tmp_path)
    run(t.manager.play(t.session.id))
    t.manager.listen(t.session.id)
    t.channel.local_idle = 20
    t.manager.release_idle_sessions()
    assert t.session.is_playing and not released(t)  # the speakers still play


def test_a_theme_picked_while_only_a_browser_listens_crossfades(tmp_path):
    t = build(tmp_path)
    t.manager.listen(t.session.id)
    t.manager.update(t.session.id, theme_id="tavern")
    assert [c[1] for c in t.channel.calls if c[0] == "set_theme"][-1] == "tavern"


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


def test_a_sequence_runs_while_only_a_browser_listens(tmp_path):
    """Listening counts as playing for a sequence: it starts, its steps come, Next works; it pauses when nothing listens."""
    t = build(tmp_path)
    sid = t.session.id
    t.manager.start_sequence(sid, "up_down")  # set on a stopped channel: nothing runs yet
    assert t.manager.runner.running_sequence(sid) is None
    t.manager.listen(sid)
    assert t.manager.runner.running_sequence(sid) is not None and t.session.preset_id == "a"
    assert run(t.manager.skip(sid)) is not None and t.session.preset_id == "b"  # Next
    t.clock.now += 10_000
    run(t.manager.runner.tick())
    assert t.session.preset_id == "c"  # the next step came by itself
    run(t.manager.play(sid))
    run(t.manager.stop(sid))  # the speakers stop, the browser still listens: it keeps running
    assert t.manager.runner.running_sequence(sid) is not None
    run(t.manager.unlisten(sid))
    assert t.manager.runner.running_sequence(sid) is None


def test_sequence_response_lists_the_steps_with_while_playing_changes():
    seq = {"presets": ["a", "b", "c"], "while": {"a": {"tracks": {"Rain": {"volume": 0.5}}, "groups": {}},
                                                 "b": {"tracks": {}, "groups": {}}}}
    response = m.sequences.to_response("s", seq, {"hold_min": 10, "hold_max": 10, "ramp": 5})
    assert response["while_steps"] == ["a"]

