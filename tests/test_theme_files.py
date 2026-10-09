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

    lute_track = recording.TrackView(recording.RecordingThemeInstance(lute), {})
    assert (lute_track.exclusion_group, lute_track.exclusive) == ("Lute", True)
    assert lute_track.playback_mode == recording.PlaybackMode.SPARSE

    fire_instance = recording.RecordingThemeInstance(fire)
    assert recording.TrackView(fire_instance, {}).exclusion_group is None
    fire_instance.exclusive = True  # old single exclusive flag
    assert recording.TrackView(fire_instance, {}).exclusion_group == "Exclusive"
