"""Themes 2.0 scanner: top-level tracks plus one level of group folders, and groups in the mixer."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "theme_files_under_test", Path(__file__).resolve().parents[1] / "sonorium_addon" / "sonorium" / "theme_files.py")
theme_files = importlib.util.module_from_spec(spec)
spec.loader.exec_module(theme_files)


def make_theme(tmp_path):
    theme = tmp_path / "Tavern"
    (theme / "Lute").mkdir(parents=True)
    (theme / "Bar chatter").mkdir()
    (theme / "_drafts").mkdir()
    (theme / "images").mkdir()
    for f in ("Fireplace.wav", "Mug clink.mp3", "notes.txt", "Lute/Lute song 2.wav", "Lute/Lute song 1.wav",
              "Bar chatter/Barkeep 1.ogg", "_drafts/Unused.wav", "images/cover.png", ".hidden.wav"):
        (theme / f).write_bytes(b"")
    return theme


def test_tracks_are_top_level_files_then_group_folders(tmp_path):
    theme = make_theme(tmp_path)
    keys = [theme_files.track_key(theme, p) for p in theme_files.theme_audio_files(theme)]
    assert keys == ["Fireplace", "Mug clink", "Bar chatter/Barkeep 1", "Lute/Lute song 1", "Lute/Lute song 2"]
    assert [g.name for g in theme_files.group_folders(theme)] == ["Bar chatter", "Lute"]  # no audio, or "_": not groups
    assert theme_files.track_group(theme, theme / "Lute" / "Lute song 1.wav") == "Lute"
    assert theme_files.track_group(theme, theme / "Fireplace.wav") is None
    assert theme_files.track_display_name("Lute/Lute song 1") == "Lute song 1"


def test_flat_theme_keeps_its_old_keys(tmp_path):
    theme = tmp_path / "Rain"
    theme.mkdir()
    (theme / "Rain on leaves.wav").write_bytes(b"")
    assert [theme_files.track_key(theme, p) for p in theme_files.theme_audio_files(theme)] == ["Rain on leaves"]
    assert theme_files.has_audio(theme) and not theme_files.has_audio(tmp_path / "missing")


def test_groups_in_the_mixer(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "sonorium.theme_files", theme_files)
    pytest.importorskip("numpy")
    pytest.importorskip("av")
    from test_crossfade_loop import recording

    theme = make_theme(tmp_path)
    lute = recording.RecordingMetadata(theme / "Lute" / "Lute song 1.wav", theme)
    fire = recording.RecordingMetadata(theme / "Fireplace.wav", theme)
    assert (lute.name, lute.group, fire.name, fire.group) == ("Lute/Lute song 1", "Lute", "Fireplace", None)

    lute.is_short_file = lambda threshold: True
    lute_track = recording.TrackView(recording.RecordingThemeInstance(lute), {})
    assert (lute_track.exclusion_group, lute_track.exclusive) == ("Lute", True)
    assert lute_track.playback_mode == recording.PlaybackMode.SPARSE  # short file in a group

    fire_instance = recording.RecordingThemeInstance(fire)
    assert recording.TrackView(fire_instance, {}).exclusion_group is None
    fire_instance.exclusive = True  # old single exclusive flag
    assert recording.TrackView(fire_instance, {}).exclusion_group == "Exclusive"


def test_group_is_a_mixer_bus(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "sonorium.theme_files", theme_files)
    pytest.importorskip("numpy")
    pytest.importorskip("av")
    from test_crossfade_loop import recording

    theme_dir = make_theme(tmp_path)
    theme = SimpleNamespace(groups={"Lute": {"volume": 0.5, "presence": 0.5, "gap_min": 30, "gap_max": 90}},
                            short_file_threshold=15.0)
    meta = recording.RecordingMetadata(theme_dir / "Lute" / "Lute song 1.wav", theme_dir)
    meta.is_short_file = lambda threshold: False
    song = recording.RecordingThemeInstance(meta, theme)
    song.volume, song.presence = 0.8, 0.5

    view = recording.TrackView(song, {})
    assert (view.volume, view.presence) == (0.4, 0.25)  # track x group: 50% x 50% = 25%
    # In a group every track plays Intermittent: its whole file once, on its turn
    for mode in (recording.PlaybackMode.AUTO, recording.PlaybackMode.CONTINUOUS, recording.PlaybackMode.PRESENCE):
        song.playback_mode = mode
        assert view.playback_mode == recording.PlaybackMode.SPARSE

    preset = {**recording.preset_group_overrides({"Lute": {"presence": 1.0, "muted": True}}),
              **recording.preset_track_overrides({"Lute/Lute song 1": {"presence": 0.2}})}
    with_preset = recording.TrackView(song, preset)
    assert with_preset.presence == 0.2  # preset track 20% x preset group 100%
    assert with_preset.volume == 0.4  # preset doesn't set volume: track 0.8 x group 0.5
    assert with_preset.is_enabled is False  # group muted by the preset

    assert recording.group_gap_range(theme.groups["Lute"]) == (30.0, 90.0)  # a real interval
    assert recording.group_gap_range({"gap_max": 10}) == (10.0, 10.0)
    assert recording.group_gap_range({}) is None
    coordinator = recording.ExclusionGroupCoordinator((30.0, 90.0))
    assert all(30.0 <= coordinator._next_gap() <= 90.0 for _ in range(50))


def test_presence_tracks_in_a_group_take_turns(monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("av")
    from test_crossfade_loop import recording

    clock = {"now": 1000.0}
    monkeypatch.setattr(recording.time, "time", lambda: clock["now"])
    coordinator = recording.ExclusionGroupCoordinator((5.0, 5.0))
    coordinator._start_time = 0.0  # past the initial delay

    def loud():
        while True:
            yield np.full((1, 1024), 10000, np.int16)

    def track(name):
        return SimpleNamespace(name=name, presence=0.5)

    a = recording.PresenceMixingStream(loud(), track("A"), coordinator)
    b = recording.PresenceMixingStream(loud(), track("B"), coordinator)
    overlap = 0
    for _ in range(int(400 * 44100 / 1024)):  # ~400 s of audio
        clock["now"] += 1024 / 44100
        both = np.abs(next(a)).max() > 0 and np.abs(next(b)).max() > 0
        overlap += both
    # The group's gap separates them: never both audible at once
    assert overlap == 0


def test_group_turns_follow_the_audio_not_the_wall_clock(monkeypatch):
    """A stalled stream (wall clock races ahead) and no gap still never overlap."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("av")
    from test_crossfade_loop import recording

    wall = {"now": 1000.0}
    monkeypatch.setattr(recording.time, "time", lambda: wall["now"])
    audio = {"seconds": 0.0}
    coordinator = recording.ExclusionGroupCoordinator((0.0, 0.0), clock=lambda: audio["seconds"])

    def loud():
        while True:
            yield np.full((1, 1024), 10000, np.int16)

    def track(name):
        return SimpleNamespace(name=name, presence=0.5)

    streams = [recording.PresenceMixingStream(loud(), track(n), coordinator) for n in "ABC"]
    overlap = played = 0
    for i in range(int(900 * 44100 / 1024)):  # ~15 min of audio
        if i % 500 == 0:
            wall["now"] += 600  # a 10-minute stall
        audible = sum(np.abs(next(s)).max() > 0 for s in streams)
        audio["seconds"] += 1024 / 44100
        overlap += audible > 1
        played += audible == 1
    assert played > 0
    assert overlap == 0


def test_a_track_holds_its_turn_only_while_playing():
    from test_crossfade_loop import recording

    audio = {"seconds": 0.0}
    coordinator = recording.ExclusionGroupCoordinator((0.0, 0.0), clock=lambda: audio["seconds"])
    coordinator.register_track("A")
    coordinator.register_track("B")
    audio["seconds"] = coordinator.INITIAL_DELAY
    assert not coordinator.try_start_playing("A", 10.0)  # the group gathers who's asking first
    audio["seconds"] += coordinator.COLLECT_SECONDS
    assert coordinator.try_start_playing("A", 10.0)  # A alone asked: A's turn
    assert coordinator.is_playing("A") and not coordinator.try_start_playing("B", 10.0)
    audio["seconds"] += 10.0
    assert not coordinator.is_playing("A")
    assert not coordinator.try_start_playing("B", 10.0)
    audio["seconds"] += coordinator.COLLECT_SECONDS
    assert coordinator.try_start_playing("B", 10.0)


def _turns(shares, turns=3000, muted=()):
    """Simulates a group: every track asks every 2 s; returns the order of plays."""
    from test_crossfade_loop import recording
    import random as _random
    _random.seed(7)
    audio = {"seconds": 0.0}
    coordinator = recording.ExclusionGroupCoordinator((0.0, 0.0), clock=lambda: audio["seconds"])
    for name in shares:
        coordinator.register_track(name)
    played = []
    while len(played) < turns:
        audio["seconds"] += 2.0
        for name, share in shares.items():
            if name in muted:
                continue
            if coordinator.try_start_playing(name, 3.0, share):
                played.append(name)
    return played


def test_a_group_never_repeats_a_track_and_shares_turns_by_weight():
    played = _turns({"A": 1.0, "B": 1.0, "C": 1.0, "D": 1.0, "E": 0.2})
    assert all(a != b for a, b in zip(played, played[1:]))  # never twice in a row
    counts = {t: played.count(t) for t in "ABCDE"}
    assert all(abs(counts[t] / len(played) - 0.225) < 0.04 for t in "ABCD")
    assert 0.05 < counts["E"] / len(played) < 0.15  # the 20% track: about 1 turn in 10


def test_drag_makes_recent_tracks_wait():
    played = _turns({t: 1.0 for t in "ABCDE"})
    # the track before the last one is weighed down: an A-B-A pattern is rare
    abab = sum(1 for a, b, c in zip(played, played[1:], played[2:]) if a == c)
    assert abab / len(played) < 0.12


def test_a_muted_track_never_holds_up_the_group():
    played = _turns({"A": 1.0, "B": 1.0}, turns=200, muted={"B"})
    assert set(played) == {"A"}  # A plays again when nothing else can
